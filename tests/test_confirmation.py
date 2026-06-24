from moonraker_mcp.confirmation import contains_heater_command, needs_confirmation


def test_detects_basic_heater_commands():
    assert contains_heater_command("M104 S200")
    assert contains_heater_command("M109 S210")
    assert contains_heater_command("M140 S60")
    assert contains_heater_command("M190 S60")
    assert contains_heater_command("M141 S40")
    assert contains_heater_command("M191 S40")
    assert contains_heater_command("SET_HEATER_TEMPERATURE HEATER=extruder TARGET=200")
    assert contains_heater_command("TEMPERATURE_WAIT SENSOR=extruder MINIMUM=200")


def test_detects_heater_command_case_insensitive_and_multiline():
    assert contains_heater_command("g28\nm104 s200\ng1 x10")
    assert contains_heater_command("set_heater_temperature heater=extruder target=0")


def test_ignores_non_heater_gcode():
    assert not contains_heater_command("G28")
    assert not contains_heater_command("G1 X10 Y10 F3000")
    assert not contains_heater_command("BED_MESH_CALIBRATE")
    assert not contains_heater_command("")


def test_does_not_false_match_embedded_tokens():
    # M1090 / M1040 are not heater commands; substring match must not trigger.
    assert not contains_heater_command("M1090")
    assert not contains_heater_command("M1040 S1")
    assert not contains_heater_command("SET_HEATER_TEMPERATURE_FOO")


def test_needs_confirmation_payload():
    payload = needs_confirmation("emergency_stop", detail="halt")
    assert payload["status"] == "confirmation_required"
    assert payload["executed"] is False
    assert payload["action"] == "emergency_stop"
    assert payload["detail"] == "halt"
    assert "confirm=true" in payload["message"]
