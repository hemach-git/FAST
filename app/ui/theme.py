"""The one colour palette for FAST: deep green and amber.

Every screen takes its colours from here (landing page, pages, charts), and `.streamlit/config.toml`, the one-page
slide (`scripts/make_pptx.py`), the Word documents (`scripts/make_docx.py`) and the diagrams
(`scripts/render_mermaid.py`) use the same values. `tests/test_theme.py` fails if config.toml drifts from this file.

Colours that carry meaning stay fixed so people learn them once: red = danger or safety, amber = waiting,
green = good or approved, blue = maintenance.
"""
from __future__ import annotations

# Brand
DEEP = "#0F3D2E"          # title bars, sidebar, headings
DEEP_SOFT = "#175C44"     # secondary surfaces on deep green
PRIMARY = "#15803D"       # buttons, active tabs, links, chart bars
AMBER = "#F59E0B"         # accent lines, highlights
AMBER_PALE = "#FEF3C7"    # tags on deep green
AMBER_DARK = "#B45309"    # amber text or chips that need contrast on white

# Surfaces and text
BACKGROUND = "#F3F7F4"
PANEL = "#E3EDE6"
BORDER = "#CFE0D5"
TEXT = "#16251E"
TEXT_ON_DEEP = "#E6F4EC"
MUTED = "#64748B"
BODY = "#334155"

# Meaning (fixed)
OK = "#2E7D32"
WARN = "#D97706"
DANGER = "#C62828"
INFO = "#1565C0"          # maintenance
