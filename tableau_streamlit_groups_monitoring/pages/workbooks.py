from contextlib import closing

import streamlit as st
import pandas as pd

import db

st.header("Workbook Permissions")
st.caption(
    "Permissions reflect group rule presence on the workbook. "
    "Project-level locks and direct user grants are not represented."
)

with closing(db.get_connection()) as conn:
    snapshots = db.get_snapshot_list(conn)

    if not snapshots:
        st.info("No workbook data yet. Run `python3 snapshot.py` to capture workbooks and their group permissions.")
        st.stop()

    snapshot_options = {s["id"]: f"#{s['id']} — {s['timestamp']}" for s in snapshots}
    selected_id = st.selectbox(
        "Snapshot",
        options=list(snapshot_options.keys()),
        format_func=lambda x: snapshot_options[x],
    )

    rows = db.get_workbooks_for_snapshot(conn, selected_id)

if not rows:
    st.info("No workbook data yet. Run `python3 snapshot.py` to capture workbooks and their group permissions.")
    st.stop()

# Collapse the LEFT JOIN result into one row per workbook with a comma-separated group list.
workbooks: dict[str, dict] = {}
for r in rows:
    wb_id = r["workbook_id"]
    entry = workbooks.setdefault(wb_id, {
        "Project": r["project_name"] or "",
        "Workbook": r["workbook_name"],
        "_groups": [],
    })
    if r["group_id"] is not None:
        # Show the raw group_id for unresolved (stale) references rather than blanking it.
        entry["_groups"].append(r["group_name"] or f"<unresolved:{r['group_id']}>")

records = [
    {
        "Project": w["Project"],
        "Workbook": w["Workbook"],
        "Groups with access": ", ".join(w["_groups"]) if w["_groups"] else "—",
    }
    for w in workbooks.values()
]

df = pd.DataFrame(records, columns=["Project", "Workbook", "Groups with access"])

col1, col2 = st.columns(2)
col1.metric("Workbooks", len(df))
col2.metric("Workbooks with no group access", int((df["Groups with access"] == "—").sum()))

search = st.text_input("Search workbook, project, or group name")
needle = search.strip()
if needle:
    mask = (
        df["Project"].str.contains(needle, case=False, na=False, regex=False)
        | df["Workbook"].str.contains(needle, case=False, na=False, regex=False)
        | df["Groups with access"].str.contains(needle, case=False, na=False, regex=False)
    )
    df = df[mask]

st.dataframe(df, use_container_width=True, hide_index=True)
