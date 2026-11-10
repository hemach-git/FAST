"""Synthesizer agent: merges specialist findings into one structured, cited answer (safety first)."""
from __future__ import annotations

from app.agents import prompts
from app.agents.base import clean_id, run_agent
from app.agents.router_agent import RouterPlan
from app.agents.specialists import Findings, _doc_tag
from app.config import get_config
from app.models.schemas import AgentStep, LLMAnswer


def synthesize(question: str, plan: RouterPlan, findings: list[Findings], hazard: bool,
               ) -> tuple[LLMAnswer, list[AgentStep], list[dict]]:
    """Returns (answer, steps, hits). hits = only the chunks the findings cite (tight grounding set)."""
    cfg = get_config()
    a = cfg["agents"]["synthesizer"]
    order = {"safety": 0}
    findings = sorted(findings, key=lambda f: order.get(f.category, 1))       # safety findings first
    hits: dict[str, dict] = {}
    for f in findings:
        for h in f.chunks:
            hits.setdefault(h["chunk_id"], h)
    lines = [f"[{f.category}] {x.claim} (cites: {', '.join(x.citation_ids)})" for f in findings for x in f.findings]
    user = (f"<question>\n{question}\n</question>\nSources searched: {[f.category for f in findings]}; "
            f"hazard={hazard}\n\nFindings:\n" + "\n".join(lines) + "\n\nEvidence sections:\n"
            + "\n".join(_doc_tag(h, cfg["retrieval"]["max_chunk_chars"]) for h in hits.values()))
    ans, steps = run_agent("synthesizer", prompts.SYNTH_SYSTEM, user, model=a["model"],
                           max_tokens=a["max_tokens"], max_steps=2, schema=LLMAnswer)
    ans.citation_ids = list(dict.fromkeys(clean_id(x) for x in ans.citation_ids))
    steps[-1].summary = f"answer with {len(ans.citation_ids)} citations, confidence={ans.confidence}"
    return ans, steps, list(hits.values())
