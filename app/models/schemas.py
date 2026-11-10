"""Pydantic I/O and domain models. This is the interface contract shared with the UI."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["safety", "maintenance", "quality"]
Role = Literal["supervisor", "manager"]
CATEGORIES: tuple[str, ...] = ("safety", "maintenance", "quality")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    role: Role = "supervisor"
    user: str = Field(default="demo.supervisor", min_length=1, max_length=60)

    @field_validator("question")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class RouteDecision(BaseModel):
    categories: list[Category]            # ordered, primary first
    primary: Category
    confidence: float = Field(ge=0, le=1)
    safety_flag: bool = False              # hazard detected -> safety forced in
    broad_search: bool = False             # low confidence -> searched all sources
    method: str = "rules+classifier"
    scores: dict[str, float] = {}
    reason: str = ""


class Citation(BaseModel):
    chunk_id: str
    doc_id: str
    doc_key: str
    doc_title: str
    version: int
    category: Category
    section: str
    snippet: str
    score: float


class LLMAnswer(BaseModel):
    """Structured output the LLM must return (validated; retry then fallback)."""
    answer: str = Field(max_length=2000)
    steps: list[str] = Field(default_factory=list, max_length=12)
    citation_ids: list[str] = Field(default_factory=list, max_length=8)
    safety_notes: list[str] = Field(default_factory=list, max_length=5)
    confidence: Literal["high", "medium", "low"] = "medium"
    abstain: bool = False


class AgentStep(BaseModel):
    """One step in the agent trace (LLM call, tool call or deterministic guard)."""
    seq: int = 0
    agent: str                              # router | safety | maintenance | quality | synthesizer | orchestrator
    kind: Literal["llm", "tool", "guard"] = "llm"
    name: str = ""                          # e.g. search_docs, grounding_check
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    status: Literal["ok", "error", "timeout", "skipped"] = "ok"
    summary: str = ""                       # short, PII-masked, plain English
    input: str = ""                         # what the step received (PII-masked, clipped to 4000 chars)
    output: str = ""                        # what it returned (PII-masked, clipped to 4000 chars)


class AskResponse(BaseModel):
    request_id: str
    query_id: str | None = None
    question: str
    status: Literal["ok", "abstained", "blocked", "error"]
    route: RouteDecision | None = None
    answer: str = ""
    steps: list[str] = []
    safety_notes: list[str] = []
    citations: list[Citation] = []
    confidence: Literal["high", "medium", "low"] = "low"
    abstain_reason: str = ""
    emergency: bool = False
    emergency_message: str = ""
    guardrail_flags: list[str] = []
    equipment_tag: str | None = None
    latency_ms: float = 0.0
    stage_ms: dict[str, float] = {}
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    used_fallback: bool = False
    mode: Literal["pipeline", "multi_agent"] = "pipeline"   # which engine answered
    agent_trace: list[AgentStep] = []                       # empty in pipeline mode


class DocumentIn(BaseModel):
    doc_key: str = Field(min_length=3, max_length=40, pattern=r"^[A-Z0-9][A-Z0-9\-]+$")
    title: str = Field(min_length=3, max_length=150)
    category: Category
    owner: str = Field(min_length=2, max_length=80)
    equipment: str = Field(default="", max_length=300)
    content: str = Field(min_length=20, max_length=20000)


class FeedbackIn(BaseModel):
    query_id: str
    rating: Literal[1, -1]
    comment: str = Field(default="", max_length=500)
    user: str = Field(default="demo.supervisor", max_length=60)


class ReviewDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewer: str = Field(min_length=1, max_length=60)
    note: str = Field(default="", max_length=500)
