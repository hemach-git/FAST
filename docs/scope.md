# FAST: Scope Document (v2: AI-powered, supervised multi-agent)

**Project:** FAST (Floor Assistant for Supervisors and Technicians)
**Client problem:** A plant operator wants floor supervisors to get answers from the right documentation (safety procedures, maintenance manuals or quality control standards), routed automatically and answered accurately.
**Engagement:** 2.5-hour prototype, local deployment only, **synthetic data**.

> **Status (2026-11-09).** The rule-based version (v1) and the Streamlit UI are built; v1 is tested (107 automated tests; 32/32 rule-based evals on a mock model, which checks wiring only). The AI agent layer (v2) is built and **formally evaluated live: 44/45 on the 45-case golden set** (synthetic data).

## 1. Business problem

Floor supervisors resolve issues during live production: jams, alarms, out-of-spec parts, PPE questions. Today they look through three separate binders. That causes:
- **Downtime:** minutes lost per issue searching for the right procedure.
- **Safety risk:** a maintenance fix done without the matching lockout procedure.
- **Quality escapes:** using an outdated spec, or a draft spec that was never approved.
- **No visibility:** managers can't see which issues keep recurring on the floor.

## 2. Users

| User | Needs | What FAST gives them |
|---|---|---|
| Floor Supervisor (primary) | Fast, reliable answers during operations, even when the question is loosely worded | One question box; an AI agent team routes it, researches the right manuals and returns a cited answer with steps; safety first; "not found" instead of a guess |
| Plant Operator / Manager (admin) | Keep documentation current; see recurring issues | Draft → approve → publish workflow; review queue for flagged answers; analytics of recurring issues and documentation gaps |

## 3. How AI is used (v2)

| Capability | AI or code | Status |
|---|---|---|
| Understand intent, pick sources, extract equipment, rewrite search queries | **Router agent (LLM)** | Built; live eval 44/45 overall |
| Research each source with search tools, return cited findings | **Safety / Maintenance / Quality agents (LLM + tools)** | Built; live eval 44/45 overall |
| Merge findings into one structured, cited answer | **Synthesizer agent (LLM)** | Built; live eval 44/45 overall |
| Safety override, published-only filter, grounding check, step and token caps, approvals, audit | **Code (deliberately not AI)** | Built |
| Rule-based router and extractive answer (fallback) | Code | Built, tested |
| Critic agent, insights agent, semantic search, LLM judge | LLM | Not built (a second AI check on answers is next) |

## 4. In scope

**Built and tested (v1)**
1. Question routing to Safety, Maintenance or Quality (rules + classifier), with a hazard → Safety override.
2. Grounded answers with steps, safety notes, citations and confidence; abstain when the documents don't cover the question.
3. Guardrails: injection and bypass blocking, emergency banner, PII masking, untrusted document text.
4. Document lifecycle: draft → manager approval → published (previous version retired). Supervisors see published documents only.
5. Feedback, a manager review queue, an audit trail and a per-run config snapshot.
6. Analytics: KPIs, trend, recurring issues, documentation gaps, drill-down and CSV export. Diagnostics page.
7. 107 pytest tests (services, agents with a scripted fake LLM, and every UI page for every role) and a 45-case golden eval set (32 of them also run in rule-based mode) with a repeatable runner.

**Built (v2)**
8. Router agent, three specialist agents with a read-only search tool, and a synthesizer agent, on a provider-agnostic LLM interface (OpenAI `gpt-4o-mini` first).
9. Supervised orchestration: stage order, parallel specialists, step and token caps, timeout, and automatic fallback to the v1 pipeline.
10. Agent trace (per step: agent, model, tokens, latency, status) in the response, the logs and the Diagnostics page.
11. Administrator tools: trace storage for every agent step, a Traces & logs page with drill-down analytics, a document *advise* flow with diff and review history, and user access management with sign-in (see design.md section 8).
12. Mode comparison in the eval runner (`python -m evals.run --compare`). Paraphrase eval cases and the formal live run are owned by the tests-and-evals session; results not yet reported here.

## 5. Out of scope (this prototype)

- Company SSO and password reset by email (a local sign-in with administrator approval is built instead).
- PDF/scanned document ingestion. Multiple languages and voice.
- CMMS or work-order integration. Cloud deployment.
- Long-term agent memory, and any agent that can write or publish.
- Sending real plant documents to an external LLM (needs a data-handling review).

## 6. Assumptions and success criteria

**Assumptions (illustrative):** 1 plant, ~40 supervisors, ~300 questions/day, 18 synthetic documents (~70 sections), Markdown with `## ` headings, English only, tablet or desktop browser.

| Criterion | Target | v1 result (measured, mock LLM) | v2 result (live `gpt-4o-mini`, 45 cases) |
|---|---|---|---|
| Routing accuracy | ≥ 90% | 100% (20/20) | **96.3%** |
| Correct document cited | ≥ 85% | 100% | **97.1%** |
| Abstains when not in docs | 100% | 100% (5/5) | **100%** |
| Safety procedure included for hazards | 100% | 100% (5/5) | **100%** (all 3 hazard-type cases pass) |
| Draft-document leaks | 0 | 0 | **0** |
| Paraphrased questions routed correctly | ≥ 80% | n/a (rules can't) | **90%** (9 of 10) |
| p95 latency | < 8 s agent mode (assumption to confirm; `targets` in config.yaml) | ~1 ms (mock pipeline) | **10.9 s (not met)**; 8.7 s in the previous run; p50 5.8 s |
| Automated tests | all pass | 107/107 | 107/107 |

> The v1 pass rate (32/32) checks **pipeline wiring**, not AI quality; the mock is extractive and was written alongside the golden set. The v2 column is the real live model on synthetic data. Failure: A10 ("how fast am I allowed to drive the lift truck indoors"): the router asked a clarifying question instead of answering. P11 was a test-string mismatch (answer said "60 and 80 microns"), fixed in the test. 2 answers mention lockout without citing the lockout procedure; 1 irrelevant safety note.

## 7. Roadmap

- **v1 (built):** local, single plant, rule-based pipeline, mock LLM, citations and approvals from day one.
- **v2 (built):** LLM agents for routing, research and answers; agent trace; fallback to v1; Streamlit UI. Live eval 44/45; latency above goal.
- **Next:** real documents from one plant line, sign-in, faster answers, a second AI check on answers.
- **Later:** PDF ingestion, hybrid search, manager insights agent, CMMS integration, multi-plant rollout, online evaluation from feedback.
