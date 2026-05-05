# Views Page — Design

## Goal

Add a "Views" page to the Tableau Groups Monitoring app that shows, for every workbook, which groups have read access to each of its views. Layout is one collapsible table per workbook, columns `View` and `Groups with access`. Answers the auditor's question: "Which groups can see this view?"

This is the v2 follow-on deferred by the workbook permissions spec (`2026-05-01-workbook-view-permissions-design.md`), now scoped narrowly to a single new page.

## Non-goals

- **Per-capability permissions.** Carries over the workbook page's existing simplification: only groups with `Read=Allow` count as "having access." Deny rules, capability-level distinctions, and explicit user grants are out.
- **Distinguishing inherited vs explicit access in the UI.** The `Groups with access` column shows the resolved effective set; provenance is not surfaced.
- **View-level diff / change tracking.** `diff.py` and the Changes page are unchanged. View-permission drift is a future concern.
- **Locked-to-project workbooks.** Behavior under project-locked permission models is not specifically tested. Documented as a known limitation; verify against a real instance before relying on this page for audit conclusions on locked content.
- **UI distinction between "no views" and "views with no groups."** Both render as a single placeholder row inside the workbook's expander.
- Automated tests. Verification is manual against the seeded fake-data DB plus a smoke test against a real Tableau instance.

## Architecture

Existing dual-process split is preserved. `snapshot.py` writes; the Streamlit app reads.

```
Tableau Server ─(REST API)─> snapshot.py ──> SQLite DB <── Streamlit app
                              (writes:                      (reads:
                               groups, users,                Current State,
                               workbooks,                    Changes,
                               workbook-group access,        Workbooks,
                               views,                        Views ← new)
                               view-group access)
```

The `views` and `view_group_access` tables mirror the `workbooks` / `workbook_group_access` pair exactly, both scoped by `snapshot_id` so each snapshot remains self-contained point-in-time truth.

**Resolved-at-capture vs. resolved-at-read tradeoff.** Inherited grants are stored verbatim into `view_group_access` rather than represented as a "no rows = inherit" sentinel resolved at read time. Verbatim storage costs W×V rows per inheriting workbook (groups × views) and re-floods on every snapshot when workbook permissions change, but keeps reads as a flat LEFT JOIN, keeps the snapshot self-contained (a snapshot row says exactly what was effective at capture time), and keeps a future drift query against `view_group_access` honest without inheritance-resolution gymnastics. For MVP scale this is the right call; revisit if the table grows past tens of thousands of rows per snapshot.

## Data model

Two new tables added to `db.py`'s `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS views (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    view_id TEXT NOT NULL,
    view_name TEXT NOT NULL,
    workbook_id TEXT NOT NULL,
    UNIQUE (snapshot_id, view_id)
);
CREATE INDEX IF NOT EXISTS idx_views_snapshot ON views(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_views_workbook ON views(snapshot_id, workbook_id);

CREATE TABLE IF NOT EXISTS view_group_access (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    view_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    group_name TEXT,
    UNIQUE (snapshot_id, view_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_vga_snapshot ON view_group_access(snapshot_id);
```

Notes:

- `workbook_id` lives on `views`, not on `view_group_access`. View → workbook is fixed; the join goes through `views` to find a view's workbook.
- `group_name` is nullable to handle the same "stale group reference" case `workbook_group_access` already handles (`pages/workbooks.py` line 45).
- `CREATE TABLE IF NOT EXISTS` makes the schema additions idempotent — existing DBs migrate on next `db.init_db()` call (which runs at import time).
- No `source` / provenance column. The page does not need it, and the workbook page proved this resolution-flat-at-capture pattern works without one.

## Snapshot capture flow

`snapshot.py` is extended with one new function and one extra call inside `take_snapshot`'s try block.

```python
def fetch_all_view_permissions(
    server: TSC.Server,
    workbook_grants: list[dict],
    group_id_to_name: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """Fetch every view and its effective group permissions.

    Returns (views, view_grants).

    Resolution: for each view, if the view has ANY explicit group rule
    (Allow or Deny) on Read, treat the view as having an explicit ruleset
    and store only its Read=Allow groups (which may be empty). Otherwise
    inherit the parent workbook's grants verbatim. Same Read=Allow /
    group-only simplification as the workbook page.

    The "any explicit Read rule blocks inheritance" form prevents a view
    that explicitly Denies a group from silently appearing as having
    access via inheritance — the audit failure mode this tool exists to
    prevent.
    """
    grants_by_wb: dict[str, list[tuple[str, str | None]]] = {}
    for g in workbook_grants:
        grants_by_wb.setdefault(g["workbook_id"], []).append(
            (g["group_id"], g["group_name"])
        )

    views: list[dict] = []
    view_grants: list[dict] = []
    all_views = list(TSC.Pager(server.views.get))
    print(f"Found {len(all_views)} views")

    for view in all_views:
        views.append({
            "view_id": view.id,
            "view_name": view.name,
            "workbook_id": view.workbook_id,
        })
        server.views.populate_permissions(view)
        has_explicit_read_rule = any(
            r.grantee.tag_name == "group" and "Read" in r.capabilities
            for r in view.permissions
        )
        if has_explicit_read_rule:
            effective = groups_with_access(view.permissions, group_id_to_name)
        else:
            effective = set(grants_by_wb.get(view.workbook_id, []))
        for gid, gname in effective:
            view_grants.append({
                "view_id": view.id,
                "group_id": gid,
                "group_name": gname,
            })

    return views, view_grants
```

Call site, inside `take_snapshot`'s `with server.auth.sign_in(auth):` block, immediately after `fetch_all_workbook_permissions`:

```python
views, view_grants = fetch_all_view_permissions(server, grants, group_id_to_name)
...
db.insert_views(conn, snapshot_id, views)
db.insert_view_group_access(conn, snapshot_id, view_grants)
```

`fetch_all_view_permissions` reuses the existing `groups_with_access` helper unchanged. `TSC.Pager(server.views.get)` enumerates views site-wide (flat), so the parent workbook is read off `view.workbook_id` rather than via a nested `workbook → views` walk.

### N+1 cost

`server.views.populate_permissions(view)` is one REST call per view. There is no bulk endpoint in TSC for view permissions. This is accepted, the same way per-workbook `populate_permissions` is accepted today. Snapshot runtime grows linearly with view count.

### Pre-implementation API cost probe

**Required gate before writing schema, helpers, or the page** (inherited from the v1 spec — views amplify the N+1 cost 5–20× over workbooks, so this is more critical here, not less). Run a one-off REPL probe against the target Tableau site:

1. Sign in (reuse the snapshot.py auth block).
2. `views = list(TSC.Pager(server.views.get))` — record the count.
3. Time `server.views.populate_permissions(view)` on 5 views; multiply by the count and add the existing workbook-fetch projection.

If the projected end-to-end snapshot exceeds ~10 minutes, **stop** and revisit the design (e.g., narrow scope to a single project, defer to nightly batch, or explore the GraphQL Metadata API) before writing any code. The verification checklist runs *after* implementation; this probe is the pre-commit kill switch. The N+1 against a server we don't control is the only part of this design that can blow up irreversibly post-merge.

### Failure semantics

The new fetch lives inside the same `try` block in `take_snapshot` (lines 107–139 in current `snapshot.py`). A failure rolls back the entire snapshot to status `failed`, just as a workbook-permissions failure does today. The membership diff (best-effort, runs after success) is unchanged.

## DB helpers

Three new functions in `db.py`, mirroring the existing workbook helpers:

```python
def insert_views(conn, snapshot_id, views): ...
def insert_view_group_access(conn, snapshot_id, view_grants): ...

def get_views_for_snapshot(conn, snapshot_id):
    """LEFT JOIN of workbooks × views × view_group_access, ordered by
    project_name, workbook_name, view_name. Workbooks with no views and
    views with no groups still appear (LEFT JOIN preserves them)."""
    return conn.execute(
        """SELECT w.workbook_id, w.workbook_name, w.project_name,
                  v.view_id, v.view_name,
                  a.group_id, a.group_name
           FROM workbooks w
           LEFT JOIN views v
             ON v.snapshot_id = w.snapshot_id
            AND v.workbook_id = w.workbook_id
           LEFT JOIN view_group_access a
             ON a.snapshot_id = v.snapshot_id
            AND a.view_id = v.view_id
           WHERE w.snapshot_id = ?
           ORDER BY w.project_name, w.workbook_name, v.view_name, a.group_name""",
        (snapshot_id,),
    ).fetchall()
```

## UI page (`pages/views.py`)

Mirrors `pages/workbooks.py`'s shape:

1. Header + caption explaining the simplification.
2. Snapshot selector.
3. Top-level metrics row: workbook count, total view count.
4. Search box across workbook / project / view / group name.
5. One `st.expander` per workbook, sorted by `(project, name)`. Inside each expander, an `st.dataframe` with columns `View` / `Groups with access`. Expanders default to collapsed; matching workbooks auto-expand when the search box is non-empty.

Empty cases:

- Workbook with zero views: expander still renders, with a single placeholder row `{"View": "—", "Groups with access": "—"}`.
- View with zero groups: row renders with `Groups with access: "—"`. Same sentinel as the workbook page.

Caption text:

> Best-effort approximation of group access per view under a Read=Allow-only model. When a view has any explicit group Read rule, only its Read=Allow groups are shown; otherwise access is inherited from the parent workbook. Project-level locks, direct user grants, and capabilities other than Read are not represented — treat this page as a starting point for audit, not a final source of truth.

## Navigation registration

`app.py` adds one entry to the `pages` list, positioned after Workbooks and before Changes:

```python
st.Page("pages/views.py", title="Views"),
```

## Fake data plumbing

For local UI iteration:

- `fake_data/fixtures.py` — extend with view rows. Required coverage: a workbook with views inheriting workbook grants; a workbook with views that have explicit overriding group rules; a view with ONLY a Read=Deny rule and no Allow rule (to exercise the "explicit blocks inheritance → zero groups" path); a workbook with zero views; a view whose explicit rule references a group that's not in the seeded `GROUPS` map (to exercise the stale-reference rendering path).
- `fake_data/seed.py` — add `_build_views()` and `_build_view_grants()` helpers mirroring `_build_workbooks` / `_build_workbook_grants`, and call `db.insert_views` / `db.insert_view_group_access` inside the per-snapshot insert loop.

## Files touched

**Modified**

- `db.py` — append two `CREATE TABLE` blocks; add `insert_views`, `insert_view_group_access`, `get_views_for_snapshot`.
- `snapshot.py` — add `fetch_all_view_permissions`; call it after `fetch_all_workbook_permissions`; insert results before `complete_snapshot`.
- `app.py` — register the new page.
- `fake_data/fixtures.py` — add view fixtures.
- `fake_data/seed.py` — seed view rows alongside workbooks.

**Created**

- `pages/views.py` — the new page.

**Unchanged**

- `diff.py`, `pages/workbooks.py`, `pages/changes.py`, `pages/current_state.py`, `config.py`, `requirements.txt`.

## Known limitations

- Carries over the workbook page's simplification (Read=Allow only, group rules only, ignores Deny / non-Read capabilities / direct user grants). The page surfaces this in its caption.
- Resolution rule "any explicit group Read rule blocks inheritance, then filter to Read=Allow" treats a view with only Read=Deny rules as "explicit, no Allow" → renders zero groups. This avoids the false-positive failure mode where a denied group would otherwise inherit access from the workbook. The cost: a view with Read=Deny on group A and no other rules will show zero groups even though other groups may inherit access at the workbook level. Acceptable for MVP — false zeroes are an audit-safe failure direction; false grants are not.
- Workbook-locked-to-project behavior is not specifically tested. TSC may or may not reflect inherited project-level permissions cleanly when a workbook is locked. Verify before using this page for audit on locked content.
- `populate_permissions` is N+1 per view. Snapshot runtime grows linearly with view count.

## Verification

**Pre-implementation gate:** Pre-implementation API cost probe (see "Pre-implementation API cost probe" above) was run against the target site, and the projected snapshot runtime is under ~10 minutes. Do not begin implementation until this is confirmed.

Manual checklist after implementation, run against the fake-data DB unless noted:

1. `python -m fake_data.seed` rebuilds `fake_data/groups.db` without errors.
2. `FAKE_DATA=1 streamlit run app.py` starts; navigation shows the Views entry between Workbooks and Changes.
3. Views page renders one expander per seeded workbook, including the zero-views workbook.
4. Inheriting views show their parent workbook's groups; overriding views show only their explicit Read=Allow groups.
5. The Deny-only fixture view renders zero groups (`—`) — confirms the "explicit blocks inheritance" rule is wired correctly and the audit page is not falsely inheriting access for an explicitly-denied view.
6. Search box filters and auto-expands matching workbooks.
7. Stale group reference in a view fixture renders as `<unresolved:GID>`.
8. Snapshot selector switches between seeded snapshots without errors.
9. Smoke test on a real Tableau instance: `python3 snapshot.py` completes within the probe's projected runtime; Views page loads against the captured snapshot.
