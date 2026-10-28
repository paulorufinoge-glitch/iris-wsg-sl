"""Orchestration: fetch (live or fixture) -> transform -> load -> record run."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import wfs_client
from .config import AttributeMapping, Settings, assert_safe_schema
from .transform import Rejection, TransformResult, build_records

log = logging.getLogger(__name__)

# Static, honest coverage statements stored with every run.
BASE_COVERAGE_NOTES = [
    "Coverage = zones published by GDI-Saarland in the configured layer only; absence of a zone is NOT proof "
    "that a site is unrestricted (planned/draft areas, pending procedures and local rules may not be included).",
    "Per-zone restriction text and permitted extraction rates are loaded only where the source publishes them; "
    "otherwise UNKNOWN / NULL with the attribute listed in attributes_missing.",
    "Rows no longer returned by the source are NOT deleted; use last_seen_run_id to find stale rows.",
]


@dataclass
class RunSummary:
    run_id: str
    state_code: str
    started_at: datetime
    finished_at: datetime | None = None
    source_url: str = ""
    source_typename: str = ""
    source_crs: str | None = None
    http_status: int | None = None
    status: str = "ERROR"          # SUCCESS | EMPTY | ERROR | DRY_RUN
    features_received: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    repaired: int = 0
    error_message: str | None = None
    coverage_notes: list = field(default_factory=list)
    source_metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)

    @property
    def exit_code(self) -> int:
        return {"SUCCESS": 0, "DRY_RUN": 0, "EMPTY": 2}.get(self.status, 1)


def read_fixture(path: str | Path, settings: Settings) -> wfs_client.FetchResult:
    fc = json.loads(Path(path).read_text(encoding="utf-8"))
    crs = wfs_client._crs_name(fc)
    return wfs_client.FetchResult(
        features=fc.get("features") or [], source_crs=crs or settings.source_crs,
        source_crs_origin="response" if crs else "configured-default",
        request_url=f"fixture:{Path(path).name}", http_status=None, pages=1,
        coverage_notes=["DATA SOURCE IS A SYNTHETIC FIXTURE, NOT LIVE GDI-SAARLAND DATA"])


def run_ingest(settings: Settings, mapping: AttributeMapping, *, fixture: str | None = None,
               dry_run: bool = False, conn=None, session=None) -> tuple[RunSummary, TransformResult | None]:
    assert_safe_schema(settings.db_schema)
    now = datetime.now(timezone.utc)
    s = RunSummary(run_id=str(uuid.uuid4()), state_code=settings.state_code, started_at=now,
                   source_url=(f"fixture:{Path(fixture).name}" if fixture else
                               settings.ogcapi_items_url if settings.source == "ogcapi" else settings.wfs_url),
                   source_typename=settings.typename, coverage_notes=list(BASE_COVERAGE_NOTES))
    tr = None
    try:
        if fixture:
            fetch = read_fixture(fixture, settings)
        else:
            session = session or wfs_client.make_session()
            if settings.source == "wfs":
                caps = wfs_client.fetch_capabilities(session, settings)
                s.coverage_notes += wfs_client.check_capabilities(caps, settings)
                s.source_metadata = {"api": "OGC WFS", "service_title": caps.title,
                                     "wfs_version": caps.version, "layer": settings.typename}
                fetch = wfs_client.get_features(session, settings, caps)
            else:
                fetch = wfs_client.get_features_ogcapi(session, settings)
                s.source_metadata = {"api": "OGC API Features", "layer": settings.typename,
                                     **fetch.metadata}
        s.http_status, s.source_crs = fetch.http_status, fetch.source_crs
        s.coverage_notes += fetch.coverage_notes
        s.features_received = len(fetch.features)
        s.source_metadata["source_crs_origin"] = fetch.source_crs_origin

        if not fetch.features:
            s.status = "EMPTY"
            s.coverage_notes.append("Service returned zero features; no rows written or deleted.")
        else:
            tr = build_records(fetch.features, mapping=mapping, settings=settings,
                               source_crs=fetch.source_crs, source_url=s.source_url, retrieved_at=now)
            s.rejected, s.repaired = len(tr.rejections), tr.repaired
            if dry_run or conn is None:
                s.status = "DRY_RUN"
            else:
                from . import db
                stats = db.load_records(conn, settings.db_schema, tr.records, s.run_id)
                s.inserted, s.updated, s.unchanged = stats.inserted, stats.updated, stats.unchanged
                s.status = "SUCCESS"
    except Exception as exc:  # recorded, never swallowed silently
        log.exception("ingest failed")
        s.status, s.error_message = "ERROR", f"{type(exc).__name__}: {exc}"[:1000]
    s.finished_at = datetime.now(timezone.utc)
    if conn is not None and not dry_run:
        from . import db
        db.record_run(conn, settings.db_schema, s.as_dict(), tr.rejections if tr else [])
    return s, tr
