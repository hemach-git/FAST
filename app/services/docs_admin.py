"""Document lifecycle: draft -> (manager approve) -> published (old version retired) | rejected.

Supervisors and the answer pipeline only ever see PUBLISHED versions.
"""
from __future__ import annotations

import difflib
import json
import re

from app.data import db
from app.models.schemas import DocumentIn, ReviewDecision
from app.observability.logging import log_event
from app.services import audit, retriever
from app.services.guardrails import scan_document

_H2 = re.compile(r"^##\s+(.*)$", re.M)


def split_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown on '## ' headings. Content before the first heading becomes 'General'."""
    parts = _H2.split(content)
    out = []
    if parts[0].strip():
        out.append(("General", parts[0].strip()))
    for i in range(1, len(parts), 2):
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body:
            out.append((parts[i].strip(), body))
    return out


def _insert(c, doc: DocumentIn, version: int, status: str, actor: str, is_synthetic: int) -> str:
    doc_id = f"{doc.doc_key}-v{version}"
    flags = scan_document(doc.content)
    c.execute("""INSERT INTO documents (doc_id, doc_key, title, category, version, status, owner, equipment,
                 content, created_by, created_at, guardrail_flags, is_synthetic)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (doc_id, doc.doc_key, doc.title, doc.category, version, status, doc.owner, doc.equipment,
               doc.content, actor, db.now_iso(), json.dumps(flags), is_synthetic))
    for n, (section, text) in enumerate(split_sections(doc.content), 1):
        c.execute("INSERT INTO chunks (chunk_id, doc_id, section_no, section, text) VALUES (?,?,?,?,?)",
                  (f"{doc_id}#s{n}", doc_id, n, section, text))
    return doc_id


def list_documents(role: str = "supervisor", status: str | None = None, category: str | None = None) -> list[dict]:
    """Supervisors: published only. Managers: all statuses (optionally filtered)."""
    sql = """SELECT d.doc_id, d.doc_key, d.title, d.category, d.version, d.status, d.owner, d.equipment,
                    d.created_by, d.created_at, d.reviewed_by, d.reviewed_at, d.review_note,
                    d.guardrail_flags, d.is_synthetic, COUNT(c.chunk_id) AS sections,
                    CASE WHEN d.status != 'draft' THEN d.status
                         WHEN (SELECT action FROM document_reviews r WHERE r.doc_id = d.doc_id
                               ORDER BY r.review_id DESC LIMIT 1) = 'advise' THEN 'changes_requested'
                         ELSE 'awaiting_review' END AS review_state
             FROM documents d LEFT JOIN chunks c ON c.doc_id = d.doc_id WHERE 1=1"""
    params: list = []
    if role not in ("manager", "admin"):
        sql += " AND d.status='published'"
    elif status:
        sql += " AND d.status=?"
        params.append(status)
    if category:
        sql += " AND d.category=?"
        params.append(category)
    sql += " GROUP BY d.doc_id ORDER BY d.category, d.doc_key, d.version DESC"
    return db.rows(sql, tuple(params))


def get_document(doc_id: str, role: str = "supervisor") -> dict | None:
    r = db.rows("SELECT * FROM documents WHERE doc_id=?", (doc_id,))
    if not r or (role not in ("manager", "admin") and r[0]["status"] != "published"):
        return None
    r[0]["sections"] = db.rows("SELECT chunk_id, section_no, section, text FROM chunks WHERE doc_id=? ORDER BY section_no", (doc_id,))
    return r[0]


def get_chunk(chunk_id: str) -> dict | None:
    r = db.rows("""SELECT c.*, d.doc_key, d.title, d.version, d.status, d.category FROM chunks c
                   JOIN documents d ON d.doc_id=c.doc_id WHERE c.chunk_id=?""", (chunk_id,))
    return r[0] if r else None


def submit_draft(doc: DocumentIn | dict, actor: str, is_synthetic: int = 0) -> str:
    """Create a new draft version (new doc_key -> v1, existing -> next version). Never auto-publishes."""
    doc = DocumentIn.model_validate(doc)
    with db.tx() as c:
        existing = c.execute("SELECT category, MAX(version) v FROM documents WHERE doc_key=?", (doc.doc_key,)).fetchone()
        if existing["v"] and existing["category"] != doc.category:
            raise ValueError(f"{doc.doc_key} is a {existing['category']} document; category cannot change.")
        if c.execute("SELECT 1 FROM documents WHERE doc_key=? AND status='draft'", (doc.doc_key,)).fetchone():
            raise ValueError(f"{doc.doc_key} already has a pending draft. Approve or reject it first.")
        version = (existing["v"] or 0) + 1
        doc_id = _insert(c, doc, version, "draft", actor, is_synthetic)
        audit.record(actor, "manager", "submit_draft", doc_id, "draft", details={"title": doc.title}, conn=c)
        _log_review(c, doc_id, actor, "submit", doc.title)
    log_event("doc_draft", doc_id=doc_id, actor=actor)
    return doc_id


def review_document(doc_id: str, decision: ReviewDecision | dict) -> dict:
    """Manager approval gate. Approve -> publish + retire previous published version + re-index."""
    d = ReviewDecision.model_validate(decision)
    with db.tx() as c:
        row = c.execute("SELECT doc_key, status FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown document {doc_id}")
        if row["status"] != "draft":
            raise ValueError(f"{doc_id} is {row['status']}, only drafts can be reviewed.")
        new_status = "published" if d.decision == "approved" else "rejected"
        retired = []
        if new_status == "published":
            retired = [r["doc_id"] for r in c.execute(
                "SELECT doc_id FROM documents WHERE doc_key=? AND status='published'", (row["doc_key"],))]
            c.execute("UPDATE documents SET status='retired' WHERE doc_key=? AND status='published'", (row["doc_key"],))
        c.execute("""UPDATE documents SET status=?, reviewed_by=?, reviewed_at=?, review_note=? WHERE doc_id=?""",
                  (new_status, d.reviewer, db.now_iso(), d.note, doc_id))
        audit.record(d.reviewer, "manager", "review_document", doc_id, d.decision,
                     details={"retired": retired, "note": d.note}, conn=c)
        _log_review(c, doc_id, d.reviewer, "approve" if d.decision == "approved" else "reject", d.note)
    retriever.invalidate()
    log_event("doc_review", doc_id=doc_id, decision=d.decision)
    return {"doc_id": doc_id, "status": new_status, "retired": retired}


def retire_document(doc_id: str, actor: str, note: str = "") -> dict:
    with db.tx() as c:
        n = c.execute("UPDATE documents SET status='retired' WHERE doc_id=? AND status='published'", (doc_id,)).rowcount
        if not n:
            raise ValueError(f"{doc_id} is not a published document.")
        audit.record(actor, "manager", "retire_document", doc_id, "retired", details={"note": note}, conn=c)
    retriever.invalidate()
    return {"doc_id": doc_id, "status": "retired"}


# ---------------------------------------------------------------- advise / resubmit / history / diff
REVIEW_ROLES = ("manager", "admin")


def _log_review(c, doc_id: str, actor: str, action: str, comment: str = "") -> None:
    c.execute("INSERT INTO document_reviews (doc_id, ts, actor, action, comment) VALUES (?,?,?,?,?)",
              (doc_id, db.now_iso(), actor, action, (comment or "")[:2000]))


def _need_reviewer(actor_role: str) -> None:
    if actor_role not in REVIEW_ROLES:
        raise PermissionError("Only a Plant Manager or Administrator can review documents.")


def list_reviews(doc_id: str) -> list[dict]:
    """Conversation and decisions on one draft, oldest first."""
    return db.rows("SELECT ts, actor, action, comment FROM document_reviews WHERE doc_id=? ORDER BY review_id", (doc_id,))


def advise_document(doc_id: str, reviewer: str, comment: str, actor_role: str = "manager") -> dict:
    """Send a draft back with advice. It stays a draft (not visible to supervisors) until resubmitted and approved."""
    _need_reviewer(actor_role)
    comment = (comment or "").strip()
    if len(comment) < 5:
        raise ValueError("Please write what should change (at least 5 characters).")
    with db.tx() as c:
        row = c.execute("SELECT status FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown document {doc_id}")
        if row["status"] != "draft":
            raise ValueError(f"{doc_id} is {row['status']}; only drafts can receive advice.")
        _log_review(c, doc_id, reviewer, "advise", comment)
        audit.record(reviewer, actor_role, "advise_document", doc_id, "changes_requested",
                     details={"comment": comment[:300]}, conn=c)
    log_event("doc_review", doc_id=doc_id, decision="advise")
    return {"doc_id": doc_id, "review_state": "changes_requested"}


def comment_on_document(doc_id: str, actor: str, comment: str, actor_role: str = "manager") -> None:
    _need_reviewer(actor_role)
    if len((comment or "").strip()) < 2:
        raise ValueError("Comment is empty.")
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM documents WHERE doc_id=?", (doc_id,)).fetchone():
            raise ValueError(f"Unknown document {doc_id}")
        _log_review(c, doc_id, actor, "comment", comment)


def resubmit_draft(doc_id: str, editor: str, content: str, title: str | None = None, note: str = "",
                   actor_role: str = "manager") -> dict:
    """Edit a draft after advice and put it back in the review queue. Re-scans and re-chunks the text."""
    _need_reviewer(actor_role)
    check = DocumentIn.model_validate({"doc_key": "TMP-CHECK", "title": title or "Resubmitted draft", "category": "quality",
                                       "owner": "check", "content": content})   # validates length / shape of the text
    with db.tx() as c:
        row = c.execute("SELECT status, title FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown document {doc_id}")
        if row["status"] != "draft":
            raise ValueError(f"{doc_id} is {row['status']}; only drafts can be edited.")
        new_title = (title or row["title"]).strip()
        c.execute("UPDATE documents SET content=?, title=?, guardrail_flags=? WHERE doc_id=?",
                  (check.content, new_title, json.dumps(scan_document(check.content)), doc_id))
        c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        for n, (section, text) in enumerate(split_sections(check.content), 1):
            c.execute("INSERT INTO chunks (chunk_id, doc_id, section_no, section, text) VALUES (?,?,?,?,?)",
                      (f"{doc_id}#s{n}", doc_id, n, section, text))
        _log_review(c, doc_id, editor, "resubmit", note or "Updated after review advice")
        audit.record(editor, actor_role, "resubmit_draft", doc_id, "awaiting_review", details={"note": note[:300]}, conn=c)
    retriever.invalidate()
    return {"doc_id": doc_id, "review_state": "awaiting_review"}


def diff_against_published(doc_id: str) -> dict:
    """Unified diff of a draft against the currently published version of the same document (if any)."""
    new = get_document(doc_id, "manager")
    if not new:
        raise ValueError(f"Unknown document {doc_id}")
    cur = db.rows("SELECT doc_id, version, content FROM documents WHERE doc_key=? AND status='published'", (new["doc_key"],))
    old_txt = cur[0]["content"] if cur else ""
    diff = list(difflib.unified_diff(old_txt.splitlines(), new["content"].splitlines(),
                                     f"published v{cur[0]['version']}" if cur else "none", f"draft v{new['version']}",
                                     lineterm="", n=2))
    added = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
    return {"published_doc_id": cur[0]["doc_id"] if cur else None, "published_version": cur[0]["version"] if cur else None,
            "old": old_txt, "new": new["content"], "diff": "\n".join(diff), "added": added, "removed": removed}
