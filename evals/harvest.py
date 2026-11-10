"""Turn flagged answers (thumbs-down feedback) into DRAFT golden cases for human review.

    python -m evals.harvest                       # data/fast.db (or FAST_DB_PATH) -> evals/candidates.jsonl
    python -m evals.harvest --include-synthetic   # also seeded demo feedback
    python -m evals.harvest --status approved     # only flags a manager confirmed (default: approved + pending)

Each candidate carries what happened (observed status, cited documents, the user's comment, the manager's note)
and `"expect_status": "TODO"`. A person decides the correct expectation, edits the line, and moves it into
evals/golden.jsonl. The eval runner refuses any case that still contains "TODO", so a draft can never be
scored by accident. Flags a manager REJECTED (the answer was fine) are skipped. Questions are already PII-masked.
Candidates already in golden.jsonl or candidates.jsonl (same question, case-insensitive) are not added twice.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _norm(q: str) -> str:
    return " ".join((q or "").lower().split())


def load_flagged(db_file: Path, statuses: tuple[str, ...], include_synthetic: bool) -> list[dict]:
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)      # read-only: harvesting never changes the DB
    con.row_factory = sqlite3.Row
    marks = ",".join("?" * len(statuses))
    sql = f"""SELECT f.feedback_id, f.comment, f.review_status, f.review_note, f.is_synthetic,
                     q.query_id, q.question_masked AS question, q.status AS observed_status,
                     q.primary_category, q.routed_categories, q.mode,
                     (SELECT group_concat(c.chunk_id, ' ') FROM citations c WHERE c.query_id = q.query_id) AS cited
              FROM feedback f JOIN queries q ON q.query_id = f.query_id
              WHERE f.rating = -1 AND f.review_status IN ({marks}) {'' if include_synthetic else 'AND f.is_synthetic = 0'}
              ORDER BY f.feedback_id"""
    try:
        return [dict(r) for r in con.execute(sql, statuses)]
    finally:
        con.close()


def to_candidate(row: dict) -> dict:
    cited = (row.get("cited") or "").split()
    return {
        "id": f"FB-{row['feedback_id']}", "type": "from_feedback", "question": row["question"],
        "expect_status": "TODO",
        "source": {"query_id": row["query_id"], "review_status": row["review_status"],
                   "user_comment": row.get("comment") or "", "manager_note": row.get("review_note") or "",
                   "observed_status": row["observed_status"], "observed_mode": row.get("mode"),
                   "observed_primary": row.get("primary_category"),
                   "observed_cited_docs": sorted({c.split("-v")[0] for c in cited})},
        "how_to_promote": "set expect_status (ok/abstained) and expect_cited_doc / expect_primary as the documents "
                          "require, delete source + how_to_promote if you like, then move this line to golden.jsonl",
    }


def existing_questions(*files: Path) -> set[str]:
    out = set()
    for f in files:
        if f.exists():
            out |= {_norm(json.loads(l).get("question", "")) for l in f.read_text(encoding="utf-8").splitlines()
                    if l.strip()}
    return out


def harvest(db_file: Path, out_file: Path, golden: Path, statuses=("approved", "pending"),
            include_synthetic: bool = False) -> list[dict]:
    seen = existing_questions(golden, out_file)
    new = []
    for row in load_flagged(db_file, tuple(statuses), include_synthetic):
        if not row["question"] or _norm(row["question"]) in seen:
            continue
        seen.add(_norm(row["question"]))
        new.append(to_candidate(row))
    if new:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with out_file.open("a", encoding="utf-8") as f:
            for c in new:
                f.write(json.dumps(c) + "\n")
    return new


def main(argv: list[str] | None = None) -> int:
    from app.config import db_path
    ap = argparse.ArgumentParser(description="Flagged answers -> draft golden cases (human review required)")
    ap.add_argument("--db", type=Path, default=None, help="SQLite file (default: FAST_DB_PATH or data/fast.db)")
    ap.add_argument("--out", type=Path, default=ROOT / "evals" / "candidates.jsonl")
    ap.add_argument("--status", choices=("approved", "pending", "both"), default="both")
    ap.add_argument("--include-synthetic", action="store_true", help="also harvest seeded demo feedback")
    a = ap.parse_args(argv)
    db_file = a.db or db_path()
    if not db_file.exists():
        print(f"No database at {db_file}")
        return 1
    statuses = ("approved", "pending") if a.status == "both" else (a.status,)
    new = harvest(db_file, a.out, ROOT / "evals" / "golden.jsonl", statuses, a.include_synthetic)
    print(f"{len(new)} new candidate(s) -> {a.out}")
    for c in new:
        print(f"  {c['id']:8s} [{c['source']['review_status']}] {c['question'][:70]!r} "
              f"(was {c['source']['observed_status']}, cited {c['source']['observed_cited_docs'] or 'nothing'})")
    if new:
        print("Next: set each expect_status (TODO) by hand, then move the line into evals/golden.jsonl.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
