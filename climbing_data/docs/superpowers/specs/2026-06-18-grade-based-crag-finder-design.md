# Red River Gorge grade-based crag finder — Design

**Date:** 2026-06-18
**Status:** Approved (brainstormed 2026-06-18)
**Author:** Nick Mackowski (with Claude)

Extends the existing `climbing_data` project (see
[`2026-06-18-climbing-grade-density-design.md`](2026-06-18-climbing-grade-density-design.md))
with an interactive front-end. It reuses the tested `climbing_density.py` transforms and the
OpenBeta USA export — no new data, no new grade/hierarchy logic.

## 1. Goal

A single **self-contained HTML file** that lets a climber check the YDS grade bands they want to
climb and instantly see which **Red River Gorge crags** best fit — ranked by both the raw **count**
of routes in those grades and the **% fit** (share of the crag that falls in range). Open it in any
browser; no server.

## 2. Decisions (locked during brainstorming)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Deliverable | One self-contained HTML file, vanilla JS, **no charting library** | Portable + offline (double-click to open); a ranked bar list needs no library, keeping the file ~80 KB with zero CDN dependency. |
| 2 | Geographic scope | Within Red River Gorge | Extends the current RRG focus — "which crag should I go to," not "where in the US." |
| 3 | Location unit | Crag (AREA+2), with preserve (AREA+1) shown as context | The resolved named-crag level (The Motherlode, Drive-By Crag); the preserve (PMRP, Muir Valley) orients the user. |
| 4 | Grade selector | YDS **bands** (5.6–5.14) as checkboxes | Matches "check which grades"; letters would be 40+ controls. |
| 5 | Ranking metrics | Show **both** `count` (routes in selected bands) and `% fit` (count ÷ crag's all-grade total); sortable; default `count` desc | Count answers "most climbing at my level"; % fit answers "don't make me hike past routes I can't climb." |
| 6 | Where ranking runs | Client-side in JS, from an embedded JSON payload | Keeps the file self-contained and interactive with no server or rebuild per selection. |
| 7 | Build & version control | Committed Python builder + HTML template; the **generated HTML is gitignored** (a build artifact, like `data/`) | Reuses the tested module; regenerate after a data refresh with one command. |

## 3. Architecture

```
parquet → load_climbs → filter_sport → filter_area(RRG) → add_grade_columns
        → derive_hierarchy(crag_offset=2) → recommender_payload(df)
        → JSON → injected into recommender_template.html → rrg_grade_recommender.html
        → browser: checkboxes → JS recompute → re-rendered leaderboard
```

All Python is the existing, tested pipeline plus one new pure function. The HTML/JS is a thin view
over the embedded payload.

## 4. Components

### 4.1 `climbing_density.recommender_payload(df, area="Red River Gorge", crag_offset=2)` (new, pure)

Takes a **prepared** DataFrame — one that already has `grade_band`, `grade_rank`, `crag`,
`crag_key` (i.e. post `add_grade_columns` + `derive_hierarchy`), matching `density_matrix`'s
contract. Returns a JSON-serializable dict:

```json
{
  "area": "Red River Gorge",
  "bands": ["5.6", "5.7", "...", "5.14"],
  "crags": [
    {"crag": "The Motherlode",
     "preserve": "Pendergrass-Murray Recreational Preserve (PMRP)",
     "total": 63,
     "counts": {"5.10": 1, "5.11": 15, "5.12": 29, "5.13": 15, "5.14": 3}}
  ]
}
```

- `bands` — all parsed bands present, ordered by `grade_rank` (so the checkbox row reads
  5.6 → 5.14, not lexically).
- One entry per **`crag_key`** (identically-named crags in different preserves stay distinct);
  `crag` is the display name.
- `total` — count of all parsed routes in that crag (the `% fit` denominator) = sum of `counts`.
- `preserve` — the AREA+1 token, read from `crag_key` (`crag_key[index(area)+1]`). If the crag key
  has no token between the area and the crag (offset/edge case), `preserve` is `None`.
- Only parsed rows (`grade_band` not null) contribute. `counts` omits zero bands (JS treats missing
  as 0) to keep the payload small.

### 4.2 `recommender_template.html` (new)

A static HTML page (the mockup approved during brainstorming) with:

- A placeholder token (`__PAYLOAD_JSON__`) where the builder injects the payload.
- A grade-checkbox row generated from `payload.bands`.
- A "top pick" headline and a sort toggle (`count` / `% fit`).
- A leaderboard of crags as CSS bars (no chart library), each row: crag, preserve, bar, count, % fit.
- Vanilla JS: on any checkbox/sort change → for each crag compute `count = Σ counts[band]` over
  checked bands and `fit = count / total`; drop crags with `count == 0`; sort by the active metric
  desc; render the top N (default 25) and a "showing N of M crags" line.
- Empty states: no grades checked → prompt to pick at least one; no matching crags → friendly note.

Styling mirrors the approved mockup (flat, accessible, works light/dark via system colors). No
external resources — fully offline.

### 4.3 `build_recommender.py` (new, committed)

Parameter block at top (`PARQUET_PATH`, `STATE="Kentucky"`, `AREA="Red River Gorge"`,
`CRAG_OFFSET=2`, `OUTPUT="rrg_grade_recommender.html"`). Runs the §3 pipeline, builds the payload,
reads the template, replaces `__PAYLOAD_JSON__`, writes the output HTML, prints a one-line summary
(crag count, route count). Reuses `load_climbs`' missing-file error.

## 5. Testing (TDD)

- **`recommender_payload`** (in `test_climbing_density.py`): synthetic prepared frame →
  - `bands` ordered by rank (incl. 5.9 before 5.10);
  - per-crag `counts` correct and zero bands omitted;
  - `total` = the crag's all-grade route count (the `% fit` denominator);
  - `preserve` read correctly from the key; identically-named crags in different preserves stay
    distinct (grouped by `crag_key`, not name).
- **`build_recommender`** (new `test_build_recommender.py`): write a tiny fixture parquet to
  `tmp_path`, point the builder at it → assert the HTML is written, contains valid injected JSON
  (round-trips with `json.loads`), contains a known crag name, and has **no leftover placeholder**.
- The JS scoring is a trivial, by-eye-verifiable mirror of the tested payload semantics (sum of
  counts; divide by total). No JS test harness is introduced (YAGNI).

## 6. Error handling

- Missing parquet → reuse `load_climbs`' actionable error pointing at `DATA.md`.
- Empty area filter → reuse `filter_area`'s friendly "closest names" error.
- Empty/zero payload → builder prints a warning and still writes a page that shows the empty state.
- All grades unchecked or no matches → handled in JS with a clear message, not a blank screen.

## 7. Repo layout additions

```
climbing_data/
├── build_recommender.py          # NEW — builds the HTML from the parquet
├── recommender_template.html     # NEW — view + scoring JS (placeholder for payload)
├── climbing_density.py           # + recommender_payload()
├── test_climbing_density.py      # + recommender_payload tests
├── test_build_recommender.py     # NEW — build smoke test
└── rrg_grade_recommender.html    # NEW — gitignored generated artifact
```

`README.md` gains a short "Interactive crag finder" section: `python build_recommender.py` →
open `rrg_grade_recommender.html`.

## 8. Out of scope (YAGNI)

- Charting libraries, maps/geospatial (coordinates are in the data but unused here).
- Wall-level drill-down, letter-grade selection, and any scope beyond RRG (USA-wide, multi-area).
- A server, deployment, or build pipeline; persisting the user's selection between visits.
- New grade-parsing or hierarchy logic — all reused from `climbing_density.py`.
