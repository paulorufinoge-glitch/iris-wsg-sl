"""CLI:  python -m wsg.cli {inspect,schema,ingest,screen} ..."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import wfs_client
from .config import AttributeMapping, Settings


def _settings(args) -> Settings:
    from dataclasses import replace
    st = Settings.from_env()
    return replace(st, source=args.source) if getattr(args, "source", None) else st


def cmd_inspect(args) -> int:
    st = _settings(args)
    if st.source == "ogcapi":
        from dataclasses import replace
        fr = wfs_client.get_features_ogcapi(wfs_client.make_session(), replace(st, page_size=1), max_pages=1)
        print("source: OGC API Features  ", st.ogcapi_items_url)
        for k, v in fr.metadata.items():
            print(f"  {k}: {v}")
        if fr.features:
            print("  properties of first feature:", ", ".join((fr.features[0].get("properties") or {}).keys()))
        return 0
    caps = wfs_client.fetch_capabilities(wfs_client.make_session(), st)
    print(f"service: {caps.title}  WFS {caps.version}")
    print("GetFeature outputFormats:", ", ".join(caps.output_formats) or "(not parsed)")
    for f in caps.feature_types:
        mark = "  <== configured" if f.name == st.typename else ""
        print(f"- {f.name}  default={f.default_crs}  other={len(f.other_crs)}{mark}")
    for w in wfs_client.check_capabilities(caps, st):
        print("WARNING:", w)
    return 0


def cmd_schema(args) -> int:
    st = _settings(args)
    s = wfs_client.make_session()
    if st.source == "ogcapi":
        fr = wfs_client.get_features_ogcapi(s, st)
        print(json.dumps(wfs_client.inspect_schema(fr.features), indent=2, ensure_ascii=False))
        print(f"\n# {len(fr.features)} of {fr.number_matched} features read; CRS={fr.source_crs} "
              f"({fr.source_crs_origin})", file=sys.stderr)
        return 0
    caps = wfs_client.fetch_capabilities(s, st)
    wfs_client.check_capabilities(caps, st)
    from dataclasses import replace
    fr = wfs_client.get_features(s, replace(st, page_size=args.limit), caps)
    print(json.dumps(wfs_client.inspect_schema(fr.features), indent=2, ensure_ascii=False))
    print(f"\n# {len(fr.features)} features sampled; CRS={fr.source_crs} ({fr.source_crs_origin})", file=sys.stderr)
    return 0


def cmd_ingest(args) -> int:
    from . import ingest, report
    st = _settings(args)
    mapping = AttributeMapping.from_file(st.mapping_file)
    conn = None
    if not args.dry_run:
        from . import db
        if not st.db_dsn:
            print("IRIS_DB_DSN not set (use --dry-run to skip the database)", file=sys.stderr)
            return 1
        conn = db.connect(st.db_dsn)
        db.ensure_schema(conn)
    summary, tr = ingest.run_ingest(st, mapping, fixture=args.fixture, dry_run=args.dry_run, conn=conn)
    md = report.render(summary, tr)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(md, encoding="utf-8")
    print(md)
    return summary.exit_code


def cmd_screen(args) -> int:
    import geopandas as gpd
    from . import screening
    st = _settings(args)
    parcels = gpd.read_file(args.parcels)
    if parcels.crs is None:
        if not args.parcels_crs:
            print("parcels have no CRS; pass --parcels-crs", file=sys.stderr)
            return 1
        parcels = parcels.set_crs(args.parcels_crs)
    if args.parcel_id_col not in parcels.columns:
        parcels[args.parcel_id_col] = range(len(parcels))
    if args.fixture:
        from datetime import datetime, timezone
        from . import ingest
        from .transform import build_records
        mapping = AttributeMapping.from_file(st.mapping_file)
        fr = ingest.read_fixture(args.fixture, st)
        tr = build_records(fr.features, mapping=mapping, settings=st, source_crs=fr.source_crs,
                           source_url=fr.request_url, retrieved_at=datetime.now(timezone.utc))
        zones = screening.zones_gdf_from_records(tr.records)
    else:
        from . import db
        zones = screening.load_zones_gdf(db.connect(st.db_dsn), st.db_schema)
    kw = {}
    if args.unknown_status_binding:   # the real layer publishes no status attribute -> all UNKNOWN
        kw = {"binding_statuses": ("IN_FORCE", "UNKNOWN"), "review_statuses": ()}
    res = screening.screen_parcels(parcels, zones, parcel_id_col=args.parcel_id_col, **kw)
    print(res.to_csv(index=False))
    print(screening.summarize(res).to_csv(index=False), file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wsg")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--source", choices=["ogcapi", "wfs"], help="live source (default: ogcapi)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inspect", help="show service capabilities / layers").set_defaults(fn=cmd_inspect)
    p = sub.add_parser("schema", help="sample live features and list attributes + distinct values")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(fn=cmd_schema)
    p = sub.add_parser("ingest", help="fetch -> validate -> load iris_staging.water_protection_zones")
    p.add_argument("--fixture", help="load a local GeoJSON FeatureCollection instead of calling the WFS")
    p.add_argument("--dry-run", action="store_true", help="no database writes")
    p.add_argument("--report", default="out/quality_report.md")
    p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("screen", help="screen parcels against loaded zones")
    p.add_argument("--parcels", required=True)
    p.add_argument("--parcels-crs")
    p.add_argument("--parcel-id-col", default="parcel_id")
    p.add_argument("--fixture", help="use zones from a local fixture instead of the database")
    p.add_argument("--unknown-status-binding", action="store_true",
                   help="treat zones with unknown status as binding (real layer has no status field)")
    p.set_defaults(fn=cmd_screen)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.fn(args)
    except wfs_client.WfsError as exc:
        print(f"WFS error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
