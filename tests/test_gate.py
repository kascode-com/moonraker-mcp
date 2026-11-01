import pytest

from moonraker_mcp.config import Settings
from moonraker_mcp.confirmation import DEFAULT_GATE, GcodeGate, parse_command_list


@pytest.mark.parametrize(
    "script",
    ["M104 S200", "PID_CALIBRATE HEATER=extruder TARGET=220", "firmware_restart", "G28\nSAVE_CONFIG", "M112"],
)
def test_default_gate_matches_heater_and_restart(script):
    assert DEFAULT_GATE.match(script)


@pytest.mark.parametrize("script", ["G28", "RESTART_FOO", "MMU_HEATER", "SAVE_VARIABLE VARIABLE=z VALUE=0", ""])
def test_default_gate_ignores_other_commands(script):
    assert DEFAULT_GATE.match(script) is None


def test_extra_commands_are_gated_case_insensitively():
    gate = GcodeGate(["M4029"])
    assert gate.match("g28\nm4029 S60") == "M4029"
    assert gate.match("M40290") is None
    assert "M104" in gate.commands


def test_parse_command_list_accepts_commas_and_spaces():
    assert parse_command_list("M4029, m901  MMU_HEATER") == ("M4029", "M901", "MMU_HEATER")
    assert parse_command_list("") == ()


def test_parse_command_list_rejects_non_tokens():
    with pytest.raises(ValueError):
        parse_command_list("M104 S0;M4029|.*")


def test_settings_build_gate_from_env(monkeypatch):
    monkeypatch.setenv("CONFIRM_GCODE_COMMANDS", "M4029,M901")
    gate = Settings().gcode_gate()
    assert gate.match("M901") == "M901"
