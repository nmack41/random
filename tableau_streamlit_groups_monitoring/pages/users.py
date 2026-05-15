from contextlib import closing

import pandas as pd
import streamlit as st

import db
from formatting import humanize_last_login


st.header("Users")
st.caption(
    "Every user on the site, including users in zero groups. Sourced from the "
    "Tableau Server REST API. Search matches across name, email, domain, and groups."
)

with closing(db.get_connection()) as conn:
    snapshots = db.get_snapshot_list(conn)

    if not snapshots:
        st.info("No snapshots yet. Run `python3 snapshot.py` to capture user data.")
        st.stop()

    snapshot_options = {s["id"]: f"#{s['id']} — {s['timestamp']}" for s in snapshots}
    selected_id = st.selectbox(
        "Snapshot",
        options=list(snapshot_options.keys()),
        format_func=lambda x: snapshot_options[x],
    )

    rows = db.get_users_for_snapshot(conn, selected_id)

if not rows:
    st.info(
        "No users captured for this snapshot. The PAT used by `snapshot.py` may lack "
        "Site Admin scope — see Gate 1 in `docs/dev/users_add.md`."
    )
    st.stop()

df = pd.DataFrame(
    rows,
    columns=[
        "user_id", "user_name", "full_name", "email",
        "site_role", "domain_name", "last_login",
        "group_count", "groups",
    ],
)

# Two parallel last-login columns: humanized for display, ISO for CSV/sort.
df["last_login_dt"] = pd.to_datetime(df["last_login"], errors="coerce", utc=True)
df["Last Login"] = df["last_login"].map(humanize_last_login)

st.metric("Total users", len(df))

search = st.text_input("Search name, email, domain, or group")

if search:
    needle = search.strip()
    mask = (
        df["user_name"].str.contains(needle, case=False, na=False, regex=False)
        | df["full_name"].fillna("").str.contains(needle, case=False, na=False, regex=False)
        | df["email"].fillna("").str.contains(needle, case=False, na=False, regex=False)
        | df["domain_name"].str.contains(needle, case=False, na=False, regex=False)
        | df["groups"].str.contains(needle, case=False, na=False, regex=False)
    )
    df = df[mask]

display = df.rename(columns={
    "user_name": "User Name",
    "full_name": "Full Name",
    "email": "Email",
    "domain_name": "Domain",
    "site_role": "Site Role",
    "group_count": "# Groups",
    "groups": "Groups",
})

column_order = ["User Name", "Full Name", "Email", "Domain", "Site Role", "Last Login", "# Groups", "Groups"]

st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    column_order=column_order,
)

# CSV export uses raw ISO last_login (sortable, machine-readable), not humanized.
csv_df = display[["User Name", "Full Name", "Email", "Domain", "Site Role", "# Groups", "Groups"]].copy()
csv_df["Last Login"] = display["last_login"]
snapshot_ts = snapshot_options[selected_id].split(" — ")[1].replace(":", "-")
csv = csv_df.to_csv(index=False)
st.download_button(
    "Export CSV",
    data=csv,
    file_name=f"users_{snapshot_ts}.csv",
    mime="text/csv",
)
