# Workbook Permissions — Design (MVP v1)

## Goal

Extend the Tableau Groups Monitoring app to capture, store, and browse which **groups** have access to each **workbook** on Tableau Server. The MVP answers one question: "Which groups can access this workbook?" Compliance/audit value comes from being able to query that data over time; drift detection, view-level detail, and the inverse "which workbooks can group X access?" view are deferred.

## Non-goals (v1)

- **Views.** View-level permissions are deferred to v2. v1 captures workbooks only.
- **Inverse "group → workbooks" view.** v1's Workbooks page is workbook-centric only; the dataframe filter approximates the inverse but won't be exact (group names appear comma-separated, so substring filters match adjacent names). Promoted to a v2 item.
- **Drift detection.** No `content_access_changes` table, no precomputed diff. If needed, drift can be computed on demand later via `EXCEPT` between two snapshots.
- Direct user-to-content grants (groups only).
- Capability-level detail (e.g., distinguishing `Filter` from `Write`); we collapse to a single boolean per group per workbook: "has access" or not.
- Special UI handling for project-locked workbooks (see "Known limitations").
- Automated tests or a test framework. Verification is a manual checklist.

## What changed from the original spec, and why

This document was critiqued by two frontier models (gemini-3-pro-preview and gpt-5.4) through an MVP lens. Both independently flagged the same over-engineering. Cuts taken:

| Cut | Rationale |
| --- | --- |
| **Views layer dropped** | `populate_permissions` per view is N+1 against the Tableau REST API. Doubles extraction surface, blows up runtime, forces nested UI Streamlit can't render natively. Workbook-level access already answers the auditor's core question. |
| **`content_access_changes` table dropped** | Author already said no drift UI in this iteration. Building a parallel diff pipeline (table + indexes + `compute_content_access_diff` + write-time invocation) for an unimplemented consumer is YAGNI. Compute on demand later. |
| **Placeholder rows replaced with normalized 2-table model** | Encoding "no group has access" as a synthetic row with `group_id IS NULL` infects every read query, every diff, the CSV export, and the mental model. Two tables (workbooks + workbook_group_access) with a `LEFT JOIN` give zero-access workbooks naturally. |
| **"Differs from parent" UI dropped** | Falls out automatically with views. |
| **Denormalized `parent_workbook_name` dropped** | Falls out automatically with views. |
| **Index count reduced from 3 → 1 per table** | Premature optimization at MVP scale. |
| **CSV export deferred** | Streamlit's `st.dataframe` provides a built-in CSV download in the toolbar. |

## Architecture

The existing dual-process split is preserved: `snapshot.py` writes; the Streamlit app reads.

```
Tableau Server ─(REST API)─> snapshot.py ──> SQLite DB <── Streamlit app
                              (writes:                      (reads:
                               groups, users,                Current State,
                               workbooks,                    Changes,
                               workbook-group access)        Workbooks)
```

A single `python3 snapshot.py` run captures groups + group memberships + workbooks + workbook-group access in one run, and computes the membership diff (unchanged from today).

## Data flow

`snapshot.py` is extended to add steps 3–4 below; existing steps 1, 2, and 5 are unchanged.

1. Sign in to Tableau Server with the Personal Access Token.
2. Fetch all groups; for each, fetch its members. Insert into `group_members` (existing).
3. **New:** fetch all workbooks. For each workbook:
   - Call `server.workbooks.populate_permissions(wb)`.
   - Fold capability rules into a deduplicated set of group IDs (see "Permission folding").
   - Insert one row per workbook into `workbooks`.
   - Insert one row per `(workbook, group_with_access)` into `workbook_group_access`.
4. **New:** mark the snapshot `success`, commit. (See "Transaction semantics" — this is the same point in the lifecycle as the existing membership snapshot.)
5. Compute the existing membership diff against the previous successful snapshot (unchanged).

On any exception in steps 1–4, mark `failed` and exit with a non-zero status.

## Schema

Existing tables (`snapshots`, `group_members`, `membership_changes`) are unchanged. Two new tables:

```sql
CREATE TABLE IF NOT EXISTS workbooks (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    workbook_id TEXT NOT NULL,            -- Tableau LUID
    workbook_name TEXT NOT NULL,
    project_name TEXT,                    -- NULL when the API returns no project
    UNIQUE (snapshot_id, workbook_id)
);

CREATE INDEX IF NOT EXISTS idx_workbooks_snapshot
    ON workbooks(snapshot_id);

CREATE TABLE IF NOT EXISTS workbook_group_access (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    workbook_id TEXT NOT NULL,            -- Tableau LUID, matches workbooks.workbook_id
    group_id TEXT NOT NULL,
    group_name TEXT,                      -- NULL when the group_id is unresolved (stale reference)
    UNIQUE (snapshot_id, workbook_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_wga_snapshot
    ON workbook_group_access(snapshot_id);
```

### Why two tables instead of one mega-table

The original spec proposed a single denormalized `content_group_access` table with placeholder rows (`group_id IS NULL`) for zero-access content. That pattern signals the schema is fighting the domain: when you need fake rows so entities don't disappear, you actually have two entities (item + association) and the model wants to be normalized.

The two-table model:
- `workbooks` is the row source — every workbook in the snapshot appears here exactly once, regardless of permissions.
- `workbook_group_access` is the association — only real grants. No null/placeholder rows.
- The Workbooks UI does a `LEFT JOIN` from `workbooks` to `workbook_group_access`; zero-access workbooks naturally appear with no group rows. No `WHERE group_id IS NOT NULL` litter.
- Future drift queries diff the association table directly with a stable key `(workbook_id, group_id)`. No placeholder exclusion logic needed.

The `UNIQUE` constraints prevent duplicate rows from inheritance-merged permission rules.

## Permission folding logic

Tableau returns workbook permissions as a list of `PermissionsRule` objects. Each rule has a `grantee` (group or user) and a `capabilities` dict mapping capability name → `'Allow'` | `'Deny'`.

A group "has access" to a workbook if its `Read` capability is exactly `Allow`. (See "Rationale" below for why this strict definition replaces an earlier loose draft.)

```python
def groups_with_access(rules, group_id_to_name) -> set[tuple[str, str | None]]:
    """Return {(group_id, group_name), ...} for groups with any allowed access.

    Returns a set to dedupe rules that resolve to the same group via
    inheritance/merging. Falls back to "" for group names not in the map
    (stale references won't crash the snapshot).
    """
    out: set[tuple[str, str | None]] = set()
    for rule in rules:
        if rule.grantee.tag_name != "group":
            continue                       # skip direct user grants entirely
        if rule.capabilities.get("Read") != "Allow":
            continue                       # auditor question is literally "can this group read it?"
        gid = rule.grantee.id
        out.add((gid, group_id_to_name.get(gid)))
    return out
```

Rationale:
- `Read` is the foundational capability; without it, no other capability is usable. The auditor question is literally "can this group read it?", so we test exactly that — `Read == "Allow"`. One capability, one direction.
- An earlier draft used "any `Allow` capability and `Read` not `Deny`," which over-counts: a rule with `Read=Unspecified, Filter=Allow` would have qualified even though Tableau can't grant Filter without Read. If the strict definition under-counts in practice, we'll find out from real data and revisit in v2.
- The `set` return type and `.get(gid)` fallback (yielding `None` for unresolved IDs) are deliberate. Tableau can return multiple rules resolving to the same group, and `populate_users` data can lag behind permission rules (stale group references). Storing `NULL` for an unresolved name surfaces the gap rather than papering over it with `""`.

The snapshot script builds an `{group_id: group_name}` map during step 2 (groups fetch) and reuses it for step 3, since `PermissionsRule.grantee` carries an ID but not a name.

### Known limitations of the fold

These are documented limitations, not bugs to fix in v1:

- **Project-locked permissions inheritance.** If a Tableau project is "Locked to the Project," workbook-level rules returned by the API may be displayed but ineffective at the server. v1 reports rule presence, not effective access. The Workbooks page header should say so.
- **The "All Users" group.** A single `Allow / Read` to "All Users" makes a workbook effectively public. v1 stores it as just another group; the UI should consider rendering it with emphasis so auditors don't misread "1 group has access" as "secure." (Polish, not blocking.)
- **Direct user grants are skipped.** Per non-goal. A workbook with only user-level grants will appear as "no group access" — UI copy should make this clear.

## Transaction semantics

The current `snapshot.py` (lines 57–65) marks the snapshot `success` and commits *before* computing the membership diff. The original spec claimed the new design would be "single transaction, no partial data exposed," which contradicts current code.

v1 keeps the existing pattern: workbook capture inserts happen *before* `complete_snapshot(SUCCESS)`, then commit, then the membership diff runs and commits separately (unchanged). If the workbook capture fails mid-flight, the snapshot is marked `failed` and rolled back. If the membership diff fails after a successful workbook capture, the snapshot stays `success` (current behavior) — drift computation is best-effort.

This is a deliberate reliability tradeoff: capture is mission-critical, diff is not. v1 preserves it; a future change can revisit it if drift becomes load-bearing.

## UI: Workbooks page

A new file `pages/workbooks.py` is registered in `app.py` as the third nav entry, between Current State and Changes. Existing pages are unchanged.

### Empty state

When no successful snapshot has any rows in `workbooks`:

> No workbook data yet. Run `python3 snapshot.py` to capture workbooks and their group permissions.

### Populated state

- **Snapshot selector** — same dropdown component as Current State, defaults to the latest successful snapshot.
- **Search bar** — case-insensitive substring filter applied to workbook name, project name, and group name.
- **Workbooks table** — one row per workbook. Columns:
  - Project
  - Workbook name
  - Groups with access (comma-separated; `—` if zero)
- **Header note** — "Permissions reflect group rule presence on the workbook. Project-level locks and direct user grants are not represented."

CSV export is not implemented in v1 — Streamlit's `st.dataframe` provides a built-in CSV download in the toolbar.

### Read query

A single fetch per snapshot, then the page groups results in Python:

```python
rows = conn.execute("""
    SELECT w.workbook_id, w.workbook_name, w.project_name,
           a.group_id, a.group_name
    FROM workbooks w
    LEFT JOIN workbook_group_access a
      ON a.snapshot_id = w.snapshot_id
     AND a.workbook_id = w.workbook_id
    WHERE w.snapshot_id = ?
    ORDER BY w.project_name, w.workbook_name, a.group_name
""", (snapshot_id,)).fetchall()
```

The page collapses results into `{workbook_id: {project, name, groups: [...]}}` and renders a flat dataframe.

## Files affected

| File | Change |
| --- | --- |
| `db.py` | Add `workbooks` and `workbook_group_access` to `SCHEMA`. Add helper functions `insert_workbooks`, `insert_workbook_group_access`, `get_workbooks_for_snapshot`. |
| `snapshot.py` | Add `fetch_all_workbook_permissions` and the `groups_with_access` fold. Extend `take_snapshot` to call it inside the existing `with server.auth.sign_in(auth):` block, before `complete_snapshot(SUCCESS)`. |
| `app.py` | Register a new `st.Page` for `pages/workbooks.py`. |
| `pages/workbooks.py` | New file. |
| `README.md` | Document the new page and the additional snapshot output. |

`diff.py` is unchanged in v1.

## Pre-implementation: API cost probe

Before writing schema, helpers, or the page, run a one-off REPL probe against the target Tableau site to size the per-workbook `populate_permissions` cost:

1. Sign in (reuse the snapshot.py auth block).
2. `wbs = list(TSC.Pager(server.workbooks.get))` — record the count.
3. Time `server.workbooks.populate_permissions(wb)` on 5 workbooks; multiply by the count.

If the projected end-to-end snapshot exceeds ~10 minutes, stop and revisit the design (e.g., narrow scope to a single project, or batch via the GraphQL Metadata API) before writing any code. The N+1 against a server we don't control is the only part of this design that can blow up irreversibly post-merge.

## Verification checklist

No test framework exists in the repo and adding one is out of scope. Verification is manual.

1. **Schema migration is safe on existing DBs.** Run `python3 -c "import db"` against a `data/groups.db` that has prior snapshots. The new `CREATE TABLE IF NOT EXISTS` statements should add the new tables without touching existing ones. Confirm with `sqlite3 data/groups.db ".schema"`.
2. **Snapshot capture against a real Tableau site.** Run `python3 snapshot.py`. Console output should gain one line: `Captured N workbooks, M workbook-group grants`.
3. **Zero-access workbooks are represented.** After the first new snapshot, `SELECT COUNT(*) FROM workbooks WHERE snapshot_id = (SELECT MAX(id) FROM snapshots)` should match the workbook count on the site, including any with no group grants. The corresponding `LEFT JOIN` query in the UI should render those workbooks with `—` in the groups column.
4. **No duplicate access rows.** `SELECT snapshot_id, workbook_id, group_id, COUNT(*) FROM workbook_group_access GROUP BY 1,2,3 HAVING COUNT(*) > 1` should return zero rows. (The `UNIQUE` constraint enforces this; the query is belt-and-suspenders.)
5. **Streamlit empty state.** Wipe `data/groups.db`, run `streamlit run app.py`, open the Workbooks page. The empty-state message should appear, no stack trace.
6. **Streamlit populated state.** With a real snapshot loaded: workbook list renders, search filters work, at least one zero-access workbook (if any exist on the site) shows `—` in the groups column.
7. **Performance sanity.** Time the snapshot run against the projection from the pre-implementation probe. A meaningful gap (>2×) means the probe missed something — investigate before relying on the data.
8. **No silent stale group references.** `SELECT COUNT(*) FROM workbook_group_access WHERE group_name IS NULL` — any non-zero count is a stale group reference (group_id present in permission rules but absent from `populate_users`). Investigate the source before shipping; the fold tolerates it but auditors shouldn't see anonymous group rows without explanation.

## Rollout

Single PR. No feature flag. The new page and the two new tables are additive; existing pages and tables continue to function with no migration step beyond the `CREATE TABLE IF NOT EXISTS` on next import.

## Deferred to v2 (explicitly out of scope here)

These are real future work items, not lost ideas — captured so the v1 cut decisions are reversible when there's pull:

- **View-level permissions.** Add `views` and `view_group_access` tables mirroring the workbook ones. Same fold logic. UI gets nested rendering or a separate Views page.
- **Inverse "group → workbooks" view.** A second tab (or page) keyed on group, listing every workbook that group has access to in the selected snapshot. One extra query against the same two tables; deferred only because the workbook-centric page already covers the primary auditor question.
- **Drift detection.** Add `content_access_changes` (or compute on demand). Surface in the Changes page or as a new Drift page. Use `(workbook_id, group_id)` as the diff key for stability across renames.
- **"All Users" group highlighting.** UI emphasis so auditors don't misread broad access.
- **Project-locked permissions awareness.** Capture the project's `controlledPermissions` state and surface it in the UI.
- **CSV export.** If `st.dataframe`'s built-in download proves insufficient.
