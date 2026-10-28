"""Parcel-level containment / intersection screening against loaded zones."""
from __future__ import annotations

import geopandas as gpd
import pandas as pd
import shapely
from shapely import STRtree

from .config import TARGET_CRS, UNKNOWN
from .zones import ZONE_RANK

NO_ZONE = "NO_ZONE_FOUND"


def zones_gdf_from_records(records: list) -> gpd.GeoDataFrame:
    df = pd.DataFrame([{"state_code": r["state_code"], "source_zone_id": r["source_zone_id"], "zone_class": r["zone_class"],
                        "status": r["status"], "geometry": r["geom"]} for r in records])
    return gpd.GeoDataFrame(df, geometry="geometry", crs=TARGET_CRS)


def load_zones_gdf(conn, schema: str, state_code: str | None = None) -> gpd.GeoDataFrame:
    """All states by default: the layer also holds zones registered under other states (e.g. RLP)."""
    from psycopg import sql
    q = sql.SQL("SELECT state_code, source_zone_id, zone_class, status, ST_AsBinary(geom) "
                "FROM {s}.water_protection_zones").format(s=sql.Identifier(schema))
    args: tuple = ()
    if state_code:
        q = q + sql.SQL(" WHERE state_code = %s")
        args = (state_code,)
    with conn.cursor() as cur:
        cur.execute(q, args)
        rows = cur.fetchall()
    df = pd.DataFrame([{"state_code": s_, "source_zone_id": a, "zone_class": b, "status": c,
                        "geometry": shapely.from_wkb(bytes(d))} for s_, a, b, c, d in rows],
                      columns=["state_code", "source_zone_id", "zone_class", "status", "geometry"])
    return gpd.GeoDataFrame(df, geometry="geometry", crs=TARGET_CRS)


def screen_parcels(parcels: gpd.GeoDataFrame, zones: gpd.GeoDataFrame, *, parcel_id_col: str = "parcel_id",
                   binding_statuses=("IN_FORCE",), review_statuses=(UNKNOWN,)) -> pd.DataFrame:
    """One row per (parcel, zone) hit, plus one NO_ZONE_FOUND row per parcel without hits.

    relation: WITHIN (zone covers parcel) | PARTIAL (positive-area overlap) | TOUCHES (boundary only).
    Zones with status in binding_statuses count; zones with status in review_statuses are returned
    with needs_review=True; REVOKED/PLANNED are excluded unless passed in binding_statuses.
    """
    if parcels.crs is None:
        raise ValueError("parcels have no CRS; refusing to guess")
    p = parcels.to_crs(TARGET_CRS)
    keep = set(binding_statuses) | set(review_statuses)
    z = zones[zones["status"].isin(keep)].reset_index(drop=True)
    tree = STRtree(z.geometry.values) if len(z) else None
    rows = []
    for pid, pg in zip(p[parcel_id_col], p.geometry):
        if pg is None or pg.is_empty:
            rows.append(_row(pid, None, None, None, "INVALID_PARCEL", None, None, True))
            continue
        if not pg.is_valid:
            pg = shapely.make_valid(pg)
        hits = tree.query(pg, predicate="intersects") if tree is not None else []
        if len(hits) == 0:
            rows.append(_row(pid, None, None, None, NO_ZONE, 0.0, 0.0, False))
            continue
        for k in hits:
            zr = z.iloc[int(k)]
            inter = pg.intersection(zr.geometry)
            area = inter.area
            if zr.geometry.covers(pg):
                rel = "WITHIN"
            elif area == 0:
                rel = "TOUCHES"
            else:
                rel = "PARTIAL"
            pct = (area / pg.area) if pg.area > 0 else None
            # unranked classes (UNKNOWN, ZONE_B, ...) cannot be placed in the severity order -> human review
            review = zr.zone_class not in ZONE_RANK or zr.status in review_statuses
            rows.append(_row(pid, zr.source_zone_id, zr.zone_class, zr.status, rel, area, pct, review,
                             zr.get("state_code")))
    return pd.DataFrame(rows)


def _row(pid, zid, zc, st, rel, area, pct, review, state=None):
    return {"parcel_id": pid, "state_code": state, "source_zone_id": zid, "zone_class": zc, "status": st, "relation": rel,
            "overlap_area_m2": None if area is None else round(area, 3),
            "overlap_pct": None if pct is None else round(pct, 4), "needs_review": review}


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    """Per parcel: restricted flag, most restrictive zone, review flag.

    NO_ZONE_FOUND means 'no zone in the loaded dataset', NOT 'safe': see README coverage limits.
    """
    out = []
    for pid, g in results.groupby("parcel_id", sort=False):
        hit = g[g["relation"].isin(["WITHIN", "PARTIAL"])]
        ranked = sorted((ZONE_RANK[z], z) for z in hit["zone_class"] if z in ZONE_RANK)
        other = [z for z in hit["zone_class"] if z and z not in ZONE_RANK and z != UNKNOWN]
        invalid = bool((g["relation"] == "INVALID_PARCEL").any())
        most = ranked[0][1] if ranked else (other[0] if other else (UNKNOWN if len(hit) else NO_ZONE))
        out.append({
            "parcel_id": pid,
            "restricted": bool(len(hit)),
            "most_restrictive_zone": "INVALID_PARCEL" if invalid else most,
            "touches_only": bool(len(hit) == 0 and (g["relation"] == "TOUCHES").any()),
            "needs_review": bool(g["needs_review"].any()),
        })
    return pd.DataFrame(out)
