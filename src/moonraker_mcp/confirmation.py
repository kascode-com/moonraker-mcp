"""Confirmation gating for dangerous operations.

Dangerous MCP tools accept ``confirm: bool = False``. When ``confirm`` is not
truthy the tool must NOT touch the printer; instead it returns the dict produced
by :func:`needs_confirmation`, which describes what would happen and asks the
caller to re-invoke with ``confirm=true``. This is portable across all MCP
clients (it does not rely on spec-level elicitation/OAuth prompts) and reliably
makes the model surface a yes/no to the user.

Raw G-code and macro calls are gated by a :class:`GcodeGate`: a set of command
names that require confirmation. The defaults cover heaters plus commands that
restart or halt Klipper; printer-specific macros that heat or restart (e.g. a
vendor ``M4029`` that heats the nozzle) are added through ``CONFIRM_GCODE_COMMANDS``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# G-code commands that command a heater. Matched case-insensitively as whole
# tokens so e.g. ``M1090`` or a comment mentioning "M104" inside a word does not
# false-trigger, while ``M104 S0`` does.
HEATER_COMMANDS = (
    "M104",  # set hotend temp
    "M109",  # set hotend temp and wait
    "M140",  # set bed temp
    "M190",  # set bed temp and wait
    "M141",  # set chamber temp
    "M191",  # set chamber temp and wait
    "SET_HEATER_TEMPERATURE",
    "TEMPERATURE_WAIT",
    "PID_CALIBRATE",  # heats to the target and cycles around it
)

# Commands that restart or halt Klipper; sent as G-code they bypass the gated
# firmware_restart / restart_host / emergency_stop tools.
RESTART_COMMANDS = (
    "FIRMWARE_RESTART",
    "RESTART",
    "SAVE_CONFIG",  # writes printer.cfg and restarts the host
    "M112",  # emergency stop
)

DEFAULT_GATED_COMMANDS = HEATER_COMMANDS + RESTART_COMMANDS

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]+$")


def parse_command_list(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Parse a comma/whitespace separated list of G-code command names."""
    if value is None:
        return ()
    items = re.split(r"[,\s]+", value) if isinstance(value, str) else list(value)
    commands = tuple(item.strip().upper() for item in items if item and item.strip())
    invalid = [c for c in commands if not _TOKEN_RE.match(c)]
    if invalid:
        raise ValueError(f"Invalid G-code command names: {invalid}")
    return commands


class GcodeGate:
    """Detects G-code scripts that contain commands requiring confirmation."""

    def __init__(self, extra: Iterable[str] = ()) -> None:
        commands = dict.fromkeys(c.upper() for c in (*DEFAULT_GATED_COMMANDS, *extra))
        self.commands: tuple[str, ...] = tuple(commands)
        self._re = re.compile(
            r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(c) for c in self.commands) + r")(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )

    def match(self, script: str) -> str | None:
        """Return the first gated command found in ``script`` (upper-cased), or None.

        Handles multi-line scripts and multiple commands separated by newlines or
        semicolons (the boundaries are covered by the surrounding non-word lookarounds).
        """
        found = self._re.search(script or "")
        return found.group(1).upper() if found else None


DEFAULT_GATE = GcodeGate()
_HEATER_GATE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(c) for c in HEATER_COMMANDS) + r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def contains_heater_command(script: str) -> bool:
    """Return True if ``script`` contains any heater-controlling G-code command."""
    return bool(_HEATER_GATE_RE.search(script or ""))


def needs_confirmation(action: str, detail: str = "") -> dict[str, Any]:
    """Build the structured "confirmation required" payload returned by a gated
    tool when ``confirm`` was not supplied."""
    message = (
        f"CONFIRMATION REQUIRED: '{action}' is a potentially unsafe operation and "
        f"was NOT executed. Re-invoke this tool with confirm=true to proceed."
    )
    payload: dict[str, Any] = {
        "status": "confirmation_required",
        "action": action,
        "executed": False,
        "message": message,
    }
    if detail:
        payload["detail"] = detail
    return payload
