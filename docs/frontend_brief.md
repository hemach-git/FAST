# Frontend session brief: FAST (Floor Assistant for Supervisors and Technicians)

You are building the **Streamlit frontend only** for FAST. Floor supervisors ask plain-English questions. The backend routes each question to the right documentation (Safety procedures, Maintenance manuals, Quality standards) and returns a grounded, cited answer. Plant managers update documents (draft, then approve), review flagged answers, and look at query analytics to spot recurring floor issues.

**Folder:** `C:\Users\Chepuri\AI-Products\FAST`. **Branch:** same folder, `main`. Touch only frontend files and **do not commit**; the main session commits.

**You own:** `app/main.py`, `app/pages/*`, `app/ui/*`, `.streamlit/config.toml`.
**Do not edit:** `app/services/*`, `app/models/*`, `app/data/*`, `app/observability/*`, `app/config.py`, `config.yaml`, `tests/*`, `evals/*`.

The backend is **already implemented and working** (mock LLM, synthetic data). Call it; don't reimplement it. On first call it creates `data/fast.db` and seeds synthetic data automatically.

Put this at the top of `app/main.py` so `import app.*` works when run via `streamlit run app/main.py`:
```python
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
```
Pages under `app/pages/` need the same line (use `parent.parent.parent`).

## Interface contract

```python
from app.services import workflow, docs_admin, audit, analytics
from app.observability.logging import recent_logs
from app.config import get_config, model_label, config_hash

workflow.ensure_ready() -> None                       # call once at app start
workflow.ask(question: str, role: "supervisor"|"manager", user: str) -> AskResponse
workflow.health() -> dict  # status, fk_violations, llm_provider, model, config_hash, published_docs, draft_docs, chunks, queries, audit_rows, db_ms

# AskResponse (pydantic, app/models/schemas.py):
#   request_id, query_id, question (PII-masked), status: "ok"|"abstained"|"blocked"|"error"
#   route: RouteDecision | None -> categories[list, primary first], primary, confidence 0-1,
#          safety_flag (hazard -> safety source forced in), broad_search, method, scores{cat: float}, reason (plain English)
#   answer: str, steps: list[str], safety_notes: list[str]
#   citations: list[Citation] -> chunk_id, doc_id, doc_key, doc_title, version, category, section, snippet, score
#   confidence: "high"|"medium"|"low", abstain_reason: str
#   emergency: bool, emergency_message: str          # show as a RED banner ABOVE everything
#   guardrail_flags: list[str], equipment_tag: str|None
#   latency_ms, stage_ms{guardrails,route,retrieve,generate}, tokens_in, tokens_out, model, used_fallback

docs_admin.list_documents(role="supervisor"|"manager", status=None|"draft"|"published"|"rejected"|"retired", category=None) -> list[dict]
    # keys: doc_id, doc_key, title, category, version, status, owner, equipment, created_by, created_at,
    #       reviewed_by, reviewed_at, review_note, guardrail_flags (JSON str), is_synthetic, sections
    # supervisors only ever get published docs
docs_admin.get_document(doc_id, role) -> dict | None  # + content, sections[{chunk_id, section_no, section, text}]
docs_admin.get_chunk(chunk_id) -> dict | None          # open a citation: text, section, doc_key, title, version, status
docs_admin.submit_draft({doc_key, title, category, owner, equipment, content(markdown with '## ' sections)}, actor) -> doc_id
    # raises ValueError (show st.error). New doc_key -> v1; existing -> next version. Never auto-publishes.
docs_admin.review_document(doc_id, {"decision": "approved"|"rejected", "reviewer": str, "note": str}) -> {doc_id, status, retired[]}
docs_admin.retire_document(doc_id, actor, note) -> dict

audit.submit_feedback({"query_id", "rating": 1|-1, "comment", "user"}) -> feedback_id   # -1 -> manager review queue
audit.list_review_queue(status="pending"|"approved"|"rejected"|"all") -> list[dict]
    # feedback_id, created_at, user_name, comment, review_status, question, primary_category, routed_categories, answer_status, equipment_tag, request_id, query_id
audit.resolve_feedback(feedback_id, {"decision": "approved"|"rejected", "reviewer", "note"}) -> dict  # approved = flag confirmed
audit.list_audit(limit=200, action=None) -> list[dict]  # ts, actor, role, action, target, decision, model, request_id, details

analytics.queries_df(days=30|None, category=None, equipment=None, include_synthetic=True) -> DataFrame
    # columns incl. created_at, date, question_masked, categories, primary_category, status, equipment_tag,
    #   latency_ms, safety_flag, data_source ("synthetic"|"live demo"), request_id
analytics.kpis(df) -> dict  # total_queries, answer_rate_pct, abstain_rate_pct, blocked, safety_routed_pct,
    #   p95_latency_ms, avg_latency_ms, helpful_pct, feedback_count, open_reviews, top_issue, synthetic_share_pct
analytics.category_breakdown(df) / daily_trend(df) / top_issues(df) / doc_gaps(df) / top_cited_docs(days) -> DataFrame
analytics.insights(kpis) -> list[str]   # plain-English takeaways
analytics.export_csv(df) -> bytes       # st.download_button

recent_logs(n=100) -> list[dict]        # JSON log events: ts, event, request_id, status, latency_ms, stage_ms, tokens_in/out, model
# Eval results (may not exist yet): evals/results/latest.json -> {"summary": {"pass_rate", "passed", "total", ...}, "cases": [...]}
```

## Pages

> **Update:** sign-in replaced the role selector. The role comes from the signed-in account (`common.role()`); the selector only appears when `auth.enabled: false`. Added pages: *Traces & logs* and *Users & access* (administrators), and *Advise changes* on Documents.

Sidebar: FAST logo/title, signed-in name and role with a Sign out button (the old **role selector** and user name only in development mode). Manager-only pages are hidden or disabled for supervisors. Show a persistent **"SYNTHETIC DATA - demo only"** badge and `model_label()` (it says `mock-extractive-v1` for now).

1. **Ask (home, supervisor main screen).** Big question box plus 6 one-click demo questions:
   - "How do I clear a jam on Conveyor C3?"
   - "CNC-04 spindle overheating alarm 2041"
   - "What is the torque spec for M10 bolts?"
   - "What PPE do I need in the paint booth?"
   - "There is a chemical spill near Paint Booth 1"
   - "What is the cafeteria menu today?" (shows the abstain state)

   Result layout:
   - Emergency red banner, if any.
   - **Safety notes** in a yellow warning box at the top.
   - **Routing chips**: one per category with a color (safety red, maintenance blue, quality green), confidence %, the `reason` text, and a "safety added" indicator.
   - Answer, then numbered steps.
   - **Sources**: expandable citations showing doc_key vX, section and snippet, with a button to open the full section via `get_chunk`.
   - Confidence badge.
   - Thumbs up / "Flag as wrong" with a comment, which calls `submit_feedback` and shows a success toast.
   - States: `abstained` → neutral info box with `abstain_reason`; `blocked` → warning; `error` → error box.
   - **"How FAST answered" expander (new, v2):** if `resp.agent_trace` is non-empty, show a compact table (step, agent, model, tokens, ms, status) and a badge for `resp.mode` ("AI agents" or "Rule-based"). If `resp.used_fallback`, show a small note, "Answered by the backup method". Hide this for supervisors on small screens if it crowds the answer.
   - **Clarifying question:** when `status == "abstained"` and `abstain_reason` ends with a question mark, show it as a prompt with a text box to reply, and re-ask with the combined text.
   - Small footer: request_id, latency, model.
   - Spinner while running. Keep a session history of the last 5 questions.
2. **Documents.**
   - Supervisors: a browsable list of published documents by category, with a viewer.
   - Managers also get:
     - Status filter.
     - A "New / update document" form: doc_key, title, category, owner, equipment, markdown content (pre-fill an example).
     - A **Pending approvals** section: diff-like side-by-side view of the draft vs the current published version, guardrail_flags warning, Approve/Reject with a note.
   - A seeded draft `QC-TORQ-01-v2` is already pending, so the demo works immediately.
3. **Review queue (manager).** Flagged answers (pending, with a tab for resolved), each with the question, category, equipment, comment and request_id, plus Confirm issue / Dismiss buttons with a note. Show an audit trail table below (filterable by action).
4. **Analytics (manager).**
   - Filters: days (7/30/90), category, equipment, include synthetic toggle.
   - 5 KPI cards: Questions, Answer rate, Safety-routed %, p95 latency, Helpful %.
   - Plain-English insights list.
   - Charts (altair):
     - Daily trend stacked by category.
     - Category breakdown.
     - **Top recurring issues** bar (equipment × category).
     - Documentation gaps table.
     - Most-cited documents.
   - Drill-down: pick equipment → table of its questions.
   - CSV download button.
   - Label it "Synthetic data, demo only" and show synthetic_share_pct.
5. **Diagnostics (manager).**
   - `health()` cards.
   - The config snapshot (`get_config()`, `config_hash()`).
   - Recent requests table from `recent_logs()`: ts, request_id, event, status, latency_ms, tokens and stage timings.
   - Error count.
   - Eval summary from `evals/results/latest.json` if it exists (else "Run `python -m evals.run`").

## Quality bar

- Professional, enterprise industrial look:
  - Sidebar navigation (`st.navigation`/`st.Page` or `pages/`).
  - Consistent theme in `.streamlit/config.toml`: dark slate primary, safety orange accent.
  - Cards via `st.container(border=True)` and `st.metric`.
- Clear spinners, error/success states and empty states.
- Plain English for supervisors, with no jargon like "BM25" on the Ask page.
- Must be usable on a tablet width.

## Rules

- Use port **8501**. The main session checked it's free. Run `streamlit run app/main.py --server.port 8501 --server.headless true` and verify **each page** loads with no exceptions, including asking each demo question and doing one approve and one feedback.
- Report what you verified. Never hardcode keys. Don't commit.
