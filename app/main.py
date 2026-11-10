import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # noqa: E702

import streamlit as st

from app.ui import common

st.set_page_config(page_title="FAST", page_icon=":material/precision_manufacturing:", layout="wide")
common.init()
common.sidebar_identity()

pages = [
    st.Page("views/ask.py", title="Ask", icon=":material/forum:", default=True),
    st.Page("views/documents.py", title="Documents", icon=":material/description:"),
]
if common.is_manager():
    pages += [
        st.Page("views/review_queue.py", title="Review queue", icon=":material/flag:"),
        st.Page("views/analytics.py", title="Analytics", icon=":material/monitoring:"),
        st.Page("views/diagnostics.py", title="Diagnostics", icon=":material/health_and_safety:"),
    ]
if common.is_admin():
    pages += [
        st.Page("views/traces.py", title="Traces & logs", icon=":material/account_tree:"),
        st.Page("views/users.py", title="Users & access", icon=":material/manage_accounts:"),
    ]

st.navigation(pages).run()
