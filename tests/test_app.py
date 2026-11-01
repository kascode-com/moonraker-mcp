"""End-to-end tests of the built MCP app: health routes, auth, tool listing and
G-code gating through a real MCP client (in-process, no network)."""

import httpx
import pytest
import respx
from mcp import Client

from moonraker_mcp.config import Settings
from moonraker_mcp.server import _PUBLIC_PATHS, _bearer_auth_wrapper, build_mcp

PRINTER_URL = "http://printer.test:7125"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no printers.toml/json here
    return Settings(moonraker_url=PRINTER_URL, confirm_gcode_commands="M4029, m901")


@pytest.mark.parametrize("path", sorted(_PUBLIC_PATHS))
async def test_health_routes_are_public(settings, path):
    mcp, _ = build_mcp(settings)
    app = _bearer_auth_wrapper(mcp.streamable_http_app(), "s3cret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        resp = await http.get(path)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_mcp_endpoint_requires_token(settings):
    mcp, _ = build_mcp(settings)
    app = _bearer_auth_wrapper(mcp.streamable_http_app(), "s3cret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        resp = await http.post("/mcp", json={})
    assert resp.status_code == 401


async def test_tools_are_listed(settings):
    mcp, _ = build_mcp(settings)
    async with Client(mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert {"run_gcode", "run_macro", "get_gated_gcode_commands", "list_printers"} <= names


async def test_configured_macro_is_gated(settings):
    mcp, registry = build_mcp(settings)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{PRINTER_URL}/printer/gcode/script").respond(json={"result": "ok"})
        async with Client(mcp) as client:
            gated = await client.call_tool("run_macro", {"name": "M4029", "params": {"S": 60}})
            plain = await client.call_tool("run_gcode", {"script": "G28"})
            listed = await client.call_tool("get_gated_gcode_commands", {})
    await registry.aclose()
    assert "confirmation_required" in gated.content[0].text
    assert "M4029" in gated.content[0].text
    assert not plain.is_error
    assert route.call_count == 1
    assert route.calls[0].request.content == b'{"script":"G28"}'
    assert {"M104", "M4029", "M901"} <= {c.text for c in listed.content}
