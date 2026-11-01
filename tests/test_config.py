import json
import sys

import pytest

from moonraker_mcp.config import PrinterConfig, Settings, _finalize


def test_single_printer_fallback_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no printers.toml/json here
    s = Settings(moonraker_url="printer.local:7125", moonraker_api_key="k")
    printers = s.resolve_printers()
    assert len(printers) == 1
    p = printers[0]
    assert p.name == "default"
    assert p.url == "http://printer.local:7125"  # normalized: scheme added
    assert p.api_key == "k"
    assert p.primary is True


def test_default_localhost_when_nothing_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Settings()
    printers = s.resolve_printers()
    assert printers[0].url == "http://localhost:7125"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="TOML printers files need tomllib (Python 3.11+)")
def test_loads_toml_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "printers.toml").write_text(
        """
        [[printers]]
        name = "voron"
        url = "voron.local:7125"
        primary = true

        [[printers]]
        name = "ender"
        url = "http://ender.local:7125"
        """
    )
    s = Settings()
    printers = s.resolve_printers()
    assert [p.name for p in printers] == ["voron", "ender"]
    assert printers[0].url == "http://voron.local:7125"
    assert printers[0].primary is True
    assert printers[1].primary is False


def test_loads_json_file_via_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"printers": [
        {"name": "a", "url": "http://a:7125"},
        {"name": "b", "url": "http://b:7125"},
    ]}))
    s = Settings(printers_file=str(path))
    printers = s.resolve_printers()
    assert [p.name for p in printers] == ["a", "b"]
    # No explicit primary -> first becomes primary.
    assert printers[0].primary is True


def test_duplicate_names_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        _finalize([
            PrinterConfig(name="x", url="http://a"),
            PrinterConfig(name="x", url="http://b"),
        ])


def test_multiple_primaries_rejected():
    with pytest.raises(ValueError, match="primary"):
        _finalize([
            PrinterConfig(name="x", url="http://a", primary=True),
            PrinterConfig(name="y", url="http://b", primary=True),
        ])
