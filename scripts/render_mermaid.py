"""Render ```mermaid blocks (first line '%% name: <file>') from docs/*.md to docs/img/<file>.png.

Uses Playwright with the locally installed Chrome (channel="chrome") and mermaid@10.9.1 from jsDelivr.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
IMG = DOCS / "img"
BLOCK = re.compile(r"```mermaid\n(.*?)```", re.S)

HTML = """<!doctype html><html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<style>body{margin:0;background:#fff;font-family:Arial} #c{display:inline-block;padding:16px}</style></head>
<body><div id="c"><pre class="mermaid">%s</pre></div>
<script>mermaid.initialize({startOnLoad:true, theme:'default', themeVariables:{fontSize:'18px'},
flowchart:{nodeSpacing:30, rankSpacing:35, htmlLabels:true, useMaxWidth:false}, sequence:{mirrorActors:false, useMaxWidth:false}, er:{useMaxWidth:false}});</script>
</body></html>"""


def blocks() -> list[tuple[str, str]]:
    out = []
    for md in sorted(DOCS.glob("*.md")):
        for m in BLOCK.finditer(md.read_text(encoding="utf-8")):
            src = m.group(1)
            name = re.search(r"%%\s*name:\s*(\S+)", src)
            if name:
                out.append((name.group(1), src))
    return out


def main() -> int:
    IMG.mkdir(parents=True, exist_ok=True)
    items = blocks()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(device_scale_factor=3, viewport={"width": 1400, "height": 900})
        for name, src in items:
            page.set_content(HTML % src.replace("<", "&lt;").replace(">", "&gt;"))
            page.wait_for_selector("#c svg", timeout=20000)
            tmp = IMG / f"_{name}.png"
            page.locator("#c").screenshot(path=str(tmp))
            os.replace(tmp, IMG / f"{name}.png")
            print("rendered", name)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
