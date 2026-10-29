"""Permissions layer: classify every tool by risk and decide allow / confirm / deny.

Risky actions are never executed straight from model output. The agent prepares a plan and waits for
an explicit human approval (see GuardedAgent.confirm)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import ALLOWED_EMAIL_DOMAINS
from ..types import ToolCall


class Risk(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


TOOL_RISK = {
    "lookup_order": Risk.READ,
    "send_email": Risk.WRITE,
    "delete_record": Risk.DESTRUCTIVE,
}


@dataclass
class PolicyDecision:
    verdict: str  # allow | confirm | deny
    reason: str = ""


def evaluate(call: ToolCall) -> PolicyDecision:
    risk = TOOL_RISK.get(call.name)
    if risk is None:
        return PolicyDecision("deny", f"unknown tool '{call.name}'")

    if call.name == "send_email":
        to = str(call.args.get("to", "")).strip().lower()
        domain = to.rsplit("@", 1)[-1] if "@" in to else ""
        if domain not in ALLOWED_EMAIL_DOMAINS:
            return PolicyDecision("deny", f"recipient domain '{domain or to}' is not allowlisted (possible exfiltration)")

    if risk is Risk.READ:
        return PolicyDecision("allow")
    return PolicyDecision("confirm", f"{risk.value} action requires human approval")


def describe_plan(call: ToolCall) -> str:
    risk = TOOL_RISK[call.name]
    args = ", ".join(f"{k}={v!r}" for k, v in call.args.items())
    lines = [
        "I've prepared this action but will NOT run it until you confirm:",
        f"  - Action: {call.name}({args})",
        f"  - Risk level: {risk.value}",
    ]
    if risk is Risk.DESTRUCTIVE:
        lines.append("  - WARNING: this action is irreversible.")
    return "\n".join(lines)
