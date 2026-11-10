"""Repeatable eval runner.

    python -m evals.run                      # = --mode pipeline: forces LLM_PROVIDER=mock (free, deterministic)
    python -m evals.run --mode multi_agent   # LIVE agents: uses the provider + key from .env (costs money)
    python -m evals.run --compare            # runs both modes (separate processes) and prints a side-by-side table
    python -m evals.run --repeat 3           # run 3 times: mean/min/max per metric + flaky cases (variance)
    python -m evals.run --gate               # CI: exit 1 unless every limit in config.yaml targets.gate holds

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
def run_mode(mode: str, gate: bool = False) -> int:
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
    validate_cases(cases_all)
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
            "known_gap": (case.get("known_gap") or {}).get(mode),
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
        "gated_pass_rate": rate(lambda x: not x["known_gap"]),       # known gaps excluded (reported below)
        "known_gap_xfail": [x["id"] for x in results if x["known_gap"] and not x["passed"]],
        "known_gap_xpass": [x["id"] for x in results if x["known_gap"] and x["passed"]],
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
        "gated_metrics": gated_metrics(pairs),          # same metrics without known-gap cases (what the gate uses)
        "p95_target_ms": target_ms(cfg, mode, live),
        "p95_target_met": None,
        "note": ("mock provider: pass rate validates pipeline wiring (routing, retrieval, guardrails, grounding), "
                 "NOT live LLM answer quality") if provider == "mock" else
                "live LLM run; cost is an estimate from PRICES in evals/run.py",
    }
    if summary["p95_target_ms"] is not None and summary["p95_latency_ms"] is not None:
        summary["p95_target_met"] = summary["p95_latency_ms"] <= summary["p95_target_ms"]
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
    print(f"  {'gated_pass_rate':26s} {summary['gated_pass_rate']}  (excludes {len(summary['known_gap_xfail'])} "
          f"known-gap failures)")
    for x in results:
        if not x["passed"]:
            tag = "XFAIL" if x["known_gap"] else "FAIL"
            gap = f"  [known gap: {x['known_gap']}]" if x["known_gap"] else ""
            print(f"  {tag} {x['id']} {x['question'][:60]!r}: [{','.join(x['failure_class'])}] "
                  f"{'; '.join(x['failures'])}{gap}")
        elif x["known_gap"]:
            print(f"  XPASS {x['id']} {x['question'][:60]!r}: passes now - remove its known_gap marker for {mode}")
    for x in results:
        if x["warnings"]:
            print(f"  WARN {x['id']} {x['question'][:60]!r}: {', '.join(x['warnings'])}")
    if summary["p95_target_ms"] is not None:
        print(f"  p95 target (assumption)    {summary['p95_target_ms']:.0f} ms -> "
              f"{'met' if summary['p95_target_met'] else 'NOT met'}")
    if provider == "mock":
        print("  NOTE:", summary["note"])
    if gate:
        return gate_check(summary, cfg)
    return 0 if summary["passed"] == n else 1


def gated_metrics(pairs: list[tuple[dict, dict]]) -> dict:
    """Rate metrics recomputed without known-gap cases, so a documented gap does not fail the CI gate twice.
    The ungated numbers stay in the summary and are what reports show."""
    keep = [(c, x) for c, x in pairs if not x["known_gap"]]

    def pct(sel, ok):
        return round(100 * sum(ok(c, x) for c, x in sel) / len(sel), 1) if sel else None
    return {
        "routing_accuracy": pct([p for p in keep if "expect_primary" in p[0]],
                                lambda c, x: x["primary"] == c["expect_primary"]),
        "citation_hit_rate": pct([p for p in keep if "expect_cited_doc" in p[0]],
                                 lambda c, x: c["expect_cited_doc"] in " ".join(x["cited"])),
        "abstain_correctness": pct([p for p in keep if c_type(p) == "negative"], lambda c, x: x["passed"]),
        "safety_inclusion_recall": pct([p for p in keep if p[0].get("expect_safety_citation")],
                                       lambda c, x: any(cid.startswith("SOP-") for cid in x["cited"])),
    }


def c_type(pair: tuple[dict, dict]) -> str:
    return pair[0]["type"]


# ---------------------------------------------------------------- golden-set validation
REQUIRED = ("id", "type", "question", "expect_status")


def validate_cases(cases: list[dict]) -> None:
    """Refuse to run on a broken golden set: duplicate ids, missing keys, unreviewed TODO values."""
    errors, seen = [], set()
    for c in cases:
        cid = c.get("id", "?")
        missing = [k for k in REQUIRED if k not in c]
        if missing:
            errors.append(f"{cid}: missing {missing}")
        if cid in seen:
            errors.append(f"{cid}: duplicate id")
        seen.add(cid)
        if "TODO" in json.dumps(c):
            errors.append(f"{cid}: still has a TODO value (fill in the expectation before promoting it)")
        if c.get("expect_status") not in (None, "ok", "abstained", "blocked", "error"):
            errors.append(f"{cid}: bad expect_status {c.get('expect_status')!r}")
        for m in (c.get("known_gap") or {}):
            if m not in MODES:
                errors.append(f"{cid}: known_gap mode {m!r} not in {MODES}")
    if errors:
        raise SystemExit("golden.jsonl is invalid:\n  " + "\n  ".join(errors))


# ---------------------------------------------------------------- repeat runs (variance)
REPEAT_METRICS = ("pass_rate", "gated_pass_rate", "routing_accuracy", "citation_hit_rate", "abstain_correctness",
                  "safety_inclusion_recall", "draft_leak_count", "p50_latency_ms", "p95_latency_ms", "tokens_per_q",
                  "est_cost_usd_total", "fallback_count")


def aggregate(runs: list[dict]) -> dict:
    """Mean / min / max per metric and per-case pass counts. Flaky = passed in some runs but not all."""
    stats = {}
    for k in REPEAT_METRICS:
        vals = [r["summary"].get(k) for r in runs if r["summary"].get(k) is not None]
        if vals:
            stats[k] = {"mean": round(statistics.fmean(vals), 2), "min": min(vals), "max": max(vals),
                        "stdev": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0}
    per_case: dict[str, int] = Counter()
    for r in runs:
        for x in r["cases"]:
            per_case[x["id"]] += int(x["passed"])
    n = len(runs)
    return {"runs": n, "metrics": stats, "case_pass_counts": dict(per_case),
            "flaky": sorted(k for k, v in per_case.items() if 0 < v < n),
            "always_fail": sorted(k for k, v in per_case.items() if v == 0)}


def repeat(mode: str, n: int) -> int:
    runs, codes = [], []
    for i in range(n):
        print(f"\n### repeat {i + 1}/{n}  --mode {mode}", flush=True)
        codes.append(subprocess.run([sys.executable, "-m", "evals.run", "--mode", mode], cwd=ROOT).returncode)
        runs.append(json.loads((RES_DIR / f"latest_{mode}.json").read_text(encoding="utf-8")))
    agg = {"mode": mode, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **aggregate(runs)}
    (RES_DIR / f"latest_{mode}_repeat.json").write_text(json.dumps(agg, indent=2), encoding="utf-8")
    print(f"\nREPEAT SUMMARY [{mode}] - {n} runs  (-> evals/results/latest_{mode}_repeat.json)")
    print(f"  {'metric':26s} {'mean':>9s} {'min':>9s} {'max':>9s} {'stdev':>8s}")
    for k, v in agg["metrics"].items():
        print(f"  {k:26s} {v['mean']:>9} {v['min']:>9} {v['max']:>9} {v['stdev']:>8}")
    print(f"  flaky cases (pass in some runs only): {agg['flaky'] or 'none'}")
    print(f"  always failing: {agg['always_fail'] or 'none'}")
    return max(codes)


# ---------------------------------------------------------------- targets + CI gate
def target_ms(cfg: dict, mode: str, live: bool) -> float | None:
    t = cfg.get("targets", {})
    return t.get("p95_latency_ms_agent") if live else t.get("p95_latency_ms_pipeline")


def gate_results(summary: dict, cfg: dict) -> list[tuple[str, object, str, bool]]:
    """Each gate limit -> (metric, value, limit text, ok). A missing metric value fails closed."""
    g = cfg["targets"]["gate"]
    limits = [("gated_pass_rate", ">=", g["min_pass_rate"]), ("routing_accuracy", ">=", g["min_routing_accuracy"]),
              ("citation_hit_rate", ">=", g["min_citation_hit_rate"]),
              ("abstain_correctness", ">=", g["min_abstain_correctness"]),
              ("safety_inclusion_recall", ">=", g["min_safety_inclusion_recall"]),
              ("draft_leak_count", "<=", g["max_draft_leaks"]), ("fallback_count", "<=", g["max_fallbacks"])]
    if not summary.get("live"):           # live latency varies with the API: reported, never gated
        limits.append(("p95_latency_ms", "<=", summary.get("p95_target_ms")))
    out = []
    gm = summary.get("gated_metrics") or {}
    for k, op, lim in limits:
        v = gm[k] if k in gm else summary.get(k)
        ok = v is not None and lim is not None and (v >= lim if op == ">=" else v <= lim)
        out.append((k, v, f"{op} {lim}", ok))
    return out


def gate_check(summary: dict, cfg: dict) -> int:
    rows = gate_results(summary, cfg)
    print("\nEVAL GATE (limits in config.yaml -> targets.gate)")
    for k, v, lim, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {k:26s} {str(v):>8s}  {lim}")
    failed = [k for k, _, _, ok in rows if not ok]
    print(f"EVAL GATE: {'FAILED (' + ', '.join(failed) + ')' if failed else 'PASSED'}")
    return 1 if failed else 0


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
    ap.add_argument("--repeat", type=int, default=1, metavar="N",
                    help="run the set N times (fresh DB each) and report mean/min/max and flaky cases")
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 unless every limit in config.yaml targets.gate holds (used by CI)")
    a = ap.parse_args(argv)
    if a.compare:
        return compare()
    if a.repeat > 1:
        return repeat(a.mode, a.repeat)
    return run_mode(a.mode, gate=a.gate)


if __name__ == "__main__":
    raise SystemExit(main())
