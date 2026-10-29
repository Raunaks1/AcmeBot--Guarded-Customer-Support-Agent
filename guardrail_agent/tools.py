"""Tools the agent can call, with failure injection so we can test tool-failure handling."""
from __future__ import annotations

from typing import Any


class ToolError(Exception):
    pass


SIDE_EFFECT_TOOLS = {"send_email", "delete_record"}

ORDERS = {
    "ORD-1001": {"status": "shipped", "eta": "2026-11-02", "customer_email": "jane.roe@example.com"},
    "ORD-1002": {"status": "processing", "eta": "2026-11-05", "customer_email": "arjun.k@example.com"},
    "ORD-1003": {"status": "delivered", "eta": "2026-10-20", "customer_email": "sam.lee@example.com"},
}


class ToolBox:
    def __init__(self, failures: dict[str, int] | None = None):
        # failures: tool name -> number of calls that should fail before it starts working (99 = always fails)
        self._failures = dict(failures or {})
        self.executed: list[tuple[str, dict]] = []  # successful calls only

    def side_effects(self) -> list[tuple[str, dict]]:
        return [c for c in self.executed if c[0] in SIDE_EFFECT_TOOLS]

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self._failures.get(name, 0) > 0:
            self._failures[name] -= 1
            raise ToolError(f"{name} failed (simulated outage)")

        if name == "lookup_order":
            oid = str(args.get("order_id", "")).upper()
            if not oid:
                raise ToolError("order_id is required")
            result = {"order_id": oid, "found": oid in ORDERS, **ORDERS.get(oid, {})}
        elif name == "send_email":
            if not args.get("to"):
                raise ToolError("'to' is required")
            result = {"sent": True, "to": args["to"]}
        elif name == "delete_record":
            if not args.get("table"):
                raise ToolError("'table' is required")
            result = {"deleted": True, "table": args["table"], "record_id": args.get("record_id")}
        else:
            raise ToolError(f"unknown tool '{name}'")

        self.executed.append((name, dict(args)))
        return result
