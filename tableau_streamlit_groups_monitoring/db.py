import os
import sqlite3
from datetime import datetime

from config import DB_PATH

STATUS_IN_PROGRESS = "in_progress"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"
CHANGE_SITE_ROLE = "site_role_changed"
CURRENT_SCHEMA_VERSION = 4  # 1 = pre-views; 2 = views + view_group_access; 3 = users + user_changes; 4 = groups inventory

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress'
);

CREATE TABLE IF NOT EXISTS group_members (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    group_name TEXT NOT NULL,
    group_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    user_id TEXT NOT NULL,
    site_role TEXT NOT NULL,
    domain_name TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_gm_snapshot_group ON group_members(snapshot_id, group_name);
CREATE INDEX IF NOT EXISTS idx_gm_snapshot_user ON group_members(snapshot_id, user_name);

CREATE TABLE IF NOT EXISTS membership_changes (
    id INTEGER PRIMARY KEY,
    detected_at DATETIME NOT NULL,
    group_name TEXT NOT NULL,
    group_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    user_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    previous_snapshot_id INTEGER REFERENCES snapshots(id),
    current_snapshot_id INTEGER NOT NULL REFERENCES snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_mc_current ON membership_changes(current_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_mc_group ON membership_changes(group_name);

CREATE TABLE IF NOT EXISTS workbooks (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    workbook_id TEXT NOT NULL,
    workbook_name TEXT NOT NULL,
    project_name TEXT,
    UNIQUE (snapshot_id, workbook_id)
);

CREATE INDEX IF NOT EXISTS idx_workbooks_snapshot
    ON workbooks(snapshot_id);

CREATE TABLE IF NOT EXISTS workbook_group_access (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    workbook_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    group_name TEXT,
    UNIQUE (snapshot_id, workbook_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_wga_snapshot
    ON workbook_group_access(snapshot_id);

CREATE TABLE IF NOT EXISTS views (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    view_id TEXT NOT NULL,
    view_name TEXT NOT NULL,
    workbook_id TEXT NOT NULL,
    UNIQUE (snapshot_id, view_id)
);

CREATE INDEX IF NOT EXISTS idx_views_snapshot
    ON views(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_views_workbook
    ON views(snapshot_id, workbook_id);

CREATE TABLE IF NOT EXISTS view_group_access (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    view_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    group_name TEXT,
    UNIQUE (snapshot_id, view_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_vga_snapshot
    ON view_group_access(snapshot_id);

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
    change_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    previous_snapshot_id INTEGER REFERENCES snapshots(id),
    current_snapshot_id INTEGER NOT NULL REFERENCES snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_uc_current ON user_changes(current_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_uc_user ON user_changes(user_id);

CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    group_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    domain_name TEXT NOT NULL DEFAULT '',
    UNIQUE (snapshot_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_groups_snapshot ON groups(snapshot_id);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    try:
        # SCHEMA uses CREATE TABLE IF NOT EXISTS throughout — running it on an
        # existing DB is a no-op for old tables and creates any new ones.
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (CURRENT_SCHEMA_VERSION,),
            )
        elif row["version"] < CURRENT_SCHEMA_VERSION:
            # Additive-only migration: the executescript above already created
            # any new tables. Just bump the recorded version.
            conn.execute(
                "UPDATE schema_version SET version = ?",
                (CURRENT_SCHEMA_VERSION,),
            )
        elif row["version"] > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"DB is newer (v{row['version']}) than code (v{CURRENT_SCHEMA_VERSION}). "
                f"Update the code."
            )
        conn.commit()
    finally:
        conn.close()


def create_snapshot(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO snapshots (timestamp, status) VALUES (?, ?)",
        (datetime.now().isoformat(), STATUS_IN_PROGRESS),
    )
    return cur.lastrowid


def complete_snapshot(conn: sqlite3.Connection, snapshot_id: int, status: str = STATUS_SUCCESS):
    conn.execute(
        "UPDATE snapshots SET status = ? WHERE id = ?",
        (status, snapshot_id),
    )


def insert_members(conn: sqlite3.Connection, snapshot_id: int, members: list[dict]):
    conn.executemany(
        """INSERT INTO group_members
           (snapshot_id, group_name, group_id, user_name, user_id, site_role, domain_name)
           VALUES (:snapshot_id, :group_name, :group_id, :user_name, :user_id, :site_role, :domain_name)""",
        [{"snapshot_id": snapshot_id, **m} for m in members],
    )


def get_latest_snapshot_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT id FROM snapshots WHERE status = ? ORDER BY id DESC LIMIT 1",
        (STATUS_SUCCESS,),
    ).fetchone()
    return row["id"] if row else None


def get_previous_snapshot_id(conn: sqlite3.Connection, current_id: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM snapshots WHERE status = ? AND id < ? ORDER BY id DESC LIMIT 1",
        (STATUS_SUCCESS, current_id),
    ).fetchone()
    return row["id"] if row else None


def get_snapshot_list(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, timestamp, status FROM snapshots WHERE status = ? ORDER BY id DESC",
        (STATUS_SUCCESS,),
    ).fetchall()


def get_members_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT group_name, user_name, site_role, domain_name FROM group_members WHERE snapshot_id = ? ORDER BY group_name, user_name",
        (snapshot_id,),
    ).fetchall()


def insert_groups(conn: sqlite3.Connection, snapshot_id: int, groups: list[dict]):
    conn.executemany(
        """INSERT INTO groups
           (snapshot_id, group_id, group_name, domain_name)
           VALUES (:snapshot_id, :group_id, :group_name, :domain_name)""",
        [{"snapshot_id": snapshot_id, **g} for g in groups],
    )


def get_groups_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    # Returns the full group inventory captured at snapshot time, including
    # zero-member groups. Empty result is the legacy-snapshot signal — callers
    # should fall back to DISTINCT group_id FROM group_members and warn.
    return conn.execute(
        "SELECT group_id, group_name, domain_name FROM groups WHERE snapshot_id = ? ORDER BY group_name",
        (snapshot_id,),
    ).fetchall()


def insert_workbooks(conn: sqlite3.Connection, snapshot_id: int, workbooks: list[dict]):
    conn.executemany(
        """INSERT INTO workbooks
           (snapshot_id, workbook_id, workbook_name, project_name)
           VALUES (:snapshot_id, :workbook_id, :workbook_name, :project_name)""",
        [{"snapshot_id": snapshot_id, **w} for w in workbooks],
    )


def insert_workbook_group_access(conn: sqlite3.Connection, snapshot_id: int, grants: list[dict]):
    conn.executemany(
        """INSERT INTO workbook_group_access
           (snapshot_id, workbook_id, group_id, group_name)
           VALUES (:snapshot_id, :workbook_id, :group_id, :group_name)""",
        [{"snapshot_id": snapshot_id, **g} for g in grants],
    )


def get_workbooks_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT w.workbook_id, w.workbook_name, w.project_name,
                  a.group_id, a.group_name
           FROM workbooks w
           LEFT JOIN workbook_group_access a
             ON a.snapshot_id = w.snapshot_id
            AND a.workbook_id = w.workbook_id
           WHERE w.snapshot_id = ?
           ORDER BY w.project_name, w.workbook_name, a.group_name""",
        (snapshot_id,),
    ).fetchall()


def insert_views(conn: sqlite3.Connection, snapshot_id: int, views: list[dict]):
    conn.executemany(
        """INSERT INTO views
           (snapshot_id, view_id, view_name, workbook_id)
           VALUES (:snapshot_id, :view_id, :view_name, :workbook_id)""",
        [{"snapshot_id": snapshot_id, **v} for v in views],
    )


def insert_view_group_access(conn: sqlite3.Connection, snapshot_id: int, grants: list[dict]):
    conn.executemany(
        """INSERT INTO view_group_access
           (snapshot_id, view_id, group_id, group_name)
           VALUES (:snapshot_id, :view_id, :group_id, :group_name)""",
        [{"snapshot_id": snapshot_id, **g} for g in grants],
    )


def get_views_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
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


def get_changes_between(conn: sqlite3.Connection, from_snapshot_id: int, to_snapshot_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT group_name, user_name, change_type, detected_at
           FROM membership_changes
           WHERE previous_snapshot_id >= ? AND current_snapshot_id <= ?
           ORDER BY detected_at DESC, group_name, user_name""",
        (from_snapshot_id, to_snapshot_id),
    ).fetchall()


def insert_users(conn: sqlite3.Connection, snapshot_id: int, users: list[dict]):
    conn.executemany(
        """INSERT INTO users
           (snapshot_id, user_id, user_name, full_name, email, site_role, domain_name, last_login)
           VALUES (:snapshot_id, :user_id, :user_name, :full_name, :email, :site_role, :domain_name, :last_login)""",
        [{"snapshot_id": snapshot_id, **u} for u in users],
    )


def get_users_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    # GROUP_CONCAT aggregates groups in SQL — avoids row blowup before pandas.
    # LEFT JOIN keeps zero-group users in the result set.
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


def get_user_changes_between(conn: sqlite3.Connection, from_snapshot_id: int, to_snapshot_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT detected_at, user_id, user_name, change_type, old_value, new_value
           FROM user_changes
           WHERE previous_snapshot_id >= ? AND current_snapshot_id <= ?
           ORDER BY detected_at DESC, user_name""",
        (from_snapshot_id, to_snapshot_id),
    ).fetchall()


def get_view_options_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    """One row per view, with workbook and project name for disambiguation in the selectbox."""
    return conn.execute(
        """SELECT v.view_id, v.view_name, w.workbook_name, w.project_name
           FROM views v
           JOIN workbooks w
             ON w.snapshot_id = v.snapshot_id
            AND w.workbook_id = v.workbook_id
           WHERE v.snapshot_id = ?
           ORDER BY w.project_name, w.workbook_name, v.view_name""",
        (snapshot_id,),
    ).fetchall()


def get_workbook_options_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    """One row per workbook, ordered by project then name."""
    return conn.execute(
        """SELECT workbook_id, workbook_name, project_name
           FROM workbooks
           WHERE snapshot_id = ?
           ORDER BY project_name, workbook_name""",
        (snapshot_id,),
    ).fetchall()


def _users_with_access_query(grant_table: str, grant_id_col: str) -> str:
    # Chain: grant table -> group_members -> users. All joins snapshot-scoped to
    # avoid cross-snapshot contamination. INNER JOIN on group_members is
    # deliberate: an empty granted group yields zero rows here, which is correct
    # — "can see this" requires a user to exist.
    return f"""
        SELECT u.user_id,
               u.user_name,
               u.full_name,
               u.email,
               u.site_role,
               u.domain_name,
               u.last_login,
               GROUP_CONCAT(DISTINCT COALESCE(g.group_name, '<unresolved:' || g.group_id || '>')) AS via_groups,
               COUNT(DISTINCT g.group_id) AS via_group_count
        FROM {grant_table} g
        JOIN group_members gm
          ON gm.snapshot_id = g.snapshot_id
         AND gm.group_id = g.group_id
        JOIN users u
          ON u.snapshot_id = gm.snapshot_id
         AND u.user_id = gm.user_id
        WHERE g.snapshot_id = ? AND g.{grant_id_col} = ?
        GROUP BY u.user_id
        ORDER BY u.user_name
    """


def get_users_with_access_to_view(conn: sqlite3.Connection, snapshot_id: int, view_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        _users_with_access_query("view_group_access", "view_id"),
        (snapshot_id, view_id),
    ).fetchall()


def get_users_with_access_to_workbook(conn: sqlite3.Connection, snapshot_id: int, workbook_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        _users_with_access_query("workbook_group_access", "workbook_id"),
        (snapshot_id, workbook_id),
    ).fetchall()


def get_all_view_grants_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    # One row per (view, group) grant. LEFT JOIN to group_members so groups that
    # grant access but have zero members still appear (member_count = 0) — that's
    # an audit signal worth surfacing, not noise to hide.
    return conn.execute(
        """SELECT v.view_id,
                  v.view_name,
                  w.workbook_id,
                  w.workbook_name,
                  w.project_name,
                  vga.group_id,
                  COALESCE(vga.group_name, '<unresolved:' || vga.group_id || '>') AS group_name,
                  COUNT(gm.user_id) AS member_count
           FROM view_group_access vga
           JOIN views v
             ON v.snapshot_id = vga.snapshot_id
            AND v.view_id = vga.view_id
           JOIN workbooks w
             ON w.snapshot_id = v.snapshot_id
            AND w.workbook_id = v.workbook_id
           LEFT JOIN group_members gm
             ON gm.snapshot_id = vga.snapshot_id
            AND gm.group_id = vga.group_id
           WHERE vga.snapshot_id = ?
           GROUP BY v.view_id, vga.group_id
           ORDER BY w.project_name, w.workbook_name, v.view_name, group_name""",
        (snapshot_id,),
    ).fetchall()


def get_all_workbook_grants_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT w.workbook_id,
                  w.workbook_name,
                  w.project_name,
                  wga.group_id,
                  COALESCE(wga.group_name, '<unresolved:' || wga.group_id || '>') AS group_name,
                  COUNT(gm.user_id) AS member_count
           FROM workbook_group_access wga
           JOIN workbooks w
             ON w.snapshot_id = wga.snapshot_id
            AND w.workbook_id = wga.workbook_id
           LEFT JOIN group_members gm
             ON gm.snapshot_id = wga.snapshot_id
            AND gm.group_id = wga.group_id
           WHERE wga.snapshot_id = ?
           GROUP BY w.workbook_id, wga.group_id
           ORDER BY w.project_name, w.workbook_name, group_name""",
        (snapshot_id,),
    ).fetchall()


def get_all_user_workbook_access_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    # One row per (user, workbook) pair. INNER JOIN on group_members (not LEFT) is
    # deliberate: zero-member granted groups produce no rows, which matches the
    # audit semantics ("can this user actually see this?" requires the user to exist).
    # via_group_count > 1 signals redundant policy — audit cleanup candidate.
    return conn.execute(
        """SELECT w.workbook_id,
                  w.workbook_name,
                  w.project_name,
                  u.user_id,
                  u.user_name,
                  u.full_name,
                  u.email,
                  u.site_role,
                  u.domain_name,
                  u.last_login,
                  COUNT(DISTINCT wga.group_id) AS via_group_count
           FROM workbook_group_access wga
           JOIN workbooks w
             ON w.snapshot_id = wga.snapshot_id
            AND w.workbook_id = wga.workbook_id
           JOIN group_members gm
             ON gm.snapshot_id = wga.snapshot_id
            AND gm.group_id = wga.group_id
           JOIN users u
             ON u.snapshot_id = gm.snapshot_id
            AND u.user_id = gm.user_id
           WHERE wga.snapshot_id = ?
           GROUP BY w.workbook_id, u.user_id
           ORDER BY w.project_name, w.workbook_name, u.user_name""",
        (snapshot_id,),
    ).fetchall()


def get_all_user_view_access_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    # See get_all_user_workbook_access_for_snapshot for join semantics.
    return conn.execute(
        """SELECT v.view_id,
                  v.view_name,
                  w.workbook_id,
                  w.workbook_name,
                  w.project_name,
                  u.user_id,
                  u.user_name,
                  u.full_name,
                  u.email,
                  u.site_role,
                  u.domain_name,
                  u.last_login,
                  COUNT(DISTINCT vga.group_id) AS via_group_count
           FROM view_group_access vga
           JOIN views v
             ON v.snapshot_id = vga.snapshot_id
            AND v.view_id = vga.view_id
           JOIN workbooks w
             ON w.snapshot_id = v.snapshot_id
            AND w.workbook_id = v.workbook_id
           JOIN group_members gm
             ON gm.snapshot_id = vga.snapshot_id
            AND gm.group_id = vga.group_id
           JOIN users u
             ON u.snapshot_id = gm.snapshot_id
            AND u.user_id = gm.user_id
           WHERE vga.snapshot_id = ?
           GROUP BY v.view_id, u.user_id
           ORDER BY w.project_name, w.workbook_name, v.view_name, u.user_name""",
        (snapshot_id,),
    ).fetchall()


# Ensure schema exists on import
init_db()
