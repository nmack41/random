"""User diff engine: detects added, removed, and site-role-changed users
between two snapshots.

Importable by snapshot.py, also CLI-invocable:
    python user_diff.py <previous_snapshot_id> <current_snapshot_id>

Three change types are written to `user_changes`:
  - 'added'              user_id present in current but not previous
  - 'removed'            user_id present in previous but not current
  - 'site_role_changed'  user_id present in both, site_role differs

For 'added' and 'removed', new_value carries the site_role at that moment
(current snapshot for added, previous snapshot for removed) and old_value is NULL.
For 'site_role_changed', old_value/new_value are the previous/current site_role.
"""

import sqlite3
from datetime import datetime

import db


def compute_user_diff(conn: sqlite3.Connection, previous_snapshot_id: int, current_snapshot_id: int) -> dict:
    """Compare two snapshots and write user-level changes to user_changes.

    Returns a summary dict: {"added": int, "removed": int, "site_role_changed": int}.
    """
    now = datetime.now().isoformat()

    # added: user_id in current but not previous; new_value = current site_role
    added_count = conn.execute(
        """INSERT INTO user_changes
           (detected_at, user_id, user_name, change_type, old_value, new_value,
            previous_snapshot_id, current_snapshot_id)
           SELECT ?, u.user_id, u.user_name, ?, NULL, u.site_role, ?, ?
           FROM users u
           WHERE u.snapshot_id = ?
             AND u.user_id NOT IN (
                 SELECT user_id FROM users WHERE snapshot_id = ?
             )""",
        (now, db.CHANGE_ADDED, previous_snapshot_id, current_snapshot_id,
         current_snapshot_id, previous_snapshot_id),
    ).rowcount

    # removed: user_id in previous but not current; new_value = site_role at remove time
    removed_count = conn.execute(
        """INSERT INTO user_changes
           (detected_at, user_id, user_name, change_type, old_value, new_value,
            previous_snapshot_id, current_snapshot_id)
           SELECT ?, u.user_id, u.user_name, ?, NULL, u.site_role, ?, ?
           FROM users u
           WHERE u.snapshot_id = ?
             AND u.user_id NOT IN (
                 SELECT user_id FROM users WHERE snapshot_id = ?
             )""",
        (now, db.CHANGE_REMOVED, previous_snapshot_id, current_snapshot_id,
         previous_snapshot_id, current_snapshot_id),
    ).rowcount

    # site_role_changed: same user_id in both snapshots, different site_role.
    # Use a JOIN rather than EXCEPT — we need both sides of the comparison to
    # populate old_value and new_value, which a set-difference can't give us.
    role_changed_count = conn.execute(
        """INSERT INTO user_changes
           (detected_at, user_id, user_name, change_type, old_value, new_value,
            previous_snapshot_id, current_snapshot_id)
           SELECT ?, curr.user_id, curr.user_name, ?, prev.site_role, curr.site_role, ?, ?
           FROM users curr
           JOIN users prev ON prev.user_id = curr.user_id
           WHERE curr.snapshot_id = ?
             AND prev.snapshot_id = ?
             AND curr.site_role <> prev.site_role""",
        (now, db.CHANGE_SITE_ROLE, previous_snapshot_id, current_snapshot_id,
         current_snapshot_id, previous_snapshot_id),
    ).rowcount

    return {
        "added": added_count,
        "removed": removed_count,
        "site_role_changed": role_changed_count,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python user_diff.py <previous_snapshot_id> <current_snapshot_id>")
        sys.exit(1)

    prev_id = int(sys.argv[1])
    curr_id = int(sys.argv[2])

    conn = db.get_connection()
    summary = compute_user_diff(conn, prev_id, curr_id)
    conn.commit()
    conn.close()

    print(
        f"User diff complete: {summary['added']} added, "
        f"{summary['removed']} removed, "
        f"{summary['site_role_changed']} site-role changes"
    )
