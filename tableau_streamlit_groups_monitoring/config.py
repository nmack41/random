import os
from dotenv import load_dotenv

load_dotenv()

# Optional at import time so fake_data/seed.py can import db.py without
# Tableau credentials. snapshot.py validates these at sign-in time.
TABLEAU_SERVER_URL = os.getenv("TABLEAU_SERVER_URL")
TABLEAU_PAT_NAME = os.getenv("TABLEAU_PAT_NAME")
TABLEAU_PAT_SECRET = os.getenv("TABLEAU_PAT_SECRET")
TABLEAU_SITE_ID = os.getenv("TABLEAU_SITE_ID", "")

if os.getenv("FAKE_DATA") == "1":
    DB_PATH = os.path.join(os.path.dirname(__file__), "fake_data", "groups.db")
else:
    DB_PATH = os.path.join(os.path.dirname(__file__), "data", "groups.db")
