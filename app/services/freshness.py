"""Document freshness: every published document has a review-due date; answers say when a source is overdue.

Review date = last time someone confirmed the content is current: `last_reviewed_at` (set by "Confirm still
current"), else the approval date (`reviewed_at`), else the creation date. Due = review date + the category's
interval from config.yaml -> freshness.review_interval_days (an ASSUMPTION TO CONFIRM, not a client policy).

Policy: an overdue document is still used. Silently dropping a safety procedure from answers is worse than
answering with a visible warning. Each answer that cites an overdue document gets the flag
`overdue_source_document` and a plain note naming the document, its due date and its owner.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.config import get_config
from app.data import db
from app.services import audit

STATES = ("current", "due_soon", "overdue")
REVIEW_ROLES = ("manager", "admin")


def _cfg() -> dict:
    f = get_config().get("freshness") or {}
    return {"intervals": f.get("review_interval_days") or {}, "default": int(f.get("default_interval_days", 365)),
            "due_soon": int(f.get("due_soon_days", 30))}


def interval_days(category: str) -> int:
    c = _cfg()
    return int(c["intervals"].get(category, c["default"]))


def _to_date(ts: str | None) -> date | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def status_for(doc: dict, today: date | None = None) -> dict:
    """{last_reviewed, review_due, days_left, state} for one document row (needs category + date columns)."""
    today = today or datetime.now(timezone.utc).date()
    last = _to_date(doc.get("last_reviewed_at")) or _to_date(doc.get("reviewed_at")) or _to_date(doc.get("created_at"))
    if last is None:                                   # no date at all: treat as overdue (fail safe)
        return {"last_reviewed": None, "review_due": None, "days_left": None, "state": "overdue"}
    due = last + timedelta(days=interval_days(doc["category"]))
    left = (due - today).days
    state = "overdue" if left < 0 else "due_soon" if left <= _cfg()["due_soon"] else "current"
    return {"last_reviewed": last.isoformat(), "review_due": due.isoformat(), "days_left": left, "state": state}


_COLS = """doc_id, doc_key, title, category, version, owner, created_at, reviewed_at, last_reviewed_at"""


def overview(today: date | None = None) -> list[dict]:
    """Published documents with their review status, most urgent first."""
    out = [{**d, **status_for(d, today)} for d in
           db.rows(f"SELECT {_COLS} FROM documents WHERE status='published'")]
    return sorted(out, key=lambda d: (d["days_left"] is not None, d["days_left"] or 0, d["doc_key"]))


def counts(today: date | None = None) -> dict:
    rows = overview(today)
    return {s: sum(r["state"] == s for r in rows) for s in STATES} | {"total": len(rows)}


def for_docs(doc_ids: list[str], today: date | None = None) -> dict[str, dict]:
    ids = list(dict.fromkeys(doc_ids))
    if not ids:
        return {}
    rows = db.rows(f"SELECT {_COLS} FROM documents WHERE doc_id IN ({','.join('?' * len(ids))})", tuple(ids))
    return {r["doc_id"]: {**r, **status_for(r, today)} for r in rows}


def annotate(resp, today: date | None = None) -> None:
    """Add review_due / review_state to each citation; flag and explain overdue sources (in place)."""
    info = for_docs([c.doc_id for c in resp.citations], today)
    noted: set[str] = set()
    for c in resp.citations:
        i = info.get(c.doc_id)
        if not i:
            continue
        c.review_due, c.review_state = i["review_due"], i["state"]
        if i["state"] == "overdue" and c.doc_id not in noted:
            noted.add(c.doc_id)
            due = f"was due for review on {i['review_due']}" if i["review_due"] else "has no review date"
            resp.freshness_notes.append(f"{c.doc_key} ({c.doc_title}) {due}. Check with the owner "
                                        f"({i['owner']}) that it is still current before relying on it.")
    if noted and "overdue_source_document" not in resp.guardrail_flags:
        resp.guardrail_flags.append("overdue_source_document")


def confirm_current(doc_id: str, actor: str, actor_role: str, note: str = "") -> dict:
    """Manager confirms a published document is still correct: restarts its review interval. Audited."""
    if actor_role not in REVIEW_ROLES:
        raise PermissionError("Only a Plant Manager or Administrator can confirm a document review.")
    note = (note or "").strip()
    if len(note) < 5:
        raise ValueError("Please say what you checked (at least 5 characters).")
    now = db.now_iso()
    with db.tx() as c:
        row = c.execute("SELECT status FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown document {doc_id}")
        if row["status"] != "published":
            raise ValueError(f"{doc_id} is {row['status']}; only published documents have review dates.")
        c.execute("UPDATE documents SET last_reviewed_at=? WHERE doc_id=?", (now, doc_id))
        c.execute("INSERT INTO document_reviews (doc_id, ts, actor, action, comment) VALUES (?,?,?,?,?)",
                  (doc_id, now, actor, "comment", f"Confirmed still current: {note}"[:2000]))
        audit.record(actor, actor_role, "confirm_document_current", doc_id, "current",
                     details={"note": note[:300]}, conn=c)
    return {"doc_id": doc_id, **for_docs([doc_id])[doc_id]}
