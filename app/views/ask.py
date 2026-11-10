import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))  # noqa: E702

import pandas as pd
import streamlit as st

from app.services import audit, docs_admin, workflow
from app.ui import common, theme
from app.ui.common import CAT_COLORS, CAT_LABELS, chip, esc, pill

common.init()

DEMOS = [
    "How do I clear a jam on Conveyor C3?",
    "CNC-04 spindle overheating alarm 2041",
    "What is the torque spec for M10 bolts?",
    "What PPE do I need in the paint booth?",
    "There is a chemical spill near Paint Booth 1",
    "What is the cafeteria menu today?",
]
CONF_COLORS = {"high": theme.OK, "medium": theme.WARN, "low": theme.DANGER}
CONF_LABELS = {"high": "High confidence", "medium": "Medium confidence", "low": "Low confidence"}


def run(question: str) -> None:
    question = question.strip()
    if not question:
        st.warning("Please type a question first.")
        return
    with st.spinner("Finding the right documents..."):
        try:
            resp = workflow.ask(question, common.query_role(), common.user())
        except Exception as exc:  # backend should not raise, but never show a stack trace to the floor
            st.error(f"Something went wrong while answering. Please try again. ({type(exc).__name__})")
            return
    st.session_state["resp"] = resp
    st.session_state["last_q"] = question
    hist = st.session_state["history"]
    hist.insert(0, question)
    st.session_state["history"] = list(dict.fromkeys(hist))[:5]


def render_route(resp) -> None:
    r = resp.route
    if not r:
        return
    chips = ""
    for cat in r.categories:
        label = CAT_LABELS.get(cat, cat.title())
        if cat == r.primary:
            label += f" - {round(r.confidence * 100)}% match"
        else:
            label += " - also checked"
        chips += chip(label, CAT_COLORS.get(cat, "#475569"))
    if r.safety_flag:
        chips += chip("Safety added: hazard detected", theme.AMBER_DARK)
    if r.broad_search:
        chips += chip("Searched all sources", "#64748B")
    st.markdown(chips, unsafe_allow_html=True)
    if r.reason:
        st.caption(f"Why these documents: {r.reason}")


def render_sources(resp) -> None:
    if not resp.citations:
        return
    st.subheader("Sources")
    if resp.freshness_notes:
        st.warning("**Document review overdue**\n\n" + "\n".join(f"- {n}" for n in resp.freshness_notes),
                   icon=":material/event_busy:")
    opened = st.session_state.setdefault("opened_chunks", set())
    for i, c in enumerate(resp.citations, 1):
        title = f"[{i}] {c.doc_key} v{c.version} - {c.section}"
        if c.review_state == "overdue":
            title += " · review overdue"
        with st.expander(title):
            st.markdown(chip(CAT_LABELS.get(c.category, c.category), CAT_COLORS.get(c.category, "#475569")),
                        unsafe_allow_html=True)
            st.write(c.snippet)
            key = f"open_{resp.request_id}_{c.chunk_id}"
            label = "Hide full section" if c.chunk_id in opened else "Open full section"
            if st.button(label, key=key):
                opened.symmetric_difference_update({c.chunk_id})
                st.rerun()
            if c.chunk_id in opened:
                chunk = docs_admin.get_chunk(c.chunk_id)
                if chunk:
                    with st.container(border=True):
                        st.markdown(f"**{chunk['title']}** ({chunk['doc_key']} v{chunk['version']}) - {chunk['section']}")
                        st.write(chunk["text"])
                else:
                    st.info("This section is no longer available.")


def render_feedback(resp) -> None:
    if not resp.query_id:
        return
    given = st.session_state.setdefault("feedback_given", set())
    st.divider()
    if resp.query_id in given:
        st.success("Thanks - your feedback was recorded.")
        return
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Helpful", icon=":material/thumb_up:", key=f"up_{resp.query_id}", width="stretch"):
            audit.submit_feedback({"query_id": resp.query_id, "rating": 1, "comment": "", "user": common.user()})
            given.add(resp.query_id)
            st.toast("Thanks for the feedback!", icon="✅")
            st.rerun()
    with c2:
        with st.popover("Flag as wrong", icon=":material/flag:"):
            comment = st.text_area("What was wrong?", key=f"fc_{resp.query_id}", max_chars=500)
            if st.button("Send to manager review", key=f"fs_{resp.query_id}"):
                audit.submit_feedback({"query_id": resp.query_id, "rating": -1,
                                       "comment": comment, "user": common.user()})
                given.add(resp.query_id)
                st.toast("Flag sent to the plant manager for review.", icon="🚩")
                st.rerun()


def render_trace(resp) -> None:
    if not resp.agent_trace and not resp.used_fallback:
        return
    with st.expander("How FAST answered"):
        if resp.mode == "multi_agent":
            st.markdown(pill("AI agents", theme.DEEP), unsafe_allow_html=True)
        else:
            st.markdown(pill("Rule-based", "#64748B"), unsafe_allow_html=True)
        if resp.used_fallback:
            st.caption("Answered by the backup method")
        if resp.agent_trace:
            df = pd.DataFrame([{"Step": s.seq, "Agent": s.agent, "Model": s.model or "-",
                                "Tokens": s.tokens_in + s.tokens_out, "ms": round(s.latency_ms, 1),
                                "Status": s.status} for s in resp.agent_trace])
            st.dataframe(df, hide_index=True, width="stretch")


def render_response(resp) -> None:
    if resp.emergency:
        st.markdown(f'<div class="emergency">🚨 EMERGENCY: {esc(resp.emergency_message)}</div>', unsafe_allow_html=True)

    if resp.status == "error":
        st.error(resp.abstain_reason or "Something went wrong. Please try again or ask your manager.")
    elif resp.status == "blocked":
        st.warning(resp.abstain_reason or "This question was blocked by safety rules and could not be answered.")
    elif resp.status == "abstained":
        reason = resp.abstain_reason or "I couldn't find this in the approved documents."
        st.info(reason)
        if reason.strip().endswith("?"):
            with st.container(border=True):
                st.markdown("**Can you tell me a bit more?**")
                reply = st.text_input("Your reply", key=f"clar_{resp.request_id}",
                                      placeholder="e.g. the machine name or what you see")
                if st.button("Send reply", key=f"clars_{resp.request_id}"):
                    run(f"{st.session_state.get('last_q', '')} {reply}")
                    st.rerun()

    if resp.safety_notes:
        st.warning("**Safety first**\n\n" + "\n".join(f"- {n}" for n in resp.safety_notes), icon="⚠️")

    render_route(resp)

    if resp.status == "ok":
        with st.container(border=True):
            st.markdown(resp.answer)
            if resp.steps:
                st.markdown("**Steps**")
                st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(resp.steps, 1)))
            st.markdown(pill(CONF_LABELS[resp.confidence], CONF_COLORS[resp.confidence],
                             "#000" if resp.confidence == "medium" else "#fff"), unsafe_allow_html=True)
        render_sources(resp)
        render_feedback(resp)
    elif resp.status == "abstained" and resp.query_id:
        pass

    render_trace(resp)
    st.markdown(f'<div class="small-muted">Request {esc(resp.request_id)} · {resp.latency_ms:.0f} ms · '
                f'{esc(resp.model)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------- page
common.page_header("Ask FAST", "Ask a plain-English question. You'll get an answer from approved documents, with sources.")

with st.form("ask_form", clear_on_submit=False, border=False):
    q = st.text_area("Your question", placeholder="e.g. How do I clear a jam on Conveyor C3?",
                     height=90, max_chars=500, label_visibility="collapsed")
    submitted = st.form_submit_button("Ask", type="primary", icon=":material/search:")
if submitted:
    run(q)

st.caption("Try one of these:")
cols = st.columns(3)
for i, demo in enumerate(DEMOS):
    if cols[i % 3].button(demo, key=f"demo_{i}", width="stretch"):
        run(demo)

history = st.session_state["history"]
if history:
    with st.sidebar:
        st.markdown("**Recent questions**")
        for j, h in enumerate(history):
            if st.button(h, key=f"hist_{j}", width="stretch"):
                run(h)

resp = st.session_state.get("resp")
st.divider()
if resp is None:
    st.info("Ask a question above or tap an example to get started.")
else:
    render_response(resp)
