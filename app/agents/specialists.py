"""Specialist agents (safety / maintenance / quality): each can only search its own published manuals."""
from __future__ import annotations

import time

from pydantic import BaseModel, Field

from app.agents import prompts
from app.agents.base import AgentError, Tool, clean_id, run_agent
from app.config import get_config
from app.models.schemas import AgentStep
from app.services import retriever
from app.services.guardrails import mask_pii


class Finding(BaseModel):
    claim: str = Field(max_length=600)
    citation_ids: list[str] = Field(default_factory=list, max_length=6)


class Findings(BaseModel):
    category: str
    findings: list[Finding] = Field(default_factory=list, max_length=14)
    abstain: bool = False
    chunks: list[dict] = Field(default_factory=list, exclude=True)   # retrieved + cited chunks (set by code)


def _doc_tag(h: dict, limit: int) -> str:
    return (f'<doc id="{h["chunk_id"]}" source="{h["doc_key"]} v{h["version"]}" section="{h["section"]}">\n'
            f'{h["text"][:limit]}\n</doc>')


def research(category: str, question: str, search_query: str) -> tuple[Findings, list[AgentStep]]:
    cfg = get_config()
    a = cfg["agents"]["specialist"]
    max_chars = cfg["retrieval"]["max_chunk_chars"]
    retrieved: dict[str, dict] = {}

    def search_docs(query: str, top_k: int = 4) -> str:
        hits = retriever.search(str(query)[:200], [category], top_k=max(1, min(int(top_k), 5)))
        retrieved.update({h["chunk_id"]: h for h in hits})
        return "\n".join(_doc_tag(h, 700) for h in hits) or "No matching sections found."

    def read_section(chunk_id: str) -> str:
        h = retrieved.get(chunk_id)
        return _doc_tag(h, max_chars) if h else "Unknown chunk_id. Use an id returned by search_docs."

    tools = [
        Tool("search_docs", f"Search the published {category} documents. Returns matching sections.",
             {"type": "object", "properties": {"query": {"type": "string"},
                                               "top_k": {"type": "integer", "minimum": 1, "maximum": 5}},
              "required": ["query"]}, search_docs),
        Tool("read_section", "Read the full text of one section returned by search_docs.",
             {"type": "object", "properties": {"chunk_id": {"type": "string"}}, "required": ["chunk_id"]},
             read_section),
    ]
    # First search runs in code (deterministic) and is handed to the agent: most specialists then need ONE LLM call.
    t0 = time.perf_counter()
    pre_out = search_docs(search_query, 3)  # fills `retrieved`
    pre = AgentStep(agent=category, kind="tool", name="search_docs", latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                    summary=f"pre-fetched search: {len(retrieved)} sections", input=mask_pii(search_query)[:300],
                    output=", ".join(retrieved) or "no matches")
    full = "\n".join(_doc_tag(h, max_chars) for h in retrieved.values()) or "No matching sections found."
    user = (f"<question>\n{question}\n</question>\nSuggested search query: {search_query}\n\n"
            f"First search results:\n{full}")
    try:
        out, steps = run_agent(category, prompts.SPECIALIST_SYSTEM.format(category=category.capitalize()), user,
                               model=a["model"], max_tokens=a["max_tokens"], max_steps=a["max_steps"],
                               schema=Findings, tools=tools, )
    except AgentError as e:
        e.steps = [pre] + e.steps
        raise
    steps.insert(0, pre)
    # Grounding at the source: keep only findings whose citations were actually retrieved by THIS agent.
    kept = []
    for f in out.findings:
        ids = [c for c in dict.fromkeys(clean_id(x) for x in f.citation_ids) if c in retrieved]
        if ids:
            kept.append(Finding(claim=f.claim, citation_ids=ids))
    out.category, out.findings = category, kept
    out.abstain = out.abstain or not kept
    out.chunks = [retrieved[c] for c in dict.fromkeys(c for f in kept for c in f.citation_ids)]
    steps[-1].summary = f"{len(kept)} cited findings" + (" (abstained)" if out.abstain else "")
    return out, steps
