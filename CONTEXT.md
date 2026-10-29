# Context: `guardrail-agent`

## 1. Project Overview

A customer-support agent ("AcmeBot") that is **guarded, evaluated, and continuously tested**.

- **Purpose**: Defend an LLM-powered support assistant against prompt injection, poisoned retrieved documents, system-prompt/canary leaks, hallucinated citations, PII exposure, and risky actions (sending emails / deleting records).
- **Current state**: Eval suite fully green — **63/63 pass with guardrails ON (100%)**; 0% false-refusal rate; guardrails OFF baseline was **26/63 (41.3%)**.

## 2. Hard Constraints (from `AGENTS.md` and `.agents/rules/guardrail-agent.md`)

- **Python Version**: Python 3.11 (CI matrix also tests 3.12).
- **Allowed Dependencies**: `pydantic`, `pytest`. An LLM SDK is allowed **only inside `guardrail_agent/llm.py`**, imported lazily.
- **Offline & Deterministic**: Tests and evals must never make network calls. Use the deterministic `MockLLM`.
- **Guardrail Testing**: Every guardrail gets unit tests, including at least one benign input that must NOT be blocked.
- **Test Integrity**: Never delete, skip, or weaken a test to make it pass. If a test looks wrong, flag why before changing it.
- **Post-Task Protocol**: After every task: run `pytest`, show the output, then summarise what changed.
- **Code Style & Config**: Keep functions small and type-hinted. Put policies and thresholds in `guardrail_agent/config.py`.

## 3. Repository Layout

```
Evaluated Guardrail Agent with CI/
├── .agents/rules/guardrail-agent.md
├── .github/workflows/ci.yml
├── data/docs/            # knowledge-base markdown docs (loaded by Retriever)
├── evals/
│   ├── cases.py          # 63 eval cases across 8 categories
│   ├── run_evals.py      # runner, summarizer, markdown report, CLI gate
│   └── results/          # results.json + results.md (generated artifacts)
├── guardrail_agent/
│   ├── agent.py          # GuardedAgent: orchestrates the full pipeline
│   ├── config.py         # policies, canary, system prompt, limits, pricing
│   ├── llm.py            # LLM Protocol, MockLLM, AnthropicLLM, make_llm
│   ├── retrieval.py      # Retriever (keyword/BM25-ish), stems()
│   ├── tools.py          # ToolBox, ToolError, side-effect tools, ORDERS
│   ├── types.py          # pydantic + dataclass models
│   ├── __main__.py       # CLI demo
│   └── guardrails/
│       ├── input.py      # injection detect + context sanitise
│       ├── output.py     # schema, citations, canary, PII redaction
│       └── permissions.py  # risk tiers, allow/confirm/deny
├── tests/
│   └── test_guardrails.py
├── AGENTS.md
├── README.md
├── CONTEXT.md            # this file
├── pytest.ini
└── requirements.txt
```

## 4. Data Model / Types (`guardrail_agent/types.py`)

- **Pydantic models**: `Citation(doc_id: str, quote: str)`, `ToolCall(name, args: dict)`, `LLMOutput(answer: str, citations: list[Citation], tool_call: ToolCall | None)`.
- **Dataclasses**: `Doc(id, text)`; `GuardrailEvent(layer, action, detail)` — layer ∈ `input | context | output | permissions`; action ∈ `blocked | dropped | redacted | denied | confirm_required | ...`; `Metrics(latency_ms, input_tokens, output_tokens, llm_calls, cost_usd)`; `AgentResult(status, answer, metrics, events, citations, pending_id)` — status ∈ `answered | blocked | no_answer | denied | pending_confirmation | cancelled | error_handled`.

## 5. Module-by-Module Behaviour

### `guardrail_agent/config.py` (single source of policy)
- `CANARY = "CANARY-7f3a91"` — planted in system prompt; any appearance in output ⇒ blocked.
- `SYSTEM_PROMPT` — AcmeBot persona; cites docs via `{"doc_id": ..., "quote": <exact sentence>}`; outputs **only JSON**: `{"answer": str, "citations": [...], "tool_call": null | {...}}`.
- `MAX_INPUT_CHARS = 4000`; `ALLOWED_EMAIL_DOMAINS = {"acme.example"}`; `PRICE_PER_MTOK_IN = 3.0`, `PRICE_PER_MTOK_OUT = 15.0` (illustrative cost tracking).

### `guardrail_agent/llm.py`
- `LLM` Protocol: `complete(system, user, context: list[Doc], tool_result=None) -> LLMResponse`.
- **`MockLLM`** (`mock-naive-llm`) — deliberately naive: obeys injected instructions, leaks system prompt, includes PII, hallucinates citations.
- **`AnthropicLLM`** — real client, lazy `import anthropic`, default model `claude-sonnet-5-5` (env `ANTHROPIC_MODEL`).

### `guardrail_agent/retrieval.py`
- `Retriever(docs=None, extra_docs=None)` loads `data/docs/*.md`; `stems()` tokenizer; `search(query, k=3, min_score=1)` keyword-overlap scoring; emails stripped before stemming.

### `guardrail_agent/tools.py`
- `ToolBox(failures: dict[str, int])` with failure injection; `SIDE_EFFECT_TOOLS = {"send_email", "delete_record"}`.
- Tools: `lookup_order(order_id)`, `send_email(to, subject, body)`, `delete_record(table, record_id)`. Orders store in `ORDERS`.

### `guardrail_agent/guardrails/input.py`
- `normalize()` (NFKC, zero-width strip, space collapse); `INJECTION_PATTERNS` (10); `CONTEXT_DIRECTIVE_PATTERNS` (`doc_exfiltration`, `doc_destructive`).
- `detect_injection(text, extra=None) -> list[str]`; `check_input(text, max_chars) -> InputDecision`; `sanitize_context(docs) -> (clean, events)`.

### `guardrail_agent/guardrails/output.py`
- `parse_output(raw) -> LLMOutput`; `verify_citations(out, docs) -> (ok, bad_ids)`; `contains_canary(text)`; `redact_pii(text) -> (text, counts)` (email/SSN/Luhn-valid card/phone → `[REDACTED_*]`).

### `guardrail_agent/guardrails/permissions.py`
- `Risk` enum (READ/WRITE/DESTRUCTIVE); `TOOL_RISK`; `evaluate(call) -> PolicyDecision` (allow/confirm/deny); `describe_plan(call)`.

### `guardrail_agent/agent.py` — `GuardedAgent`
Pipeline: input check (blocked before any LLM call) → retrieve → sanitize context → LLM → parse → tool handling (permissions, retry) → finalize (canary, citations, PII).

## 6. Guardrail Layers

| Risk | Defence | Where |
|---|---|---|
| Prompt injection (user) | Normalised regex detector, blocked before any LLM call | `guardrails/input.py` |
| Malicious retrieved docs | Docs that issue commands are quarantined | `sanitize_context` |
| System-prompt leak | Canary token in prompt; any appearance in output is blocked | `guardrails/output.py` |
| PII exposure | Email / phone / SSN / Luhn-valid card redaction on outputs | `redact_pii` |
| Hallucinated sources | Every citation must be an exact quote from a retrieved doc | `verify_citations` |
| Risky actions | Tools have risk tiers; writes/deletes become a plan that waits for `confirm()`; external recipients denied | `guardrails/permissions.py` |
| Tool outages | One retry, then a graceful message | `agent._exec` |

## 7. Eval Suite (`evals/`)

- **Categories**: normal / edge / adversarial / indirect_injection / risky_action / pii / missing_data / tool_failure.
- **63 cases** (`evals/cases.py`): normal 13, edge 11, adversarial 10, indirect_injection 5, risky_action 8, pii 4, missing_data 7, tool_failure 5.
- **Runner** (`evals/run_evals.py`): `run_case`, `summarize`, `markdown`, CLI flags `--llm`, `--baseline`, `--min-pass` (0.95), `--min-security` (1.0).
- **Gates**: overall pass rate ≥ 0.95; every security category ≥ 1.0.

## 8. CI (`.github/workflows/ci.yml`)

- **Job 1** `test-and-evaluate`: Python 3.11 & 3.12; `pytest -v`; `python -m evals.run_evals --baseline --min-pass 0.95 --min-security 1.0`; publish results.md to step summary; upload artifact.
- **Job 2** `live-llm-eval`: workflow_dispatch only; anthropic; `--min-pass 0.90 --min-security 0.95`; uses `ANTHROPIC_API_KEY` secret.

## 9. Tests (`tests/test_guardrails.py`)

- ~57 unit tests; injection detection (13 attacks / 7 benign), boundaries, poisoned docs, output guardrails, PII, canary, citations, permissions, retries, agent integration, acceptance tests.

## 10. Latest Eval Results (snapshot)

- Guardrails ON: **100% (63/63)**; OFF: **41.3% (26/63)**.
- Per-category ON: normal 13/13, edge 11/11, adversarial 10/10, indirect_injection 5/5, risky_action 8/8, pii 4/4, missing_data 7/7, tool_failure 5/5.
- False-refusal rate: **0%**; 12 of 63 requests stopped before any LLM call; avg tokens/task 251 vs 339 with guardrails off (−26%).

## 11. Git State

- Clean working tree; recent history: `feat:`/`test:`/`docs:`/`ci:` commits, latest `e3124ba ci: trigger workflow on all pushes and pull requests`.

## 12. Known Limitations

- Regex injection detection is a first layer, not a complete defence (classifier recommended for production).
- Retriever is keyword-based; vector store recommended for real deployment.
- Adding cases = one line in `evals/cases.py`.
