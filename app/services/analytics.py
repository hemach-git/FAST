"""Business KPIs and trends computed from the query/feedback records (never hardcoded)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.data import db


def queries_df(days: int | None = 30, category: str | None = None, equipment: str | None = None,
               include_synthetic: bool = True) -> pd.DataFrame:
    sql = "SELECT * FROM queries WHERE 1=1"
    params: list = []
    if days:
        sql += " AND created_at >= ?"
        params.append((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    if category:
        sql += " AND routed_categories LIKE ?"
        params.append(f'%"{category}"%')
    if equipment:
        sql += " AND equipment_tag = ?"
        params.append(equipment)
    if not include_synthetic:
        sql += " AND is_synthetic = 0"
    df = pd.DataFrame(db.rows(sql + " ORDER BY created_at DESC", tuple(params)))
    if df.empty:
        return df
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True, format="ISO8601")
    df["date"] = df["created_at"].dt.date
    df["categories"] = df["routed_categories"].apply(lambda s: ", ".join(json.loads(s)) if s else "")
    df["data_source"] = df["is_synthetic"].map({1: "synthetic", 0: "live demo"})
    return df


def kpis(df: pd.DataFrame | None = None, days: int | None = 30) -> dict:
    df = queries_df(days) if df is None else df
    fb = pd.DataFrame(db.rows("SELECT rating, review_status, query_id FROM feedback"))
    if df.empty:
        return {"total_queries": 0}
    answered = df["status"].eq("ok")
    if not fb.empty:
        fb = fb[fb["query_id"].isin(df["query_id"])]
    top = (df.dropna(subset=["equipment_tag"]).groupby(["equipment_tag", "primary_category"]).size()
           .sort_values(ascending=False))
    return {
        "total_queries": int(len(df)),
        "answer_rate_pct": round(100 * answered.mean(), 1),
        "abstain_rate_pct": round(100 * df["status"].eq("abstained").mean(), 1),
        "blocked": int(df["status"].eq("blocked").sum()),
        "safety_routed_pct": round(100 * df["safety_flag"].astype(bool).mean(), 1),
        "p95_latency_ms": round(float(df["latency_ms"].quantile(0.95)), 1),
        "avg_latency_ms": round(float(df["latency_ms"].mean()), 1),
        "helpful_pct": round(100 * (fb["rating"] == 1).mean(), 1) if not fb.empty else None,
        "feedback_count": int(len(fb)),
        "open_reviews": int((fb["review_status"] == "pending").sum()) if not fb.empty else 0,
        "top_issue": f"{top.index[0][0]} ({top.index[0][1]}) - {int(top.iloc[0])} questions" if len(top) else "n/a",
        "synthetic_share_pct": round(100 * df["is_synthetic"].mean(), 1),
    }


def category_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["category", "queries"])
    s = df["routed_categories"].apply(json.loads).explode().dropna()
    return s.value_counts().rename_axis("category").reset_index(name="queries")


def daily_trend(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["date", "primary_category", "queries"])
    return (df.dropna(subset=["primary_category"]).groupby(["date", "primary_category"]).size()
            .reset_index(name="queries"))


def top_issues(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Recurring floor issues: equipment x category, with abstain count (doc gaps)."""
    if df.empty:
        return pd.DataFrame()
    d = df.dropna(subset=["equipment_tag"])
    g = d.groupby(["equipment_tag", "primary_category"]).agg(
        queries=("query_id", "count"), abstained=("status", lambda s: int((s == "abstained").sum())),
        last_asked=("created_at", "max")).reset_index().sort_values("queries", ascending=False)
    return g.head(n)


def doc_gaps(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Questions the docs could not answer - candidates for new documentation."""
    if df.empty:
        return pd.DataFrame()
    d = df[df["status"] == "abstained"]
    return d.groupby("question_masked").size().sort_values(ascending=False).head(n).reset_index(name="times_asked")


def top_cited_docs(days: int | None = 30, n: int = 10) -> pd.DataFrame:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat() if days else "0000"
    return pd.DataFrame(db.rows("""SELECT d.doc_key, d.title, d.category, COUNT(*) AS citations
        FROM citations ci JOIN chunks c ON c.chunk_id = ci.chunk_id JOIN documents d ON d.doc_id = c.doc_id
        JOIN queries q ON q.query_id = ci.query_id WHERE q.created_at >= ?
        GROUP BY d.doc_key, d.title, d.category ORDER BY citations DESC LIMIT ?""", (since, n)))


def export_csv(df: pd.DataFrame) -> bytes:
    cols = [c for c in ["created_at", "data_source", "user_name", "role", "question_masked", "categories",
                        "primary_category", "route_confidence", "status", "answer_confidence", "equipment_tag",
                        "safety_flag", "emergency", "latency_ms", "model", "request_id"] if c in df.columns]
    return df[cols].to_csv(index=False).encode("utf-8")


def insights(k: dict) -> list[str]:
    """Plain-English takeaways for managers."""
    if not k.get("total_queries"):
        return ["No questions recorded yet."]
    out = [f"Supervisors asked {k['total_queries']} questions; {k['answer_rate_pct']}% were answered from approved documents."]
    if k.get("top_issue") and k["top_issue"] != "n/a":
        out.append(f"Most frequent floor issue: {k['top_issue']}. Consider a root-cause review with engineering.")
    out.append(f"{k['safety_routed_pct']}% of questions involved a hazard, so the safety procedure was shown with the answer.")
    if k["abstain_rate_pct"] > 5:
        out.append(f"{k['abstain_rate_pct']}% of questions were not covered by the documents - see 'Documentation gaps'.")
    if k.get("open_reviews"):
        out.append(f"{k['open_reviews']} flagged answers are waiting for manager review.")
    return out
