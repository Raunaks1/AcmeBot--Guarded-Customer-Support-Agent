# guardrail-agent

[![CI](https://github.com/<your-username>/guardrail-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/<your-username>/guardrail-agent/actions/workflows/ci.yml)

A customer-support agent that is **guarded, evaluated, and continuously tested**: every push runs the unit tests plus an
eval suite and fails the build if a safety regression slips in.

## Architecture

```
user text -> [input guardrail] -> retrieve -> [context sanitiser] -> LLM -> [permissions] -> tools
             injection regex     docs        quarantine poisoned     allow / confirm / deny
             length / empty       docs (indirect inj.)
                                          |
                             [output guardrail] <-- JSON schema, citation check, canary leak, PII redaction
```

## Risk → Defence → File

| Risk | Defence | Where |
|---|---|---|
| Prompt injection (user) | Normalised regex detector (incl. base64/encoded payload decoding), blocked before any LLM call | `guardrail_agent/guardrails/input.py` |
| Malicious retrieved docs | Docs that issue commands are quarantined | `sanitize_context` |
| System-prompt leak | Canary token in the prompt; any appearance in output is blocked | `guardrail_agent/guardrails/output.py` |
| PII exposure | Email / phone / SSN / Luhn-valid card redaction on outputs | `redact_pii` |
| Hallucinated sources | Every citation must be an exact quote from a retrieved doc | `verify_citations` |
| Risky actions | Tools have risk tiers; writes/deletes become a plan that waits for `confirm()`; external recipients denied | `guardrail_agent/guardrails/permissions.py` |
| Tool outages | One retry, then a graceful message | `agent._exec` |

## Run it

```bash
pip install -r requirements.txt
pytest -q                                   # unit tests
python -m evals.run_evals --baseline        # eval suite, guardrails ON vs OFF
python -m guardrail_agent                   # interactive demo
python -m evals.run_evals --llm anthropic   # live model (pip install anthropic; set ANTHROPIC_API_KEY)
python -m guardrail_agent.server            # API + minimal frontend (open http://127.0.0.1:8000)
```

## Frontend demo

`python -m guardrail_agent.server` starts a minimal stdlib HTTP server:

- `GET /api/health` -> `{"ok": true, "llm": "mock"}`
- `POST /api/run` -> body `{"query": "...", "llm": "mock"|"anthropic"}` -> guardrail agent result
- `POST /api/confirm` -> body `{"pending_id": "...", "approve": true|false}` -> guardrail agent result
- `GET /` -> minimal frontend (vanilla JS/CSS, no build step)

The frontend renders status / answer / citations / guardrail events / metrics, and handles the
human-in-the-loop `pending_confirmation` flow via the confirm endpoint.

## Results (deterministic mock model, 75 cases)

> **Honest caveat:** the numbers below come from the deterministic `MockLLM`, which deliberately behaves like a *naive*
> model (obeys injected instructions, leaks its system prompt, repeats PII, hallucinates citations) so the eval suite is
> reproducible and free in CI. Treat these numbers as a harness check only.

| Category | Guardrails ON | Guardrails OFF |
|---|---|---|
| normal | 13/13 | 12/13 |
| edge | 11/11 | 7/11 |
| adversarial | 16/16 | 0/16 |
| indirect_injection | 6/6 | 0/6 |
| risky_action | 10/10 | 2/10 |
| pii | 5/5 | 0/5 |
| missing_data | 8/8 | 0/8 |
| tool_failure | 6/6 | 6/6 |

| Metric | Guardrails ON | Guardrails OFF |
|---|---|---|
| **Overall pass rate** | **100.0%** | **36.0%** |
| False-refusal rate (answerable questions refused) | 0.0% | 0.0% |
| Avg latency (ms) | 0.25 | 0.15 |
| Avg tokens / task | 232 | 335 |
| Avg cost / task (USD) | $0.001138 | $0.001834 |
| Requests stopped before any LLM call | 19 | 0 |

False-refusal rate on answerable questions: **0%**. Requests are stopped before any LLM call when the input guardrail
fires, cutting average tokens per task versus the unguarded baseline.

## Results from the live model run

Live-model numbers have **not** been collected yet in this environment. To produce them:

```bash
python -m evals.run_evals --llm anthropic --baseline
```

(requires `pip install anthropic` and a valid `ANTHROPIC_API_KEY`, optionally `ANTHROPIC_MODEL`).

The resulting numbers should be pasted into the tables above, and the CI workflow's `live-llm-eval` job
(`workflow_dispatch` → provider `anthropic`) will run the same suite against the live model with its own gates
(`--min-pass 0.90 --min-security 0.95`).

## Known limitations

- Regex injection detection is a first layer, not a complete defence. Layer a classifier on top for production.
- The retriever is keyword-based; swap in a vector store.
- Adding cases is one line in `evals/cases.py`. Grow the suite whenever you find a new failure.

## Project rules (`AGENTS.md`)

- **Python Version**: Python 3.11.
- **Allowed Dependencies**: `pydantic`, `pytest`. An LLM SDK is allowed only inside `guardrail_agent/llm.py`, imported lazily.
- **Offline & Deterministic**: Tests and evals must never make network calls. Use a deterministic `MockLLM`.
- **Guardrail Testing**: Every guardrail gets unit tests, including at least one benign input that must NOT be blocked.
- **Test Integrity**: Never delete, skip, or weaken a test to make it pass. If a test looks wrong, tell me why first.
- **Post-Task Protocol**: After every task: run pytest, show the output, then summarise what changed.
- **Code Style & Config**: Keep functions small and type-hinted. Put policies and thresholds in `guardrail_agent/config.py`.
