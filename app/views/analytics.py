import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))  # noqa: E702

import altair as alt
import streamlit as st

from app.services import analytics, freshness
from app.ui import common
from app.ui.common import CAT_COLORS

common.init()
common.require_manager()
common.page_header("Analytics", "What supervisors are asking, and where the documents fall short.")

CAT_SCALE = alt.Scale(domain=list(CAT_COLORS), range=list(CAT_COLORS.values()))

all_df = analytics.queries_df(days=None)
equip_opts = sorted(all_df["equipment_tag"].dropna().unique()) if not all_df.empty else []

f1, f2, f3, f4 = st.columns(4)
days = f1.selectbox("Period", [7, 30, 90], index=1, format_func=lambda d: f"Last {d} days")
cat = f2.selectbox("Category", ["all", *CAT_COLORS], format_func=str.title)
equip = f3.selectbox("Equipment", ["all", *equip_opts])
synth = f4.toggle("Include synthetic data", value=True)

df = analytics.queries_df(days=days, category=None if cat == "all" else cat,
                          equipment=None if equip == "all" else equip, include_synthetic=synth)

if df.empty:
    st.info("No questions match these filters.")
    st.stop()

k = analytics.kpis(df)
st.markdown(f'<span class="synthetic-badge" style="padding:3px 10px;border-radius:6px;font-size:.75rem">'
            f'Synthetic data, demo only</span> &nbsp; <span class="small-muted">'
            f'{k["synthetic_share_pct"]}% of these questions are synthetic.</span>', unsafe_allow_html=True)

m = st.columns(5)
m[0].metric("Questions", k["total_queries"])
m[1].metric("Answer rate", f"{k['answer_rate_pct']}%")
m[2].metric("Safety-routed", f"{k['safety_routed_pct']}%")
m[3].metric("p95 latency", f"{k['p95_latency_ms']:.0f} ms")
m[4].metric("Helpful", "n/a" if k["helpful_pct"] is None else f"{k['helpful_pct']}%")

with st.container(border=True):
    st.markdown("**Key takeaways**")
    for line in analytics.insights(k):
        st.markdown(f"- {line}")
    fk = freshness.counts()
    if fk["overdue"] or fk["due_soon"]:
        st.markdown(f"- **Document reviews:** {fk['overdue']} overdue and {fk['due_soon']} due soon "
                    f"(of {fk['total']} published). See Documents → Review dates.")

trend = analytics.daily_trend(df)
cb = analytics.category_breakdown(df)
c1, c2 = st.columns([3, 2])
with c1:
    st.subheader("Daily questions")
    st.altair_chart(alt.Chart(trend).mark_bar().encode(
        x=alt.X("date:T", title=None), y=alt.Y("queries:Q", title="Questions"),
        color=alt.Color("primary_category:N", scale=CAT_SCALE, title="Category"),
        tooltip=["date:T", "primary_category", "queries"]).properties(height=280), width="stretch")
with c2:
    st.subheader("By category")
    st.altair_chart(alt.Chart(cb).mark_bar().encode(
        x=alt.X("queries:Q", title="Questions"), y=alt.Y("category:N", sort="-x", title=None),
        color=alt.Color("category:N", scale=CAT_SCALE, legend=None),
        tooltip=["category", "queries"]).properties(height=280), width="stretch")

st.subheader("Top recurring issues")
ti = analytics.top_issues(df)
if ti.empty:
    st.info("No equipment-specific questions in this period.")
else:
    ti = ti.assign(issue=ti["equipment_tag"] + " · " + ti["primary_category"])
    st.altair_chart(alt.Chart(ti).mark_bar().encode(
        x=alt.X("queries:Q", title="Questions"), y=alt.Y("issue:N", sort="-x", title=None),
        color=alt.Color("primary_category:N", scale=CAT_SCALE, legend=None),
        tooltip=["equipment_tag", "primary_category", "queries", "abstained"]).properties(height=300),
        width="stretch")

d1, d2 = st.columns(2)
with d1:
    st.subheader("Documentation gaps")
    st.caption("Questions the documents couldn't answer. Candidates for new documents.")
    gaps = analytics.doc_gaps(df)
    if gaps.empty:
        st.success("No unanswered questions in this period.")
    else:
        st.dataframe(gaps.rename(columns={"question_masked": "Question", "times_asked": "Times asked"}),
                     hide_index=True, width="stretch")
with d2:
    st.subheader("Most-cited documents")
    top = analytics.top_cited_docs(days)
    if top.empty:
        st.info("No citations yet.")
    else:
        st.altair_chart(alt.Chart(top).mark_bar().encode(
            x=alt.X("citations:Q", title="Citations"), y=alt.Y("doc_key:N", sort="-x", title=None),
            color=alt.Color("category:N", scale=CAT_SCALE, legend=None),
            tooltip=["doc_key", "title", "citations"]).properties(height=280), width="stretch")

st.subheader("Drill down by equipment")
eq_in_df = sorted(df["equipment_tag"].dropna().unique())
if eq_in_df:
    pick = st.selectbox("Equipment", eq_in_df, key="drill_eq")
    sub = df[df["equipment_tag"] == pick][["created_at", "question_masked", "categories", "status", "latency_ms"]]
    st.dataframe(sub, hide_index=True, width="stretch")
else:
    st.info("No equipment tags in the selected data.")

st.download_button("Download questions (CSV)", analytics.export_csv(df), "fast_questions.csv", "text/csv",
                   icon=":material/download:")
