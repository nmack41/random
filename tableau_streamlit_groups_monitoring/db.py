import os
import sqlite3
from datetime import datetime

from config import DB_PATH

STATUS_IN_PROGRESS = "in_progress"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"

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
    conn.executescript(SCHEMA)
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
