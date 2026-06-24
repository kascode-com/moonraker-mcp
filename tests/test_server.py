"""Tests for the ASGI middleware in server.py: bearer-token auth and the
lifespan shutdown hook that closes per-printer HTTP clients."""

from moonraker_mcp.server import _PUBLIC_PATHS, _bearer_auth_wrapper, _close_on_shutdown


class SendRecorder:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


def make_inner_app():
    """A trivial inner ASGI app that records whether it was reached."""
    state = {"reached": False}

    async def app(scope, receive, send):
        state["reached"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app, state


async def _noop_receive():
    return {"type": "http.request"}


def _http_scope(path: str, token: str | None):
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return {"type": "http", "path": path, "headers": headers}


# -- bearer auth (#4) --------------------------------------------------------

async def test_auth_allows_correct_token():
    inner, state = make_inner_app()
    app = _bearer_auth_wrapper(inner, "s3cret")
    send = SendRecorder()
    await app(_http_scope("/mcp", "s3cret"), _noop_receive, send)
    assert state["reached"]
    assert send.messages[0]["status"] == 200


async def test_auth_rejects_wrong_token():
    inner, state = make_inner_app()
    app = _bearer_auth_wrapper(inner, "s3cret")
    send = SendRecorder()
    await app(_http_scope("/mcp", "nope"), _noop_receive, send)
    assert not state["reached"]
    assert send.messages[0]["status"] == 401


async def test_auth_rejects_missing_header():
    inner, state = make_inner_app()
    app = _bearer_auth_wrapper(inner, "s3cret")
    send = SendRecorder()
    await app(_http_scope("/mcp", None), _noop_receive, send)
    assert not state["reached"]
    assert send.messages[0]["status"] == 401


async def test_auth_public_path_bypasses_token():
    path = next(iter(_PUBLIC_PATHS))
    inner, state = make_inner_app()
    app = _bearer_auth_wrapper(inner, "s3cret")
    send = SendRecorder()
    await app(_http_scope(path, None), _noop_receive, send)
    assert state["reached"]


async def test_auth_passes_through_non_http_scopes():
    inner, state = make_inner_app()
    app = _bearer_auth_wrapper(inner, "s3cret")
    send = SendRecorder()
    await app({"type": "lifespan"}, _noop_receive, send)
    assert state["reached"]


# -- shutdown closes clients (#6) --------------------------------------------

class FakeRegistry:
    def __init__(self) -> None:
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


async def test_close_on_shutdown_closes_registry():
    registry = FakeRegistry()

    async def inner(scope, receive, send):
        # Emulate Starlette's lifespan handling: ack shutdown.
        await send({"type": "lifespan.shutdown.complete"})

    app = _close_on_shutdown(inner, registry)
    send = SendRecorder()
    await app({"type": "lifespan"}, _noop_receive, send)

    assert registry.closed == 1
    assert send.messages[-1]["type"] == "lifespan.shutdown.complete"


async def test_close_on_shutdown_ignores_non_lifespan():
    registry = FakeRegistry()
    inner, state = make_inner_app()
    app = _close_on_shutdown(inner, registry)
    await app(_http_scope("/mcp", "x"), _noop_receive, SendRecorder())
    assert state["reached"]
    assert registry.closed == 0
