import geopandas as gpd
import pytest
from shapely.geometry import box

from wsg.screening import NO_ZONE, screen_parcels, summarize, zones_gdf_from_records

E, N = 354000, 5455000


@pytest.fixture(scope="module")
def zones(built):
    return zones_gdf_from_records(built.records)


def parcels(*geoms_ids, crs="EPSG:25832"):
    return gpd.GeoDataFrame({"parcel_id": [i for i, _ in geoms_ids]},
                            geometry=[g for _, g in geoms_ids], crs=crs)


def rel(res, pid):
    return res[res.parcel_id == pid]


def test_within_partial_none_touch(zones):
    p = parcels(("inside_I", box(E+20, N+20, E+60, N+60)),
                ("straddle_I_II", box(E+80, N+20, E+120, N+60)),
                ("far", box(E+20000, N+20000, E+20050, N+20050)),
                ("touch_IIIA", box(E+2200, N, E+2300, N+100)),
                ("in_second_part_of_multipolygon", box(E+50, N+220, E+100, N+260)))
    res = screen_parcels(p, zones)
    assert list(rel(res, "inside_I").relation) == ["WITHIN"]
    assert rel(res, "inside_I").zone_class.iloc[0] == "ZONE_I"
    s = rel(res, "straddle_I_II")
    assert set(s.zone_class) == {"ZONE_I", "ZONE_II"} and set(s.relation) == {"PARTIAL"}
    assert s.overlap_pct.tolist() == [0.5, 0.5]
    assert list(rel(res, "far").relation) == [NO_ZONE]
    assert list(rel(res, "touch_IIIA").relation) == ["TOUCHES"]
    mp = rel(res, "in_second_part_of_multipolygon")
    assert list(mp.relation) == ["WITHIN"] and mp.zone_class.iloc[0] == "ZONE_III"

    summ = summarize(res).set_index("parcel_id")
    assert summ.loc["straddle_I_II", "most_restrictive_zone"] == "ZONE_I" and summ.loc["straddle_I_II", "restricted"]
    assert not summ.loc["touch_IIIA", "restricted"] and summ.loc["touch_IIIA", "touches_only"]
    assert summ.loc["far", "most_restrictive_zone"] == NO_ZONE


def test_revoked_excluded_unknown_flagged(zones):
    p = parcels(("in_revoked", box(E+4020, N+20, E+4060, N+60)),
                ("in_unknown_status", box(E+5020, N+20, E+5060, N+60)))
    res = screen_parcels(p, zones)
    assert list(rel(res, "in_revoked").relation) == [NO_ZONE]            # REVOKED not binding
    u = rel(res, "in_unknown_status")
    assert list(u.relation) == ["WITHIN"] and bool(u.needs_review.iloc[0])  # UNKNOWN status/class -> review
    assert summarize(res).set_index("parcel_id").loc["in_unknown_status", "most_restrictive_zone"] == "UNKNOWN"


def test_parcel_in_other_crs_is_reprojected(zones):
    p = parcels(("inside_I", box(E+20, N+20, E+60, N+60))).to_crs("EPSG:4326")
    res = screen_parcels(p, zones)
    assert list(res.relation) == ["WITHIN"]


def test_parcel_without_crs_refused(zones):
    p = gpd.GeoDataFrame({"parcel_id": [1]}, geometry=[box(0, 0, 1, 1)])
    with pytest.raises(ValueError):
        screen_parcels(p, zones)


def test_unranked_zone_class_is_reported_and_needs_review(zones):
    import pandas as pd
    z = zones.copy()
    z.loc[z.source_zone_id == "SYN-001-I", "zone_class"] = "ZONE_B"     # a class with no severity rank
    res = screen_parcels(parcels(("p", box(E+20, N+20, E+60, N+60))), z)
    assert bool(res.needs_review.iloc[0])
    summ = summarize(res).set_index("parcel_id")
    assert summ.loc["p", "most_restrictive_zone"] == "ZONE_B" and bool(summ.loc["p", "needs_review"])


def test_results_carry_state_code(zones):
    res = screen_parcels(parcels(("p", box(E+20, N+20, E+60, N+60))), zones)
    assert res.state_code.iloc[0] == "SL"
