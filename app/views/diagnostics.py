import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))  # noqa: E702

import json

import pandas as pd
import streamlit as st

from app.config import ROOT, config_hash, get_config
from app.observability.logging import recent_logs
from app.services import workflow
from app.ui import common

common.init()
common.require_manager()
common.page_header("Diagnostics", "System health, configuration and recent requests.")

h = workflow.health()
st.subheader("Health")
row1 = st.columns(4)
row1[0].metric("Status", str(h.get("status", "?")).upper())
row1[1].metric("DB check", f"{h.get('db_ms', 0)} ms")
row1[2].metric("FK violations", h.get("fk_violations", 0))
row1[3].metric("LLM", f"{h.get('llm_provider')} / {h.get('model')}")
row2 = st.columns(5)
row2[0].metric("Published docs", h.get("published_docs"))
row2[1].metric("Draft docs", h.get("draft_docs"))
row2[2].metric("Chunks", h.get("chunks"))
row2[3].metric("Queries", h.get("queries"))
row2[4].metric("Audit rows", h.get("audit_rows"))


def redact(o):
    if isinstance(o, dict):
        return {k: ("***" if any(s in k.lower() for s in ("key", "secret", "token", "password")) else redact(v))
                for k, v in o.items()}
    return o


with st.expander(f"Config snapshot (hash {config_hash()})"):
    st.json(redact(get_config()))

st.subheader("Recent requests")
logs = recent_logs(100)
if not logs:
    st.info("No log events yet. Ask a question first.")
else:
    errors = sum(1 for e in logs if e.get("status") == "error" or e.get("error"))
    c1, c2 = st.columns(2)
    c1.metric("Events shown", len(logs))
    c2.metric("Errors", errors, delta="needs attention" if errors else None, delta_color="inverse")
    rows = []
    for e in logs:
        st_ms = e.get("stage_ms") or {}
        rows.append({"ts": e.get("ts"), "request_id": e.get("request_id"), "event": e.get("event"),
                     "status": e.get("status"), "latency_ms": e.get("latency_ms"),
                     "tokens_in": e.get("tokens_in"), "tokens_out": e.get("tokens_out"),
                     **{f"{k}_ms": v for k, v in st_ms.items()}})
    st.dataframe(pd.DataFrame(rows).iloc[::-1], hide_index=True, width="stretch")

st.subheader("Evaluation results")
p = ROOT / "evals" / "results" / "latest.json"
if p.exists():
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        s = data.get("summary", {})
        e1, e2, e3 = st.columns(3)
        e1.metric("Pass rate", f"{s.get('pass_rate')}%")
        e2.metric("Passed", f"{s.get('passed')} / {s.get('total')}")
        e3.metric("Last run", str(s.get("run_at", ""))[:16])
        if s.get("by_type"):
            st.dataframe(pd.DataFrame(s["by_type"].items(), columns=["Test type", "Pass %"]),
                         hide_index=True, width="stretch")
    except (ValueError, OSError) as exc:
        st.error(f"Could not read eval results: {exc}")
else:
    st.info("No eval results yet. Run `python -m evals.run`.")
