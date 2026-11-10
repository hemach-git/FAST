"""Seed SYNTHETIC data: 18 published docs, 1 pending draft, ~30 days of query history + feedback.

Every seeded row has is_synthetic=1. Run directly to rebuild:  python -m app.data.seed_synthetic --reset
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from app.config import db_path
from app.data import db

CORPUS = Path(__file__).parent / "corpus"

# (question template, weight) - weights create realistic recurring issues for analytics.
HISTORY = [
    ("CNC-04 spindle overheating alarm 2041, what do I do?", 14),
    ("Spindle alarm 2041 came back again on CNC-04", 9),
    ("How do I clear a jam on Conveyor C3?", 12),
    ("Conveyor C3 belt keeps drifting to one side", 6),
    ("Conveyor C3 motor overload keeps tripping", 5),
    ("Press Line 2 hydraulic pressure is low", 7),
    ("Hydraulic oil leak on Press Line 2, what should I do?", 4),
    ("Steps for a die change on Press Line 2", 5),
    ("What is the torque spec for M10 bolts?", 8),
    ("Torque reading out of tolerance, what do I do with the parts?", 4),
    ("What PPE is required in Paint Booth 1?", 6),
    ("What are the lockout tagout steps?", 6),
    ("Paint orange peel defect on parts from booth 1", 5),
    ("What is the dry film thickness spec for paint?", 3),
    ("Plant air pressure dropping on compressor AC-2", 4),
    ("Robot Cell R7 fault recovery steps", 4),
    ("Weld porosity acceptance criteria", 3),
    ("How do I handle nonconforming parts with a hold tag?", 4),
    ("SPC chart shows 7 points above center line", 3),
    ("CNC-04 bore diameter tolerance", 3),
    ("Light curtain did not stop the press at shift start", 3),
    ("Forklift pre-use inspection checklist", 2),
    ("How often do we grease conveyor bearings?", 2),
    ("Chemical spill of paint thinner near booth", 2),
    ("What is the cafeteria menu today?", 2),
    ("Who won the football game last night?", 1),
]
USERS = ["sup.alvarez", "sup.chen", "sup.okafor", "sup.patel", "sup.nguyen", "sup.smith"]


def parse_md(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    _, fm, body = text.split("---", 2)
    meta = yaml.safe_load(fm)
    return {**meta, "content": body.strip()}


def seed_documents() -> None:
    from app.services.docs_admin import _insert
    from app.models.schemas import DocumentIn
    from app.services import audit

    base = datetime.now(timezone.utc) - timedelta(days=60)
    with db.tx() as c:
        for p in sorted(CORPUS.glob("*/*.md")):
            if p.parent.name == "drafts":
                continue
            m = parse_md(p)
            doc = DocumentIn(**{k: m[k] for k in ("doc_key", "title", "category", "owner", "equipment", "content")})
            doc_id = _insert(c, doc, 1, "published", "seed", is_synthetic=1)
            c.execute("UPDATE documents SET created_at=?, reviewed_by='qa.manager', reviewed_at=?, review_note=? WHERE doc_id=?",
                      (base.isoformat(), base.isoformat(), "Initial synthetic baseline", doc_id))
            audit.record("seed", "system", "seed_document", doc_id, "published", conn=c)
        for p in sorted((CORPUS / "drafts").glob("*.md")):
            m = parse_md(p)
            doc = DocumentIn(**{k: m[k] for k in ("doc_key", "title", "category", "owner", "equipment", "content")})
            v = c.execute("SELECT MAX(version) v FROM documents WHERE doc_key=?", (doc.doc_key,)).fetchone()["v"] or 0
            doc_id = _insert(c, doc, v + 1, "draft", "qa.engineer", is_synthetic=1)
            audit.record("qa.engineer", "manager", "submit_draft", doc_id, "draft", conn=c)


def seed_history(n: int = 320, days: int = 30, rng_seed: int = 7) -> None:
    from app.services import retriever, workflow

    retriever.invalidate()
    rng = random.Random(rng_seed)
    qs, ws = zip(*HISTORY)
    now = datetime.now(timezone.utc)
    global _real_agents_enabled
    _real_agents_enabled = workflow.agents_enabled
    workflow.agents_enabled = lambda: False   # seeding must never call a paid LLM (restored by seed())
    for i in range(n):
        q = rng.choices(qs, ws)[0]
        # Ramp: more queries in recent days (adoption growth), shift hours 6-22.
        day = int(days * (1 - rng.random() ** 1.6))
        ts = (now - timedelta(days=day)).replace(hour=rng.randint(6, 21), minute=rng.randint(0, 59), second=0, microsecond=0)
        resp = workflow.ask(q, "supervisor", rng.choice(USERS), is_synthetic=1, created_at=ts.isoformat())
        if resp.query_id and rng.random() < 0.3:
            rating = -1 if (resp.status != "ok" and rng.random() < 0.7) or rng.random() < 0.08 else 1
            status = "n/a" if rating == 1 else rng.choice(["pending", "approved", "rejected"]) if day > 2 else "pending"
            with db.tx() as c:
                c.execute("""INSERT INTO feedback (query_id, created_at, user_name, rating, comment, review_status,
                             reviewed_by, reviewed_at, is_synthetic) VALUES (?,?,?,?,?,?,?,?,1)""",
                          (resp.query_id, ts.isoformat(), "sup.chen", rating,
                           "" if rating == 1 else rng.choice(["Answer did not match the floor setup",
                                                              "Needed more detail", "Not in the docs?"]),
                           status, None if status in ("n/a", "pending") else "plant.manager",
                           None if status in ("n/a", "pending") else ts.isoformat()))


def seed(reset: bool = False) -> None:
    if reset and db_path().exists():
        db_path().unlink()
    db.init_db()
    if db.rows("SELECT 1 FROM documents LIMIT 1"):
        return
    seed_documents()
    try:
        seed_history()
    finally:
        from app.services import workflow
        workflow.agents_enabled = _real_agents_enabled


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    seed(reset=a.reset)
    print(db.rows("SELECT (SELECT COUNT(*) FROM documents) docs, (SELECT COUNT(*) FROM chunks) chunks, "
                  "(SELECT COUNT(*) FROM queries) queries, (SELECT COUNT(*) FROM feedback) feedback")[0])
