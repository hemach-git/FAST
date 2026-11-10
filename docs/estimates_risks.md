# FAST: Estimates and Risk Register (v2)

## 1. Effort estimates

| Phase | Prototype (actual or plan, AI-assisted) | Pilot (estimate) |
|---|---|---|
| Design + plan (v1 and v2) | ~35 min | 1 week |
| v1 backend, guardrails, audit, analytics | ~65 min | 3 weeks |
| v1 tests + evals + docs + slide | ~40 min | 1 week |
| **v2 agents** (adapter, router, specialists, synthesizer, fallback) | **~40 min, parallel sessions** | 2–3 weeks |
| Frontend (parallel session) | ~60 min | 2 weeks |
| v2 tests, paraphrase evals, live eval and tuning | ~20 min | 2 weeks (+ judge calibration) |
| Real document ingestion (PDF, OCR), SSO, deployment | not in prototype | 3–4 weeks |

## 2. Run-cost estimate (illustrative, live LLM)

- **Calls per question:** 1 router + 1–3 specialists (up to 3 steps each) + 1 synthesizer, so roughly **3–6 calls, and about 3–5× the tokens of the v1 design**.
- At ~300 questions/day with a small model (`gpt-4o-mini`), the monthly cost for one plant stays in the **tens of dollars**. Check current provider pricing before quoting it.
- **Levers:**
  - Cache answers to frequent questions.
  - Skip specialists the router scores low.
  - Keep the small model for routing.
  - Cap context to the top 4 sections per source.
  - Use the rule-based pipeline when the router is highly confident (later).

## 3. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | Safety question routed only to Maintenance | Low | **High** | Hazard override in code (keyword rules ∪ router flag) forces the Safety specialist; safety-inclusion recall eval at 100% | EHS + Eng |
| R2 | Confident wrong answer (hallucination) | **Med-High with a small model** | High | Citation-only claims; deterministic grounding check; abstain path; critic agent checks each claim against the evidence text (built); upgrade the synthesizer if evals show unsupported claims | Eng |
| R3 | Outdated or unapproved document used | Med | High | Published-only retrieval; draft → approve; old version retired; draft-leak eval at 0 | Quality Mgr |
| R4 | Prompt injection via question, document or tool result | Med | Med | Patterns blocked before any LLM call; text delimited as untrusted; agents have read-only tools; document scan flags embedded instructions | Eng |
| R5 | Supervisor relies on FAST in an emergency | Low | **High** | Emergency banner first; emergency SOP cited | EHS |
| R6 | Keyword search misses synonyms | Low | Med | Router agent rewrites queries; hybrid search with embeddings (built; retrieval eval top-1 hit 88.6% → 100%); documentation-gaps report shows misses | Eng |
| R7 | **Agent latency above target** (several sequential calls). Quick check: average 7.2 s, slowest 11.3 s vs an 8 s goal (assumption to confirm; configurable in `targets`). Latest live run with the critic: p95 25.1 s | **High (observed)** | Med | Parallel specialists; token caps; small model; fallback to the rule-based pipeline; streaming in the next version; run the critic only for hazard or long answers | Eng |
| R8 | **First live LLM run, little time to tune prompts.** Quick check found 1 vague question refused instead of a follow-up, and extra lockout steps in some answers | **High** | Med | Rule-based fallback behind a config flag; quick check done; formal live eval (separate session) gates any quality claim | Eng |
| R9 | **Non-deterministic evals** (flaky results) | Med | Med | Temperature 0; repeat failing cases; report counts, not only percentages | Eng |
| R10 | **Data leaves the machine** (question and sections sent to the LLM API) | Certain (live mode) | Med | Synthetic data only today; PII masked; real documents need a data-handling and vendor review | Security |
| R11 | **API key or access problems** (key expiry, quota, outage) | Med | Med | Key in `.env` only; fallback pipeline; provider-agnostic adapter | Eng |
| R12 | Agent loops or runaway cost | Low | Med | Max 3 steps per specialist; request timeout and token budget; call counts logged | Eng |
| R13 | PII in questions | Med | Med | Masked before storage and logging | Eng |
| R14 | Low adoption on the floor | Med | High | Answers within seconds, plain English, tablet UI; track usage and helpful % | Plant Mgr |
| R15 | Synthetic data differs from real binders | Certain | Med | Pilot with 2–3 real documents per category; re-run the golden set | Eng + SMEs |
| R16 | **Stored traces hold what supervisors typed and what the model saw** (masked and clipped, but still sensitive) | Certain | Med | PII masking before storage; admin-only access; set a retention period and a deletion job before a pilot; no raw traces in git | Security |
| R17 | **Local sign-in is weaker than company SSO** (no MFA, no email reset, demo passwords on disk) | Certain | Med | Passwords hashed and salted; lockout; random first-run passwords in a git-ignored file; replace with the company identity provider for a pilot | Security + IT |
| R18 | **One administrator is a single point of failure** | Med | Med | The last administrator cannot be disabled or demoted; a second administrator should be approved before a pilot | Plant Mgr |

## 4. Assumptions and limitations

- All documents, queries and feedback are **synthetic** (`is_synthetic=1`).
- Sign-in is a local prototype (salted hashes, lockout, administrator approval). It is not SSO; there is no email password reset and no multi-factor step.
- **Live-LLM quality was measured on 67 synthetic cases (63/67).** It is not a guarantee for real documents; the checks are citation and keyword based, not an AI judge. The rule-based result checks wiring only.
- Small-model answers can vary run to run, and their safety content must be checked.
- Hybrid search (BM25 + embeddings) plus query rewriting, tested on ~70 sections only.
- SQLite runs single-node and locally.
