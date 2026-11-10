"""Orchestrator: plain code that owns stage order, hazard override, parallelism, caps and fallback.

Agents propose; code decides. Any AgentError / timeout / budget breach raises to the caller, which falls back
to the rule-based pipeline.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from pydantic import BaseModel, Field

from app.agents import router_agent, specialists, synthesizer
from app.agents.base import AgentError
from app.config import get_config
from app.models.schemas import CATEGORIES, AgentStep, LLMAnswer, RouteDecision
from app.services import retriever
from app.services import router as rules_router


PHYSICAL = re.compile(r"\b(stuck|jam\w*|swap|seiz\w*|replac\w*|repair\w*|fix\w*|clear\w*|remov\w*|unclog\w*|adjust\w*|"
                      r"clean\w*|enter\w*|reach\w*|restart\w*|reset|die change|roller|belt)\b", re.I)


class OrchestratorResult(BaseModel):
    decision: RouteDecision
    answer: LLMAnswer
    hits: list[dict] = Field(default_factory=list)
    trace: list[AgentStep] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    equipment: str | None = None
    clarify: str | None = None


def _finish_trace(trace: list[AgentStep]) -> tuple[int, int]:
    for i, s in enumerate(trace, 1):
        s.seq = i
    return sum(s.tokens_in for s in trace), sum(s.tokens_out for s in trace)


def _check_budget(trace: list[AgentStep], budget: int) -> None:
    used = sum(s.tokens_in + s.tokens_out for s in trace)
    if used > budget:
        raise AgentError(f"token budget exceeded ({used} > {budget})")


def answer(question: str, emergency: bool = False) -> OrchestratorResult:
    """Run the agents. On any failure the exception carries `.trace` (the partial trace) for the caller."""
    trace: list[AgentStep] = []
    try:
        return _answer(question, emergency, trace)
    except Exception as e:
        e.trace = trace + [s for s in getattr(e, "steps", []) if s not in trace]   # type: ignore[attr-defined]
        raise


def _answer(question: str, emergency: bool, trace: list[AgentStep]) -> OrchestratorResult:
    cfg = get_config()
    acfg = cfg["agents"]

    # 1. Router agent
    plan, steps = router_agent.plan(question)
    trace += steps
    _check_budget(trace, acfg["token_budget"])

    # 2. Hazard override (CODE): keyword rules OR router flag OR emergency -> Safety always runs
    hazard = rules_router.hazard_flag(question) or plan.hazard or emergency
    cats = list(plan.categories)
    broad = False
    if not cats and plan.confidence > 0:
        cats, broad = list(CATEGORIES), True
    if hazard and "safety" not in cats:
        cats.append("safety")
        trace.append(AgentStep(agent="orchestrator", kind="guard", name="hazard_override",
                               summary="Safety specialist added (hazard detected)"))
    if emergency and "safety" in cats:
        cats.remove("safety")
        cats.insert(0, "safety")

    equipment = plan.equipment if plan.equipment in cfg["equipment"] else None
    clarify_needed = bool(plan.clarify) and plan.confidence < acfg["min_clarify_confidence"] and not hazard

    def _decision(cs: list[str]) -> RouteDecision:
        primary = cs[0] if cs else "maintenance"
        return RouteDecision(categories=cs or [primary], primary=primary, confidence=plan.confidence,
                             safety_flag=hazard, broad_search=broad, method="agent:router",
                             scores={c: plan.confidence for c in cs}, reason=plan.reason or "Routed by the router agent.")

    if clarify_needed:
        tin, tout = _finish_trace(trace)
        return OrchestratorResult(decision=_decision(cats), answer=LLMAnswer(answer="", abstain=True),
                                  trace=trace, tokens_in=tin, tokens_out=tout, equipment=equipment,
                                  clarify=plan.clarify)
    if not cats:   # router says: not a plant-operations question
        tin, tout = _finish_trace(trace)
        return OrchestratorResult(decision=_decision(list(CATEGORIES)), answer=LLMAnswer(answer="", abstain=True),
                                  trace=trace, tokens_in=tin, tokens_out=tout, equipment=equipment)

    # 3. Specialists in parallel (each: own category only, published only, max steps)
    def run(cat: str):
        q = plan.queries.get(cat) or question
        if emergency and cat == "safety":
            q += " emergency procedure"
        return specialists.research(cat, question, q)

    found: list[specialists.Findings] = []
    first_error: Exception | None = None
    with ThreadPoolExecutor(max_workers=len(cats)) as pool:
        futures = {c: pool.submit(run, c) for c in cats}
        for c, fut in futures.items():
            try:
                f, st = fut.result(timeout=acfg["timeout_s"])
            except FutureTimeout as e:
                trace.append(AgentStep(agent=c, kind="llm", status="timeout", summary="specialist timed out"))
                if first_error is None:
                    first_error = AgentError(f"{c} specialist timed out")
                    first_error.__cause__ = e
                continue
            except AgentError as e:           # keep the partial trace of the failed specialist, then fail over
                trace += e.steps
                first_error = first_error or e
                continue
            trace += st
            found.append(f)
    if first_error is not None:
        raise first_error
    _check_budget(trace, acfg["token_budget"])

    # 3b. Safety evidence guarantee (CODE): a hazard task must carry a cited safety section even if the safety
    #     specialist returned nothing. Evidence is a real, published section, so it is grounded by construction.
    if hazard:
        sf = next((f for f in found if f.category == "safety"), None)
        if sf is None or not sf.findings:
            qs = (plan.queries.get("safety") or "") + " " + question
            top = retriever.search(qs, ["safety"], top_k=1)
            if top:
                h = top[0]
                warn = [ln.replace("WARNING:", "").strip() for ln in h["text"].splitlines() if ln.startswith("WARNING")]
                claim = warn[0] if warn else h["text"].splitlines()[0][:300]
                added = specialists.Findings(category="safety", abstain=False, chunks=[h],
                                             findings=[specialists.Finding(claim=claim, citation_ids=[h["chunk_id"]])])
                found = [f for f in found if f.category != "safety"] + [added]
                trace.append(AgentStep(agent="orchestrator", kind="guard", name="safety_evidence_added",
                                       summary=f"Added {h['doc_key']} - {h['section']} (safety specialist found nothing)"))

    # 3c. LOTO guarantee (CODE): physical work on machinery must carry the lockout/tagout procedure.
    if hazard and not emergency and PHYSICAL.search(question):
        sf = next((f for f in found if f.category == "safety"), None)
        cited = {c for f in (sf.findings if sf else []) for c in f.citation_ids}
        if not any(c.startswith("SOP-LOTO") for c in cited):
            hits = [h for h in retriever.search("lockout tagout isolate energy " + question, ["safety"], top_k=8)
                    if h["doc_key"].startswith("SOP-LOTO")]
            if hits:
                h = hits[0]
                claim = h["text"].splitlines()[0][:300]
                fnd = specialists.Finding(claim=claim, citation_ids=[h["chunk_id"]])
                if sf is None:
                    sf = specialists.Findings(category="safety", chunks=[h], findings=[fnd])
                    found.append(sf)
                else:
                    sf.abstain = False
                    sf.chunks.append(h)
                    sf.findings.append(fnd)
                trace.append(AgentStep(agent="orchestrator", kind="guard", name="loto_added",
                                       summary=f"Added {h['doc_key']} - {h['section']} (physical work on machinery)"))

    # 3d. Maintenance evidence guarantee (CODE): a hazard task routed to maintenance must not lose its repair steps.
    if hazard and "maintenance" in cats:
        mf = next((f for f in found if f.category == "maintenance"), None)
        if mf is None or not mf.findings:
            top = retriever.search((plan.queries.get("maintenance") or "") + " " + question, ["maintenance"], top_k=1)
            if top and top[0]["coverage"] >= 0.3:
                h = top[0]
                added = specialists.Findings(category="maintenance", chunks=[h], findings=[
                    specialists.Finding(claim=h["text"].splitlines()[0][:300], citation_ids=[h["chunk_id"]])])
                found = [f for f in found if f.category != "maintenance"] + [added]
                trace.append(AgentStep(agent="orchestrator", kind="guard", name="maintenance_evidence_added",
                                       summary=f"Added {h['doc_key']} - {h['section']} (specialist found nothing)"))

    # 4. Synthesizer (skipped when no specialist found anything: abstain)
    useful = [f for f in found if f.findings]
    if not useful:
        tin, tout = _finish_trace(trace)
        return OrchestratorResult(decision=_decision(cats), answer=LLMAnswer(answer="", abstain=True),
                                  trace=trace, tokens_in=tin, tokens_out=tout, equipment=equipment)
    ans, st, hits = synthesizer.synthesize(question, plan, useful, hazard)
    trace += st
    _check_budget(trace, acfg["token_budget"])
    tin, tout = _finish_trace(trace)
    return OrchestratorResult(decision=_decision(cats), answer=ans, hits=hits, trace=trace,
                              tokens_in=tin, tokens_out=tout, equipment=equipment)
