import os
from dotenv import load_dotenv

load_dotenv()

TABLEAU_SERVER_URL = os.environ["TABLEAU_SERVER_URL"]
TABLEAU_PAT_NAME = os.environ["TABLEAU_PAT_NAME"]
TABLEAU_PAT_SECRET = os.environ["TABLEAU_PAT_SECRET"]
TABLEAU_SITE_ID = os.getenv("TABLEAU_SITE_ID", "")

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "groups.db")
