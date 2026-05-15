# Plan: Add a Users page

Add a fourth Streamlit page that lists all site users, including users in zero groups, with per-user metadata (email, full name, last login) sourced from the Tableau Server REST API. Plan revised after multi-model critique (gpt-5.4 / gemini-3.1-pro / grok-4.3).

## Scope

**In scope (v1):**
- New snapshot-scoped `users` table populated from `server.users.get()`
- New `pages/users.py` page: snapshot selector → search → table → CSV export
- Additive schema migration to v3 (preserves historical data)
- `user_changes` diff table mirroring `membership_changes` (additions, removals, site_role changes)

**Explicitly NOT in v1:**
- Opinionated audit metrics (stale-login detection, license breakdown, zero-group counts, over-grouped counts)
- Per-user owned-content fetches (`populate_workbooks`, `populate_favorites`) — Tier 3, expensive
- A dedicated "User Changes" page — diffs are captured in the table but surfacing them is a separate page worth doing later

## Feasibility gates — validate BEFORE writing code

The plan depends on two assumptions about the Tableau REST API. Both must be verified against the live server with the production PAT before any implementation work begins.

### Gate 1 — PAT scope on `server.users.get`

`server.users.get` requires Site Admin or Server Admin permission. A non-admin PAT will 403 or return only the caller's own user record. If this gate fails, the plan must shrink to Tier 1 (derive from `group_members` only) and the new `users` table is not viable.

**Probe:**
```python
# tools/probe_users_api.py — discard after validation
import tableauserverclient as TSC
import config

server = TSC.Server(config.TABLEAU_SERVER_URL)
server.version = "3.22"
auth = TSC.PersonalAccessTokenAuth(config.TABLEAU_PAT_NAME, config.TABLEAU_PAT_SECRET, site_id=config.TABLEAU_SITE_ID)
with server.auth.sign_in(auth):
    users = list(TSC.Pager(server.users.get))
    print(f"Returned {len(users)} users")
    sample = users[0]
    print(f"Fields present: name={sample.name!r} email={getattr(sample, 'email', None)!r} "
          f"full_name={getattr(sample, 'fullname', None)!r} "
          f"last_login={getattr(sample, 'last_login', None)!r} "
          f"site_role={sample.site_role!r}")
```

**Pass condition:** count matches expected site population (cross-check against Tableau Server admin UI). If count is 1 or 0, the PAT lacks scope.

### Gate 2 — `last_login` reliability

TSC's `UserItem.last_login` is nullable, has poorly documented timezone semantics, and may be entirely absent at API version 3.22 (pinned at [snapshot.py:162](../../snapshot.py#L162)).

**Pass condition:**
- Field is present on the object (not `AttributeError`)
- ≥80% of active users have a non-null value
- The value parses as a recognizable timestamp (ISO 8601 or RFC-compatible)

**If it fails:** keep `last_login` in the schema (no harm in nullable column) but drop it from the headline UI and skip the "humanize" formatting work.

## Schema migration (db.py)

### Current behavior to fix

[db.py:126-130](../../db.py#L126-L130) raises `RuntimeError` on version mismatch. Because `init_db()` runs on every import ([db.py:268](../../db.py#L268)), this would brick the Streamlit read path the moment the constant is bumped — *before* the snapshot script has had a chance to migrate. Historical `membership_changes` rows survive the schema bump only if no one wipes `groups.db` to "fix" the error.

### New behavior

Replace the strict mismatch branch with an additive migration. Pseudocode:

```python
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)  # already CREATE TABLE IF NOT EXISTS — safe
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (CURRENT_SCHEMA_VERSION,))
        elif row["version"] < CURRENT_SCHEMA_VERSION:
            # Additive only: the SCHEMA executescript above already created any
            # new tables. Just bump the version row.
            conn.execute("UPDATE schema_version SET version = ?", (CURRENT_SCHEMA_VERSION,))
        elif row["version"] > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(f"DB is newer (v{row['version']}) than code (v{CURRENT_SCHEMA_VERSION}). Update the code.")
        conn.commit()
    finally:
        conn.close()
```

This is safe because every existing schema change in this project has been additive (new tables, never column changes). If a future change is destructive, that future migration adds its own `if old_version < N` branch with explicit ALTER/COPY logic.

### New tables (added to `SCHEMA` constant)

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    full_name TEXT,
    email TEXT,
    site_role TEXT NOT NULL,
    domain_name TEXT NOT NULL DEFAULT '',
    last_login DATETIME,
    UNIQUE (snapshot_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_users_snapshot ON users(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_users_snapshot_name ON users(snapshot_id, user_name);

CREATE TABLE IF NOT EXISTS user_changes (
    id INTEGER PRIMARY KEY,
    detected_at DATETIME NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    change_type TEXT NOT NULL,        -- 'added' | 'removed' | 'site_role_changed'
    old_value TEXT,                    -- previous site_role for site_role_changed; NULL otherwise
    new_value TEXT,                    -- current site_role for site_role_changed; site_role at add/remove time otherwise
    previous_snapshot_id INTEGER REFERENCES snapshots(id),
    current_snapshot_id INTEGER NOT NULL REFERENCES snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_uc_current ON user_changes(current_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_uc_user ON user_changes(user_id);
```

`CURRENT_SCHEMA_VERSION = 3` at [db.py:12](../../db.py#L12).

### Why `user_changes` ships in v1

The multi-model panel (2 of 3) recommended adding this now. Argument: you're already paying the snapshot cost; the diff is essentially free to compute; deferring forces a future re-migration. The table writes happen during snapshot, with zero new UI surface in v1 — surfacing the data is a future page. This keeps the "no opinionated metrics on the Users page itself" preference intact while preventing hidden product debt.

If you want to defer this anyway, drop the `user_changes` table from the SCHEMA additions and the `compute_user_diff` step from snapshot.py. The rest of the plan still works.

## Data layer (db.py)

New helpers, alongside existing patterns at [db.py:150-156](../../db.py#L150-L156) and [db.py:189-218](../../db.py#L189-L218):

```python
def insert_users(conn, snapshot_id, users):
    conn.executemany(
        """INSERT INTO users
           (snapshot_id, user_id, user_name, full_name, email, site_role, domain_name, last_login)
           VALUES (:snapshot_id, :user_id, :user_name, :full_name, :email, :site_role, :domain_name, :last_login)""",
        [{"snapshot_id": snapshot_id, **u} for u in users],
    )

def get_users_for_snapshot(conn, snapshot_id):
    # GROUP_CONCAT aggregates groups in SQL — avoids row blowup before pandas.
    # COALESCE keeps zero-group users in the result set (LEFT JOIN + GROUP BY).
    return conn.execute(
        """SELECT u.user_id,
                  u.user_name,
                  u.full_name,
                  u.email,
                  u.site_role,
                  u.domain_name,
                  u.last_login,
                  COUNT(gm.id) AS group_count,
                  COALESCE(GROUP_CONCAT(DISTINCT gm.group_name), '') AS groups
           FROM users u
           LEFT JOIN group_members gm
             ON gm.snapshot_id = u.snapshot_id
            AND gm.user_id = u.user_id
           WHERE u.snapshot_id = ?
           GROUP BY u.user_id
           ORDER BY u.user_name""",
        (snapshot_id,),
    ).fetchall()

def get_user_changes_between(conn, from_snapshot_id, to_snapshot_id):
    return conn.execute(
        """SELECT detected_at, user_id, user_name, change_type, old_value, new_value
           FROM user_changes
           WHERE previous_snapshot_id >= ? AND current_snapshot_id <= ?
           ORDER BY detected_at DESC, user_name""",
        (from_snapshot_id, to_snapshot_id),
    ).fetchall()
```

## Snapshot script (snapshot.py)

### New fetcher

```python
def fetch_all_users(server: TSC.Server) -> list[dict]:
    """Fetch every site user via REST API."""
    users = []
    all_users = list(TSC.Pager(server.users.get))
    print(f"Found {len(all_users)} users")
    for u in all_users:
        users.append({
            "user_id": u.id,
            "user_name": u.name,
            "full_name": getattr(u, "fullname", None),
            "email": getattr(u, "email", None),
            "site_role": u.site_role,
            "domain_name": getattr(u, "domain_name", "") or "",
            "last_login": getattr(u, "last_login", None),
        })
    return users
```

Naming note: TSC exposes `fullname` (one word) on `UserItem`. Gate 1 probe confirms the spelling on v3.22 before this lands.

### Wiring into `take_snapshot`

Insert the users fetch **inside** the existing `with server.auth.sign_in(auth):` block, then write it inside the existing transaction. See [snapshot.py:169-183](../../snapshot.py#L169-L183):

```python
with server.auth.sign_in(auth):
    members, group_id_to_name = fetch_all_group_members(server)
    workbooks, grants = fetch_all_workbook_permissions(server, group_id_to_name)
    views, view_grants = fetch_all_view_permissions(server, grants, group_id_to_name)
    users = fetch_all_users(server)  # NEW

# ... existing prints ...
db.insert_members(conn, snapshot_id, members)
db.insert_workbooks(conn, snapshot_id, workbooks)
db.insert_workbook_group_access(conn, snapshot_id, grants)
db.insert_views(conn, snapshot_id, views)
db.insert_view_group_access(conn, snapshot_id, view_grants)
db.insert_users(conn, snapshot_id, users)  # NEW
db.complete_snapshot(conn, snapshot_id, db.STATUS_SUCCESS)
conn.commit()
```

**Transactional trade-off considered.** A late user-fetch failure currently rolls back workbook + view captures too. The panel flagged this. However, the existing snapshot.py already uses the unified-transaction pattern for groups/workbooks/views; introducing best-effort decomposition for users alone would be inconsistent. Two acceptable options:

- **Option A (chosen):** Keep users in the unified transaction. Consistency with existing pattern. Failure mode: whole snapshot fails atomically.
- **Option B:** Move users fetch outside the main transaction, mirror the best-effort diff pattern at [snapshot.py:202-213](../../snapshot.py#L202-L213). Allows users to be re-run independently. Adds complexity.

Default to A unless Gate 1 reveals that users-fetch is materially flakier than the others.

### User diff (new file: `user_diff.py`, mirroring `diff.py`)

Same SQL `EXCEPT` shape as the existing membership diff, but on the `users` table keyed by `user_id`. Three change types:
- `added` — `user_id` exists in B but not A
- `removed` — exists in A but not B
- `site_role_changed` — exists in both, different `site_role`

Called from `take_snapshot` right after `compute_diff` at [snapshot.py:202-213](../../snapshot.py#L202-L213), in the same best-effort try block (a user-diff failure must not downgrade the snapshot).

## UI (pages/users.py)

Mirror [pages/current_state.py](../../pages/current_state.py) structure. Key differences:

- **Three metric tiles deferred.** The panel (grok-4.3) flagged that "Users / Domains / Site roles" counts may be premature. Show only **Total users** for v1. Adding the other two later is one line each.
- **Group display: `# Groups` column, not joined cell.** Panel was unanimous. The `groups` string is still in the dataframe (for search) but hidden by default; revealed via `st.dataframe(column_config=...)` controls or a "Show groups column" checkbox.
- **`last_login` formatting:** humanized in UI ("23 days ago" / "never"), raw ISO in CSV export. Compute both with `pd.to_datetime` + a small helper. Defer until Gate 2 passes.
- **Default sort:** alphabetical by `user_name`. Roster-first, not activity-first.
- **Search:** `user_name`, `full_name`, `email`, `domain_name`, and `groups` (the comma-joined string). Pandas `str.contains` across these columns, same idiom as [pages/current_state.py:38-42](../../pages/current_state.py#L38-L42).

Columns: `User Name | Full Name | Email | Domain | Site Role | Last Login | # Groups`.

## Navigation (app.py)

Add to the list at [app.py:5-9](../../app.py#L5-L9):

```python
st.Page("pages/users.py", title="Users"),
```

Place between "Current State" and "Views" so the nav reads: Current State → Users → Views → Changes.

## Documentation drift

[PROJECT_STRUCTURE.md:16-17](../../PROJECT_STRUCTURE.md#L16-L17) lists only `current_state.py` and `changes.py` under `pages/` — it predates `views.py`. Adding a fourth page makes the drift worse. Update the file tree and add a Users page section mirroring the existing per-file descriptions.

## Reordered open questions

Decisions still required (in priority order):

| # | Question | Decision needed before |
|---|---|---|
| 1 | Does the production PAT have Site Admin scope? | Any code |
| 2 | Does `last_login` populate reliably on API v3.22? | UI work |
| 3 | Is `user_id` stable across snapshots? | `user_diff.py` |
| 4 | Keep `user_changes` table in v1, or defer? | Schema migration |
| 5 | (UX) Show `groups` column by default? | UI work — not blocking |
| 6 | (UX) Humanized timestamp format detail | UI work — not blocking |

## Implementation order

1. Probe Gates 1 + 2. Stop if either fails.
2. Update `db.py`: SCHEMA additions, version constant, migration logic, helpers.
3. Update `snapshot.py`: `fetch_all_users`, wire into `take_snapshot`.
4. Add `user_diff.py`, call from `take_snapshot`.
5. Add `pages/users.py`.
6. Update `app.py` nav.
7. Run snapshot once. Verify users appear, including zero-group users.
8. Run snapshot a second time. Verify `user_changes` populates correctly when nothing changes (should be empty).
9. Update `PROJECT_STRUCTURE.md`.

## Why this differs from the original plan

| Issue | Original plan | Revised | Source |
|---|---|---|---|
| Schema bump | Forced re-seed via RuntimeError | Additive migration preserving history | All 3 panelists (critical) |
| Group aggregation | LEFT JOIN + pandas-side dedup | SQL `GROUP_CONCAT` | gpt-5.4 + gemini |
| PAT scope | Open question | Pre-coding feasibility gate | gpt-5.4 (go/no-go) |
| `last_login` | "Open question: humanize vs ISO" | Pre-coding feasibility gate | gpt-5.4 + gemini |
| User-level diff | Out of scope | `user_changes` table ships in v1 (UI deferred) | gemini + grok-4.3 (tiebreaker) |
| Metric tiles | 3 counts | 1 count, defer others | grok-4.3 |
| Indexes | `snapshot_id` only | + `(snapshot_id, user_name)` | gpt-5.4 |
| Group display | Open question | `# Groups` count, `groups` hidden | All 3 panelists |
