"""Async HTTP client for the Moonraker REST API.

Moonraker's HTTP API is "REST-ish": successful responses are wrapped as
``{"result": <data>}``. This client unwraps that envelope and normalizes errors
(HTTP failures, and the common "Klippy is not ready" 503) into a single
:class:`MoonrakerError` with a readable message.

Only request/response endpoints are used — MCP tools are request/response, so we
don't need the websocket/subscription transport.
"""

from __future__ import annotations

from typing import Any

import httpx


class MoonrakerError(Exception):
    """Raised when Moonraker returns an error or is unreachable."""


class MoonrakerClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        headers: dict[str, str] = {}
        if api_key:
            headers["X-Api-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> MoonrakerClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- core ---------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Issue a request and return the unwrapped ``result`` payload."""
        try:
            resp = await self._client.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            raise MoonrakerError(f"Could not reach Moonraker at {self._client.base_url}: {exc}") from exc

        # Moonraker reports errors as a JSON body with an "error" key, often with
        # a non-2xx status (e.g. 503 when Klippy is disconnected/not ready).
        data: Any = None
        try:
            data = resp.json()
        except ValueError:
            data = None

        if resp.is_error:
            message = _extract_error(data) or resp.text or f"HTTP {resp.status_code}"
            raise MoonrakerError(f"Moonraker error ({resp.status_code}): {message}")

        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, *, json: dict[str, Any] | None = None) -> Any:
        return await self.request("POST", path, json=json)

    # -- printer ------------------------------------------------------------

    async def printer_info(self) -> Any:
        return await self.get("/printer/info")

    async def list_objects(self) -> Any:
        return await self.get("/printer/objects/list")

    async def query_objects(self, objects: dict[str, Any]) -> Any:
        return await self.post("/printer/objects/query", json={"objects": objects})

    async def query_endstops(self) -> Any:
        return await self.get("/printer/query_endstops/status")

    async def run_gcode(self, script: str) -> Any:
        return await self.post("/printer/gcode/script", json={"script": script})

    async def gcode_help(self) -> Any:
        return await self.get("/printer/gcode/help")

    async def emergency_stop(self) -> Any:
        return await self.post("/printer/emergency_stop")

    async def firmware_restart(self) -> Any:
        return await self.post("/printer/firmware_restart")

    async def host_restart(self) -> Any:
        return await self.post("/printer/restart")

    # -- print job ----------------------------------------------------------

    async def print_start(self, filename: str) -> Any:
        return await self.post("/printer/print/start", json={"filename": filename})

    async def print_pause(self) -> Any:
        return await self.post("/printer/print/pause")

    async def print_resume(self) -> Any:
        return await self.post("/printer/print/resume")

    async def print_cancel(self) -> Any:
        return await self.post("/printer/print/cancel")

    # -- server -------------------------------------------------------------

    async def server_info(self) -> Any:
        return await self.get("/server/info")

    async def server_config(self) -> Any:
        return await self.get("/server/config")

    async def temperature_store(self) -> Any:
        return await self.get("/server/temperature_store")

    async def gcode_store(self, count: int | None = None) -> Any:
        params = {"count": count} if count is not None else None
        return await self.get("/server/gcode_store", params=params)

    # -- job history --------------------------------------------------------

    async def history_list(self, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return await self.get("/server/history/list", params=clean or None)

    async def history_totals(self) -> Any:
        return await self.get("/server/history/totals")

    async def history_job(self, uid: str) -> Any:
        return await self.get("/server/history/job", params={"uid": uid})

    async def history_reset_totals(self) -> Any:
        return await self.post("/server/history/reset_totals")

    async def history_delete_job(self, uid: str | None = None, all: bool = False) -> Any:
        params: dict[str, Any] = {"all": all}
        if uid is not None:
            params["uid"] = uid
        return await self.request("DELETE", "/server/history/job", params=params)

    # -- webcams ------------------------------------------------------------

    async def webcams_list(self) -> Any:
        return await self.get("/server/webcams/list")

    async def webcam_item(self, uid: str) -> Any:
        return await self.get("/server/webcams/item", params={"uid": uid})

    async def webcam_test(self, uid: str) -> Any:
        return await self.request("POST", "/server/webcams/test", params={"uid": uid})

    async def webcam_post(self, payload: dict[str, Any]) -> Any:
        return await self.post("/server/webcams/item", json=payload)

    async def webcam_delete(self, uid: str) -> Any:
        return await self.request("DELETE", "/server/webcams/item", params={"uid": uid})

    # -- sensors ------------------------------------------------------------

    async def sensors_list(self, extended: bool = False) -> Any:
        return await self.get("/server/sensors/list", params={"extended": extended})

    async def sensor_info(self, sensor: str, extended: bool = False) -> Any:
        return await self.get(
            "/server/sensors/info", params={"sensor": sensor, "extended": extended}
        )

    async def sensor_measurements(self, sensor: str | None = None) -> Any:
        params = {"sensor": sensor} if sensor is not None else None
        return await self.get("/server/sensors/measurements", params=params)

    # -- spoolman -----------------------------------------------------------

    async def spoolman_status(self) -> Any:
        return await self.get("/server/spoolman/status")

    async def spoolman_get_spool_id(self) -> Any:
        return await self.get("/server/spoolman/spool_id")

    async def spoolman_set_spool_id(self, spool_id: int | None) -> Any:
        return await self.post("/server/spoolman/spool_id", json={"spool_id": spool_id})

    async def spoolman_proxy(
        self,
        request_method: str,
        path: str,
        query: str | None = None,
        body: dict[str, Any] | None = None,
        use_v2_response: bool = False,
    ) -> Any:
        payload: dict[str, Any] = {
            "request_method": request_method,
            "path": path,
            "use_v2_response": use_v2_response,
        }
        if query is not None:
            payload["query"] = query
        if body is not None:
            payload["body"] = body
        return await self.post("/server/spoolman/proxy", json=payload)

    # -- files --------------------------------------------------------------

    async def files_list(self, root: str = "gcodes") -> Any:
        return await self.get("/server/files/list", params={"root": root})

    async def file_metadata(self, filename: str) -> Any:
        return await self.get("/server/files/metadata", params={"filename": filename})

    async def file_delete(self, root: str, path: str) -> Any:
        # DELETE /server/files/{root}/{path}
        return await self.request("DELETE", f"/server/files/{root}/{path}")

    async def file_download(
        self,
        root: str,
        path: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> str:
        """Fetch raw file contents from /server/files/{root}/{path}.

        Unlike :meth:`request`, this returns the body verbatim — G-code is
        text/plain, not the JSON ``{"result": ...}`` envelope. An optional byte
        range maps to an HTTP ``Range`` header so callers can sample a slice
        (e.g. the first layer) instead of pulling the whole file.
        """
        headers: dict[str, str] = {}
        if start is not None or end is not None:
            lo = "" if start is None else str(start)
            hi = "" if end is None else str(end)
            headers["Range"] = f"bytes={lo}-{hi}"
        try:
            resp = await self._client.get(f"/server/files/{root}/{path}", headers=headers)
        except httpx.HTTPError as exc:
            raise MoonrakerError(f"Could not reach Moonraker at {self._client.base_url}: {exc}") from exc

        if resp.is_error:
            try:
                data = resp.json()
            except ValueError:
                data = None
            message = _extract_error(data) or resp.text or f"HTTP {resp.status_code}"
            raise MoonrakerError(f"Moonraker error ({resp.status_code}): {message}")
        return resp.text


def _extract_error(data: Any) -> str | None:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("message") or str(err)
        if isinstance(err, str):
            return err
    return None
