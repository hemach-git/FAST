"""Shared UI helpers: session state, sidebar, theme CSS, badges."""
from __future__ import annotations

import html

import streamlit as st

from app.config import db_path, get_config, model_label
from app.services import users, workflow

ROLES = {"Floor Supervisor": "supervisor", "Plant Manager": "manager", "Administrator": "admin"}
ROLE_LABELS = {v: k for k, v in ROLES.items()}
CAT_COLORS = {"safety": "#C62828", "maintenance": "#1565C0", "quality": "#2E7D32"}
CAT_LABELS = {"safety": "Safety", "maintenance": "Maintenance", "quality": "Quality"}

CSS = """
<style>
.block-container {padding-top: 2.2rem; max-width: 1200px;}
.chip {display:inline-block; padding:4px 12px; border-radius:16px; color:#fff; font-weight:600;
       font-size:0.85rem; margin:0 6px 6px 0;}
.chip-muted {background:#64748B;}
.pill {display:inline-block; padding:2px 10px; border-radius:10px; font-size:0.8rem; font-weight:600;}
.synthetic-badge {background:#E8590C; color:#fff; padding:4px 10px; border-radius:6px;
       font-size:0.75rem; font-weight:700; letter-spacing:.04em; text-align:center; margin:4px 0;}
.emergency {background:#B71C1C; color:#fff; padding:16px 20px; border-radius:8px; font-size:1.15rem;
       font-weight:700; margin-bottom:12px; border-left:8px solid #FFEB3B;}
.small-muted {color:#64748B; font-size:0.8rem;}
[data-testid="stMetric"] {background:#fff; border:1px solid #D5DBE3; border-radius:8px; padding:10px 14px;}
</style>
"""


def esc(text: object) -> str:
    return html.escape(str(text))


def chip(label: str, color: str) -> str:
    return f'<span class="chip" style="background:{color}">{esc(label)}</span>'


def pill(label: str, bg: str, fg: str = "#fff") -> str:
    return f'<span class="pill" style="background:{bg};color:{fg}">{esc(label)}</span>'


def auth_enabled() -> bool:
    return bool(get_config()["auth"]["enabled"])


def current_user() -> dict | None:
    return st.session_state.get("auth_user") if auth_enabled() else None


def role() -> str:
    """supervisor | manager | admin. Comes from the signed-in account (never from a widget) when auth is on."""
    u = current_user()
    if u:
        return u["role"]
    return ROLES[st.session_state.get("role_label", "Floor Supervisor")]


def query_role() -> str:
    """Role stored with a question: administrators act as managers there."""
    return "supervisor" if role() == "supervisor" else "manager"


def user() -> str:
    u = current_user()
    if u:
        return u["username"]
    return (st.session_state.get("user_name") or "").strip() or "demo.user"


def is_manager() -> bool:
    return role() in ("manager", "admin")


def is_admin() -> bool:
    return role() == "admin"


@st.cache_resource(show_spinner="Starting FAST...")
def _boot() -> bool:
    workflow.ensure_ready()
    return True


def init() -> None:
    """Call at the top of every page: boot backend once, set defaults, inject CSS."""
    _boot()
    st.session_state.setdefault("role_label", "Floor Supervisor")
    st.session_state.setdefault("user_name", "demo.user")
    st.session_state.setdefault("history", [])
    st.markdown(CSS, unsafe_allow_html=True)
    if auth_enabled():
        # Re-check the account on every page run: a disabled user or a role change takes effect immediately.
        u = st.session_state.get("auth_user")
        if u:
            fresh = users.get_user(u["username"])
            if not fresh or fresh["status"] != "approved":
                st.session_state.pop("auth_user", None)
                u = None
            else:
                st.session_state["auth_user"] = fresh
        if not u:
            login_gate()
            st.stop()


def login_gate() -> None:
    st.markdown("<style>[data-testid=\"stSidebar\"], [data-testid=\"stSidebarCollapsedControl\"] {display: none;}</style>",
                unsafe_allow_html=True)       # signed out: no page list in the sidebar
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown("## :orange[FAST]")
        st.caption("Floor Assistant for Supervisors and Technicians - prototype, synthetic data")
        t_in, t_req = st.tabs(["Sign in", "Request access"])
        with t_in:
            with st.form("login_form"):
                name = st.text_input("Username")
                pw = st.text_input("Password", type="password")
                go = st.form_submit_button("Sign in", type="primary", width="stretch")
            if go:
                try:
                    st.session_state["auth_user"] = users.authenticate(name, pw)
                    st.rerun()
                except users.AccessError as exc:
                    st.error(str(exc))
            st.caption(f"First run on this computer? The demo account passwords are in `{db_path().parent / 'initial_credentials.txt'}`.")
        with t_req:
            st.caption("An administrator must approve your request before you can sign in.")
            with st.form("request_form"):
                a, b = st.columns(2)
                uname = a.text_input("Choose a username", help="3-31 characters: a-z, 0-9, dot, dash, underscore")
                dname = b.text_input("Your name")
                email = st.text_input("Work email (optional)")
                want = st.selectbox("Access you need", ["supervisor", "manager"], format_func=lambda r: ROLE_LABELS[r])
                p1 = st.text_input("Password", type="password", help="At least 10 characters with a capital letter and a digit")
                p2 = st.text_input("Repeat password", type="password")
                send = st.form_submit_button("Request access", type="primary", width="stretch")
            if send:
                if p1 != p2:
                    st.error("The passwords do not match.")
                else:
                    try:
                        users.request_access(uname, dname, p1, want, email)
                        st.success("Request sent. You can sign in once an administrator approves it.")
                    except ValueError as exc:
                        st.error(str(exc))


def sidebar_identity() -> None:
    with st.sidebar:
        st.markdown("## :orange[FAST]")
        st.caption("Floor Assistant for Supervisors and Technicians")
        u = current_user()
        if u:
            st.markdown(f"**{esc(u['display_name'])}**")
            st.caption(f"{ROLE_LABELS[u['role']]} · `{u['username']}`")
            if st.button("Sign out", width="stretch"):
                st.session_state.pop("auth_user", None)
                st.session_state["history"] = []
                st.rerun()
        else:
            st.selectbox("Role", list(ROLES), key="role_label")
            st.text_input("Your name", key="user_name")
            st.caption(":red[Sign-in is off (development mode).]")
        st.markdown('<div class="synthetic-badge">SYNTHETIC DATA - demo only</div>', unsafe_allow_html=True)
        st.caption(f"Model: `{model_label()}`")


def require_manager() -> None:
    if not is_manager():
        st.warning("This page is for Plant Managers and Administrators.")
        st.stop()


def require_admin() -> None:
    if not is_admin():
        st.warning("This page is for Administrators.")
        st.stop()


def page_header(title: str, subtitle: str = "") -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)
