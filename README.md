# guardrail-agent

![CI](https://github.com/<your-username>/guardrail-agent/actions/workflows/ci.yml/badge.svg)

A customer-support agent that is **guarded, evaluated, and continuously tested**: every push runs 28 unit tests plus a
60-case eval suite and fails the build if a safety regression slips in.

## Architecture

```
user text -> [input guardrail] -> retrieve -> [context sanitiser] -> LLM -> [permissions] -> tools
                 injection regex     docs        quarantine poisoned     allow / confirm / deny
                 length / empty                  docs (indirect inj.)
                                                          |
                                        [output guardrail] <-- JSON schema, citation check, canary leak, PII redaction
```

| Risk | Defence | Where |
|---|---|---|
| Prompt injection (user) | Normalised regex detector, blocked before any LLM call | `guardrails/input.py` |
| Malicious retrieved docs | Docs that issue commands are quarantined | `sanitize_context` |
| System-prompt leak | Canary token in the prompt; any appearance in output is blocked | `guardrails/output.py` |
| PII exposure | Email / phone / SSN / Luhn-valid card redaction on outputs | `redact_pii` |
| Hallucinated sources | Every citation must be an exact quote from a retrieved doc | `verify_citations` |
| Risky actions | Tools have risk tiers; writes/deletes become a plan that waits for `confirm()`; external recipients denied | `guardrails/permissions.py` |
| Tool outages | One retry, then a graceful message | `agent._exec` |

## Run it

```bash
pip install -r requirements.txt
pytest -q                                   # unit tests
python -m evals.run_evals --baseline        # eval suite, guardrails ON vs OFF
python -m guardrail_agent                   # interactive demo
python -m evals.run_evals --llm anthropic   # live model (pip install anthropic; set ANTHROPIC_API_KEY)
```

## Results (deterministic mock model, 60 cases)

| Category | Guardrails ON | Guardrails OFF |
|---|---|---|
| normal | 12/12 | 11/12 |
| edge | 10/10 | 6/10 |
| adversarial (direct injection) | 10/10 | 0/10 |
| indirect injection | 5/5 | 0/5 |
| risky actions | 8/8 | 2/8 |
| PII | 3/3 | 0/3 |
| missing data | 7/7 | 0/7 |
| tool failure | 5/5 | 5/5 |
| **Overall** | **100%** | **40%** |

False-refusal rate on answerable questions: 0%. 12 of 60 requests were stopped before any LLM call, cutting average
tokens per task from 340 to 247 (-27%).

> **Honest caveat:** the mock model is intentionally naive (it obeys injected instructions and invents citations) so the
> suite is deterministic and free in CI. Treat these numbers as a harness check. Run `--llm anthropic` (or your own
> provider) and put *those* numbers on your resume.

## Known limitations

- Regex injection detection is a first layer, not a complete defence. Layer a classifier on top for production.
- The retriever is keyword-based; swap in a vector store.
- Adding cases is one line in `evals/cases.py`. Grow the suite whenever you find a new failure.
