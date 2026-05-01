# Workbook & View Permissions — Design

## Goal

Extend the Tableau Groups Monitoring app to capture, store, and browse which **groups** have access to each **workbook** and **view** on Tableau Server. Combine three use cases in one feature: compliance/audit answers, drift detection between snapshots, and self-service browsing.

## Non-goals

- Direct user-to-content grants (groups only).
- Capability-level detail (e.g., distinguishing `Filter` from `Write`); we collapse to a single boolean per group per content item: "has access" or not.
- Special UI handling for project-locked workbooks.
- A dedicated UI for workbook/view permission drift in this iteration. Drift data is captured in the database for ad-hoc query but not surfaced in the Streamlit app yet.
- Automated tests or a test framework. Verification is a manual checklist.

## Architecture

The existing dual-process split is preserved: `snapshot.py` writes; the Streamlit app reads.

```
Tableau Server ─(REST API)─> snapshot.py ──> SQLite DB <── Streamlit app
                              (writes:                      (reads:
                               groups, users,                Current State,
                               workbooks, views,             Changes,
                               content access)               Workbooks)
```

A single `python3 snapshot.py` run captures groups + group memberships + workbooks + views + content-group access in one transaction, computes both diffs (membership + content access), and commits.

## Data flow

`snapshot.py` is extended to add steps 3–5 below; existing steps 1, 2, and 6 are unchanged.

1. Sign in to Tableau Server with the Personal Access Token.
2. Fetch all groups; for each, fetch its members. Insert into `group_members` (existing).
3. **New:** fetch all workbooks. For each workbook:
   - Call `server.workbooks.populate_permissions(wb)`.
   - Fold capability rules into a single boolean per group (see "Permission folding").
   - Insert one row per `(workbook, group_with_access)` into `content_group_access`.
   - If no group has access, insert one placeholder row with `group_id IS NULL`.
4. **New:** fetch all views. For each view:
   - Call `server.views.populate_permissions(view)`.
   - Same folding and insertion as workbooks, with `parent_workbook_id` and `parent_workbook_name` populated.
5. **New:** compute content access diff against the previous successful snapshot via `EXCEPT`, writing rows to `content_access_changes`. Placeholder rows are excluded from the diff.
6. Mark the snapshot `success`, commit. On any exception in steps 1–5, mark `failed` and exit with a non-zero status; no partial data is exposed to the read side.

## Schema

Existing tables (`snapshots`, `group_members`, `membership_changes`) are unchanged. Two new tables:

```sql
CREATE TABLE IF NOT EXISTS content_group_access (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    content_type TEXT NOT NULL,            -- 'workbook' | 'view'
    content_id TEXT NOT NULL,              -- Tableau LUID
    content_name TEXT NOT NULL,
    project_name TEXT NOT NULL DEFAULT '',
    parent_workbook_id TEXT,               -- NULL for workbooks; set for views
    parent_workbook_name TEXT,             -- denormalized for fast UI rendering
    group_name TEXT,                       -- NULL = placeholder, no group has access
    group_id TEXT                          -- NULL = placeholder
);

CREATE INDEX IF NOT EXISTS idx_cga_snapshot_content
    ON content_group_access(snapshot_id, content_type, content_id);
CREATE INDEX IF NOT EXISTS idx_cga_snapshot_group
    ON content_group_access(snapshot_id, group_name);
CREATE INDEX IF NOT EXISTS idx_cga_parent
    ON content_group_access(snapshot_id, parent_workbook_id);

CREATE TABLE IF NOT EXISTS content_access_changes (
    id INTEGER PRIMARY KEY,
    detected_at DATETIME NOT NULL,
    content_type TEXT NOT NULL,
    content_id TEXT NOT NULL,
    content_name TEXT NOT NULL,
    parent_workbook_id TEXT,
    group_name TEXT NOT NULL,              -- placeholders excluded from diffs
    group_id TEXT NOT NULL,
    change_type TEXT NOT NULL,             -- 'added' | 'removed'
    previous_snapshot_id INTEGER REFERENCES snapshots(id),
    current_snapshot_id INTEGER NOT NULL REFERENCES snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_cac_current ON content_access_changes(current_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_cac_content ON content_access_changes(content_type, content_id);
```

### Why a single denormalized `content_group_access` table

This follows Approach B (one mega-table) rather than two normalized tables. The trade-off:

- **Pro:** matches the existing denormalized-per-snapshot pattern of `group_members`. Simple ETL (single append-only insert), straightforward `EXCEPT` diffs, friendly for ad-hoc SQL.
- **Con:** workbooks/views with no group grants would otherwise vanish from the table. Mitigated by emitting a placeholder row with `group_id IS NULL` for any content item with zero group grants.

Every read query that aggregates group access must filter `WHERE group_id IS NOT NULL`, or use the placeholder deliberately to detect "no access at all."

### Why `parent_workbook_name` is denormalized

The Workbooks UI lists views grouped under their parent. Storing the parent name on each view row avoids a self-join in the read query.

## Permission folding logic

Tableau returns content permissions as a list of `PermissionsRule` objects. Each rule has a `grantee` (group or user) and a `capabilities` dict mapping capability name → `'Allow'` | `'Deny'`.

A group "has access" to a workbook or view if **any capability is `Allow` and `Read` is not `Deny`**.

```python
def groups_with_access(rules, group_id_to_name) -> list[tuple[str, str]]:
    """Return [(group_id, group_name), ...] for groups with any allowed access."""
    out = []
    for rule in rules:
        if rule.grantee.tag_name != "group":
            continue                       # skip direct user grants entirely
        caps = rule.capabilities
        if caps.get("Read") == "Deny":
            continue                       # explicit Read-Deny blocks all access
        if any(mode == "Allow" for mode in caps.values()):
            out.append((rule.grantee.id, group_id_to_name[rule.grantee.id]))
    return out
```

Rationale:
- `Read` is the foundational capability; without it, no other capability is usable. An explicit `Deny` on `Read` means the group is blocked regardless of other rules.
- Any `Allow` (Filter, ExportData, Write, etc.) signals intent to grant access. A rule with only `Deny` entries grants nothing.
- This matches the question a compliance auditor actually asks: "Can this group see it at all?"

The snapshot script builds an `{group_id: group_name}` map during step 2 (groups fetch) and reuses it for steps 3–4, since `PermissionsRule.grantee` carries an ID but not a name.

## Diff logic

`diff.py` gains a parallel function `compute_content_access_diff(conn, prev_id, curr_id) -> dict`, mirroring the existing `compute_diff`. The `EXCEPT` projects on **stable identifiers only** — `(content_type, content_id, group_id)` — and the human-readable columns (`content_name`, `parent_workbook_id`, `group_name`) are joined back from the source snapshot when constructing the change row. This ensures a workbook rename, project move, or group rename produces no spurious `added`/`removed` pairs.

```sql
-- Added: (content, group) pairs in current but not previous.
-- Pull names/parent from the CURRENT snapshot, since the row is "alive" there.
INSERT INTO content_access_changes (
    detected_at, content_type, content_id, content_name,
    parent_workbook_id, group_name, group_id,
    change_type, previous_snapshot_id, current_snapshot_id
)
SELECT
    :now, c.content_type, c.content_id, c.content_name,
    c.parent_workbook_id, c.group_name, c.group_id,
    'added', :prev, :curr
FROM content_group_access c
WHERE c.snapshot_id = :curr
  AND c.group_id IS NOT NULL
  AND (c.content_type, c.content_id, c.group_id) IN (
      SELECT content_type, content_id, group_id
      FROM content_group_access
      WHERE snapshot_id = :curr AND group_id IS NOT NULL
      EXCEPT
      SELECT content_type, content_id, group_id
      FROM content_group_access
      WHERE snapshot_id = :prev AND group_id IS NOT NULL
  );

-- Removed: pairs in previous but not current.
-- Pull names/parent from the PREVIOUS snapshot, since the row no longer exists in current.
-- Mirror image of the above: source = :prev, EXCEPT direction reversed, change_type='removed'.
```

Notes:
- The diff key is `(content_type, content_id, group_id)`. Workbook renames, project moves, and group renames are all invisible to the equality test.
- Placeholder rows are excluded via `group_id IS NOT NULL`, so a workbook moving from "no access" to "group X granted" produces only one `added` row, not a phantom `removed` row for the placeholder.
- A workbook deleted between snapshots produces `removed` rows for every group that previously had access. Names come from the previous snapshot since the row is gone in the current one. This matches the existing `membership_changes` behavior.

The diff runs inside the same transaction as the content insert in `snapshot.py`, immediately after the existing `compute_diff` call.

## UI: Workbooks page

A new file `pages/workbooks.py` is registered in `app.py` as the third nav entry, between Current State and Changes. Existing pages are unchanged.

### Empty state

When no successful snapshot has any rows in `content_group_access`:

> No workbook data yet. Run `python3 snapshot.py` to capture workbooks, views, and their group permissions.

### Populated state

- **Snapshot selector** — same dropdown component as Current State, defaults to the latest successful snapshot.
- **Search bar** — case-insensitive substring filter applied to workbook name, view name, project name, and group name.
- **Workbooks table** — one row per workbook. Columns:
  - Project
  - Workbook name
  - Groups with access (comma-separated; `—` if zero)
  - View count
  - Views differing from parent (count, e.g. `2 / 5`)
- **Row expansion** — clicking a workbook reveals its views in a nested table. Columns:
  - View name
  - Groups with access
  - **Differs from parent?** — `✓` highlighted with a yellow background when the view's group set is not equal to the parent workbook's group set; blank otherwise.
- **Export CSV** — flattens to one row per (content item, group) pair, including a `parent_workbook` column. Placeholder rows render as `(no group access)` in the group column.

### Read query

A single fetch per snapshot, then the page groups results in Python:

```python
rows = conn.execute("""
    SELECT content_type, content_id, content_name, project_name,
           parent_workbook_id, parent_workbook_name, group_id, group_name
    FROM content_group_access
    WHERE snapshot_id = ?
    ORDER BY project_name, content_name, group_name
""", (snapshot_id,)).fetchall()
```

The page builds `{workbook_id: {groups: set, views: [...]}}` and computes "differs" by set inequality. Streamlit renders the nested tables.

## Files affected

| File | Change |
| --- | --- |
| `db.py` | Add `content_group_access` and `content_access_changes` to `SCHEMA`. Add helper functions `insert_content_access`, `get_content_access_for_snapshot`, `get_content_access_changes_between`. |
| `snapshot.py` | Add `fetch_all_workbook_permissions`, `fetch_all_view_permissions`, the `groups_with_access` fold, and a content-diff invocation. Extend `take_snapshot` to call them inside the existing `with server.auth.sign_in(auth):` block. |
| `diff.py` | Add `compute_content_access_diff(conn, prev_id, curr_id)`. |
| `app.py` | Register a new `st.Page` for `pages/workbooks.py`. |
| `pages/workbooks.py` | New file. |
| `README.md` | Document the new page and the additional snapshot output. |

## Verification checklist

No test framework exists in the repo and adding one is out of scope. Verification is manual.

1. **Schema migration is safe on existing DBs.** Run `python3 -c "import db"` against a `data/groups.db` that has prior snapshots. The new `CREATE TABLE IF NOT EXISTS` statements should add the new tables without touching existing ones. Confirm with `sqlite3 data/groups.db ".schema"`.
2. **Snapshot capture against a real Tableau site.** Run `python3 snapshot.py`. Console output should gain two lines: `Captured N workbooks, M views` and `Content access diff vs snapshot #X: A added, B removed`. The first run after the upgrade prints `First content snapshot — no previous data to diff against`.
3. **Zero-access placeholder rows exist.** After the first new snapshot, `SELECT COUNT(*) FROM content_group_access WHERE snapshot_id = (SELECT MAX(id) FROM snapshots) AND group_id IS NULL` should be `> 0` if any workbook or view has no group grants.
4. **Diff excludes placeholders.** After a second snapshot in which one workbook gains its first group, `SELECT change_type, content_name, group_name FROM content_access_changes ORDER BY id DESC` should show exactly one `added` row, no spurious `removed` placeholder.
5. **Streamlit empty state.** Wipe `data/groups.db`, run `streamlit run app.py`, open the Workbooks page. The empty-state message should appear, no stack trace.
6. **Streamlit populated state.** With a real snapshot loaded: workbook list renders, expand reveals views, "differs from parent" highlight appears on at least one view (cross-check against Tableau Server), CSV export downloads.
7. **Performance sanity.** Time the snapshot run. The captured trade-off is that capture may take 10–30 minutes on a site with hundreds of workbooks; flag if it exceeds that envelope.

## Rollout

Single PR. No feature flag. The two new pages and the two new tables are additive; existing pages and tables continue to function with no migration step beyond the `CREATE TABLE IF NOT EXISTS` on next import.
