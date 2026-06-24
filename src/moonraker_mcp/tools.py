"""MCP tool implementations.

Each tool is a plain async function taking the :class:`MoonrakerClient` as its
first argument so it can be unit-tested directly with a mocked client.
:func:`register_tools` exposes them on a FastMCP instance with a ``printer``
argument that selects which configured printer's client to use (omitted →
primary), resolved via the :class:`PrinterRegistry`.

Dangerous tools take ``confirm: bool = False`` and refuse to act (returning the
:func:`needs_confirmation` payload) until called with ``confirm=true``.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .client import MoonrakerClient
from .confirmation import contains_heater_command, needs_confirmation
from .registry import PrinterRegistry

# Shared, statically-described "which printer" argument. The concrete list of
# valid names is dynamic, so it lives in the FastMCP server instructions and the
# list_printers tool rather than here.
PrinterArg = Annotated[
    str | None,
    Field(description="Configured printer name; omit to target the primary/default. Call list_printers to see options."),
]

# Default object set queried by get_printer_status. Mirrors KlippyDash's
# SUBSCRIBE_OBJECTS (klippydash/src/printer.ts) plus a few read-only extras.
DEFAULT_STATUS_OBJECTS = [
    "toolhead",
    "extruder",
    "heater_bed",
    "print_stats",
    "display_status",
    "virtual_sdcard",
    "gcode_move",
    "fan",
]


# -- read / status ----------------------------------------------------------

async def get_printer_status(client: MoonrakerClient, objects: list[str] | None = None) -> Any:
    """Query live printer objects. Defaults to a useful core set."""
    names = objects or DEFAULT_STATUS_OBJECTS
    query = {name: None for name in names}
    return await client.query_objects(query)


async def get_print_status(client: MoonrakerClient) -> dict[str, Any]:
    """Summarize the current print job (state, file, progress, times)."""
    result = await client.query_objects(
        {"print_stats": None, "virtual_sdcard": None, "display_status": None}
    )
    status = result.get("status", {}) if isinstance(result, dict) else {}
    print_stats = status.get("print_stats", {})
    vsd = status.get("virtual_sdcard", {})
    display = status.get("display_status", {})
    return {
        "state": print_stats.get("state"),
        "filename": print_stats.get("filename"),
        "message": print_stats.get("message"),
        "progress": display.get("progress", vsd.get("progress")),
        "print_duration": print_stats.get("print_duration"),
        "total_duration": print_stats.get("total_duration"),
        "filament_used": print_stats.get("filament_used"),
        "is_active": vsd.get("is_active"),
    }


async def get_temperatures(client: MoonrakerClient) -> dict[str, Any]:
    """Current and target temperatures for heaters and sensors."""
    objects_list = await client.list_objects()
    names = objects_list.get("objects", []) if isinstance(objects_list, dict) else []
    heatable = [
        n
        for n in names
        if n in ("extruder", "heater_bed")
        or n.startswith(("extruder", "heater_generic ", "temperature_sensor ", "temperature_fan "))
    ]
    if not heatable:
        heatable = ["extruder", "heater_bed"]
    result = await client.query_objects({n: None for n in heatable})
    status = result.get("status", {}) if isinstance(result, dict) else {}
    temps: dict[str, Any] = {}
    for name, data in status.items():
        if not isinstance(data, dict):
            continue
        if "temperature" in data:
            temps[name] = {
                "temperature": data.get("temperature"),
                "target": data.get("target"),
                "power": data.get("power"),
            }
    return temps


async def list_printer_objects(client: MoonrakerClient) -> Any:
    return await client.list_objects()


async def get_printer_info(client: MoonrakerClient) -> Any:
    return await client.printer_info()


async def get_server_info(client: MoonrakerClient) -> Any:
    return await client.server_info()


async def query_endstops(client: MoonrakerClient) -> Any:
    return await client.query_endstops()


async def get_temperature_history(client: MoonrakerClient) -> Any:
    return await client.temperature_store()


async def get_recent_gcode_responses(client: MoonrakerClient, count: int = 50) -> Any:
    """Recent G-code console command/response history."""
    return await client.gcode_store(count=count)


async def get_gcode_help(client: MoonrakerClient) -> Any:
    return await client.gcode_help()


# -- files ------------------------------------------------------------------

async def list_gcode_files(client: MoonrakerClient) -> Any:
    return await client.files_list(root="gcodes")


async def list_files(client: MoonrakerClient, root: str = "gcodes") -> Any:
    return await client.files_list(root=root)


async def get_file_metadata(client: MoonrakerClient, filename: str) -> Any:
    return await client.file_metadata(filename)


async def get_file(
    client: MoonrakerClient,
    filename: str,
    root: str = "gcodes",
    start_byte: int | None = None,
    end_byte: int | None = None,
    max_bytes: int = 32768,
) -> Any:
    """Fetch raw file contents (e.g. G-code) with an optional byte range.

    Read-only. G-code files are large, so the response is capped at
    ``max_bytes``; pass ``start_byte`` (from metadata's ``gcode_start_byte``)
    to skip the slicer header and sample the print body.
    """
    # Clamp the requested range so we never pull more than max_bytes, even when
    # the caller passes an explicit end_byte beyond the cap.
    if start_byte is None:
        start_byte = 0
    cap_end = start_byte + max_bytes - 1
    if end_byte is None or end_byte > cap_end:
        end_byte = cap_end

    text = await client.file_download(root, filename, start=start_byte, end=end_byte)
    # Defensively trim client-side: if Moonraker ignores the Range header and
    # returns the whole file (HTTP 200 instead of 206), enforce the cap here so
    # the response is never larger than max_bytes.
    raw = text.encode()
    if len(raw) > max_bytes:
        text = raw[:max_bytes].decode(errors="ignore")
    returned = len(text.encode())
    return {
        "filename": filename,
        "root": root,
        "range": {"start": start_byte, "end": end_byte},
        "bytes_returned": returned,
        "truncated": returned >= max_bytes,
        "content": text,
    }


async def delete_file(client: MoonrakerClient, filename: str, root: str = "gcodes", confirm: bool = False) -> Any:
    if not confirm:
        return needs_confirmation(
            "delete_file",
            detail=f"Permanently delete '{filename}' from root '{root}'. This cannot be undone.",
        )
    return await client.file_delete(root, filename)


# -- control ----------------------------------------------------------------

async def start_print(client: MoonrakerClient, filename: str, confirm: bool = False) -> Any:
    if not confirm:
        return needs_confirmation(
            "start_print", detail=f"Start a new print of '{filename}'."
        )
    return await client.print_start(filename)


async def pause_print(client: MoonrakerClient, confirm: bool = False) -> Any:
    if not confirm:
        return needs_confirmation("pause_print", detail="Pause the active print job.")
    return await client.print_pause()


async def resume_print(client: MoonrakerClient, confirm: bool = False) -> Any:
    if not confirm:
        return needs_confirmation("resume_print", detail="Resume the paused print job.")
    return await client.print_resume()


async def cancel_print(client: MoonrakerClient, confirm: bool = False) -> Any:
    if not confirm:
        return needs_confirmation(
            "cancel_print", detail="Cancel the active print job. The print cannot be resumed."
        )
    return await client.print_cancel()


async def run_gcode(client: MoonrakerClient, script: str, confirm: bool = False) -> Any:
    """Run a raw G-code script. Requires confirm=true if it commands a heater."""
    if contains_heater_command(script) and not confirm:
        return needs_confirmation(
            "run_gcode",
            detail=f"This script commands a heater and was not run: {script!r}",
        )
    return await client.run_gcode(script)


async def run_macro(
    client: MoonrakerClient,
    name: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> Any:
    """Run a Klipper macro by name, with optional KEY=VALUE params."""
    parts = [name.strip()]
    for key, value in (params or {}).items():
        parts.append(f"{key}={value}")
    script = " ".join(parts)
    if contains_heater_command(script) and not confirm:
        return needs_confirmation(
            "run_macro",
            detail=f"This macro invocation commands a heater and was not run: {script!r}",
        )
    return await client.run_gcode(script)


async def emergency_stop(client: MoonrakerClient, confirm: bool = False) -> Any:
    if not confirm:
        return needs_confirmation(
            "emergency_stop",
            detail="Immediately halt Klipper (M112). A firmware restart is required afterward.",
        )
    return await client.emergency_stop()


async def firmware_restart(client: MoonrakerClient, confirm: bool = False) -> Any:
    if not confirm:
        return needs_confirmation("firmware_restart", detail="Restart the Klipper firmware (MCU).")
    return await client.firmware_restart()


async def restart_host(client: MoonrakerClient, confirm: bool = False) -> Any:
    if not confirm:
        return needs_confirmation("restart_host", detail="Restart the Klipper host process.")
    return await client.host_restart()


# -- server config ----------------------------------------------------------

async def get_server_config(client: MoonrakerClient) -> Any:
    return await client.server_config()


# -- job history ------------------------------------------------------------

async def get_history(
    client: MoonrakerClient,
    limit: int = 50,
    start: int = 0,
    since: float | None = None,
    before: float | None = None,
    order: str = "desc",
) -> Any:
    """List past print jobs (paged, newest first by default)."""
    return await client.history_list(
        limit=limit, start=start, since=since, before=before, order=order
    )


async def get_history_totals(client: MoonrakerClient) -> Any:
    return await client.history_totals()


async def get_history_job(client: MoonrakerClient, uid: str) -> Any:
    return await client.history_job(uid)


async def reset_history_totals(client: MoonrakerClient, confirm: bool = False) -> Any:
    if not confirm:
        return needs_confirmation(
            "reset_history_totals",
            detail="Reset cumulative job totals (print time, filament used, etc.) to zero. Irreversible.",
        )
    return await client.history_reset_totals()


async def delete_history_job(
    client: MoonrakerClient, uid: str | None = None, all: bool = False, confirm: bool = False
) -> Any:
    if not uid and not all:
        return {"status": "error", "message": "Provide a 'uid' to delete, or all=true to delete every job."}
    target = "ALL job history" if all else f"history job {uid!r}"
    if not confirm:
        return needs_confirmation(
            "delete_history_job", detail=f"Permanently delete {target}. Irreversible."
        )
    return await client.history_delete_job(uid=uid, all=all)


# -- webcams ----------------------------------------------------------------

async def list_webcams(client: MoonrakerClient) -> Any:
    return await client.webcams_list()


async def get_webcam(client: MoonrakerClient, uid: str) -> Any:
    return await client.webcam_item(uid)


async def test_webcam(client: MoonrakerClient, uid: str) -> Any:
    """Test connectivity to a configured webcam (read-only check)."""
    return await client.webcam_test(uid)


async def set_webcam(
    client: MoonrakerClient,
    name: str,
    uid: str | None = None,
    stream_url: str | None = None,
    snapshot_url: str | None = None,
    service: str | None = None,
    enabled: bool | None = None,
    extra: dict[str, Any] | None = None,
    confirm: bool = False,
) -> Any:
    """Create (no uid) or update (with uid) a webcam definition. Gated because it
    writes/overwrites persistent Moonraker config."""
    if not confirm:
        verb = "update" if uid else "create"
        return needs_confirmation(
            "set_webcam", detail=f"This will {verb} the webcam definition '{name}'."
        )
    payload: dict[str, Any] = {"name": name}
    for key, value in (
        ("uid", uid),
        ("stream_url", stream_url),
        ("snapshot_url", snapshot_url),
        ("service", service),
        ("enabled", enabled),
    ):
        if value is not None:
            payload[key] = value
    if extra:
        payload.update(extra)
    return await client.webcam_post(payload)


async def delete_webcam(client: MoonrakerClient, uid: str, confirm: bool = False) -> Any:
    if not confirm:
        return needs_confirmation(
            "delete_webcam", detail=f"Permanently delete webcam {uid!r}. Irreversible."
        )
    return await client.webcam_delete(uid)


# -- sensors ----------------------------------------------------------------

async def list_sensors(client: MoonrakerClient, extended: bool = False) -> Any:
    return await client.sensors_list(extended=extended)


async def get_sensor_info(client: MoonrakerClient, sensor: str, extended: bool = False) -> Any:
    return await client.sensor_info(sensor, extended=extended)


async def get_sensor_measurements(client: MoonrakerClient, sensor: str | None = None) -> Any:
    return await client.sensor_measurements(sensor)


# -- spoolman ---------------------------------------------------------------

async def get_spoolman_status(client: MoonrakerClient) -> Any:
    return await client.spoolman_status()


async def get_active_spool(client: MoonrakerClient) -> Any:
    return await client.spoolman_get_spool_id()


async def set_active_spool(client: MoonrakerClient, spool_id: int | None) -> Any:
    """Set the active Spoolman spool id (use null to clear). Reversible, so ungated."""
    return await client.spoolman_set_spool_id(spool_id)


async def spoolman_proxy(
    client: MoonrakerClient,
    request_method: str,
    path: str,
    query: str | None = None,
    body: dict[str, Any] | None = None,
    use_v2_response: bool = False,
    confirm: bool = False,
) -> Any:
    """Proxy an arbitrary request to the Spoolman server. Non-GET methods are
    gated because they can modify/delete Spoolman data."""
    if request_method.upper() != "GET" and not confirm:
        return needs_confirmation(
            "spoolman_proxy",
            detail=f"This will send a {request_method.upper()} request to Spoolman at {path!r}, which may modify or delete data.",
        )
    return await client.spoolman_proxy(
        request_method=request_method,
        path=path,
        query=query,
        body=body,
        use_v2_response=use_v2_response,
    )


def register_tools(mcp: FastMCP, registry: PrinterRegistry) -> None:
    """Register every tool on the FastMCP instance.

    Each tool takes ``printer`` (a configured printer name; omit for the primary)
    and resolves the matching client from ``registry``. An unknown name raises
    :class:`~moonraker_mcp.registry.UnknownPrinterError`, which surfaces as a tool
    error listing the valid names.
    """

    @mcp.tool(name="list_printers")
    async def list_printers_tool() -> Any:
        """List configured printers and which one is the default (primary)."""
        return registry.describe()

    @mcp.tool(name="get_printer_status")
    async def get_printer_status_tool(
        objects: list[str] | None = None, printer: PrinterArg = None
    ) -> Any:
        """Query live printer objects (temps, position, print state, etc.). Pass a
        list of object names to query specific ones, or omit for the core set."""
        return await get_printer_status(registry.get(printer), objects)

    @mcp.tool(name="get_print_status")
    async def get_print_status_tool(printer: PrinterArg = None) -> Any:
        """Summary of the current print job: state, file, progress, and elapsed/total time."""
        return await get_print_status(registry.get(printer))

    @mcp.tool(name="get_temperatures")
    async def get_temperatures_tool(printer: PrinterArg = None) -> Any:
        """Current and target temperatures for all heaters and temperature sensors."""
        return await get_temperatures(registry.get(printer))

    @mcp.tool(name="list_printer_objects")
    async def list_printer_objects_tool(printer: PrinterArg = None) -> Any:
        """List every available printer object that can be queried."""
        return await list_printer_objects(registry.get(printer))

    @mcp.tool(name="get_printer_info")
    async def get_printer_info_tool(printer: PrinterArg = None) -> Any:
        """Klippy host info and state (ready / error / startup / shutdown)."""
        return await get_printer_info(registry.get(printer))

    @mcp.tool(name="get_server_info")
    async def get_server_info_tool(printer: PrinterArg = None) -> Any:
        """Moonraker server info and loaded components."""
        return await get_server_info(registry.get(printer))

    @mcp.tool(name="query_endstops")
    async def query_endstops_tool(printer: PrinterArg = None) -> Any:
        """Triggered/open state of each endstop."""
        return await query_endstops(registry.get(printer))

    @mcp.tool(name="get_temperature_history")
    async def get_temperature_history_tool(printer: PrinterArg = None) -> Any:
        """Cached temperature history for all monitored sensors."""
        return await get_temperature_history(registry.get(printer))

    @mcp.tool(name="get_recent_gcode_responses")
    async def get_recent_gcode_responses_tool(count: int = 50, printer: PrinterArg = None) -> Any:
        """Recent G-code console command/response history."""
        return await get_recent_gcode_responses(registry.get(printer), count)

    @mcp.tool(name="get_gcode_help")
    async def get_gcode_help_tool(printer: PrinterArg = None) -> Any:
        """List available Klipper G-code/extended commands with descriptions."""
        return await get_gcode_help(registry.get(printer))

    @mcp.tool(name="list_gcode_files")
    async def list_gcode_files_tool(printer: PrinterArg = None) -> Any:
        """List uploaded G-code files available to print."""
        return await list_gcode_files(registry.get(printer))

    @mcp.tool(name="list_files")
    async def list_files_tool(root: str = "gcodes", printer: PrinterArg = None) -> Any:
        """List files under a Moonraker root (gcodes, config, etc.)."""
        return await list_files(registry.get(printer), root)

    @mcp.tool(name="get_file_metadata")
    async def get_file_metadata_tool(filename: str, printer: PrinterArg = None) -> Any:
        """Metadata for a G-code file (size, estimated time, filament, thumbnails)."""
        return await get_file_metadata(registry.get(printer), filename)

    @mcp.tool(name="get_file")
    async def get_file_tool(
        filename: str,
        root: str = "gcodes",
        start_byte: int | None = None,
        end_byte: int | None = None,
        max_bytes: int = 32768,
        printer: PrinterArg = None,
    ) -> Any:
        """Read raw file contents (G-code, config, etc.) with an optional byte range. Read-only; capped at max_bytes."""
        return await get_file(registry.get(printer), filename, root, start_byte, end_byte, max_bytes)

    @mcp.tool(name="delete_file")
    async def delete_file_tool(
        filename: str, root: str = "gcodes", confirm: bool = False, printer: PrinterArg = None
    ) -> Any:
        """Delete a file. UNSAFE: requires confirm=true (irreversible)."""
        return await delete_file(registry.get(printer), filename, root, confirm)

    @mcp.tool(name="start_print")
    async def start_print_tool(filename: str, confirm: bool = False, printer: PrinterArg = None) -> Any:
        """Start printing the named G-code file. UNSAFE: requires confirm=true."""
        return await start_print(registry.get(printer), filename, confirm)

    @mcp.tool(name="pause_print")
    async def pause_print_tool(confirm: bool = False, printer: PrinterArg = None) -> Any:
        """Pause the active print. UNSAFE: requires confirm=true."""
        return await pause_print(registry.get(printer), confirm)

    @mcp.tool(name="resume_print")
    async def resume_print_tool(confirm: bool = False, printer: PrinterArg = None) -> Any:
        """Resume a paused print. UNSAFE: requires confirm=true."""
        return await resume_print(registry.get(printer), confirm)

    @mcp.tool(name="cancel_print")
    async def cancel_print_tool(confirm: bool = False, printer: PrinterArg = None) -> Any:
        """Cancel the active print. UNSAFE: requires confirm=true."""
        return await cancel_print(registry.get(printer), confirm)

    @mcp.tool(name="run_gcode")
    async def run_gcode_tool(script: str, confirm: bool = False, printer: PrinterArg = None) -> Any:
        """Run a raw G-code script. Requires confirm=true if it commands a heater
        (M104/M109/M140/M190/M141/M191/SET_HEATER_TEMPERATURE/TEMPERATURE_WAIT)."""
        return await run_gcode(registry.get(printer), script, confirm)

    @mcp.tool(name="run_macro")
    async def run_macro_tool(
        name: str,
        params: dict[str, Any] | None = None,
        confirm: bool = False,
        printer: PrinterArg = None,
    ) -> Any:
        """Run a Klipper macro by name with optional KEY=VALUE params. Requires
        confirm=true if the resulting command drives a heater."""
        return await run_macro(registry.get(printer), name, params, confirm)

    @mcp.tool(name="emergency_stop")
    async def emergency_stop_tool(confirm: bool = False, printer: PrinterArg = None) -> Any:
        """Emergency stop (M112) — halt the printer immediately. UNSAFE: requires confirm=true."""
        return await emergency_stop(registry.get(printer), confirm)

    @mcp.tool(name="firmware_restart")
    async def firmware_restart_tool(confirm: bool = False, printer: PrinterArg = None) -> Any:
        """Restart the Klipper firmware/MCU. UNSAFE: requires confirm=true."""
        return await firmware_restart(registry.get(printer), confirm)

    @mcp.tool(name="restart_host")
    async def restart_host_tool(confirm: bool = False, printer: PrinterArg = None) -> Any:
        """Restart the Klipper host process. UNSAFE: requires confirm=true."""
        return await restart_host(registry.get(printer), confirm)

    # -- server config ------------------------------------------------------

    @mcp.tool(name="get_server_config")
    async def get_server_config_tool(printer: PrinterArg = None) -> Any:
        """Full Moonraker configuration (parsed moonraker.conf + file paths)."""
        return await get_server_config(registry.get(printer))

    # -- job history --------------------------------------------------------

    @mcp.tool(name="get_history")
    async def get_history_tool(
        limit: int = 50,
        start: int = 0,
        since: float | None = None,
        before: float | None = None,
        order: str = "desc",
        printer: PrinterArg = None,
    ) -> Any:
        """List past print jobs (paged; newest first). 'since'/'before' are unix
        timestamps; 'order' is 'asc' or 'desc'."""
        return await get_history(registry.get(printer), limit, start, since, before, order)

    @mcp.tool(name="get_history_totals")
    async def get_history_totals_tool(printer: PrinterArg = None) -> Any:
        """Cumulative totals: total jobs, print time, filament used, longest job, etc."""
        return await get_history_totals(registry.get(printer))

    @mcp.tool(name="get_history_job")
    async def get_history_job_tool(uid: str, printer: PrinterArg = None) -> Any:
        """Details of a single past print job by its uid."""
        return await get_history_job(registry.get(printer), uid)

    @mcp.tool(name="reset_history_totals")
    async def reset_history_totals_tool(confirm: bool = False, printer: PrinterArg = None) -> Any:
        """Reset cumulative job totals to zero. UNSAFE: requires confirm=true (irreversible)."""
        return await reset_history_totals(registry.get(printer), confirm)

    @mcp.tool(name="delete_history_job")
    async def delete_history_job_tool(
        uid: str | None = None, all: bool = False, confirm: bool = False, printer: PrinterArg = None
    ) -> Any:
        """Delete a print-history entry by uid, or all entries with all=true.
        UNSAFE: requires confirm=true (irreversible)."""
        return await delete_history_job(registry.get(printer), uid, all, confirm)

    # -- webcams ------------------------------------------------------------

    @mcp.tool(name="list_webcams")
    async def list_webcams_tool(printer: PrinterArg = None) -> Any:
        """List configured webcams (name, urls, service, uid)."""
        return await list_webcams(registry.get(printer))

    @mcp.tool(name="get_webcam")
    async def get_webcam_tool(uid: str, printer: PrinterArg = None) -> Any:
        """Get a single webcam definition by uid."""
        return await get_webcam(registry.get(printer), uid)

    @mcp.tool(name="test_webcam")
    async def test_webcam_tool(uid: str, printer: PrinterArg = None) -> Any:
        """Test connectivity to a configured webcam (read-only check)."""
        return await test_webcam(registry.get(printer), uid)

    @mcp.tool(name="set_webcam")
    async def set_webcam_tool(
        name: str,
        uid: str | None = None,
        stream_url: str | None = None,
        snapshot_url: str | None = None,
        service: str | None = None,
        enabled: bool | None = None,
        extra: dict[str, Any] | None = None,
        confirm: bool = False,
        printer: PrinterArg = None,
    ) -> Any:
        """Create (omit uid) or update (with uid) a webcam definition. UNSAFE:
        requires confirm=true (writes persistent config)."""
        return await set_webcam(
            registry.get(printer), name, uid, stream_url, snapshot_url, service, enabled, extra, confirm
        )

    @mcp.tool(name="delete_webcam")
    async def delete_webcam_tool(uid: str, confirm: bool = False, printer: PrinterArg = None) -> Any:
        """Delete a webcam definition by uid. UNSAFE: requires confirm=true (irreversible)."""
        return await delete_webcam(registry.get(printer), uid, confirm)

    # -- sensors ------------------------------------------------------------

    @mcp.tool(name="list_sensors")
    async def list_sensors_tool(extended: bool = False, printer: PrinterArg = None) -> Any:
        """List configured sensors and their latest values. extended=true adds
        parameter/history field metadata."""
        return await list_sensors(registry.get(printer), extended)

    @mcp.tool(name="get_sensor_info")
    async def get_sensor_info_tool(sensor: str, extended: bool = False, printer: PrinterArg = None) -> Any:
        """Info and latest values for a single sensor by name."""
        return await get_sensor_info(registry.get(printer), sensor, extended)

    @mcp.tool(name="get_sensor_measurements")
    async def get_sensor_measurements_tool(sensor: str | None = None, printer: PrinterArg = None) -> Any:
        """Stored measurement history for one sensor (by name) or all sensors."""
        return await get_sensor_measurements(registry.get(printer), sensor)

    # -- spoolman -----------------------------------------------------------

    @mcp.tool(name="get_spoolman_status")
    async def get_spoolman_status_tool(printer: PrinterArg = None) -> Any:
        """Spoolman connection status, pending reports, and current spool id."""
        return await get_spoolman_status(registry.get(printer))

    @mcp.tool(name="get_active_spool")
    async def get_active_spool_tool(printer: PrinterArg = None) -> Any:
        """The currently tracked Spoolman spool id."""
        return await get_active_spool(registry.get(printer))

    @mcp.tool(name="set_active_spool")
    async def set_active_spool_tool(spool_id: int | None, printer: PrinterArg = None) -> Any:
        """Set the active Spoolman spool id (null to clear). Reversible, so not gated."""
        return await set_active_spool(registry.get(printer), spool_id)

    @mcp.tool(name="spoolman_proxy")
    async def spoolman_proxy_tool(
        request_method: str,
        path: str,
        query: str | None = None,
        body: dict[str, Any] | None = None,
        use_v2_response: bool = False,
        confirm: bool = False,
        printer: PrinterArg = None,
    ) -> Any:
        """Proxy a raw request to the Spoolman server (e.g. GET '/v1/spool' to list
        spools). Non-GET methods are UNSAFE and require confirm=true."""
        return await spoolman_proxy(
            registry.get(printer), request_method, path, query, body, use_v2_response, confirm
        )
