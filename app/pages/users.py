import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))  # noqa: E702

import pandas as pd
import streamlit as st

from app.services import users
from app.ui import common
from app.ui.common import ROLE_LABELS, esc, pill

common.init()
common.require_admin()
me = common.current_user() or {"username": common.user(), "role": "admin"}
common.page_header("Users & access", "Approve or deny access requests, manage roles and accounts. Every decision is audited.")

STATUS_COLORS = {"approved": "#2E7D32", "pending": "#F9A825", "denied": "#C62828", "disabled": "#64748B"}
if not common.auth_enabled():
    st.warning("Sign-in is switched off (development mode), so these roles are not enforced. Turn it on in config.yaml.")

all_users = users.list_users()
pending = [u for u in all_users if u["status"] == "pending"]
m = st.columns(4)
m[0].metric("Waiting for approval", len(pending))
m[1].metric("Active users", sum(1 for u in all_users if u["status"] == "approved"))
m[2].metric("Administrators", users.active_admins())
m[3].metric("Disabled / denied", sum(1 for u in all_users if u["status"] in ("disabled", "denied")))

t1, t2, t3 = st.tabs([f"Access requests ({len(pending)})", "All users", "Activity"])

with t1:
    if not pending:
        st.success("No access requests are waiting.")
    for u in pending:
        with st.container(border=True):
            st.markdown(f"**{esc(u['display_name'])}** · `{u['username']}` " + pill("pending", STATUS_COLORS["pending"]),
                        unsafe_allow_html=True)
            st.caption(f"Requested {ROLE_LABELS[u['requested_role']]} access on {u['created_at'][:16]}"
                       + (f" · {esc(u['email'])}" if u["email"] else ""))
            c1, c2 = st.columns([1, 2])
            role = c1.selectbox("Grant role", list(users.ROLES), index=list(users.ROLES).index(u["requested_role"]),
                                format_func=ROLE_LABELS.get, key=f"grant_{u['username']}")
            note = c2.text_input("Note (shown in the audit trail)", key=f"unote_{u['username']}")
            a, b, _ = st.columns([1, 1, 3])
            if a.button("Approve", key=f"ap_{u['username']}", type="primary", width="stretch"):
                users.decide_access(u["username"], "approved", me, role, note)
                st.toast(f"{u['username']} approved as {ROLE_LABELS[role]}", icon="✅")
                st.rerun()
            if b.button("Deny", key=f"dn_{u['username']}", width="stretch"):
                users.decide_access(u["username"], "denied", me, "supervisor", note)
                st.toast(f"{u['username']} denied", icon="🚫")
                st.rerun()

with t2:
    if not all_users:
        st.info("No users yet.")
    else:
        show = pd.DataFrame(all_users)[["username", "display_name", "role", "status", "last_login", "decided_by",
                                        "decided_at", "created_at"]]
        st.dataframe(show, hide_index=True, width="stretch")
        st.markdown("**Change an account**")
        managed = [u for u in all_users if u["status"] in ("approved", "disabled")]
        if managed:
            names = [u["username"] for u in managed]
            sel = st.selectbox("User", names)
            cur = next(u for u in managed if u["username"] == sel)
            st.markdown(f"{esc(cur['display_name'])} " + pill(cur["status"], STATUS_COLORS[cur["status"]])
                        + f" · {ROLE_LABELS[cur['role']]}", unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            with c1:
                new_role = st.selectbox("Role", list(users.ROLES), index=list(users.ROLES).index(cur["role"]),
                                        format_func=ROLE_LABELS.get, key="chg_role")
                if st.button("Save role", disabled=new_role == cur["role"], width="stretch"):
                    try:
                        users.set_role(sel, new_role, me)
                        st.toast("Role updated", icon="✅")
                        st.rerun()
                    except (users.AccessError, ValueError) as exc:
                        st.error(str(exc))
            with c2:
                st.write("")
                st.write("")
                if cur["status"] == "approved":
                    if st.button("Disable account", width="stretch"):
                        try:
                            users.set_status(sel, "disabled", me)
                            st.rerun()
                        except (users.AccessError, ValueError) as exc:
                            st.error(str(exc))
                elif st.button("Re-enable account", type="primary", width="stretch"):
                    users.set_status(sel, "approved", me)
                    st.rerun()
            with c3:
                with st.form("pw_reset", clear_on_submit=True):
                    npw = st.text_input("New password", type="password", help="Share it with the user in person")
                    if st.form_submit_button("Reset password", width="stretch"):
                        try:
                            users.reset_password(sel, npw, me)
                            st.success("Password reset. The account is unlocked.")
                        except (users.AccessError, ValueError) as exc:
                            st.error(str(exc))

with t3:
    act = users.access_activity(200)
    if act:
        st.dataframe(pd.DataFrame(act), hide_index=True, width="stretch")
    else:
        st.info("No access activity yet.")
