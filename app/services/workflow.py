"""Core use case: supervisor asks -> guardrails -> route -> retrieve -> answer -> ground -> store -> audit.

Fixed single-pass pipeline (no agent loop): predictable latency, no runaway cost.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from pydantic import ValidationError

from app.config import config_hash, get_config, model_label
from app.data import db
from app.models.schemas import AgentStep, AskRequest, AskResponse, Citation, LLMAnswer
from app.observability.logging import log_event, new_request_id
from app.services import audit, guardrails, llm, pricing, retriever, router
from app.agents import enabled as agents_enabled
from app.agents import orchestrator
from app.agents.prompts import PROMPT_VERSION

_ready = False


def ensure_ready() -> None:
    """Create schema and seed synthetic data on first run (idempotent)."""
    global _ready
    if _ready:
        return
    db.init_db()
    if not db.rows("SELECT 1 FROM documents LIMIT 1"):
        from app.data.seed_synthetic import seed
        seed()
    from app.services import users
    creds = users.ensure_seed_users()
    if creds:
        from app.config import db_path
        print(f"[FAST] Demo accounts created. Passwords are in {db_path().parent / 'initial_credentials.txt'}")
    _ready = True


def _equipment_tag(question: str) -> str | None:
    q = question.lower()
    for eq in get_config()["equipment"]:
        variants = {eq.lower(), eq.lower().replace("-", ""), eq.lower().replace("-", " ")}
        key = eq.split()[-1].lower()  # e.g. "c3", "r7", "2"
        if any(v in q for v in variants) or (len(key) >= 2 and re.search(rf"\b{re.escape(key)}\b", q) and not key.isdigit()):
            return eq
    if "press" in q:
        return "Press Line 2"
    if "conveyor" in q:
        return "Conveyor C3"
    if "compressor" in q or "plant air" in q:
        return "Compressor AC-2"
    if "robot" in q:
        return "Robot Cell R7"
    if "paint" in q:
        return "Paint Booth 1"
    if "cnc" in q or "spindle" in q:
        return "CNC-04"
    return None


def _validate_llm(raw: str) -> LLMAnswer:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON object in model output")
    return LLMAnswer.model_validate(json.loads(m.group(0)))


def _abstain_reason(hits: list[dict]) -> str:
    rc = get_config()["retrieval"]
    if not hits:
        return "No matching section was found in the published documentation."
    best = hits[0]
    if best["score"] < rc["abstain_min_score"] or best["coverage"] < rc["min_query_coverage"]:
        return "The documentation does not clearly cover this question."
    return ""


def ask(question: str, role: str = "supervisor", user: str = "demo.supervisor",
        *, is_synthetic: int = 0, created_at: str | None = None) -> AskResponse:
    ensure_ready()
    t0 = time.perf_counter()
    rid = new_request_id()
    stage: dict[str, float] = {}
    req = AskRequest(question=question[:2000], role=role, user=user)
    masked = guardrails.mask_pii(req.question)
    cfg = get_config()
    model = model_label()
    resp = AskResponse(request_id=rid, question=masked, status="ok", model=model)
    usage_in = usage_out = 0

    try:
        # 1. Guardrails
        t = time.perf_counter()
        ok, flags, msg = guardrails.validate_question(req.question)
        resp.guardrail_flags = flags
        resp.emergency = guardrails.detect_emergency(req.question)
        if resp.emergency:
            resp.emergency_message = (f"EMERGENCY: make the area safe and call {cfg['emergency']['contact']} now. "
                                      "Then follow the emergency procedure below.")
        stage["guardrails"] = _ms(t)
        if not ok:
            resp.status, resp.abstain_reason = "blocked", msg
            return _finish(resp, req, t0, stage, is_synthetic, created_at, [], 0, 0)

        resp.equipment_tag = _equipment_tag(req.question)

        # 2-4. Agent path (live LLM) with automatic fallback to the rule-based pipeline
        agent_res = None
        if agents_enabled():
            t = time.perf_counter()
            try:
                agent_res = orchestrator.answer(req.question, emergency=resp.emergency)
            except Exception as e:  # AgentError, timeout, budget, provider error -> rule-based pipeline
                resp.used_fallback = True
                resp.guardrail_flags.append("agent_fallback")
                resp.agent_trace = list(getattr(e, "trace", []))     # keep the partial trace of the failed run
                resp.agent_trace.append(AgentStep(agent="orchestrator", kind="guard", name="fallback_to_pipeline",
                                                  status="error", summary=f"{type(e).__name__}: {str(e)[:120]}"))
                log_event("agent_fallback", rid, error=type(e).__name__, detail=str(e)[:200])
            stage["agents"] = _ms(t)
        if agent_res is not None:
            decision, hits, ans = agent_res.decision, agent_res.hits, agent_res.answer
            resp.mode, resp.agent_trace, resp.route = "multi_agent", agent_res.trace, decision
            usage_in += agent_res.tokens_in
            usage_out += agent_res.tokens_out
            if agent_res.equipment and not resp.equipment_tag:
                resp.equipment_tag = agent_res.equipment
            if agent_res.clarify:
                resp.status, resp.answer = "abstained", "I need a little more detail to find the right procedure."
                resp.abstain_reason = agent_res.clarify
                return _finish(resp, req, t0, stage, is_synthetic, created_at, [], usage_in, usage_out)
        else:
            # 2. Route
            t = time.perf_counter()
            decision, rusage = router.route(req.question)
            if resp.emergency and "safety" not in decision.categories:
                decision.categories.insert(0, "safety")
                decision.primary = "safety"
                decision.safety_flag = True
            resp.route = decision
            usage_in += rusage.get("tokens_in", 0)
            usage_out += rusage.get("tokens_out", 0)
            stage["route"] = _ms(t)

            # 3. Retrieve (published docs only, routed categories only)
            t = time.perf_counter()
            hits = retriever.search(req.question, decision.categories, top_k=cfg["retrieval"]["top_k_per_category"])
            # Keep the primary category's evidence first, then others; cap context size.
            hits.sort(key=lambda h: (h["category"] != decision.primary, -h["score"]))
            hits = hits[: cfg["retrieval"]["max_context_chunks"]]
            stage["retrieve"] = _ms(t)
            primary_hits = [h for h in hits if h["category"] == decision.primary] or hits
            reason = _abstain_reason(sorted(primary_hits, key=lambda h: -h["score"]) if not decision.broad_search
                                     else sorted(hits, key=lambda h: -h["score"]))
            if reason and not resp.emergency:
                resp.status, resp.abstain_reason = "abstained", reason + " Please contact your area lead or the document owner."
                resp.answer = "Not found in the approved documentation."
                return _finish(resp, req, t0, stage, is_synthetic, created_at, [], usage_in, usage_out)
            if resp.emergency:
                # Emergency: ensure the emergency procedure is evidence #1 for the answer.
                em = retriever.search(req.question + " emergency", ["safety"], top_k=2)
                hits = em + [h for h in hits if h["chunk_id"] not in {e["chunk_id"] for e in em}]
                decision.primary = "safety"

            # 4. Generate (structured JSON, validate, retry once, fall back to extractive)
            t = time.perf_counter()
            ans: LLMAnswer | None = None
            for attempt in range(cfg["llm"]["max_retries"] + 1):
                try:
                    raw, usage = llm.generate_answer(req.question, hits, decision.categories if not resp.emergency
                                                     else ["safety"] + [c for c in decision.categories if c != "safety"])
                    usage_in += usage.get("tokens_in", 0)
                    usage_out += usage.get("tokens_out", 0)
                    ans = _validate_llm(raw)
                    break
                except (ValidationError, ValueError, json.JSONDecodeError, KeyError) as e:
                    log_event("llm_output_invalid", rid, attempt=attempt, error=type(e).__name__)
                except Exception as e:  # provider/network error
                    log_event("llm_error", rid, attempt=attempt, error=type(e).__name__)
            if ans is None:
                raw, _ = llm.mock_answer(req.question, hits, decision.categories)
                ans = _validate_llm(raw)
                resp.used_fallback = True
            stage["generate"] = _ms(t)

        # 5. Grounding check: every citation must be a retrieved chunk; else abstain (fail closed)
        by_id = {h["chunk_id"]: h for h in hits}
        cited = [cid for cid in dict.fromkeys(ans.citation_ids) if cid in by_id]
        dropped = [cid for cid in ans.citation_ids if cid not in by_id]
        if dropped:
            resp.guardrail_flags.append("ungrounded_citation_dropped")
        if ans.abstain or not cited:
            resp.status = "abstained"
            resp.answer = "Not found in the approved documentation."
            resp.abstain_reason = "The assistant could not ground an answer in the documents. Please contact your area lead."
            return _finish(resp, req, t0, stage, is_synthetic, created_at, [], usage_in, usage_out)

        # Safety override check: if safety was routed, a safety citation must be present.
        if "safety" in decision.categories and not any(by_id[c]["category"] == "safety" for c in cited):
            safety_hits = [h for h in hits if h["category"] == "safety"]
            if safety_hits:
                cited.append(safety_hits[0]["chunk_id"])
                ans.safety_notes.insert(0, f"Before you start: follow {safety_hits[0]['doc_key']} ({safety_hits[0]['title']}).")

        resp.answer, resp.steps, resp.safety_notes = ans.answer, ans.steps, ans.safety_notes
        resp.confidence = ans.confidence if decision.confidence >= 0.4 else "low"
        resp.citations = [Citation(chunk_id=c, doc_id=by_id[c]["doc_id"], doc_key=by_id[c]["doc_key"],
                                   doc_title=by_id[c]["title"], version=by_id[c]["version"],
                                   category=by_id[c]["category"], section=by_id[c]["section"],
                                   snippet=by_id[c]["text"][:300], score=by_id[c]["score"]) for c in cited]
        return _finish(resp, req, t0, stage, is_synthetic, created_at, resp.citations, usage_in, usage_out)

    except Exception as e:  # never crash the UI; record the error
        resp.status = "error"
        resp.abstain_reason = "Something went wrong. Please try again or use the binder procedure."
        log_event("ask_error", rid, error=type(e).__name__, detail=str(e)[:200])
        return _finish(resp, req, t0, stage, is_synthetic, created_at, [], usage_in, usage_out, error=repr(e)[:300])


def _ms(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 2)


def _finish(resp: AskResponse, req: AskRequest, t0: float, stage: dict, is_synthetic: int,
            created_at: str | None, citations: list, tin: int, tout: int, error: str | None = None) -> AskResponse:
    resp.latency_ms = _ms(t0)
    resp.stage_ms = stage
    resp.tokens_in, resp.tokens_out = tin, tout
    qid = "q-" + uuid.uuid4().hex[:12]
    r = resp.route
    total_cost = round(sum(pricing.cost(s.model, s.tokens_in, s.tokens_out) for s in resp.agent_trace), 6)
    try:
        with db.tx() as c:
            c.execute("""INSERT INTO queries (query_id, request_id, created_at, user_name, role, question_masked,
                         routed_categories, primary_category, route_confidence, safety_flag, broad_search, emergency,
                         status, answer_confidence, equipment_tag, latency_ms, tokens_in, tokens_out, model,
                         guardrail_flags, error, is_synthetic, mode, used_fallback, cost_usd)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (qid, resp.request_id, created_at or db.now_iso(), req.user, req.role, resp.question,
                       json.dumps(r.categories if r else []), r.primary if r else None, r.confidence if r else None,
                       int(bool(r and r.safety_flag)), int(bool(r and r.broad_search)), int(resp.emergency),
                       resp.status, resp.confidence if resp.status == "ok" else None, resp.equipment_tag,
                       resp.latency_ms, tin, tout, resp.model, json.dumps(resp.guardrail_flags), error, is_synthetic,
                       resp.mode, int(resp.used_fallback), total_cost))
            for i, st in enumerate(resp.agent_trace, 1):
                st.seq = i
                c.execute("""INSERT INTO agent_steps (query_id, seq, agent, kind, name, model, tokens_in, tokens_out,
                             latency_ms, status, summary, input_text, output_text, cost_usd)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (qid, i, st.agent, st.kind, st.name, st.model, st.tokens_in, st.tokens_out, st.latency_ms,
                           st.status, st.summary, st.input, st.output,
                           pricing.cost(st.model, st.tokens_in, st.tokens_out)))
            for i, cit in enumerate(citations, 1):
                c.execute("INSERT INTO citations (query_id, chunk_id, rank, score) VALUES (?,?,?,?)",
                          (qid, cit.chunk_id, i, cit.score))
            audit.record(req.user, req.role, "ask", qid, resp.status, resp.model, resp.request_id,
                         {"categories": r.categories if r else [], "citations": [x.chunk_id for x in citations],
                          "config_hash": config_hash(), "flags": resp.guardrail_flags, "mode": resp.mode,
                          "prompt_version": PROMPT_VERSION}, conn=c)
        resp.query_id = qid
    except Exception as e:
        log_event("persist_error", resp.request_id, error=type(e).__name__, detail=str(e)[:200])
    if not is_synthetic:
        log_event("ask", resp.request_id, user_role=req.role, status=resp.status,
                  categories=r.categories if r else [], route_confidence=r.confidence if r else None,
                  latency_ms=resp.latency_ms, stage_ms=stage, tokens_in=tin, tokens_out=tout, model=resp.model,
                  flags=resp.guardrail_flags, question=resp.question[:120], error=error, mode=resp.mode,
                  agent_steps=[x.model_dump(exclude={"input", "output"}) for x in resp.agent_trace])
    return resp


def health() -> dict:
    """For the Diagnostics page."""
    ensure_ready()
    t = time.perf_counter()
    counts = db.rows("""SELECT (SELECT COUNT(*) FROM documents WHERE status='published') published_docs,
                               (SELECT COUNT(*) FROM documents WHERE status='draft') draft_docs,
                               (SELECT COUNT(*) FROM chunks) chunks,
                               (SELECT COUNT(*) FROM queries) queries,
                               (SELECT COUNT(*) FROM audit_log) audit_rows""")[0]
    fk = db.rows("PRAGMA foreign_key_check")
    return {"status": "ok" if not fk else "degraded", "db_ms": _ms(t), "fk_violations": len(fk),
            "llm_provider": get_config()["llm"]["provider"], "model": model_label(),
            "config_hash": config_hash(), **counts}
