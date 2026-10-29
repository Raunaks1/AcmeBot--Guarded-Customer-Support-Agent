"""The guarded agent: input guardrails -> retrieval -> context sanitising -> LLM -> permissions -> output guardrails.

`guardrails=False` disables every safety layer (but keeps schema parsing and tool retries). It exists purely so the
eval suite can show a before/after comparison."""
from __future__ import annotations

import time
import uuid

from .config import MAX_INPUT_CHARS, SYSTEM_PROMPT
from .guardrails.input import check_input, sanitize_context
from .guardrails.output import (OutputValidationError, contains_canary, parse_output, redact_pii, verify_citations)
from .guardrails.permissions import TOOL_RISK, Risk, describe_plan, evaluate
from .llm import LLM
from .retrieval import Retriever
from .tools import ToolBox, ToolError
from .types import AgentResult, GuardrailEvent, LLMOutput, Metrics, ToolCall

MSG_NO_ANSWER = "I couldn't find verified information about that in our knowledge base."
MSG_LEAK = "I can't share that."
MSG_TOOL_FAILED = "I was unable to complete that because a tool failed. Please try again later."
MSG_INVALID = "I was unable to produce a valid response. Please try again."


class GuardedAgent:
    def __init__(self, llm: LLM, retriever: Retriever | None = None, toolbox: ToolBox | None = None,
                 guardrails: bool = True, max_tool_attempts: int = 2):
        self.llm = llm
        self.retriever = retriever or Retriever()
        self.toolbox = toolbox or ToolBox()
        self.guardrails = guardrails
        self.max_tool_attempts = max_tool_attempts
        self.pending: dict[str, ToolCall] = {}

    # ------------------------------------------------------------------ public API
    def run(self, query: str) -> AgentResult:
        t0 = time.perf_counter()
        m, ev = Metrics(), []
        result = self._run(query, m, ev)
        m.latency_ms = (time.perf_counter() - t0) * 1000
        result.metrics, result.events = m, ev
        return result

    def confirm(self, pending_id: str, approve: bool) -> AgentResult:
        """Human-in-the-loop step for risky actions."""
        call = self.pending.pop(pending_id, None)
        if call is None:
            return AgentResult("error_handled", "No such pending action.")
        ev = [GuardrailEvent("permissions", "approved" if approve else "rejected", call.name)]
        if not approve:
            return AgentResult("cancelled", "Action cancelled. Nothing was executed.", events=ev)
        try:
            self._exec(call)
        except ToolError as e:
            ev.append(GuardrailEvent("tool", "failed", str(e)))
            return AgentResult("error_handled", MSG_TOOL_FAILED, events=ev)
        return AgentResult("answered", self._done_message(call), events=ev)

    # ------------------------------------------------------------------ pipeline
    def _run(self, query: str, m: Metrics, ev: list[GuardrailEvent]) -> AgentResult:
        if self.guardrails:
            d = check_input(query, MAX_INPUT_CHARS)
            if not d.allowed:
                ev.append(GuardrailEvent("input", "blocked", ",".join(d.reasons)))
                return AgentResult("blocked", d.message)  # blocked BEFORE any LLM call -> zero tokens spent

        docs = self.retriever.search(query)
        if self.guardrails:
            docs, dropped = sanitize_context(docs)
            ev.extend(dropped)

        try:
            out = parse_output(self._llm(query, docs, None, m))
        except OutputValidationError as e:
            ev.append(GuardrailEvent("output", "invalid_schema", str(e)))
            return AgentResult("error_handled", MSG_INVALID)

        if out.tool_call:
            return self._handle_tool(query, docs, out.tool_call, m, ev)
        return self._finalize(out, docs, ev, require_citations=True)

    def _handle_tool(self, query, docs, call: ToolCall, m: Metrics, ev) -> AgentResult:
        if self.guardrails:
            decision = evaluate(call)
            if decision.verdict == "deny":
                ev.append(GuardrailEvent("permissions", "denied", decision.reason))
                return AgentResult("denied", f"I can't do that: {decision.reason}.")
            if decision.verdict == "confirm":
                pid = uuid.uuid4().hex[:8]
                self.pending[pid] = call
                ev.append(GuardrailEvent("permissions", "confirm_required", f"{call.name} ({decision.reason})"))
                return AgentResult("pending_confirmation", describe_plan(call), pending_id=pid)

        try:
            result = self._exec(call)
        except ToolError as e:
            ev.append(GuardrailEvent("tool", "failed", str(e)))
            return AgentResult("error_handled", MSG_TOOL_FAILED)

        if TOOL_RISK.get(call.name) is Risk.READ:
            try:
                out = parse_output(self._llm(query, docs, result, m))
            except OutputValidationError as e:
                ev.append(GuardrailEvent("output", "invalid_schema", str(e)))
                return AgentResult("error_handled", MSG_INVALID)
            return self._finalize(out, docs, ev, require_citations=False)  # source of truth is the tool, not docs
        return AgentResult("answered", self._done_message(call))  # only reachable with guardrails disabled

    def _finalize(self, out: LLMOutput, docs, ev, require_citations: bool) -> AgentResult:
        answer = out.answer
        if self.guardrails:
            if contains_canary(answer):
                ev.append(GuardrailEvent("output", "blocked", "system prompt / canary leak"))
                return AgentResult("blocked", MSG_LEAK)
            if require_citations:
                ok, bad = verify_citations(out, docs)
                if not ok:
                    ev.append(GuardrailEvent("output", "blocked", f"unverified citations: {','.join(bad)}"))
                    return AgentResult("no_answer", MSG_NO_ANSWER)
            answer, counts = redact_pii(answer)
            if counts:
                ev.append(GuardrailEvent("output", "redacted", str(counts)))
        return AgentResult("answered", answer, citations=out.citations)

    # ------------------------------------------------------------------ helpers
    def _llm(self, query, docs, tool_result, m: Metrics) -> str:
        r = self.llm.complete(SYSTEM_PROMPT, query, docs, tool_result)
        m.llm_calls += 1
        m.input_tokens += r.input_tokens
        m.output_tokens += r.output_tokens
        return r.text

    def _exec(self, call: ToolCall) -> dict:
        last: ToolError | None = None
        for _ in range(self.max_tool_attempts):  # retry transient tool failures once
            try:
                return self.toolbox.call(call.name, call.args)
            except ToolError as e:
                last = e
        raise last  # type: ignore[misc]

    @staticmethod
    def _done_message(call: ToolCall) -> str:
        if call.name == "send_email":
            return f"Done: email sent to {call.args.get('to')}."
        if call.name == "delete_record":
            rid = call.args.get("record_id")
            return f"Done: deleted {call.args.get('table')}" + (f" record {rid}." if rid else " (entire table).")
        return f"Done: {call.name} executed."
