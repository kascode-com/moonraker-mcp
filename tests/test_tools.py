from unittest.mock import AsyncMock

import pytest

from moonraker_mcp import tools


@pytest.fixture
def client():
    return AsyncMock()


# -- read tools --------------------------------------------------------------

async def test_get_printer_status_uses_default_objects(client):
    client.query_objects.return_value = {"status": {}}
    await tools.get_printer_status(client)
    sent = client.query_objects.call_args.args[0]
    assert set(sent) == set(tools.DEFAULT_STATUS_OBJECTS)
    assert all(v is None for v in sent.values())


async def test_get_printer_status_custom_objects(client):
    client.query_objects.return_value = {"status": {}}
    await tools.get_printer_status(client, ["toolhead"])
    assert client.query_objects.call_args.args[0] == {"toolhead": None}


async def test_get_print_status_flattens_fields(client):
    client.query_objects.return_value = {
        "status": {
            "print_stats": {"state": "printing", "filename": "a.gcode", "print_duration": 10},
            "virtual_sdcard": {"progress": 0.5, "is_active": True},
            "display_status": {"progress": 0.5},
        }
    }
    res = await tools.get_print_status(client)
    assert res["state"] == "printing"
    assert res["filename"] == "a.gcode"
    assert res["progress"] == 0.5
    assert res["is_active"] is True


async def test_get_file_defaults_to_capped_range(client):
    client.file_download.return_value = "G1 X0\n"
    res = await tools.get_file(client, "part.gcode")
    client.file_download.assert_awaited_once_with(
        "gcodes", "part.gcode", start=0, end=32767
    )
    assert res["range"] == {"start": 0, "end": 32767}
    assert res["truncated"] is False
    assert res["content"] == "G1 X0\n"


async def test_get_file_clamps_open_range_to_max_bytes(client):
    client.file_download.return_value = "x"
    await tools.get_file(client, "part.gcode", start_byte=12824, max_bytes=1024)
    client.file_download.assert_awaited_once_with(
        "gcodes", "part.gcode", start=12824, end=12824 + 1024 - 1
    )


async def test_get_file_flags_truncation_at_cap(client):
    client.file_download.return_value = "a" * 1024
    res = await tools.get_file(client, "part.gcode", max_bytes=1024)
    assert res["bytes_returned"] == 1024
    assert res["truncated"] is True


async def test_get_file_clamps_explicit_oversized_end(client):
    # The real bug: an explicit end_byte beyond the cap must still be clamped,
    # not forwarded verbatim (which would pull the whole file).
    client.file_download.return_value = "x"
    await tools.get_file(
        client, "part.gcode", start_byte=0, end_byte=9_999_999, max_bytes=1024
    )
    client.file_download.assert_awaited_once_with(
        "gcodes", "part.gcode", start=0, end=1023
    )


async def test_get_file_trims_when_server_ignores_range(client):
    # If Moonraker ignores the Range header and returns the whole file (HTTP 200),
    # the cap must still be enforced client-side.
    client.file_download.return_value = "y" * 5000
    res = await tools.get_file(client, "part.gcode", max_bytes=1024)
    assert res["bytes_returned"] == 1024
    assert res["truncated"] is True
    assert len(res["content"]) == 1024


# -- gating: refuse without confirm -----------------------------------------

@pytest.mark.parametrize(
    "fn",
    [tools.pause_print, tools.cancel_print, tools.resume_print, tools.emergency_stop,
     tools.firmware_restart, tools.restart_host],
)
async def test_unsafe_tools_refuse_without_confirm(client, fn):
    res = await fn(client)
    assert res["status"] == "confirmation_required"
    assert res["executed"] is False
    client.assert_not_awaited()


async def test_pause_print_executes_with_confirm(client):
    client.print_pause.return_value = "ok"
    res = await tools.pause_print(client, confirm=True)
    assert res == "ok"
    client.print_pause.assert_awaited_once()


async def test_emergency_stop_executes_with_confirm(client):
    client.emergency_stop.return_value = "ok"
    await tools.emergency_stop(client, confirm=True)
    client.emergency_stop.assert_awaited_once()


async def test_cancel_print_executes_with_confirm(client):
    client.print_cancel.return_value = "ok"
    await tools.cancel_print(client, confirm=True)
    client.print_cancel.assert_awaited_once()


async def test_delete_file_requires_confirm(client):
    res = await tools.delete_file(client, "old.gcode")
    assert res["status"] == "confirmation_required"
    client.file_delete.assert_not_awaited()
    await tools.delete_file(client, "old.gcode", confirm=True)
    client.file_delete.assert_awaited_once_with("gcodes", "old.gcode")


# -- run_gcode heater gating -------------------------------------------------

async def test_run_gcode_plain_runs_without_confirm(client):
    client.run_gcode.return_value = "ok"
    res = await tools.run_gcode(client, "G28")
    assert res == "ok"
    client.run_gcode.assert_awaited_once_with("G28")


async def test_run_gcode_heater_requires_confirm(client):
    res = await tools.run_gcode(client, "M104 S200")
    assert res["status"] == "confirmation_required"
    client.run_gcode.assert_not_awaited()


async def test_run_gcode_heater_runs_with_confirm(client):
    client.run_gcode.return_value = "ok"
    await tools.run_gcode(client, "M104 S200", confirm=True)
    client.run_gcode.assert_awaited_once_with("M104 S200")


# -- run_macro ---------------------------------------------------------------

async def test_run_macro_builds_command(client):
    client.run_gcode.return_value = "ok"
    await tools.run_macro(client, "PRINT_START", {"BED": 60, "EXTRUDER": 200})
    # No heater G-code token in the macro line, so it runs without confirm.
    assert client.run_gcode.call_args.args[0] == "PRINT_START BED=60 EXTRUDER=200"


async def test_run_macro_with_heater_token_requires_confirm(client):
    res = await tools.run_macro(client, "M104", {"S": 200})
    assert res["status"] == "confirmation_required"
    client.run_gcode.assert_not_awaited()


# -- start/resume now gated --------------------------------------------------

async def test_start_print_requires_confirm(client):
    res = await tools.start_print(client, "a.gcode")
    assert res["status"] == "confirmation_required"
    client.print_start.assert_not_awaited()


async def test_start_print_runs_with_confirm(client):
    client.print_start.return_value = "ok"
    await tools.start_print(client, "a.gcode", confirm=True)
    client.print_start.assert_awaited_once_with("a.gcode")


async def test_resume_print_requires_confirm(client):
    res = await tools.resume_print(client)
    assert res["status"] == "confirmation_required"
    client.print_resume.assert_not_awaited()


async def test_resume_print_runs_with_confirm(client):
    client.print_resume.return_value = "ok"
    await tools.resume_print(client, confirm=True)
    client.print_resume.assert_awaited_once()


# -- new read tools: no confirm, plain passthrough ---------------------------

async def test_get_history_passes_params(client):
    client.history_list.return_value = {"jobs": []}
    await tools.get_history(client, limit=10, order="asc")
    kwargs = client.history_list.call_args.kwargs
    assert kwargs["limit"] == 10 and kwargs["order"] == "asc"


async def test_read_tools_dont_gate(client):
    client.server_config.return_value = {}
    client.sensors_list.return_value = {}
    client.spoolman_status.return_value = {}
    await tools.get_server_config(client)
    await tools.list_sensors(client, extended=True)
    await tools.get_spoolman_status(client)
    client.server_config.assert_awaited_once()
    client.sensors_list.assert_awaited_once_with(extended=True)
    client.spoolman_status.assert_awaited_once()


async def test_set_active_spool_not_gated(client):
    client.spoolman_set_spool_id.return_value = {"spool_id": 5}
    await tools.set_active_spool(client, 5)
    client.spoolman_set_spool_id.assert_awaited_once_with(5)


# -- new destructive tools: gated --------------------------------------------

@pytest.mark.parametrize(
    "coro_factory",
    [
        lambda c: tools.reset_history_totals(c),
        lambda c: tools.delete_history_job(c, uid="abc"),
        lambda c: tools.delete_webcam(c, "cam1"),
        lambda c: tools.set_webcam(c, "Cam", uid="u1"),
    ],
)
async def test_new_destructive_tools_refuse_without_confirm(client, coro_factory):
    res = await coro_factory(client)
    assert res["status"] == "confirmation_required"
    client.history_reset_totals.assert_not_awaited()
    client.history_delete_job.assert_not_awaited()
    client.webcam_delete.assert_not_awaited()
    client.webcam_post.assert_not_awaited()


async def test_delete_history_job_requires_target(client):
    res = await tools.delete_history_job(client, confirm=True)
    assert res["status"] == "error"
    client.history_delete_job.assert_not_awaited()


async def test_delete_history_job_runs_with_confirm(client):
    client.history_delete_job.return_value = {}
    await tools.delete_history_job(client, all=True, confirm=True)
    client.history_delete_job.assert_awaited_once_with(uid=None, all=True)


async def test_set_webcam_builds_payload_with_confirm(client):
    client.webcam_post.return_value = {}
    await tools.set_webcam(client, "Cam", stream_url="http://s", enabled=True, confirm=True)
    payload = client.webcam_post.call_args.args[0]
    assert payload["name"] == "Cam"
    assert payload["stream_url"] == "http://s"
    assert payload["enabled"] is True
    assert "uid" not in payload  # None values omitted


# -- spoolman proxy: gated only for non-GET ----------------------------------

async def test_spoolman_proxy_get_not_gated(client):
    client.spoolman_proxy.return_value = {}
    await tools.spoolman_proxy(client, "GET", "/v1/spool")
    client.spoolman_proxy.assert_awaited_once()


async def test_spoolman_proxy_delete_gated(client):
    res = await tools.spoolman_proxy(client, "DELETE", "/v1/spool/3")
    assert res["status"] == "confirmation_required"
    client.spoolman_proxy.assert_not_awaited()


async def test_spoolman_proxy_delete_runs_with_confirm(client):
    client.spoolman_proxy.return_value = {}
    await tools.spoolman_proxy(client, "delete", "/v1/spool/3", confirm=True)
    client.spoolman_proxy.assert_awaited_once()
