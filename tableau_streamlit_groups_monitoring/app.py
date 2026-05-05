import streamlit as st

st.set_page_config(page_title="Tableau Groups Monitor", layout="wide")

pages = [
    st.Page("pages/current_state.py", title="Current State", ),
    st.Page("pages/workbooks.py", title="Workbooks", ),
    st.Page("pages/views.py", title="Views", ),
    st.Page("pages/changes.py", title="Changes", ),
]

nav = st.navigation(pages)
nav.run()
