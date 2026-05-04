"""Seed fake_data/groups.db with a small fictional org for local UI demo
and dev iteration.

Run from the project root:
    python -m fake_data.seed

Then point the Streamlit app at the fake DB:
    FAKE_DATA=1 streamlit run app.py
"""
import os
import sys
from contextlib import closing

# Force the fake-data DB path before importing config / db, so db.init_db()
# (which runs at import time) targets fake_data/groups.db.
os.environ["FAKE_DATA"] = "1"

# Ensure the project root is on sys.path when run as `python -m fake_data.seed`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import db      # noqa: E402
import diff    # noqa: E402

from fake_data.fixtures import DOMAIN, GROUPS, USERS, WORKBOOKS, SNAPSHOTS  # noqa: E402


def _wipe_db():
    if os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)
    db.init_db()


def _build_members(membership: dict[str, list[str]]) -> list[dict]:
    rows = []
    for group_id, user_ids in membership.items():
        for uid in user_ids:
            user_name, site_role = USERS[uid]
            rows.append({
                "group_name":  GROUPS[group_id],
                "group_id":    group_id,
                "user_name":   user_name,
                "user_id":     uid,
                "site_role":   site_role,
                "domain_name": DOMAIN,
            })
    return rows


def _build_workbooks() -> list[dict]:
    return [
        {"workbook_id": wb_id, "workbook_name": name, "project_name": project}
        for wb_id, (name, project, _groups) in WORKBOOKS.items()
    ]


def _build_workbook_grants() -> list[dict]:
    grants = []
    for wb_id, (_name, _project, group_ids) in WORKBOOKS.items():
        for gid in group_ids:
            grants.append({
                "workbook_id": wb_id,
                "group_id":    gid,
                "group_name":  GROUPS[gid],
            })
    return grants


def seed():
    _wipe_db()
    workbooks = _build_workbooks()
    grants = _build_workbook_grants()

    snapshot_ids: list[int] = []
    with closing(db.get_connection()) as conn:
        for ts, membership in SNAPSHOTS:
            cur = conn.execute(
                "INSERT INTO snapshots (timestamp, status) VALUES (?, ?)",
                (ts, db.STATUS_SUCCESS),
            )
            snapshot_id = cur.lastrowid
            snapshot_ids.append(snapshot_id)

            db.insert_members(conn, snapshot_id, _build_members(membership))
            db.insert_workbooks(conn, snapshot_id, workbooks)
            db.insert_workbook_group_access(conn, snapshot_id, grants)
        conn.commit()

        change_total = 0
        for prev_id, curr_id in zip(snapshot_ids, snapshot_ids[1:]):
            summary = diff.compute_diff(conn, prev_id, curr_id)
            change_total += summary["added"] + summary["removed"]
        conn.commit()

    project_count = len({project for _, project, _ in WORKBOOKS.values()})
    print(
        f"Seeded {config.DB_PATH}\n"
        f"  {len(SNAPSHOTS)} snapshots, "
        f"{len(USERS)} users, "
        f"{len(GROUPS)} groups, "
        f"{len(WORKBOOKS)} workbooks across {project_count} projects, "
        f"{change_total} membership changes"
    )


if __name__ == "__main__":
    seed()
