"""Final one-page slide for plant managers and business leaders (plain English, editable text boxes).

    python scripts/make_pptx.py   ->  docs/one_pager_deck.pptx

Numbers come from result files, never typed in by hand where a file exists:
  - evals/results/latest_pipeline.json      rule-based version on a test model (plumbing check only)
  - evals/results/latest_multi_agent.json   formal live-AI evaluation, used when it exists
Without the live-AI file the slide shows the informal 9-question live check, labelled "quick check".
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "evals" / "results"
# p95 goal from config.yaml targets (an ASSUMPTION TO CONFIRM, see the comment there)
GOAL_S = (__import__("yaml").safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
          .get("targets", {}).get("p95_latency_ms_agent", 8000)) / 1000
NAVY, ORANGE, GREY, WHITE = RGBColor(0x0F, 0x3D, 0x2E), RGBColor(0xB4, 0x53, 0x09), RGBColor(0x47, 0x55, 0x69), RGBColor(0xFF, 0xFF, 0xFF)   # NAVY = deep green, ORANGE = dark amber (theme.py)
RED, BLUE, GREEN, LIGHT = RGBColor(0xDC, 0x26, 0x26), RGBColor(0x25, 0x63, 0xEB), RGBColor(0x15, 0x80, 0x3D), RGBColor(0xEC, 0xF4, 0xEF)
PALE = RGBColor(0xCB, 0xD5, 0xE1)
H = 15  # section heading size (pt); body text is never below 12 pt


def box(slide, x, y, w, h, fill=None, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(1.25)
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = 0.06
    s.shadow.inherit = False
    return s


def text(slide, x, y, w, h, lines, size=12, color=NAVY, align=PP_ALIGN.LEFT, after=3):
    """lines: str (body style) or (text, size, color, bold) or a list of such tuples = one paragraph, several runs."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, Inches(0.04))
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(after)
        runs = ln if isinstance(ln, list) else [ln if isinstance(ln, tuple) else (ln, size, color, False)]
        for content, sz, col, bold in runs:
            assert sz >= 12, f"font below 12 pt: {content!r}"
            r = p.add_run()
            r.text = content
            r.font.size, r.font.color.rgb, r.font.bold, r.font.name = Pt(sz), col, bold, "Calibri"
    return tb


def live_lines() -> list:
    """Live-AI result lines: the formal evaluation if its file exists, else the informal quick check."""
    path = RESULTS / "latest_multi_agent.json"
    if path.exists():
        s = json.loads(path.read_text(encoding="utf-8")).get("summary", {})
        print("using formal live evaluation:", path.relative_to(ROOT))
        lines = [("Live AI, full test set", 12.5, NAVY, True),
                 [(f"{s['passed']}/{s['total']}", 20, NAVY, True), ("  test questions passed", 12, GREY, False)]]
        facts = []
        if "safety_inclusion_recall" in s:
            facts.append(f"safety step included for hazards: {s['safety_inclusion_recall']:.0f}%")
        if "abstain_correctness" in s:
            facts.append(f"said \"not found\" when it should: {s['abstain_correctness']:.0f}%")
        if "p95_latency_ms" in s:
            facts.append(f"95% of answers within {s['p95_latency_ms'] / 1000:.1f} s (goal, to be confirmed: under {GOAL_S:g} s)")
        return lines + [("; ".join(facts) + ".", 12, GREY, False)] if facts else lines
    print("no formal live evaluation yet: using the informal 9-question quick check")
    return [
        ("Live AI, quick check of 9 questions (not a full test)", 12.5, NAVY, True),
        [("7 of 9", 20, NAVY, True), ("  answered with sources", 12, GREY, False)],
        ("1 off-topic question (cafeteria menu) was correctly refused. "
         "1 vague question was refused instead of asking a follow-up.", 12, GREY, False),
        (f"Average 7.2 s, slowest 11.3 s. Goal (to be confirmed): under {GOAL_S:g} s, so slow safety questions miss it.",
         12, GREY, False),
        ("Some answers add extra lockout steps: wordy, but safe.", 12, GREY, False),
    ]


def main() -> None:
    res = json.loads((RESULTS / ("latest_pipeline.json" if (RESULTS / "latest_pipeline.json").exists() else "latest.json")).read_text(encoding="utf-8"))["summary"]
    m = re.search(r"(\d+) passed", (ROOT / "tests" / "results" / "pytest_latest.txt").read_text(encoding="utf-8"))
    failed = re.search(r"(\d+) failed", (ROOT / "tests" / "results" / "pytest_latest.txt").read_text(encoding="utf-8"))
    n_pass = int(m.group(1)) if m else 0
    tests = f"{n_pass}/{n_pass + (int(failed.group(1)) if failed else 0)}"
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[6])

    # Header: what it is (one line)
    box(s, 0, 0, 13.333, 1.15, fill=NAVY, shape=MSO_SHAPE.RECTANGLE)
    text(s, 0.45, 0.12, 9.8, 0.6, [("FAST: the right answer from the right manual", 28, WHITE, True)])
    text(s, 0.45, 0.68, 10.2, 0.42, [("It sends plant-floor questions to the Safety, Maintenance or Quality documents "
                                     "and answers with numbered steps and sources.", 14, PALE, False)])
    tag = box(s, 10.75, 0.33, 2.2, 0.48, fill=ORANGE)
    tag.text_frame.text = "Prototype · made-up data"
    for p in tag.text_frame.paragraphs:
        p.alignment = PP_ALIGN.CENTER
        for r in p.runs:
            r.font.size, r.font.bold, r.font.color.rgb = Pt(12), True, WHITE

    # Row 1: the problem | how it works
    box(s, 0.45, 1.32, 3.85, 2.0, fill=LIGHT)
    text(s, 0.62, 1.38, 3.55, 1.92, [
        ("The problem", H, ORANGE, True),
        "Supervisors search three binders to fix floor problems.",
        "That wastes time on the line.",
        "A fix without the right safety step can hurt someone.",
        "Managers cannot see which problems keep repeating."], after=2)

    text(s, 4.48, 1.3, 8.4, 0.4, [("How it works", H, ORANGE, True)])
    steps = [("1  Ask", "A supervisor types a question in plain words, like \"Conveyor C3 is jammed\".", NAVY),
             ("2  Understand", "One AI helper works out the question and which manuals to check.", BLUE),
             ("3  Search", "Three AI helpers search their own manuals at once, by meaning and keywords.", GREEN),
             ("4  Answer", "One AI helper writes numbered steps with sources; a checker drops steps the manuals don't support.", RED)]
    for i, (head, body, col) in enumerate(steps):
        x = 4.48 + i * 2.11
        box(s, x, 1.7, 1.99, 1.62, fill=WHITE, line=col)
        text(s, x + 0.08, 1.75, 1.83, 1.55, [(head, 15, col, True), (body, 13, GREY, False)])

    # Row 2: trust | tested | who
    y2, h2 = 3.5, 2.92
    box(s, 0.45, y2, 3.85, h2, fill=LIGHT)
    text(s, 0.62, y2 + 0.08, 3.55, h2 - 0.1, [
        ("Why you can trust it", H, ORANGE, True),
        ("Safety rules are plain computer code, not AI:", 12, NAVY, True),
        "• A hazard always brings in the safety procedure.",
        "• Every claim must point to a real section; a second AI check removes unsupported steps.",
        "• Not in the approved documents? It says \"not found\".",
        "• AI helpers can read documents, never change them.",
        "• Only a manager can approve a document change; overdue document reviews are flagged.",
        "• Every action is recorded."], after=2)

    box(s, 4.48, y2, 5.15, h2, fill=LIGHT)
    text(s, 4.65, y2 + 0.08, 4.85, h2 - 0.1, [
        ("What we tested (honest numbers)", H, ORANGE, True),
        ("Basic rule-based version, test setup (no live AI)", 12.5, NAVY, True),
        [(f"{res['passed']}/{res['total']}", 20, NAVY, True),
         (f"  questions ({len(res['known_gap_xfail'])} known gaps)   " if res.get("known_gap_xfail")
          else "  test questions   ", 12, GREY, False),
         (tests, 20, NAVY, True), ("  automated tests", 12, GREY, False)],
        ("This proves the parts work together, not the quality of the AI.", 12, GREY, False),
    ] + live_lines(), after=2)

    box(s, 9.81, y2, 3.08, h2, fill=LIGHT)
    text(s, 9.96, y2 + 0.08, 2.85, h2 - 0.1, [
        ("Who it helps", H, ORANGE, True),
        ("Floor supervisors", 12.5, NAVY, True),
        ("Fast answers they can check, during the shift.", 12, GREY, False),
        ("Plant managers", 12.5, NAVY, True),
        ("Approve, reject or advise on document changes.", 12, GREY, False),
        ("Review flagged answers; see repeat floor problems (CSV export).", 12, GREY, False),
        ("Administrators", 12.5, NAVY, True),
        ("Approve who gets access. Follow any answer step by step, with its cost.", 12, GREY, False)], after=2)

    # What is next
    box(s, 0.45, 6.55, 12.44, 0.5, fill=NAVY)
    text(s, 0.62, 6.6, 12.1, 0.42, [[("What is next:  ", 14, ORANGE, True),
                                     ("real documents from one plant line  ·  company single sign-on  ·  faster answers  ·  "
                                      "confirm the speed goal and review intervals", 14, WHITE, False)]])
    # Fine print
    text(s, 0.45, 7.08, 12.44, 0.36, [(
        "Uses OpenAI gpt-4o-mini. Runs on a local computer. If the AI is unavailable, it falls back to a simple rule-based mode. "
        "All documents and data are synthetic (made up).", 12, GREY, False)])

    out = ROOT / "docs" / "one_pager_deck.pptx"
    tmp = out.with_name("_" + out.name)
    prs.save(tmp)
    tmp.replace(out)
    print("wrote", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
