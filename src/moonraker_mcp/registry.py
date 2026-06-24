"""Holds one :class:`MoonrakerClient` per configured printer and resolves a
printer name (or ``None`` → primary) to its client."""

from __future__ import annotations

from .client import MoonrakerClient
from .config import PrinterConfig


class UnknownPrinterError(ValueError):
    """Raised when a tool is asked to act on a printer name that isn't configured."""


class PrinterRegistry:
    def __init__(self, printers: list[PrinterConfig], timeout: float = 10.0) -> None:
        if not printers:
            raise ValueError("At least one printer must be configured.")
        self._clients: dict[str, MoonrakerClient] = {
            p.name: MoonrakerClient(base_url=p.url, api_key=p.api_key, timeout=timeout)
            for p in printers
        }
        self._configs: dict[str, PrinterConfig] = {p.name: p for p in printers}
        self.names: list[str] = list(self._clients)
        self.primary: str = next(p.name for p in printers if p.primary)

    def get(self, name: str | None = None) -> MoonrakerClient:
        """Resolve a printer name to its client. ``None``/blank → the primary."""
        key = name or self.primary
        client = self._clients.get(key)
        if client is None:
            raise UnknownPrinterError(
                f"Unknown printer {name!r}. Configured printers: {', '.join(self.names)}."
            )
        return client

    def describe(self) -> list[dict[str, object]]:
        """Summary for the ``list_printers`` tool."""
        return [
            {"name": p.name, "url": p.url, "primary": p.primary}
            for p in self._configs.values()
        ]

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
