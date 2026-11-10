"""Audit trail + answer feedback and the human review queue for flagged answers."""
from __future__ import annotations

import json

from app.data import db
from app.models.schemas import FeedbackIn, ReviewDecision
from app.observability.logging import log_event
from app.services.guardrails import mask_pii


def record(actor: str, role: str, action: str, target: str, decision: str | None = None,
           model: str | None = None, request_id: str | None = None, details: dict | None = None,
           conn=None) -> None:
    sql = """INSERT INTO audit_log (ts, actor, role, action, target, decision, model, request_id, details)
             VALUES (?,?,?,?,?,?,?,?,?)"""
    params = (db.now_iso(), actor, role, action, target, decision, model, request_id,
              json.dumps(details or {}, default=str))
    if conn is not None:
        conn.execute(sql, params)
    else:
        with db.tx() as c:
            c.execute(sql, params)


def list_audit(limit: int = 200, action: str | None = None) -> list[dict]:
    if action:
        return db.rows("SELECT * FROM audit_log WHERE action=? ORDER BY audit_id DESC LIMIT ?", (action, limit))
    return db.rows("SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT ?", (limit,))


def submit_feedback(fb: FeedbackIn | dict) -> int:
    """Thumbs up/down on an answer. Thumbs-down goes to the manager review queue."""
    fb = FeedbackIn.model_validate(fb)
    status = "pending" if fb.rating == -1 else "n/a"
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM queries WHERE query_id=?", (fb.query_id,)).fetchone():
            raise ValueError(f"Unknown query_id {fb.query_id}")
        cur = c.execute("""INSERT INTO feedback (query_id, created_at, user_name, rating, comment, review_status)
                           VALUES (?,?,?,?,?,?)""",
                        (fb.query_id, db.now_iso(), fb.user, fb.rating, mask_pii(fb.comment), status))
        fid = cur.lastrowid
        record(fb.user, "supervisor", "feedback", fb.query_id, "helpful" if fb.rating == 1 else "flagged",
               details={"feedback_id": fid}, conn=c)
    log_event("feedback", query_id=fb.query_id, rating=fb.rating)
    return fid


def list_review_queue(status: str = "pending") -> list[dict]:
    return db.rows("""SELECT f.feedback_id, f.created_at, f.user_name, f.comment, f.review_status,
                             f.reviewed_by, f.review_note, q.query_id, q.question_masked AS question,
                             q.primary_category, q.routed_categories, q.status AS answer_status,
                             q.equipment_tag, q.request_id
                      FROM feedback f JOIN queries q ON q.query_id = f.query_id
                      WHERE f.rating = -1 AND (? = 'all' OR f.review_status = ?)
                      ORDER BY f.created_at DESC""", (status, status))


def resolve_feedback(feedback_id: int, decision: ReviewDecision | dict) -> dict:
    """Manager decision on a flagged answer (human-in-the-loop). Recorded in the audit trail.
    approved = flag confirmed (doc/routing needs fixing); rejected = answer was fine."""
    d = ReviewDecision.model_validate(decision)
    with db.tx() as c:
        row = c.execute("SELECT review_status, query_id FROM feedback WHERE feedback_id=?", (feedback_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown feedback_id {feedback_id}")
        if row["review_status"] != "pending":
            raise ValueError(f"Feedback {feedback_id} already {row['review_status']}")
        c.execute("""UPDATE feedback SET review_status=?, reviewed_by=?, reviewed_at=?, review_note=?
                     WHERE feedback_id=?""", (d.decision, d.reviewer, db.now_iso(), mask_pii(d.note), feedback_id))
        record(d.reviewer, "manager", "review_flagged_answer", f"feedback:{feedback_id}", d.decision,
               details={"query_id": row["query_id"], "note": mask_pii(d.note)}, conn=c)
    return {"feedback_id": feedback_id, "review_status": d.decision}
