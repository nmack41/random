# Tableau Groups Monitoring Dashboard

A Streamlit app that snapshots Tableau Server group memberships, detects changes over time, and lets you answer compliance questions like "who's in group X?" with a CSV export in under 30 seconds.

## Why this exists

When leadership or compliance asks "who has access to what on Tableau Server?", there's no built-in way to produce that answer quickly. The Tableau admin UI lets you click through groups one-by-one, but that's too slow and non-exportable. This tool fills that gap.

## How it works

There are two separate pieces that run independently:

1. **`snapshot.py`** — A CLI script you run manually. It connects to Tableau Server via the REST API, pulls every group and its members, and stores them in a local SQLite database. It also computes a diff against the previous snapshot so you can see what changed.

2. **`app.py`** — A Streamlit web app that reads from that same SQLite database and gives you a searchable, filterable, exportable view of the data. It never talks to Tableau Server directly.

```
Tableau Server ──(REST API)──> snapshot.py ──> SQLite DB <── Streamlit app
                                 (manual)                      (on demand)
```

## Prerequisites

- Python 3.9 or newer
- Network access from your machine to Tableau Server
- A Personal Access Token (PAT) from Tableau Server with admin permissions
  - Create one in Tableau Server: My Account Settings > Personal Access Tokens

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

This installs four packages:
- `tableauserverclient` — official Tableau REST API library
- `streamlit` — the web UI framework
- `pandas` — data tables and CSV export
- `python-dotenv` — loads your `.env` file

### 2. Configure credentials

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Then edit `.env`:

```
TABLEAU_SERVER_URL=https://tableau.yourcompany.com
TABLEAU_PAT_NAME=my-pat-name
TABLEAU_PAT_SECRET=my-pat-secret
TABLEAU_SITE_ID=
```

| Variable | Required | Notes |
|---|---|---|
| `TABLEAU_SERVER_URL` | Yes | Full URL to your Tableau Server |
| `TABLEAU_PAT_NAME` | Yes | Name of your Personal Access Token |
| `TABLEAU_PAT_SECRET` | Yes | Secret value of the PAT |
| `TABLEAU_SITE_ID` | No | Leave blank for the Default site. For other sites, use the site's "content URL" (the slug in the Tableau Server URL, e.g. `mysite`). |

**Never commit `.env` to git** — it's already in `.gitignore`.

### 3. Take your first snapshot

```bash
python3 snapshot.py
```

You should see output like:

```
Found 12 groups
Captured 247 total memberships
First snapshot — no previous data to diff against
Snapshot #1 complete
```

If this fails with a connection or auth error, double-check your `.env` values and that your PAT hasn't expired.

### 4. Launch the dashboard

```bash
streamlit run app.py
```

This opens a browser to `http://localhost:8501`. You'll see the **Current State** page with your group membership data.

## Daily usage

### Taking snapshots

Run `python3 snapshot.py` whenever you want to capture the current state. Each snapshot is compared against the previous one, so the **Changes** page will start showing additions and removals after your second snapshot.

### Using the dashboard

**Current State page** — Shows who's in which group right now (or at any past snapshot). Use the search bar to filter by group name or user name. Click "Export CSV" to download.

**Changes page** — Shows membership additions and removals between any two snapshots. Green rows = users added to groups, red rows = users removed. Exportable to CSV.

## Project structure

```
├── app.py                  # Streamlit entry point — run with `streamlit run app.py`
├── snapshot.py             # CLI script — captures group memberships from Tableau Server
├── diff.py                 # Diff engine — computes changes between two snapshots
├── db.py                   # SQLite schema, connection, and query helpers
├── config.py               # Reads credentials from .env
├── pages/
│   ├── current_state.py    # Page 1: browse group memberships
│   └── changes.py          # Page 2: view membership changes over time
├── data/
│   └── groups.db           # SQLite database (auto-created, gitignored)
├── requirements.txt
├── .env.example            # Template for credentials
├── .gitignore
└── spec.md                 # Original design document
```

## How the code fits together

### Data flow

1. **`config.py`** loads credentials from `.env` into Python variables.
2. **`db.py`** defines the SQLite schema (3 tables) and auto-creates the database on first import. Every other module imports `db` to read/write data.
3. **`snapshot.py`** authenticates to Tableau Server, iterates all groups with `TSC.Pager()` (handles pagination), iterates all users within each group, and bulk-inserts everything into `group_members`. If the API call fails, the snapshot is marked "failed" and no partial data is saved.
4. **`diff.py`** compares two snapshots using SQL `EXCEPT` queries to find who was added or removed. It writes results to `membership_changes`. It's called automatically by `snapshot.py` but can also be run standalone: `python3 diff.py 1 2`.
5. **`app.py`** sets up the Streamlit multi-page app. Each page in `pages/` reads from SQLite independently.

### Database tables

| Table | Purpose |
|---|---|
| `snapshots` | One row per snapshot run, with a timestamp and success/failure status |
| `group_members` | Every group-user pair at a given snapshot. This is the main data table. |
| `membership_changes` | Computed diffs: who was added/removed between consecutive snapshots |

### Key design decisions

- **SQLite, not Postgres** — Zero infrastructure. The database file lives in `data/groups.db` and is created automatically. At ~1,000 rows per snapshot, SQLite handles years of data comfortably.
- **Snapshots are immutable** — Once written, snapshot data is never modified. This makes diffs reliable and gives you a full audit trail.
- **The Streamlit app never writes data** — It only reads from SQLite. All writes happen in `snapshot.py`. This separation means the dashboard can't accidentally corrupt your data.
- **WAL mode** — The database uses Write-Ahead Logging so `snapshot.py` can write while Streamlit reads without locking conflicts.

## Troubleshooting

| Problem | Fix |
|---|---|
| `python3: command not found` | Install Python 3.9+ from python.org or your package manager |
| `KeyError: 'TABLEAU_SERVER_URL'` | Your `.env` file is missing or the variable isn't set. Check that `.env` exists and has all required values. |
| `snapshot.py` fails with 401 | Your PAT expired. Generate a new one in Tableau Server and update `.env`. |
| `snapshot.py` fails with connection error | Check that `TABLEAU_SERVER_URL` is correct and your machine can reach Tableau Server on the network. |
| Dashboard shows "No snapshots found" | Run `python3 snapshot.py` first to capture data. |
| Changes page says "Need at least two snapshots" | You need two successful snapshots to compare. Run `snapshot.py` again after some time or membership changes. |

## Running an ad-hoc diff

If you need to compare two specific snapshots that aren't consecutive:

```bash
python3 diff.py <from_snapshot_id> <to_snapshot_id>
```

Snapshot IDs are visible in the dashboard dropdowns (e.g. `#1`, `#2`).
