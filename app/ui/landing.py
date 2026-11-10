"""Signed-out landing page content (static HTML) for FAST: compact one-page layout.

Banner, hero with four safeguards, "how FAST answers" row, and one card per module. All text is fixed copy about
what the application does; nothing user-supplied is rendered. HTML is built without blank lines or indentation so
Markdown does not turn it into code blocks.
"""
from __future__ import annotations

CSS = """<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"],
header[data-testid="stHeader"] {display: none;}
.block-container {max-width: 1320px; padding: 0.7rem 1.2rem 0.4rem;}
div[data-testid="stVerticalBlock"] {gap: 0.5rem;}
.fa-bar {background:#0F3D2E; display:flex; align-items:center; justify-content:space-between; gap:14px; padding:8px 18px;
         border-bottom:3px solid #F59E0B; border-radius:6px; margin-bottom:.5rem;}
.fa-bar .t {color:#FFFFFF; font-size:1.45rem; font-weight:800; letter-spacing:.34em; white-space:nowrap;}
.fa-bar .s {color:#FCD34D; font-size:.72rem; font-weight:700; letter-spacing:.16em; text-transform:uppercase; text-align:center; flex:1;}
.fa-bar .b {background:#FEF3C7; color:#3B2F00; font-size:.72rem; font-weight:700; padding:4px 10px; border-radius:5px; white-space:nowrap;}
.fa-hero {background:#fff; border:1px solid #D5DBE3; border-radius:8px; padding:18px 24px; min-height:340px;}
.fa-eyebrow {color:#64748B; font-size:.76rem; letter-spacing:.08em; text-transform:uppercase;}
.fa-hero h1 {color:#1E3A5F; font-size:1.8rem; line-height:1.2; margin:.25rem 0 .55rem; padding:0;}
.fa-hero p {color:#334155; font-size:1.02rem; line-height:1.5; margin:0 0 .9rem;}
.fa-label {color:#475569; font-size:.74rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; margin:.2rem 0 .25rem;}
.fa-guards {display:grid; grid-template-columns:repeat(auto-fit,minmax(135px,1fr)); gap:10px;}
.fa-guard {border-left:3px solid #2E7D32; padding-left:9px;}
.fa-guard b {display:block; color:#1E3A5F; font-size:.95rem; margin-bottom:2px;}
.fa-guard span {display:block; color:#475569; font-size:.85rem; line-height:1.38;}
.fa-h {color:#1E3A5F; font-size:1.02rem; font-weight:700; margin:6px 0 5px;}
.fa-h small {color:#64748B; font-size:.76rem; font-weight:400; margin-left:8px;}
.fa-grid {display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:8px;}
.fa-card {background:#fff; border:1px solid #D5DBE3; border-radius:7px; padding:8px 11px; display:flex; flex-direction:column;}
.fa-card b {color:#1E3A5F; font-size:.86rem; margin-bottom:2px;}
.fa-card span {display:block; color:#475569; font-size:.77rem; line-height:1.32; flex:1;}
.fa-card em {color:#64748B; font-size:.7rem; font-style:normal; margin-top:5px;}
.fa-ai {border-top:3px solid #E8590C;}
.fa-code {border-top:3px solid #1E3A5F;}
.fa-foot {text-align:center; color:#64748B; font-size:.74rem; padding:4px 0 0;}
</style>"""

BANNER = ('<div class="fa-bar"><div class="t">FAST</div>'
          '<div class="s">Floor Assistant for Supervisors and Technicians</div>'
          '<div class="b">SYNTHETIC DATA · no real plant records</div></div>')

HERO = (
    '<div class="fa-hero">'
    '<div class="fa-eyebrow">Safety · maintenance · quality documents in one place</div>'
    '<h1>The right answer from the right manual. People stay in charge.</h1>'
    '<p>FAST reads a floor question in plain words, checks the Safety, Maintenance and Quality documents and answers '
    'with numbered steps and a source for every claim. Nothing becomes official until a manager approves it.</p>'
    '<div class="fa-label">Safeguards</div>'
    '<div class="fa-guards">'
    '<div class="fa-guard"><b>Safety first</b><span>A hazard always brings in the safety procedure.</span></div>'
    '<div class="fa-guard"><b>Sources you can check</b><span>Every claim cites a real section, or it says "not found".</span></div>'
    '<div class="fa-guard"><b>People decide</b><span>The AI only reads. Managers approve every change.</span></div>'
    '<div class="fa-guard"><b>Access checked</b><span>Sign-in, roles and approval, all recorded.</span></div>'
    '</div></div>')


def _card(title: str, body: str, foot: str = "", kind: str = "") -> str:
    return (f'<div class="fa-card {kind}"><b>{title}</b><span>{body}</span>'
            + (f'<em>{foot}</em>' if foot else '') + '</div>')


HOW = (
    '<div class="fa-h">How FAST answers<small>Three AI agents read and write; plain code enforces safety, sources and '
    'approvals.</small></div><div class="fa-grid">'
    + _card("1. Understands", "A router agent finds the topic, equipment and hazard, or asks a follow-up.", "AI agent", "fa-ai")
    + _card("2. Searches", "Safety, Maintenance and Quality agents search their own manuals, read-only.", "AI agents", "fa-ai")
    + _card("3. Answers", "A writer agent gives numbered steps, safety first, with sources.", "AI agent", "fa-ai")
    + _card("4. Checks", "Hazard override, every citation verified, safe fallback if the AI is down.", "Plain code", "fa-code")
    + _card("5. Records", "Every step, cost and decision is stored for review and audit.", "Plain code", "fa-code")
    + '</div>')

MODULES = [
    ("Ask", "Ask in plain words. Get steps, safety notes and sources.", "Everyone"),
    ("Documents", "Browse approved documents. Approve, advise or reject drafts.", "Managers approve"),
    ("Review queue", "Confirm or dismiss answers flagged as wrong.", "Manager · Admin"),
    ("Analytics", "Recurring issues, documentation gaps, trends, CSV.", "Manager · Admin"),
    ("Diagnostics", "Health, configuration and evaluation results.", "Manager · Admin"),
    ("Traces & logs", "Follow any answer step by step, with cost and drill-down.", "Admin"),
    ("Users & access", "Approve access, set roles, reset passwords.", "Admin"),
]

MODS = ('<div class="fa-h">Every page in the application<small>Each person sees only the pages their role '
        'allows.</small></div><div class="fa-grid">' + "".join(_card(t, b, r) for t, b, r in MODULES) + '</div>')

FOOT = '<div class="fa-foot">FAST · Synthetic data · No real plant records, people or equipment</div>'


def inject_css() -> None:
    import streamlit as st
    st.markdown(CSS, unsafe_allow_html=True)


def banner() -> str:
    return BANNER


def below_fold() -> str:
    return HOW + MODS + FOOT
