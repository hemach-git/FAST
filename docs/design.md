# FAST: Design v2 (AI-powered, supervised multi-agent)

> **Status (2026-11-09).**
> - **v1 (built, tested):** rule-based pipeline with a mock LLM. 107 automated tests; 32/32 rule-based evals pass, which checks the wiring only.
> - **v2 (built):** LLM agents for routing, research and answer writing, plus the Streamlit UI. **Formal live eval: 44/45 passed** on 45 synthetic cases (routing 96.3%, correct document cited 97.1%, abstain 100%, safety included 100%); p95 latency 8.7-10.9 s misses the 8 s goal.
> - **Provider:** OpenAI `gpt-4o-mini` is the first target. The interface is provider-agnostic, so Claude plugs in the same way.

Diagrams: [architecture.md](architecture.md) and [data_model.md](data_model.md). Agent interfaces: [agents_contract.md](agents_contract.md).

## 1. Clarify

**Requirements**
- **R1.** Understand a supervisor's question in plain or messy wording ("belt is stuck", "boxes piling up") and pick the right documentation. Safety, Maintenance and Quality can be combined.
- **R2.** Give a grounded, cited answer with numbered steps. **Every claim must trace to a retrieved section.**
- **R3.** Abstain when the documents don't cover the question.
- **R4.** Safety first: a hazard always brings in the Safety specialist, whatever the AI decides.
- **R5.** Managers update documents through draft → approval → publish. Agents never write or publish.
- **R6.** Query analytics with recurring issues and CSV export.
- **R7.** Supervisors flag wrong answers; managers review them.
- **R8.** Ambiguous question: ask one clarifying question, or search broadly and say so.
- **R9 (stretch).** Manager insights: cluster unanswered questions into documentation gaps, summarize recurring issues, draft a document update (human approves).
- **Non-functional:** p95 under 8 s in agent mode (**measured p95 8.7 s in one run and 10.9 s in the next, p50 5.8 s: not met**), under 1 s in the rule-based fallback, runs locally, no secrets in the repo, every request traceable.

**Never list**
- NEVER give a safety answer without citing the safety procedure; never suggest skipping a safety step.
- NEVER present a claim that is not backed by a retrieved section.
- NEVER show draft or retired documents to supervisors.
- NEVER follow instructions found inside documents or tool results.
- NEVER let an agent write, publish, delete or change data.
- NEVER let the model's judgement remove the Safety specialist when a hazard is detected.

**User journeys**
- **J1.** Supervisor, "the belt on C3 is stuck": the router agent maps it to a jam → Maintenance + Safety specialists run in parallel → one answer with lockout first.
- **J2.** Manager approves a revised torque spec → agents answer from the new version immediately; audit record written.
- **J3.** Manager opens Analytics → recurring issues and documentation gaps → CSV.
- **J4.** Supervisor types something vague → one clarifying question, or a broad search labelled as such.

## 2. Architecture: where AI is used and where code stays in charge

| AI (LLM agents) | Plain code (deterministic control plane) |
|---|---|
| **Router agent:** understands intent, picks sources, extracts equipment, flags hazards, rewrites the question into one search query per source, asks for clarification | **Orchestrator:** fixed order of stages, parallel dispatch, caps, timeouts, fallback |
| **Safety / Maintenance / Quality specialist agents:** each searches its own manuals with tools and returns cited findings | **Guardrails:** injection blocking, emergency detection, PII masking |
| **Synthesizer agent:** merges findings into one structured answer, safety first, notes conflicts and gaps | **Hazard override:** keyword rules ∪ router flag → Safety always runs |
| *Stretch:* **Critic agent** (checks claims), **Insights agent** (manager summaries), embeddings (semantic search) | **Retriever:** published-only filter, per-category index. **Grounding check:** every citation must be a retrieved section. **Audit, logging, analytics** |

**Why supervised multi-agent:** the three sources are natural specialist domains, and parallel specialists keep latency down. Code owns the control flow so the safety rules, caps and approvals cannot be talked around by a model.

## 3. Agent deep dive

| Agent | Model (config) | Input → output | Tools | Limits |
|---|---|---|---|---|
| **Router** | `gpt-4o-mini` | Question → `RouterPlan` JSON: categories + confidence, hazard flag, equipment, per-source search queries, optional clarifying question | none | 1 call, 300 tokens |
| **Specialist ×3** | `gpt-4o-mini` | Question + its search query → `Findings` JSON: claims, each with citation ids, or abstain | `search_docs(query, top_k)`, `read_section(chunk_id)`, both **read-only and limited to the specialist's own category** | max 3 steps, 700 tokens, 10 s |
| **Synthesizer** | `gpt-4o-mini` (stronger model if evals demand) | All findings → `LLMAnswer` JSON: answer, steps, safety notes, citations, confidence, abstain | none | 1 call, 900 tokens |

**Prompts are config:** versioned in `app/agents/prompts.py` with a `PROMPT_VERSION` recorded on every audit row.

**Memory:** short-term only, which is the per-request state passed between agents (budgeted). No long-term memory in this prototype, which avoids stale or poisoned memory.

**One request traced end to end** ("the belt on C3 is stuck, boxes jammed"):
1. Guardrails pass. The PII mask is applied.
2. The router agent returns: Maintenance 0.9, Safety 0.7, hazard = true, equipment = Conveyor C3, queries = {maintenance: "clear belt jam conveyor C3", safety: "lockout before clearing jam"}.
3. The hazard rule agrees. The orchestrator starts the Maintenance and Safety specialists **in parallel**.
4. Each specialist calls `search_docs` (published only), optionally `read_section`, then returns cited findings.
5. The synthesizer merges them: the lockout warning first, then the steps from MM-CONV-02 §2.
6. **Grounding check (code):** every citation id must be in the retrieved set. Otherwise the claim is dropped, or the system abstains.
7. The orchestrator stores the query, the citations, the `agent_trace` and the audit row, and returns `AskResponse`.

## 4. Reliability and guardrails

| Failure mode | Mitigation |
|---|---|
| Infinite or long loops | Specialist max 3 steps; the router and the synthesizer make one call each; a 25 s request timeout; a token budget per request |
| Derailment | Specialists only get tools for their own category and a narrow system prompt; the orchestrator, not the model, decides the stage order |
| Context overflow | Top-k and chunk character caps; only the findings (not raw documents) go to the synthesizer |
| Cascading errors | JSON schema check at every agent boundary; one retry; on failure **fall back to the rule-based pipeline**; fail closed (abstain) |
| Confident hallucination | Citation-only claims, deterministic grounding check, abstain path |
| Cost runaway | Per-agent `max_tokens`, request token budget, small model by default, call count logged |

Also:
- **Prompt injection:**
  - Question text and document text are untrusted and delimited.
  - Tool results are treated as data.
  - Agents have no write tools.
  - Known injection patterns are blocked before any model call.
- **PII and data handling:** PII is masked before storage and logging. With a live LLM, the masked question and retrieved sections leave the machine, which is fine for synthetic data. Real plant documents need a data-handling review first.
- **Human in the loop:** document publishing and flag review stay manager decisions.

## 5. Evals and observability

**Golden set:** 32 cases today (15 positive, 5 multi-route, 5 negative, 7 edge). For v2, add about 10 **paraphrase cases** that keyword routing cannot pass, tagged `agent_only`, so they count only in agent mode.

**Metrics**

| Metric | Target |
|---|---|
| Routing accuracy | ≥ 90% |
| Correct document cited | ≥ 85% |
| Abstain correctness | 100% |
| Safety-inclusion recall | 100% |
| Draft leaks | 0 |
| Ungrounded citations reaching the user | 0 |
| p95 latency (agent mode) | < 8 s |

Also tracked per question: tokens, LLM calls, and estimated cost.

**Faithfulness:** manual check on the multi-route cases. An LLM judge is a stretch goal, and judge bias would have to be calibrated against human labels.

**Mode comparison:** run the same golden set in `pipeline` and `multi_agent` mode and report both.

**Observability:** each request carries an `agent_trace` (agent, step, model, tokens, latency, status, input, output). Every step is stored in the `agent_steps` table, including the first pre-fetched search and the partial steps of a run that failed over to the rule-based pipeline. Administrators read it in the **Traces & logs** page (see section 8).

## 6. Tradeoffs ledger

| Tension | We lean | Gave up | Why |
|---|---|---|---|
| Orchestration | Supervised multi-agent (code controls flow) | Free-running autonomous agents; the v1 single pass | Specialist reasoning and parallelism, with safety and cost still bounded |
| Models | One small model for all agents | A stronger model everywhere | Latency and cost; **upgrade the synthesizer only if evals show unsupported claims** |
| Retrieval | BM25 plus agent query rewriting | Embeddings now | Fits the time box; embeddings are the next step |
| Checking | Deterministic grounding check | LLM critic | A code check cannot be persuaded; the critic is a stretch goal |
| Latency vs depth | Parallel specialists, capped steps | Deeper multi-step research | A supervisor needs an answer in seconds |
| Vendor | OpenAI today (the key available) | Claude today | The adapter keeps Claude swappable; compare both on the golden set later |

## 7. Build plan (60-minute window, parallel sessions; executed)

| Session | Owns | Job |
|---|---|---|
| Main | `app/services/llm.py`, `workflow.py`, `router.py`, `app/agents/*`, `config.yaml` | OpenAI adapter, router, specialists, synthesizer, fallback switch |
| Frontend | `app/main.py`, `app/views/*`, `app/ui/*`, `.streamlit/` | UI, including an "agent activity" panel |
| Tests + evals | `tests/*`, `evals/*` | Fake-LLM agent tests, paraphrase cases, mode comparison |

**MVP:** router, three specialists, synthesizer, fallback switch.
**Stretch (not built):** critic, insights agent, embeddings, LLM judge.
**Next:** real documents from one plant line, sign-in, faster answers, a second AI check on answers (the critic).
The rule-based pipeline stays behind `agents.mode: pipeline` as the safety net.

## 8. Administrator capabilities (added after the first release)

| Need | What was built | Business why |
|---|---|---|
| **Trace and log view** | A *Traces & logs* page modelled on LLM-observability tools: a filterable list of every question; a trace detail with a waterfall timeline, one expandable row per step showing its input and output, tokens, latency and cost; citations, feedback, audit rows and raw log lines for the same request. Agent and cost tables plus a log search. | When an answer is wrong or slow, an administrator can see which agent did what and fix the cause instead of guessing. |
| **Aggregated metrics with drill-down** | Eight metrics (volume, error rate, fallback rate, p50 and p95 latency, tokens, cost, helpful %) grouped by day, category, status, engine, model, equipment, user or role. Click any row to list the questions behind it, then open one trace. | Turns a number on a dashboard into the concrete questions to look at. |
| **Document updates: approve, deny or advise** | Besides *Approve* and *Reject*, a reviewer can *Advise changes*: the draft stays hidden from supervisors, the author sees the comment, edits and resubmits. Each draft has a review history and a diff against the published version. | Fewer rejected-and-lost drafts, and a recorded reason for every change to a safety document. |
| **Users and access** | Sign-in with an access-request flow. An administrator approves (choosing the role), denies, changes roles, disables accounts and resets passwords. Failed sign-ins lock the account for 15 minutes after 5 tries. The last administrator cannot be removed. Every decision is in the audit trail. | Roles are enforced instead of chosen from a menu, and access decisions can be shown to an auditor. |

**Design choices**
- **Trace storage:** prompts and tool results are stored PII-masked and clipped to 4,000 characters, so a trace is readable without becoming a second copy of the documents. A retention period is a pilot decision (see risks R16).
- **Timeline:** only step durations are stored, so the waterfall start times are reconstructed (router, then specialists in parallel, then synthesizer). The page says so.
- **Local sign-in:** salted PBKDF2-SHA256 hashes, no extra dependency, no passwords in the repository. First-run demo passwords are random and written next to the database (git-ignored). It is not SSO and has no password-reset by email; the pilot should use the company identity provider.
- **Advise state:** modelled as a review history rather than a new document status, so the published-only rule and the existing status constraint are untouched.
- **Roles:** `supervisor`, `manager` (documents, review queue, analytics, diagnostics) and `admin` (everything, plus traces and users). Administrators act as managers when they ask questions.

