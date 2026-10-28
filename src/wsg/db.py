"""PostGIS persistence: idempotent upsert keyed by (state_code, source_zone_id). Staging only."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psycopg
import shapely
from psycopg import sql
from psycopg.types.json import Jsonb

from .config import TARGET_CRS, assert_safe_schema

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "sql" / "001_schema.sql"

_UPSERT = sql.SQL("""
INSERT INTO {s}.water_protection_zones (
    state_code, source_zone_id, source_id_origin, zone_class, zone_code_raw, zone_name,
    status, status_raw, restriction_category, restriction_raw,
    extraction_rate_value, extraction_rate_unit, attributes_missing, quality_flags,
    source_attributes, geom, source_url, source_typename, source_crs, source_updated_at,
    retrieved_at, content_hash, last_seen_run_id)
VALUES (
    %(state_code)s, %(source_zone_id)s, %(source_id_origin)s, %(zone_class)s, %(zone_code_raw)s, %(zone_name)s,
    %(status)s, %(status_raw)s, %(restriction_category)s, %(restriction_raw)s,
    %(extraction_rate_value)s, %(extraction_rate_unit)s, %(attributes_missing)s::text[], %(quality_flags)s::text[],
    %(source_attributes)s, ST_Multi(ST_GeomFromWKB(%(geom_wkb)s, 25832)), %(source_url)s, %(source_typename)s,
    %(source_crs)s, %(source_updated_at)s, %(retrieved_at)s, %(content_hash)s, %(run_id)s)
ON CONFLICT (state_code, source_zone_id) DO UPDATE SET
    source_id_origin = EXCLUDED.source_id_origin, zone_class = EXCLUDED.zone_class,
    zone_code_raw = EXCLUDED.zone_code_raw, zone_name = EXCLUDED.zone_name,
    status = EXCLUDED.status, status_raw = EXCLUDED.status_raw,
    restriction_category = EXCLUDED.restriction_category, restriction_raw = EXCLUDED.restriction_raw,
    extraction_rate_value = EXCLUDED.extraction_rate_value, extraction_rate_unit = EXCLUDED.extraction_rate_unit,
    attributes_missing = EXCLUDED.attributes_missing, quality_flags = EXCLUDED.quality_flags,
    source_attributes = EXCLUDED.source_attributes, geom = EXCLUDED.geom,
    source_url = EXCLUDED.source_url, source_typename = EXCLUDED.source_typename,
    source_crs = EXCLUDED.source_crs, source_updated_at = EXCLUDED.source_updated_at,
    retrieved_at = EXCLUDED.retrieved_at, content_hash = EXCLUDED.content_hash,
    last_seen_run_id = EXCLUDED.last_seen_run_id, updated_at = now()
WHERE {s}.water_protection_zones.content_hash IS DISTINCT FROM EXCLUDED.content_hash
RETURNING (xmax = 0) AS inserted
""")


@dataclass
class LoadStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


def connect(dsn: str):
    return psycopg.connect(dsn)


def ensure_schema(conn) -> None:
    """Create iris_staging tables if missing. Requires the postgis extension to exist already."""
    with conn.transaction():
        conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def load_records(conn, schema: str, records: list, run_id) -> LoadStats:
    """Single transaction; re-running with identical data yields inserted=0, updated=0."""
    assert_safe_schema(schema)
    q = _UPSERT.format(s=sql.Identifier(schema))
    stats = LoadStats()
    with conn.transaction():
        with conn.cursor() as cur:
            for r in records:
                params = {k: r[k] for k in (
                    "state_code", "source_zone_id", "source_id_origin", "zone_class", "zone_code_raw",
                    "zone_name", "status", "status_raw", "restriction_category", "restriction_raw",
                    "extraction_rate_value", "extraction_rate_unit", "attributes_missing",
                    "quality_flags", "source_url", "source_typename", "source_crs",
                    "source_updated_at", "retrieved_at", "content_hash")}
                params["source_attributes"] = Jsonb(r["source_attributes"])
                params["geom_wkb"] = shapely.to_wkb(r["geom"])
                params["run_id"] = run_id
                cur.execute(q, params)
                row = cur.fetchone()
                if row is None:
                    stats.unchanged += 1
                elif row[0]:
                    stats.inserted += 1
                else:
                    stats.updated += 1
            if records:
                # mark every row seen this run (also unchanged ones) so stale rows are queryable
                cur.execute(sql.SQL(
                    "UPDATE {s}.water_protection_zones SET last_seen_run_id = %s "
                    "WHERE (state_code, source_zone_id) IN (SELECT * FROM unnest(%s::text[], %s::text[]))"
                ).format(s=sql.Identifier(schema)),
                    (run_id, [r["state_code"] for r in records], [r["source_zone_id"] for r in records]))
    return stats


def record_run(conn, schema: str, summary: dict, rejections: list) -> None:
    assert_safe_schema(schema)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(sql.SQL("""
                INSERT INTO {s}.water_protection_zone_ingest_runs
                (run_id, state_code, started_at, finished_at, source_url, source_typename, source_crs,
                 http_status, status, features_received, inserted, updated, unchanged, rejected, repaired,
                 error_message, coverage_notes, source_metadata)
                VALUES (%(run_id)s, %(state_code)s, %(started_at)s, %(finished_at)s, %(source_url)s,
                 %(source_typename)s, %(source_crs)s, %(http_status)s, %(status)s, %(features_received)s,
                 %(inserted)s, %(updated)s, %(unchanged)s, %(rejected)s, %(repaired)s, %(error_message)s,
                 %(coverage_notes)s::text[], %(source_metadata)s)
            """).format(s=sql.Identifier(schema)), {**summary,
                                                     "source_metadata": Jsonb(summary.get("source_metadata") or {})})
            for rj in rejections:
                cur.execute(sql.SQL(
                    "INSERT INTO {s}.water_protection_zone_rejections (run_id, source_feature_id, reason, detail) "
                    "VALUES (%s, %s, %s, %s)").format(s=sql.Identifier(schema)),
                    (summary["run_id"], rj.source_feature_id, rj.reason, rj.detail))
