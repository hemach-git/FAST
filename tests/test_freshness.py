"""Document freshness: review-due dates, overdue warnings on answers, manager confirmation."""
from __future__ import annotations

from datetime import date

import pytest

from app.data import db
from app.services import docs_admin, freshness, workflow


def doc(category="safety", **dates):
    return {"category": category, "created_at": None, "reviewed_at": None, "last_reviewed_at": None, **dates}


TODAY = date(2026, 11, 10)


@pytest.mark.parametrize("last, category, state, due", [
    ("2026-06-01T00:00:00+00:00", "safety", "current", "2027-06-01"),
    ("2025-11-20T00:00:00+00:00", "safety", "due_soon", "2026-11-20"),       # 10 days left <= 30
    ("2025-11-01T00:00:00+00:00", "safety", "overdue", "2026-11-01"),
    ("2025-11-01T00:00:00+00:00", "maintenance", "current", "2027-11-01"),   # maintenance interval is 730 days
])
def test_status_uses_the_category_interval(last, category, state, due):
    s = freshness.status_for(doc(category, last_reviewed_at=last), TODAY)
    assert (s["state"], s["review_due"]) == (state, due)


def test_review_date_falls_back_to_approval_then_creation():
    assert freshness.status_for(doc(reviewed_at="2026-01-01T00:00:00+00:00",
                                    created_at="2020-01-01T00:00:00+00:00"), TODAY)["last_reviewed"] == "2026-01-01"
    assert freshness.status_for(doc(created_at="2026-02-02T00:00:00+00:00"), TODAY)["last_reviewed"] == "2026-02-02"


def test_no_date_at_all_fails_safe_as_overdue():
    assert freshness.status_for(doc(), TODAY)["state"] == "overdue"


def test_seeded_demo_has_overdue_and_due_soon_documents():
    states = {r["doc_key"]: r["state"] for r in freshness.overview()}
    assert states["SOP-FLT-06"] == "overdue" and states["MM-PM-06"] == "overdue"
    assert states["QC-SPC-04"] == "due_soon" and states["SOP-LOTO-04"] == "current"
    rows = freshness.overview()
    assert rows[0]["state"] == "overdue", "most urgent first"
    k = freshness.counts()
    assert k["total"] == k["overdue"] + k["due_soon"] + k["current"]


def test_answer_citing_an_overdue_document_is_kept_but_warned():
    r = workflow.ask("Forklift pre-use inspection checklist", "supervisor", "sup.test")
    assert r.status == "ok", "overdue documents are still used"
    flt = [c for c in r.citations if c.doc_key == "SOP-FLT-06"]
    assert flt and flt[0].review_state == "overdue" and flt[0].review_due
    assert "overdue_source_document" in r.guardrail_flags
    assert len(r.freshness_notes) == 1 and "SOP-FLT-06" in r.freshness_notes[0] and "owner" in r.freshness_notes[0]


def test_answer_from_current_documents_has_no_warning():
    r = workflow.ask("What are the lockout tagout steps?", "supervisor", "sup.test")
    assert r.status == "ok" and r.citations
    assert all(c.review_state in ("current", "due_soon") for c in r.citations)
    assert "overdue_source_document" not in r.guardrail_flags and not r.freshness_notes


def test_manager_confirmation_restarts_the_interval_and_is_audited():
    did = next(d["doc_id"] for d in docs_admin.list_documents("manager", "published") if d["doc_key"] == "MM-PM-06")
    res = freshness.confirm_current(did, "plant.manager", "manager", "walked the PM schedule with the lead")
    assert res["state"] == "current" and res["days_left"] >= 729
    assert db.rows("SELECT 1 FROM audit_log WHERE action='confirm_document_current' AND target=?", (did,))
    assert any(r["comment"].startswith("Confirmed still current") for r in docs_admin.list_reviews(did))


@pytest.mark.parametrize("role, note, err", [("supervisor", "checked it fully", PermissionError),
                                             ("manager", "ok", ValueError)])
def test_confirmation_needs_a_manager_and_a_note(role, note, err):
    did = next(d["doc_id"] for d in docs_admin.list_documents("manager", "published") if d["doc_key"] == "QC-SPC-04")
    with pytest.raises(err):
        freshness.confirm_current(did, "someone", role, note)


def test_only_published_documents_can_be_confirmed():
    draft = next(d["doc_id"] for d in docs_admin.list_documents("manager", "draft"))
    with pytest.raises(ValueError, match="only published"):
        freshness.confirm_current(draft, "plant.manager", "manager", "checked the draft")
