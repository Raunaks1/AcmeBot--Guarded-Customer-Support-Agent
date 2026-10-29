"""Output-side guardrails: schema validation, citation verification, PII redaction, leak detection."""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from ..config import CANARY
from ..types import Doc, LLMOutput


class OutputValidationError(Exception):
    pass


def parse_output(raw: str) -> LLMOutput:
    """Parse + validate the model's JSON against the LLMOutput schema (tolerates ``` fences / chatter)."""
    s = re.sub(r"```(?:json)?", "", raw).strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < start:
        raise OutputValidationError("no JSON object found")
    try:
        data = json.loads(s[start : end + 1])
    except json.JSONDecodeError as e:
        raise OutputValidationError(f"invalid JSON: {e}") from e
    try:
        return LLMOutput.model_validate(data)
    except ValidationError as e:
        raise OutputValidationError(f"schema violation: {e.error_count()} error(s)") from e


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_citations(out: LLMOutput, docs: list[Doc]) -> tuple[bool, list[str]]:
    """Every citation must reference a doc we actually retrieved AND quote text that is really in it."""
    if not out.citations:
        return False, ["no_citations"]
    by_id = {d.id: _norm(d.text) for d in docs}
    bad = []
    for c in out.citations:
        q = _norm(c.quote)
        if c.doc_id not in by_id or not q or q not in by_id[c.doc_id]:
            bad.append(c.doc_id)
    return (not bad), bad


def contains_canary(text: str) -> bool:
    return CANARY.lower() in text.lower()


# ---------------- PII redaction ----------------
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")
_PHONE = re.compile(r"(?<![\w-])\+?\d[\d\s().-]{8,}\d(?![\w-])")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _luhn(num: str) -> bool:
    digits = [int(c) for c in num if c.isdigit()]
    if not 13 <= len(digits) <= 16:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def redact_pii(text: str) -> tuple[str, dict[str, int]]:
    counts = {"email": 0, "phone": 0, "ssn": 0, "card": 0}

    def sub(kind, label, validator=None):
        def _r(m):
            if validator and not validator(m.group(0)):
                return m.group(0)
            counts[kind] += 1
            return f"[REDACTED_{label}]"
        return _r

    text = _CARD.sub(sub("card", "CARD", _luhn), text)
    text = _SSN.sub(sub("ssn", "SSN"), text)
    text = _EMAIL.sub(sub("email", "EMAIL"), text)
    text = _PHONE.sub(
        sub("phone", "PHONE", lambda s: sum(c.isdigit() for c in s) >= 10 and not _ISO_DATE.match(s.strip())), text
    )
    return text, {k: v for k, v in counts.items() if v}
