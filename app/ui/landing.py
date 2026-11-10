"""Signed-out landing page content (static HTML) for FAST: banner, hero, safeguards, how it answers, modules.

All text here is fixed marketing-style copy about what the application does; nothing user-supplied is rendered.
HTML is built without blank lines or indentation so Markdown does not turn it into code blocks.
"""
from __future__ import annotations

NAVY = "#1E293B"
ORANGE = "#E8590C"

CSS = """<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"] {display: none;}
.block-container {max-width: 1240px; padding-top: 3.2rem;}
.fa-strip {background:#FFE9A8; color:#3B2F00; text-align:center; font-size:.82rem; font-weight:600; padding:6px 10px; border-radius:6px 6px 0 0;}
.fa-bar {background:#1E293B; text-align:center; padding:14px 10px 12px; border-bottom:3px solid #E8590C; margin-bottom:14px; border-radius:0 0 6px 6px;}
.fa-bar .t {color:#fff; font-size:1.9rem; font-weight:800; letter-spacing:.42em; padding-left:.42em;}
.fa-bar .s {color:#F5B781; font-size:.72rem; font-weight:700; letter-spacing:.2em; text-transform:uppercase;}
.fa-hero {background:#fff; border:1px solid #D5DBE3; border-radius:8px; padding:22px 26px; min-height:330px;}
.fa-eyebrow {color:#64748B; font-size:.74rem; letter-spacing:.08em; text-transform:uppercase;}
.fa-hero h1 {color:#1E3A5F; font-size:1.85rem; line-height:1.2; margin:.3rem 0 .6rem; padding:0;}
.fa-hero p {color:#334155; font-size:1.0rem; line-height:1.5; margin:0 0 .8rem;}
.fa-label {color:#475569; font-size:.72rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; margin:.5rem 0 .3rem;}
.fa-guards {display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px;}
.fa-guard {border-left:3px solid #2E7D32; padding-left:10px;}
.fa-guard b {display:block; color:#1E3A5F; font-size:.92rem; margin-bottom:2px;}
.fa-guard span {display:block; color:#475569; font-size:.8rem; line-height:1.35;}
.fa-h {color:#1E3A5F; font-size:1.2rem; font-weight:700; margin:20px 0 8px;}
.fa-h small {color:#64748B; font-size:.82rem; font-weight:400; margin-left:8px;}
.fa-grid {display:grid; grid-template-columns:repeat(auto-fit,minmax(215px,1fr)); gap:10px;}
.fa-card {background:#fff; border:1px solid #D5DBE3; border-radius:8px; padding:12px 14px; display:flex; flex-direction:column; min-height:112px;}
.fa-card b {color:#1E3A5F; font-size:.95rem; margin-bottom:4px;}
.fa-card span {display:block; color:#475569; font-size:.82rem; line-height:1.4; flex:1;}
.fa-card em {color:#64748B; font-size:.74rem; font-style:normal; margin-top:8px;}
.fa-grid5 {display:grid; grid-template-columns:repeat(auto-fit,minmax(168px,1fr)); gap:10px;}
.fa-ai {border-top:3px solid #E8590C;}
.fa-code {border-top:3px solid #1E3A5F;}
.fa-foot {text-align:center; color:#64748B; font-size:.8rem; padding:14px 0 4px;}
</style>"""

BANNER = ('<div class="fa-strip">SYNTHETIC DATA · Prototype · No real plant records, people or equipment</div>'
          '<div class="fa-bar"><div class="t">FAST</div>'
          '<div class="s">Floor Assistant for Supervisors and Technicians</div></div>')

HERO = (
    '<div class="fa-hero">'
    '<div class="fa-eyebrow">Safety · maintenance · quality documents, in one place</div>'
    '<h1>The right answer from the right manual. People stay in charge.</h1>'
    '<p>FAST reads a floor question in plain words, sends it to the Safety, Maintenance or Quality documents, '
    'checks each source and answers with numbered steps and a citation for every claim. '
    'Nothing becomes official until a manager approves it.</p>'
    '<div class="fa-label">Safeguards</div>'
    '<div class="fa-guards">'
    '<div class="fa-guard"><b>Safety first</b><span>A hazard always brings in the safety procedure. '
    'A model mistake cannot drop it.</span></div>'
    '<div class="fa-guard"><b>Sources you can check</b><span>Every claim points to a real section. '
    'Not in the documents? It says "not found".</span></div>'
    '<div class="fa-guard"><b>People decide</b><span>The AI only reads documents. A manager approves, '
    'advises or rejects every change.</span></div>'
    '<div class="fa-guard"><b>Access checked</b><span>Sign-in, roles and administrator approval. '
    'Every decision is recorded.</span></div>'
    '</div></div>')


def _card(title: str, body: str, foot: str = "", kind: str = "") -> str:
    return (f'<div class="fa-card {kind}"><b>{title}</b><span>{body}</span>'
            + (f'<em>{foot}</em>' if foot else '') + '</div>')


HOW = (
    '<div class="fa-h">How FAST answers a question<small>Three AI agents read and write; plain code enforces safety, '
    'sources and approvals.</small></div><div class="fa-grid5">'
    + _card("1. Understands the question", "A router agent works out what you mean, which manuals to check, the "
            "equipment and whether it is a hazard. It asks a follow-up when the question is unclear.", "AI agent", "fa-ai")
    + _card("2. Searches three manuals at once", "Safety, Maintenance and Quality specialist agents each search only "
            "their own approved manuals, using read-only tools.", "AI agents", "fa-ai")
    + _card("3. Writes a cited answer", "A synthesizer agent merges the findings into numbered steps, safety first, "
            "with the source of each step.", "AI agent", "fa-ai")
    + _card("4. Checks every claim", "Code, not AI: the hazard override, a check that every citation is a real "
            "retrieved section, and a safe fallback if the AI is unavailable.", "Plain code", "fa-code")
    + _card("5. Records everything", "Every step, with its input, output, tokens and cost, is stored for review. "
            "Every access and approval decision is audited.", "Plain code", "fa-code")
    + '</div>')

MODULES = [
    ("Ask", "Type a floor question or pick an example. Get numbered steps, safety notes and the sources, "
            "and flag a wrong answer.", "Supervisor · Manager · Administrator"),
    ("Documents", "Browse approved procedures. Managers submit updates and approve, advise changes on or reject "
                  "drafts, with a diff and review history.", "Everyone views · Manager · Administrator approve"),
    ("Review queue", "Answers that supervisors flagged as wrong. Confirm real issues or dismiss them, with an "
                     "audit trail.", "Manager · Administrator"),
    ("Analytics", "Questions asked, top recurring floor issues, documentation gaps and a daily trend, with "
                  "CSV export.", "Manager · Administrator"),
    ("Diagnostics", "System health, configuration snapshot, recent requests and the latest evaluation results.",
     "Manager · Administrator"),
    ("Traces & logs", "Follow any answer step by step: timeline, each agent's input and output, cost, and drill-down "
                      "metrics.", "Administrator"),
    ("Users & access", "Approve or deny access requests, set roles, disable accounts and reset passwords.",
     "Administrator"),
]

MODS = ('<div class="fa-h">Every module in the application<small>Each person sees only the pages their role '
        'allows.</small></div><div class="fa-grid">' + "".join(_card(t, b, r) for t, b, r in MODULES) + '</div>')

FOOT = '<div class="fa-foot">FAST · Synthetic data · No real plant records</div>'


def inject_css() -> None:
    import streamlit as st
    st.markdown(CSS, unsafe_allow_html=True)


def banner() -> str:
    return BANNER


def below_fold() -> str:
    return HOW + MODS + FOOT
