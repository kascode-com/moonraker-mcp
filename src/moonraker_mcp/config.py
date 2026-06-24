"""Configuration: server settings plus one-or-many printer definitions.

Two ways to define printers, checked in this order:

1. A printers file (TOML or JSON) — path from ``PRINTERS_FILE``, or a
   ``printers.toml`` / ``printers.json`` found in the working directory:

       [[printers]]
       name = "voron"
       url  = "http://voron.local:7125"
       primary = true

       [[printers]]
       name = "ender"
       url  = "http://ender.local:7125"
       api_key = "..."

2. Single-printer fallback via ``MOONRAKER_URL`` / ``MOONRAKER_API_KEY`` (named
   ``default``). This keeps the original single-printer behavior working
   unchanged when no printers file is present.

Server-level settings (bind host/port, auth token, timeout) always come from the
environment.
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter version
    tomllib = None  # type: ignore[assignment]


def strip_trailing_slashes(host: str) -> str:
    """Strip trailing slashes (cf. ``stripTrailingSlashes`` in config.ts)."""
    return host.rstrip("/")


def normalize_url(url: str) -> str:
    url = strip_trailing_slashes(url.strip())
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


class PrinterConfig(BaseModel):
    """A single Moonraker/Klipper printer."""

    name: str
    url: str
    api_key: str | None = None
    primary: bool = False

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, v: str) -> str:
        return normalize_url(v)

    @field_validator("api_key", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v


class Settings(BaseSettings):
    """Server configuration, populated from environment variables / ``.env``.

    Env vars (no prefix): ``MOONRAKER_URL``, ``MOONRAKER_API_KEY``,
    ``PRINTERS_FILE``, ``MCP_HOST``, ``MCP_PORT``, ``MCP_AUTH_TOKEN``,
    ``REQUEST_TIMEOUT``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Single-printer fallback (used only when no printers file is found).
    moonraker_url: str | None = Field(default=None)
    moonraker_api_key: str | None = Field(default=None)

    # Multi-printer config file (TOML or JSON).
    printers_file: str | None = Field(default=None)

    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=8790)
    # When unset the streamable-HTTP endpoint is unauthenticated (trusted LAN only).
    mcp_auth_token: str | None = Field(default=None)

    request_timeout: float = Field(default=10.0)

    @field_validator("moonraker_api_key", "mcp_auth_token", "printers_file", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    def resolve_printers(self) -> list[PrinterConfig]:
        """Return the configured printers, from a file if present else the
        single-printer environment fallback."""
        path = self.printers_file or _default_printers_path()
        if path:
            return _finalize(_parse_printers_file(path))
        url = self.moonraker_url or "http://localhost:7125"
        return [PrinterConfig(name="default", url=url, api_key=self.moonraker_api_key, primary=True)]


_PRINTERS_FILE_CANDIDATES = ("printers.toml", "printers.json")


def _default_printers_path() -> str | None:
    for candidate in _PRINTERS_FILE_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


def _parse_printers_file(path: str) -> list[PrinterConfig]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Printers file not found: {path}")
    if path.endswith(".toml"):
        if tomllib is None:
            raise RuntimeError(
                f"Cannot read {path}: TOML support requires Python 3.11+. "
                "Use a .json printers file instead."
            )
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    else:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

    raw = data.get("printers") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path} must contain a non-empty 'printers' list.")
    return [PrinterConfig(**entry) for entry in raw]


def _finalize(printers: list[PrinterConfig]) -> list[PrinterConfig]:
    """Validate uniqueness and ensure exactly one printer is primary."""
    names = [p.name for p in printers]
    if len(set(names)) != len(names):
        raise ValueError(f"Duplicate printer names: {names}")

    primaries = [p for p in printers if p.primary]
    if len(primaries) > 1:
        raise ValueError(
            f"Only one printer may be primary; got: {[p.name for p in primaries]}"
        )
    if not primaries:
        printers[0].primary = True
    return printers
