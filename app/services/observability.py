"""Read-side of tracing: trace list, trace detail, timeline, aggregated metrics with drill-down, log search.

Everything is computed from the stored records (queries, agent_steps, citations, feedback, audit_log); nothing is hardcoded.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pandas as pd

from app.data import db
from app.observability.logging import recent_logs

# metric key -> (label, aggregation description)
METRICS = {
    "queries": "Number of questions",
    "error_rate": "Error / abstain rate (%)",
    "fallback_rate": "Fallback to rule-based (%)",
    "p50_latency": "Latency p50 (ms)",
    "p95_latency": "Latency p95 (ms)",
    "avg_tokens": "Average tokens per question",
    "cost": "Estimated cost (USD)",
    "helpful": "Helpful answers (% of rated)",
}
GROUP_BYS = {
    "day": "Day", "primary_category": "Category", "status": "Status", "mode": "Engine",
    "model": "Model", "equipment_tag": "Equipment", "user_name": "User", "role": "Role",
}


@dataclass
class Filters:
    days: int | None = 30
    status: str | None = None
    mode: str | None = None
    category: str | None = None
    fallback_only: bool = False
    text: str = ""
    include_synthetic: bool = True
    extra: dict = field(default_factory=dict)


def _where(f: Filters) -> tuple[str, list]:
    w, p = ["1=1"], []
    if f.days:
        w.append("q.created_at >= datetime('now', ?)")
        p.append(f"-{int(f.days)} days")
    for col, val in (("status", f.status), ("mode", f.mode), ("primary_category", f.category)):
        if val and val != "all":
            w.append(f"q.{col} = ?")
            p.append(val)
    if f.fallback_only:
        w.append("q.used_fallback = 1")
    if f.text.strip():
        w.append("(q.question_masked LIKE ? OR q.request_id LIKE ? OR q.query_id LIKE ?)")
        p += [f"%{f.text.strip()}%"] * 3
    if not f.include_synthetic:
        w.append("q.is_synthetic = 0")
    return " AND ".join(w), p


def base_df(f: Filters | None = None) -> pd.DataFrame:
    """One row per question with step counts and feedback attached."""
    f = f or Filters()
    where, params = _where(f)
    rows = db.rows(f"""
        SELECT q.query_id, q.request_id, q.created_at, q.user_name, q.role, q.question_masked, q.primary_category,
               q.status, q.mode, q.used_fallback, q.model, q.equipment_tag, q.latency_ms, q.tokens_in, q.tokens_out,
               q.cost_usd, q.emergency, q.safety_flag, q.is_synthetic, q.error,
               (SELECT COUNT(*) FROM agent_steps s WHERE s.query_id = q.query_id) AS steps,
               (SELECT COUNT(*) FROM agent_steps s WHERE s.query_id = q.query_id AND s.status != 'ok') AS step_errors,
               (SELECT MIN(rating) FROM feedback fb WHERE fb.query_id = q.query_id) AS rating
        FROM queries q WHERE {where} ORDER BY q.created_at DESC""", tuple(params))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["day"] = df["created_at"].str[:10]
    df["tokens"] = df["tokens_in"] + df["tokens_out"]
    return df


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"traces": 0, "error_rate": 0.0, "fallback_rate": 0.0, "p50_ms": 0.0, "p95_ms": 0.0,
                "tokens": 0, "cost": 0.0}
    bad = df["status"].isin(["error", "abstained"]).mean() * 100
    return {"traces": int(len(df)), "error_rate": round(bad, 1),
            "fallback_rate": round(df["used_fallback"].mean() * 100, 1),
            "p50_ms": round(float(df["latency_ms"].quantile(0.5)), 1),
            "p95_ms": round(float(df["latency_ms"].quantile(0.95)), 1),
            "tokens": int(df["tokens"].sum()), "cost": round(float(df["cost_usd"].sum()), 4)}


def _agg(g: pd.DataFrame, metric: str) -> float:
    if metric == "queries":
        return float(len(g))
    if metric == "error_rate":
        return round(g["status"].isin(["error", "abstained"]).mean() * 100, 1)
    if metric == "fallback_rate":
        return round(g["used_fallback"].mean() * 100, 1)
    if metric == "p50_latency":
        return round(float(g["latency_ms"].quantile(0.5)), 1)
    if metric == "p95_latency":
        return round(float(g["latency_ms"].quantile(0.95)), 1)
    if metric == "avg_tokens":
        return round(float(g["tokens"].mean()), 1)
    if metric == "cost":
        return round(float(g["cost_usd"].sum()), 4)
    if metric == "helpful":
        rated = g["rating"].dropna()
        return round(float((rated == 1).mean() * 100), 1) if len(rated) else float("nan")
    raise ValueError(f"Unknown metric {metric}")


def aggregate(df: pd.DataFrame, metric: str, group_by: str) -> pd.DataFrame:
    """Metric per group, e.g. p95 latency by category. Columns: group, value, n (questions)."""
    if metric not in METRICS:
        raise ValueError(f"Unknown metric {metric}")
    if group_by not in GROUP_BYS:
        raise ValueError(f"Unknown group_by {group_by}")
    if df.empty:
        return pd.DataFrame(columns=["group", "value", "n"])
    d = df.copy()
    d[group_by] = d[group_by].fillna("(none)").astype(str)
    out = [{"group": k, "value": _agg(g, metric), "n": int(len(g))} for k, g in d.groupby(group_by)]
    res = pd.DataFrame(out)
    return res.sort_values("group") if group_by == "day" else res.sort_values("value", ascending=False)


def drill(df: pd.DataFrame, group_by: str, value: str) -> pd.DataFrame:
    """The questions behind one bar / table row."""
    if df.empty:
        return df
    return df[df[group_by].fillna("(none)").astype(str) == str(value)]


def agent_metrics(f: Filters | None = None) -> pd.DataFrame:
    """Per agent and step name: calls, errors, latency, tokens, cost (from stored steps)."""
    f = f or Filters()
    where, params = _where(f)
    rows = db.rows(f"""
        SELECT s.agent, s.kind, s.name, s.model, s.status, s.latency_ms, s.tokens_in, s.tokens_out, s.cost_usd
        FROM agent_steps s JOIN queries q ON q.query_id = s.query_id WHERE {where}""", tuple(params))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["tokens"] = df["tokens_in"] + df["tokens_out"]
    g = df.groupby(["agent", "kind", "name"], dropna=False)
    res = g.agg(calls=("status", "size"), errors=("status", lambda s: int((s != "ok").sum())),
                avg_ms=("latency_ms", "mean"), p95_ms=("latency_ms", lambda s: s.quantile(0.95)),
                tokens=("tokens", "sum"), cost_usd=("cost_usd", "sum")).reset_index()
    res[["avg_ms", "p95_ms"]] = res[["avg_ms", "p95_ms"]].round(1)
    res["cost_usd"] = res["cost_usd"].round(5)
    return res.sort_values("cost_usd", ascending=False)


def get_trace(query_id: str) -> dict | None:
    q = db.rows("SELECT * FROM queries WHERE query_id=?", (query_id,))
    if not q:
        return None
    q = q[0]
    for k in ("routed_categories", "guardrail_flags"):
        try:
            q[k] = json.loads(q[k] or "[]")
        except ValueError:
            q[k] = []
    steps = db.rows("SELECT * FROM agent_steps WHERE query_id=? ORDER BY seq", (query_id,))
    return {
        "query": q,
        "steps": steps,
        "timeline": timeline(steps),
        "citations": db.rows("""SELECT c.rank, c.score, d.doc_key, d.version, ch.section, ch.chunk_id
                                FROM citations c JOIN chunks ch ON ch.chunk_id = c.chunk_id
                                JOIN documents d ON d.doc_id = ch.doc_id WHERE c.query_id=? ORDER BY c.rank""",
                             (query_id,)),
        "feedback": db.rows("SELECT * FROM feedback WHERE query_id=?", (query_id,)),
        "audit": db.rows("SELECT * FROM audit_log WHERE request_id=? ORDER BY audit_id", (q["request_id"],)),
        "logs": [e for e in recent_logs(2000) if e.get("request_id") == q["request_id"]],
    }


def timeline(steps: list[dict]) -> list[dict]:
    """Reconstruct a waterfall from step latencies (router -> specialists in parallel -> synthesizer).

    Only per-step durations are stored, so start offsets are derived, not measured. Labelled as such in the UI.
    """
    lanes: dict[str, float] = {}
    router_end = 0.0
    out: list[dict] = []
    for s in steps:
        a = s["agent"]
        if a == "router":
            start = lanes.get(a, 0.0)
        elif a in ("safety", "maintenance", "quality"):
            start = lanes.get(a, router_end)
        elif a == "synthesizer":
            start = lanes.get(a, max([v for k, v in lanes.items() if k != "synthesizer"] or [router_end]))
        else:   # orchestrator guards: placed after everything before them
            start = max(lanes.values() or [0.0])
        end = start + float(s["latency_ms"] or 0)
        lanes[a] = end
        if a == "router":
            router_end = end
        out.append({"seq": s["seq"], "agent": a, "kind": s["kind"], "name": s["name"] or s["kind"],
                    "label": f"{s['seq']:>2}. {a} · {s['name'] or s['kind']}", "start_ms": round(start, 1),
                    "end_ms": round(max(end, start + 1.0), 1), "status": s["status"]})
    return out


def log_events(n: int = 500, event: str | None = None, text: str = "") -> list[dict]:
    ev = recent_logs(n)
    if event and event != "all":
        ev = [e for e in ev if e.get("event") == event]
    if text.strip():
        t = text.strip().lower()
        ev = [e for e in ev if t in json.dumps(e, default=str).lower()]
    return ev
