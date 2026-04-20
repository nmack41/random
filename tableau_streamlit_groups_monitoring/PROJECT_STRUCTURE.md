# Project Structure

## File tree

```
tableau_streamlit_groups_monitoring/
├── .env.example            # Template for Tableau Server credentials
├── .gitignore              # Keeps secrets and generated files out of git
├── requirements.txt        # Python dependencies (4 packages)
├── config.py               # Loads credentials from .env into Python variables
├── db.py                   # SQLite schema, connection factory, all query helpers
├── snapshot.py             # CLI: connects to Tableau Server, captures group memberships
├── diff.py                 # Diff engine: computes additions/removals between snapshots
├── app.py                  # Streamlit entry point: sets up multi-page navigation
├── pages/
│   ├── current_state.py    # Page 1: browse and search group memberships
│   └── changes.py          # Page 2: view membership changes over time
├── data/
│   └── groups.db           # SQLite database (auto-created on first run, gitignored)
├── spec.md                 # Original design document
├── README.md               # Setup and usage guide
└── PROJECT_STRUCTURE.md    # This file
```

---

## Two runtime contexts

This project has two completely separate execution paths that share a single SQLite database. Understanding this split is the key to understanding the whole project.

### CLI context (write path)

```
snapshot.py ──> Tableau Server REST API ──> SQLite
                                              ^
diff.py ──────────────────────────────────────┘
```

`snapshot.py` and `diff.py` are Python scripts you run from the terminal. They write data to SQLite and then exit. They are never executed by Streamlit.

### Streamlit context (read path)

```
app.py ──> pages/current_state.py ──> SQLite (read-only)
       └─> pages/changes.py ──────> SQLite (read-only)
```

`app.py` is a long-running Streamlit server. It reads from SQLite on every user interaction but never writes to it. It never talks to Tableau Server.

**These two contexts never conflict** because the database uses WAL (Write-Ahead Logging) mode, which allows concurrent reads and writes without locking.

---

## File-by-file descriptions

### config.py

**Purpose:** Single source of truth for all configuration values.

**What it does:** Calls `load_dotenv()` at module level to read `.env`, then exposes four Tableau Server settings and the database path as module-level constants.

**Exports used by other files:**
| Constant | Used by | Purpose |
|---|---|---|
| `TABLEAU_SERVER_URL` | `snapshot.py` | Tableau Server connection |
| `TABLEAU_PAT_NAME` | `snapshot.py` | PAT authentication |
| `TABLEAU_PAT_SECRET` | `snapshot.py` | PAT authentication |
| `TABLEAU_SITE_ID` | `snapshot.py` | Target site (blank = default site) |
| `DB_PATH` | `db.py` | Location of the SQLite file |

**Side effect on import:** `load_dotenv()` runs immediately, so `.env` is loaded before any other code reads `os.environ`. This is intentional — any file that imports `config` gets env vars populated as a side effect.

---

### db.py

**Purpose:** Everything SQLite — schema definition, connection management, and every query the app needs.

**What it does:** Defines the database schema as a SQL string, provides a `get_connection()` factory that configures WAL mode and foreign keys, and exposes named functions for every read/write operation.

**Side effect on import:** `init_db()` is called at the bottom of the file (line 123). This means the database schema is guaranteed to exist before any query runs in any file. No other file needs to call `init_db()`.

**Functions and who calls them:**

| Function | What it does | Called by |
|---|---|---|
| `get_connection()` | Creates a SQLite connection with WAL mode, foreign keys, and `Row` factory. Auto-creates the `data/` directory. | `snapshot.py`, `diff.py` (CLI mode), `pages/current_state.py`, `pages/changes.py` |
| `init_db()` | Runs the `CREATE TABLE IF NOT EXISTS` schema. | Called automatically on import (line 123). Never called explicitly. |
| `create_snapshot(conn)` | Inserts a new row into `snapshots` with status "in_progress". Returns the new snapshot ID. | `snapshot.py:take_snapshot()` |
| `complete_snapshot(conn, id, status)` | Updates a snapshot's status to "success" or "failed". | `snapshot.py:take_snapshot()` |
| `insert_members(conn, id, members)` | Bulk-inserts group membership rows via `executemany`. | `snapshot.py:take_snapshot()` |
| `get_latest_snapshot_id(conn)` | Returns the most recent successful snapshot ID, or `None`. | Not currently used (available for future use) |
| `get_previous_snapshot_id(conn, id)` | Returns the successful snapshot immediately before the given ID. | `snapshot.py:take_snapshot()` |
| `get_snapshot_list(conn)` | Returns all successful snapshots, newest first. | `pages/current_state.py`, `pages/changes.py` |
| `get_members_for_snapshot(conn, id)` | Returns all group-user pairs for a snapshot, ordered by group then user. | `pages/current_state.py` |
| `get_changes_between(conn, from_id, to_id)` | Returns all membership changes in a snapshot range. | `pages/changes.py` |

**Database tables:**

```
snapshots                     group_members                   membership_changes
┌─────────────────────┐       ┌─────────────────────────┐     ┌──────────────────────────────┐
│ id (PK)             │◄──────│ snapshot_id (FK)        │     │ id (PK)                      │
│ timestamp           │       │ id (PK)                 │     │ detected_at                  │
│ status              │       │ group_name              │     │ group_name                   │
│   "in_progress"     │       │ group_id                │     │ group_id                     │
│   "success"         │       │ user_name               │     │ user_name                    │
│   "failed"          │       │ user_id                 │     │ user_id                      │
└─────────────────────┘       │ site_role               │     │ change_type ("added"/"removed")│
                              │ domain_name             │     │ previous_snapshot_id (FK)────│──► snapshots
                              └─────────────────────────┘     │ current_snapshot_id (FK)─────│──► snapshots
                                                              └──────────────────────────────┘
```

**Indexes:**
- `group_members(snapshot_id, group_name)` — fast lookup when filtering by group within a snapshot
- `group_members(snapshot_id, user_name)` — fast lookup when filtering by user within a snapshot
- `membership_changes(current_snapshot_id)` — fast lookup for changes detected at a given snapshot
- `membership_changes(group_name)` — fast lookup for all changes to a specific group

---

### snapshot.py

**Purpose:** The data collection script. Connects to Tableau Server, downloads all group memberships, stores them, and triggers a diff.

**When it runs:** Manually via `python3 snapshot.py`.

**What it does, step by step:**

1. Opens a database connection and creates a new snapshot row with status "in_progress"
2. Commits immediately so the snapshot row exists even if the API call fails
3. Authenticates to Tableau Server using a Personal Access Token (PAT)
4. Calls `fetch_all_group_members(server)` which:
   - Uses `TSC.Pager(server.groups)` to iterate all groups (handles pagination automatically)
   - For each group, paginates through users with `RequestOptions(pagesize=100)` and incrementing page numbers
   - Collects every group-user pair into a list of dicts
5. Bulk-inserts all memberships via `db.insert_members()`
6. Marks the snapshot as "success" via `db.complete_snapshot()`
7. Looks up the previous successful snapshot via `db.get_previous_snapshot_id()`
8. If a previous snapshot exists, calls `diff.compute_diff()` to detect changes
9. Closes the connection

**On failure:** If any exception occurs during steps 3-8, the snapshot is marked "failed", the connection is closed, and the process exits with code 1. Because memberships are only inserted on success and committed together, there are no partial snapshots in the database.

**Imports:**
- `config` — for `TABLEAU_SERVER_URL`, `TABLEAU_PAT_NAME`, `TABLEAU_PAT_SECRET`, `TABLEAU_SITE_ID`
- `db` — for `get_connection`, `create_snapshot`, `insert_members`, `complete_snapshot`, `get_previous_snapshot_id`
- `diff` — for `compute_diff`
- `tableauserverclient as TSC` — Tableau REST API library

---

### diff.py

**Purpose:** Computes what changed between two snapshots — who was added to or removed from which groups.

**Algorithm:** Uses SQL `EXCEPT` set operations on the `group_members` table:
- **Added** = rows in snapshot B that don't exist in snapshot A (compared on `group_name`, `group_id`, `user_name`, `user_id`)
- **Removed** = rows in snapshot A that don't exist in snapshot B

The results are written to the `membership_changes` table.

**Two ways to run it:**

1. **As a module** (normal path): `snapshot.py` imports `diff` and calls `compute_diff(conn, prev_id, curr_id)`, passing an existing database connection. The caller is responsible for committing.

2. **As a CLI script** (ad-hoc): `python3 diff.py 1 2` opens its own connection, runs the diff, commits, and closes. Useful for recomputing diffs or comparing non-consecutive snapshots.

**Imports:**
- `db` — for `get_connection` (CLI mode only)
- `sqlite3` — for type annotation
- `datetime` — for `detected_at` timestamps

---

### app.py

**Purpose:** Streamlit entry point. Configures the app and sets up multi-page navigation.

**What it does:**
1. Calls `st.set_page_config()` to set the browser tab title, icon, and wide layout
2. Registers two pages using `st.Page()` with file paths to the page scripts
3. Creates a sidebar navigation via `st.navigation()` and runs the selected page

**This file does not import `db`, `config`, or any project module.** It only references the page files as string paths. Streamlit handles loading and executing them.

---

### pages/current_state.py

**Purpose:** Page 1 of the dashboard — shows who's in which group at a given point in time.

**What it does:**
1. Loads the list of successful snapshots from the database
2. Shows a dropdown defaulting to the most recent snapshot
3. Fetches all group-user pairs for the selected snapshot
4. Displays summary metrics: number of groups, unique users, total memberships
5. Provides a text search bar that filters across group names and user names
6. Renders a full-width dataframe
7. Offers a CSV download button with a timestamped filename

**Imports:** `db` (for `get_connection`, `get_snapshot_list`, `get_members_for_snapshot`), `streamlit`, `pandas`

**Database interaction:** Read-only. Opens a connection, makes two queries (`get_snapshot_list` + `get_members_for_snapshot`), closes the connection. Re-runs on every user interaction (this is how Streamlit works — the entire script re-executes on each widget change).

---

### pages/changes.py

**Purpose:** Page 2 of the dashboard — shows membership additions and removals between two snapshots.

**What it does:**
1. Loads the list of successful snapshots
2. Shows two dropdowns: "From" (defaults to second-most-recent) and "To" (defaults to most recent)
3. Validates that "From" is earlier than "To"
4. Fetches all membership changes in that range
5. Displays summary metrics: additions, removals, groups affected
6. Renders a color-coded dataframe (green rows for additions, red for removals) using Pandas Styler
7. Offers a CSV download button

**Imports:** `db` (for `get_connection`, `get_snapshot_list`, `get_changes_between`), `streamlit`, `pandas`

**Database interaction:** Read-only. Same open-query-close pattern as `current_state.py`.

---

### .env.example

Template showing the four environment variables needed. Copied to `.env` and filled in during setup. `.env` is gitignored.

### requirements.txt

Four packages: `tableauserverclient`, `streamlit`, `pandas`, `python-dotenv`. The `sqlite3` module is built into Python and doesn't need installing.

### .gitignore

Prevents committing: `.env` (secrets), `data/*.db` (generated data), `__pycache__/` and `*.pyc` (bytecode), `.streamlit/secrets.toml` (Streamlit's own secret storage).

---

## Import dependency graph

An arrow means "imports from." Read bottom-to-top to see what depends on what.

```
                    .env
                     │
                     ▼
                  config.py
                 ╱         ╲
                ▼           ▼
             db.py ◄── snapshot.py
            ╱  |  ╲         │
           ▼   ▼   ▼        ▼
   current   changes   diff.py
  _state.py   .py
       ╲      │
        ▼     ▼
        app.py (registers pages by path, does not import them)
```

**Key detail:** `app.py` references the page files as strings (`"pages/current_state.py"`), not as Python imports. Streamlit executes them dynamically. So `app.py` has no direct Python dependency on the page modules or on `db`.

---

## Data flow through the system

### Write path (snapshot.py, runs manually)

```
                         Tableau Server
                              │
                    ┌─────────┴─────────┐
                    │   REST API call    │
                    │  (all groups and   │
                    │   their members)   │
                    └─────────┬─────────┘
                              │
                              ▼
                       snapshot.py
                    fetch_all_group_members()
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
     db.create_snapshot  db.insert_members  db.complete_snapshot
     (status:in_progress) (bulk write)      (status:success)
              │               │               │
              └───────┬───────┘               │
                      ▼                       │
               [snapshots table]              │
               [group_members table]          │
                      │                       │
                      ▼                       │
              db.get_previous_snapshot_id     │
                      │                       │
                      ▼                       │
               diff.compute_diff             │
                      │                       │
                      ▼                       │
            [membership_changes table]        │
```

### Read path (Streamlit, runs continuously)

```
     User browser
          │
          ▼
       app.py
     st.navigation()
       ╱        ╲
      ▼          ▼
 current_       changes.py
 state.py          │
    │              ├── db.get_snapshot_list()     ──► [snapshots]
    │              └── db.get_changes_between()   ──► [membership_changes]
    │
    ├── db.get_snapshot_list()                    ──► [snapshots]
    └── db.get_members_for_snapshot()             ──► [group_members]
```

---

## Database table lifecycle

Each table is written by exactly one code path and read by specific consumers:

| Table | Written by | Read by |
|---|---|---|
| `snapshots` | `snapshot.py` via `db.create_snapshot()` and `db.complete_snapshot()` | `pages/current_state.py` and `pages/changes.py` via `db.get_snapshot_list()`. Also `snapshot.py` via `db.get_previous_snapshot_id()`. |
| `group_members` | `snapshot.py` via `db.insert_members()` | `pages/current_state.py` via `db.get_members_for_snapshot()`. Also `diff.py` via direct SQL queries. |
| `membership_changes` | `diff.py` via direct SQL inserts in `compute_diff()` | `pages/changes.py` via `db.get_changes_between()` |

---

## Subtle behaviors worth knowing

**`db.py` auto-initializes the schema on import.** Line 123 calls `init_db()`. This means the first time *any* file imports `db`, the `data/` directory and all tables are created if they don't exist. You never need to run a "create database" command.

**`snapshot.py` commits the snapshot row before the API call.** The snapshot is created with status "in_progress" and committed (line 49) before Tableau Server is contacted. If the API call fails, `complete_snapshot("failed")` updates that row. If the process crashes hard (kill -9, power loss), you'll have an "in_progress" row that never completes — but the dashboard only shows "success" snapshots, so this is harmless.

**`diff.py` doesn't commit.** `compute_diff()` writes to `membership_changes` but does not call `conn.commit()`. The caller (`snapshot.py` or the CLI `__main__` block) is responsible for committing. This is intentional — it lets `snapshot.py` treat the membership insert + diff as a single logical transaction.

**Streamlit re-executes page scripts on every interaction.** Every time a user changes a dropdown or types in the search box, the entire page script runs top-to-bottom again. This means a new database connection is opened and closed on each interaction. This is fine for SQLite and is the standard Streamlit execution model.

**`get_changes_between` uses `>=` and `<=` on snapshot IDs.** This means it returns changes across multiple consecutive diffs, not just a single pair. If you select snapshot #1 to #5, you get all changes detected between snapshots 1-2, 2-3, 3-4, and 4-5.
