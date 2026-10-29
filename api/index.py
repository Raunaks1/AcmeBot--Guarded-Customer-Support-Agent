"""Vercel serverless entrypoint for the AcmeBot guarded agent API."""
from typing import Any

from guardrail_agent.server import api_app


async def app(scope: Any, receive: Any, send: Any) -> None:
    """ASGI entrypoint for Vercel."""
    await api_app(scope, receive, send)
