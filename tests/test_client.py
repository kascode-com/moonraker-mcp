import httpx
import pytest
import respx

from moonraker_mcp.client import MoonrakerClient, MoonrakerError

BASE = "http://printer.local:7125"


@pytest.fixture
def client():
    return MoonrakerClient(base_url=BASE, api_key="secret", timeout=5.0)


@respx.mock
async def test_unwraps_result_envelope(client):
    route = respx.get(f"{BASE}/printer/info").mock(
        return_value=httpx.Response(200, json={"result": {"state": "ready"}})
    )
    result = await client.printer_info()
    assert result == {"state": "ready"}
    assert route.called
    assert route.calls.last.request.headers["X-Api-Key"] == "secret"


@respx.mock
async def test_query_objects_builds_body(client):
    route = respx.post(f"{BASE}/printer/objects/query").mock(
        return_value=httpx.Response(200, json={"result": {"status": {}}})
    )
    await client.query_objects({"toolhead": None, "extruder": None})
    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"objects": {"toolhead": None, "extruder": None}}


@respx.mock
async def test_run_gcode_posts_script(client):
    route = respx.post(f"{BASE}/printer/gcode/script").mock(
        return_value=httpx.Response(200, json={"result": "ok"})
    )
    res = await client.run_gcode("G28")
    assert res == "ok"
    import json

    assert json.loads(route.calls.last.request.content) == {"script": "G28"}


@respx.mock
async def test_klippy_not_ready_maps_to_error(client):
    respx.get(f"{BASE}/printer/info").mock(
        return_value=httpx.Response(
            503, json={"error": {"code": 503, "message": "Klippy Not Ready"}}
        )
    )
    with pytest.raises(MoonrakerError) as exc:
        await client.printer_info()
    assert "Klippy Not Ready" in str(exc.value)


@respx.mock
async def test_connection_failure_maps_to_error(client):
    respx.get(f"{BASE}/server/info").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(MoonrakerError) as exc:
        await client.server_info()
    assert "Could not reach Moonraker" in str(exc.value)


@respx.mock
async def test_file_delete_path(client):
    route = respx.delete(f"{BASE}/server/files/gcodes/old.gcode").mock(
        return_value=httpx.Response(200, json={"result": {"item": {}}})
    )
    await client.file_delete("gcodes", "old.gcode")
    assert route.called


@respx.mock
async def test_file_download_returns_raw_body(client):
    route = respx.get(f"{BASE}/server/files/gcodes/part.gcode").mock(
        return_value=httpx.Response(206, text="G1 X0 Y0\nG1 X10 Y10\n")
    )
    text = await client.file_download("gcodes", "part.gcode", start=100, end=199)
    assert text == "G1 X0 Y0\nG1 X10 Y10\n"
    assert route.calls.last.request.headers["Range"] == "bytes=100-199"


@respx.mock
async def test_file_download_maps_error(client):
    respx.get(f"{BASE}/server/files/gcodes/missing.gcode").mock(
        return_value=httpx.Response(404, json={"error": {"message": "File not found"}})
    )
    with pytest.raises(MoonrakerError) as exc:
        await client.file_download("gcodes", "missing.gcode")
    assert "File not found" in str(exc.value)


@respx.mock
async def test_history_list_drops_none_params(client):
    route = respx.get(f"{BASE}/server/history/list").mock(
        return_value=httpx.Response(200, json={"result": {"jobs": []}})
    )
    await client.history_list(limit=10, start=0, since=None, before=None, order="desc")
    sent = route.calls.last.request.url.params
    assert sent["limit"] == "10"
    assert "since" not in sent and "before" not in sent  # None dropped


@respx.mock
async def test_history_delete_all_serializes_bool(client):
    route = respx.delete(f"{BASE}/server/history/job").mock(
        return_value=httpx.Response(200, json={"result": {"deleted": []}})
    )
    await client.history_delete_job(all=True)
    assert route.calls.last.request.url.params["all"] == "true"


@respx.mock
async def test_sensors_list_passes_extended(client):
    route = respx.get(f"{BASE}/server/sensors/list").mock(
        return_value=httpx.Response(200, json={"result": {"sensors": {}}})
    )
    await client.sensors_list(extended=True)
    assert route.calls.last.request.url.params["extended"] == "true"


@respx.mock
async def test_spoolman_proxy_builds_body(client):
    import json as _json

    route = respx.post(f"{BASE}/server/spoolman/proxy").mock(
        return_value=httpx.Response(200, json={"result": {}})
    )
    await client.spoolman_proxy("GET", "/v1/spool", query="a=1")
    body = _json.loads(route.calls.last.request.content)
    assert body["request_method"] == "GET"
    assert body["path"] == "/v1/spool"
    assert body["query"] == "a=1"
    assert "body" not in body  # None omitted
