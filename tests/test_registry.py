import pytest

from moonraker_mcp.config import PrinterConfig
from moonraker_mcp.registry import PrinterRegistry, UnknownPrinterError


@pytest.fixture
def registry():
    return PrinterRegistry(
        [
            PrinterConfig(name="voron", url="http://voron:7125", primary=True),
            PrinterConfig(name="ender", url="http://ender:7125"),
        ],
        timeout=5.0,
    )


def test_names_and_primary(registry):
    assert registry.names == ["voron", "ender"]
    assert registry.primary == "voron"


def test_get_primary_when_none(registry):
    assert registry.get() is registry.get("voron")
    assert registry.get(None) is registry.get("voron")


def test_get_by_name_returns_distinct_clients(registry):
    assert registry.get("voron") is not registry.get("ender")


def test_unknown_printer_raises(registry):
    with pytest.raises(UnknownPrinterError, match="voron, ender"):
        registry.get("missing")


def test_describe(registry):
    described = registry.describe()
    assert {d["name"] for d in described} == {"voron", "ender"}
    primary = [d for d in described if d["primary"]]
    assert len(primary) == 1 and primary[0]["name"] == "voron"


def test_empty_rejected():
    with pytest.raises(ValueError):
        PrinterRegistry([], timeout=1.0)
