"""Router agent: understands the question, picks sources, rewrites per-source queries."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.agents import prompts
from app.agents.base import run_agent
from app.config import get_config
from app.models.schemas import CATEGORIES, AgentStep


class RouterPlan(BaseModel):
    categories: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(default=0.0, ge=0, le=1)
    hazard: bool = False
    equipment: str | None = None
    queries: dict[str, str] = Field(default_factory=dict)
    clarify: str | None = None
    reason: str = ""

    @field_validator("categories")
    @classmethod
    def valid_categories(cls, v: list[str]) -> list[str]:
        return [c for c in dict.fromkeys(x.lower() for x in v) if c in CATEGORIES]


def plan(question: str) -> tuple[RouterPlan, list[AgentStep]]:
    cfg = get_config()
    a = cfg["agents"]["router"]
    system = prompts.ROUTER_SYSTEM.format(equipment=", ".join(cfg["equipment"]))
    user = f"<question>\n{question}\n</question>"
    result, steps = run_agent("router", system, user, model=a["model"], max_tokens=a["max_tokens"],
                              max_steps=2, schema=RouterPlan)
    steps[-1].summary = f"sources={result.categories} confidence={result.confidence:.2f} hazard={result.hazard}"
    return result, steps
