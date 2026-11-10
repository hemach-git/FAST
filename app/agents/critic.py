"""Faithfulness critic: checks that each claim in the drafted answer is supported by the evidence text.

The grounding check (workflow step 5) proves a citation EXISTS. This agent checks that the evidence SUPPORTS
the claim. The critic only labels; plain code (`apply`) decides what to remove and when to abstain:
  - unsupported steps are removed;
  - safety notes are NEVER removed (a wrongly deleted warning is worse than an extra one): they are flagged;
  - the answer abstains when its summary sentence is unsupported, or too many claims are unsupported;
  - evidence the critic names as support (and that was retrieved) is added to the citations.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.agents import prompts
from app.agents.base import clean_id, run_agent
from app.agents.specialists import _doc_tag
from app.config import get_config
from app.models.schemas import AgentStep, LLMAnswer


class Verdict(BaseModel):
    id: str = Field(max_length=8)                       # A1 (answer), S1..Sn (steps), N1..Nn (safety notes)
    verdict: Literal["supported", "partial", "unsupported"]
    support_ids: list[str] = Field(default_factory=list, max_length=4)
    reason: str = Field(default="", max_length=300)


class CriticReport(BaseModel):
    verdicts: list[Verdict] = Field(default_factory=list, max_length=30)


class CriticOutcome(BaseModel):
    answer: LLMAnswer
    flags: list[str] = Field(default_factory=list)
    unfaithful: bool = False                             # True -> the caller must abstain
    summary: str = ""


def claims_of(ans: LLMAnswer) -> dict[str, str]:
    """Number every checkable claim so the critic can refer to it by id."""
    items = {"A1": ans.answer}
    items.update({f"S{i}": s for i, s in enumerate(ans.steps, 1)})
    items.update({f"N{i}": s for i, s in enumerate(ans.safety_notes, 1)})
    return {k: v for k, v in items.items() if v and v.strip()}


def review(question: str, ans: LLMAnswer, hits: list[dict]) -> tuple[CriticReport, list[AgentStep]]:
    """One LLM call. Evidence = every section the synthesizer saw (cited ones first)."""
    cfg = get_config()
    a = cfg["agents"]["critic"]
    cited = set(ans.citation_ids)
    ordered = sorted(hits, key=lambda h: h["chunk_id"] not in cited)
    claims = claims_of(ans)
    user = (f"<question>\n{question}\n</question>\n\nClaims to check:\n"
            + "\n".join(f"{k}: {v}" for k, v in claims.items())
            + "\n\nEvidence sections:\n"
            + "\n".join(_doc_tag(h, cfg["retrieval"]["max_chunk_chars"]) for h in ordered))
    report, steps = run_agent("critic", prompts.CRITIC_SYSTEM, user, model=a["model"], max_tokens=a["max_tokens"],
                              max_steps=2, schema=CriticReport)
    return report, steps


def apply(ans: LLMAnswer, report: CriticReport, hits: list[dict]) -> CriticOutcome:
    """Plain-code policy on the critic's labels. Missing verdicts count as 'unchecked' and are kept."""
    max_ratio = get_config()["agents"]["critic"]["max_unsupported_ratio"]
    claims = claims_of(ans)
    by_id = {v.id.strip().upper(): v for v in report.verdicts}
    verdict = {k: (by_id[k].verdict if k in by_id else "unchecked") for k in claims}
    unsupported = [k for k, v in verdict.items() if v == "unsupported"]
    unchecked = [k for k, v in verdict.items() if v == "unchecked"]
    flags: list[str] = []

    known = {h["chunk_id"] for h in hits}
    extra = [c for v in report.verdicts if v.verdict != "unsupported"
             for c in (clean_id(x) for x in v.support_ids) if c in known and c not in ans.citation_ids]
    citation_ids = list(dict.fromkeys(ans.citation_ids + extra))[:8]

    ratio = len(unsupported) / max(len(claims), 1)
    if "A1" in unsupported or ratio > max_ratio:
        flags.append("unfaithful_answer")
        out = ans.model_copy(update={"abstain": True})
        return CriticOutcome(answer=out, flags=flags, unfaithful=True,
                             summary=f"unfaithful: {len(unsupported)}/{len(claims)} claims unsupported "
                                     f"({', '.join(unsupported)}) -> abstain")

    steps = [s for i, s in enumerate(ans.steps, 1) if f"S{i}" not in unsupported]
    if len(steps) < len(ans.steps):
        flags.append("unsupported_step_removed")
    if any(k.startswith("N") for k in unsupported):
        flags.append("unsupported_safety_note_kept")
    if extra:
        flags.append("critic_citation_added")
    out = ans.model_copy(update={"steps": steps, "citation_ids": citation_ids})
    if unsupported and out.confidence == "high":
        out.confidence = "medium"
    parts = [f"{len(claims) - len(unsupported) - len(unchecked)}/{len(claims)} claims supported"]
    if unsupported:
        parts.append("unsupported: " + ", ".join(unsupported))
    if unchecked:
        parts.append("unchecked: " + ", ".join(unchecked))
    if extra:
        parts.append(f"+{len(extra)} citations")
    return CriticOutcome(answer=out, flags=flags, summary="; ".join(parts))
