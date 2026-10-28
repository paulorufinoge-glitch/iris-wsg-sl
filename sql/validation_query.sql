-- Example: parcel-level screening in SQL.
--   psql "$IRIS_DB_DSN" -v parcel_wkt="'POLYGON((354020 5455020,354060 5455020,354060 5455060,354020 5455060,354020 5455020))'" \
--        -v parcel_srid=25832 -f sql/validation_query.sql
WITH parcel AS (
    SELECT ST_Transform(ST_GeomFromText(:parcel_wkt, :parcel_srid), 25832) AS geom
)
SELECT z.source_zone_id, z.zone_class, z.status,
       CASE WHEN ST_Covers(z.geom, p.geom)                         THEN 'WITHIN'
            WHEN ST_Area(ST_Intersection(z.geom, p.geom)) = 0      THEN 'TOUCHES'
            ELSE 'PARTIAL' END                                     AS relation,
       round(ST_Area(ST_Intersection(z.geom, p.geom))::numeric, 2) AS overlap_m2
FROM iris_staging.water_protection_zones z
JOIN parcel p ON ST_Intersects(z.geom, p.geom)
ORDER BY z.zone_class, z.source_zone_id;

-- Integrity checks (all expected to return 0 rows / 0):
SELECT count(*) AS invalid_geoms   FROM iris_staging.water_protection_zones WHERE NOT ST_IsValid(geom);
SELECT count(*) AS wrong_srid      FROM iris_staging.water_protection_zones WHERE ST_SRID(geom) <> 25832;
SELECT state_code, source_zone_id, count(*) FROM iris_staging.water_protection_zones GROUP BY 1,2 HAVING count(*) > 1;
-- Review queue:
SELECT zone_class, status, count(*) FROM iris_staging.water_protection_zones GROUP BY 1,2 ORDER BY 1,2;
