import os
import sqlite3
from datetime import datetime

from config import DB_PATH

STATUS_IN_PROGRESS = "in_progress"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"
CURRENT_SCHEMA_VERSION = 2  # 1 = pre-views; 2 = views + view_group_access added

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


# Ensure schema exists on import
init_db()
