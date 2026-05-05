from contextlib import closing

import pandas as pd
import streamlit as st

import db

PLACEHOLDER = "—"

st.header("View Permissions")
st.caption(
    "Best-effort approximation of group access per view under a Read=Allow-only model. "
    "When a view has any explicit group Read rule, only its Read=Allow groups are shown; "
    "otherwise access is inherited from the parent workbook. "
    "Project-level locks, direct user grants, and capabilities other than Read are not "
    "represented — treat this page as a starting point for audit, not a final source of truth. "
    "A flagged row with no listed groups is an explicit Deny that blocks parent inheritance."
)

with closing(db.get_connection()) as conn:
    snapshots = db.get_snapshot_list(conn)

    if not snapshots:
        st.info("No view data yet. Run `python3 snapshot.py` to capture views and their group permissions.")
        st.stop()

    snapshot_options = {s["id"]: f"#{s['id']} — {s['timestamp']}" for s in snapshots}
    selected_id = st.selectbox(
        "Snapshot",
        options=list(snapshot_options.keys()),
        format_func=lambda x: snapshot_options[x],
    )

    rows = db.get_views_for_snapshot(conn, selected_id)
    wb_rows = db.get_workbooks_for_snapshot(conn, selected_id)

if not rows:
    st.info("No view data yet. Run `python3 snapshot.py` to capture views and their group permissions.")
    st.stop()

# Per-workbook group_id sets, used downstream to flag views whose effective
# set diverges from their parent workbook. Keyed by workbook_id; LEFT-JOIN
# null group rows are skipped (they mean "no grants," not "a group with id None").
wb_groups: dict[str, set[str]] = {}
for r in wb_rows:
    s = wb_groups.setdefault(r["workbook_id"], set())
    if r["group_id"] is not None:
        s.add(r["group_id"])

# Collapse the LEFT JOIN into per-workbook → per-view → groups, preserving SQL ordering
# (project, workbook_name, view_name, group_name).
workbooks: dict[str, dict] = {}
for r in rows:
    wb_id = r["workbook_id"]
    wb = workbooks.setdefault(wb_id, {
        "project": r["project_name"] or "",
        "name": r["workbook_name"],
        "views": {},
    })
    if r["view_id"] is None:
        continue  # workbook with zero views; placeholder row rendered below
    view = wb["views"].setdefault(r["view_id"], {
        "name": r["view_name"],
        "groups": [],
        "group_ids": set(),
    })
    if r["group_id"] is not None:
        # Show the raw group_id for unresolved (stale) references rather than blanking it,
        # mirroring pages/workbooks.py.
        view["groups"].append(r["group_name"] or f"<unresolved:{r['group_id']}>")
        view["group_ids"].add(r["group_id"])

# Flag views whose effective group set diverges from the parent workbook's.
# Suppressed for workbooks with zero group grants — those are surfaced on the
# Workbooks page instead (see Known limitations in the design spec).
for wb_id, wb in workbooks.items():
    wb_set = wb_groups.get(wb_id, set())
    skip_diff = not wb_set
    for v in wb["views"].values():
        v["differs"] = (not skip_diff) and (v["group_ids"] != wb_set)

total_views = sum(len(wb["views"]) for wb in workbooks.values())
col1, col2 = st.columns(2)
col1.metric("Workbooks", len(workbooks))
col2.metric("Views", total_views)

search = st.text_input("Search workbook, project, view, or group name")
needle = search.strip().lower()


def workbook_matches(wb: dict) -> bool:
    if needle in wb["project"].lower() or needle in wb["name"].lower():
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
