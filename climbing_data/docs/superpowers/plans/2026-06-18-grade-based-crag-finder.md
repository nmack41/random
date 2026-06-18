# RRG grade-based crag finder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a self-contained, offline HTML page that ranks Red River Gorge crags by the YDS grade bands a climber checks — by raw route `count` and by `% fit` — built from the existing tested `climbing_density` pipeline.

**Architecture:** One new *pure* Python function (`recommender_payload`) turns the prepared DataFrame into a JSON payload. A committed `build_recommender.py` runs the existing pipeline, injects that payload HTML-safely into a committed `recommender_template.html`, and writes a gitignored `rrg_grade_recommender.html`. All ranking/interactivity is client-side vanilla JS over the embedded payload — no server, no charting library, no CDN.

**Tech Stack:** Python 3.14 + pandas/pyarrow (existing `.venv`), pytest for tests, vanilla HTML/CSS/JS for the view. Test runner: `.venv/bin/python -m pytest -q`.

**Source spec:** [`docs/superpowers/specs/2026-06-18-grade-based-crag-finder-design.md`](../specs/2026-06-18-grade-based-crag-finder-design.md)

**Baseline:** 31 existing tests pass. Do not break them. The existing module functions reused here are `load_climbs`, `filter_sport`, `filter_area`, `add_grade_columns`, `derive_hierarchy` ([`climbing_density.py`](../../../climbing_density.py)).

---

## File structure (decomposition)

| File | Status | Responsibility |
|------|--------|----------------|
| `climbing_density.py` | modify | add one pure function `recommender_payload(df, area=...)` |
| `test_climbing_density.py` | modify | add `recommender_payload` unit tests |
| `recommender_template.html` | create | static view + scoring JS; `__PAYLOAD_JSON__` placeholder |
| `build_recommender.py` | create | run pipeline → payload → inject → write HTML |
| `test_build_recommender.py` | create | build smoke test on a fixture parquet |
| `.gitignore` | modify | ignore the generated `rrg_grade_recommender.html` (specific filename) |
| `README.md` | modify | add "Interactive crag finder" section + layout update |
| `rrg_grade_recommender.html` | generated | gitignored build artifact (created by Task 5, never committed) |

---

## Task 1: `recommender_payload` (pure function, TDD)

**Files:**
- Test: `test_climbing_density.py` (append a new section)
- Modify: `climbing_density.py` (add function after `density_matrix`)

- [ ] **Step 1: Write the failing tests**

Append to `test_climbing_density.py`:

```python
# --------------------------------------------------------------------------- #
# recommender_payload
# --------------------------------------------------------------------------- #

def _recommender_frame(crag_offset=2):
    """Prepared RRG frame for recommender_payload tests (mirrors _density_frame).

    Built so a single frame exercises every spec §5 case at crag_offset=2:
      - The Motherlode (PMRP): a deep, fallback-to-wall crag that still has a preserve;
      - The Arsenal under BOTH Muir Valley and PMRP: identically-named, must stay distinct;
      - Roadside Crag: directly under the area -> preserve None (never its own name).
    """
    rows = [
        {"climb_id": "1", "grade_yds": "5.13a",
         "path_tokens": ["USA", "Kentucky", RRG, "PMRP", "The Motherlode"]},
        {"climb_id": "2", "grade_yds": "5.12c",
         "path_tokens": ["USA", "Kentucky", RRG, "PMRP", "The Motherlode"]},
        {"climb_id": "3", "grade_yds": "5.12a",
         "path_tokens": ["USA", "Kentucky", RRG, "PMRP", "The Motherlode"]},
        {"climb_id": "4", "grade_yds": "5.9",
         "path_tokens": ["USA", "Kentucky", RRG, "Muir Valley", "The Arsenal"]},
        {"climb_id": "5", "grade_yds": "5.11a",
         "path_tokens": ["USA", "Kentucky", RRG, "PMRP", "The Arsenal"]},
        {"climb_id": "6", "grade_yds": "5.8",
         "path_tokens": ["USA", "Kentucky", RRG, "Roadside Crag"]},
    ]
    df = cd.add_grade_columns(_frame(rows))
    return cd.derive_hierarchy(df, area=RRG, crag_offset=crag_offset)


def _payload_crag(payload, name, preserve):
    """The single crag entry matching (display name, preserve) — keys are distinct by both."""
    hits = [c for c in payload["crags"] if c["crag"] == name and c["preserve"] == preserve]
    assert len(hits) == 1, f"expected one {name!r} under {preserve!r}, got {len(hits)}"
    return hits[0]


def test_recommender_payload_bands_are_rank_ordered_present_only():
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    assert p["area"] == RRG
    # rank order (5.9 before higher numbers), and 5.10 absent because no route is in it
    assert p["bands"] == ["5.8", "5.9", "5.11", "5.12", "5.13"]


def test_recommender_payload_counts_total_and_zero_omission():
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    ml = _payload_crag(p, "The Motherlode", "PMRP")
    assert ml["counts"] == {"5.12": 2, "5.13": 1}     # 5.12a + 5.12c roll up to 5.12
    assert "5.8" not in ml["counts"]                  # zero band omitted, not stored as 0
    assert ml["total"] == 3                           # == sum(counts) == the crag's route count


def test_recommender_payload_preserve_read_for_deep_crag():
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    arsenal_muir = _payload_crag(p, "The Arsenal", "Muir Valley")
    assert arsenal_muir["counts"] == {"5.9": 1}       # preserve = the AREA+1 token


def test_recommender_payload_preserve_none_never_own_name_directly_under_area():
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    road = [c for c in p["crags"] if c["crag"] == "Roadside Crag"]
    assert len(road) == 1
    assert road[0]["preserve"] is None                # NOT "Roadside Crag" — the strict-between guard


def test_recommender_payload_deep_fallback_crag_keeps_preserve_not_none_or_own_name():
    # The Motherlode falls back to the wall at crag_offset=2, yet PMRP sits strictly between
    # area and leaf -> preserve must be "PMRP", never None and never "The Motherlode".
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    assert _payload_crag(p, "The Motherlode", "PMRP")["preserve"] == "PMRP"


def test_recommender_payload_identically_named_crags_stay_distinct_by_key():
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    arsenals = sorted(c["preserve"] for c in p["crags"] if c["crag"] == "The Arsenal")
    assert arsenals == ["Muir Valley", "PMRP"]        # two distinct entries grouped by crag_key
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest test_climbing_density.py -k recommender -q`
Expected: FAIL — `AttributeError: module 'climbing_density' has no attribute 'recommender_payload'`

- [ ] **Step 3: Write the minimal implementation**

In `climbing_density.py`, add this function immediately after `density_matrix` (before `top_units_for_grade`), reusing `density_matrix`'s rank-ordering idiom:

```python
def recommender_payload(df, area="Red River Gorge"):
    """Build the JSON-serializable payload for the client-side crag recommender (design §4.1).

    Takes a PREPARED frame (post add_grade_columns + derive_hierarchy): it must already have
    grade_band, grade_rank, crag, crag_key — the same contract as density_matrix. Returns:

        {"area": str,
         "bands": [band, ...],                      # present bands, ordered by grade_rank
         "crags": [{"crag": str, "preserve": str|None, "total": int, "counts": {band: int}}]}

    One entry per crag_key, so identically-named crags in different preserves stay distinct;
    `crag` is the display name. `total` is the crag's parsed-route count (the % fit denominator)
    and equals sum(counts). `preserve` is the AREA+1 orienting token, read from crag_key only
    when a token sits strictly between the area and the crag (guard i + 1 < len(key) - 1);
    otherwise None, so a crag directly under the area is never mislabeled with its own name.
    """
    d = df[df["grade_band"].notna()]
    bands = d.groupby("grade_band")["grade_rank"].min().sort_values().index.tolist()
    crags = []
    for crag_key, g in d.groupby("crag_key"):
        raw = g["grade_band"].value_counts().to_dict()
        counts = {b: int(raw[b]) for b in bands if b in raw}      # band-ordered; zero bands omitted
        key = list(crag_key)
        i = key.index(area)
        preserve = key[i + 1] if i + 1 < len(key) - 1 else None   # strict-between guard (§4.1)
        crags.append({
            "crag": g["crag"].iloc[0],
            "preserve": preserve,
            "total": int(sum(counts.values())),
            "counts": counts,
        })
    return {"area": area, "bands": bands, "crags": crags}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest test_climbing_density.py -k recommender -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Run the FULL suite to confirm no regression**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (37 passed — the original 31 plus 6 new)

- [ ] **Step 6: Commit**

```bash
git add climbing_density.py test_climbing_density.py
git commit -m "feat: add recommender_payload() for the RRG crag finder"
```

---

## Task 2: `recommender_template.html` (view + scoring JS)

No JS unit harness (spec §5, §8 — YAGNI). This task creates the file; Task 3's build test and Task 5's browser check verify it. Each row is keyed by **array index**, never display name, so identically-named crags can't collide in the DOM.

**Files:**
- Create: `recommender_template.html`

- [ ] **Step 1: Create the template file**

Write `recommender_template.html` with exactly this content:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Red River Gorge — grade-based crag finder</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0 auto; padding: 1.5rem; max-width: 880px;
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
  .sub { opacity: .7; margin: 0 0 1.25rem; }
  fieldset { border: 1px solid color-mix(in srgb, currentColor 25%, transparent);
             border-radius: 8px; padding: .75rem 1rem 1rem; margin: 0 0 1rem; }
  legend { padding: 0 .4rem; font-weight: 600; }
  .bands { display: flex; flex-wrap: wrap; gap: .4rem .9rem; }
  .bands label { display: inline-flex; align-items: center; gap: .35rem; cursor: pointer; }
  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; margin: 0 0 1rem; }
  button.sort { font: inherit; padding: .3rem .7rem; border-radius: 6px; cursor: pointer;
                color: inherit; background: transparent;
                border: 1px solid color-mix(in srgb, currentColor 30%, transparent); }
  button.sort[aria-pressed="true"] { font-weight: 600;
                background: color-mix(in srgb, currentColor 12%, transparent); }
  .headline { font-size: 1.05rem; margin: 0 0 1rem; min-height: 1.5rem; }
  .headline strong { font-size: 1.15rem; }
  ol.board { list-style: none; margin: 0; padding: 0; display: grid; gap: .4rem; }
  li.row { padding: .5rem .6rem; border-radius: 6px;
           background: color-mix(in srgb, currentColor 5%, transparent); }
  .row .top { display: flex; align-items: baseline; gap: .5rem; }
  .row .name { font-weight: 600; }
  .row .preserve { opacity: .6; font-size: .85em; }
  .row .nums { margin-left: auto; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .row .nums .fit { opacity: .6; }
  .bar { height: 8px; border-radius: 4px; margin-top: .3rem;
         background: color-mix(in srgb, currentColor 65%, transparent); }
  .empty { opacity: .7; padding: 1.25rem 0; }
  .count-line { opacity: .6; font-size: .85em; margin: .75rem 0 0; }
</style>
</head>
<body>
  <h1>Red River Gorge — grade-based crag finder</h1>
  <p class="sub">Check the grades you climb; see which crags fit best.
     Data: OpenBeta (CC0), sport routes only.</p>

  <fieldset>
    <legend>Grades</legend>
    <div class="bands" id="bands"></div>
  </fieldset>

  <div class="controls">
    <span>Sort by</span>
    <button class="sort" id="sort-count" aria-pressed="true">Route count</button>
    <button class="sort" id="sort-fit" aria-pressed="false">% fit</button>
  </div>

  <p class="headline" id="headline"></p>
  <ol class="board" id="board"></ol>
  <p class="count-line" id="count-line"></p>

<script>
const PAYLOAD = __PAYLOAD_JSON__;
const TOP_N = 25;
let sortKey = "count";                       // "count" | "fit"

const $ = (id) => document.getElementById(id);
const bandsEl = $("bands"), boardEl = $("board");
const headlineEl = $("headline"), countLineEl = $("count-line");

// Build one checkbox per present band (empty bands never render). All checked initially.
for (const band of PAYLOAD.bands) {
  const label = document.createElement("label");
  const cb = document.createElement("input");
  cb.type = "checkbox"; cb.value = band; cb.checked = true;
  cb.addEventListener("change", render);
  label.append(cb, document.createTextNode(band));
  bandsEl.append(label);
}

$("sort-count").addEventListener("click", () => setSort("count"));
$("sort-fit").addEventListener("click", () => setSort("fit"));
function setSort(key) {
  sortKey = key;
  $("sort-count").setAttribute("aria-pressed", String(key === "count"));
  $("sort-fit").setAttribute("aria-pressed", String(key === "fit"));
  render();
}

const checkedBands = () =>
  [...bandsEl.querySelectorAll("input:checked")].map((cb) => cb.value);

function render() {
  const bands = checkedBands();
  boardEl.innerHTML = ""; headlineEl.textContent = ""; countLineEl.textContent = "";

  if (bands.length === 0) {
    showEmpty("Pick at least one grade to see matching crags.");
    return;
  }

  // count = sum of counts over checked bands; fit = count / total (sport denominator).
  const scored = PAYLOAD.crags.map((c, i) => {
    let count = 0;
    for (const b of bands) count += c.counts[b] || 0;
    return { i, crag: c.crag, preserve: c.preserve, total: c.total, count, fit: count / c.total };
  }).filter((s) => s.count > 0);

  if (scored.length === 0) {
    showEmpty("No crags have routes in the selected grades. Try widening your selection.");
    return;
  }

  // Active metric desc; deterministic tiebreaker: total desc, then crag name asc.
  scored.sort((a, b) =>
    (b[sortKey] - a[sortKey]) || (b.total - a.total) || a.crag.localeCompare(b.crag));

  const best = scored[0], max = best.count, top = scored.slice(0, TOP_N);
  headlineEl.innerHTML =
    `Top pick: <strong>${esc(best.crag)}</strong>` +
    (best.preserve ? ` <span class="preserve">· ${esc(best.preserve)}</span>` : "") +
    ` — ${best.count} route${best.count === 1 ? "" : "s"}, ${pct(best.fit)} fit`;

  for (const s of top) {
    const li = document.createElement("li");
    li.className = "row";
    li.dataset.i = s.i;                      // keyed by array index, not display name
    li.innerHTML =
      `<div class="top">
         <span class="name">${esc(s.crag)}</span>` +
      (s.preserve ? `<span class="preserve">${esc(s.preserve)}</span>` : "") +
      `<span class="nums">${s.count} <span class="fit">· ${pct(s.fit)} fit</span></span>
       </div>
       <div class="bar" style="width:${(100 * s.count / max).toFixed(1)}%"></div>`;
    boardEl.append(li);
  }
  countLineEl.textContent = `Showing ${top.length} of ${scored.length} matching crags.`;
}

function showEmpty(msg) { boardEl.innerHTML = `<li class="empty">${esc(msg)}</li>`; }
const pct = (x) => (100 * x).toFixed(0) + "%";
const esc = (s) => String(s).replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

render();                                    // initial paint: all bands checked
</script>
</body>
</html>
```

- [ ] **Step 2: Sanity-check the placeholder is present and unique**

Run: `grep -c "__PAYLOAD_JSON__" recommender_template.html`
Expected: `1`

- [ ] **Step 3: Commit**

```bash
git add recommender_template.html
git commit -m "feat: add recommender_template.html (crag-finder view + scoring JS)"
```

---

## Task 3: `build_recommender.py` + build smoke test (TDD)

**Files:**
- Test: `test_build_recommender.py` (create)
- Create: `build_recommender.py`

- [ ] **Step 1: Write the failing test**

Create `test_build_recommender.py`:

```python
"""Smoke test for build_recommender (design §5): fixture parquet -> built HTML.

Run: .venv/bin/python -m pytest test_build_recommender.py -q
"""
import json
import re

import pandas as pd

import build_recommender as br

RRG = "Red River Gorge"


def _fixture_parquet(path):
    pd.DataFrame({
        "climb_id": ["1", "2", "3"],
        "grade_yds": ["5.12a", "5.13b", "5.9"],
        "is_sport": [True, True, True],
        "state_province": ["Kentucky", "Kentucky", "Kentucky"],
        "path_tokens": [
            ["USA", "Kentucky", RRG, "PMRP", "The Madness Cave"],
            ["USA", "Kentucky", RRG, "PMRP", "The Madness Cave"],
            ["USA", "Kentucky", RRG, "Muir Valley", "Sunnyside"],
        ],
    }).to_parquet(path)


def test_build_writes_html_with_valid_injected_json_and_no_placeholder(tmp_path):
    parquet = tmp_path / "tiny.parquet"
    _fixture_parquet(parquet)
    out = tmp_path / "out.html"

    br.main(parquet_path=str(parquet), output=str(out))

    html = out.read_text(encoding="utf-8")
    assert br.PLACEHOLDER not in html                  # no leftover placeholder
    assert "The Madness Cave" in html                  # a known crag name made it in

    m = re.search(r"const PAYLOAD = (\{.*?\});", html)  # extract the injected literal
    assert m, "payload assignment not found in output HTML"
    payload = json.loads(m.group(1))                    # round-trips as JSON (< is valid)
    assert payload["area"] == RRG
    assert payload["bands"] == ["5.9", "5.12", "5.13"]  # rank-ordered
    assert "The Madness Cave" in {c["crag"] for c in payload["crags"]}


def test_build_payload_counts_and_preserve_from_fixture(tmp_path):
    parquet = tmp_path / "tiny.parquet"
    _fixture_parquet(parquet)
    payload = br.build_payload(parquet_path=str(parquet))
    madness = next(c for c in payload["crags"] if c["crag"] == "The Madness Cave")
    assert madness["counts"] == {"5.12": 1, "5.13": 1}
    assert madness["total"] == 2
    assert madness["preserve"] == "PMRP"               # AREA+1 token, even though it fell back to wall
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest test_build_recommender.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'build_recommender'`

- [ ] **Step 3: Write the implementation**

Create `build_recommender.py`:

```python
"""Build the self-contained RRG grade-based crag finder HTML (design §4.3).

Runs the tested climbing_density pipeline, builds the recommender payload, injects it
HTML-safely into recommender_template.html, and writes rrg_grade_recommender.html.

Run:  .venv/bin/python build_recommender.py
"""
import json
from pathlib import Path

import climbing_density as cd

# --- parameters (edit here to retarget) ------------------------------------- #
PARQUET_PATH = "data/usa-sport-climbs.parquet"
STATE = "Kentucky"
AREA = "Red River Gorge"
CRAG_OFFSET = 2                          # the single place the crag level is set (design §4.3)
TEMPLATE = "recommender_template.html"
OUTPUT = "rrg_grade_recommender.html"
PLACEHOLDER = "__PAYLOAD_JSON__"


def build_payload(parquet_path=PARQUET_PATH, state=STATE, area=AREA, crag_offset=CRAG_OFFSET):
    """Run the design §3 pipeline and return the recommender payload dict.

    Reuses load_climbs' missing-file error and filter_area's friendly "closest names" error.
    """
    df = cd.load_climbs(parquet_path)
    df = cd.filter_sport(df)
    df = cd.filter_area(df, state=state, area=area)
    df = cd.add_grade_columns(df)
    df = cd.derive_hierarchy(df, area=area, crag_offset=crag_offset)
    return cd.recommender_payload(df, area=area)


def render_html(payload, template_path=TEMPLATE, placeholder=PLACEHOLDER):
    """Inject the payload into the template, HTML-safely.

    Escapes every `<` to its unicode form so a stray `</script>` in a future crag name can't
    break out of the <script> block. `\\u003c` is valid in both JSON and the JS string it lands
    in, and `<` only ever appears inside JSON string values, so this never corrupts structure.
    Trusted CC0 data — this is cheap insurance, not a security boundary (design §4.3).
    """
    template = Path(template_path).read_text(encoding="utf-8")
    if placeholder not in template:
        raise ValueError(f"Template {template_path!r} has no {placeholder!r} placeholder.")
    injected = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return template.replace(placeholder, injected)


def main(parquet_path=PARQUET_PATH, output=OUTPUT):
    payload = build_payload(parquet_path)
    Path(output).write_text(render_html(payload), encoding="utf-8")
    n_crags = len(payload["crags"])
    n_routes = sum(c["total"] for c in payload["crags"])
    if n_crags == 0:
        print(f"WARNING: no crags found for {AREA!r} — wrote {output} showing the empty state.")
    else:
        print(f"Wrote {output}: {n_crags} crags, {n_routes} sport routes in {AREA}.")
    return output


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest test_build_recommender.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (39 passed — 31 original + 6 payload + 2 build)

- [ ] **Step 6: Commit**

```bash
git add build_recommender.py test_build_recommender.py
git commit -m "feat: add build_recommender.py + build smoke test"
```

---

## Task 4: `.gitignore` + `README.md`

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`

- [ ] **Step 1: Ignore the generated artifact (specific filename, NOT `*.html`)**

In `.gitignore`, add this block immediately after the `data/` block (after line 2):

```gitignore

# Generated crag-finder page — rebuild with `python build_recommender.py`.
# Specific filename, NOT *.html (which would also ignore the committed template).
rrg_grade_recommender.html
```

- [ ] **Step 2: Verify the template stays tracked and the artifact is ignored**

Run: `git check-ignore rrg_grade_recommender.html recommender_template.html; echo "exit:$?"`
Expected: prints `rrg_grade_recommender.html` only (the template is NOT listed), `exit:0`

- [ ] **Step 3: Add the README section**

In `README.md`, insert this section between the `## Quickstart` block and `## What you get` (i.e. after the "Open the notebook... `FOCUS_CRAG`)." paragraph, before `## What you get`):

```markdown
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

```

- [ ] **Step 4: Update the README layout tree**

In `README.md`, replace the existing layout lines for `climbing_density.py` and `test_climbing_density.py` and `grade_density.ipynb` and `data/` so the tree reads (replace those four `├──`/`└──` lines with this block):

```
├── climbing_density.py        # pure, tested transforms (grade parsing, hierarchy, matrices)
├── test_climbing_density.py   # pytest unit tests
├── build_recommender.py       # builds the crag-finder HTML from the parquet
├── recommender_template.html  # crag-finder view + scoring JS (payload placeholder)
├── test_build_recommender.py  # crag-finder build smoke test
├── grade_density.ipynb        # the exploratory notebook
├── rrg_grade_recommender.html # gitignored generated crag-finder page
└── data/                      # gitignored; holds usa-sport-climbs.parquet
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore README.md
git commit -m "docs: ignore generated crag finder + document it in README"
```

---

## Task 5: Generate the artifact and verify end-to-end

**Files:** none modified (produces the gitignored `rrg_grade_recommender.html`).

- [ ] **Step 1: Build against the real parquet**

Run: `.venv/bin/python build_recommender.py`
Expected: prints `Wrote rrg_grade_recommender.html: 135 crags, 1856 sport routes in Red River Gorge.`
(Counts may differ after a data refresh — the point is a non-zero crag/route count and no traceback.)

- [ ] **Step 2: Confirm the artifact is well-formed and the placeholder is gone**

Run:
```bash
test -f rrg_grade_recommender.html && \
grep -c "__PAYLOAD_JSON__" rrg_grade_recommender.html; \
grep -c "Motherlode" rrg_grade_recommender.html
```
Expected: the file exists; placeholder count `0`; a non-zero "Motherlode" count (a known RRG crag is embedded).

- [ ] **Step 3: Confirm it is gitignored (must NOT be staged)**

Run: `git status --porcelain rrg_grade_recommender.html; echo "exit:$?"`
Expected: no output (git ignores it), `exit:0`

- [ ] **Step 4: Eyeball it in a browser (manual, the §5 by-eye JS check)**

Run: `open rrg_grade_recommender.html`
Verify by hand:
- grade checkboxes render (5.x bands), all checked, a leaderboard of bars shows;
- unchecking high grades (5.13/5.14) re-ranks toward beginner crags;
- the "% fit" sort toggle changes the order vs "Route count";
- unchecking every grade shows the "Pick at least one grade" prompt.

- [ ] **Step 5: Final full-suite run**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (39 passed). No commit here — the only new file is the gitignored artifact.

---

## Self-review (done while writing — recorded for the executor)

- **Spec coverage:** §4.1 `recommender_payload` → Task 1; §4.2 template → Task 2; §4.3 builder + HTML-safe `<`→`<` injection → Task 3; §5 payload tests + build smoke test → Tasks 1 & 3; §6 error handling (reused `load_climbs`/`filter_area` errors via `build_payload`, empty-payload warning in `main`, JS empty states in template) → Tasks 2 & 3; §7 repo layout + **specific-filename** `.gitignore` + README → Task 4; §8 out-of-scope respected (no chart lib, no map, no server, no JS test harness).
- **Type/name consistency:** payload keys (`area`, `bands`, `crags`, `crag`, `preserve`, `total`, `counts`) are identical across `recommender_payload`, the JS in the template, and both test files. The placeholder token `__PAYLOAD_JSON__` and constant `PLACEHOLDER` match. `build_payload`/`render_html`/`main` signatures match how the tests call them (`main(parquet_path=, output=)`, `build_payload(parquet_path=)`).
- **No placeholders:** every code step contains complete, runnable content.
- **Grounding:** the payload function and the `bands`/`counts`/`total`/`preserve` values were prototyped against the real parquet — "Motherlode, The" returns `total: 63` and the expected per-band counts, and per-crag totals sum to the row count (no double-count).
```
