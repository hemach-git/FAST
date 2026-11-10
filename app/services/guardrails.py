"""Input validation, prompt-injection detection, emergency detection, PII masking."""
from __future__ import annotations

import re

from app.config import get_config

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|the) (instructions|rules|prompts?)",
    r"disregard (all |any )?(previous|prior|above|your) (instructions|rules)",
    r"\bsystem prompt\b",
    r"\byou are now\b",
    r"\bact as (an? )?(unrestricted|different|jailbroken)",
    r"\bjailbreak\b",
    r"\bdeveloper mode\b",
    r"reveal (your|the) (instructions|prompt|rules|api key)",
    r"<\s*/?\s*(system|doc|instructions?)\s*>",
    r"\bbypass (the )?(safety|guard|lockout|interlock|light curtain)",
    r"\b(skip|ignore) (the )?(lockout|loto|safety)\b",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]

PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"\b(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b(?:badge|emp|employee)\s*(?:id|no\.?|#)?\s*[:#]?\s*[A-Z]{0,3}-?\d{3,8}\b", re.I), "[EMPLOYEE_ID]"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16})\b"), "[SECRET]"),
]


def mask_pii(text: str) -> str:
    for pat, repl in PII_PATTERNS:
        text = pat.sub(repl, text)
    return text


def detect_injection(text: str) -> list[str]:
    return [p.pattern for p in _INJ if p.search(text)]


def detect_emergency(text: str) -> bool:
    t = text.lower()
    return any(re.search(r"\b" + re.escape(k) + r"\b", t) for k in get_config()["emergency"]["keywords"])


def validate_question(question: str) -> tuple[bool, list[str], str]:
    """Return (ok, flags, user_message). Blocks empty/oversized/injection input."""
    g = get_config()["guardrails"]
    flags: list[str] = []
    q = (question or "").strip()
    if len(q) < g["min_question_chars"]:
        return False, ["too_short"], "Please type a question (at least a few words)."
    if len(q) > g["max_question_chars"]:
        return False, ["too_long"], f"Please keep questions under {g['max_question_chars']} characters."
    if not re.search(r"[A-Za-z]", q):
        return False, ["no_text"], "Please type your question in words."
    if detect_injection(q):
        return False, ["prompt_injection"], (
            "This request was blocked: it asks the assistant to ignore its rules or bypass a safety "
            "control. Safety procedures can never be skipped. Rephrase your question about the task.")
    if mask_pii(q) != q:
        flags.append("pii_masked")
    return True, flags, ""


def scan_document(content: str) -> list[str]:
    """Uploaded document text is untrusted: flag embedded instructions for the reviewer."""
    flags = []
    if detect_injection(content):
        flags.append("embedded_instructions")
    if mask_pii(content) != content:
        flags.append("contains_pii")
    if len(content) > get_config()["guardrails"]["max_doc_chars"]:
        flags.append("too_long")
    return flags
