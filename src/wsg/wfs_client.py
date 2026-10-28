"""Minimal OGC WFS 2.0 client (capabilities inspection + paged GetFeature as GeoJSON).

Stdlib + requests only. Never retries around authorisation failures (no bypassing).
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings

log = logging.getLogger(__name__)
MAX_PAGES = 500
USER_AGENT = "iris-wsg-sl/0.1 (+staging ingestion; contact: see README)"


class WfsError(RuntimeError):
    pass


class WfsAccessDenied(WfsError):
    pass


@dataclass
class FeatureTypeInfo:
    name: str
    title: str | None = None
    default_crs: str | None = None
    other_crs: list = field(default_factory=list)


@dataclass
class CapabilitiesInfo:
    version: str | None
    title: str | None
    feature_types: list
    output_formats: list

    def find(self, typename: str) -> FeatureTypeInfo | None:
        return next((f for f in self.feature_types if f.name == typename), None)


@dataclass
class FetchResult:
    features: list
    source_crs: str
    source_crs_origin: str          # 'response' | 'configured-default'
    request_url: str
    http_status: int | None
    pages: int
    coverage_notes: list
    capabilities: CapabilitiesInfo | None = None
    number_matched: int | None = None
    metadata: dict = field(default_factory=dict)


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.0, status_forcelist=(500, 502, 503, 504),
                  allowed_methods=("GET",), respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = USER_AGENT
    return s


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _request(session, url, params, timeout):
    try:
        resp = session.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise WfsError(f"request failed: {exc}") from exc
    if resp.status_code in (401, 403):
        raise WfsAccessDenied(f"HTTP {resp.status_code}: access not permitted; not attempting to bypass")
    if resp.status_code >= 400:
        raise WfsError(f"HTTP {resp.status_code} from service")
    head = resp.content[:4000]
    if b"ExceptionReport" in head or b"ServiceException" in head:
        raise WfsError(f"OGC exception: {resp.text[:500]!r}")
    return resp


def parse_capabilities(xml_bytes: bytes) -> CapabilitiesInfo:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise WfsError(f"capabilities not parseable as XML: {exc}") from exc
    if _ln(root.tag) == "ExceptionReport":
        raise WfsError("capabilities request returned an OGC ExceptionReport")
    title, fts, formats = None, [], []
    for el in root.iter():
        n = _ln(el.tag)
        if n == "ServiceIdentification":
            for c in el:
                if _ln(c.tag) == "Title" and title is None:
                    title = (c.text or "").strip() or None
        elif n == "FeatureType":
            ft = FeatureTypeInfo(name="")
            for c in el:
                cn, txt = _ln(c.tag), (c.text or "").strip()
                if cn == "Name":
                    ft.name = txt
                elif cn == "Title":
                    ft.title = txt
                elif cn in ("DefaultCRS", "DefaultSRS"):
                    ft.default_crs = txt
                elif cn in ("OtherCRS", "OtherSRS"):
                    ft.other_crs.append(txt)
            if ft.name:
                fts.append(ft)
        elif n == "Operation" and el.attrib.get("name") == "GetFeature":
            for p in el.iter():
                if _ln(p.tag) == "Parameter" and p.attrib.get("name", "").lower() == "outputformat":
                    formats += [(v.text or "").strip() for v in p.iter() if _ln(v.tag) == "Value"]
    return CapabilitiesInfo(root.attrib.get("version"), title, fts, formats)


def fetch_capabilities(session, settings: Settings) -> CapabilitiesInfo:
    params = {"SERVICE": "WFS", "VERSION": settings.wfs_version, "REQUEST": "GetCapabilities"}
    resp = _request(session, settings.wfs_url, params, settings.timeout_s)
    return parse_capabilities(resp.content)


def check_capabilities(caps: CapabilitiesInfo, settings: Settings) -> list:
    """Hard-fail if the configured layer is not advertised; return soft warnings otherwise."""
    ft = caps.find(settings.typename)
    if ft is None:
        avail = ", ".join(f.name for f in caps.feature_types) or "(none parsed)"
        raise WfsError(f"layer {settings.typename!r} not advertised by service. Available: {avail}")
    warnings = []
    if caps.output_formats and not any(f.upper().startswith("GEOJSON") for f in caps.output_formats):
        warnings.append("GEOJSON output not advertised in capabilities; trying anyway")
    if ft.default_crs and settings.source_crs.split(":")[-1] not in ft.default_crs:
        warnings.append(
            f"layer DefaultCRS is {ft.default_crs!r} but SOURCE_CRS fallback is {settings.source_crs!r}")
    return warnings


def _crs_name(fc: dict) -> str | None:
    crs = fc.get("crs")
    if isinstance(crs, dict):
        return (crs.get("properties") or {}).get("name")
    return None


def get_features(session, settings: Settings, caps: CapabilitiesInfo | None = None) -> FetchResult:
    notes: list = []
    base = {
        "SERVICE": "WFS", "VERSION": settings.wfs_version, "REQUEST": "GetFeature",
        "TYPENAMES": settings.typename, "OUTPUTFORMAT": "GEOJSON",
        "SRSNAME": "urn:ogc:def:crs:EPSG::" + settings.source_crs.split(":")[-1],
    }
    features, start, pages, last_ids = [], 0, 0, None
    crs_name, matched, status = None, None, None
    truncated_by_pages = True
    while pages < MAX_PAGES:
        params = {**base, "COUNT": settings.page_size, "STARTINDEX": start}
        resp = _request(session, settings.wfs_url, params, settings.timeout_s)
        status = resp.status_code
        try:
            fc = resp.json()
        except ValueError as exc:
            raise WfsError(f"non-JSON GetFeature response: {resp.text[:200]!r}") from exc
        pages += 1
        if fc.get("numberMatched") is not None:
            try:
                matched = int(fc["numberMatched"])
            except (TypeError, ValueError):
                pass
        crs_name = crs_name or _crs_name(fc)
        page = fc.get("features") or []
        if not page:
            truncated_by_pages = False
            break
        ids = [f.get("id") for f in page]
        if last_ids is not None and ids == last_ids and any(i is not None for i in ids):
            notes.append("server appears to ignore STARTINDEX (identical page repeated); stopped paging")
            truncated_by_pages = False
            break
        features.extend(page)
        last_ids = ids
        if len(page) < settings.page_size:
            truncated_by_pages = False
            break
        start += len(page)
    if truncated_by_pages:
        notes.append(f"MAX_PAGES ({MAX_PAGES}) reached; result may be truncated")
    if matched is not None and len(features) < matched:
        notes.append(f"received {len(features)} of numberMatched={matched}; coverage incomplete")
    return FetchResult(
        features=features,
        source_crs=crs_name or settings.source_crs,
        source_crs_origin="response" if crs_name else "configured-default",
        request_url=settings.wfs_url, http_status=status, pages=pages,
        coverage_notes=notes, capabilities=caps, number_matched=matched,
    )


def get_features_ogcapi(session, settings: Settings, max_pages: int = MAX_PAGES) -> FetchResult:
    """Page through an OGC API Features collection (limit/offset) as GeoJSON.

    The service sends no CRS member; per GeoJSON (RFC 7946) coordinates are WGS84 lon/lat, so the
    configured fallback CRS is used. A wrong assumption is caught by the extent check in transform.
    """
    notes: list = []
    features, offset, pages, matched, status, meta = [], 0, 0, None, None, {}
    first_ids_prev, truncated = None, True
    while pages < max_pages:
        params = {"f": "json", "limit": settings.page_size, "offset": offset}
        resp = _request(session, settings.ogcapi_items_url, params, settings.timeout_s)
        status = resp.status_code
        try:
            fc = resp.json()
        except ValueError as exc:
            raise WfsError(f"non-JSON response: {resp.text[:200]!r}") from exc
        pages += 1
        if pages == 1:
            meta = {k: fc[k] for k in ("serviceTitle", "collectionName", "collectionTitle",
                                       "description", "timeStamp", "numberMatched", "extent") if k in fc}
        if fc.get("numberMatched") is not None:
            try:
                matched = int(fc["numberMatched"])
            except (TypeError, ValueError):
                pass
        page = fc.get("features") or []
        if not page:
            truncated = False
            break
        first_id = (page[0].get("properties") or {}).get("gml_id") or page[0].get("id")
        if first_ids_prev is not None and first_id is not None and first_id == first_ids_prev:
            notes.append("server appears to ignore 'offset' (same first feature repeated); stopped paging")
            truncated = False
            break
        first_ids_prev = first_id
        features.extend(page)
        offset += len(page)
        if matched is not None and len(features) >= matched:
            truncated = False
            break
        if matched is None and len(page) < settings.page_size:
            truncated = False
            break
    if truncated and pages >= max_pages:
        notes.append(f"stopped after {max_pages} page(s); result may be truncated")
    if matched is not None and len(features) < matched:
        notes.append(f"received {len(features)} of numberMatched={matched}; coverage incomplete")
    notes.append("Service declares no CRS in the GeoJSON; WGS84 lon/lat (RFC 7946 default) assumed and "
                 "verified by the extent check")
    return FetchResult(
        features=features, source_crs=settings.ogcapi_source_crs, source_crs_origin="configured-default",
        request_url=settings.ogcapi_items_url, http_status=status, pages=pages,
        coverage_notes=notes, number_matched=matched, metadata=meta)


def inspect_schema(features: list, max_distinct: int = 25) -> dict:
    """Summarise property names / example & distinct values to help fill the mapping file."""
    out: dict = {}
    for f in features:
        for k, v in (f.get("properties") or {}).items():
            d = out.setdefault(k, {"non_null": 0, "types": set(), "distinct": set()})
            if v is not None and v != "":
                d["non_null"] += 1
                d["types"].add(type(v).__name__)
                if len(d["distinct"]) < max_distinct:
                    d["distinct"].add(str(v))
    return {k: {"non_null": v["non_null"], "types": sorted(v["types"]),
                "sample_distinct": sorted(v["distinct"])} for k, v in out.items()}
