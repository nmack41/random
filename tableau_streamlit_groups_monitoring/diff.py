"""Diff engine: computes group membership changes between two snapshots.

Importable by snapshot.py, also CLI-invocable for ad-hoc comparisons:
    python diff.py <previous_snapshot_id> <current_snapshot_id>
"""

import sqlite3
from datetime import datetime

import db


def compute_diff(conn: sqlite3.Connection, previous_snapshot_id: int, current_snapshot_id: int) -> dict:
    """Compare two snapshots and write changes to membership_changes.

    Returns a summary dict: {"added": int, "removed": int}.
    """
    now = datetime.now().isoformat()

    # Single SQL template: find members in source_id but not compare_id, insert as change_type
    insert_diff_sql = """INSERT INTO membership_changes
        (detected_at, group_name, group_id, user_name, user_id, change_type, previous_snapshot_id, current_snapshot_id)
        SELECT ?, group_name, group_id, user_name, user_id, ?, ?, ?
        FROM (
            SELECT gm.group_name, gm.group_id, gm.user_name, gm.user_id
            FROM group_members gm
            WHERE gm.snapshot_id = ?
            EXCEPT
            SELECT gm2.group_name, gm2.group_id, gm2.user_name, gm2.user_id
            FROM group_members gm2
            WHERE gm2.snapshot_id = ?
        )"""

    added_count = conn.execute(
        insert_diff_sql,
        (now, db.CHANGE_ADDED, previous_snapshot_id, current_snapshot_id,
         current_snapshot_id, previous_snapshot_id),
    ).rowcount

    removed_count = conn.execute(
        insert_diff_sql,
        (now, db.CHANGE_REMOVED, previous_snapshot_id, current_snapshot_id,
         previous_snapshot_id, current_snapshot_id),
    ).rowcount

    return {"added": added_count, "removed": removed_count}


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python diff.py <previous_snapshot_id> <current_snapshot_id>")
        sys.exit(1)

    prev_id = int(sys.argv[1])
    curr_id = int(sys.argv[2])

    conn = db.get_connection()
    summary = compute_diff(conn, prev_id, curr_id)
    conn.commit()
    conn.close()

    print(f"Diff complete: {summary['added']} added, {summary['removed']} removed")
