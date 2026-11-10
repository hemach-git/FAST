"""Scripted fake LLM for agent tests (no key, no network).

Replaces `app.services.llm.chat`. Each call is matched to an agent by its system prompt
(router / safety / maintenance / quality / synthesizer) and answered with a canned ChatResult.
Failure injection per agent: API error, timeout (sleep), invalid JSON, ungrounded citation ids,
ids wrapped in tags, and an endless tool loop.

Usage (see tests/test_agents.py):
    fake = FakeLLM(); fake.install(monkeypatch)   # also switches agents on (provider -> "openai")
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from app.services import llm

AGENTS = ("router", "safety", "maintenance", "quality", "synthesizer")
FAKE_ID = "FAKE-DOC-v1#s9"           # looks like a real chunk id but was never retrieved


def agent_of(system: str) -> str:
    if "routing agent" in system:
        return "router"
    m = re.search(r"the (\w+) documentation specialist", system)
    if m:
        return m.group(1).lower()
    if "answer-writing agent" in system:
        return "synthesizer"
    raise AssertionError("unknown agent prompt: " + system[:60])


@dataclass
class Call:
    agent: str
    messages: list[dict]
    tools: list[dict] | None
    json_mode: bool


@dataclass
class FakeLLM:
    # Router plan returned as JSON (None -> DEFAULT_PLAN)
    plan: dict | None = None
    # --- failure injection (keys are agent names) ---
    fail: set = field(default_factory=set)                 # raise RuntimeError (API outage)
    timeout: dict = field(default_factory=dict)            # agent -> seconds to sleep before replying
    invalid_json: dict = field(default_factory=dict)       # agent -> number of non-JSON replies before a valid one
    bad_cite: set = field(default_factory=set)             # add FAKE_ID to the citations
    only_bad_cite: set = field(default_factory=set)        # cite ONLY FAKE_ID
    tag_wrap: set = field(default_factory=set)             # wrap ids like <chunk id="X#s1">
    loop: set = field(default_factory=set)                 # always request a tool when tools are offered
    loop_forever: set = field(default_factory=set)         # request a tool even when none are offered
    extra_tool_calls: dict = field(default_factory=dict)   # agent -> list[(tool_name, arguments)] on 1st call
    tokens: tuple = (100, 40)
    calls: list[Call] = field(default_factory=list)

    DEFAULT_PLAN = {"categories": ["maintenance"], "confidence": 0.9, "hazard": False, "equipment": "Conveyor C3",
                    "queries": {"maintenance": "clear belt jam conveyor", "safety": "lockout before clearing jam"},
                    "clarify": None, "reason": "belt jam"}

    # ------------------------------------------------------------------ install
    def install(self, monkeypatch) -> "FakeLLM":
        monkeypatch.setattr(llm, "chat", self)
        monkeypatch.setattr(llm, "provider", lambda: "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)      # belt and braces: no real call possible
        return self

    def calls_for(self, agent: str) -> list[Call]:
        return [c for c in self.calls if c.agent == agent]

    # ------------------------------------------------------------------ chat
    def __call__(self, messages, *, model, tools=None, json_mode=False, max_tokens=700, temperature=0.0):
        agent = agent_of(messages[0]["content"])
        self.calls.append(Call(agent, [dict(m) for m in messages], tools, json_mode))
        if agent in self.timeout:
            time.sleep(self.timeout[agent])
        if agent in self.fail:
            raise RuntimeError("simulated API outage")
        n_prior = len(self.calls_for(agent)) - 1

        def R(text="", tool_calls=None):
            return llm.ChatResult(text=text, tool_calls=tool_calls or [], tokens_in=self.tokens[0],
                                  tokens_out=self.tokens[1], model=model, latency_ms=5.0)

        if self.invalid_json.get(agent, 0) > 0:
            self.invalid_json[agent] -= 1
            return R("Sure! Here is my answer: it depends.")

        if agent == "router":
            return R(json.dumps(self.plan or self.DEFAULT_PLAN))

        if agent in ("safety", "maintenance", "quality"):
            tool_msgs = [m for m in messages if m["role"] == "tool"]
            if agent in self.loop_forever or (agent in self.loop and tools):
                return R(tool_calls=[llm.ToolCall(id=f"t{n_prior}", name="search_docs", arguments={"query": "x"})])
            if tools and agent in self.extra_tool_calls and not tool_msgs:
                return R(tool_calls=[llm.ToolCall(id=f"x{i}", name=n, arguments=a)
                                     for i, (n, a) in enumerate(self.extra_tool_calls[agent])])
            text = messages[1]["content"] + "\n" + "\n".join(m["content"] or "" for m in tool_msgs)
            ids = list(dict.fromkeys(re.findall(r'<doc id="([^"]+)"', text)))
            cites = self._cites(agent, ids[:1])
            f = {"category": agent, "abstain": not ids,
                 "findings": [{"claim": f"{agent} finding", "citation_ids": cites}] if ids else []}
            return R(json.dumps(f))

        # synthesizer
        ids = list(dict.fromkeys(re.findall(r'<doc id="([^"]+)"', messages[1]["content"])))
        a = {"answer": "Stop the conveyor, lock it out, clear the jam.", "steps": ["Lock out", "Clear jam"],
             "citation_ids": self._cites(agent, ids), "safety_notes": ["Apply lockout first."],
             "confidence": "high", "abstain": False}
        return R(json.dumps(a))

    def _cites(self, agent: str, ids: list[str]) -> list[str]:
        if agent in self.only_bad_cite:
            return [FAKE_ID]
        if agent in self.tag_wrap:
            ids = [f'<chunk id="{i}">' for i in ids]
        return ids + ([FAKE_ID] if agent in self.bad_cite else [])
