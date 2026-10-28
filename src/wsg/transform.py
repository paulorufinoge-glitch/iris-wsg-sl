"""GeoJSON features -> validated staging records (MultiPolygon, EPSG:25832).

Every rejection is explicit and recorded; nothing is silently dropped or invented.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import shapely
from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, shape
from shapely.ops import transform as shp_transform

from .config import (SAARLAND_BBOX_WGS84, UNKNOWN, AttributeMapping, Settings)
from .zones import map_value

REJECT_EMPTY = "EMPTY_GEOMETRY"
REJECT_MALFORMED = "MALFORMED_GEOMETRY"
REJECT_NON_POLYGON = "NON_POLYGON_GEOMETRY"
REJECT_UNREPAIRABLE = "INVALID_UNREPAIRABLE"
REJECT_ZERO_AREA = "ZERO_OR_TINY_AREA"
REJECT_EXTENT = "OUT_OF_EXPECTED_EXTENT"
REJECT_NO_ID = "MISSING_SOURCE_ID"
REJECT_DUP = "DUPLICATE_SOURCE_ID"
REJECT_CRS = "CRS_TRANSFORM_FAILED"

HASH_FIELDS = ("source_zone_id", "zone_class", "zone_code_raw", "zone_name", "status", "status_raw",
               "restriction_category", "restriction_raw", "extraction_rate_value",
               "extraction_rate_unit", "attributes_missing", "quality_flags",
               "source_attributes", "source_updated_at")

_NUM_RE = re.compile(r"^\d+(?:[.,]\d+)?$")


@dataclass
class Rejection:
    source_feature_id: str | None
    reason: str
    detail: str = ""


@dataclass
class TransformResult:
    records: list = field(default_factory=list)
    rejections: list = field(default_factory=list)

    @property
    def repaired(self) -> int:
        return sum(1 for r in self.records if "GEOMETRY_REPAIRED" in r["quality_flags"])


def parse_decimal(v):
    """Strict: '1200', '1200.5', '1200,5'. No thousands separators (-> None, flagged by caller)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    return float(s.replace(",", ".")) if _NUM_RE.match(s) else None


def _polygonal_parts(geom):
    if geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    if geom.geom_type == "MultiPolygon":
        return geom
    polys = []
    for part in getattr(geom, "geoms", []):
        sub = _polygonal_parts(part)
        if sub is not None:
            polys.extend(sub.geoms)
    return MultiPolygon(polys) if polys else None


def expected_extent(target_crs: str):
    t = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    return t.transform_bounds(*SAARLAND_BBOX_WGS84)


def content_hash(rec: dict) -> str:
    g = shapely.normalize(shapely.set_precision(rec["geom"], 0.001))  # 1 mm: ignore float noise
    payload = {k: rec[k] for k in HASH_FIELDS}
    payload["geom_wkb"] = shapely.to_wkb(g).hex()
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _val(props: dict, field_name):
    if not field_name:
        return None
    v = props.get(field_name)
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return v


def build_records(features: list, *, mapping: AttributeMapping, settings: Settings,
                  source_crs: str, source_url: str, retrieved_at: datetime) -> TransformResult:
    res = TransformResult()
    src, tgt = CRS.from_user_input(source_crs), CRS.from_user_input(settings.target_crs)
    transformer = None if src == tgt else Transformer.from_crs(src, tgt, always_xy=True)
    minx, miny, maxx, maxy = expected_extent(settings.target_crs)
    seen: set = set()

    for i, feat in enumerate(features):
        props = feat.get("properties") or {}
        sid, origin = None, None
        raw_id = _val(props, mapping.id_field)
        if raw_id is not None:
            sid, origin = str(raw_id).strip(), f"attribute:{mapping.id_field}"
        elif feat.get("id") not in (None, ""):
            sid, origin = str(feat["id"]).strip(), "wfs_feature_id"
        label = sid or f"index:{i}"
        st_raw = _val(props, mapping.state_field)
        state = str(st_raw).strip() if st_raw is not None else settings.state_code

        if sid is None:
            res.rejections.append(Rejection(label, REJECT_NO_ID, "no ID attribute and no WFS feature id"))
            continue
        if (state, sid) in seen:
            res.rejections.append(Rejection(label, REJECT_DUP, "source ID already seen in this batch"))
            continue

        gj = feat.get("geometry")
        if not gj:
            res.rejections.append(Rejection(label, REJECT_EMPTY, "null geometry"))
            continue
        try:
            geom = shape(gj)
        except Exception as exc:  # malformed coordinates / unknown type
            res.rejections.append(Rejection(label, REJECT_MALFORMED, str(exc)[:200]))
            continue
        if geom.is_empty:
            res.rejections.append(Rejection(label, REJECT_EMPTY, "empty geometry"))
            continue
        if geom.geom_type not in ("Polygon", "MultiPolygon", "GeometryCollection"):
            res.rejections.append(Rejection(label, REJECT_NON_POLYGON, geom.geom_type))
            continue

        geom = shapely.force_2d(geom)
        if transformer is not None:
            try:
                geom = shp_transform(transformer.transform, geom)
            except Exception as exc:
                res.rejections.append(Rejection(label, REJECT_CRS, str(exc)[:200]))
                continue

        flags: list = []
        if state != settings.state_code:
            flags.append("STATE_OTHER_THAN_REQUESTED")
        if not geom.is_valid or geom.geom_type == "GeometryCollection":
            fixed = _polygonal_parts(shapely.make_valid(geom))
            if fixed is None:
                res.rejections.append(Rejection(label, REJECT_UNREPAIRABLE, "no polygonal part after make_valid"))
                continue
            geom = fixed
            flags.append("GEOMETRY_REPAIRED")
        else:
            geom = _polygonal_parts(geom)
        if geom is None or geom.is_empty:
            res.rejections.append(Rejection(label, REJECT_EMPTY, "no polygonal part"))
            continue
        gx0, gy0, gx1, gy1 = geom.bounds
        if gx0 < minx or gx1 > maxx or gy0 < miny or gy1 > maxy:
            res.rejections.append(Rejection(
                label, REJECT_EXTENT,
                f"bounds=({gx0:.1f},{gy0:.1f},{gx1:.1f},{gy1:.1f}) outside expected "
                f"({minx:.0f},{miny:.0f},{maxx:.0f},{maxy:.0f}); wrong/mislabelled CRS?"))
            continue

        if geom.area < settings.min_area_m2:
            res.rejections.append(Rejection(label, REJECT_ZERO_AREA, f"area={geom.area:.4f} m2"))
            continue

        # ---- attributes: explicit mapping, explicit unknowns -------------------------------
        missing = []
        zone_raw = _val(props, mapping.zone_field)
        status_raw = _val(props, mapping.status_field)
        restr_raw = _val(props, mapping.restriction_field)
        rate_raw = _val(props, mapping.extraction_rate_field)
        unit_raw = _val(props, mapping.extraction_unit_field)
        name = _val(props, mapping.name_field)
        upd_raw = _val(props, mapping.updated_field)
        for logical, v in (("zone", zone_raw), ("status", status_raw), ("restriction", restr_raw),
                           ("extraction_rate", rate_raw), ("name", name), ("updated", upd_raw)):
            if v is None:
                missing.append(logical)

        zone_class, ok = map_value(zone_raw, mapping.value_maps.get("zone", {}))
        if zone_raw is not None and not ok:
            flags.append("ZONE_UNMAPPED")
        status, ok = map_value(status_raw, mapping.value_maps.get("status", {}))
        if status_raw is not None and not ok:
            flags.append("STATUS_UNMAPPED")
        restriction, ok = map_value(restr_raw, mapping.value_maps.get("restriction", {}))
        if restr_raw is not None and not ok:
            flags.append("RESTRICTION_UNMAPPED")

        rate = None
        if rate_raw is not None:
            rate = parse_decimal(rate_raw)
            if rate is None:
                flags.append("EXTRACTION_RATE_UNPARSEABLE")
            if unit_raw is None:
                flags.append("EXTRACTION_UNIT_NOT_PUBLISHED")

        upd = None
        if upd_raw is not None:
            try:
                upd = date.fromisoformat(str(upd_raw)[:10])
            except ValueError:
                flags.append("UPDATE_DATE_UNPARSEABLE")

        rec = {
            "state_code": state, "source_zone_id": sid, "source_id_origin": origin,
            "zone_class": zone_class,
            "zone_code_raw": None if zone_raw is None else str(zone_raw),
            "zone_name": None if name is None else str(name),
            "status": status, "status_raw": None if status_raw is None else str(status_raw),
            "restriction_category": restriction,
            "restriction_raw": None if restr_raw is None else str(restr_raw),
            "extraction_rate_value": rate,
            "extraction_rate_unit": None if unit_raw is None else str(unit_raw),
            "attributes_missing": sorted(missing), "quality_flags": sorted(flags),
            "source_attributes": props, "geom": geom,
            "source_url": source_url, "source_typename": settings.typename,
            "source_crs": str(source_crs), "source_updated_at": upd, "retrieved_at": retrieved_at,
        }
        rec["content_hash"] = content_hash(rec)
        seen.add((state, sid))
        res.records.append(rec)
    return res
