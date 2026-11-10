"""FAST v2 agent layer: router, specialists, synthesizer, orchestrator (code-controlled)."""
from app.config import get_config


def enabled() -> bool:
    """Agents run only when configured AND a live LLM provider is selected (mock -> rule-based pipeline)."""
    from app.services import llm

    return get_config().get("agents", {}).get("mode") == "multi_agent" and llm.provider() != "mock"
