"""Faithfulness critic: agent labels claims, code removes unsupported steps / abstains. Offline (FakeLLM)."""
from __future__ import annotations

import pytest

from fakes import FakeLLM

from app.agents import critic
from app.config import get_config
from app.models.schemas import LLMAnswer
from app.services import workflow

JAM = "How do I clear a jam on Conveyor C3?"          # default fake answer: A1, S1, S2, N1 (4 claims)
COOLANT = "What coolant concentration should CNC-04 run at?"


@pytest.fixture
def fake(monkeypatch):
    return FakeLLM().install(monkeypatch)


def guard(r, name="faithfulness_check"):
    return next((s for s in r.agent_trace if s.kind == "guard" and s.name == name), None)


# ---------------------------------------------------------------- end to end (workflow + orchestrator)
def test_all_supported_answer_passes_unchanged(fake):
    r = workflow.ask(JAM)
    assert r.status == "ok" and r.mode == "multi_agent" and not r.used_fallback
    assert r.steps == ["Lock out", "Clear jam"]
    assert fake.calls_for("critic"), "critic must run after the synthesizer"
    g = guard(r)
    assert g and g.status == "ok" and "4/4 claims supported" in g.summary
    assert not {"unfaithful_answer", "unsupported_step_removed"} & set(r.guardrail_flags)
    assert [s.agent for s in r.agent_trace if s.kind == "llm"][-1] == "critic"


def test_critic_sees_claims_and_evidence_text(fake):
    workflow.ask(JAM)
    user = fake.calls_for("critic")[0].messages[1]["content"]
    assert "A1: Stop the conveyor" in user and "S2: Clear jam" in user and "N1: Apply lockout first." in user
    assert '<doc id="' in user, "critic must judge against the evidence text, not just ids"


def test_unsupported_step_is_removed_and_confidence_lowered(fake):
    fake.critic_verdicts = {"S2": "unsupported"}
    r = workflow.ask(JAM)
    assert r.status == "ok" and r.steps == ["Lock out"]
    assert "unsupported_step_removed" in r.guardrail_flags
    assert r.confidence != "high"
    assert "unsupported: S2" in guard(r).summary


def test_partial_claims_are_kept(fake):
    fake.critic_verdicts = {"S1": "partial", "S2": "partial"}
    r = workflow.ask(JAM)
    assert r.status == "ok" and r.steps == ["Lock out", "Clear jam"]
    assert "unsupported_step_removed" not in r.guardrail_flags


def test_unsupported_summary_abstains(fake):
    fake.critic_verdicts = {"A1": "unsupported"}
    r = workflow.ask(JAM)
    assert r.status == "abstained" and not r.citations and not r.steps
    assert "unfaithful_answer" in r.guardrail_flags
    assert "faithfulness check" in r.abstain_reason
    assert guard(r).status == "error"


def test_too_many_unsupported_claims_abstains(fake):
    fake.critic_verdicts = {"S1": "unsupported", "S2": "unsupported"}     # 2/4 = 0.5 > 0.34
    r = workflow.ask(JAM)
    assert r.status == "abstained" and "unfaithful_answer" in r.guardrail_flags


def test_unsupported_safety_note_is_kept_but_flagged(fake):
    fake.critic_verdicts = {"N1": "unsupported"}
    r = workflow.ask(JAM)
    assert r.status == "ok" and "Apply lockout first." in r.safety_notes
    assert "unsupported_safety_note_kept" in r.guardrail_flags


def test_missing_verdicts_are_kept_as_unchecked(fake):
    fake.critic_omit = {"S1", "S2"}
    r = workflow.ask(JAM)
    assert r.status == "ok" and r.steps == ["Lock out", "Clear jam"]
    assert "unchecked: S1, S2" in guard(r).summary


def test_critic_api_error_falls_back_to_pipeline(fake):
    fake.fail = {"critic"}
    r = workflow.ask(JAM)
    assert r.used_fallback and "agent_fallback" in r.guardrail_flags and r.status == "ok"
    assert any(s.name == "fallback_to_pipeline" for s in r.agent_trace)


def test_critic_invalid_json_once_is_corrected(fake):
    fake.invalid_json = {"critic": 1}
    r = workflow.ask(JAM)
    assert r.status == "ok" and not r.used_fallback and len(fake.calls_for("critic")) == 2


def test_critic_skipped_when_disabled(fake, monkeypatch):
    monkeypatch.setitem(get_config()["agents"]["critic"], "enabled", False)
    r = workflow.ask(JAM)
    assert r.status == "ok" and not fake.calls_for("critic") and guard(r) is None


def test_critic_skipped_when_synthesizer_abstains(fake):
    fake.synth_answer = {"answer": "", "steps": [], "safety_notes": [], "confidence": "low", "abstain": True}
    r = workflow.ask(COOLANT)
    assert r.status == "abstained" and not fake.calls_for("critic")


def test_critic_tokens_count_toward_budget(fake, monkeypatch):
    # router 140 + specialist 140+ + synthesizer 140 = ~420 before the critic; critic pushes it over 500
    monkeypatch.setitem(get_config()["agents"], "token_budget", 500)
    fake.plan = {"categories": ["maintenance"], "confidence": 0.9, "hazard": False, "equipment": None,
                 "queries": {"maintenance": "coolant concentration"}, "clarify": None, "reason": "t"}
    r = workflow.ask(COOLANT)
    assert r.used_fallback and fake.calls_for("critic")
    assert "token budget exceeded" in next(s.summary for s in r.agent_trace if s.name == "fallback_to_pipeline")


# ---------------------------------------------------------------- policy unit tests (pure code)
HITS = [{"chunk_id": "MM-A-v1#s1"}, {"chunk_id": "MM-A-v1#s2"}]
ANS = LLMAnswer(answer="Do X.", steps=["one", "two", "three"], citation_ids=["MM-A-v1#s1"],
                safety_notes=["Wear gloves."], confidence="high")


def rep(**verdicts):
    return critic.CriticReport(verdicts=[critic.Verdict(id=k, verdict=v) for k, v in verdicts.items()])


def test_claims_numbering():
    assert list(critic.claims_of(ANS)) == ["A1", "S1", "S2", "S3", "N1"]


def test_apply_ratio_boundary():
    one = critic.apply(ANS, rep(S1="unsupported"), HITS)              # 1/5 = 0.2
    assert not one.unfaithful and one.answer.steps == ["two", "three"] and one.answer.confidence == "medium"
    two = critic.apply(ANS, rep(S1="unsupported", S2="unsupported"), HITS)   # 2/5 = 0.4 > 0.34
    assert two.unfaithful and two.answer.abstain


def test_apply_adds_only_retrieved_support_ids():
    r = critic.CriticReport(verdicts=[
        critic.Verdict(id="S1", verdict="supported", support_ids=['<doc id="MM-A-v1#s2">', "FAKE-DOC-v1#s9"]),
        critic.Verdict(id="S2", verdict="unsupported", support_ids=["MM-A-v1#s2"])])
    out = critic.apply(ANS, r, HITS)
    assert out.answer.citation_ids == ["MM-A-v1#s1", "MM-A-v1#s2"]
    assert "critic_citation_added" in out.flags


def test_apply_verdict_ids_case_insensitive():
    out = critic.apply(ANS, critic.CriticReport(verdicts=[critic.Verdict(id="s3", verdict="unsupported")]), HITS)
    assert out.answer.steps == ["one", "two"]
