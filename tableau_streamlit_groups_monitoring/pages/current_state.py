import streamlit as st
import pandas as pd

import db

st.header("Current Group Memberships")

conn = db.get_connection()
snapshots = db.get_snapshot_list(conn)

if not snapshots:
    st.warning("No snapshots found. Run `python snapshot.py` to capture group data.")
    conn.close()
    st.stop()

# Snapshot selector
snapshot_options = {s["id"]: f"#{s['id']} — {s['timestamp']}" for s in snapshots}
selected_id = st.selectbox(
    "Snapshot",
    options=list(snapshot_options.keys()),
    format_func=lambda x: snapshot_options[x],
)

# Load data
rows = db.get_members_for_snapshot(conn, selected_id)
conn.close()

df = pd.DataFrame(rows, columns=["Group Name", "User Name", "Site Role", "Domain"])

# Summary stats
col1, col2, col3 = st.columns(3)
col1.metric("Groups", df["Group Name"].nunique())
col2.metric("Users", df["User Name"].nunique())
col3.metric("Memberships", len(df))

# Search filter
search = st.text_input("Search groups or users")
if search:
    mask = (
        df["Group Name"].str.contains(search, case=False, na=False)
        | df["User Name"].str.contains(search, case=False, na=False)
    )
    df = df[mask]

st.dataframe(df, use_container_width=True, hide_index=True)

# CSV export
snapshot_ts = snapshot_options[selected_id].split(" — ")[1].replace(":", "-")
csv = df.to_csv(index=False)
st.download_button(
    "Export CSV",
    data=csv,
    file_name=f"group_memberships_{snapshot_ts}.csv",
    mime="text/csv",
)
