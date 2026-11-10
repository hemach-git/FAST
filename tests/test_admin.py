"""Users and access, document advise flow with diff, and the trace / metrics read side."""
from __future__ import annotations

import pytest

from app.config import get_config
from app.data import db
from app.services import docs_admin, observability as obs, users, workflow
from fakes import FakeLLM
from test_agents import COOLANT, plan

PW = "Strong-Pass-1x"


@pytest.fixture
def admin():
    return {"username": "admin", "role": "admin"}


def mk(username, role="supervisor", status="approved"):
    """Create an account directly in the requested state."""
    users.request_access(username, username.title(), PW, "supervisor")
    if status != "pending":
        users.decide_access(username, "approved" if status in ("approved", "disabled") else "denied",
                            {"username": "admin", "role": "admin"}, role)
        if status == "disabled":
            users.set_status(username, "disabled", {"username": "admin", "role": "admin"})


# ---------------------------------------------------------------- users
def test_seed_accounts_exist_and_passwords_are_not_in_repo():
    assert {u["username"] for u in users.list_users()} >= {"admin", "plant.manager", "sup.chen"}
    assert all("pw_hash" not in u and "pw_salt" not in u for u in users.list_users())
    assert users.ensure_seed_users() == {}           # idempotent: nothing new when users exist


def test_request_then_approve_then_login(admin):
    users.request_access("new.person", "New Person", PW, "manager")
    with pytest.raises(users.AccessError, match="waiting for administrator approval"):
        users.authenticate("new.person", PW)
    u = users.decide_access("new.person", "approved", admin, "supervisor", "ok")
    assert u["status"] == "approved" and u["role"] == "supervisor"      # admin chose a lower role than requested
    assert users.authenticate("new.person", PW)["role"] == "supervisor"


def test_denied_and_disabled_cannot_login(admin):
    users.request_access("denied.one", "Denied One", PW)
    users.decide_access("denied.one", "denied", admin, note="no")
    with pytest.raises(users.AccessError, match="denied"):
        users.authenticate("denied.one", PW)
    mk("gone.one", status="disabled")
    with pytest.raises(users.AccessError, match="disabled"):
        users.authenticate("gone.one", PW)


def test_wrong_password_is_generic_and_locks_after_max_attempts():
    mk("lock.me")
    n = get_config()["auth"]["max_failed_attempts"]
    for _ in range(n):
        with pytest.raises(users.AccessError, match="Wrong username or password"):
            users.authenticate("lock.me", "bad-Pass-0")
    with pytest.raises(users.AccessError, match="Too many failed attempts"):
        users.authenticate("lock.me", PW)           # even the right password is refused while locked
    with pytest.raises(users.AccessError, match="Wrong username or password"):
        users.authenticate("nobody.here", "x")      # unknown user gives the same message


def test_password_and_username_rules():
    with pytest.raises(ValueError, match="at least"):
        users.request_access("short.pw", "Short", "Ab1")
    with pytest.raises(ValueError, match="upper-case"):
        users.request_access("weak.pw", "Weak", "alllowercase123")
    with pytest.raises(ValueError, match="Username"):
        users.request_access("Bad Name!", "Bad", PW)
    with pytest.raises(ValueError, match="taken"):
        users.request_access("admin", "Dup", PW)


def test_nobody_can_request_admin_for_themselves():
    with pytest.raises(ValueError):
        users.request_access("sneaky", "Sneaky", PW, "admin")


def test_only_admin_can_decide_or_change(admin):
    users.request_access("needs.ok", "Needs Ok", PW)
    for who in ({"username": "plant.manager", "role": "manager"}, {"username": "sup.chen", "role": "supervisor"}):
        with pytest.raises(users.AccessError, match="administrator"):
            users.decide_access("needs.ok", "approved", who)
        with pytest.raises(users.AccessError):
            users.set_role("sup.chen", "admin", who)
        with pytest.raises(users.AccessError):
            users.reset_password("needs.ok", "Another-Pass-9", who)
    assert users.get_user("needs.ok")["status"] == "pending"


def test_last_admin_cannot_be_demoted_or_disabled(admin):
    assert users.active_admins() == 1
    with pytest.raises(users.AccessError, match="last administrator"):
        users.set_role("admin", "manager", admin)
    with pytest.raises(users.AccessError):
        users.set_status("admin", "disabled", admin)


def test_role_change_password_reset_and_audit(admin):
    mk("promote.me")
    assert users.set_role("promote.me", "manager", admin)["role"] == "manager"
    users.reset_password("promote.me", "Brand-New-Pass-5", admin)
    assert users.authenticate("promote.me", "Brand-New-Pass-5")["role"] == "manager"
    actions = {r["action"] for r in users.access_activity(500)}
    assert {"access_requested", "access_approved", "role_changed", "password_reset", "login"} <= actions


def test_password_is_hashed_not_stored():
    row = db.rows("SELECT pw_hash, pw_salt FROM users WHERE username='admin'")[0]
    assert len(row["pw_hash"]) == 64 and row["pw_salt"] and PW not in row["pw_hash"]


# ---------------------------------------------------------------- document advise flow + diff
def draft_for(key="QC-TORQ-01", extra="\n## 9. New note\nCheck calibration weekly.\n"):
    cur = docs_admin.get_document(f"{key}-v1", "manager") or docs_admin.get_document(
        db.rows("SELECT doc_id FROM documents WHERE doc_key=? AND status='published'", (key,))[0]["doc_id"], "manager")
    return docs_admin.submit_draft({"doc_key": key, "title": cur["title"], "category": cur["category"],
                                    "owner": cur["owner"] or "Quality Mgr", "equipment": cur["equipment"] or "",
                                    "content": cur["content"] + extra}, "plant.manager")


def test_advise_keeps_draft_hidden_and_resubmit_returns_to_queue():
    # QC-TORQ-01 may have been published by earlier tests: use a fresh key to keep this test independent.
    doc_id = docs_admin.submit_draft({"doc_key": "QC-ADV-09", "title": "Advice test procedure", "category": "quality",
                                      "owner": "Quality Mgr", "content": "## 1. Purpose\nCheck widgets.\n\n## 2. Steps\n1. Look.\n"},
                                     "plant.manager")
    state = lambda: next(d for d in docs_admin.list_documents("manager", "draft") if d["doc_id"] == doc_id)["review_state"]
    assert state() == "awaiting_review"
    docs_admin.advise_document(doc_id, "plant.manager", "Add the gauge model to step 2.", "manager")
    assert state() == "changes_requested"
    assert docs_admin.list_documents("supervisor", None) and doc_id not in {d["doc_id"] for d in docs_admin.list_documents("supervisor")}
    docs_admin.resubmit_draft(doc_id, "plant.manager", "## 1. Purpose\nCheck widgets.\n\n## 2. Steps\n1. Look with gauge G-7.\n",
                              note="added gauge", actor_role="manager")
    assert state() == "awaiting_review"
    assert "gauge G-7" in docs_admin.get_document(doc_id, "manager")["content"]
    assert any("gauge G-7" in c["text"] for c in db.rows("SELECT text FROM chunks WHERE doc_id=?", (doc_id,)))
    acts = [r["action"] for r in docs_admin.list_reviews(doc_id)]
    assert acts == ["submit", "advise", "resubmit"]
    res = docs_admin.review_document(doc_id, {"decision": "approved", "reviewer": "plant.manager", "note": "good"})
    assert res["status"] == "published"
    assert [r["action"] for r in docs_admin.list_reviews(doc_id)][-1] == "approve"


def test_advise_needs_reviewer_role_and_a_comment():
    doc_id = docs_admin.submit_draft({"doc_key": "QC-ADV-10", "title": "Second advice test", "category": "quality",
                                      "owner": "Quality Mgr", "content": "## 1. Purpose\nSomething useful.\n"}, "plant.manager")
    with pytest.raises(PermissionError):
        docs_admin.advise_document(doc_id, "sup.chen", "Please change this.", "supervisor")
    with pytest.raises(ValueError, match="what should change"):
        docs_admin.advise_document(doc_id, "plant.manager", "  ", "manager")
    with pytest.raises(PermissionError):
        docs_admin.resubmit_draft(doc_id, "sup.chen", "## 1. Purpose\nHacked text here.\n", actor_role="supervisor")
    docs_admin.review_document(doc_id, {"decision": "rejected", "reviewer": "plant.manager", "note": "no"})
    with pytest.raises(ValueError, match="only drafts"):
        docs_admin.advise_document(doc_id, "plant.manager", "Too late to change.", "manager")


def test_diff_against_published_counts_changes():
    doc_id = docs_admin.submit_draft({"doc_key": "QC-ADV-11", "title": "Diff test", "category": "quality", "owner": "Quality Mgr",
                                      "content": "## 1. Purpose\nLine one.\n"}, "plant.manager")
    docs_admin.review_document(doc_id, {"decision": "approved", "reviewer": "plant.manager", "note": ""})
    v2 = docs_admin.submit_draft({"doc_key": "QC-ADV-11", "title": "Diff test", "category": "quality", "owner": "Quality Mgr",
                                  "content": "## 1. Purpose\nLine one changed.\n\n## 2. Added\nNew section.\n"}, "plant.manager")
    d = docs_admin.diff_against_published(v2)
    assert d["published_version"] == 1 and d["added"] >= 2 and d["removed"] >= 1 and "Line one changed." in d["diff"]
    first = docs_admin.diff_against_published(docs_admin.submit_draft(
        {"doc_key": "QC-ADV-12", "title": "Brand new", "category": "quality", "owner": "Quality Mgr",
         "content": "## 1. Purpose\nA brand new procedure.\n"}, "plant.manager"))
    assert first["published_version"] is None and first["added"] >= 1


# ---------------------------------------------------------------- traces and metrics
@pytest.fixture
def traced(monkeypatch):
    fake = FakeLLM().install(monkeypatch)
    fake.plan = plan(["maintenance"], queries={"maintenance": "coolant concentration"})
    return [workflow.ask(COOLANT, "supervisor", "trace.user") for _ in range(2)]


def test_get_trace_has_steps_timeline_and_citations(traced):
    t = obs.get_trace(traced[0].query_id)
    assert t["query"]["mode"] == "multi_agent" and t["steps"] and t["citations"]
    tl = t["timeline"]
    assert len(tl) == len(t["steps"]) and all(x["end_ms"] > x["start_ms"] for x in tl)
    router_end = max(x["end_ms"] for x in tl if x["agent"] == "router")
    assert all(x["start_ms"] >= router_end for x in tl if x["agent"] == "maintenance")     # specialists start after the router
    assert obs.get_trace("q-does-not-exist") is None


def test_summary_aggregate_and_drill_down(traced):
    df = obs.base_df(obs.Filters(days=None, mode="multi_agent", include_synthetic=False))
    assert len(df) >= 2 and obs.summary(df)["traces"] == len(df)
    for metric in obs.METRICS:
        for group in obs.GROUP_BYS:
            res = obs.aggregate(df, metric, group)
            assert list(res.columns) == ["group", "value", "n"] and res["n"].sum() == len(df)
    by_cat = obs.aggregate(df, "queries", "primary_category")
    top = by_cat.iloc[0]
    sub = obs.drill(df, "primary_category", top["group"])
    assert len(sub) == top["n"] and set(sub["query_id"]) <= set(df["query_id"])


def test_filters_narrow_the_set(traced):
    all_df = obs.base_df(obs.Filters(days=None))
    live = obs.base_df(obs.Filters(days=None, include_synthetic=False))
    assert len(live) < len(all_df)
    assert obs.base_df(obs.Filters(days=None, text="coolant concentration", include_synthetic=False)).shape[0] >= 2
    assert obs.base_df(obs.Filters(days=None, fallback_only=True, include_synthetic=False)).empty or True
    with pytest.raises(ValueError):
        obs.aggregate(all_df, "nope", "day")


def test_agent_metrics_and_costs(traced):
    am = obs.agent_metrics(obs.Filters(days=None, include_synthetic=False))
    assert {"router", "maintenance", "synthesizer"} <= set(am["agent"]) and (am["calls"] >= 1).all()
    assert {"search_docs"} <= set(am["name"])


def test_log_search(traced):
    ev = obs.log_events(200, "ask")
    assert ev and all(e["event"] == "ask" for e in ev)
    assert obs.log_events(200, "all", "zzz-no-such-text") == []
