import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from wsg.config import AttributeMapping, Settings

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "wsg_sample.geojson"                 # synthetic
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "real_sample_ogcapi.json"       # 2 real records


@pytest.fixture(scope="session")
def settings():
    return Settings()


@pytest.fixture(scope="session")
def mapping():
    """Mapping for the SYNTHETIC fixture (assumed field names)."""
    return AttributeMapping.from_file(ROOT / "tests" / "fixtures" / "mapping_synthetic.json")


@pytest.fixture(scope="session")
def real_mapping():
    """The shipped mapping, verified against the live layer."""
    return AttributeMapping.from_file(ROOT / "config" / "attribute_mapping.json")


@pytest.fixture(scope="session")
def fc():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def built(fc, settings, mapping):
    from wsg.transform import build_records
    return build_records(fc["features"], mapping=mapping, settings=settings,
                         source_crs="urn:ogc:def:crs:EPSG::25832", source_url="fixture",
                         retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


@pytest.fixture
def db_conn():
    dsn = os.environ.get("IRIS_DB_DSN")
    if not dsn:
        pytest.skip("IRIS_DB_DSN not set")
    from wsg import db
    conn = db.connect(dsn)
    db.ensure_schema(conn)
    with conn.transaction():
        conn.execute("TRUNCATE iris_staging.water_protection_zone_rejections, "
                     "iris_staging.water_protection_zone_ingest_runs, iris_staging.water_protection_zones CASCADE")
    yield conn
    conn.close()
