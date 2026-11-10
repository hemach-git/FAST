# FAST: Scope Document (v2: AI-powered, supervised multi-agent)

**Project:** FAST (Floor Assistant for Supervisors and Technicians)
**Client problem:** A plant operator wants floor supervisors to get answers from the right documentation (safety procedures, maintenance manuals or quality control standards), routed automatically and answered accurately.
**Engagement:** 2.5-hour prototype, local deployment only, **synthetic data**.

> **Status (2026-11-10).** The rule-based version (v1) and the Streamlit UI are built; v1 is tested (173 automated tests; a rule-based eval gate runs in CI on a mock model, which checks wiring only). The AI agent layer (v2) is built and **formally evaluated live: 63/67 on the 67-case golden set** with hybrid search and the faithfulness critic (synthetic data; earlier BM25-only run 44/45 on 45 cases).

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
| Understand intent, pick sources, extract equipment, rewrite search queries | **Router agent (LLM)** | Built; live eval 63/67 overall |
| Research each source with search tools, return cited findings | **Safety / Maintenance / Quality agents (LLM + tools)** | Built; live eval 63/67 overall |
| Merge findings into one structured, cited answer | **Synthesizer agent (LLM)** | Built; live eval 63/67 overall |
| Check each claim in the answer against the document text | **Critic agent (LLM)** | Built; removes unsupported steps, abstains on an unsupported answer |
| Safety override, published-only filter, grounding check, step and token caps, approvals, audit | **Code (deliberately not AI)** | Built |
| Rule-based router and extractive answer (fallback) | Code | Built, tested |
| Find sections by meaning, not only keywords | Hybrid search (local embeddings + BM25), code | Built; default |
| Insights agent, LLM judge | LLM | Not built |

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

| Criterion | Target | v1 result (measured, mock LLM) | v2 result (live `gpt-4o-mini`, 67 cases, hybrid + critic) |
|---|---|---|---|
| Routing accuracy | ≥ 90% | 100% (20/20) | **95.5%** |
| Correct document cited | ≥ 85% | 100% | **94.3%** |
| Abstains when not in docs | 100% | 100% (5/5) | **100%** |
| Safety procedure included for hazards | 100% | 100% (5/5) | **81.8%** (G02 and H02 miss) |
| Draft-document leaks | 0 | 0 | **0** |
| Paraphrased questions routed correctly | ≥ 80% | n/a (rules can't) | **100%** (10 of 10) |
| p95 latency | < 8 s agent mode (assumption to confirm; `targets` in config.yaml) | ~1 ms (mock pipeline) | **25.1 s (not met)**, p50 7.6 s (critic adds ~5 s); before the critic 8.7-10.9 s |
| Automated tests | all pass | 173/173 | 173/173 |

> The v1 result checks **pipeline wiring**, not AI quality; the mock is extractive and was written alongside the golden set. The v2 column is the latest live run (2026-11-10) on synthetic data. Failures: H02 (the critic removed invented roller-replacement steps and the answer abstained; expectation under review), G02 (no lockout citation), G06 and G18 (misrouted, then abstained). Earlier run (BM25 only, no critic, 45 cases): 44/45, the failure was A10 (router asked a clarifying question).

## 7. Roadmap

- **v1 (built):** local, single plant, rule-based pipeline, mock LLM, citations and approvals from day one.
- **v2 (built):** LLM agents for routing, research and answers; agent trace; fallback to v1; Streamlit UI. Live eval 44/45 (45 cases); latency above goal.
- **v2.1 (built):** faithfulness critic, hybrid search (BM25 + embeddings), CI eval gate, 67-case golden set. Live eval 63/67; latency further above goal.
- **v2.2 (built):** document review dates: overdue sources are warned in answers, managers confirm documents are still current.
- **Next:** real documents from one plant line, sign-in, faster answers, confirm the review intervals with EHS and Quality.
- **Later:** PDF ingestion, manager insights agent, CMMS integration, multi-plant rollout, online evaluation from feedback.
