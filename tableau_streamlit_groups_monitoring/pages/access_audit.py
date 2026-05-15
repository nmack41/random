from contextlib import closing

import pandas as pd
import streamlit as st

import db
from formatting import humanize_last_login

VIEW = "View"
WORKBOOK = "Workbook"
ALL_GRANTS = "All grants"
GROUPS_BY_WORKBOOKS = "Groups × Workbooks"
GROUPS_BY_VIEWS = "Groups × Views"
USERS_BY_WORKBOOKS = "Users × Workbooks"
USERS_BY_VIEWS = "Users × Views"

st.header("Access Audit")
st.caption(
    "Pick a view or workbook to list every user who has access via group "
    "membership. The 'Via Groups' column shows which group(s) grant each "
    "user's access — useful for tracing unexpected permissions back to a policy. "
    "Direct user grants and capabilities other than Read=Allow are not represented. "
    "Workbook-level access doesn't guarantee access to every view in it "
    "(individual views can override) — use the Views page for per-view accuracy. "
    "Use 'All grants' to browse the full (target × group) permissions landscape."
)

with closing(db.get_connection()) as conn:
    snapshots = db.get_snapshot_list(conn)
    if not snapshots:
        st.info("No snapshots yet. Run `python3 snapshot.py` to capture data.")
        st.stop()

    snapshot_options = {s["id"]: f"#{s['id']} — {s['timestamp']}" for s in snapshots}
    selected_snapshot_id = st.selectbox(
        "Snapshot",
        options=list(snapshot_options.keys()),
        format_func=lambda x: snapshot_options[x],
    )

    target_type = st.radio(
        "Audit target",
        [
            VIEW, WORKBOOK, ALL_GRANTS,
            GROUPS_BY_WORKBOOKS, GROUPS_BY_VIEWS,
            USERS_BY_WORKBOOKS, USERS_BY_VIEWS,
        ],
        horizontal=True,
    )

    if target_type == ALL_GRANTS:
        view_grants = db.get_all_view_grants_for_snapshot(conn, selected_snapshot_id)
        workbook_grants = db.get_all_workbook_grants_for_snapshot(conn, selected_snapshot_id)

        view_df = pd.DataFrame(
            [dict(r) for r in view_grants],
            columns=[
                "view_id", "view_name", "workbook_id", "workbook_name",
                "project_name", "group_id", "group_name", "member_count",
            ],
        )
        view_df["Type"] = VIEW
        view_df = view_df.rename(columns={"view_name": "Target"})
        view_df["Workbook"] = view_df["workbook_name"]

        wb_df = pd.DataFrame(
            [dict(r) for r in workbook_grants],
            columns=[
                "workbook_id", "workbook_name", "project_name",
                "group_id", "group_name", "member_count",
            ],
        )
        wb_df["Type"] = WORKBOOK
        wb_df = wb_df.rename(columns={"workbook_name": "Target"})
        wb_df["Workbook"] = ""  # workbook-level grants have no parent workbook column

        grants = pd.concat([view_df, wb_df], ignore_index=True, sort=False)
        grants = grants.rename(columns={
            "project_name": "Project",
            "group_name": "Group",
            "member_count": "# Members",
        })

        if grants.empty:
            st.info("No group grants recorded for any view or workbook in this snapshot.")
            st.stop()

        col1, col2, col3 = st.columns(3)
        col1.metric("Total grants", len(grants))
        col2.metric("Distinct groups", grants["Group"].nunique())
        col3.metric("Distinct targets", grants[["Type", "Target"]].drop_duplicates().shape[0])

        search = st.text_input("Search project, workbook, target, or group")

        # TODO(you): Build the filter `mask` over `grants` using `search`.
        # Decisions to make:
        #   - Which columns should the search match against? (Project, Workbook,
        #     Target, Group are the candidates — all are strings.)
        #   - Case-insensitive substring is the convention used elsewhere in this
        #     file (see line ~112 for the pattern: .str.contains(needle, case=False,
        #     na=False, regex=False)).
        #   - When `search` is empty, the table should show everything.
        # Aim for 5–10 lines. Replace the placeholder below.
        filtered = grants  # placeholder: no filtering yet

        st.dataframe(
            filtered,
            use_container_width=True,
            hide_index=True,
            column_order=["Type", "Project", "Workbook", "Target", "Group", "# Members"],
        )

        snapshot_ts = snapshot_options[selected_snapshot_id].split(" — ")[1].replace(":", "-")
        csv = filtered[["Type", "Project", "Workbook", "Target", "Group", "# Members"]].to_csv(index=False)
        st.download_button(
            "Export CSV",
            data=csv,
            file_name=f"access_all_grants_{snapshot_ts}.csv",
            mime="text/csv",
        )
        st.stop()

    if target_type in (GROUPS_BY_WORKBOOKS, GROUPS_BY_VIEWS):
        # Build the long-form (group, target, member_count) table by reusing the
        # same SQL that powers ALL_GRANTS — pivoting in pandas is cheaper than
        # writing a second matrix-shaped query.
        if target_type == GROUPS_BY_WORKBOOKS:
            rows = db.get_all_workbook_grants_for_snapshot(conn, selected_snapshot_id)
            df = pd.DataFrame(
                [dict(r) for r in rows],
                columns=[
                    "workbook_id", "workbook_name", "project_name",
                    "group_id", "group_name", "member_count",
                ],
            )
            target_kind = "workbook"
        else:
            rows = db.get_all_view_grants_for_snapshot(conn, selected_snapshot_id)
            df = pd.DataFrame(
                [dict(r) for r in rows],
                columns=[
                    "view_id", "view_name", "workbook_id", "workbook_name",
                    "project_name", "group_id", "group_name", "member_count",
                ],
            )
            target_kind = "view"

        if df.empty:
            st.info(f"No group grants on any {target_kind} in this snapshot.")
            st.stop()

        # Project filter is mandatory for both — even the workbook matrix can
        # be unwieldy at scale. The view matrix also takes a workbook filter
        # because a single workbook can have dozens of views.
        projects = sorted(df["project_name"].dropna().unique().tolist())
        selected_projects = st.multiselect(
            "Project filter", projects, default=projects,
        )
        df = df[df["project_name"].isin(selected_projects)]

        if target_kind == "view":
            workbooks = sorted(df["workbook_name"].dropna().unique().tolist())
            selected_workbooks = st.multiselect(
                "Workbook filter", workbooks, default=workbooks,
            )
            df = df[df["workbook_name"].isin(selected_workbooks)]
            # Disambiguate views with the same name across workbooks.
            df["target_label"] = df["workbook_name"] + " / " + df["view_name"]
        else:
            df["target_label"] = (
                df["project_name"].fillna("") + " / " + df["workbook_name"]
            )

        if df.empty:
            st.info("No grants match the current filters.")
            st.stop()

        # TODO(you): decide what each cell in the matrix should show.
        # The input is `member_count` (int >= 0) for one (group, target) grant.
        # Decisions to make:
        #   - "✓" / "" — cleanest visual scan; hides the stale-grant signal.
        #   - str(member_count) — numeric heat map; readable but noisier.
        #   - "✓ ({n})" — both signals; my recommended starting point.
        #   - "✓" vs "⚠ stale" when member_count == 0 — surfaces dead policies.
        # The audit value of this view depends on this choice. Aim for 3-6 lines.
        def format_cell(member_count: int) -> str:
            return "✓"  # placeholder — replace with your chosen formatting

        df["cell"] = df["member_count"].map(format_cell)

        matrix = df.pivot_table(
            index="group_name",
            columns="target_label",
            values="cell",
            aggfunc="first",
            fill_value="",
        )
        matrix = matrix.sort_index().sort_index(axis=1)

        col1, col2, col3 = st.columns(3)
        col1.metric("Groups", matrix.shape[0])
        col2.metric(f"{target_kind.capitalize()}s", matrix.shape[1])
        col3.metric("Grants shown", int((matrix != "").sum().sum()))

        st.dataframe(matrix, use_container_width=True)

        snapshot_ts = snapshot_options[selected_snapshot_id].split(" — ")[1].replace(":", "-")
        csv = matrix.to_csv()
        st.download_button(
            "Export CSV",
            data=csv,
            file_name=f"access_groups_by_{target_kind}s_{snapshot_ts}.csv",
            mime="text/csv",
        )
        st.stop()

    if target_type in (USERS_BY_WORKBOOKS, USERS_BY_VIEWS):
        # Parallel to the groups matrix above, but rows are users (expanded out
        # through group_members in SQL). Cell value is via_group_count — how many
        # distinct groups grant this user this access. > 1 = redundant policy.
        if target_type == USERS_BY_WORKBOOKS:
            rows = db.get_all_user_workbook_access_for_snapshot(conn, selected_snapshot_id)
            df = pd.DataFrame(
                [dict(r) for r in rows],
                columns=[
                    "workbook_id", "workbook_name", "project_name",
                    "user_id", "user_name", "full_name", "email",
                    "site_role", "domain_name", "last_login", "via_group_count",
                ],
            )
            target_kind = "workbook"
        else:
            rows = db.get_all_user_view_access_for_snapshot(conn, selected_snapshot_id)
            df = pd.DataFrame(
                [dict(r) for r in rows],
                columns=[
                    "view_id", "view_name", "workbook_id", "workbook_name",
                    "project_name", "user_id", "user_name", "full_name", "email",
                    "site_role", "domain_name", "last_login", "via_group_count",
                ],
            )
            target_kind = "view"

        if df.empty:
            st.info(f"No users have access to any {target_kind} via groups in this snapshot.")
            st.stop()

        # Project + (workbook) + site_role filters keep the matrix scannable —
        # users × targets can blow up faster than groups × targets.
        projects = sorted(df["project_name"].dropna().unique().tolist())
        selected_projects = st.multiselect(
            "Project filter", projects, default=projects,
        )
        df = df[df["project_name"].isin(selected_projects)]

        if target_kind == "view":
            workbooks = sorted(df["workbook_name"].dropna().unique().tolist())
            selected_workbooks = st.multiselect(
                "Workbook filter", workbooks, default=workbooks,
            )
            df = df[df["workbook_name"].isin(selected_workbooks)]
            df["target_label"] = df["workbook_name"] + " / " + df["view_name"]
        else:
            df["target_label"] = (
                df["project_name"].fillna("") + " / " + df["workbook_name"]
            )

        # Site role filter: most audit value lives in non-admin roles since
        # admins have access by definition. Default keeps everything visible.
        site_roles = sorted(df["site_role"].dropna().unique().tolist())
        selected_roles = st.multiselect(
            "Site role filter", site_roles, default=site_roles,
        )
        df = df[df["site_role"].isin(selected_roles)]

        # last_login arrives as SQLite text — normalize once so downstream
        # comparisons and the humanizer both see real timestamps. utc=True
        # because Tableau emits UTC and we don't want naive/aware mix-ups.
        df["last_login"] = pd.to_datetime(df["last_login"], errors="coerce", utc=True)

        stale_days = st.slider(
            "Show only users inactive more than (days)",
            min_value=0, max_value=365, value=0,
            help="0 = no staleness filter. Higher = stricter audit lens.",
        )

        # TODO(you): apply the staleness filter when stale_days > 0.
        # Two real decisions, both with audit consequences:
        #
        #   1) NULL handling. df["last_login"].isna() means "never logged in"
        #      (or never reported by Tableau). Are those users MORE stale than
        #      anyone (include them) or unknown/excluded (drop them)? The
        #      conservative audit answer is "include" — never-logged-in
        #      accounts with permissions are exactly what cleanup hunts for.
        #
        #   2) Cutoff arithmetic. Compute the threshold timestamp once
        #      (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=stale_days))
        #      and compare. Don't iterate.
        #
        # Aim for ~5 lines. Skip the block entirely when stale_days == 0 so
        # the existing "no filter" path stays cheap.
        if stale_days > 0:
            pass  # replace with your filter logic

        if df.empty:
            st.info("No grants match the current filters.")
            st.stop()

        # TODO(you): parallel decision to format_cell above (line ~180), but the
        # input here is `via_group_count` (# of distinct groups granting this
        # user this access).
        # Decisions to make:
        #   - "✓" / "" — pure presence; loses the redundancy signal.
        #   - str(via_group_count) — numeric; high values = over-permissioned users.
        #   - "✓ ({n})" — both signals; redundant grants visible at a glance.
        # Consistency with the groups matrix is nice but not required — these
        # cells encode different semantics (member_count vs via_group_count).
        # Aim for 3-6 lines. Replace the placeholder.
        def format_user_cell(via_group_count: int) -> str:
            return "✓"  # placeholder — see TODO above

        df["cell"] = df["via_group_count"].map(format_user_cell)

        # user_name is Tableau-unique; full_name can collide. Trade readability
        # for unambiguous row labels.
        matrix = df.pivot_table(
            index="user_name",
            columns="target_label",
            values="cell",
            aggfunc="first",
            fill_value="",
        )
        matrix = matrix.sort_index().sort_index(axis=1)

        col1, col2, col3 = st.columns(3)
        col1.metric("Users", matrix.shape[0])
        col2.metric(f"{target_kind.capitalize()}s", matrix.shape[1])
        col3.metric("Grants shown", int((matrix != "").sum().sum()))

        st.dataframe(matrix, use_container_width=True)

        snapshot_ts = snapshot_options[selected_snapshot_id].split(" — ")[1].replace(":", "-")
        csv = matrix.to_csv()
        st.download_button(
            "Export CSV",
            data=csv,
            file_name=f"access_users_by_{target_kind}s_{snapshot_ts}.csv",
            mime="text/csv",
        )
        st.stop()

    if target_type == VIEW:
        options = db.get_view_options_for_snapshot(conn, selected_snapshot_id)
        if not options:
            st.info("No views in this snapshot.")
            st.stop()
        labels = {}
        for o in options:
            prefix = " / ".join(p for p in [o["project_name"], o["workbook_name"]] if p)
            labels[o["view_id"]] = f"{prefix} / {o['view_name']}" if prefix else o["view_name"]
        target_id = st.selectbox(
            "View",
            options=list(labels.keys()),
            format_func=lambda x: labels[x],
        )
        rows = db.get_users_with_access_to_view(conn, selected_snapshot_id, target_id)
        # Count granted groups to disambiguate the two empty-state cases below.
        granted_group_count = conn.execute(
            "SELECT COUNT(*) AS c FROM view_group_access WHERE snapshot_id = ? AND view_id = ?",
            (selected_snapshot_id, target_id),
        ).fetchone()["c"]
    else:
        options = db.get_workbook_options_for_snapshot(conn, selected_snapshot_id)
        if not options:
            st.info("No workbooks in this snapshot.")
            st.stop()
        labels = {}
        for o in options:
            labels[o["workbook_id"]] = (
                f"{o['project_name']} / {o['workbook_name']}"
                if o["project_name"] else o["workbook_name"]
            )
        target_id = st.selectbox(
            "Workbook",
            options=list(labels.keys()),
            format_func=lambda x: labels[x],
        )
        rows = db.get_users_with_access_to_workbook(conn, selected_snapshot_id, target_id)
        granted_group_count = conn.execute(
            "SELECT COUNT(*) AS c FROM workbook_group_access WHERE snapshot_id = ? AND workbook_id = ?",
            (selected_snapshot_id, target_id),
        ).fetchone()["c"]

if not rows:
    # Distinguish "no groups granted access" from "groups granted but all empty" —
    # different audit signals: the first is a permissions gap, the second is a stale
    # group definition.
    if granted_group_count == 0:
        st.info(
            f"No groups have access to this {target_type.lower()}. "
            "Access (if any) would come from direct user grants or project-level locks, "
            "neither of which is captured by this tool."
        )
    else:
        st.info(
            f"{granted_group_count} group(s) are granted access, but they have no members. "
            "Either the groups are empty, or membership data is missing for this snapshot."
        )
    st.stop()

df = pd.DataFrame(
    [dict(r) for r in rows],
    columns=[
        "user_id", "user_name", "full_name", "email",
        "site_role", "domain_name", "last_login",
        "via_groups", "via_group_count",
    ],
)
df["Last Login"] = df["last_login"].map(humanize_last_login)

st.metric("Users with access", len(df))

search = st.text_input("Search name, email, domain, or granting group")
if search:
    needle = search.strip()
    mask = (
        df["user_name"].str.contains(needle, case=False, na=False, regex=False)
        | df["full_name"].fillna("").str.contains(needle, case=False, na=False, regex=False)
        | df["email"].fillna("").str.contains(needle, case=False, na=False, regex=False)
        | df["domain_name"].str.contains(needle, case=False, na=False, regex=False)
        | df["via_groups"].str.contains(needle, case=False, na=False, regex=False)
    )
    df = df[mask]

display = df.rename(columns={
    "user_name": "User Name",
    "full_name": "Full Name",
    "email": "Email",
    "domain_name": "Domain",
    "site_role": "Site Role",
    "via_group_count": "# Groups",
    "via_groups": "Via Groups",
})

st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    column_order=[
        "User Name", "Full Name", "Email", "Domain",
        "Site Role", "Last Login", "# Groups", "Via Groups",
    ],
)

csv_df = display[["User Name", "Full Name", "Email", "Domain", "Site Role", "# Groups", "Via Groups"]].copy()
csv_df["Last Login"] = display["last_login"]  # ISO for CSV (sortable, machine-readable)
snapshot_ts = snapshot_options[selected_snapshot_id].split(" — ")[1].replace(":", "-")
safe_label = "".join(c if c.isalnum() else "_" for c in labels[target_id])[:80]
csv = csv_df.to_csv(index=False)
st.download_button(
    "Export CSV",
    data=csv,
    file_name=f"access_{target_type.lower()}_{safe_label}_{snapshot_ts}.csv",
    mime="text/csv",
)
