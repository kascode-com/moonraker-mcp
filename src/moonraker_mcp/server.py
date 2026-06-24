"""FastMCP server wiring: build the app, gate it behind a bearer token, run it
over Streamable HTTP."""

from __future__ import annotations

import logging
import secrets

import uvicorn
from mcp.server.fastmcp import FastMCP

from .config import Settings
from .registry import PrinterRegistry
from .tools import register_tools

logger = logging.getLogger("moonraker_mcp")

# Endpoints that must remain reachable without the bearer token (health/liveness).
_PUBLIC_PATHS = frozenset({"/health"})


def build_mcp(settings: Settings) -> tuple[FastMCP, PrinterRegistry]:
    """Create a FastMCP instance with all Moonraker tools registered, plus the
    backing :class:`PrinterRegistry`."""
    printers = settings.resolve_printers()
    registry = PrinterRegistry(printers, timeout=settings.request_timeout)

    listing = ", ".join(
        f"{p.name}{' (primary)' if p.primary else ''}={p.url}" for p in printers
    )
    instructions = (
        "Controls one or more Klipper printers via Moonraker. Every tool accepts a "
        f"'printer' argument naming which printer to act on; omit it for the primary "
        f"('{registry.primary}'). Configured printers: {listing}. "
        "Call list_printers to enumerate them."
    )

    mcp = FastMCP(
        "moonraker",
        host=settings.mcp_host,
        port=settings.mcp_port,
        instructions=instructions,
    )
    register_tools(mcp, registry)
    return mcp, registry


def _bearer_auth_wrapper(app, token: str):
    """ASGI middleware enforcing ``Authorization: Bearer <token>``."""

    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        if scope.get("path") in _PUBLIC_PATHS:
            await app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        # Constant-time compare so the token can't be recovered by timing the
        # response to byte-by-byte guesses.
        if secrets.compare_digest(auth, f"Bearer {token}"):
            await app(scope, receive, send)
            return

        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send(
            {"type": "http.response.body", "body": b'{"error":"unauthorized"}'}
        )

    return wrapped


def _close_on_shutdown(app, registry: PrinterRegistry):
    """ASGI middleware that closes the registry's HTTP clients when the ASGI
    lifespan shuts down.

    FastMCP's app owns the Starlette ``lifespan`` (for its session manager), so
    ``on_shutdown`` event handlers are ignored. Instead we intercept the lifespan
    channel and call :meth:`PrinterRegistry.aclose` once the inner app has
    reported ``lifespan.shutdown.complete``.
    """

    async def wrapped(scope, receive, send):
        if scope["type"] != "lifespan":
            await app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "lifespan.shutdown.complete":
                await registry.aclose()
            await send(message)

        await app(scope, receive, send_wrapper)

    return wrapped


def run(settings: Settings | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    settings = settings or Settings()

    mcp, registry = build_mcp(settings)
    app = mcp.streamable_http_app()
    # Close per-printer HTTP clients cleanly when the server shuts down.
    app = _close_on_shutdown(app, registry)

    if settings.mcp_auth_token:
        app = _bearer_auth_wrapper(app, settings.mcp_auth_token)
        logger.info("Bearer-token authentication enabled for the MCP endpoint.")
    else:
        logger.warning(
            "MCP_AUTH_TOKEN is not set — the MCP endpoint is UNAUTHENTICATED. "
            "Only run this on a trusted LAN."
        )

    logger.info(
        "Starting Moonraker MCP server on http://%s:%s/mcp — printers: %s (primary: %s)",
        settings.mcp_host,
        settings.mcp_port,
        ", ".join(registry.names),
        registry.primary,
    )
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port)
