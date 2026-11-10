# Tests + evals session brief: FAST v2 agents

You are the **Tests + evals** session for FAST in `C:\Users\Chepuri\AI-Products\FAST`. Do **not commit**; the main session commits.

**You own:** `tests/*`, `evals/*`. **Do not edit** `app/*`, `config.yaml`, `docs/*`.

Read `docs/agents_contract.md` and `docs/design.md` first. The agent code is being written in parallel by the main session, so code against the **contract**. Replace `llm.chat` with a scripted fake and keep unit tests offline.

## Tasks (in order)

1. **Fake-LLM harness** (`tests/fakes.py`): a scripted `chat` function that returns canned `ChatResult`s per agent (match on the system prompt keywords: router, safety, maintenance, quality, synthesizer) and can inject failures (invalid JSON, timeout, ungrounded citation id).
2. **`tests/test_agents.py`** (write now, un-skip as the agent modules land):
   - Router plan for a jam question includes Maintenance and Safety.
   - The hazard override adds Safety even when the fake router omits it.
   - Specialists only receive tools scoped to their own category and never see draft documents.
   - The synthesizer output with a citation id that wasn't retrieved is dropped or abstained.
   - An agent error or timeout falls back to the rule-based pipeline (`used_fallback=True`).
   - Step cap: a fake specialist that keeps calling tools stops at 3 steps.
   - `agent_trace` is populated, with tokens and latency on every step.
   - `agents.mode: pipeline` produces an empty trace.
3. **Paraphrase eval cases:** add about 10 to `evals/golden.jsonl` with ids `A01`..., each with `"modes": ["multi_agent"]`. Examples: "the belt on C3 is stuck and boxes are piling up", "spindle on the mill is running hot", "do I need a respirator when spraying", "bolts feel loose on the assembly line, what torque", "press is losing power". Include expected primary category and expected cited document. Existing cases run in both modes.
4. **`evals/run.py`:** add `--mode pipeline|multi_agent` (default from config), skip cases whose `modes` excludes the current mode, record `agent_trace` summaries, tokens, call counts and estimated cost per case, and write `evals/results/latest_<mode>.json` plus the existing `latest.json`. **Keep the mock-provider warning**, and print a clear banner when running a live model.
5. **Mode comparison:** `python -m evals.run --compare` runs both modes and prints a side-by-side table (routing accuracy, citation hit rate, abstain correctness, safety-inclusion recall, draft leaks, p95 latency, tokens/question).
6. When the main session says the agents are ready, run the live eval and report **real numbers, failures included**. Do not tune the golden set to make a model pass.

## Rules

- Every new test must pass (or be clearly marked as waiting for an agent module).
- Never put keys in tests. The live eval needs `OPENAI_API_KEY` in `.env`.
- Run `python -m pytest -q` and report the result.
