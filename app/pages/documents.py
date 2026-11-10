import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))  # noqa: E702

import difflib
import json

import streamlit as st

from app.services import docs_admin
from app.ui import common
from app.ui.common import CAT_COLORS, CAT_LABELS, chip, esc, pill

common.init()
mgr = common.is_manager()
STATUS_COLORS = {"published": "#2E7D32", "draft": "#F9A825", "rejected": "#C62828", "retired": "#64748B"}
CATS = ["safety", "maintenance", "quality"]

EXAMPLE = """## 1. Purpose
Describe what this procedure covers and who it applies to.

## 2. Procedure
1. First step.
2. Second step.

## 3. Safety notes
List any hazards and required PPE.
"""


def viewer(doc_id: str) -> None:
    d = docs_admin.get_document(doc_id, common.role())
    if not d:
        st.info("Document not available.")
        return
    st.markdown(chip(CAT_LABELS.get(d["category"], d["category"]), CAT_COLORS.get(d["category"], "#475569"))
                + pill(d["status"], STATUS_COLORS.get(d["status"], "#475569")), unsafe_allow_html=True)
    st.markdown(f"### {d['title']}")
    st.caption(f"{d['doc_key']} v{d['version']} · Owner: {d.get('owner') or '-'} · Equipment: {d.get('equipment') or '-'}")
    for s in d.get("sections", []):
        with st.expander(s["section"], expanded=False):
            st.markdown(s["text"])


def browse() -> None:
    c1, c2, c3 = st.columns(3)
    status = None
    if mgr:
        status = c1.selectbox("Status", ["all", "published", "draft", "rejected", "retired"])
        status = None if status == "all" else status
    cat = c2.selectbox("Category", ["all"] + CATS, format_func=lambda x: CAT_LABELS.get(x, "All"))
    docs = docs_admin.list_documents(role=common.role(), status=status, category=None if cat == "all" else cat)
    if not docs:
        st.info("No documents match these filters.")
        return
    labels = {d["doc_id"]: f"{d['doc_key']} v{d['version']} - {d['title']}" + (f" [{d['status']}]" if mgr else "")
              for d in docs}
    sel = c3.selectbox("Document", list(labels), format_func=labels.get)
    with st.container(border=True):
        viewer(sel)


def new_doc_form() -> None:
    with st.form("new_doc"):
        a, b = st.columns(2)
        key = a.text_input("Document key", placeholder="e.g. MM-CONV-02 (existing key = new version)")
        title = b.text_input("Title")
        c, d, e = st.columns(3)
        cat = c.selectbox("Category", CATS, format_func=CAT_LABELS.get)
        owner = d.text_input("Owner", value="Plant Manager")
        equip = e.text_input("Equipment (optional)")
        content = st.text_area("Content (Markdown, sections start with '## ')", value=EXAMPLE, height=260)
        go = st.form_submit_button("Submit draft for approval", type="primary")
    if go:
        try:
            doc_id = docs_admin.submit_draft({"doc_key": key.strip().upper(), "title": title.strip(), "category": cat,
                                              "owner": owner.strip(), "equipment": equip.strip(),
                                              "content": content}, common.user())
            st.success(f"Draft submitted ({doc_id}). It needs approval below before supervisors can see it.")
        except ValueError as exc:
            st.error(f"Could not submit: {exc}")


STATE_LABELS = {"awaiting_review": ("Awaiting review", "#F9A825"), "changes_requested": ("Changes requested", "#EF6C00")}


def history(doc_id: str) -> None:
    rows = docs_admin.list_reviews(doc_id)
    if not rows:
        st.caption("No review activity yet.")
    icons = {"submit": "📝", "advise": "💬", "resubmit": "🔁", "approve": "✅", "reject": "⛔", "comment": "🗨️"}
    for r in rows:
        st.markdown(f"{icons.get(r['action'], '•')} **{esc(r['actor'])}** · {r['action']} · "
                    f"<span class='small-muted'>{str(r['ts'])[:16]}</span>"
                    + (f"  \n> {esc(r['comment'])}" if r["comment"] else ""), unsafe_allow_html=True)


def pending() -> None:
    drafts = docs_admin.list_documents(role=common.role(), status="draft")
    if not drafts:
        st.info("No drafts waiting for approval.")
        return
    for dr in drafts:
        label, color = STATE_LABELS.get(dr.get("review_state"), STATE_LABELS["awaiting_review"])
        with st.container(border=True):
            st.markdown(f"**{dr['doc_key']} v{dr['version']}** - {esc(dr['title'])} " + pill(label, color), unsafe_allow_html=True)
            st.caption(f"Submitted by {dr.get('created_by') or '-'} on {str(dr.get('created_at'))[:16]}")
            try:
                flags = json.loads(dr.get("guardrail_flags") or "[]")
            except (TypeError, ValueError):
                flags = []
            if flags:
                st.warning("Automatic checks flagged: " + ", ".join(map(str, flags)))
            d = docs_admin.diff_against_published(dr["doc_id"])
            cur_label = f" (v{d['published_version']})" if d["published_version"] else " - none (new document)"
            st.markdown(f"**Changes:** +{d['added']} lines, -{d['removed']} lines against the published version{cur_label}")
            tab_d, tab_s, tab_h = st.tabs(["Changes only (diff)", "Side by side", "Review history"])
            with tab_d:
                st.code(d["diff"] or "No text differences.", language="diff")
            with tab_s:
                left, right = st.columns(2)
                left.markdown(f"**Current published**{cur_label}")
                left.code(d["old"] or "(no published version)", language="markdown", wrap_lines=True)
                right.markdown(f"**Proposed draft (v{dr['version']})**")
                right.code(d["new"], language="markdown", wrap_lines=True)
            with tab_h:
                history(dr["doc_id"])
            note = st.text_area("Your comment (required for 'Advise', shown to the author)", key=f"note_{dr['doc_id']}", height=70)
            a, v, r, _ = st.columns([1.2, 1.2, 1, 2])
            who, role = common.user(), common.role()
            if a.button("Approve & publish", key=f"approved_{dr['doc_id']}", type="primary", width="stretch"):
                try:
                    res = docs_admin.review_document(dr["doc_id"], {"decision": "approved", "reviewer": who, "note": note})
                    st.session_state["flash"] = f"{dr['doc_key']} v{dr['version']} {res['status']}."
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if v.button("Advise changes", key=f"advise_{dr['doc_id']}", width="stretch",
                        help="Send the draft back with your advice. Supervisors keep seeing the current published version."):
                try:
                    docs_admin.advise_document(dr["doc_id"], who, note, role)
                    st.session_state["flash"] = f"Advice sent on {dr['doc_key']} v{dr['version']}. It stays a draft until updated."
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
            if r.button("Reject", key=f"rejected_{dr['doc_id']}", width="stretch"):
                try:
                    res = docs_admin.review_document(dr["doc_id"], {"decision": "rejected", "reviewer": who, "note": note})
                    st.session_state["flash"] = f"{dr['doc_key']} v{dr['version']} {res['status']}."
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if dr.get("review_state") == "changes_requested":
                with st.expander("Edit the draft and resubmit", icon=":material/edit:"):
                    with st.form(f"resub_{dr['doc_id']}"):
                        new_title = st.text_input("Title", value=dr["title"])
                        new_content = st.text_area("Content (Markdown)", value=d["new"], height=240)
                        why = st.text_input("What did you change?")
                        if st.form_submit_button("Resubmit for review", type="primary"):
                            try:
                                docs_admin.resubmit_draft(dr["doc_id"], who, new_content, new_title, why, role)
                                st.session_state["flash"] = f"{dr['doc_key']} v{dr['version']} resubmitted for review."
                                st.rerun()
                            except (ValueError, PermissionError) as exc:
                                st.error(str(exc))


common.page_header("Documents", "Approved procedures, manuals and quality standards." if not mgr
                   else "Browse documents, submit updates and approve drafts.")
if st.session_state.get("flash"):
    st.success(st.session_state.pop("flash"))

if mgr:
    tab1, tab2, tab3 = st.tabs(["Browse", "Pending approvals", "New / update document"])
    with tab1:
        browse()
    with tab2:
        pending()
    with tab3:
        new_doc_form()
else:
    browse()
