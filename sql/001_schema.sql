-- Idempotent. Target is ALWAYS iris_staging (the application refuses any other schema).
CREATE SCHEMA IF NOT EXISTS iris_staging;

CREATE TABLE IF NOT EXISTS iris_staging.water_protection_zones (
    state_code            text        NOT NULL,
    source_zone_id        text        NOT NULL,
    source_id_origin      text        NOT NULL,           -- 'attribute:<field>' or 'wfs_feature_id'
    zone_class            text        NOT NULL DEFAULT 'UNKNOWN',  -- ZONE_I/II/III/IIIA/IIIB/UNKNOWN (explicit table, no inference)
    zone_code_raw         text,                           -- value exactly as published (NULL = not published)
    zone_name             text,
    status                text        NOT NULL DEFAULT 'UNKNOWN',  -- IN_FORCE/PLANNED/REVOKED/UNKNOWN
    status_raw            text,
    restriction_category  text        NOT NULL DEFAULT 'UNKNOWN',
    restriction_raw       text,
    extraction_rate_value numeric,                        -- NULL = not published / unparseable
    extraction_rate_unit  text,
    attributes_missing    text[]      NOT NULL DEFAULT '{}',  -- logical attributes absent in source
    quality_flags         text[]      NOT NULL DEFAULT '{}',  -- e.g. GEOMETRY_REPAIRED, ZONE_UNMAPPED
    source_attributes     jsonb       NOT NULL,           -- full raw property bag
    geom                  geometry(MultiPolygon, 25832) NOT NULL,
    source_url            text        NOT NULL,
    source_typename       text        NOT NULL,
    source_crs            text        NOT NULL,           -- declared CRS before transform
    source_updated_at     date,                           -- only if the source publishes it
    retrieved_at          timestamptz NOT NULL,
    content_hash          text        NOT NULL,
    first_loaded_at       timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    last_seen_run_id      uuid,
    PRIMARY KEY (state_code, source_zone_id)
);
CREATE INDEX IF NOT EXISTS water_protection_zones_geom_gix
    ON iris_staging.water_protection_zones USING gist (geom);

CREATE TABLE IF NOT EXISTS iris_staging.water_protection_zone_ingest_runs (
    run_id           uuid PRIMARY KEY,
    state_code       text        NOT NULL,
    started_at       timestamptz NOT NULL,
    finished_at      timestamptz,
    source_url       text        NOT NULL,
    source_typename  text        NOT NULL,
    source_crs       text,
    http_status      integer,
    status           text        NOT NULL CHECK (status IN ('SUCCESS','EMPTY','ERROR','DRY_RUN')),
    features_received integer,
    inserted         integer,
    updated          integer,
    unchanged        integer,
    rejected         integer,
    repaired         integer,
    error_message    text,
    coverage_notes   text[]      NOT NULL DEFAULT '{}',
    source_metadata  jsonb
);

CREATE TABLE IF NOT EXISTS iris_staging.water_protection_zone_rejections (
    id                bigserial PRIMARY KEY,
    run_id            uuid NOT NULL REFERENCES iris_staging.water_protection_zone_ingest_runs(run_id) ON DELETE CASCADE,
    source_feature_id text,
    reason            text NOT NULL,
    detail            text
);
