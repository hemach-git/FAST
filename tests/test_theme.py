"""The colour palette lives in app/ui/theme.py; every other place that needs the colours must agree with it."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from app.ui import landing, theme

ROOT = Path(__file__).resolve().parent.parent


def test_streamlit_theme_matches_theme_py():
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))["theme"]
    assert cfg["primaryColor"].upper() == theme.PRIMARY
    assert cfg["backgroundColor"].upper() == theme.BACKGROUND
    assert cfg["secondaryBackgroundColor"].upper() == theme.PANEL
    assert cfg["textColor"].upper() == theme.TEXT
    sb = cfg["sidebar"]
    assert sb["backgroundColor"].upper() == theme.DEEP
    assert sb["secondaryBackgroundColor"].upper() == theme.DEEP_SOFT
    assert sb["textColor"].upper() == theme.TEXT_ON_DEEP


def test_slide_and_documents_use_the_palette():
    deck = (ROOT / "scripts" / "make_pptx.py").read_text(encoding="utf-8")
    assert "RGBColor(0x0F, 0x3D, 0x2E)" in deck          # theme.DEEP
    assert "RGBColor(0xB4, 0x53, 0x09)" in deck          # theme.AMBER_DARK
    assert "0F3D2E" in (ROOT / "scripts" / "render_mermaid.py").read_text(encoding="utf-8").upper()


def test_landing_page_uses_only_palette_colours_for_its_brand_elements():
    css = landing.CSS.upper()
    for needed in (theme.DEEP, theme.AMBER, theme.PRIMARY):
        assert needed in css
    assert "#E8590C" not in css and "#1E293B" not in css       # the old orange / navy theme


def test_no_pages_folder_next_to_main():
    """A `pages/` folder makes Streamlit draw its own sidebar list before sign-in (a flash on the landing page)."""
    assert not (ROOT / "app" / "pages").exists()
    assert (ROOT / "app" / "views").is_dir()
