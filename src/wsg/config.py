"""Configuration. Environment-driven; no secrets in code. Stdlib only."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ALLOWED_SCHEMAS = frozenset({"iris_staging"})
TARGET_CRS = "EPSG:25832"

# Endpoint found via the official GDI-DE / GDI-Saarland catalogue (NOT the /wfs/wsg URL from the
# assignment PDF, which did not respond during verification). Override with WFS_URL if needed.
DEFAULT_WFS_URL = "https://geoportal.saarland.de/mapbender/php/wfs.php?INSPIRE=1&FEATURETYPE_ID=664"
DEFAULT_TYPENAME = "Schutzgebiete_INS:Wasserschutzgebiet"

# Primary live source: the same layer published as OGC API Features by GDI-Saarland
# (listed on the service landing page https://geoportal.saarland.de/spatial-objects/215).
# The legacy WFS GetFeature call was answered with "Parameter REQUEST invalid" (cause unknown).
DEFAULT_OGCAPI_ITEMS_URL = ("https://geoportal.saarland.de/spatial-objects/215/collections/"
                            "Schutzgebiete_INS:Wasserschutzgebiet/items")

# Generous WGS84 box around Saarland (lon_min, lat_min, lon_max, lat_max); used as a sanity extent
# to catch mislabelled CRS / swapped axes. The service advertises ~6.61..7.39 E, 49.11..49.66 N.
SAARLAND_BBOX_WGS84 = (6.30, 49.05, 7.45, 49.70)

UNKNOWN = "UNKNOWN"


def normalize_token(value) -> str:
    """Whitespace-collapse + casefold. The ONLY normalisation applied before an exact lookup."""
    return " ".join(str(value).split()).casefold()


class UnsafeTargetError(RuntimeError):
    pass


def assert_safe_schema(schema: str) -> None:
    """Hard guard: this module may only ever write to iris_staging."""
    if schema not in ALLOWED_SCHEMAS:
        raise UnsafeTargetError(
            f"Refusing to write to schema {schema!r}; allowed: {sorted(ALLOWED_SCHEMAS)}"
        )


@dataclass(frozen=True)
class AttributeMapping:
    id_field: str | None
    state_field: str | None
    name_field: str | None
    zone_field: str | None
    status_field: str | None
    restriction_field: str | None
    extraction_rate_field: str | None
    extraction_unit_field: str | None
    updated_field: str | None
    value_maps: dict  # {"zone": {normalized_raw: canonical}, ...}

    @classmethod
    def from_dict(cls, d: dict) -> "AttributeMapping":
        maps = {}
        for key, table in (d.get("value_maps") or {}).items():
            if key.startswith("_"):
                continue
            maps[key] = {normalize_token(k): v for k, v in table.items()}
        return cls(
            id_field=d.get("id_field"),
            state_field=d.get("state_field"),
            name_field=d.get("name_field"),
            zone_field=d.get("zone_field"),
            status_field=d.get("status_field"),
            restriction_field=d.get("restriction_field"),
            extraction_rate_field=d.get("extraction_rate_field"),
            extraction_unit_field=d.get("extraction_unit_field"),
            updated_field=d.get("updated_field"),
            value_maps=maps,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "AttributeMapping":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Settings:
    wfs_url: str = DEFAULT_WFS_URL
    typename: str = DEFAULT_TYPENAME
    wfs_version: str = "2.0.0"
    page_size: int = 1000
    timeout_s: float = 60.0
    source_crs: str = TARGET_CRS      # used only if the response does not declare a CRS
    target_crs: str = TARGET_CRS
    state_code: str = "SL"
    db_dsn: str | None = None
    db_schema: str = "iris_staging"
    mapping_file: str = "config/attribute_mapping.json"
    min_area_m2: float = 1.0
    source: str = "ogcapi"                  # 'ogcapi' (default) or 'wfs'
    ogcapi_items_url: str = DEFAULT_OGCAPI_ITEMS_URL
    ogcapi_source_crs: str = "EPSG:4326"    # service sends GeoJSON lon/lat with no CRS member

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        return cls(
            wfs_url=e.get("WFS_URL", DEFAULT_WFS_URL),
            typename=e.get("WFS_TYPENAME", DEFAULT_TYPENAME),
            wfs_version=e.get("WFS_VERSION", "2.0.0"),
            page_size=int(e.get("WFS_PAGE_SIZE", "1000")),
            timeout_s=float(e.get("WFS_TIMEOUT_S", "60")),
            source_crs=e.get("SOURCE_CRS", TARGET_CRS),
            state_code=e.get("STATE_CODE", "SL"),
            db_dsn=e.get("IRIS_DB_DSN"),
            db_schema=e.get("IRIS_DB_SCHEMA", "iris_staging"),
            mapping_file=e.get("MAPPING_FILE", "config/attribute_mapping.json"),
            min_area_m2=float(e.get("MIN_AREA_M2", "1.0")),
            source=e.get("WSG_SOURCE", "ogcapi"),
            ogcapi_items_url=e.get("OGCAPI_ITEMS_URL", DEFAULT_OGCAPI_ITEMS_URL),
            ogcapi_source_crs=e.get("OGCAPI_SOURCE_CRS", "EPSG:4326"),
        )
