"""Snapshot script: connects to Tableau Server, captures all group memberships,
and computes a diff against the previous snapshot.

Usage:  python snapshot.py
"""

import sys

import tableauserverclient as TSC

import config
import db
from diff import compute_diff

PAGE_SIZE = 100


def fetch_all_group_members(server: TSC.Server) -> list[dict]:
    """Fetch every group and its full member list from Tableau Server."""
    members = []
    all_groups = list(TSC.Pager(server.groups))
    print(f"Found {len(all_groups)} groups")

    req = TSC.RequestOptions(pagesize=PAGE_SIZE)
    for group in all_groups:
        page_number = 1
        while True:
            req.pagenumber = page_number
            server.groups.populate_users(group, req)
            for user in group.users:
                members.append({
                    "group_name": group.name,
                    "group_id": group.id,
                    "user_name": user.name,
                    "user_id": user.id,
                    "site_role": user.site_role,
                    "domain_name": getattr(user, "domain_name", ""),
                })
            if len(group.users) < PAGE_SIZE:
                break
            page_number += 1

    return members


def take_snapshot():
    """Authenticate, snapshot all group memberships, and compute diff."""
    conn = db.get_connection()
    snapshot_id = db.create_snapshot(conn)

    try:
        server = TSC.Server(config.TABLEAU_SERVER_URL, use_server_version=True)
        auth = TSC.PersonalAccessTokenAuth(
            config.TABLEAU_PAT_NAME,
            config.TABLEAU_PAT_SECRET,
            site_id=config.TABLEAU_SITE_ID,
        )

        with server.auth.sign_in(auth):
            members = fetch_all_group_members(server)

        print(f"Captured {len(members)} total memberships")
        db.insert_members(conn, snapshot_id, members)
        db.complete_snapshot(conn, snapshot_id, db.STATUS_SUCCESS)
        conn.commit()

        previous_id = db.get_previous_snapshot_id(conn, snapshot_id)
        if previous_id is not None:
            summary = compute_diff(conn, previous_id, snapshot_id)
            conn.commit()
            print(f"Diff vs snapshot #{previous_id}: {summary['added']} added, {summary['removed']} removed")
        else:
            print("First snapshot — no previous data to diff against")

    except Exception as e:
        print(f"Snapshot failed: {e}", file=sys.stderr)
        db.complete_snapshot(conn, snapshot_id, db.STATUS_FAILED)
        conn.commit()
        conn.close()
        sys.exit(1)

    conn.close()
    print(f"Snapshot #{snapshot_id} complete")


if __name__ == "__main__":
    take_snapshot()
