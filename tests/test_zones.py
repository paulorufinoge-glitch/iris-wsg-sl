from wsg.config import UNKNOWN, AttributeMapping, UnsafeTargetError, assert_safe_schema
from wsg.zones import map_value
import pytest


def table(mapping):
    return mapping.value_maps["zone"]


def test_exact_and_normalised_lookup(mapping):
    assert map_value("Zone III", table(mapping)) == ("ZONE_III", True)
    assert map_value("  zone   iiia ", table(mapping)) == ("ZONE_IIIA", True)   # whitespace/case only
    assert map_value("I", table(mapping)) == ("ZONE_I", True)


@pytest.mark.parametrize("raw", ["Zone III (aufgehoben)", "IIIB-Entwurf", "Zone IV", "Schutzzone I/II", "III–A"])
def test_no_substring_inference(mapping, raw):
    """A fragile substring rule would map these to a real zone; we must say UNKNOWN."""
    assert map_value(raw, table(mapping)) == (UNKNOWN, False)


def test_absent_value_is_unknown(mapping):
    assert map_value(None, table(mapping)) == (UNKNOWN, False)


def test_only_iris_staging_is_writable():
    assert_safe_schema("iris_staging")
    for bad in ("iris_core", "public", "iris_staging; drop table x"):
        with pytest.raises(UnsafeTargetError):
            assert_safe_schema(bad)
