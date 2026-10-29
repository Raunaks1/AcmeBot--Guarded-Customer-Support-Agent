"""Minimal HTTP server exposing the GuardedAgent so the frontend can drive it.

    python -m guardrail_agent.server [--port 8000] [--llm mock|anthropic]

Endpoints:
    GET  /api/health        -> {"ok": true, "llm": "<kind>"}
    POST /api/run           -> body {"query": str, "llm": "mock"|"anthropic"} -> GuardedAgent result
    POST /api/confirm       -> body {"pending_id": str, "approve": bool}       -> GuardedAgent result
    GET  /                 -> serves the minimal frontend from ../frontend
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .agent import GuardedAgent
from .llm import make_llm
from .types import AgentResult

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DEFAULT_PORT = 8000
ALLOWED_LLMS = ("mock", "anthropic")

_agent: GuardedAgent | None = None
_agent_kind: str = "mock"


def get_agent(llm: str) -> GuardedAgent:
    """One GuardedAgent instance per server process (mirrors the CLI demo)."""
    global _agent, _agent_kind
    if _agent is None or llm != _agent_kind:
        _agent = GuardedAgent(make_llm(llm))
        _agent_kind = llm
    return _agent


def result_to_payload(result: AgentResult) -> dict[str, Any]:
    """Serialise an AgentResult into the JSON shape the frontend renders."""
    return {
        "status": result.status,
        "answer": result.answer,
        "metrics": {
            "latency_ms": result.metrics.latency_ms,
            "input_tokens": result.metrics.input_tokens,
            "output_tokens": result.metrics.output_tokens,
            "llm_calls": result.metrics.llm_calls,
            "cost_usd": result.metrics.cost_usd,
        },
        "events": [
            {"layer": e.layer, "action": e.action, "detail": e.detail} for e in result.events
        ],
        "citations": [{"doc_id": c.doc_id, "quote": c.quote} for c in result.citations],
        "pending_id": result.pending_id,
    }


async def _read_body(receive: Any) -> bytes:
    body = b""
    while True:
        msg = await receive()
        if msg.get("type") == "http.request":
            body += msg.get("body", b"")
            if not msg.get("more_body", False):
                break
        elif msg.get("type") == "http.disconnect":
            break
    return body


async def _send(send: Any, status: int, payload: dict, content_type: str = "application/json") -> None:
    body = json.dumps(payload).encode("utf-8")
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", content_type.encode("utf-8")), (b"content-length", str(len(body)).encode("utf-8"))]})
    await send({"type": "http.response.body", "body": body})


async def api_app(scope: Any, receive: Any, send: Any) -> None:
    """ASGI entrypoint so the guarded agent API can run on Vercel serverless."""
    if scope.get("type") != "http":
        return
    method: str = scope.get("method", "")
    path: str = scope.get("path", "")

    if path == "/api/health" and method == "GET":
        await _send(send, 200, {"ok": True, "llm": _agent_kind})
        return

    if path == "/api/run" and method == "POST":
        body_bytes = await _read_body(receive)
        try:
            data = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            await _send(send, 400, {"error": "invalid JSON"})
            return
        payload = data if isinstance(data, dict) else {}
        query = payload.get("query")
        llm = payload.get("llm")
        if not isinstance(query, str) or not query.strip():
            await _send(send, 400, {"error": "field 'query' must be a non-empty string"})
            return
        if llm is not None and str(llm) not in ALLOWED_LLMS:
            await _send(send, 400, {"error": f"field 'llm' must be one of {list(ALLOWED_LLMS)}"})
            return
        result = get_agent(str(llm) if llm is not None else "mock").run(query)
        await _send(send, 200, result_to_payload(result))
        return

    if path == "/api/confirm" and method == "POST":
        body_bytes = await _read_body(receive)
        try:
            data = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            await _send(send, 400, {"error": "invalid JSON"})
            return
        payload = data if isinstance(data, dict) else {}
        pending_id = str(payload.get("pending_id") or "")
        approve = bool(payload.get("approve"))
        result = get_agent().confirm(pending_id, approve)
        await _send(send, 200, result_to_payload(result))
        return

    if method == "GET" and path in ("/", "/index.html"):
        file_path = FRONTEND_DIR / "index.html"
        ctype = "text/html; charset=utf-8"
    elif method == "GET" and path == "/style.css":
        file_path = FRONTEND_DIR / "style.css"
        ctype = "text/css; charset=utf-8"
    elif method == "GET" and path == "/app.js":
        file_path = FRONTEND_DIR / "app.js"
        ctype = "application/javascript; charset=utf-8"
    else:
        await _send(send, 404, {"error": f"not found: {path}"})
        return
    try:
        body = file_path.read_bytes()
    except OSError:
        await _send(send, 404, {"error": f"not found: {file_path.name}"})
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", ctype.encode("utf-8")), (b"content-length", str(len(body)).encode("utf-8"))]})
    await send({"type": "http.response.body", "body": body})


class Handler(BaseHTTPRequestHandler):
    """Minimal JSON + static-file handler for the guarded agent."""

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._send_json(404, {"error": f"not found: {path.name}"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._send_json(200, {"ok": True, "llm": _agent_kind})
        elif path == "/":
            self._send_file(FRONTEND_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/style.css":
            self._send_file(FRONTEND_DIR / "style.css", "text/css; charset=utf-8")
        elif path == "/app.js":
            self._send_file(FRONTEND_DIR / "app.js", "application/javascript; charset=utf-8")
        else:
            self._send_json(404, {"error": f"not found: {path}"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/run":
            self._handle_run()
        elif path == "/api/confirm":
            self._handle_confirm()
        else:
            self._send_json(404, {"error": f"not found: {path}"})

    def _handle_run(self) -> None:
        data = self._read_json_body()
        query = data.get("query") if data else None
        llm = data.get("llm") if data else None
        if not isinstance(query, str) or not query.strip():
            self._send_json(400, {"error": "field 'query' must be a non-empty string"})
            return
        if llm is not None and str(llm) not in ALLOWED_LLMS:
            self._send_json(400, {"error": f"field 'llm' must be one of {list(ALLOWED_LLMS)}"})
            return
        result = get_agent(str(llm) if llm is not None else "mock").run(query)
        self._send_json(200, result_to_payload(result))

    def _handle_confirm(self) -> None:
        data = self._read_json_body()
        pending_id = str(data.get("pending_id") or "") if data else ""
        approve = bool(data.get("approve")) if data else False
        result = get_agent().confirm(pending_id, approve)
        self._send_json(200, result_to_payload(result))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        print(f"{self.address_string()} - {format % args}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Serve the guarded agent API and minimal frontend.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--llm", default="mock", choices=list(ALLOWED_LLMS))
    args = ap.parse_args(argv)

    get_agent(args.llm)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"AcmeBot demo server running at http://127.0.0.1:{args.port} (llm={args.llm}, guardrails on)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
