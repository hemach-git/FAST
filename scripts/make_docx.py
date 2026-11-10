"""Convert docs/*.md (scope, estimates_risks, design + architecture) to .docx with diagrams as PNGs.

Minimal Markdown support: headings, paragraphs, bullets, numbered lists, tables, blockquotes, **bold**, `code`,
fenced code; ```mermaid blocks are replaced by docs/img/<name>.png (landscape section if wide).
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
IMG = DOCS / "img"


def add_runs(par, text: str) -> None:
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not part:
            continue
        if part.startswith("**"):
            par.add_run(part[2:-2]).bold = True
        elif part.startswith("`"):
            r = par.add_run(part[1:-1])
            r.font.name = "Consolas"
        else:
            par.add_run(re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", part))


def set_landscape(section, landscape: bool) -> None:
    w, h = section.page_width, section.page_height
    if landscape != (w > h):
        section.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
        section.page_width, section.page_height = h, w
    for m in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, m, Inches(0.75))


def add_image(doc, name: str) -> None:
    from PIL import Image  # pillow ships with python-pptx

    path = IMG / f"{name}.png"
    if not path.exists():
        doc.add_paragraph(f"[diagram {name} missing - run scripts/render_mermaid.py]")
        return
    w, h = Image.open(path).size
    wide = w / h > 1.25
    sec = doc.add_section()
    set_landscape(sec, wide)
    usable_w = (sec.page_width - sec.left_margin - sec.right_margin) / 914400
    usable_h = (sec.page_height - sec.top_margin - sec.bottom_margin) / 914400 - 0.8
    width = min(usable_w, usable_h * w / h)
    doc.add_picture(str(path), width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    if wide:
        set_landscape(doc.add_section(), False)


def convert(md_files: list[Path], out: Path, title: str) -> None:
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    set_landscape(doc.sections[0], False)
    t = doc.add_heading(title, 0)
    p = doc.add_paragraph()
    r = p.add_run("SYNTHETIC DATA - prototype documentation")
    r.bold, r.font.color.rgb = True, RGBColor(0xB4, 0x53, 0x09)
    for md in md_files:
        lines = md.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.startswith("```"):
                j = i + 1
                while j < len(lines) and not lines[j].startswith("```"):
                    j += 1
                block = lines[i + 1:j]
                m = re.search(r"%%\s*name:\s*(\S+)", "\n".join(block))
                if ln.startswith("```mermaid") and m:
                    add_image(doc, m.group(1))
                else:
                    cp = doc.add_paragraph()
                    run = cp.add_run("\n".join(block))
                    run.font.name, run.font.size = "Consolas", Pt(8)
                i = j + 1
                continue
            if ln.startswith("|"):
                tbl = []
                while i < len(lines) and lines[i].startswith("|"):
                    if not re.match(r"^\|[\s:\-|]+\|$", lines[i]):
                        tbl.append([c.strip() for c in lines[i].strip("|").split("|")])
                    i += 1
                table = doc.add_table(rows=len(tbl), cols=len(tbl[0]))
                table.style = "Light Grid Accent 1"
                for ri, row in enumerate(tbl):
                    for ci, cell in enumerate(row[: len(tbl[0])]):
                        c = table.cell(ri, ci)
                        c.text = ""
                        add_runs(c.paragraphs[0], cell)
                        for run in c.paragraphs[0].runs:
                            run.font.size = Pt(9)
                            run.bold = run.bold or ri == 0
                doc.add_paragraph()
                continue
            h = re.match(r"^(#{1,4})\s+(.*)", ln)
            if h:
                doc.add_heading(h.group(2), min(len(h.group(1)), 3))
            elif re.match(r"^\s*[-*]\s+", ln):
                add_runs(doc.add_paragraph(style="List Bullet"), re.sub(r"^\s*[-*]\s+", "", ln))
            elif re.match(r"^\s*\d+\.\s+", ln):
                add_runs(doc.add_paragraph(style="List Number"), re.sub(r"^\s*\d+\.\s+", "", ln))
            elif ln.startswith(">"):
                q = doc.add_paragraph(style="Intense Quote")
                add_runs(q, ln.lstrip("> "))
            elif ln.strip():
                add_runs(doc.add_paragraph(), ln)
            i += 1
    tmp = out.with_name("_" + out.name)
    doc.save(tmp)
    tmp.replace(out)
    print("wrote", out.relative_to(ROOT))


if __name__ == "__main__":
    convert([DOCS / "scope.md", DOCS / "architecture.md", DOCS / "data_model.md", DOCS / "data_sources.md"],
            DOCS / "scope.docx", "FAST - Scope Document")
    convert([DOCS / "estimates_risks.md"], DOCS / "estimates_risks.docx", "FAST - Estimates and Risk Register")
    convert([DOCS / "design.md"], DOCS / "design.docx", "FAST - Compact Design")
