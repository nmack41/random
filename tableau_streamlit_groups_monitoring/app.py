import streamlit as st

st.set_page_config(page_title="Tableau Groups Monitor", layout="wide")

pages = [
    st.Page("pages/current_state.py", title="Current State", ),
    st.Page("pages/users.py", title="Users", ),
    st.Page("pages/views.py", title="Views", ),
    st.Page("pages/access_audit.py", title="Access Audit", ),
    st.Page("pages/changes.py", title="Changes", ),
]

nav = st.navigation(pages)
nav.run()
