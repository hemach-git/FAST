import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))  # noqa: E702

import pandas as pd
import streamlit as st

from app.services import audit
from app.ui import common
from app.ui.common import CAT_COLORS, CAT_LABELS, chip

common.init()
common.require_manager()
common.page_header("Review queue", "Answers that supervisors flagged as wrong. Confirm real issues or dismiss.")


def card(item: dict, resolvable: bool) -> None:
    fid = item["feedback_id"]
    with st.container(border=True):
        cat = item.get("primary_category")
        st.markdown((chip(CAT_LABELS.get(cat, cat), CAT_COLORS.get(cat, "#475569")) if cat else "")
                    + f" **{common.esc(item.get('question', ''))}**", unsafe_allow_html=True)
        st.caption(f"Equipment: {item.get('equipment_tag') or '-'} · Answer was: {item.get('answer_status')} · "
                   f"Flagged by {item.get('user_name')} on {str(item.get('created_at'))[:16]} · "
                   f"Request {item.get('request_id')}")
        st.markdown(f"> {item.get('comment') or '(no comment)'}")
        if common.is_admin() and item.get("query_id"):
            if st.button("Open trace", key=f"tr_{fid}", icon=":material/account_tree:"):
                st.session_state["open_trace"] = item["query_id"]
                st.switch_page("pages/traces.py")
        if resolvable:
            note = st.text_input("Note", key=f"rq_note_{fid}")
            a, b, _ = st.columns([1, 1, 3])
            for label, decision, kind, col in (("Confirm issue", "approved", "primary", a),
                                               ("Dismiss", "rejected", "secondary", b)):
                if col.button(label, key=f"rq_{decision}_{fid}", type=kind, width="stretch"):
                    audit.resolve_feedback(fid, {"decision": decision, "reviewer": common.user(), "note": note})
                    st.toast("Flag resolved." , icon="✅")
                    st.rerun()
        else:
            st.caption(f"Resolved: {'issue confirmed' if item['review_status'] == 'approved' else 'dismissed'}")


pend = audit.list_review_queue("pending")
done = [i for i in audit.list_review_queue("all") if i["review_status"] != "pending"]
t1, t2 = st.tabs([f"Pending ({len(pend)})", f"Resolved ({len(done)})"])
with t1:
    if not pend:
        st.success("Nothing waiting for review.")
    for item in pend:
        card(item, True)
with t2:
    if not done:
        st.info("No resolved flags yet.")
    for item in done[:50]:
        card(item, False)

st.divider()
st.subheader("Audit trail")
actions = sorted({r["action"] for r in audit.list_audit(limit=500)})
sel = st.selectbox("Filter by action", ["all"] + actions)
rows = audit.list_audit(limit=200, action=None if sel == "all" else sel)
if rows:
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch")
else:
    st.info("No audit entries for this filter.")
