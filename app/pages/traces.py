import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))  # noqa: E702

import json

import altair as alt
import pandas as pd
import streamlit as st

from app.services import observability as obs
from app.ui import common
from app.ui.common import esc, pill

common.init()
common.require_admin()
common.page_header("Traces & logs", "Follow any question step by step: what each agent saw, did and cost. "
                   "Aggregate the metrics and drill down to the individual traces.")

STATUS_COLORS = {"ok": "#2E7D32", "abstained": "#F9A825", "blocked": "#6D4C41", "error": "#C62828",
                 "timeout": "#C62828", "skipped": "#64748B"}
AGENT_COLORS = {"router": "#7E57C2", "safety": "#C62828", "maintenance": "#1565C0", "quality": "#2E7D32",
                "synthesizer": "#EF6C00", "orchestrator": "#64748B"}

# ------------------------------------------------------------------ filters (shared by every tab)
with st.container(border=True):
    c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 2])
    days = c1.selectbox("Period", [1, 7, 30, 90, None], index=2, format_func=lambda d: "All time" if d is None else f"Last {d} d")
    status = c2.selectbox("Status", ["all", "ok", "abstained", "blocked", "error"])
    mode = c3.selectbox("Engine", ["all", "multi_agent", "pipeline"])
    cat = c4.selectbox("Category", ["all", "safety", "maintenance", "quality"])
    text = c5.text_input("Search question / request id", placeholder="e.g. conveyor jam or req-1a2b...")
    d1, d2 = st.columns(2)
    fb_only = d1.toggle("Only runs that fell back to rule-based", value=False)
    synth = d2.toggle("Include seeded demo history", value=True,
                      help="Seeded rows are synthetic and have no agent steps. Turn off to see live questions only.")
flt = obs.Filters(days=days, status=status, mode=mode, category=cat, fallback_only=fb_only, text=text, include_synthetic=synth)
df = obs.base_df(flt)
s = obs.summary(df)

m = st.columns(4)
m[0].metric("Traces", s["traces"])
m[1].metric("Error / abstain", f"{s['error_rate']}%")
m[2].metric("Fallback to rule-based", f"{s['fallback_rate']}%")
m[3].metric("Est. cost", f"${s['cost']:.4f}")
m2 = st.columns(4)
m2[0].metric("Latency p50", f"{s['p50_ms'] / 1000:.1f} s")
m2[1].metric("Latency p95", f"{s['p95_ms'] / 1000:.1f} s")
m2[2].metric("Tokens", f"{s['tokens']:,}")


def open_trace(qid: str | None) -> None:
    st.session_state["open_trace"] = qid


# ------------------------------------------------------------------ trace detail
def render_detail(qid: str) -> None:
    t = obs.get_trace(qid)
    if not t:
        st.warning("Trace not found.")
        return
    q = t["query"]
    with st.container(border=True):
        top, btn = st.columns([6, 1])
        top.subheader("Trace detail")
        btn.button("Close", on_click=open_trace, args=(None,), width="stretch")
        st.markdown(
            pill(q["status"], STATUS_COLORS.get(q["status"], "#475569")) + " "
            + pill(q["mode"], "#455A64") + " "
            + (pill("fell back to rule-based", "#C62828") if q["used_fallback"] else "") + " "
            + (pill("emergency", "#B71C1C") if q["emergency"] else "") + " "
            + (pill("synthetic", "#E8590C") if q["is_synthetic"] else ""), unsafe_allow_html=True)
        st.markdown(f"**Question:** {esc(q['question_masked'])}")
        st.caption(f"{q['query_id']} · {q['request_id']} · {q['created_at'][:19]} · {q['user_name']} ({q['role']}) · "
                   f"model {q['model']} · routed to {', '.join(q['routed_categories']) or '-'}")
        k = st.columns(5)
        k[0].metric("Latency", f"{q['latency_ms'] / 1000:.2f} s")
        k[1].metric("Tokens in / out", f"{q['tokens_in']} / {q['tokens_out']}")
        k[2].metric("Cost", f"${q['cost_usd']:.5f}")
        k[3].metric("Steps", len(t["steps"]))
        k[4].metric("Citations", len(t["citations"]))

        if not t["steps"]:
            st.info("No agent steps stored for this question. It was answered by the rule-based pipeline "
                    "(or it is seeded demo history).")
        else:
            tl = pd.DataFrame(t["timeline"])
            st.markdown("**Timeline** (start times are reconstructed from step durations; specialists run in parallel)")
            chart = alt.Chart(tl).mark_bar(cornerRadius=3, height=16).encode(
                x=alt.X("start_ms:Q", title="milliseconds"), x2="end_ms:Q",
                y=alt.Y("label:N", sort=None, title=None),
                color=alt.Color("agent:N", scale=alt.Scale(domain=list(AGENT_COLORS), range=list(AGENT_COLORS.values())),
                                legend=alt.Legend(title="Agent")),
                opacity=alt.condition(alt.datum.status == "ok", alt.value(1.0), alt.value(0.45)),
                tooltip=["label", "kind", "status", "start_ms", "end_ms"],
            ).properties(height=max(120, 26 * len(tl)))
            st.altair_chart(chart, width="stretch")

            st.markdown("**Steps**")
            for stp in t["steps"]:
                icon = {"llm": ":material/psychology:", "tool": ":material/build:", "guard": ":material/shield:"}[stp["kind"]]
                head = (f"{stp['seq']}. {stp['agent']} · {stp['name'] or stp['kind']}  —  {stp['status']}"
                        f"  ·  {stp['latency_ms']:.0f} ms" + (f"  ·  {stp['tokens_in']}+{stp['tokens_out']} tok" if stp["tokens_in"] or stp["tokens_out"] else ""))
                with st.expander(head, icon=icon, expanded=stp["status"] != "ok"):
                    st.caption(f"{stp['kind']} · model {stp['model'] or '-'} · cost ${stp['cost_usd']:.5f}")
                    if stp["summary"]:
                        st.write(stp["summary"])
                    a, b = st.columns(2)
                    a.markdown("**Input**")
                    a.code(stp["input_text"] or "(not recorded)", language=None, wrap_lines=True)
                    b.markdown("**Output**")
                    b.code(stp["output_text"] or "(not recorded)", language=None, wrap_lines=True)

        tabs = st.tabs(["Citations", "Feedback", "Audit", "Log lines"])
        with tabs[0]:
            if t["citations"]:
                st.dataframe(pd.DataFrame(t["citations"]), hide_index=True, width="stretch")
            else:
                st.caption("No citations (the question was blocked, abstained or errored).")
        with tabs[1]:
            st.dataframe(pd.DataFrame(t["feedback"]), hide_index=True, width="stretch") if t["feedback"] else st.caption("No feedback.")
        with tabs[2]:
            st.dataframe(pd.DataFrame(t["audit"]), hide_index=True, width="stretch") if t["audit"] else st.caption("No audit rows.")
        with tabs[3]:
            if t["logs"]:
                for e in t["logs"]:
                    st.code(json.dumps(e, indent=2, default=str), language="json")
            else:
                st.caption("No log lines in the recent log window (seeded history is not logged).")
        if q["error"]:
            st.error(q["error"])


# ------------------------------------------------------------------ tabs
tab_list, tab_metrics, tab_agents, tab_logs = st.tabs(["Traces", "Metrics & drill-down", "Agents & cost", "Logs"])


def selectable_table(frame: pd.DataFrame, key: str) -> None:
    """Table whose row click opens the trace detail."""
    show = frame[["created_at", "question_masked", "primary_category", "status", "mode", "used_fallback", "latency_ms",
                  "tokens", "cost_usd", "steps", "step_errors", "rating", "query_id"]].copy()
    show["created_at"] = show["created_at"].str[:19].str.replace("T", " ")
    show["used_fallback"] = show["used_fallback"].astype(bool)
    ev = st.dataframe(show, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=key,
                      column_config={"question_masked": st.column_config.TextColumn("question", width="large"),
                                     "latency_ms": st.column_config.NumberColumn("latency ms", format="%.0f"),
                                     "cost_usd": st.column_config.NumberColumn("cost $", format="%.5f"),
                                     "used_fallback": st.column_config.CheckboxColumn("fallback")})
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        qid = show.iloc[rows[0]]["query_id"]
        if st.session_state.get("open_trace") != qid:
            st.session_state["open_trace"] = qid
            st.rerun()


with tab_list:
    if df.empty:
        st.info("No traces match these filters. Ask a question first, or widen the period.")
    else:
        st.caption(f"{len(df)} traces. Click a row to open its trace.")
        selectable_table(df.head(300), "tbl_traces")
        st.download_button("Export CSV", df.to_csv(index=False).encode("utf-8"), "fast_traces.csv", "text/csv")

with tab_metrics:
    if df.empty:
        st.info("Nothing to aggregate for these filters.")
    else:
        a, b = st.columns(2)
        metric = a.selectbox("Metric", list(obs.METRICS), format_func=obs.METRICS.get)
        group = b.selectbox("Group by", list(obs.GROUP_BYS), format_func=obs.GROUP_BYS.get,
                            index=list(obs.GROUP_BYS).index("primary_category"))
        agg = obs.aggregate(df, metric, group)
        if agg.empty:
            st.info("No data.")
        else:
            ch = alt.Chart(agg).mark_bar(cornerRadius=3).encode(
                x=alt.X("group:N", sort=None, title=obs.GROUP_BYS[group]),
                y=alt.Y("value:Q", title=obs.METRICS[metric]),
                tooltip=["group", "value", "n"], color=alt.value("#1565C0")).properties(height=280)
            st.altair_chart(ch, width="stretch")
            st.markdown("**Drill down:** click a row to list the questions behind it.")
            ev = st.dataframe(agg.rename(columns={"group": obs.GROUP_BYS[group], "value": obs.METRICS[metric], "n": "questions"}),
                              hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="tbl_agg")
            sel = ev.selection.rows if ev and ev.selection else []
            if sel:
                value = agg.iloc[sel[0]]["group"]
                sub = obs.drill(df, group, value)
                st.markdown(f"**{len(sub)} questions where {obs.GROUP_BYS[group]} = `{value}`**  (click a row to open its trace)")
                selectable_table(sub.head(200), "tbl_drill")

with tab_agents:
    am = obs.agent_metrics(flt)
    if am.empty:
        st.info("No agent steps yet. Ask a question with the live agents on (LLM_PROVIDER=openai).")
    else:
        st.caption("Per agent and step type, from the stored steps.")
        st.dataframe(am, hide_index=True, width="stretch")
        c1, c2 = st.columns(2)
        by_agent = am.groupby("agent", as_index=False)[["cost_usd", "tokens"]].sum()
        c1.altair_chart(alt.Chart(by_agent).mark_bar(cornerRadius=3).encode(
            x=alt.X("agent:N", sort="-y"), y=alt.Y("cost_usd:Q", title="cost (USD)"),
            color=alt.Color("agent:N", scale=alt.Scale(domain=list(AGENT_COLORS), range=list(AGENT_COLORS.values())), legend=None),
            tooltip=["agent", "cost_usd", "tokens"]).properties(height=240, title="Cost by agent"), width="stretch")
        lat = am[am["kind"] == "llm"]
        if not lat.empty:
            c2.altair_chart(alt.Chart(lat).mark_bar(cornerRadius=3).encode(
                x=alt.X("agent:N", sort="-y"), y=alt.Y("avg_ms:Q", title="average ms per LLM call"),
                color=alt.Color("agent:N", scale=alt.Scale(domain=list(AGENT_COLORS), range=list(AGENT_COLORS.values())), legend=None),
                tooltip=["agent", "avg_ms", "p95_ms", "calls"]).properties(height=240, title="LLM latency by agent"), width="stretch")

with tab_logs:
    ev_names = ["all", "ask", "agent_fallback", "feedback", "doc_draft", "doc_review", "llm_error", "ask_error", "access"]
    a, b = st.columns([1, 3])
    ev_sel = a.selectbox("Event", ev_names)
    q_text = b.text_input("Search the log lines", placeholder="request id, status, text...")
    logs = obs.log_events(500, ev_sel, q_text)
    if not logs:
        st.info("No log events match.")
    else:
        flat = pd.DataFrame([{"ts": e.get("ts"), "event": e.get("event"), "request_id": e.get("request_id"),
                              "status": e.get("status"), "latency_ms": e.get("latency_ms"),
                              "error": e.get("error") or e.get("detail"), "raw": json.dumps(e, default=str)} for e in logs])
        st.caption(f"{len(flat)} log lines (newest first). Logs are PII-masked JSON lines in logs/app.jsonl.")
        st.dataframe(flat.drop(columns="raw"), hide_index=True, width="stretch")
        with st.expander("Raw JSON of the newest 20 lines"):
            for r in flat["raw"].head(20):
                st.code(r, language="json", wrap_lines=True)

if st.session_state.get("open_trace"):
    st.divider()
    render_detail(st.session_state["open_trace"])
