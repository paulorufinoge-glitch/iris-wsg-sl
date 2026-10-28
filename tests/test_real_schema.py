"""Two REAL records from the GDI-Saarland layer (CC-BY 4.0) run through the shipped mapping."""
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from wsg.config import Settings
from wsg.transform import build_records
from tests.conftest import REAL_FIXTURE

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def run(features, real_mapping):
    return build_records(features, mapping=real_mapping, settings=Settings(), source_crs="EPSG:4326",
                         source_url="real-sample", retrieved_at=NOW)


@pytest.fixture(scope="module")
def features():
    return json.loads(REAL_FIXTURE.read_text(encoding="utf-8"))["features"]


def test_real_records_accepted_with_traceable_classes(features, real_mapping):
    res = run(features, real_mapping)
    assert res.rejections == []
    recs = {r["source_zone_id"]: r for r in res.records}
    assert set(recs) == {"Wasserschutzgebiet.4", "Wasserschutzgebiet.10"}
    assert recs["Wasserschutzgebiet.4"]["zone_class"] == "ZONE_II"
    assert recs["Wasserschutzgebiet.10"]["zone_class"] == "ZONE_I"
    assert recs["Wasserschutzgebiet.10"]["zone_code_raw"] == "I"
    assert recs["Wasserschutzgebiet.10"]["source_id_origin"] == "attribute:gml_id"
    assert recs["Wasserschutzgebiet.4"]["zone_name"] == "WSG Elm - Sprengen"


def test_unpublished_attributes_are_explicit(features, real_mapping):
    for r in run(features, real_mapping).records:
        assert {"status", "restriction", "extraction_rate", "updated"} <= set(r["attributes_missing"])
        assert r["status"] == "UNKNOWN" and r["restriction_category"] == "UNKNOWN"
        assert r["extraction_rate_value"] is None


def test_reprojected_area_matches_published_shape_area(features, real_mapping):
    """Independent check of CRS handling: the service publishes SHAPE_Area for each record."""
    for r in run(features, real_mapping).records:
        published = r["source_attributes"]["SHAPE_Area"]
        assert r["geom"].area == pytest.approx(published, rel=0.02)
        assert r["geom"].bounds[0] > 300000       # metres (EPSG:25832), not degrees


def test_unlisted_zone_value_becomes_unknown_not_guessed(features, real_mapping):
    f = deepcopy(features[:1])
    f[0]["properties"]["SCHUTZZONE"] = "IIIA"      # not observed in the data I inspected
    r = run(f, real_mapping).records[0]
    assert r["zone_class"] == "UNKNOWN" and r["zone_code_raw"] == "IIIA"
    assert "ZONE_UNMAPPED" in r["quality_flags"]


def test_state_code_comes_from_land_attribute(features, real_mapping):
    """The live layer mixes SL (350) and RLP (3) rows; the key must carry the published state."""
    ok = run(deepcopy(features[:1]), real_mapping).records[0]
    assert ok["state_code"] == "SL" and "STATE_OTHER_THAN_REQUESTED" not in ok["quality_flags"]
    f = deepcopy(features[:1])
    f[0]["properties"]["LAND"] = "RLP"
    other = run(f, real_mapping).records[0]
    assert other["state_code"] == "RLP" and "STATE_OTHER_THAN_REQUESTED" in other["quality_flags"]


def test_same_id_in_two_states_is_not_a_duplicate(features, real_mapping):
    a, b = deepcopy(features[0]), deepcopy(features[0])
    b["properties"]["LAND"] = "RLP"
    res = run([a, b], real_mapping)
    assert len(res.records) == 2 and res.rejections == []


def test_zone_b_maps_from_published_value(features, real_mapping):
    f = deepcopy(features[:1])
    f[0]["properties"]["SCHUTZZONE"] = "B"
    r = run(f, real_mapping).records[0]
    assert r["zone_class"] == "ZONE_B" and r["zone_code_raw"] == "B" and "ZONE_UNMAPPED" not in r["quality_flags"]
