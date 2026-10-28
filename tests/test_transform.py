import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest
import shapely
from pyproj import Transformer
from shapely.geometry import MultiPolygon, shape
from shapely.ops import transform as shp_transform

from wsg.transform import build_records, content_hash, parse_decimal

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def by_id(built):
    return {r["source_zone_id"]: r for r in built.records}


def test_accept_and_reject_sets(built):
    assert set(by_id(built)) == {"SYN-001-I", "SYN-001-II", "SYN-001-III", "SYN-002-IIIA",
                                 "SYN-003-BOWTIE", "SYN-005-TRAP", "SYN-007-NOZONE", "SYN-009-RATE"}
    reasons = {r.source_feature_id: r.reason for r in built.rejections}
    assert reasons["SYN-004-NULLGEOM"] == "EMPTY_GEOMETRY"
    assert reasons["SYN-006-POINT"] == "NON_POLYGON_GEOMETRY"
    assert reasons["SYN-008-WRONGCRS"] == "OUT_OF_EXPECTED_EXTENT"
    assert reasons["SYN-001-I"] == "DUPLICATE_SOURCE_ID"
    assert "MISSING_SOURCE_ID" in reasons.values()
    assert len(built.records) + len(built.rejections) == 13


def test_all_geometries_valid_multipolygon_25832_extent(built):
    for r in built.records:
        g = r["geom"]
        assert isinstance(g, MultiPolygon) and g.is_valid and not g.is_empty
        assert not shapely.has_z(g)


def test_multipart_preserved(built):
    assert len(by_id(built)["SYN-001-III"]["geom"].geoms) == 2


def test_bowtie_repaired_and_flagged(built):
    r = by_id(built)["SYN-003-BOWTIE"]
    assert "GEOMETRY_REPAIRED" in r["quality_flags"] and r["geom"].is_valid and r["geom"].area > 0
    assert built.repaired == 1


def test_zone_classification_traceable_not_inferred(built):
    recs = by_id(built)
    assert recs["SYN-001-I"]["zone_class"] == "ZONE_I" and recs["SYN-001-I"]["zone_code_raw"] == "I"
    trap = recs["SYN-005-TRAP"]
    assert trap["zone_class"] == "UNKNOWN" and trap["zone_code_raw"] == "Zone III (aufgehoben)"
    assert "ZONE_UNMAPPED" in trap["quality_flags"] and trap["status"] == "REVOKED"


def test_absent_attributes_explicit(built):
    r = by_id(built)["SYN-007-NOZONE"]
    assert r["zone_class"] == "UNKNOWN" and r["zone_code_raw"] is None and r["status"] == "UNKNOWN"
    assert {"zone", "status", "restriction", "extraction_rate"} <= set(r["attributes_missing"])
    assert r["extraction_rate_value"] is None


def test_published_rate_kept_unmapped_restriction_unknown(built):
    r = by_id(built)["SYN-009-RATE"]
    assert r["extraction_rate_value"] == 1200.0 and r["extraction_rate_unit"] == "m3/a"
    assert r["restriction_category"] == "UNKNOWN" and r["restriction_raw"] == "Nr. 3"
    assert "RESTRICTION_UNMAPPED" in r["quality_flags"]


def test_parse_decimal_strict():
    assert parse_decimal("1200,5") == 1200.5 and parse_decimal(3) == 3.0
    assert parse_decimal("1.200,5") is None and parse_decimal("abc") is None and parse_decimal(True) is None


def test_crs_transform_roundtrip(fc, settings, mapping):
    """Same fixture expressed in EPSG:4326 must come back to the original EPSG:25832 coordinates."""
    to4326 = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    ok = [deepcopy(f) for f in fc["features"] if f["properties"].get("zone_id") == "SYN-001-II"]
    g = shp_transform(to4326.transform, shape(ok[0]["geometry"]))
    ok[0]["geometry"] = json.loads(shapely.to_geojson(g))
    res = build_records(ok, mapping=mapping, settings=settings, source_crs="EPSG:4326",
                        source_url="t", retrieved_at=NOW)
    assert len(res.records) == 1
    assert res.records[0]["geom"].area == pytest.approx(200 * 100, rel=1e-4)
    assert res.records[0]["geom"].bounds == pytest.approx((354100, 5455000, 354300, 5455100), abs=0.05)


def test_hash_stable_and_sensitive(fc, settings, mapping, built):
    again = build_records(fc["features"], mapping=mapping, settings=settings,
                          source_crs="urn:ogc:def:crs:EPSG::25832", source_url="other-url",
                          retrieved_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert [r["content_hash"] for r in built.records] == [r["content_hash"] for r in again.records]
    rec = dict(built.records[0]); rec["zone_name"] = "changed"
    assert content_hash(rec) != built.records[0]["content_hash"]
