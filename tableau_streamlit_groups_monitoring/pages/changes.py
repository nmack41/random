import streamlit as st
import pandas as pd

import db

st.header("Membership Changes")

conn = db.get_connection()
snapshots = db.get_snapshot_list(conn)

if len(snapshots) < 2:
    st.info("Need at least two snapshots to show changes. Run `python snapshot.py` again after some time has passed.")
    conn.close()
    st.stop()

snapshot_options = {s["id"]: f"#{s['id']} — {s['timestamp']}" for s in snapshots}
snapshot_ids = list(snapshot_options.keys())

col_from, col_to = st.columns(2)
with col_from:
    from_id = st.selectbox(
        "From snapshot",
        options=snapshot_ids,
        index=min(1, len(snapshot_ids) - 1),  # default to second-most-recent
        format_func=lambda x: snapshot_options[x],
    )
with col_to:
    to_id = st.selectbox(
        "To snapshot",
        options=snapshot_ids,
        index=0,  # default to most recent
        format_func=lambda x: snapshot_options[x],
    )

if from_id >= to_id:
    st.warning("'From' snapshot must be earlier than 'To' snapshot.")
    conn.close()
    st.stop()

rows = db.get_changes_between(conn, from_id, to_id)
conn.close()

if not rows:
    st.success("No membership changes detected in this range.")
    st.stop()

df = pd.DataFrame(rows, columns=["Group Name", "User Name", "Change Type", "Detected At"])

# Summary
added_count = len(df[df["Change Type"] == db.CHANGE_ADDED])
removed_count = len(df[df["Change Type"] == db.CHANGE_REMOVED])
groups_affected = df["Group Name"].nunique()

col1, col2, col3 = st.columns(3)
col1.metric("Additions", added_count)
col2.metric("Removals", removed_count)
col3.metric("Groups Affected", groups_affected)


# Color-coded display
def highlight_changes(row):
    if row["Change Type"] == db.CHANGE_ADDED:
        return ["background-color: #d4edda"] * len(row)
    elif row["Change Type"] == db.CHANGE_REMOVED:
        return ["background-color: #f8d7da"] * len(row)
    return [""] * len(row)


st.dataframe(
    df.style.apply(highlight_changes, axis=1),
    use_container_width=True,
    hide_index=True,
)

# CSV export
csv = df.to_csv(index=False)
st.download_button(
    "Export CSV",
    data=csv,
    file_name=f"membership_changes_{from_id}_to_{to_id}.csv",
    mime="text/csv",
)
