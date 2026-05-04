# Change log — 2026-05-04

Implementation of `docs/superpowers/specs/2026-05-01-workbook-view-permissions-design.md` (Workbook Permissions MVP v1).

## Summary

Extended the Tableau Groups Monitoring app to capture, store, and browse which **groups** have access to each **workbook** on Tableau Server. Adds a third Streamlit page ("Workbooks") between "Current State" and "Changes". The MVP answers one question: *"Which groups can access this workbook?"*

Per the spec's MVP cuts, this implementation explicitly does **not** include views, drift detection, the inverse "group → workbooks" view, capability-level detail, special handling for project-locked workbooks, or automated tests.

## Files changed

| File | Type | Notes |
| --- | --- | --- |
| `db.py` | modified | Schema + helpers |
| `snapshot.py` | modified | Workbook-permissions capture |
| `app.py` | modified | Page registration |
| `pages/workbooks.py` | new | Workbooks page |
| `README.md` | modified | Page docs, output example, schema table, known limitations |
| `docs/change_log_2026_05_04.md` | new | This file |

`diff.py` is unchanged in v1.

## Detailed changes

### `db.py`

Added two tables to the `SCHEMA` constant:

- **`workbooks`** — one row per `(snapshot_id, workbook_id)` with workbook name and project name. `UNIQUE (snapshot_id, workbook_id)`. Indexed on `snapshot_id`.
- **`workbook_group_access`** — one row per `(snapshot_id, workbook_id, group_id)` association. `group_name` is nullable to surface stale references rather than paper over them with empty strings. `UNIQUE (snapshot_id, workbook_id, group_id)` enforces the "no duplicates from inheritance-merged rules" invariant. Indexed on `snapshot_id`.

Added three helper functions:

- `insert_workbooks(conn, snapshot_id, workbooks)`
- `insert_workbook_group_access(conn, snapshot_id, grants)`
- `get_workbooks_for_snapshot(conn, snapshot_id)` — returns the `LEFT JOIN` of `workbooks` to `workbook_group_access` so zero-access workbooks naturally appear with `group_id IS NULL` rows on the right side.

Schema migration is safe on existing DBs because the new tables use `CREATE TABLE IF NOT EXISTS` and reference `snapshots(id)` only via foreign key (no changes to existing tables).

### `snapshot.py`

Added two functions and modified two:

- **New: `groups_with_access(rules, group_id_to_name)`** — folds a workbook's `PermissionsRule` list into `{(group_id, group_name), ...}` for groups with `Read=Allow`. Skips direct user grants. Returns a `set` to dedupe inheritance-merged rules. Falls back to `None` for unresolved group IDs (stale references) so the gap surfaces in the UI rather than being papered over.
- **New: `fetch_all_workbook_permissions(server, group_id_to_name)`** — iterates all workbooks via `TSC.Pager(server.workbooks.get)`, calls `populate_permissions` per workbook, applies the fold. Returns `(workbooks, grants)` row lists ready for bulk insert.
- **Modified: `fetch_all_group_members`** — now also returns the `{group_id: group_name}` map built during step 2, so the workbook fold can resolve grantee IDs without a second groups fetch. Return type changed from `list[dict]` to `tuple[list[dict], dict[str, str]]`.
- **Modified: `take_snapshot`** — calls `fetch_all_workbook_permissions` inside the existing `with server.auth.sign_in(auth):` block, then bulk-inserts both `workbooks` and `workbook_group_access` *before* `complete_snapshot(SUCCESS)`. Adds one console line: `Captured N workbooks, M workbook-group grants`.

Transaction semantics preserved per the spec: workbook capture happens before the success commit, so a mid-flight failure rolls back via `STATUS_FAILED`. The membership diff continues to run *after* the success commit (best-effort, unchanged).

### Permission folding details

Per the spec, the fold uses the **strict** definition of "has access": `Read=Allow` is required. Earlier draft language ("any `Allow` capability and `Read` not `Deny`") was rejected because it over-counts — Tableau cannot grant any other capability without `Read`, so a rule with `Read=Unspecified, Filter=Allow` would have qualified despite being effectively non-functional.

### `pages/workbooks.py` (new)

A new Streamlit page with the same shape as `current_state.py`:

- **Empty state** when no snapshots exist OR the selected snapshot has no workbook rows (e.g., a pre-feature snapshot).
- **Snapshot selector** — same dropdown component as Current State, defaults to the latest successful snapshot.
- **Header note** — calls out that project-level locks and direct user grants are not represented.
- **Metrics** — total workbook count and zero-access workbook count.
- **Search bar** — case-insensitive substring filter applied to project, workbook name, and the comma-separated groups column.
- **Workbooks table** — flat dataframe with three columns: Project / Workbook / Groups with access. Comma-separated group list, `—` for zero-access. Stale group references render as `<unresolved:gid>` so auditors can see the gap (per spec verification step #8).

CSV export is not a manual button — Streamlit's `st.dataframe` toolbar provides a built-in CSV download.

### `app.py`

Registered `pages/workbooks.py` as the **third** nav entry, between Current State and Changes (per spec).

### `README.md`

- Tagline updated to mention workbook permissions.
- Snapshot output example updated to include the new "Found N workbooks" and "Captured N workbooks, M workbook-group grants" lines.
- New "Workbooks page" entry under "Using the dashboard."
- Project structure updated.
- Data flow paragraph (item 3) updated to describe the workbook capture step.
- Database tables table extended with `workbooks` and `workbook_group_access`, with a one-line explanation of the LEFT JOIN pattern.
- New "Known limitations of workbook permissions" section above "Troubleshooting" — covers project-locked workbooks, the "All Users" group, direct user grants, and the v2 deferred items (with a pointer to the spec).

## Verification status

Per the spec, no automated tests exist; verification is a manual checklist. The following items were verified locally **before the live Tableau Server run**:

| # | Spec verification step | Status | Method |
| --- | --- | --- | --- |
| 1 | Schema migration is safe on existing DBs | ✅ verified | Applied old-shape schema in-memory, inserted a row, then ran the new `CREATE TABLE IF NOT EXISTS` script — old data preserved, both new tables added. |
| 2 | Snapshot capture against a real Tableau site | ⏳ pending — needs live server | Code path is wired; user must run `python3 snapshot.py` to verify the new console line appears. |
| 3 | Zero-access workbooks are represented | ✅ verified | Smoke test inserted a workbook with no grant rows; `LEFT JOIN` returned the workbook with `group_id=NULL`; collapse logic rendered `—`. |
| 4 | No duplicate access rows | ✅ verified | Attempted duplicate insert raised `sqlite3.IntegrityError` from `UNIQUE (snapshot_id, workbook_id, group_id)`. |
| 5 | Streamlit empty state | ⏳ pending — needs streamlit | Empty-state branch added explicitly for both "no snapshots" and "snapshot has no workbooks." User must run `streamlit run app.py` against an empty DB to confirm. |
| 6 | Streamlit populated state | ⏳ pending — needs streamlit | Smoke-tested the underlying read query and collapse logic; UI render needs manual confirmation. |
| 7 | Performance sanity | ⏳ pending — needs live server | Spec recommends an API cost probe (see below). |
| 8 | No silent stale group references | ✅ partially | Stale references render as `<unresolved:gid>` in the UI; `SELECT COUNT(*) FROM workbook_group_access WHERE group_name IS NULL` is the audit query. |

Additional unit-level verification:
- **`groups_with_access` fold** — tested with six mock `PermissionsRule` cases: `Read=Allow` (counted), multiple rules to same group (deduped), `Read=Deny` (excluded), `Read` missing with `Filter=Allow` (excluded — confirms the strict tightening), direct user grant (excluded), stale reference (counted with `name=None`).

## Pre-implementation step the user still needs to run

The spec's pre-implementation API cost probe was **not** run, because no live Tableau Server access was available in this environment. Per the spec, run the probe **before relying on production output**:

> Sign in (reuse the snapshot.py auth block).
> `wbs = list(TSC.Pager(server.workbooks.get))` — record the count.
> Time `server.workbooks.populate_permissions(wb)` on 5 workbooks; multiply by the count.
>
> If the projected end-to-end snapshot exceeds ~10 minutes, stop and revisit the design (e.g., narrow scope to a single project, or batch via the GraphQL Metadata API) before writing any code.

The implementation is shippable on schema/UI grounds; the probe protects against an irreversible scaling surprise on the N+1 `populate_permissions` calls against an external server.

## Rollout

Single PR, no feature flag, no DB migration script — `CREATE TABLE IF NOT EXISTS` runs on next import. Existing pages and tables are untouched.

## Post-review fixes (after PAL `codereview` via gpt-5.5-pro)

A code review surfaced four real defects, all fixed in the same change:

### `snapshot.py` — transaction boundaries (HIGH)

**Bug:** `db.create_snapshot()` opens an implicit SQLite write transaction. If `insert_workbooks` or `insert_workbook_group_access` threw mid-flight, the except branch's `complete_snapshot(FAILED)` + `commit()` would persist **both** the FAILED status **and** any partial rows already inserted in the uncommitted transaction.

**Bug:** `compute_diff()` ran inside the same broad `try` as capture, so a diff failure after the success commit would mark an already-committed-success snapshot as FAILED — directly contradicting the spec's "diff is best-effort, snapshot stays `success`" guarantee.

**Fix:**
- Commit the in_progress snapshot row immediately so it survives a rollback.
- Capture work runs in its own try/except: on failure, `conn.rollback()` discards partial rows *before* marking FAILED.
- Diff runs in a separate try/except: on failure, log and continue — the success snapshot is durable.

Verified with two in-memory tests:
1. Inject a mid-flight capture exception → snapshot persists with `status='failed'` and zero rows in `group_members`/`workbooks`/`workbook_group_access`.
2. Inject a post-success diff exception → snapshot stays `status='success'` with capture data intact.

### `pages/workbooks.py` — search regex semantics (MEDIUM)

**Bug:** `df["..."].str.contains(needle, case=False, na=False)` defaults to `regex=True`. A user typing `[` would crash with a regex compile error; typing `.` would match every workbook.

**Fix:** added `regex=False` to all three `str.contains` calls. Removed the redundant `.str.lower()` (case-insensitivity now handled by `case=False`). Also applied the same fix to `pages/current_state.py`, which had the identical pre-existing bug.

### `pages/workbooks.py` — connection cleanup (LOW)

**Improvement:** wrapped the SQLite connection in `contextlib.closing` so it's released even on exception paths.

### Spec doc — stale "loose definition" prose (MEDIUM)

**Issue:** the spec body at the top of "Permission folding logic" still contained the rejected loose definition: *"any capability is `Allow` and `Read` is not `Deny`"*. The rationale section directly below correctly tightens this to strict `Read=="Allow"`, which is what was implemented. Future readers might see the contradiction and "fix" the code in the wrong direction.

**Fix:** replaced the body sentence with the strict definition and updated the function-signature example to reflect `set[tuple[str, str | None]]` (the actual return type, accounting for stale group references).

## Deferred to v2 (per spec)

- View-level permissions (`views` + `view_group_access`).
- Inverse "group → workbooks" page.
- Drift detection (`content_access_changes` or compute on demand).
- "All Users" group highlighting.
- Project-locked permissions awareness (capture project's `controlledPermissions` state).
- CSV export beyond the dataframe toolbar.
