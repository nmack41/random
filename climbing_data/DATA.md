# Getting the data — the one-time OpenBeta export

The notebook reads `data/usa-sport-climbs.parquet`, a USA-sport-only slice of the
[OpenBeta](https://openbeta.io) climbing dataset (CC0). It is **not** committed (it is large and
refreshable); you generate it once with the steps below, and re-run them whenever you want fresher
data (OpenBeta refreshes continuously).

## Why a custom export

OpenBeta ships a weekly pre-built parquet via [`OpenBeta/parquet-exporter`](https://github.com/OpenBeta/parquet-exporter),
but its default schema flattens only `pathTokens[1..5]` and drops the full path array. For deeply
nested areas the true leaf (the wall/sector) lives below level 5 and is lost — exactly the
fixed-depth pitfall this project avoids. So we run the exporter with a **custom schema** that emits
the full `pathTokens` array, letting the notebook derive the wall (`path_tokens[-1]`, always a true
leaf) and an area-anchored crag, robust to nesting depth.

Our two customization files live in [`exporter/`](exporter/):

- [`exporter/schema.sql`](exporter/schema.sql) — sport + USA filter, named levels **plus** the full
  `path_tokens` array, `grade_yds`; the example's `lat/lng IS NOT NULL` filters are intentionally
  dropped (a route should count toward density even without coordinates).
- [`exporter/config.yaml`](exporter/config.yaml) — sets `export.regions: ["USA"]`.

## Steps

```bash
# 1. Clone the upstream exporter somewhere outside this repo.
git clone https://github.com/OpenBeta/parquet-exporter
cd parquet-exporter

# 2. Install its dependencies (duckdb, pyyaml, requests).
pip install -r requirements.txt

# 3. Copy our customizations over the clone's defaults.
cp /path/to/climbing_data/exporter/config.yaml ./config.yaml
cp /path/to/climbing_data/exporter/schema.sql  ./schema.sql

# 4. Run the export. Produces ./usa-sport-climbs.parquet (see the timing note below).
python export.py

# 5. Move the parquet into this project's (gitignored) data/ directory.
mv usa-sport-climbs.parquet /path/to/climbing_data/data/usa-sport-climbs.parquet
```

Then run the notebook (`grade_density.ipynb`) — it loads from `data/usa-sport-climbs.parquet`.

## Expect a long run, not a quick step

`export.py` queries **every** country from the API and applies the `regions` filter only *after*
fetching (`filter_climbs` matches `pathTokens[0]`). So `export.regions: ["USA"]` shrinks the output
parquet but **not** the network work — step 4 is a full global crawl (paginated at 500/page, with
per-page retries), not a USA-only pull. One-time cost, but budget several minutes.

**`regions` is country-level only.** It filters on `pathTokens[0]`, so sub-country names (e.g.
`"Kentucky"`) will *not* work here. The Red River Gorge focus is applied later, in the notebook. A
USA-wide sport parquet is small enough for pandas, and keeping the export country-scoped means you
can retarget the notebook to **any** US area with only a parameter change — no re-export.

## Troubleshooting

- **`Countries query failed: 504 ...` at the very start.** Transient OpenBeta API gateway timeout.
  The initial countries query is *not* retried by `export.py`, so a single hiccup aborts the run —
  just re-run `python export.py`. (Per-page area queries *are* retried, so failures mid-crawl
  usually recover on their own.)
- **`No climbs remained after filtering!`** Your `config.yaml` `regions` is empty or not a country
  in `pathTokens[0]`. Confirm it reads `regions: ["USA"]` nested under `export:`.
- **Notebook raises "parquet not found".** You haven't completed step 5 — the file must be at
  `data/usa-sport-climbs.parquet`.
