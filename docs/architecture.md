# FAST: Architecture (v2: AI-powered, supervised multi-agent)

FAST routes floor supervisors' questions to the right approved documentation using a small team of LLM agents, and returns grounded, cited answers. All data is **synthetic**.

> **Build status.** The deterministic control plane, the rule-based pipeline (used as fallback), guardrails, audit, analytics and the UI contract are **built and tested**. The LLM agents (router, specialists, synthesizer) are **built and evaluated live: 44/45 on the 45-case golden set**.

## 1. Solution architecture

```mermaid
%% name: architecture
flowchart TD
  S[Floor Supervisor] --> UI[Streamlit UI]
  M[Plant Manager] --> UI
  UI --> ORC[Orchestrator: plain code]
  ORC --> GR[Guardrails]
  ORC --> RA[Router Agent]
  RA --> SP[Safety, Maintenance and Quality Agents]
  SP --> TL[search_docs tool, read only]
  TL --> IDX[(Published-only index)]
  SP --> SY[Synthesizer Agent]
  SY --> GC[Grounding check]
  GC --> ORC
  RA -.-> LLM[LLM API]
  SP -.-> LLM
  SY -.-> LLM
  ORC --> DB[(SQLite: queries, audit, agent trace)]
  ORC -.-> FB[Rule-based pipeline fallback]
```

| Component | Type | Responsibility | Business "why" |
|---|---|---|---|
| Orchestrator | Code | Runs the stages in a fixed order, dispatches specialists in parallel, enforces caps and timeouts, falls back on failure | Predictable behaviour; no runaway agent on the floor |
| Guardrails | Code | Length and type checks, injection and safety-bypass blocking, emergency detection, PII masking | Protects people; keeps logs clean |
| **Router agent** | LLM | Understands the question, picks the sources, extracts equipment, flags hazards, rewrites per-source search queries, asks for clarification | Handles messy wording that keywords miss |
| **Specialist agents ×3** | LLM + tools | Each searches only its own manuals (max 3 steps) and returns cited findings or abstains | Domain focus; faster in parallel; fewer cross-source mistakes |
| **Synthesizer agent** | LLM | Merges findings, safety first, notes conflicts and gaps, outputs strict JSON | One clear answer instead of three documents |
| Hazard override | Code | Keyword rules ∪ router flag → Safety specialist always runs | A model slip can never drop the safety step |
| Retriever | Code | BM25 over section chunks, **published documents only**, scoped per category | Supervisors only see approved procedures |
| Grounding check | Code | Every citation must be a retrieved section, else drop it or abstain | Blocks invented sources |
| Rule-based pipeline | Code | v1 router and extractive answer, used when agents fail or `agents.mode: pipeline` | The demo and the floor keep working if the LLM API fails |
| Docs admin, Audit, Analytics | Code | Draft → approve → publish; audit trail; KPIs and recurring issues | Compliance and visibility for managers |
| *Stretch:* Critic, Insights agent, embeddings | LLM | Claim checking, manager summaries and gap clustering, semantic search | Next quality and insight steps |

## 2. Data flow

```mermaid
%% name: data_flow
flowchart TD
  Q[Supervisor question] --> V{Guardrails}
  V -- bad input or injection --> B[Blocked and logged]
  V -- ok --> EM{Emergency?}
  EM -- yes --> BN[Red banner and emergency SOP]
  EM -- no --> RA[Router agent]
  BN --> RA
  RA --> CL{Clear enough?}
  CL -- no --> ASK[Ask one clarifying question]
  CL -- yes --> HZ{Hazard? rules or router}
  HZ -- yes --> ADD[Force Safety specialist in]
  HZ -- no --> SPEC[Specialists run in parallel]
  ADD --> SPEC
  SPEC --> SYN[Synthesizer agent]
  SYN --> GCH{Citations grounded?}
  GCH -- no --> AB[Abstain: not in docs]
  GCH -- yes --> OUT[Answer, steps, safety notes, sources]
  SPEC -. agent error or timeout .-> FBK[Fallback: rule-based pipeline]
  FBK --> OUT
  OUT --> ST[(Store query, trace, audit)]
  AB --> ST
  B --> ST
  ST --> FDB[Feedback: flag wrong answer]
  FDB --> HR[Manager review queue]
  ST --> KPI[Analytics]
```

## 3. Request sequence (one question, end to end)

```mermaid
%% name: sequence
sequenceDiagram
  actor S as Supervisor
  participant UI as Streamlit
  participant O as Orchestrator
  participant R as Router Agent
  participant M as Maintenance Agent
  participant F as Safety Agent
  participant Y as Synthesizer
  participant DB as SQLite
  S->>UI: The belt on C3 is stuck
  UI->>O: ask(question, role, user)
  O->>O: guardrails and PII mask
  O->>R: plan this question
  R-->>O: maintenance and safety, hazard, queries
  par Specialists in parallel
    O->>M: research with search tool
    M-->>O: cited findings
  and
    O->>F: research with search tool
    F-->>O: cited findings
  end
  O->>Y: merge findings, safety first
  Y-->>O: JSON answer with citation ids
  O->>O: grounding check
  O->>DB: query, citations, agent trace, audit
  O-->>UI: AskResponse
  UI-->>S: Safety note, steps, sources
```

## 4. File structure (status in brackets)

```
FAST/
├── app/
│   ├── main.py, pages/, ui/          Streamlit frontend [built]: Ask, Documents (approve / advise / reject),
│   │                                 Review queue, Analytics, Diagnostics, Traces & logs (admin), Users & access (admin)
│   ├── agents/                       [built]
│   │   ├── base.py                   agent runner: tool loop, caps, trace
│   │   ├── prompts.py                prompts and PROMPT_VERSION
│   │   ├── router_agent.py           RouterPlan
│   │   ├── specialists.py            safety / maintenance / quality
│   │   ├── synthesizer.py            merge, structured answer
│   │   └── orchestrator.py           stages, parallelism, fallback
│   ├── services/                     [built]
│   │   ├── workflow.py               ask(): picks agents.mode
│   │   ├── llm.py                    mock / openai / anthropic adapter
│   │   ├── router.py, retriever.py, guardrails.py   (rule-based fallback, retrieval, guards)
│   │   ├── docs_admin.py, audit.py, analytics.py
│   ├── observability.py          trace list/detail, timeline, metrics + drill-down, log search
│   ├── users.py                  access requests, approval, sign-in, lockout
│   ├── pricing.py                token price table (cost estimates)
│   ├── models/schemas.py             [built] Pydantic contract incl. AgentStep
│   ├── data/db.py, seed_synthetic.py, corpus/*.md (18 synthetic docs + 1 draft)
│   └── observability/logging.py
├── evals/ golden.jsonl, run.py, results/
├── tests/                            pytest
├── docs/                             design, architecture, data model, scope, risks, slide
├── scripts/                          render_mermaid.py, make_docx.py, make_pptx.py
└── config.yaml, .env.example, requirements.txt, README.md
```

## 5. Configuration

```yaml
llm:
  provider: openai            # openai | anthropic | mock   (key in .env only)
agents:
  mode: multi_agent           # multi_agent | pipeline (rule-based fallback)
  timeout_s: 25
  token_budget: 12000
  router:       { model: gpt-4o-mini, max_tokens: 300 }
  specialist:   { model: gpt-4o-mini, max_tokens: 700, max_steps: 3 }
  synthesizer:  { model: gpt-4o-mini, max_tokens: 900 }
```

## 6. Observability and access control

- **Trace write path.** `workflow.ask()` collects the `AgentStep` list (including the pre-fetched search and, on failure, the partial steps plus a `fallback_to_pipeline` guard). `_finish()` writes the query row, its citations, every step (`agent_steps`, with masked input/output and cost) and the audit row in **one transaction**.
- **Trace read path.** `services/observability.py` builds the trace list, trace detail, waterfall, aggregates and drill-down from those tables. The *Traces & logs* page is a thin view over it. Only administrators can open it.
- **Access.** `ui/common.init()` is the gate on every page: no signed-in account means the sign-in / request-access form and nothing else. The account is re-read from the database on each page run, so disabling a user or changing a role takes effect on their next click. Admin-only pages check the role again (`require_admin`).
- **Account lifecycle.** request (pending) → administrator approves with a role or denies → sign-in. Lockout after repeated failures. All steps are written to `audit_log`.

