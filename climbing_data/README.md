# Sport-climbing grade-density explorer

How many sport routes of each YDS grade live in each **crag** and **wall**? This is an exploratory
Jupyter notebook that answers that for the **Red River Gorge** (Kentucky) — "how many 5.10s are in
this crag?" — for any grade band and any unit of the area hierarchy. The area is a parameter, so the
same notebook retargets to any US area without code changes.

Data is [OpenBeta](https://openbeta.io) (CC0). Analysis only — no product, service, or live API calls.

## Quickstart

```bash
# 1. Environment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Data — generate the parquet once (see DATA.md for the full procedure)
#    ...produces data/usa-sport-climbs.parquet

# 3. Tests (no data needed — they run on synthetic frames)
.venv/bin/python -m pytest -q

# 4. Explore
.venv/bin/jupyter lab grade_density.ipynb     # or: jupyter notebook
```

Open the notebook, run all cells, then tweak the **parameter cell** (`AREA`, `STATE`, `CRAG_OFFSET`,
`GRADE`, `FOCUS_CRAG`) to ask your own questions.

## Interactive crag finder

Prefer clicking to coding? Build a **self-contained HTML page** that ranks RRG crags by the grades
you want to climb:

```bash
.venv/bin/python build_recommender.py      # writes rrg_grade_recommender.html
open rrg_grade_recommender.html            # or just double-click it in any browser
```

Check the grade bands you climb and the page instantly ranks crags by **route count** and **% fit**
(the share of that crag's sport routes in your range) — no server, no internet, fully offline.
Regenerate after a data refresh. The generated page is a build artifact (gitignored); the
`recommender_template.html` it is built from is committed.

## What you get

- **Crag × grade-band** and **wall × grade-band** matrices — raw route counts, columns ordered by
  difficulty, with a `total_routes` column.
- **Top-N crags/walls** for a chosen grade.
- A **grade histogram** for one crag/wall and a **heatmap** of grades across the walls of a crag.
- An **unparsed-grade report** (non-YDS values are surfaced, never silently dropped) and a
  **crag-anchor sanity check** that prints the hierarchy levels below the target area.

## Layout

```
climbing_data/
├── README.md                 # this file
├── DATA.md                   # the one-time OpenBeta export procedure
├── requirements.txt          # pandas, pyarrow, duckdb, matplotlib, jupyter, pytest
├── exporter/
│   ├── schema.sql            # custom export schema (full path_tokens array)
│   └── config.yaml           # export.regions: ["USA"]
├── climbing_density.py        # pure, tested transforms (grade parsing, hierarchy, matrices)
├── test_climbing_density.py   # pytest unit tests
├── build_recommender.py       # builds the crag-finder HTML from the parquet
├── recommender_template.html  # crag-finder view + scoring JS (payload placeholder)
├── test_build_recommender.py  # crag-finder build smoke test
├── grade_density.ipynb        # the exploratory notebook
├── rrg_grade_recommender.html # gitignored generated crag-finder page
└── data/                      # gitignored; holds usa-sport-climbs.parquet
```

## How it works (the two ideas worth knowing)

- **Grades** are parsed into a *band* (`5.10a` → `5.10`), a *letter* (finest form, as-is), and an
  integer *rank* (`10·number + letter-weight`) so grade columns sort by real difficulty —
  `5.9` (90) below `5.10a` (101), sidestepping the lexical `5.9`-vs-`5.10` trap.
- **Hierarchy** uses the full `path_tokens` array: the **wall** is the leaf (`path_tokens[-1]` —
  always a true wall, because the exporter only fetches leaf areas), and the **crag** is anchored
  relative to the target area (`path_tokens[index(AREA) + CRAG_OFFSET]`) rather than at a fixed
  depth, so it stays at a stable semantic level despite the Gorge's uneven nesting. Stable
  *path-tuple* keys keep identically-named walls in different crags from merging.

Design rationale: [`docs/superpowers/specs/2026-06-18-climbing-grade-density-design.md`](docs/superpowers/specs/2026-06-18-climbing-grade-density-design.md).
