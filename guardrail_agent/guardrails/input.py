"""Input-side guardrails: prompt-injection detection for user text AND retrieved documents."""
from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass, field

from ..types import Doc, GuardrailEvent

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)

_B64 = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def normalize(text: str) -> str:
    """Defeat trivial obfuscation: unicode look-alikes, zero-width chars, runs of spaces."""
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    return re.sub(r"[ \t]+", " ", text)


_F = re.IGNORECASE | re.MULTILINE
INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("override_instructions", re.compile(r"\b(ignore|disregard|forget|override)\s+(all\s+|any\s+)?(of\s+)?(the\s+)?(previous|prior|above|earlier|preceding|your)\s+(instructions?|prompts?|rules?|messages?|guidelines?)", _F)),
    ("override_instructions_postfix", re.compile(r"\b(ignore|disregard|forget|override)\s+(all\s+|any\s+)?(of\s+)?(the\s+)?(instructions?|rules?|prompts?|guidelines?)\s+(above|before|given|you\s+(were|have\s+been)\s+given)", _F)),
    ("forget_everything", re.compile(r"\b(forget|ignore|disregard)\s+(everything|all)\s+(above|before|previous|prior)", _F)),
    ("reveal_prompt", re.compile(r"\b(reveal|print|show|display|output|leak|expose)\b[^.\n]{0,30}\b(system|hidden|initial|original|secret)\s+(prompt|instructions?|message)", _F)),
    ("ask_initial_instructions", re.compile(r"\b(what\s+(were|are|was)\s+(your\s+)?(initial|original|hidden|system)\s+(instructions?|prompts?)|your\s+system\s+prompt)", _F)),
    ("persona_jailbreak", re.compile(r"\b(you\s+are\s+now\s+(dan|in\s+developer\s+mode|an?\s+unrestricted)|developer\s+mode|jailbreak(ed)?\s+mode|do\s+anything\s+now)", _F)),
    ("fake_system_header", re.compile(r"^\s*#{0,4}\s*(system|assistant)\s*:", _F)),
    ("fake_system_tag", re.compile(r"</?\s*(system|instructions?)\s*>", _F)),
    ("new_instructions", re.compile(r"\bnew\s+(system\s+)?(instructions?|rules?)\s*:", _F)),
    ("pretend_no_rules", re.compile(r"\bpretend\s+(that\s+)?(you|the\s+rules|there\s+are\s+no)", _F)),
    # Broad directive-override phrasing: any imperative verb + instruction/rules keyword within a sentence.
    ("override_directives", re.compile(r"\b(ignore|disregard|forget|override|abandon|drop|cancel|replace|repeat|recite|echo|copy|parrot)\b[^.\n]{0,60}\b(instructions?|prompts?|rules?|guidelines?|messages?|directives?)\b", _F)),
    # Instruction-extraction questions: asking about / requesting the system's instructions.
    ("ask_instructions", re.compile(r"\b(what|which|who|when|where|why|how)\b[^.\n]{0,60}\b(instructions?|prompts?|rules?|guidelines?|messages?|directives?)\b", _F)),
]

# Extra patterns that only make sense for retrieved *documents*: documents should never give orders.
CONTEXT_DIRECTIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("doc_exfiltration", re.compile(r"\b(send|forward|email|e-mail)\b[^.\n]{0,60}[\w.+-]+@[\w-]+\.[\w.-]+", _F)),
    ("doc_destructive", re.compile(r"\b(delete|drop|wipe|truncate)\b[^.\n]{0,40}\b(tables?|databases?|records?)\b", _F)),
]


def _decoded_candidates(text: str) -> list[str]:
    """Pull base64-like tokens out of the text and decode them; injection may hide inside encoded payloads."""
    found: list[str] = []
    for m in _B64.finditer(text):
        token = m.group(0)
        if len(token) < 16:
            continue
        padded = token + "=" * (-len(token) % 4)
        try:
            dec = base64.b64decode(padded, validate=False).decode("utf-8", errors="ignore").strip()
        except Exception:
            continue
        if len(dec) > 4 and dec.strip():
            found.append(dec)
    return found


def detect_injection(text: str, extra: list[tuple[str, re.Pattern]] | None = None) -> list[str]:
    t = normalize(text)
    pats = INJECTION_PATTERNS + (extra or [])
    hits = [name for name, pat in pats if pat.search(t)]
    for dec in _decoded_candidates(text):
        dt = normalize(dec)
        hits += [name for name, pat in pats if pat.search(dt)]
    out: list[str] = []
    for h in hits:
        if h not in out:
            out.append(h)
    return out


@dataclass
class InputDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    message: str = ""


def check_input(text: str, max_chars: int) -> InputDecision:
    if not text or not text.strip():
        return InputDecision(False, ["empty_input"], "Please enter a question.")
    if len(text) > max_chars:
        return InputDecision(False, ["too_long"], f"Your message is too long (limit {max_chars} characters).")
    hits = detect_injection(text)
    if hits:
        return InputDecision(False, hits, "I can't help with that request.")
    return InputDecision(True)


def sanitize_context(docs: list[Doc]) -> tuple[list[Doc], list[GuardrailEvent]]:
    """Indirect prompt injection defense: quarantine retrieved docs that try to give the model orders."""
    clean, events = [], []
    for d in docs:
        hits = detect_injection(d.text, CONTEXT_DIRECTIVE_PATTERNS)
        if hits:
            events.append(GuardrailEvent("context", "dropped", f"doc={d.id} patterns={','.join(hits)}"))
        else:
            clean.append(d)
    return clean, events
