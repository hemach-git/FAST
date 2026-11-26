"""Local Ollama provider: config override, no API key, OpenAI-compatible call shape. Offline (fake client)."""
from __future__ import annotations

import types

import pytest

from app import config
from app.services import llm


@pytest.fixture
def ollama_cfg(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)          # must work without an OpenAI key
    for v in ("OLLAMA_MODEL", "OLLAMA_REASONING", "OLLAMA_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(v, raising=False)
    config.get_config.cache_clear()
    yield config.get_config()
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    config.get_config.cache_clear()


class FakeCompletions:
    def __init__(self, sink):
        self.sink = sink

    def create(self, **kw):
        self.sink["call"] = kw
        msg = types.SimpleNamespace(content='{"ok": true}', tool_calls=None)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], model=kw["model"],
                                     usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5))


@pytest.fixture
def fake_openai(monkeypatch):
    sink: dict = {}

    class FakeOpenAI:
        def __init__(self, **kw):
            sink["client"] = kw
            self.chat = types.SimpleNamespace(completions=FakeCompletions(sink))

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return sink


def test_config_points_every_agent_at_the_local_model(ollama_cfg):
    c = ollama_cfg
    assert c["llm"]["provider"] == "ollama" and c["llm"]["model"] == "gpt-oss:20b"
    assert {c["agents"][r]["model"] for r in ("router", "specialist", "synthesizer", "critic")} == {"gpt-oss:20b"}
    assert c["agents"]["timeout_s"] >= 120, "local calls need a longer timeout (model load)"
    assert config.model_label() == "ollama:gpt-oss:20b"


def test_env_overrides_model_and_reasoning(ollama_cfg, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3")
    monkeypatch.setenv("OLLAMA_REASONING", "low")
    config.get_config.cache_clear()
    c = config.get_config()
    assert c["agents"]["router"]["model"] == "llama3" and c["ollama"]["reasoning_effort"] == "low"


def test_agents_are_enabled_for_ollama(ollama_cfg):
    from app.agents import enabled
    assert enabled()


def test_chat_goes_to_local_server_without_a_key(ollama_cfg, fake_openai):
    r = llm.chat([{"role": "user", "content": "hi"}], model="gpt-oss:20b", json_mode=True, max_tokens=300)
    assert fake_openai["client"]["base_url"] == "http://localhost:11434/v1"
    assert fake_openai["client"]["api_key"] == "ollama"
    call = fake_openai["call"]
    assert call["max_tokens"] == 300 + ollama_cfg["ollama"]["extra_max_tokens"], "reasoning headroom"
    assert call["reasoning_effort"] == "medium" and call["response_format"] == {"type": "json_object"}
    assert r.text == '{"ok": true}' and r.tokens_in == 10


def test_reasoning_effort_only_sent_to_gpt_oss(ollama_cfg, fake_openai):
    llm.chat([{"role": "user", "content": "hi"}], model="mistral:latest", max_tokens=100)
    assert "reasoning_effort" not in fake_openai["call"]


def test_local_calls_cost_nothing():
    from app.services import pricing
    assert pricing.cost("gpt-oss:20b", 100_000, 100_000) == 0.0
