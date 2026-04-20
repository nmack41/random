import streamlit as st

st.set_page_config(page_title="Tableau Groups Monitor", page_icon=":busts_in_silhouette:", layout="wide")

pages = [
    st.Page("pages/current_state.py", title="Current State", icon=":mag:"),
    st.Page("pages/changes.py", title="Changes", icon=":chart_with_upwards_trend:"),
]

nav = st.navigation(pages)
nav.run()
