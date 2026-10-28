"""Needs PostGIS: `docker compose --profile test run --rm tests` (IRIS_DB_DSN is set there)."""
import os

import pytest

from wsg import db, ingest
from wsg.config import Settings, UnsafeTargetError
from tests.conftest import FIXTURE, REAL_FIXTURE

pytestmark = pytest.mark.db


def settings():
    return Settings(db_dsn=os.environ.get("IRIS_DB_DSN"))


def run(conn, mapping, fixture=FIXTURE):
    return ingest.run_ingest(settings(), mapping, fixture=str(fixture), conn=conn)


def count(conn, where="true"):
    return conn.execute(f"SELECT count(*) FROM iris_staging.water_protection_zones WHERE {where}").fetchone()[0]


def test_first_load_then_duplicate_load_is_noop(db_conn, mapping):
    s1, tr = run(db_conn, mapping)
    assert (s1.status, s1.inserted, s1.updated, s1.unchanged) == ("SUCCESS", 8, 0, 0)
    assert s1.rejected == 5 and s1.repaired == 1
    s2, _ = run(db_conn, mapping)
    assert (s2.inserted, s2.updated, s2.unchanged) == (0, 0, 8)         # idempotent
    assert count(db_conn) == 8
    assert db_conn.execute("SELECT count(*) FROM iris_staging.water_protection_zones WHERE last_seen_run_id = %s",
                           (s2.run_id,)).fetchone()[0] == 8


def test_schema_invariants(db_conn, mapping):
    run(db_conn, mapping)
    assert count(db_conn, "NOT ST_IsValid(geom)") == 0
    assert count(db_conn, "ST_SRID(geom) <> 25832") == 0
    assert count(db_conn, "GeometryType(geom) <> 'MULTIPOLYGON'") == 0
    assert count(db_conn, "source_zone_id = 'SYN-001-III' AND ST_NumGeometries(geom) = 2") == 1
    assert count(db_conn, "zone_class = 'UNKNOWN' AND zone_code_raw = 'Zone III (aufgehoben)'") == 1


def test_changed_source_updates_only_that_row(db_conn, mapping, tmp_path):
    import json
    run(db_conn, mapping)
    fc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for f in fc["features"]:
        if f["properties"].get("zone_id") == "SYN-001-II":
            f["properties"]["name"] = "Renamed upstream"
    p = tmp_path / "changed.geojson"
    p.write_text(json.dumps(fc), encoding="utf-8")
    s, _ = run(db_conn, mapping, p)
    assert (s.inserted, s.updated, s.unchanged) == (0, 1, 7)


def test_runs_and_rejections_recorded(db_conn, mapping):
    s, _ = run(db_conn, mapping)
    n = db_conn.execute("SELECT count(*) FROM iris_staging.water_protection_zone_rejections WHERE run_id = %s",
                        (s.run_id,)).fetchone()[0]
    assert n == 5
    st = db_conn.execute("SELECT status FROM iris_staging.water_protection_zone_ingest_runs WHERE run_id = %s",
                         (s.run_id,)).fetchone()[0]
    assert st == "SUCCESS"


def test_empty_response_does_not_delete(db_conn, mapping, tmp_path):
    run(db_conn, mapping)
    p = tmp_path / "empty.geojson"
    p.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    s, _ = run(db_conn, mapping, p)
    assert s.status == "EMPTY" and s.exit_code == 2 and count(db_conn) == 8


def test_parcel_sql_matches_python(db_conn, mapping):
    run(db_conn, mapping)
    row = db_conn.execute(
        "SELECT z.zone_class FROM iris_staging.water_protection_zones z "
        "WHERE ST_Covers(z.geom, ST_GeomFromText('POLYGON((354020 5455020,354060 5455020,354060 5455060,"
        "354020 5455060,354020 5455020))', 25832))").fetchall()
    assert row == [("ZONE_I",)]


def test_core_schema_refused(db_conn, mapping):
    from dataclasses import replace
    with pytest.raises(UnsafeTargetError):
        ingest.run_ingest(replace(settings(), db_schema="iris_core"), mapping, fixture=str(FIXTURE), conn=db_conn)


def test_real_sample_loads_and_reloads_idempotently(db_conn, real_mapping):
    a, _ = ingest.run_ingest(settings(), real_mapping, fixture=str(REAL_FIXTURE), conn=db_conn)
    assert (a.status, a.inserted, a.rejected) == ("SUCCESS", 2, 0)
    b, _ = ingest.run_ingest(settings(), real_mapping, fixture=str(REAL_FIXTURE), conn=db_conn)
    assert (b.inserted, b.updated, b.unchanged) == (0, 0, 2)
    assert count(db_conn, "zone_class = 'ZONE_I' AND source_zone_id = 'Wasserschutzgebiet.10'") == 1
    assert count(db_conn, "ST_SRID(geom) = 25832 AND ST_Area(geom) BETWEEN 390 AND 410") == 1
