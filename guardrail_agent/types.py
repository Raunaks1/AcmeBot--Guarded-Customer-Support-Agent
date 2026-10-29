from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .config import PRICE_PER_MTOK_IN, PRICE_PER_MTOK_OUT


class Citation(BaseModel):
    doc_id: str
    quote: str


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class LLMOutput(BaseModel):
    """The structured response schema we require from the model."""
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    tool_call: ToolCall | None = None


@dataclass
class Doc:
    id: str
    text: str


@dataclass
class GuardrailEvent:
    layer: str   # input | context | output | permissions
    action: str  # blocked | dropped | redacted | denied | confirm_required | ...
    detail: str = ""


@dataclass
class Metrics:
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens * PRICE_PER_MTOK_IN + self.output_tokens * PRICE_PER_MTOK_OUT) / 1_000_000


@dataclass
class AgentResult:
    status: str  # answered | blocked | no_answer | denied | pending_confirmation | cancelled | error_handled
    answer: str
    metrics: Metrics = field(default_factory=Metrics)
    events: list[GuardrailEvent] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    pending_id: str | None = None
