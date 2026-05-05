"""Snapshot script: connects to Tableau Server, captures all group memberships,
and computes a diff against the previous snapshot.

Usage:  python snapshot.py
"""

import sys
import traceback

import tableauserverclient as TSC

import config
import db
from diff import compute_diff

PAGE_SIZE = 100


def fetch_all_group_members(server: TSC.Server) -> tuple[list[dict], dict[str, str]]:
    """Fetch every group and its full member list from Tableau Server.

    Returns (members, group_id_to_name). The name map is reused by the
    workbook-permissions fold, since PermissionsRule grantees carry an ID
    but not a name.
    """
    members = []
    group_id_to_name: dict[str, str] = {}
    all_groups = list(TSC.Pager(server.groups.get))
    print(f"Found {len(all_groups)} groups")

    for group in all_groups:
        group_id_to_name[group.id] = group.name
        server.groups.populate_users(group)
        for user in group.users:
            members.append({
                "group_name": group.name,
                "group_id": group.id,
                "user_name": user.name,
                "user_id": user.id,
                "site_role": user.site_role,
                "domain_name": getattr(user, "domain_name", ""),
            })

    return members, group_id_to_name


def groups_with_access(rules, group_id_to_name: dict[str, str]) -> set[tuple[str, str | None]]:
    """Return {(group_id, group_name), ...} for groups with Read=Allow on a workbook.

    A group "has access" if it has any rule with Read=Allow. Rules with
    Read missing or Read=Deny do not qualify, regardless of other capabilities,
    because Read is foundational on Tableau (no other capability is usable
    without it).

    Returns a set so multiple inheritance-merged rules resolving to the same
    group dedupe naturally. Direct user grants are skipped per non-goal.
    Names default to None when the group_id is not in the map (stale reference).
    """
    out: set[tuple[str, str | None]] = set()
    for rule in rules:
        if rule.grantee.tag_name != "group":
            continue
        if rule.capabilities.get("Read") != "Allow":
            continue
        gid = rule.grantee.id
        out.add((gid, group_id_to_name.get(gid)))
    return out


def fetch_all_workbook_permissions(
    server: TSC.Server,
    group_id_to_name: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """Fetch every workbook and its group-level permissions.

    Returns (workbooks, grants) where workbooks is one row per workbook and
    grants is one row per (workbook, group_with_access) pair.
    """
    workbooks: list[dict] = []
    grants: list[dict] = []
    all_wbs = list(TSC.Pager(server.workbooks.get))
    print(f"Found {len(all_wbs)} workbooks")

    for wb in all_wbs:
        workbooks.append({
            "workbook_id": wb.id,
            "workbook_name": wb.name,
            "project_name": getattr(wb, "project_name", None),
        })
        server.workbooks.populate_permissions(wb)
        for gid, gname in groups_with_access(wb.permissions, group_id_to_name):
            grants.append({
                "workbook_id": wb.id,
                "group_id": gid,
                "group_name": gname,
            })

    return workbooks, grants


def fetch_all_view_permissions(
    server: TSC.Server,
    workbook_grants: list[dict],
    group_id_to_name: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """Fetch every view and its effective group permissions.

    Returns (views, view_grants). Resolution: if a view has ANY explicit
    group rule on Read (Allow or Deny), block inheritance and surface only
    Read=Allow groups. Otherwise inherit the parent workbook's grants
    verbatim. The "any explicit Read rule blocks inheritance" form prevents
    a view that explicitly Denies a group from silently appearing as having
    access via inheritance — the audit failure mode this tool exists to prevent.
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
            rule.grantee.tag_name == "group" and "Read" in rule.capabilities
            for rule in view.permissions
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


def take_snapshot():
    """Authenticate, snapshot all group memberships, and compute diff."""
    conn = db.get_connection()
    snapshot_id = db.create_snapshot(conn)
    conn.commit()

    try:
        server = TSC.Server(config.TABLEAU_SERVER_URL, use_server_version=True)
        auth = TSC.PersonalAccessTokenAuth(
            config.TABLEAU_PAT_NAME,
            config.TABLEAU_PAT_SECRET,
            site_id=config.TABLEAU_SITE_ID,
        )

        with server.auth.sign_in(auth):
            members, group_id_to_name = fetch_all_group_members(server)
            workbooks, grants = fetch_all_workbook_permissions(server, group_id_to_name)
            views, view_grants = fetch_all_view_permissions(server, grants, group_id_to_name)

        print(f"Captured {len(members)} total memberships")
        print(f"Captured {len(workbooks)} workbooks, {len(grants)} workbook-group grants")
        print(f"Captured {len(views)} views, {len(view_grants)} view-group grants")
        db.insert_members(conn, snapshot_id, members)
        db.insert_workbooks(conn, snapshot_id, workbooks)
        db.insert_workbook_group_access(conn, snapshot_id, grants)
        db.insert_views(conn, snapshot_id, views)
        db.insert_view_group_access(conn, snapshot_id, view_grants)
        db.complete_snapshot(conn, snapshot_id, db.STATUS_SUCCESS)
        conn.commit()
    except Exception as e:
        # Discard partial capture rows so the FAILED snapshot has no data attached.
        conn.rollback()
        print(f"Snapshot failed: {e}", file=sys.stderr)
        try:
            from importlib.metadata import version
            print(f"tableauserverclient version: {version('tableauserverclient')}", file=sys.stderr)
        except Exception:
            pass
        traceback.print_exc(file=sys.stderr)
        db.complete_snapshot(conn, snapshot_id, db.STATUS_FAILED)
        conn.commit()
        conn.close()
        sys.exit(1)

    # Diff is best-effort: a failure here must NOT downgrade the already-committed
    # success snapshot. Spec: "If the membership diff fails after a successful
    # workbook capture, the snapshot stays `success` ... drift computation is best-effort."
    try:
        previous_id = db.get_previous_snapshot_id(conn, snapshot_id)
        if previous_id is not None:
            summary = compute_diff(conn, previous_id, snapshot_id)
            conn.commit()
            print(f"Diff vs snapshot #{previous_id}: {summary['added']} added, {summary['removed']} removed")
        else:
            print("First snapshot — no previous data to diff against")
    except Exception as e:
        conn.rollback()
        print(f"Diff failed for snapshot #{snapshot_id} (snapshot remains success): {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    conn.close()
    print(f"Snapshot #{snapshot_id} complete")


if __name__ == "__main__":
    take_snapshot()
