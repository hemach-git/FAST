"""Eval upgrades: golden-set validation, known gaps (xfail), repeat-run aggregation, feedback harvesting."""
from __future__ import annotations

import json

import pytest

from app.data import db
from app.services import workflow
from evals import harvest, run


# ---------------------------------------------------------------- golden set
def golden():
    return [json.loads(l) for l in (run.ROOT / "evals" / "golden.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]


def test_golden_set_is_valid_and_bigger():
    cases = golden()
    run.validate_cases(cases)                        # raises SystemExit if broken
    assert len(cases) >= 67
    assert sum(c["type"] == "near_miss" for c in cases) >= 4
    assert all(c["expect_status"] == "abstained" for c in cases if c["type"] == "near_miss")


def test_every_published_document_is_covered_by_a_case():
    cited = {c.get("expect_cited_doc") for c in golden()}
    corpus = {f.stem for cat in ("safety", "maintenance", "quality")          # seeded corpus (tests add other docs)
              for f in (run.ROOT / "app" / "data" / "corpus" / cat).glob("*.md")}
    assert len(corpus) == 18 and corpus <= cited, f"documents without a golden case: {sorted(corpus - cited)}"


@pytest.mark.parametrize("bad, msg", [
    ([{"id": "X1", "type": "t", "question": "q", "expect_status": "TODO"}], "TODO"),
    ([{"id": "X1", "type": "t", "question": "q", "expect_status": "ok"}] * 2, "duplicate"),
    ([{"id": "X1", "type": "t", "question": "q"}], "missing"),
    ([{"id": "X1", "type": "t", "question": "q", "expect_status": "maybe"}], "bad expect_status"),
    ([{"id": "X1", "type": "t", "question": "q", "expect_status": "ok", "known_gap": {"live": "x"}}], "known_gap mode"),
])
def test_validation_rejects_broken_cases(bad, msg):
    with pytest.raises(SystemExit, match=msg):
        run.validate_cases(bad)


def test_known_gaps_have_a_reason():
    for c in golden():
        for mode, why in (c.get("known_gap") or {}).items():
            assert mode in run.MODES and len(why) > 20, c["id"]


# ---------------------------------------------------------------- repeat runs
def fake_run(passed: dict, p95: float):
    return {"summary": {"pass_rate": 100.0 * sum(passed.values()) / len(passed), "p95_latency_ms": p95},
            "cases": [{"id": k, "passed": v} for k, v in passed.items()]}


def test_aggregate_finds_flaky_and_always_failing_cases():
    runs = [fake_run({"A": True, "B": True, "C": False}, 8000),
            fake_run({"A": True, "B": False, "C": False}, 10000),
            fake_run({"A": True, "B": True, "C": False}, 9000)]
    agg = run.aggregate(runs)
    assert agg["runs"] == 3 and agg["flaky"] == ["B"] and agg["always_fail"] == ["C"]
    assert agg["case_pass_counts"] == {"A": 3, "B": 2, "C": 0}
    p95 = agg["metrics"]["p95_latency_ms"]
    assert (p95["mean"], p95["min"], p95["max"]) == (9000, 8000, 10000) and p95["stdev"] == 1000.0


def test_aggregate_single_run_has_zero_stdev():
    assert run.aggregate([fake_run({"A": True}, 5)])["metrics"]["p95_latency_ms"]["stdev"] == 0.0


# ---------------------------------------------------------------- harvesting flagged answers
def test_harvest_turns_flagged_answers_into_todo_candidates(tmp_path, monkeypatch):
    from app.config import db_path
    q_new = "Press Line 2 die is sticking after the change, harvest test"
    r = workflow.ask(q_new, "supervisor", "sup.test")
    golden_q = golden()[0]["question"]
    r2 = workflow.ask(golden_q, "supervisor", "sup.test")
    with db.tx() as c:
        for qid, status in ((r.query_id, "pending"), (r2.query_id, "approved"), (r.query_id, "rejected")):
            c.execute("""INSERT INTO feedback (query_id, created_at, user_name, rating, comment, review_status)
                         VALUES (?,?,?,?,?,?)""", (qid, db.now_iso(), "sup.test", -1, "wrong steps", status))
    out = tmp_path / "candidates.jsonl"
    golden_file = run.ROOT / "evals" / "golden.jsonl"
    new = harvest.harvest(db_path(), out, golden_file)
    qs = [c["question"] for c in new]
    assert q_new in qs, "pending flag on a new question must become a candidate"
    assert golden_q not in qs, "questions already in the golden set are skipped"
    cand = next(c for c in new if c["question"] == q_new)
    assert cand["expect_status"] == "TODO" and cand["type"] == "from_feedback"
    assert cand["source"]["user_comment"] == "wrong steps" and cand["source"]["observed_status"] == r.status
    with pytest.raises(SystemExit, match="TODO"):
        run.validate_cases([cand])                 # a draft can never be scored by accident
    assert harvest.harvest(db_path(), out, golden_file) == [], "second harvest adds nothing (dedup)"
    assert len(out.read_text(encoding="utf-8").splitlines()) == len(new)


def test_harvest_skips_rejected_and_synthetic(tmp_path):
    from app.config import db_path
    q = "Conveyor C3 belt squeals loudly, rejected-flag test"
    r = workflow.ask(q, "supervisor", "sup.test")
    q2 = workflow.ask("Robot Cell R7 teach pendant frozen, synthetic test", "supervisor", "sup.test")
    with db.tx() as c:
        c.execute("""INSERT INTO feedback (query_id, created_at, user_name, rating, comment, review_status, is_synthetic)
                     VALUES (?,?,?,?,?,?,?)""", (r.query_id, db.now_iso(), "sup.test", -1, "x", "rejected", 0))
        c.execute("""INSERT INTO feedback (query_id, created_at, user_name, rating, comment, review_status, is_synthetic)
                     VALUES (?,?,?,?,?,?,?)""", (q2.query_id, db.now_iso(), "seed", -1, "x", "pending", 1))
    qs = [c["question"] for c in harvest.harvest(db_path(), tmp_path / "c.jsonl", tmp_path / "none.jsonl")]
    assert q not in qs and "Robot Cell R7 teach pendant frozen, synthetic test" not in qs
