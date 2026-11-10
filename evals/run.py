"""Repeatable eval runner.

    python -m evals.run                      # = --mode pipeline: forces LLM_PROVIDER=mock (free, deterministic)
    python -m evals.run --mode multi_agent   # LIVE agents: uses the provider + key from .env (costs money)
    python -m evals.run --compare            # runs both modes (separate processes) and prints a side-by-side table

Runs the golden set against a fresh, isolated, seeded DB. Cases with a "modes" list run only in those modes.
Writes evals/results/latest_<mode>.json, latest.json (last run) and a timestamped copy.
NOTE: with LLM_PROVIDER=mock the pass rate checks the PIPELINE WIRING (routing, retrieval, guardrails,
grounding), not real LLM answer quality - the mock is extractive and was written alongside this golden set.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES_DIR = ROOT / "evals" / "results"
sys.path.insert(0, str(ROOT))

MODES = ("pipeline", "multi_agent")
# USD per 1M tokens (input, output). Estimates for reporting only; longest prefix match on the model id.
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00), "gpt-4.1-mini": (0.40, 1.60),
          "gpt-4.1-nano": (0.10, 0.40), "gpt-4.1": (2.00, 8.00)}
EMERGENCY_WORDS = re.compile(r"\b(fire|smoke|evacuat\w*|spill\w*|emergency|first aid|injur\w*|gas leak)\b", re.I)
LOCKOUT_WORDS = re.compile(r"\block ?out\b|\blockout\b|\btag ?out\b|\bloto\b", re.I)


# ---------------------------------------------------------------- checks
def check(case: dict, r) -> list[str]:
    fails = []
    if r.status != case["expect_status"]:
        fails.append(f"status {r.status} != {case['expect_status']}")
    if "expect_primary" in case and (not r.route or r.route.primary != case["expect_primary"]):
        fails.append(f"primary {r.route.primary if r.route else None} != {case['expect_primary']}")
    for c in case.get("expect_categories_include", []):
        if not r.route or c not in r.route.categories:
            fails.append(f"category {c} not routed")
    if "expect_cited_doc" in case and case["expect_cited_doc"] not in [c.doc_key for c in r.citations]:
        fails.append(f"{case['expect_cited_doc']} not cited ({[c.doc_key for c in r.citations]})")
    if case.get("expect_safety_citation") and not any(c.category == "safety" for c in r.citations):
        fails.append("no safety citation")
    if "expect_answer_contains" in case and case["expect_answer_contains"] not in r.answer:
        fails.append(f"answer missing '{case['expect_answer_contains']}'")
    if "expect_answer_not_contains" in case and case["expect_answer_not_contains"] in r.answer:
        fails.append(f"answer contains forbidden '{case['expect_answer_not_contains']}'")
    if case.get("expect_no_draft") and any(c.doc_id.endswith("-v2") for c in r.citations):
        fails.append("DRAFT document leaked")
    if "expect_emergency" in case and r.emergency != case["expect_emergency"]:
        fails.append("emergency flag mismatch")
    if "expect_masked" in case and case["expect_masked"] in r.question:
        fails.append("PII not masked")
    return fails


def failure_class(case: dict, r, fails: list[str]) -> list[str]:
    """Bucket a failure: abstain | wrong_route | missing_citation | missing_safety_citation | other."""
    out = []
    if r.status == "abstained" and case["expect_status"] == "ok":
        out.append("abstain")
    for f in fails:
        if f.startswith("primary") or f.startswith("category"):
            out.append("wrong_route")
        elif f == "no safety citation":
            out.append("missing_safety_citation")
        elif "not cited" in f and r.status == "ok":
            out.append("missing_citation")
        elif not f.startswith("status") and "not cited" not in f:
            out.append("other")
    if case["expect_status"] == "abstained" and r.status == "ok":
        out.append("answered_should_abstain")
    elif r.status not in (case["expect_status"], "abstained"):
        out.append("other")
    return sorted(set(out))


def warnings(case: dict, r) -> list[str]:
    """Quality signals that are NOT pass/fail criteria in the golden set (reported, never hidden)."""
    w = []
    if r.status != "ok":
        return w
    notes = " ".join(r.safety_notes)
    if not r.emergency and EMERGENCY_WORDS.search(notes) and not EMERGENCY_WORDS.search(case["question"]):
        w.append("irrelevant_safety_note")       # e.g. fire/spill procedure on a routine question
    body = " ".join([r.answer, *r.steps, notes])
    if LOCKOUT_WORDS.search(body) and not any(c.doc_key == "SOP-LOTO-04" for c in r.citations):
        w.append("lockout_without_loto_citation")      # tells people to lock out but cites no lockout procedure
    return w


def price(model: str | None) -> tuple[float, float] | None:
    for k in sorted(PRICES, key=len, reverse=True):
        if (model or "").startswith(k):
            return PRICES[k]
    return None


def cost_usd(trace) -> float | None:
    total, unknown = 0.0, False
    for s in trace:
        if s.kind != "llm":
            continue
        p = price(s.model)
        if p is None:
            unknown = unknown or (s.tokens_in + s.tokens_out) > 0
            continue
        total += (s.tokens_in * p[0] + s.tokens_out * p[1]) / 1e6
    return None if unknown else round(total, 6)


def trace_summary(trace) -> dict:
    return {"steps": len(trace),
            "llm_calls_by_agent": dict(Counter(s.agent for s in trace if s.kind == "llm")),
            "tool_calls": [f"{s.agent}.{s.name}" for s in trace if s.kind == "tool"],
            "guards": [s.name for s in trace if s.kind == "guard"],
            "errors": [f"{s.agent}: {s.summary}" for s in trace if s.status != "ok"],
            "steps_detail": [{"seq": s.seq, "agent": s.agent, "kind": s.kind, "name": s.name, "status": s.status,
                              "tokens_in": s.tokens_in, "tokens_out": s.tokens_out, "latency_ms": s.latency_ms,
                              "summary": s.summary} for s in trace]}


# ---------------------------------------------------------------- one mode
def run_mode(mode: str) -> int:
    tmp = Path(tempfile.mkdtemp(prefix=f"fast-eval-{mode}-"))
    os.environ["FAST_DB_PATH"] = str(tmp / "eval.db")
    os.environ["FAST_LOG_DIR"] = str(tmp / "logs")
    if mode == "pipeline":
        os.environ["LLM_PROVIDER"] = "mock"          # never call a paid API in pipeline mode
    from app.config import config_hash, get_config, model_label
    get_config.cache_clear()
    cfg = get_config()
    cfg["agents"]["mode"] = mode                     # in-memory only; config.yaml is not touched
    from app.services import workflow
    from app.agents import enabled as agents_enabled

    provider = cfg["llm"]["provider"]
    live = agents_enabled()
    cases_all = [json.loads(l) for l in (ROOT / "evals" / "golden.jsonl").read_text(encoding="utf-8").splitlines()
                 if l.strip()]
    cases = [c for c in cases_all if mode in c.get("modes", MODES)]
    agent_models = {k: cfg["agents"][k]["model"] for k in ("router", "specialist", "synthesizer", "critic")}

    if mode == "multi_agent" and not live:
        print("!" * 78 + f"\nWARNING: --mode multi_agent but LLM_PROVIDER={provider}: agents are OFF, every answer "
              "comes from the\nrule-based pipeline. Set LLM_PROVIDER=openai and OPENAI_API_KEY in .env.\n" + "!" * 78)
    if live:
        print("=" * 78 + f"\n LIVE LLM EVAL  mode=multi_agent  provider={provider}  models={agent_models}\n"
              f" {len(cases)} cases - this calls a paid API (a few cents with gpt-4o-mini).\n" + "=" * 78)

    workflow.ensure_ready()
    results = []
    for i, case in enumerate(cases, 1):
        r = workflow.ask(case["question"], "supervisor", "eval.runner")
        fails = check(case, r)
        llm_calls = sum(1 for s in r.agent_trace if s.kind == "llm")
        results.append({
            "id": case["id"], "type": case["type"], "question": case["question"], "passed": not fails,
            "failures": fails, "failure_class": failure_class(case, r, fails) if fails else [],
            "warnings": warnings(case, r), "status": r.status, "mode_used": r.mode, "used_fallback": r.used_fallback,
            "categories": r.route.categories if r.route else [], "primary": r.route.primary if r.route else None,
            "cited": [c.chunk_id for c in r.citations], "safety_notes": r.safety_notes, "answer": r.answer[:400],
            "abstain_reason": r.abstain_reason, "latency_ms": r.latency_ms, "llm_calls": llm_calls,
            "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
            "est_cost_usd": cost_usd(r.agent_trace) if r.agent_trace else 0.0,
            "agent_trace": trace_summary(r.agent_trace)})
        if live:
            x = results[-1]
            print(f"  [{i:2d}/{len(cases)}] {x['id']:4s} {'PASS' if x['passed'] else 'FAIL'} "
                  f"{x['latency_ms'] / 1000:5.1f}s calls={llm_calls} tok={r.tokens_in + r.tokens_out}", flush=True)

    def rate(pred):
        sel = [x for x in results if pred(x)]
        return round(100 * sum(x["passed"] for x in sel) / len(sel), 1) if sel else None

    def pct(num, den):
        return round(100 * num / den, 1) if den else None

    pairs = list(zip(cases, results))
    routed = [(c, x) for c, x in pairs if "expect_primary" in c]
    cite_cases = [(c, x) for c, x in pairs if "expect_cited_doc" in c]
    safety_cases = [(c, x) for c, x in pairs if c.get("expect_safety_citation")]
    lat = [x["latency_ms"] for x in results]
    costs = [x["est_cost_usd"] for x in results]
    n = len(results)
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": mode, "live": live,
        "model": model_label() if not live else f"{provider}:{agent_models}", "llm_provider": provider,
        "config_hash": config_hash(), "total": n, "skipped": len(cases_all) - n,
        "passed": sum(x["passed"] for x in results), "pass_rate": rate(lambda x: True),
        "shared_pass_rate": rate(lambda x: "modes" not in next(c for c in cases if c["id"] == x["id"])),
        "by_type": {t: rate(lambda x, t=t: x["type"] == t) for t in sorted({x["type"] for x in results})},
        "routing_accuracy": pct(sum(x["primary"] == c["expect_primary"] for c, x in routed), len(routed)),
        "citation_hit_rate": pct(sum(c["expect_cited_doc"] in " ".join(x["cited"]) for c, x in cite_cases),
                                 len(cite_cases)),
        "abstain_correctness": rate(lambda x: x["type"] == "negative"),
        "safety_inclusion_recall": pct(sum(any(cid.startswith("SOP-") for cid in x["cited"]) for _, x in safety_cases),
                                       len(safety_cases)),
        "draft_leak_count": sum(any(cid.split("#")[0].endswith("-v2") for cid in x["cited"]) for x in results),
        "p50_latency_ms": round(statistics.median(lat), 1) if lat else None,
        "p95_latency_ms": round(statistics.quantiles(lat, n=20)[-1], 1) if len(lat) > 1 else (lat or [None])[0],
        "llm_calls_per_q": round(sum(x["llm_calls"] for x in results) / n, 2) if n else 0,
        "tokens_per_q": round(sum(x["tokens_in"] + x["tokens_out"] for x in results) / n) if n else 0,
        "est_cost_usd_total": None if None in costs else round(sum(costs), 4),
        "est_cost_usd_per_q": None if None in costs or not n else round(sum(costs) / n, 5),
        "fallback_count": sum(x["used_fallback"] for x in results),
        "mode_used": dict(Counter(x["mode_used"] for x in results)),
        "failure_classes": dict(Counter(k for x in results for k in x["failure_class"])),
        "warnings": dict(Counter(w for x in results for w in x["warnings"])),
        "note": ("mock provider: pass rate validates pipeline wiring (routing, retrieval, guardrails, grounding), "
                 "NOT live LLM answer quality") if provider == "mock" else
                "live LLM run; cost is an estimate from PRICES in evals/run.py",
    }
    out = {"summary": summary, "config_snapshot": cfg, "cases": results}
    RES_DIR.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(out, indent=2)
    (RES_DIR / f"latest_{mode}.json").write_text(blob, encoding="utf-8")
    (RES_DIR / "latest.json").write_text(blob, encoding="utf-8")
    (RES_DIR / f"run_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json").write_text(blob, encoding="utf-8")

    print(f"\nFAST eval [{mode}] - {summary['model']}  ({summary['passed']}/{n} passed, {summary['pass_rate']}%,"
          f" {summary['skipped']} skipped for this mode)")
    for k in ("routing_accuracy", "citation_hit_rate", "abstain_correctness", "safety_inclusion_recall",
              "draft_leak_count", "p50_latency_ms", "p95_latency_ms", "llm_calls_per_q", "tokens_per_q",
              "est_cost_usd_total", "fallback_count", "mode_used", "failure_classes", "warnings"):
        print(f"  {k:26s} {summary[k]}")
    print("  by type:", summary["by_type"])
    for x in results:
        if not x["passed"]:
            print(f"  FAIL {x['id']} {x['question'][:60]!r}: [{','.join(x['failure_class'])}] {'; '.join(x['failures'])}")
    for x in results:
        if x["warnings"]:
            print(f"  WARN {x['id']} {x['question'][:60]!r}: {', '.join(x['warnings'])}")
    if provider == "mock":
        print("  NOTE:", summary["note"])
    return 0 if summary["passed"] == n else 1


# ---------------------------------------------------------------- compare
def compare() -> int:
    codes = {}
    for m in MODES:
        print(f"\n### running --mode {m}", flush=True)
        codes[m] = subprocess.run([sys.executable, "-m", "evals.run", "--mode", m], cwd=ROOT).returncode
    s = {m: json.loads((RES_DIR / f"latest_{m}.json").read_text(encoding="utf-8"))["summary"] for m in MODES}
    rows = [("cases run", "total"), ("pass rate %", "pass_rate"), ("pass rate % (shared cases)", "shared_pass_rate"),
            ("routing accuracy %", "routing_accuracy"), ("citation hit rate %", "citation_hit_rate"),
            ("abstain correctness %", "abstain_correctness"), ("safety-inclusion recall %", "safety_inclusion_recall"),
            ("draft leaks", "draft_leak_count"), ("p95 latency ms", "p95_latency_ms"),
            ("LLM calls / question", "llm_calls_per_q"), ("tokens / question", "tokens_per_q"),
            ("est. cost USD (total)", "est_cost_usd_total"), ("fallbacks", "fallback_count"), ("live LLM", "live")]
    print(f"\n{'metric':30s} {'pipeline':>14s} {'multi_agent':>14s}\n" + "-" * 60)
    for label, k in rows:
        print(f"{label:30s} {str(s['pipeline'].get(k)):>14s} {str(s['multi_agent'].get(k)):>14s}")
    return max(codes.values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FAST golden-set eval")
    ap.add_argument("--mode", choices=MODES, default="pipeline",
                    help="pipeline (default, mock LLM, free) or multi_agent (live agents from .env)")
    ap.add_argument("--compare", action="store_true", help="run both modes and print a side-by-side table")
    a = ap.parse_args(argv)
    return compare() if a.compare else run_mode(a.mode)


if __name__ == "__main__":
    raise SystemExit(main())
