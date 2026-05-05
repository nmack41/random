# Views Page — Schema Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the codebase in sync with the current `2026-05-04-views-page-design.md` spec by adding the `schema_version` sentinel table and version-mismatch fail-loud check in `db.init_db()`. All other parts of the spec (views/view_group_access tables, helpers, snapshot capture, page, fake data) are already on `main`.

**Architecture:** Single-row `schema_version` table; `init_db()` inserts the current version on first run, raises `RuntimeError` on mismatch otherwise. `CURRENT_SCHEMA_VERSION = 2` (1 = pre-views, 2 = views added). No migration runner — just a version handle to fail loud on the next schema change.

**Tech Stack:** Python 3, SQLite, sqlite3 stdlib. No new dependencies.

---

## Why this is the only code delta

The spec we're implementing was edited today to add a "Schema versioning" subsection (lines 70-90 of the spec). Every other section of the spec describes work already shipped on `main`:

- `db.py:73-97` — `views` and `view_group_access` tables (commit `d1b91a`).
- `db.py:202-235` — `insert_views`, `insert_view_group_access`, `get_views_for_snapshot`.
- `snapshot.py:101-148` — `fetch_all_view_permissions` (and its call site at lines 168, 176-177).
- `app.py:8` — Views page registered.
- `pages/views.py` — present, with an extra "Differs from workbook" column from a later spec (`2026-05-05-views-permission-diff-highlight-design.md`); not in scope for this plan.
- `fake_data/fixtures.py` and `fake_data/seed.py` — `VIEWS` dict and `_build_views`/`_build_view_grants` already present.

The only spec code requirement not yet met is the `schema_version` sentinel.

## Scope check

This plan covers a single subsystem change (~10 lines in one file). No further decomposition needed.

## File structure

**Modified:**
- `db.py` — append a `CREATE TABLE IF NOT EXISTS schema_version` block to `SCHEMA`; add a module-level `CURRENT_SCHEMA_VERSION` constant; extend `init_db()` to perform the version check.

No new files. No tests (per spec: "Automated tests... are out of scope. Verification is manual against the seeded fake-data DB plus a smoke test against a real Tableau instance.").

---

### Task 1: Add `schema_version` table to `SCHEMA`

**Files:**
- Modify: `db.py:13-98` (the `SCHEMA` constant)

- [ ] **Step 1: Append the new table block to `SCHEMA`**

In `db.py`, the `SCHEMA` triple-quoted string ends at line 98 with `CREATE INDEX IF NOT EXISTS idx_vga_snapshot ON view_group_access(snapshot_id);`. Append the new table after that, before the closing `"""`:

```sql

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
```

The leading blank line keeps the same one-blank-line-between-blocks style the rest of `SCHEMA` uses. `IF NOT EXISTS` keeps `init_db()` idempotent.

### Task 2: Add `CURRENT_SCHEMA_VERSION` constant

**Files:**
- Modify: `db.py:7-12` (the existing module-level constants block)

- [ ] **Step 1: Add the version constant alongside the other module constants**

After `CHANGE_REMOVED = "removed"` on line 11, add:

```python
CURRENT_SCHEMA_VERSION = 2  # 1 = pre-views; 2 = views + view_group_access added
```

The trailing comment captures the version history inline so future bumps don't need to grep the spec to know what version `1` represented.

### Task 3: Extend `init_db()` with the version check

**Files:**
- Modify: `db.py:109-113` (the `init_db` function)

- [ ] **Step 1: Replace `init_db()` with the version-checking variant**

Current code:

```python
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.close()
```

New code:

```python
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (CURRENT_SCHEMA_VERSION,),
            )
            conn.commit()
        elif row["version"] != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"DB schema v{row['version']}, code expects v{CURRENT_SCHEMA_VERSION}. "
                f"Re-seed or migrate."
            )
    finally:
        conn.close()
```

Notes:

- `conn.row_factory = sqlite3.Row` is set in `get_connection()` (line 103), so `row["version"]` reads the named column rather than `row[0]`. Matches the rest of the module's style.
- `conn.commit()` is needed because the existing `init_db()` only ran `executescript` (which auto-commits each statement). Adding an `INSERT` requires an explicit commit.
- `try/finally` ensures the connection closes even if `RuntimeError` is raised — `init_db()` runs at *import time* (line 249), so a leaked connection there would leak forever in any importer (Streamlit pages, snapshot.py, the seeding script).
- Existing seeded DBs without a `schema_version` row will pass the `IS NULL` branch and get tagged as v2 on next import. That's correct: the additive `CREATE TABLE IF NOT EXISTS` blocks make them v2-compatible at the moment they're upgraded.

### Task 4: Manual verification

- [ ] **Step 1: Re-seed and confirm no errors**

```bash
python -m fake_data.seed
```

Expected: prints the existing seed summary (`Seeded fake_data/groups.db ...`) without any `RuntimeError`. The new `schema_version` table exists and contains a single row with `version=2`.

- [ ] **Step 2: Inspect the new table in the seeded DB**

```bash
sqlite3 fake_data/groups.db "SELECT version FROM schema_version"
```

Expected output: `2`

- [ ] **Step 3: Confirm the existing pages still load**

```bash
FAKE_DATA=1 streamlit run app.py
```

Click through Current State → Workbooks → Views → Changes. Each should render without errors. (The Workbooks-page regression check in spec verification step 10 is process-level guidance for a real-Tableau-instance smoke test; for this plan, "page renders against fake data" is sufficient.)

- [ ] **Step 4: Force a mismatch to verify the fail-loud path**

```bash
sqlite3 fake_data/groups.db "UPDATE schema_version SET version = 1"
python -c "import db"
```

Expected: `RuntimeError: DB schema v1, code expects v2. Re-seed or migrate.`

Then restore:

```bash
python -m fake_data.seed
```

### Task 5: Commit

- [ ] **Step 1: Stage and commit the change**

```bash
git add db.py
git commit -m "Add schema_version sentinel for fail-loud version mismatches"
```

Recommended commit body — keep it short, the spec captures the rationale:

```
Single-row schema_version table + check in db.init_db() raising
RuntimeError when the seeded DB version doesn't match
CURRENT_SCHEMA_VERSION. No migration runner; this is a version
handle so the next ALTER TABLE migration doesn't have to
retrofit version detection.

Implements the Schema versioning section of
docs/superpowers/specs/2026-05-04-views-page-design.md
```

- [ ] **Step 2: Commit the spec itself if it isn't already**

The spec file is currently `M` in `git status`. If the schema-versioning section was added in this same working session (it appears to have been — see the unstaged diff at `docs/superpowers/specs/2026-05-04-views-page-design.md`), include it in this commit or in a preceding "spec" commit. Confirm with the user before deciding which.

## Verification (consolidated)

- `python -m fake_data.seed` re-seeds without `RuntimeError`.
- `sqlite3 fake_data/groups.db "SELECT version FROM schema_version"` returns `2`.
- `FAKE_DATA=1 streamlit run app.py` loads all four pages.
- Forcing `version = 1` in the DB and re-importing `db` raises the expected `RuntimeError`.

## Self-review

**Spec coverage.** The only spec section requiring code is "Schema versioning" (spec lines 70-90). Tasks 1-3 implement it; Task 4 verifies; Task 5 commits. Every other spec section maps to existing code (see "Why this is the only code delta" above).

**Placeholder scan.** No TBDs, no "implement later", no "similar to Task N", no "add error handling" — every step contains the actual code or command.

**Type consistency.** `CURRENT_SCHEMA_VERSION` (Task 2) is referenced in the same form in Task 3's `init_db` body. The `schema_version.version` column name (Task 1's CREATE TABLE) is used in Task 3's `INSERT` and `SELECT`. The `RuntimeError` signature is consistent with how `db.py` already raises (it doesn't — this is the first; one-shot consistency is fine).
