"""Confirmation gating for dangerous operations.

Dangerous MCP tools accept ``confirm: bool = False``. When ``confirm`` is not
truthy the tool must NOT touch the printer; instead it returns the dict produced
by :func:`needs_confirmation`, which describes what would happen and asks the
caller to re-invoke with ``confirm=true``. This is portable across all MCP
clients (it does not rely on spec-level elicitation/OAuth prompts) and reliably
makes the model surface a yes/no to the user.
"""

from __future__ import annotations

import re
from typing import Any

# G-code commands that command a heater. Matched case-insensitively as whole
# tokens so e.g. ``M1090`` or a comment mentioning "M104" inside a word does not
# false-trigger, while ``M104 S0`` does.
_HEATER_COMMANDS = (
    "M104",  # set hotend temp
    "M109",  # set hotend temp and wait
    "M140",  # set bed temp
    "M190",  # set bed temp and wait
    "M141",  # set chamber temp
    "M191",  # set chamber temp and wait
    "SET_HEATER_TEMPERATURE",
    "TEMPERATURE_WAIT",
)

_HEATER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(c) for c in _HEATER_COMMANDS) + r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def contains_heater_command(script: str) -> bool:
    """Return True if ``script`` contains any heater-controlling G-code command.

    Handles multi-line scripts and multiple commands separated by newlines or
    semicolons (the boundaries are covered by the surrounding non-word lookarounds).
    """
    return bool(_HEATER_RE.search(script or ""))


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
