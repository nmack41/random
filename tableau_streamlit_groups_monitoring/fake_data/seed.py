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

import config       # noqa: E402
import db           # noqa: E402
import diff         # noqa: E402
import user_diff    # noqa: E402

from fake_data.fixtures import (  # noqa: E402
    DOMAIN, GROUPS, USERS, WORKBOOKS, VIEWS, SNAPSHOTS,
    ZERO_GROUP_USERS, LAST_LOGIN_BY_USER, SITE_ROLE_OVERRIDES,
)


def _wipe_db():
    if os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)
    db.init_db()


def _full_name(user_name: str) -> str:
    """Derive a presentational name from 'first.last' → 'First Last'."""
    return " ".join(part.capitalize() for part in user_name.split("."))


def _role_for(uid: str, snapshot_index: int) -> str:
    """Site role at a given snapshot, honoring per-snapshot overrides."""
    default = USERS[uid][1]
    return SITE_ROLE_OVERRIDES.get(uid, {}).get(snapshot_index, default)


def _build_groups() -> list[dict]:
    return [
        {"group_id": gid, "group_name": gname, "domain_name": DOMAIN}
        for gid, gname in GROUPS.items()
    ]


def _build_members(snapshot_index: int, membership: dict[str, list[str]]) -> list[dict]:
    rows = []
    for group_id, user_ids in membership.items():
        for uid in user_ids:
            user_name, _ = USERS[uid]
            rows.append({
                "group_name":  GROUPS[group_id],
                "group_id":    group_id,
                "user_name":   user_name,
                "user_id":     uid,
                "site_role":   _role_for(uid, snapshot_index),
                "domain_name": DOMAIN,
            })
    return rows


def _build_users(snapshot_index: int, membership: dict[str, list[str]]) -> list[dict]:
    """Users present on the site for this snapshot.

    Set = union of every group's membership ∪ ZERO_GROUP_USERS. This makes a
    user "disappear from the site" simply by removing them from every group's
    membership in SNAPSHOTS — no separate per-snapshot user list to keep in sync.
    """
    user_ids = {uid for uids in membership.values() for uid in uids}
    user_ids.update(ZERO_GROUP_USERS)

    rows = []
    for uid in sorted(user_ids):
        user_name, _ = USERS[uid]
        rows.append({
            "user_id":     uid,
            "user_name":   user_name,
            "full_name":   _full_name(user_name),
            "email":       f"{user_name}@{DOMAIN}.com",
            "site_role":   _role_for(uid, snapshot_index),
            "domain_name": DOMAIN,
            "last_login":  LAST_LOGIN_BY_USER.get(uid),
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


def _build_views() -> list[dict]:
    return [
        {"view_id": v_id, "view_name": name, "workbook_id": workbook_id}
        for v_id, (name, workbook_id, _rules) in VIEWS.items()
    ]


def _build_view_grants() -> list[dict]:
    """Resolve view permissions using the same rule snapshot.py applies:

    - If explicit_rules is None, inherit parent workbook grants verbatim.
    - Otherwise (explicit Read rule(s) exist), block inheritance and surface
      only Read=Allow entries; Deny entries block inheritance but never grant.

    GROUPS.get(gid) returns None for stale references (e.g. g-removed),
    matching how snapshot.py stores nullable group_name.
    """
    workbook_groups = {wb_id: groups for wb_id, (_n, _p, groups) in WORKBOOKS.items()}
    grants: list[dict] = []
    for view_id, (_name, workbook_id, explicit_rules) in VIEWS.items():
        if explicit_rules is None:
            for gid in workbook_groups.get(workbook_id, []):
                grants.append({
                    "view_id":    view_id,
                    "group_id":   gid,
                    "group_name": GROUPS.get(gid),
                })
        else:
            for gid, capability in explicit_rules:
                if capability != "Allow":
                    continue
                grants.append({
                    "view_id":    view_id,
                    "group_id":   gid,
                    "group_name": GROUPS.get(gid),
                })
    return grants


def seed():
    _wipe_db()
    workbooks = _build_workbooks()
    grants = _build_workbook_grants()
    views = _build_views()
    view_grants = _build_view_grants()

    snapshot_ids: list[int] = []
    with closing(db.get_connection()) as conn:
        for idx, (ts, membership) in enumerate(SNAPSHOTS):
            cur = conn.execute(
                "INSERT INTO snapshots (timestamp, status) VALUES (?, ?)",
                (ts, db.STATUS_SUCCESS),
            )
            snapshot_id = cur.lastrowid
            snapshot_ids.append(snapshot_id)

            db.insert_groups(conn, snapshot_id, _build_groups())
            db.insert_members(conn, snapshot_id, _build_members(idx, membership))
            db.insert_workbooks(conn, snapshot_id, workbooks)
            db.insert_workbook_group_access(conn, snapshot_id, grants)
            db.insert_views(conn, snapshot_id, views)
            db.insert_view_group_access(conn, snapshot_id, view_grants)
            db.insert_users(conn, snapshot_id, _build_users(idx, membership))
        conn.commit()

        change_total = 0
        user_change_total = 0
        for prev_id, curr_id in zip(snapshot_ids, snapshot_ids[1:]):
            summary = diff.compute_diff(conn, prev_id, curr_id)
            change_total += summary["added"] + summary["removed"]
            user_summary = user_diff.compute_user_diff(conn, prev_id, curr_id)
            user_change_total += (
                user_summary["added"]
                + user_summary["removed"]
                + user_summary["site_role_changed"]
            )
        conn.commit()

    project_count = len({project for _, project, _ in WORKBOOKS.values()})
    print(
        f"Seeded {config.DB_PATH}\n"
        f"  {len(SNAPSHOTS)} snapshots, "
        f"{len(USERS)} users ({len(ZERO_GROUP_USERS)} in zero groups), "
        f"{len(GROUPS)} groups, "
        f"{len(WORKBOOKS)} workbooks across {project_count} projects, "
        f"{len(VIEWS)} views, "
        f"{len(view_grants)} view-group grants, "
        f"{change_total} membership changes, "
        f"{user_change_total} user changes"
    )


if __name__ == "__main__":
    seed()
