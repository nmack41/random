-- Climbing grade-density export schema (custom).
--
-- Adapted from the upstream parquet-exporter's examples/schema-usa-sport-only.sql.
-- Differences from that example, all deliberate (see ../DATA.md and the design doc §4):
--   * grade column renamed `grade` -> `grade_yds`.
--   * added a `country` column and `is_sport` / `is_trad` flags.
--   * added the FULL pathTokens array as `path_tokens` (the example drops it). This is the
--     whole reason we run a custom export: the default schema flattens only pathTokens[1..5],
--     which loses the true leaf for deeply-nested areas and forces a fixed-depth crag. The
--     array lets the notebook derive wall = path_tokens[-1] (always a leaf, because the
--     exporter only fetches leaf areas) and a depth-robust, area-anchored crag.
--   * INTENTIONALLY dropped the example's `lat/lng IS NOT NULL` filters: a route should count
--     toward grade density even when it has no coordinates. Coordinates are still carried.
--
-- DuckDB note: list_element() is 1-based, so pathTokens[1] is the country.

SELECT
    uuid                        AS climb_id,
    name                        AS climb_name,
    CAST(grades.yds AS VARCHAR) AS grade_yds,
    type.sport                  AS is_sport,
    type.trad                   AS is_trad,

    -- Named levels for convenient, index-free filtering by state / region.
    list_element(pathTokens, 1) AS country,
    list_element(pathTokens, 2) AS state_province,
    list_element(pathTokens, 3) AS region,

    -- The full ancestor path, for depth-robust leaf (wall) and area-anchored crag derivation.
    pathTokens                  AS path_tokens,

    metadata.lat                AS latitude,
    metadata.lng                AS longitude

-- NOTE: no trailing semicolon — export.py interpolates this as a subquery: COPY ( <schema> ) TO ...
FROM climbs
WHERE type.sport = true
  AND list_element(pathTokens, 1) = 'USA'
