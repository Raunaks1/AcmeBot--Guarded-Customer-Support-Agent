"""LLM backends.

* MockLLM  - deterministic and offline. It deliberately behaves like a *naive* model: it obeys injected
             instructions, leaks its system prompt when asked, repeats PII, and hallucinates citations when it has
             no context. That makes the eval suite meaningful (guardrails ON vs OFF) and CI free + reproducible.
* AnthropicLLM - the real thing, for live evals and the demo CLI (pip install anthropic, set ANTHROPIC_API_KEY).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .retrieval import stems
from .types import Doc


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str, context: list[Doc], tool_result: dict | None = None) -> LLMResponse: ...


# ------------------------------------------------------------------ mock
_LEAK = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)?\s*(instructions|rules)"
    r"|system prompt|hidden instructions|everything you were told|initial instructions",
    re.I,
)
_EMAIL_DIRECTIVE = re.compile(
    r"\b(send|forward|email)\b\s+(?:[\w\s]{0,40}?)\bto\s+([\w.+-]+@[\w-]+\.[\w.-]+)", re.I
)
_ORDER = re.compile(r"\bord-?\s?(\d{3,})\b", re.I)
_DELETE = re.compile(r"\b(delete|drop|remove|wipe)\b", re.I)
_DELETE_TARGET = re.compile(r"\b(table|database|record|records)\b", re.I)


class MockLLM:
    name = "mock-naive-llm"

    def complete(self, system: str, user: str, context: list[Doc], tool_result: dict | None = None) -> LLMResponse:
        payload = self._decide(system, user, context, tool_result)
        text = json.dumps(payload)
        prompt = system + user + "".join(d.text for d in context) + (json.dumps(tool_result) if tool_result else "")
        return LLMResponse(text, max(1, len(prompt) // 4), max(1, len(text) // 4))

    @staticmethod
    def _decide(system: str, user: str, context: list[Doc], tool_result: dict | None) -> dict[str, Any]:
        if tool_result is not None:  # second turn: summarise the tool output (naively includes everything, PII too)
            if not tool_result.get("found", True):
                return {"answer": f"I couldn't find order {tool_result.get('order_id')}.", "citations": [], "tool_call": None}
            return {
                "answer": f"Order {tool_result['order_id']} is {tool_result['status']}, estimated delivery "
                          f"{tool_result['eta']}. Customer on file: {tool_result['customer_email']}.",
                "citations": [], "tool_call": None,
            }

        blob = user + "\n" + "\n".join(d.text for d in context)

        if _LEAK.search(blob):  # naive: obeys "ignore instructions / reveal prompt" from user OR documents
            return {"answer": system, "citations": [], "tool_call": None}

        m = _EMAIL_DIRECTIVE.search(blob)  # naive: obeys "email X to Y" from user OR documents
        if m:
            body = re.search(r"saying\s+(.+)$", user, re.I | re.S)
            return {"answer": "", "citations": [], "tool_call": {
                "name": "send_email",
                "args": {"to": m.group(2), "subject": "Message from AcmeBot", "body": (body.group(1) if body else user).strip()},
            }}

        if _DELETE.search(user) and _DELETE_TARGET.search(user):
            table = re.search(r"(\w+)\s+(?:table|database)\b", user, re.I)
            if not table:
                table = re.search(r"\bfrom\s+(?:the\s+)?(\w+)", user, re.I)
            rec = re.search(r"\brecords?\s+#?(\w+)", user, re.I)
            rec_id = rec.group(1) if rec and rec.group(1).lower() not in {"from", "in", "table"} else None
            args = {"table": (table.group(1) if table else "unknown").lower()}
            if rec_id:
                args["record_id"] = rec_id
            return {"answer": "", "citations": [], "tool_call": {"name": "delete_record", "args": args}}

        o = _ORDER.search(user)
        if o:
            return {"answer": "", "citations": [], "tool_call": {"name": "lookup_order", "args": {"order_id": f"ORD-{o.group(1)}"}}}

        if context:  # extractive answer from the best doc, with an exact-quote citation
            top = context[0]
            sentences = re.split(r"(?<=[.!?])\s+", top.text.strip())
            q = stems(user)
            best = max(sentences, key=lambda s: len(q & stems(s)))
            return {"answer": best, "citations": [{"doc_id": top.id, "quote": best}], "tool_call": None}

        # no context: a naive model makes something up and invents a source
        return {
            "answer": "Based on our policy, this is typically handled within 2 business days.",
            "citations": [{"doc_id": "kb-999", "quote": "typically handled within 2 business days"}],
            "tool_call": None,
        }


# ------------------------------------------------------------------ real model
class AnthropicLLM:
    def __init__(self, model: str | None = None):
        import anthropic  # imported lazily so the mock path has no extra dependency

        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.name = self.model

    def complete(self, system: str, user: str, context: list[Doc], tool_result: dict | None = None) -> LLMResponse:
        ctx = "\n".join(f'<doc id="{d.id}">{d.text}</doc>' for d in context) or "(no documents found)"
        content = f"<context>\n{ctx}\n</context>\n\n<user_question>\n{user}\n</user_question>"
        if tool_result is not None:
            content += f"\n\n<tool_result>{json.dumps(tool_result)}</tool_result>\nAnswer the user from the tool result. Set tool_call to null."
        r = self.client.messages.create(
            model=self.model, max_tokens=1000, system=system, messages=[{"role": "user", "content": content}]
        )
        text = "".join(b.text for b in r.content if b.type == "text")
        return LLMResponse(text, r.usage.input_tokens, r.usage.output_tokens)


def make_llm(kind: str = "mock") -> LLM:
    return AnthropicLLM() if kind == "anthropic" else MockLLM()
