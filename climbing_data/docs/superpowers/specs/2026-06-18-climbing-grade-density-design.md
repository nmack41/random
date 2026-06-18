# Sport-climbing grade-density explorer — Design

**Date:** 2026-06-18
**Status:** Revised 2026-06-18 — claims verified against the upstream `OpenBeta/parquet-exporter` source
**Author:** Nick Mackowski (with Claude)

> **Revision note (2026-06-18):** §3–§10 were updated after reading the upstream `parquet-exporter`
> source (`schema.sql`, `examples/`, `config.yaml`, `export.py`). **Verified:** the pre-built parquet
> does drop the full `pathTokens` array, so Approach A is justified. **Corrected:** the `config.yaml`
> shape; the fact that `regions` filters by **country only**; that the export is a full-world crawl;
> and the crag definition — `path_tokens[-2]` reintroduced a fixed-depth assumption, now replaced by
> an anchor on the target-area position. One residual decision (crag granularity) is in §10.

## 1. Goal

Build an exploratory Jupyter notebook that measures, for the **Red River Gorge** (Kentucky),
**how many sport routes of each YDS grade exist in each crag and wall**. The headline
question is "how many 5.10s are in this crag/wall," answerable for any grade band and any
unit of the area hierarchy. The region is parameterized, so the same notebook works for
other areas, but RRG is the target it ships pointed at.

This is an analysis/exploration deliverable, not a product or service.

## 2. Decisions (locked during brainstorming)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Deliverable | Exploratory Jupyter notebook | Lowest-friction way to answer open-ended density questions; matches existing notebooks in the `random` repo. |
| 2 | Geographic scope | Red River Gorge, KY (parameterized) | Target is RRG, selected by area-name membership in `path_tokens` (robust to RRG's uneven nesting depth); a parameter cell lets the area be swapped without code changes. |
| 3 | Disciplines / grades | Sport only, Yosemite Decimal System (YDS) | Matches the "how many 5.10s" framing; avoids mixing grade systems. |
| 4 | Grade granularity | Store letter (`5.10a`), roll up to band (`5.10`) | Most flexible with minimal extra work; band view answers the headline question, letter view is available when wanted. |
| 5 | Crag/wall definition | Leaf area = wall (robust); crag = the token one level below the target area (`path_tokens[index(AREA)+1]`), **not** the leaf's parent; roll up via full ancestor path | The exporter fetches only leaf areas, so `path_tokens[-1]` is always a true wall/sector. But defining crag as the leaf's parent (`[-2]`) would reintroduce a fixed-depth assumption — RRG nests irregularly, so `[-2]` lands at different semantic levels per route. Anchoring crag on the target-area position keeps it at a stable level. Granularity to confirm — see §10. |
| 6 | Data acquisition | Approach A — custom one-time export from `parquet-exporter` including full `pathTokens` | Current data (weekly source) + exact leaf/parent hierarchy model, in exchange for a small one-time setup. |
| 7 | Density metric | Raw counts (with a per-unit total-routes column for context/sorting) | Answers "how many" literally; no normalization requested. |

## 3. Data source

**OpenBeta** is the chosen source — it publishes openly-licensed (CC0) climbing data and
is the cleanest of the three candidates evaluated:

- `OpenBeta/parquet-exporter` — exports the live OpenBeta GraphQL data to Apache Parquet.
  Ships a weekly pre-built release, *and* can be customized and run locally. **This is our path.**
- `OpenBeta/climbing-data` — curated pickles, but only an **August 2020** snapshot whose
  schema carries a single `parent_sector` level (no full ancestor path). Rejected: stale and
  too shallow for the crag→wall model.
- The two Mountain Project scrapers (`rohanbk`, `alexcrist`) — require live crawling against
  Mountain Project (ToS gray area, fragile) and neither reliably captures grades. Rejected.

### Why a custom export (Approach A) rather than the pre-built parquet

**Verified against the upstream `schema.sql`:** the default schema that generates the weekly
pre-built release selects only `list_element(pathTokens, 1..5)` (named `country … crag`) and
emits **no** array column. For deeply nested areas the true leaf (the wall/sector) lives below
level 5 and is dropped; for shallow areas the `crag` column is null. That is exactly the
fixed-depth pitfall decision #5 rejects. Emitting the full `pathTokens` array lets us derive the
wall as `pathTokens[-1]` — always a true leaf, because the exporter fetches only leaf areas (§6.1)
— robust to depth.

Note: the upstream `examples/schema-extended.sql` *already* emits the full array
(`pathTokens AS full_location_path`), so our customization is small — effectively the
sport/USA-filtered example plus one array column.

## 4. The one-time export (documented in `DATA.md`)

Steps the user runs once (and re-runs to refresh data):

1. `git clone https://github.com/OpenBeta/parquet-exporter && cd parquet-exporter`
2. `pip install -r requirements.txt`
3. Copy our `exporter/config.yaml` (sets `export.regions: ["USA"]` — note the **nested** key, not a
   top-level `regions:`) and `exporter/schema.sql` over the clone's.
4. `python export.py` → produces a parquet.
5. Move it to `climbing_data/data/usa-sport-climbs.parquet`.

> **Expect a long run, not a quick step.** `export.py` queries *every* country from the API and
> applies the `regions` filter only **after** fetching (`filter_climbs` matches `pathTokens[0]`).
> So `export.regions: ["USA"]` shrinks the output parquet but **not** the network work — step 4 is
> a full global crawl (paginated at 500/page, with retries), not a USA-only pull. One-time cost,
> but budget minutes.

Our `schema.sql` adapts the upstream `examples/schema-usa-sport-only.sql` (which is *already*
sport- and USA-filtered). We add a `country` column and the full `path_tokens` array, rename the
grade column to `grade_yds`, and **intentionally drop the example's `lat/lng IS NOT NULL` filters**
(a route should count toward density even if it has no coordinates). It selects at minimum:

```sql
SELECT
    uuid                        AS climb_id,
    name                        AS climb_name,
    CAST(grades.yds AS VARCHAR) AS grade_yds,
    type.sport                  AS is_sport,
    type.trad                   AS is_trad,
    -- named levels for convenient filtering (DuckDB list_element is 1-based)
    list_element(pathTokens, 1) AS country,
    list_element(pathTokens, 2) AS state_province,
    list_element(pathTokens, 3) AS region,
    -- the full ancestor path, for depth-robust leaf/parent derivation
    pathTokens                  AS path_tokens,
    metadata.lat                AS latitude,
    metadata.lng                AS longitude
FROM climbs
WHERE type.sport = true
  AND list_element(pathTokens, 1) = 'USA';
```

`path_tokens` is written as a Parquet list column and read by pandas (via pyarrow) as a
Python list per row. We emit **both** the named level columns (`country`,
`state_province`, `region`) and the full `path_tokens` array on purpose: filtering by
state/region uses the named columns (no index arithmetic), the **wall** is `path_tokens[-1]`
(the leaf — robust to depth, since the exporter emits only leaf areas), and the **crag** is
anchored on the target-area position (`path_tokens[index(AREA)+1]`, see §6.1) rather than a
fixed offset like `[-2]`, which would drift with nesting depth.

**Note on region scoping:** `config.yaml`'s `export.regions` filters by **country only** —
confirmed in the upstream `export.py`, where `filter_climbs` keeps rows whose `pathTokens[0]` is in
`regions`. Sub-country area names will **not** work, so country-wide export + in-notebook RRG
filtering is the only path, not a fallback (a USA sport-only parquet is small enough for pandas
anyway). The RRG focus is applied in the notebook (see §6): coarse-filter on
`state_province == "Kentucky"`, then keep routes whose `path_tokens` contains `"Red River Gorge"`.

## 5. Repo layout

```
climbing_data/
├── README.md                  # what it is + quickstart
├── DATA.md                    # the one-time export steps (§4)
├── requirements.txt           # pandas, duckdb, pyarrow, matplotlib, jupyter, pytest
├── exporter/
│   ├── schema.sql             # customized export schema (§4)
│   └── config.yaml            # sets export.regions: ["USA"] (country-level filter)
├── climbing_density.py        # pure, testable transforms (§6)
├── test_climbing_density.py   # pytest unit tests (§7)
├── grade_density.ipynb        # the exploratory notebook (§6)
└── data/                      # gitignored; holds usa-sport-climbs.parquet
```

Rationale for splitting logic into `climbing_density.py`: the grade-parsing and
hierarchy-derivation logic has real edge cases and benefits from unit tests; keeping it in a
module keeps the notebook readable and the logic verifiable. The notebook imports the module
and focuses on filtering, aggregation calls, and visualization.

## 6. Components

### 6.1 `climbing_density.py` (pure functions)

- `load_climbs(parquet_path) -> DataFrame` — read parquet; raise a clear, actionable error
  if the file is missing (points at `DATA.md`).
- `parse_yds_grade(grade_yds: str) -> {grade_letter, grade_band, grade_rank}` —
  - `grade_band`: strip the letter/modifier → `"5.10a"`→`"5.10"`, `"5.10-"`→`"5.10"`,
    `"5.10a/b"`→`"5.10"`. For a cross-band slash like `"5.10d/5.11a"`, take the **first** band
    (`"5.10"`) — pin this rule explicitly in tests.
  - `grade_letter`: the finest available form, preserved as-is.
  - `grade_rank`: integer for correct ordering using the weighting `10*num + letter_weight`
    (so `5.9`→90 sorts below `5.10a`→100+, sidestepping the lexical 5.9-vs-5.10 trap). This is a
    ~5-line function — implement it inline and test it; do **not** assume you'll find OpenBeta's
    `grade_rank_calculation.py` (not at the root of `parquet-exporter` or `climbing-data`; the
    formula above is all that's needed).
  - Unparseable / non-YDS values → flagged for the **"unparsed" bucket**, never silently dropped.
- `filter_sport(df) -> DataFrame` — keep rows where `is_sport` is true and `grade_yds`
  parses as YDS (multi-discipline routes kept as long as sport flag set); dedupe by `climb_id`.
  (Defensive: the export SQL already filters `type.sport = true`, so this mainly guards a
  differently-built parquet and drops sport routes without a parseable YDS grade.)
- `filter_area(df, state=None, area=None) -> DataFrame` — coarse-filter on `state_province`
  when `state` is given, then keep rows whose `path_tokens` **contains** `area` (membership,
  not a fixed level) when `area` is given. Defaults target RRG (`state="Kentucky"`,
  `area="Red River Gorge"`). Raises a friendly error listing close matches if `area` is
  absent from the data.
- `derive_hierarchy(df, area="Red River Gorge") -> DataFrame` — add `wall = path_tokens[-1]` (the
  leaf area; robust because the exporter emits only leaf areas). Derive `crag` by **anchoring on
  the target area**: let `i = path_tokens.index(area)`; `crag = path_tokens[i+1]` when it exists.
  This holds crag at a stable semantic level instead of `path_tokens[-2]`, which drifts with
  nesting depth (the same fixed-depth pitfall decision #5 rejects). Retain the full `path_tokens`;
  build a stable `wall_key`/`crag_key` from the **path tuple** (not display name) so
  identically-named walls in different crags don't merge. Edge cases: leaf is a direct child of
  `area` (`i+1` is the leaf) → crag falls back to wall and is flagged; `area` not in the path →
  flagged (should not occur after `filter_area`).
- `density_matrix(df, unit="crag"|"wall", grade_level="band"|"letter") -> DataFrame` —
  pivot of unit × grade with **raw route counts**, grade columns ordered by `grade_rank`,
  plus a `total_routes` column. `unit` can also be any ancestor level for roll-up.
- `top_units_for_grade(df, grade, unit, n) -> DataFrame` — ranked "top N crags/walls for grade X."

### 6.2 `grade_density.ipynb`

1. **Parameter cell:** `STATE = "Kentucky"`, `AREA = "Red River Gorge"` (the ships-with
   default; change these to retarget the notebook). `AREA` is matched by membership in
   `path_tokens`.
2. Load → `filter_sport` → `filter_area(STATE, AREA)` → parse grades → `derive_hierarchy(area=AREA)`
   → report the unparsed bucket (count + examples).
3. **Sanity check the crag anchor:** print the distinct tokens at each level below `AREA` once, so
   the crag granularity (§6.1/§10) can be eyeballed and the anchor offset adjusted in the
   parameter cell if `AREA+1` is too coarse/fine — no code change.
4. **Crag × grade-band matrix** and **wall × grade-band matrix** (counts), sortable by any grade.
5. **Top N crags/walls** for a chosen grade (e.g. top 5.10 crags).
6. **Grade histogram** for a single selected crag/wall.
7. **Heatmap** of grade distribution across the walls within one selected crag.

## 7. Testing

TDD on `climbing_density.py` using pytest:

- `parse_yds_grade`: table of inputs → expected `{letter, band, rank}`, covering
  `5.6`, `5.10a`, `5.10`, `5.10-`, `5.10+`, `5.10a/b`, `5.10d/5.11a` (cross-band → first band),
  `5.11d`, and non-YDS/garbage (→ unparsed). Assert rank ordering is monotonic across a known
  sequence (including `5.9` < `5.10a`).
- `derive_hierarchy`: sample frames with deep paths, a leaf that is a direct child of `AREA`
  (crag falls back to wall), and duplicate wall names across different crags → correct leaf,
  correct `AREA`-anchored crag, and distinct path-tuple keys.
- `filter_area`: a frame mixing RRG and non-RRG paths (RRG appearing at different depths)
  → only the RRG-subtree rows survive; an absent area name raises with suggestions.
- `density_matrix`: tiny synthetic frame → expected counts, column ordering, and totals.
- `load_climbs` / `top_units_for_grade`: a missing file raises the actionable `DATA.md` error;
  a small frame yields the expected top-N ordering for a chosen grade.

## 8. Error handling

- Missing/unreadable parquet → explicit error message pointing to `DATA.md`.
- Unparseable grades → collected into a reported "unparsed" bucket (count + sample values),
  surfaced in the notebook, not dropped silently.
- Leaf that is a direct child of the target area → wall defined; crag flagged as falling back to
  wall (no intermediate level exists to anchor on).
- Empty result after the area filter → friendly message listing the area names actually
  present under the chosen state (so a mismatch like "Red River" vs "Red River Gorge" is obvious).

## 9. Out of scope (YAGNI)

- Trad, bouldering, top-rope, and non-YDS grade systems.
- Normalized/share-based density metrics (counts only, per decision #7).
- Maps / geospatial visualization (coordinates are carried but not plotted in v1).
- Any live API calls at analysis time (data is a local parquet).
- A packaged CLI, web app, or dashboard.

## 10. Open questions

**One residual decision, worth closing before coding — crag granularity.** With wall = leaf and
crag anchored at `path_tokens[index(AREA)+1]` (§6.1), the crag level is whatever sits one step
below `"Red River Gorge"`. That may be too coarse if RRG's first sublevel is a large grouping
(e.g. `"Muir Valley"`) that itself contains many crags. Resolve by inspecting one known
wall→crag→RRG example in the data (the §6.2 sanity-check cell prints the candidate levels), then
either accept `AREA+1`, pick `AREA+2`, or expose the offset as a parameter. This is the one choice
that determines whether the headline crag × grade matrix is trustworthy.

Non-blocking, verify during implementation: the exact spelling/structure of RRG in the export —
whether it is a single `"Red River Gorge"` node or split (e.g. northern/southern or a Muir Valley
grouping). The membership-based `filter_area` plus the empty-result diagnostic (§8) make this easy
to confirm and adjust in the parameter cell without code changes. The export stays country-scoped
(USA), so retargeting to any other US area needs only a parameter change, no re-export.

_Resolved during review:_ `config.yaml`'s `regions` accepts **country names only** (it filters on
`pathTokens[0]`, per the upstream `export.py`), so the export cannot be narrowed below USA — see §4.
