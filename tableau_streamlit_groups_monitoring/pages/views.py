from contextlib import closing

import pandas as pd
import streamlit as st

import db

PLACEHOLDER = "—"

st.header("Permissions")
st.caption(
    "Group access shown at two levels: each workbook's own group rules, and a "
    "best-effort view of group access per view under a Read=Allow-only model. "
    "When a view has any explicit group Read rule, only its Read=Allow groups are shown; "
    "otherwise access is inherited from the parent workbook. "
    "Project-level locks, direct user grants, and capabilities other than Read are not "
    "represented — treat this page as a starting point for audit, not a final source of truth. "
    "A flagged row with no listed groups is an explicit Deny that blocks parent inheritance."
)

with closing(db.get_connection()) as conn:
    snapshots = db.get_snapshot_list(conn)

    if not snapshots:
        st.info("No data yet. Run `python3 snapshot.py` to capture workbooks, views, and their group permissions.")
        st.stop()

    snapshot_options = {s["id"]: f"#{s['id']} — {s['timestamp']}" for s in snapshots}
    selected_id = st.selectbox(
        "Snapshot",
        options=list(snapshot_options.keys()),
        format_func=lambda x: snapshot_options[x],
    )

    wb_rows = db.get_workbooks_for_snapshot(conn, selected_id)
    rows = db.get_views_for_snapshot(conn, selected_id)

if not rows and not wb_rows:
    st.info("No data yet. Run `python3 snapshot.py` to capture workbooks, views, and their group permissions.")
    st.stop()

# First pass: seed every workbook from wb_rows so a workbook with zero views
# still shows up. Track group_ids (for divergence) and group_names (for the
# Workbook groups block) side by side.
workbooks: dict[str, dict] = {}
for r in wb_rows:
    wb = workbooks.setdefault(r["workbook_id"], {
        "project": r["project_name"] or "",
        "name": r["workbook_name"],
        "wb_group_ids": set(),
        "wb_group_names": [],
        "views": {},
    })
    if r["group_id"] is not None:
        wb["wb_group_ids"].add(r["group_id"])
        # Show the raw group_id for unresolved (stale) references rather than blanking it.
        wb["wb_group_names"].append(r["group_name"] or f"<unresolved:{r['group_id']}>")

# Second pass: collapse view rows into per-workbook → per-view → groups.
for r in rows:
    wb = workbooks.get(r["workbook_id"])
    if wb is None:
        # Defensive: every workbook_id from views should already be in wb_rows.
        continue
    if r["view_id"] is None:
        continue  # workbook with zero views; placeholder row rendered below
    view = wb["views"].setdefault(r["view_id"], {
        "name": r["view_name"],
        "groups": [],
        "group_ids": set(),
    })
    if r["group_id"] is not None:
        view["groups"].append(r["group_name"] or f"<unresolved:{r['group_id']}>")
        view["group_ids"].add(r["group_id"])

# Third pass: flag views whose effective group set diverges from the parent
# workbook's. Suppressed for workbooks with zero group grants — divergence
# isn't meaningful when the parent has no rules to diverge from.
for wb in workbooks.values():
    skip_diff = not wb["wb_group_ids"]
    for v in wb["views"].values():
        v["differs"] = (not skip_diff) and (v["group_ids"] != wb["wb_group_ids"])

total_views = sum(len(wb["views"]) for wb in workbooks.values())
no_access = sum(1 for wb in workbooks.values() if not wb["wb_group_ids"])
col1, col2, col3 = st.columns(3)
col1.metric("Workbooks", len(workbooks))
col2.metric("Views", total_views)
col3.metric("Workbooks with no group access", no_access)

search = st.text_input("Search workbook, project, view, or group name")
needle = search.strip().lower()


def workbook_matches(wb: dict) -> bool:
    if needle in wb["project"].lower() or needle in wb["name"].lower():
        return True
    if any(needle in g.lower() for g in wb["wb_group_names"]):
        return True
    for v in wb["views"].values():
        if needle in v["name"].lower():
            return True
        if any(needle in g.lower() for g in v["groups"]):
            return True
    return False


items = list(workbooks.items())  # already SQL-ordered by (project, workbook_name)
if needle:
    items = [(wb_id, wb) for wb_id, wb in items if workbook_matches(wb)]
    if not items:
        st.info(f"No workbooks match '{search.strip()}'.")

COLUMNS = ["View", "Groups with access", "Differs from workbook"]

# Auto-expand on search: passing `expanded=bool(needle)` is read on every Streamlit rerun
# (it's not a one-shot initial-state prop), so this stays reactive without session_state.
for wb_id, wb in items:
    base_label = f"{wb['project']} / {wb['name']}" if wb["project"] else wb["name"]
    n_diff = sum(1 for v in wb["views"].values() if v["differs"])
    if n_diff > 0:
        noun = "view" if n_diff == 1 else "views"
        label = f":orange[**{base_label}**] ({n_diff} {noun} differ)"
    else:
        label = base_label
    with st.expander(label, expanded=bool(needle)):
        wb_df = pd.DataFrame(
            [{"Workbook groups": ", ".join(wb["wb_group_names"]) or PLACEHOLDER}]
        )
        st.dataframe(wb_df, use_container_width=True, hide_index=True)

        if not wb["views"]:
            records = [{"View": PLACEHOLDER, "Groups with access": PLACEHOLDER, "Differs from workbook": PLACEHOLDER}]
        else:
            # Pre-sort differing rows to the top so the audit signal is visible without
            # requiring a column-header sort click. Stable sort preserves the underlying
            # SQL view-name ordering within each group.
            ordered_views = sorted(wb["views"].values(), key=lambda v: not v["differs"])
            records = [
                {
                    "View": v["name"],
                    "Groups with access": ", ".join(v["groups"]) if v["groups"] else PLACEHOLDER,
                    "Differs from workbook": "Yes" if v["differs"] else "",
                }
                for v in ordered_views
            ]
        df = pd.DataFrame(records, columns=COLUMNS)
        st.dataframe(df, use_container_width=True, hide_index=True)
