# Quality & error report  (run 0580527b-c09f-460b-9e71-666f0cd36dca)

- Source: `https://geoportal.saarland.de/spatial-objects/215/collections/Schutzgebiete_INS:Wasserschutzgebiet/items`  layer `Schutzgebiete_INS:Wasserschutzgebiet`
- Declared source CRS: `EPSG:4326`  -> target `EPSG:25832`
- Started: 2026-10-28T13:31:17.313300+00:00   Status: **SUCCESS**
- Features received: 353; rejected: 0; repaired: 0
- Loaded: inserted 353, updated 0, unchanged 0

## Rejected features

| reason | count | source ids |
|---|---|---|
| (none) | 0 | |

## Accepted with flags

| source id | zone_class | status | flags | missing |
|---|---|---|---|---|
| Wasserschutzgebiet.159 | ZONE_III | UNKNOWN | STATE_OTHER_THAN_REQUESTED | extraction_rate, restriction, status, updated |
| Wasserschutzgebiet.275 | ZONE_II | UNKNOWN | STATE_OTHER_THAN_REQUESTED | extraction_rate, restriction, status, updated |
| Wasserschutzgebiet.278 | ZONE_II | UNKNOWN | STATE_OTHER_THAN_REQUESTED | extraction_rate, restriction, status, updated |

## Attributes NOT published by the source (rows without a value)

- extraction_rate: 353 of 353
- restriction: 353 of 353
- status: 353 of 353
- updated: 353 of 353

## Accepted by zone class

ZONE_B: 1, ZONE_I: 259, ZONE_II: 49, ZONE_III: 44

## Coverage limitations

- Coverage = zones published by GDI-Saarland in the configured layer only; absence of a zone is NOT proof that a site is unrestricted (planned/draft areas, pending procedures and local rules may not be included).
- Per-zone restriction text and permitted extraction rates are loaded only where the source publishes them; otherwise UNKNOWN / NULL with the attribute listed in attributes_missing.
- Rows no longer returned by the source are NOT deleted; use last_seen_run_id to find stale rows.
- Service declares no CRS in the GeoJSON; WGS84 lon/lat (RFC 7946 default) assumed and verified by the extent check
