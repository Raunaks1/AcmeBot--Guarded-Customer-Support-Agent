# Project Rules for guardrail-agent

- **Python Version**: Python 3.11.
- **Allowed Dependencies**: `pydantic`, `pytest`. An LLM SDK is allowed only inside `guardrail_agent/llm.py`, imported lazily.
- **Offline & Deterministic**: Tests and evals must never make network calls. Use a deterministic `MockLLM`.
- **Guardrail Testing**: Every guardrail gets unit tests, including at least one benign input that must NOT be blocked.
- **Test Integrity**: Never delete, skip, or weaken a test to make it pass. If a test looks wrong, tell me why first.
- **Post-Task Protocol**: After every task: run pytest, show the output, then summarise what changed.
- **Code Style & Config**: Keep functions small and type-hinted. Put policies and thresholds in `guardrail_agent/config.py`.

