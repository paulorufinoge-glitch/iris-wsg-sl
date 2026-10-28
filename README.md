# IRIS-WSG-SL – Saarland drinking-water-protection zones → PostGIS staging

Ingests the Saarland *Wasserschutzgebiete* layer into `iris_staging.water_protection_zones`
(MultiPolygon, EPSG:25832) for site-permitting / groundwater-restriction screening.
**Writes only to `iris_staging`**: the code refuses any other schema (including `iris_core`), and
`docker-compose.yml` starts its own throw-away PostGIS container.

## 1. Verification findings (what I actually checked, 2026-10-28)

| Assignment PDF | Finding | Handling |
|---|---|---|
| Endpoint `https://geoportal.saarland.de/wfs/wsg` | Did not respond to my capability request (client error). Not in the public catalogue records I found. | Not used. |
| Layer `sl_wsg_poly` | Not found anywhere. | Not used. |
| – | The official GDI-Saarland catalogue lists the layer **`Schutzgebiete_INS:Wasserschutzgebiet`** ("Wasserschutzgebiete des Saarlandes"). WFS 2.0.0 GetCapabilities **works** (`wsg.cli --source wfs inspect`): default CRS EPSG:25832, GeoJSON among output formats. | Confirmed by running it. |
| – | **WFS GetFeature on that endpoint answered `Parameter REQUEST invalid`** (OGC exception) on my first attempt. Cause not determined. | Not worked around by guessing. |
| – | The *same layer* is published as **OGC API Features** (landing page `https://geoportal.saarland.de/spatial-objects/215`): 353 features, GeoJSON, no access restriction stated, licence CC-BY 4.0 (© GDI-SL). | **Default live source** (`WSG_SOURCE=ogcapi`). WFS path kept (`--source wfs`) as the PDF's "WFS" pattern. |

Please confirm with the IRIS team which endpoint they intend. If `/wfs/wsg` is an internal alias, set `WFS_URL`/`WFS_TYPENAME`
(and `WSG_SOURCE=wfs`) – the rest of the pipeline is unchanged.

### Real schema and value domains (all 353 records read on 2026-10-28)
Attributes: `gml_id, NR, NAME, VERORDNUNG (ordinance date), ERFASSUNG, RECHTSGRUNDLAGE, SCHUTZZONE, LAND, INFO_KONTAKT, BEGUENSTIGT,
OIDEXT, BEMERKUNG, SCHUTZ_TYP, URL, OBJECTID, SHAPE_Length, SHAPE_Area` and, for 14 records, `UND_VERORDNUNG` (`NR` is empty in 1 record).
- `SCHUTZZONE` ∈ {`I`, `II`, `III`, `B`} → loaded: Zone I 259, Zone II 49, Zone III 44, **`B` 1** (`WSG Bietzener Wiesen`).
  `B` is not a typo: the ordinance for that area (Quellschutzgebietsverordnung „Bietzener Wiesen“, Saarland 2006) defines a separate
  **"Schutzzone B"** next to zones I–III (permit-required activities). I saw this in search-result excerpts only – the official legal portal blocks automated
  access, so the full text was **not** read: confirm before relying on `ZONE_B`. It has no place in the I > II > III severity ranking, so screening always flags it for review.
- `LAND` ∈ {`SL` (350 rows), `RLP` (3 rows)}: the layer also holds zones registered under Rheinland-Pfalz. `state_code` is therefore taken from `LAND`
  (flag `STATE_OTHER_THAN_REQUESTED` for the 3 RLP rows; they are kept, not dropped). Saarland ordinances can cover areas across the border under an
  administrative agreement with RLP – consistent with this, but not verified for these 3 rows.
- `ERFASSUNG` (how the geometry was captured): 259 Zone-I rows split into 238 *"generiert anhand eines Punktes"* (point-derived, ≈ 400–1,650 m²)
  and 21 from the ALK cadastral map. Point-derived Zone-I areas are **not surveyed boundaries**; parcel results near them are indicative only.
- **Not published**: status (in force / revoked), restriction category, extraction rate. Stored as `UNKNOWN`/`NULL`.

CRS check: the service sends GeoJSON lon/lat without a CRS member. Converting records' coordinates to EPSG:25832 and computing the area
reproduces the published `SHAPE_Area` (ratio 1.0000 and 0.9999) – automated in `tests/test_real_schema.py`. The live load had 0 rejected, 0 invalid, 0 wrong-SRID rows.

## 2. Data provenance
- Publisher: GDI-Saarland / Geodatenzentrum, Saarland environment ministry. Licence CC-BY 4.0 – keep "© GDI-SL" attribution.
- Per row: `source_url`, `source_typename`, declared `source_crs`, `retrieved_at`, full raw properties (`source_attributes`).
  `source_updated_at` stays NULL: the layer has no update-date attribute (`VERORDNUNG` is the legal ordinance date, kept in raw properties).
- The service landing page shows a dataset time range of 2018-05-18 … 2019-08-05; I could not establish how current the data is – **verify**.
- Per run: `iris_staging.water_protection_zone_ingest_runs` (HTTP status, counts, status `SUCCESS|EMPTY|ERROR|DRY_RUN`, errors,
  coverage notes, service metadata) and `..._rejections` (each rejected feature + reason).

## 3. Run it
```bash
cp .env.example .env            # set STAGING_DB_PASSWORD (letters/digits only; never commit .env)
docker compose up -d db
docker compose --profile test run --rm tests                                   # test-suite (incl. PostGIS tests)
docker compose --profile run run --rm app ingest --fixture tests/fixtures/wsg_sample.geojson   # offline demo (synthetic)
docker compose --profile run run --rm app inspect                              # live: collection metadata
docker compose --profile run run --rm app schema                               # live: attributes + distinct values
docker compose --profile run run --rm app ingest                               # live ingest (353 features expected)
```
Exit codes: 0 ok, 1 error, 2 empty response. A report is written to `out/quality_report.md`.
Parcel screening: `app screen --parcels parcels.geojson [--parcels-crs EPSG:xxxx] [--unknown-status-binding]` or `sql/validation_query.sql`.
Without Docker: Python ≥ 3.11, `pip install -r requirements-dev.txt`, `PYTHONPATH=src python -m pytest`.

## 4. Schema mapping (`iris_staging.water_protection_zones`)
Key `(state_code, source_zone_id)`; `source_zone_id` = `gml_id` (source-provided; features without an ID are rejected, never invented).

| Column | Source | Unknown/absent |
|---|---|---|
| `zone_class` | exact lookup of `SCHUTZZONE` (`I/II/III` → `ZONE_I/II/III`) | `UNKNOWN`, raw kept in `zone_code_raw`, flag `ZONE_UNMAPPED` |
| `zone_name` | `NAME` | NULL |
| `status`, `restriction_category`, `extraction_rate_*` | not published by this layer | `UNKNOWN` / NULL, listed in `attributes_missing` |
| `quality_flags` | `GEOMETRY_REPAIRED`, `ZONE_UNMAPPED`, … | |
| `source_attributes` | all raw properties (JSONB) – incl. `RECHTSGRUNDLAGE`, `URL`, `VERORDNUNG` | |
| `geom` | validated 2D MultiPolygon, EPSG:25832 | rejected if impossible |

**Classification is traceable, not inferred**: exact-match table (whitespace/case normalised) – no substring/regex rules
(`test_no_substring_inference`). Only values actually observed are in the table.

## 5. Geometry validation
Null/empty, non-polygon, malformed → rejected. Invalid polygons → `make_valid`, flagged `GEOMETRY_REPAIRED`. Multipart and holes preserved
(stored as MultiPolygon). CRS from the response, else `OGCAPI_SOURCE_CRS` (EPSG:4326), transformed with pyproj. Results must lie inside a
Saarland extent (catches mislabelled CRS) and exceed `MIN_AREA_M2`. Duplicate IDs in one batch → later duplicates rejected (reported).

## 6. Idempotency
`ON CONFLICT (state_code, source_zone_id) DO UPDATE … WHERE content_hash IS DISTINCT FROM EXCLUDED.content_hash`; the hash covers mapped +
raw attributes and geometry snapped to 1 mm. Re-loading identical data ⇒ `inserted=0, updated=0, unchanged=N` (tested). One transaction per load.
Rows that vanish upstream are **not deleted** (`last_seen_run_id` finds them); an empty/failed response never deletes anything.

## 7. Assumptions
1. `gml_id` is a stable identifier. It looks like a server-generated sequence (`Wasserschutzgebiet.N`, equal to `OBJECTID`), so it may change if the
   publisher rebuilds the layer; `OIDEXT` is more descriptive but I could not confirm it is unique.
2. The key is `(state_code, source_zone_id)` with `state_code` from the published `LAND` value as-is (`SL`, `RLP`), not normalised to ISO codes.
3. Zone severity ranking for screening (I > II > IIIA = III > IIIB) is my understanding – confirm with the LUA. `ZONE_B` and `UNKNOWN` are unranked → review.
4. No status is published, so screening treats status `UNKNOWN` as "needs review" by default (`--unknown-status-binding` counts it as binding).
   A catalogue entry for a related WMS says that dataset also contains areas revoked since 2009; this layer has no field to tell – **verify**.

## 8. Limitations
- **Currency of the data is unknown.** The service page shows a dataset time range of 2018-05-18 … 2019-08-05; newer ordinances exist (e.g. a Saarland
  ordinance „Hundscheid, Weißenborn und Eschenbruch“ dated 20 Feb 2026, published 12 Mar 2026). I did not check whether this layer contains it.
- Restriction categories and extraction rates are **not available** in this layer; the site-permitting use case needs the legal text (`URL`/`RECHTSGRUNDLAGE`),
  which I could not read automatically.
- Point-derived Zone-I geometries (238 of 259) are small squares around a well, not legal boundaries.
- "No zone found" ≠ "unrestricted" (planned areas, pending procedures, local rules). Screening output is decision support, not a legal determination.
- Unchanged rows keep their original `retrieved_at`/`source_url` (the hash ignores provenance on purpose); `last_seen_run_id` records each run that saw them.
- Cause of the WFS GetFeature error is unknown; the pipeline therefore defaults to the OGC API of the same layer.
- The OGC API sends no CRS; lon/lat is assumed (RFC 7946) and validated by area comparison and the extent check.

## 9. Evidence from the live run (2026-10-28)
- docs/quality_report_carga1.md: first load of the live layer: 353 received, 0 rejected, 353 inserted.
- docs/quality_report_carga2.md: identical second load: 0 inserted, 0 updated, 353 unchanged (idempotent load).
- Test suite: 53 tests passed (docker compose --profile test run --rm tests).
- Flagged rows: 3 with STATE_OTHER_THAN_REQUESTED (LAND = RLP) and 1 ZONE_B (see section 1).
- Note: the DB tests truncate the iris_staging tables; run the live ingest after the tests, not before.
