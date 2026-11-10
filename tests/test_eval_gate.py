"""CI eval gate (evals/run.py --gate): limits come from config.yaml targets.gate; missing metrics fail closed."""
from __future__ import annotations

import copy

from app.config import get_config
from evals import run

GOOD = {"live": False, "pass_rate": 100.0, "routing_accuracy": 100.0, "citation_hit_rate": 100.0,
        "abstain_correctness": 100.0, "safety_inclusion_recall": 100.0, "draft_leak_count": 0,
        "fallback_count": 0, "p95_latency_ms": 2.0, "p95_target_ms": 1000}


def failed(summary, cfg=None):
    return [k for k, _, _, ok in run.gate_results(summary, cfg or get_config()) if not ok]


def test_targets_are_configured_and_marked_as_assumption():
    t = get_config()["targets"]
    assert t["p95_latency_ms_agent"] == 8000 and t["p95_latency_ms_pipeline"] == 1000
    text = (run.ROOT / "config.yaml").read_text(encoding="utf-8")
    assert "ASSUMPTION TO CONFIRM" in text


def test_good_run_passes_gate():
    assert failed(GOOD) == [] and run.gate_check(GOOD, get_config()) == 0


def test_each_limit_can_fail_the_gate():
    for k, bad in [("pass_rate", 96.9), ("routing_accuracy", 90.0), ("citation_hit_rate", 80.0),
                   ("abstain_correctness", 99.0), ("safety_inclusion_recall", 90.0), ("draft_leak_count", 1),
                   ("fallback_count", 2), ("p95_latency_ms", 1500.0)]:
        assert failed({**GOOD, k: bad}) == [k], k
    assert run.gate_check({**GOOD, "draft_leak_count": 1}, get_config()) == 1


def test_missing_metric_fails_closed():
    assert failed({**GOOD, "citation_hit_rate": None}) == ["citation_hit_rate"]


def test_live_latency_is_reported_not_gated():
    assert "p95_latency_ms" not in failed({**GOOD, "live": True, "p95_latency_ms": 12000.0, "p95_target_ms": 8000})


def test_gate_limits_read_from_config():
    cfg = copy.deepcopy(get_config())
    cfg["targets"]["gate"]["min_pass_rate"] = 90.0
    assert failed({**GOOD, "pass_rate": 96.9}, cfg) == []


def test_target_picks_mode():
    cfg = get_config()
    assert run.target_ms(cfg, "multi_agent", True) == 8000 and run.target_ms(cfg, "pipeline", False) == 1000
