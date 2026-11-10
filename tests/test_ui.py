"""Smoke tests for every Streamlit page, run headless with Streamlit's AppTest (no browser, no network)."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.services import users

ROOT = Path(__file__).resolve().parent.parent
PAGES = {"ask": "pages/ask.py", "documents": "pages/documents.py", "review": "pages/review_queue.py",
         "analytics": "pages/analytics.py", "diagnostics": "pages/diagnostics.py", "traces": "pages/traces.py",
         "users": "pages/users.py"}
ALLOWED = {"supervisor": {"ask", "documents"},
           "manager": {"ask", "documents", "review", "analytics", "diagnostics"},
           "admin": set(PAGES)}
ACCOUNT = {"supervisor": "sup.chen", "manager": "plant.manager", "admin": "admin"}


def run(page: str, role: str | None) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app" / PAGES[page]), default_timeout=60)
    if role:
        at.session_state["auth_user"] = users.get_user(ACCOUNT[role])
    return at.run()


def texts(at: AppTest) -> str:
    return " ".join([w.value for w in at.warning] + [w.value for w in at.markdown] + [w.value for w in at.title]
                    + [w.value for w in at.caption] + [w.value for w in at.error])


@pytest.mark.parametrize("page", list(PAGES))
def test_signed_out_users_only_see_the_login_form(page):
    at = run(page, None)
    assert not at.exception
    assert any(t.label == "Sign in" for t in at.tabs) or "Sign in" in texts(at)
    assert not at.title or "Traces" not in at.title[0].value


@pytest.mark.parametrize("role", list(ALLOWED))
@pytest.mark.parametrize("page", list(PAGES))
def test_every_page_renders_for_every_role(page, role):
    at = run(page, role)
    assert not at.exception, [e.value for e in at.exception]
    if page in ALLOWED[role]:
        assert at.title, f"{page} should render a title for {role}"
    else:
        assert not at.title and any("This page is for" in w.value for w in at.warning)


def test_login_with_wrong_password_shows_error_and_stays_signed_out():
    at = AppTest.from_file(str(ROOT / "app/pages/ask.py"), default_timeout=60).run()
    at.text_input[0].set_value("admin")
    at.text_input[1].set_value("definitely-wrong-1A")
    at.button[0].click()
    at.run()
    assert any("Wrong username or password" in e.value for e in at.error)
    assert "auth_user" not in at.session_state


def test_disabled_account_is_signed_out_on_next_page_run():
    from app.services import users as u
    admin = {"username": "admin", "role": "admin"}
    u.request_access("ui.disabled", "Ui Disabled", "Strong-Pass-1x")
    u.decide_access("ui.disabled", "approved", admin, "supervisor")
    at = AppTest.from_file(str(ROOT / "app/pages/ask.py"), default_timeout=60)
    at.session_state["auth_user"] = u.get_user("ui.disabled")
    at.run()
    assert not at.exception and at.title            # signed in: sees the page
    u.set_status("ui.disabled", "disabled", admin)
    at.run()
    assert "auth_user" not in at.session_state       # next interaction signs them out


def test_traces_page_shows_detail_for_an_agent_run(monkeypatch):
    from fakes import FakeLLM
    from test_agents import COOLANT, plan
    from app.services import workflow
    fake = FakeLLM().install(monkeypatch)
    fake.plan = plan(["maintenance"], queries={"maintenance": "coolant concentration"})
    r = workflow.ask(COOLANT, "supervisor", "ui.trace")
    at = AppTest.from_file(str(ROOT / "app/pages/traces.py"), default_timeout=60)
    at.session_state["auth_user"] = users.get_user("admin")
    at.session_state["open_trace"] = r.query_id
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    body = " ".join(m.value for m in at.markdown) + " ".join(s.value for s in at.subheader)
    assert "Trace detail" in body and "Timeline" in body
    labels = " ".join(e.label for e in at.expander)
    assert "router" in labels and "search_docs" in labels and "synthesizer" in labels
