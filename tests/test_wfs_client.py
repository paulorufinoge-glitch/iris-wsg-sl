import json

import pytest

from wsg import wfs_client as w
from wsg.config import Settings

CAPS = b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">
 <ows:ServiceIdentification><ows:Title>Test WFS</ows:Title></ows:ServiceIdentification>
 <ows:OperationsMetadata><ows:Operation name="GetFeature"><ows:Parameter name="outputFormat">
   <ows:AllowedValues><ows:Value>GEOJSON</ows:Value><ows:Value>GML32</ows:Value></ows:AllowedValues>
 </ows:Parameter></ows:Operation></ows:OperationsMetadata>
 <wfs:FeatureTypeList>
  <wfs:FeatureType><wfs:Name>Schutzgebiete_INS:Wasserschutzgebiet</wfs:Name><wfs:Title>Wasserschutzgebiet</wfs:Title>
   <wfs:DefaultCRS>urn:ogc:def:crs:EPSG::25832</wfs:DefaultCRS></wfs:FeatureType>
  <wfs:FeatureType><wfs:Name>Schutzgebiete_INS:Urwald_SL</wfs:Name></wfs:FeatureType>
 </wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""


class Resp:
    def __init__(self, body, status=200):
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status_code = status
        self.text = self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content)


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return self.responses.pop(0)


def feats(n, start=0):
    return [{"type": "Feature", "id": f"f{start+i}", "properties": {}, "geometry": None} for i in range(n)]


ST = Settings(page_size=2)


def test_capabilities_parse_and_layer_check():
    caps = w.parse_capabilities(CAPS)
    assert caps.version == "2.0.0" and "GEOJSON" in caps.output_formats
    assert caps.find("Schutzgebiete_INS:Wasserschutzgebiet").default_crs.endswith("25832")
    assert w.check_capabilities(caps, Settings()) == []


def test_missing_layer_is_hard_error():
    caps = w.parse_capabilities(CAPS)
    with pytest.raises(w.WfsError, match="not advertised"):
        w.check_capabilities(caps, Settings(typename="sl_wsg_poly"))   # the name from the PDF


def test_paging_collects_all_and_reads_crs():
    fc = lambda f: {"type": "FeatureCollection", "features": f,
                    "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25832"}}}
    s = FakeSession(Resp(fc(feats(2))), Resp(fc(feats(1, 2))))
    r = w.get_features(s, ST)
    assert len(r.features) == 3 and r.pages == 2 and r.source_crs_origin == "response"
    assert [c["STARTINDEX"] for c in s.calls] == [0, 2]


def test_empty_response_is_reported_not_error():
    r = w.get_features(FakeSession(Resp({"type": "FeatureCollection", "features": []})), ST)
    assert r.features == [] and r.source_crs_origin == "configured-default"


def test_server_ignoring_startindex_stops():
    page = {"type": "FeatureCollection", "features": feats(2)}
    r = w.get_features(FakeSession(Resp(page), Resp(page)), ST)
    assert len(r.features) == 2 and any("STARTINDEX" in n for n in r.coverage_notes)


def test_truncation_noted():
    page = {"type": "FeatureCollection", "numberMatched": 10, "features": feats(1)}
    r = w.get_features(FakeSession(Resp(page)), ST)
    assert any("numberMatched" in n for n in r.coverage_notes)


def test_ogc_exception_and_access_denied():
    with pytest.raises(w.WfsError, match="OGC exception"):
        w.get_features(FakeSession(Resp(b"<ows:ExceptionReport>boom</ows:ExceptionReport>")), ST)
    with pytest.raises(w.WfsAccessDenied):
        w.get_features(FakeSession(Resp(b"forbidden", 403)), ST)
    with pytest.raises(w.WfsError, match="non-JSON"):
        w.get_features(FakeSession(Resp(b"<html>hi</html>")), ST)


def test_inspect_schema_lists_distinct_values():
    out = w.inspect_schema([{"properties": {"zone": "I", "n": 1}}, {"properties": {"zone": "II", "n": None}}])
    assert out["zone"]["sample_distinct"] == ["I", "II"] and out["n"]["non_null"] == 1


# ---------------- OGC API Features path ----------------
def of(n, start=0):
    return [{"type": "Feature", "properties": {"gml_id": f"W.{start+i}"}, "geometry": None} for i in range(n)]


def page(features, matched=None, **extra):
    d = {"type": "FeatureCollection", "features": features, **extra}
    if matched is not None:
        d["numberMatched"] = matched
    return d


def test_ogcapi_paging_by_offset_until_number_matched():
    s = FakeSession(Resp(page(of(2), 3, collectionName="X")), Resp(page(of(1, 2), 3)))
    r = w.get_features_ogcapi(s, ST)
    assert len(r.features) == 3 and r.pages == 2 and r.number_matched == 3
    assert [c["offset"] for c in s.calls] == [0, 2] and all(c["f"] == "json" for c in s.calls)
    assert r.source_crs == "EPSG:4326" and r.metadata["collectionName"] == "X"
    assert not any("incomplete" in n for n in r.coverage_notes)


def test_ogcapi_single_page_when_server_returns_all():
    r = w.get_features_ogcapi(FakeSession(Resp(page(of(2), 2))), Settings(page_size=10))
    assert len(r.features) == 2 and r.pages == 1


def test_ogcapi_empty_collection():
    r = w.get_features_ogcapi(FakeSession(Resp(page([], 0))), ST)
    assert r.features == []


def test_ogcapi_incomplete_coverage_noted():
    r = w.get_features_ogcapi(FakeSession(Resp(page(of(1), 5)), Resp(page([], 5))), ST)
    assert any("incomplete" in n for n in r.coverage_notes)


def test_ogcapi_ignored_offset_stops():
    p = page(of(2), 10)
    r = w.get_features_ogcapi(FakeSession(Resp(p), Resp(p)), ST)
    assert len(r.features) == 2 and any("offset" in n for n in r.coverage_notes)


def test_ogcapi_errors():
    with pytest.raises(w.WfsAccessDenied):
        w.get_features_ogcapi(FakeSession(Resp(b"no", 403)), ST)
    with pytest.raises(w.WfsError, match="non-JSON"):
        w.get_features_ogcapi(FakeSession(Resp(b"<html>x</html>")), ST)
