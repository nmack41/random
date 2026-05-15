# Plan: Migrate from local SQLite to Databricks

Move the snapshot store from `data/groups.db` (local SQLite) to Delta tables in a Unity Catalog–enabled Databricks workspace. Convert `snapshot.py` into a scheduled Databricks Job, redeploy the Streamlit UI as a Databricks App, and decommission the local DB.

## Motivation

Three drivers, in priority order:

1. **Standard org data platform** — group memberships should live where the rest of the data lives, with Unity Catalog governance and lineage.
2. **Join with other org data** — enable joins to HR, Active Directory, and asset inventories that already live in Databricks.
3. **Multi-user shared access** — replace "one SQLite file per analyst" with governed shared tables.

Performance is **not** a driver. The current dataset is small enough that SQLite is comfortable; the migration optimizes for governance and integration, not throughput. The schema stays nearly identical.

## Scope

**In scope:**
- Provision UC catalog/schema and Delta tables.
- Backfill all historical snapshots from `data/groups.db` into Delta.
- Rewrite the write path (`snapshot.py`, `diff.py`, `user_diff.py`, `db.py`) to target Delta via a Databricks Job.
- Rewrite the read path (`pages/*`) to query Delta via `databricks-sql-connector` from a Databricks App.
- Replace `.env` / `config.py` with Databricks Secrets.
- Decommission `data/groups.db`.

**Explicitly NOT in scope:**
- Schema redesign or a bronze/silver/gold layering pass. The current schema ports almost verbatim; refactoring into medallion layers is a follow-up if it's ever needed.
- Streaming / near-real-time ingestion. Snapshots stay batch.
- Joining to other tables (HR, AD). That becomes *possible* after this migration but is a separate piece of work.
- Tableau-side changes (PAT scope, REST API usage, etc.).

## Decisions locked in

| Decision | Choice | Notes |
|---|---|---|
| Where the dashboard runs | **Databricks Apps** | Streamlit hosted inside Databricks; SSO via workspace auth; uses an SQL warehouse. |
| Where the snapshot job runs | **Databricks Job** | Scheduled Job replaces manual `python3 snapshot.py`. |
| Workspace flavour | **Unity Catalog** | Three-level namespacing, governed access, Delta with optional identity columns. |
| Catalog / schema | `tableau_monitoring.groups` (proposed) | Confirm naming convention with the data platform team in Phase 0. |
| Surrogate `id` columns | **Dropped** | Nothing joins on them; removal simplifies inserts and matches Delta idioms. |
| `snapshot_id` generation | **Application-side BIGINT = Unix epoch seconds at snapshot start** | Monotonic, sortable, knowable upfront so children can carry it without a RETURNING round-trip. |
| Compute for the Job | **Serverless compute for jobs** | Cold-start friendly; no idle cost. |
| Compute for the App | **Serverless SQL warehouse**, `auto_stop_mins=10` | Warm during business hours, dark otherwise. |
| Service principals | **Two** — `tableau-monitoring-job-sp` (write) and `tableau-monitoring-app-sp` (read-only) | Least privilege. |
| Tableau PAT storage | **Databricks Secret Scope** `tableau-monitoring` with keys `pat-name`, `pat-secret`, `server-url`, `site-id` | Replaces `.env` + `config.py`. |
| FK enforcement | **Informational only** | Delta doesn't enforce FKs. Single-writer (`snapshot.py`) makes this safe. |
| Uniqueness constraints | **Idempotency in the writer** | A Job re-run writes a new `snapshot_id`, never overwrites an existing one. |

## Feasibility gates — validate BEFORE writing migration code

Three gates. All three must pass before Phase 1 starts. Each is cheap and catches a class of "discover at hour 8" failure.

### Gate 1 — Workspace + UC access

The chosen SPs need the right grants. A common stumble: the SP exists but lacks `USE CATALOG` or `USE SCHEMA`, making every operation 403.

**Probe (run from a Databricks notebook authenticated as `tableau-monitoring-job-sp`):**

```python
spark.sql("USE CATALOG tableau_monitoring")
spark.sql("USE SCHEMA groups")
spark.sql("CREATE TABLE IF NOT EXISTS _gate1_probe (x INT) USING DELTA")
spark.sql("INSERT INTO _gate1_probe VALUES (1)")
print(spark.sql("SELECT COUNT(*) FROM _gate1_probe").collect())
spark.sql("DROP TABLE _gate1_probe")
```

**Pass condition:** all five statements succeed.

**If it fails:** the SP needs `USE CATALOG` + `USE SCHEMA` + `CREATE TABLE` + `MODIFY` on the target schema. Loop in the workspace admin before continuing.

Repeat for `tableau-monitoring-app-sp` with `SELECT` (and only `SELECT`).

### Gate 2 — Tableau Server reachable from Databricks compute

The Job needs to talk to Tableau Server. If Tableau is on-prem behind a firewall, the Databricks workspace may not have egress.

**Probe (Databricks notebook on the Job cluster / serverless):**

```python
import urllib.request
import config_secrets  # thin wrapper around dbutils.secrets.get
url = config_secrets.TABLEAU_SERVER_URL
with urllib.request.urlopen(f"{url}/api/3.22/serverinfo", timeout=10) as r:
    print(r.status, r.read()[:200])
```

**Pass condition:** HTTP 200 and a `<tsResponse>` XML body.

**If it fails:** options are (a) IP-allowlist the workspace NAT gateway on the Tableau side, (b) put the Job on a customer-managed VPC with a route to Tableau, or (c) keep `snapshot.py` running on its current host and have it write to Databricks remotely via `databricks-sql-connector`. Option (c) is a smaller change but means you keep a host to babysit — which is part of what we're trying to eliminate.

### Gate 3 — `EXCEPT` semantics match SQLite

The diff engine (`diff.py`) and user-diff engine (`user_diff.py`) rely on `EXCEPT` set semantics, which include matching `NULL` to `NULL` (unlike `=`). Both SQLite and Spark SQL should behave this way, but the migration is meaningless if the diff output differs.

**Probe (in a notebook, run on the dev schema after Phase 2 backfill):**

```python
# Pick any two consecutive backfilled snapshots
prev_id, curr_id = 1717000000, 1717086400  # example epoch seconds
spark.sql(f"""
  SELECT group_name, group_id, user_name, user_id
  FROM tableau_monitoring.groups.group_members WHERE snapshot_id = {curr_id}
  EXCEPT
  SELECT group_name, group_id, user_name, user_id
  FROM tableau_monitoring.groups.group_members WHERE snapshot_id = {prev_id}
""").write.mode("overwrite").saveAsTable("tableau_monitoring.groups._gate3_added")

# Compare row-for-row against the same snapshot pair's membership_changes
# from the SQLite source (joined on (group_id, user_id, change_type='added')).
```

**Pass condition:** zero symmetric difference between the Spark `EXCEPT` result and the SQLite `membership_changes(change_type='added')` for that snapshot pair.

**If it fails:** debug before rewriting `diff.py`. Likely culprit: NULL-handling on `domain_name` or `site_role` columns where one side stores `''` and the other `NULL`.

## Target architecture

```
                                       ┌─────────────────────────────────┐
                                       │ Databricks Workspace (UC enabled)│
   Tableau Server REST API             │                                  │
            ▲                          │  Catalog:  tableau_monitoring    │
            │ TSC + PAT (from Secrets) │  Schema:   groups                │
   ┌────────┴──────────┐               │                                  │
   │ Databricks Job    │ ─── append ─► │  Delta tables (8):               │
   │  scheduled daily  │               │   snapshots                      │
   │  serverless       │               │   group_members                  │
   │  job-sp           │               │   membership_changes             │
   └───────────────────┘               │   workbooks                      │
                                       │   workbook_group_access          │
   ┌───────────────────┐               │   views                          │
   │ Databricks App    │ ─── SELECT ─► │   view_group_access              │
   │  Streamlit        │               │   users                          │
   │  app-sp           │               │   user_changes                   │
   │  serverless WH    │               │                                  │
   └───────────────────┘               └─────────────────────────────────┘
                                                  ▲
                              Other org tables (HR, AD, asset inventory)
                              live in sibling catalogs/schemas — joinable
                              by user_id / email / domain_name.
```

The two-context split documented in [PROJECT_STRUCTURE.md](../../PROJECT_STRUCTURE.md#two-runtime-contexts) is preserved: one writer (the Job), many readers (the App + ad-hoc analyst queries). Delta optimistic concurrency replaces SQLite's WAL.

## Schema translation

The Delta schema mirrors the SQLite schema 1:1 except for the per-table changes below.

### Per-table changes

| Table | SQLite source | Delta target change |
|---|---|---|
| All | `id INTEGER PRIMARY KEY` autoincrement | **Drop the `id` column entirely.** Nothing joins on it. `COUNT(gm.id)` in [db.py](../../db.py) becomes `COUNT(*)`. |
| `snapshots.id` | autoincrement | **Application-generated `snapshot_id BIGINT`** = `int(time.time())` at the start of `take_snapshot()`. Same column name. |
| `*_id` columns | TEXT | STRING (no change in semantics) |
| `timestamp`, `detected_at`, `last_login` | DATETIME stored as ISO string | TIMESTAMP (native) — enables `date_trunc`, `BETWEEN`, etc. without parsing. |
| FK declarations | Enforced via `PRAGMA foreign_keys=ON` | `FOREIGN KEY ... NOT ENFORCED` — informational, used by Catalog Explorer and lineage. |
| Indexes | B-tree per column | **`OPTIMIZE ... ZORDER BY (snapshot_id)`** weekly on the wide tables (`group_members`, `users`, the access tables). `snapshot_id` is the universal filter; Z-ORDER + file pruning replaces B-tree indexes. |
| `UNIQUE (snapshot_id, ...)` | Enforced | Informational only. Writer never re-uses a `snapshot_id`. |
| `schema_version` table | Used in `init_db()` | **Drop.** Delta has its own schema evolution (`ALTER TABLE ADD COLUMN`, `mergeSchema`); the bespoke versioning is no longer needed. |

### Resulting DDL sketch

Full DDL lives in `notebooks/01_create_tables.py` (Phase 1 deliverable). Sketch:

```sql
CREATE TABLE tableau_monitoring.groups.snapshots (
  snapshot_id BIGINT NOT NULL,
  timestamp   TIMESTAMP NOT NULL,
  status      STRING NOT NULL,
  CONSTRAINT pk_snapshots PRIMARY KEY (snapshot_id) RELY
) USING DELTA;

CREATE TABLE tableau_monitoring.groups.group_members (
  snapshot_id BIGINT NOT NULL,
  group_name  STRING NOT NULL,
  group_id    STRING NOT NULL,
  user_name   STRING NOT NULL,
  user_id     STRING NOT NULL,
  site_role   STRING NOT NULL,
  domain_name STRING NOT NULL DEFAULT '',
  CONSTRAINT fk_gm_snapshot FOREIGN KEY (snapshot_id)
    REFERENCES tableau_monitoring.groups.snapshots(snapshot_id) NOT ENFORCED RELY
) USING DELTA;
```

…and similarly for the other six tables. Each adds the `snapshot_id`-rooted FK; the access tables additionally carry FKs to `workbooks` / `views` (informational only, but useful for the optimizer and lineage).

## Query translation gotchas

Most of [db.py](../../db.py) ports cleanly. The non-trivial translations:

| SQLite | Delta / Databricks SQL | Affected functions |
|---|---|---|
| `GROUP_CONCAT(DISTINCT x)` | `array_join(collect_set(x), ',')` | `get_users_for_snapshot`, `_users_with_access_query`, `get_all_view_grants_for_snapshot`, `get_all_workbook_grants_for_snapshot` |
| `COUNT(gm.id)` (counting via dropped PK) | `COUNT(*)` | `get_users_for_snapshot`, the access-count queries |
| `EXCEPT` in `diff.py` / `user_diff.py` | Same keyword, works on Delta. **NULL-equality semantics preserved.** See Gate 3. | `compute_diff`, `compute_user_diff` |
| `COALESCE(..., '')` | Same. Works. | `_users_with_access_query`, the `<unresolved:...>` patterns |
| ISO-string date comparisons | Use TIMESTAMP comparisons. `last_login < current_timestamp() - INTERVAL 90 DAYS` instead of string `<`. | `formatting.humanize_last_login` (read path only — no change needed) |
| `sqlite3.Row` factory | `cursor.fetchall()` from `databricks-sql-connector` returns named tuples (`Row`-like). Or `.fetchall_arrow().to_pandas()` for direct DataFrame. | All read-path call sites |

## Phased migration

### Phase 0 — Lock in decisions (≈1 hour)

Output: a one-page decision log committed to the repo, all gates green.

Tasks:
1. Confirm catalog/schema naming with the data platform team.
2. Request creation of two service principals (or create yourself if you have permissions).
3. Decide schedule cadence (daily 6am UTC? weekly?).
4. Decide backfill scope. **Recommendation: backfill all historical snapshots from `data/groups.db`** — preserves the diff history that the Changes page depends on.
5. Run all three gates. Block on failures.

### Phase 1 — Provision Databricks objects (≈2 hours)

Output: empty Delta tables exist in UC, secrets loaded, SPs granted.

Tasks:
1. Create catalog + schema (or request via platform team).
2. Create the secret scope `tableau-monitoring` and load `pat-name`, `pat-secret`, `server-url`, `site-id`.
3. Write `notebooks/01_create_tables.py` — a single notebook that creates all eight Delta tables with FKs. Idempotent (`CREATE TABLE IF NOT EXISTS`).
4. Grant `SELECT, MODIFY` to job-sp; `SELECT` only to app-sp.
5. Run `OPTIMIZE` once on each table (no-op but verifies the SP can do it — `OPTIMIZE` will be scheduled later).

### Phase 2 — Backfill historical snapshots (≈half day)

Output: every successful snapshot in `data/groups.db` is present in Delta with identical row counts.

The backfill is a throwaway script archived after one run.

Approach:

1. Open `data/groups.db` with `sqlite3`.
2. For each of the eight tables, `pandas.read_sql_query("SELECT * FROM <table>", conn)`.
3. **Remap `snapshot_id`**: SQLite `snapshots.id` runs `1, 2, 3, …`. Translate to epoch seconds using each row's `snapshots.timestamp`:
   ```python
   id_map = {row.id: int(pd.to_datetime(row.timestamp).timestamp()) for row in snapshots_df.itertuples()}
   ```
   Then `.map(id_map)` over `snapshot_id` in every child table (and `previous_snapshot_id` / `current_snapshot_id` in the change tables).
4. Drop the per-row `id` column.
5. Parse ISO-string dates into proper datetimes (pandas handles this for the timestamp columns).
6. Upload as Parquet to a UC volume (e.g., `/Volumes/tableau_monitoring/groups/_backfill/`).
7. `COPY INTO tableau_monitoring.groups.<table> FROM '/Volumes/.../`<table>.parquet' FILEFORMAT = PARQUET` for each table.

**Validation gate before declaring Phase 2 done:**

```sql
-- Row counts must match SQLite source
SELECT 'snapshots'        AS t, COUNT(*) FROM tableau_monitoring.groups.snapshots
UNION ALL SELECT 'group_members',       COUNT(*) FROM tableau_monitoring.groups.group_members
UNION ALL SELECT 'membership_changes',  COUNT(*) FROM tableau_monitoring.groups.membership_changes
UNION ALL SELECT 'workbooks',           COUNT(*) FROM tableau_monitoring.groups.workbooks
UNION ALL SELECT 'workbook_group_access', COUNT(*) FROM tableau_monitoring.groups.workbook_group_access
UNION ALL SELECT 'views',               COUNT(*) FROM tableau_monitoring.groups.views
UNION ALL SELECT 'view_group_access',   COUNT(*) FROM tableau_monitoring.groups.view_group_access
UNION ALL SELECT 'users',               COUNT(*) FROM tableau_monitoring.groups.users
UNION ALL SELECT 'user_changes',        COUNT(*) FROM tableau_monitoring.groups.user_changes;
```

Compare with `SELECT COUNT(*) FROM <table>` in the source SQLite. Counts must be exact.

### Phase 3 — Rewrite the write path (≈1 day)

Output: a Databricks Job runs `snapshot.py`-equivalent code and writes one new snapshot end-to-end matching SQLite output for the same Tableau state.

**File-level changes:**

1. **Delete `config.py`.** Replace with a thin `config_secrets.py` that reads from `dbutils.secrets`:
   ```python
   from databricks.sdk.runtime import dbutils  # available in notebook + Job contexts
   TABLEAU_SERVER_URL = dbutils.secrets.get("tableau-monitoring", "server-url")
   TABLEAU_PAT_NAME   = dbutils.secrets.get("tableau-monitoring", "pat-name")
   TABLEAU_PAT_SECRET = dbutils.secrets.get("tableau-monitoring", "pat-secret")
   TABLEAU_SITE_ID    = dbutils.secrets.get("tableau-monitoring", "site-id")
   ```
2. **Rename `db.py` → `delta_io.py`.** Same public function names. Implementation switches to Spark DataFrames:
   ```python
   def insert_members(spark, snapshot_id, members):
       df = spark.createDataFrame(
           [{**m, "snapshot_id": snapshot_id} for m in members]
       )
       df.write.format("delta").mode("append").saveAsTable(
           "tableau_monitoring.groups.group_members"
       )
   ```
   `create_snapshot` / `complete_snapshot` use `spark.sql("INSERT INTO ...")` / `spark.sql("UPDATE ... SET status = ...")`.
3. **Modify `snapshot.py`**: top-level structure unchanged. Replace `db.get_connection()` with `spark` (passed in or `SparkSession.builder.getOrCreate()`). Generate `snapshot_id = int(time.time())` once at the top of `take_snapshot()` and pass through. Remove `conn.commit()` calls (Delta auto-commits per write).
4. **Rewrite `diff.py` and `user_diff.py`** SQL to target Delta. The `EXCEPT` logic is identical; only the table names and column references change. Run via `spark.sql(...).write.mode("append").saveAsTable("...membership_changes")`.

**Job configuration:**
- Notebook entry point: `notebooks/02_run_snapshot.py` (just imports and calls `take_snapshot()`).
- Compute: serverless for jobs.
- Schedule: per Phase 0 decision.
- Retries: 1 retry on infrastructure failure; 0 retries on application failure (Tableau auth error shouldn't keep hammering).
- Notification: email or Slack on failure. (No alert on success — noise.)

**Test gate before declaring Phase 3 done:**

Run the new Job once into a `tableau_monitoring.groups_dev` schema (or use a different snapshot_id range). Compare:
- Row counts in `group_members`, `users`, `workbook_group_access`, `view_group_access` against the latest SQLite snapshot of the same Tableau state. Must match exactly.
- The `membership_changes` rows produced by the diff against the same diff run in SQLite. See Gate 3.

### Phase 4 — Rewrite the read path (≈half day)

Output: Streamlit pages read from Delta. Page logic unchanged.

**File-level changes:**

1. **Add `dbsql.py`** — a thin wrapper around `databricks.sql.connect()`:
   ```python
   from databricks import sql
   import os

   def get_connection():
       return sql.connect(
           server_hostname=os.environ["DATABRICKS_HOST"],
           http_path=os.environ["DATABRICKS_HTTP_PATH"],
           credentials_provider=...,  # supplied by Databricks Apps runtime
       )
   ```
2. **Rewrite `db.py` read functions** to use `dbsql.get_connection()` instead of `sqlite3.connect()`. Apply the query translations from the "Query translation gotchas" section. Function signatures stay the same so the pages don't change.
3. **Add `@st.cache_data(ttl=300)`** on `get_snapshot_list` and the per-snapshot fetchers. SQLite was fast enough not to need caching; DBSQL has more latency.
4. **Page files (`pages/*.py`)** should need near-zero changes — they consume opaque pandas DataFrames.

### Phase 5 — Deploy as a Databricks App (≈half day)

Output: Streamlit running at `https://<workspace>.databricksapps.com/tableau-monitoring`, SSO-gated.

Tasks:
1. Add `databricks.yml` (Asset Bundle) defining the App and the Job in one place.
2. Configure the App's runtime to use `tableau-monitoring-app-sp` and the SQL warehouse from Phase 1.
3. Set required env vars (`DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`) in the App config.
4. `databricks bundle deploy`.
5. Smoke test: hit each of the five pages, verify data renders, verify CSV download works, verify search filters work.

### Phase 6 — Decommission SQLite (≈1 hour)

Order matters here — wait until the Databricks pipeline has run successfully for at least a week before touching the SQLite copy.

Tasks:
1. Final SQLite snapshot taken and verified mirrored to Delta.
2. Archive `data/groups.db` to a UC volume or local archive (don't delete — it's audit trail until you're 100% sure).
3. Delete `config.py`, `db.py` (or whatever survives), and `.env.example`.
4. Update `requirements.txt`: add `databricks-sql-connector`, `databricks-sdk`; remove `python-dotenv`.
5. Update [README.md](../../README.md) and [PROJECT_STRUCTURE.md](../../PROJECT_STRUCTURE.md) to reflect the new topology.
6. Update this doc with a "shipped on YYYY-MM-DD" note and move to an `archive/` subfolder.

## File-level changes summary

| File | Fate |
|---|---|
| [config.py](../../config.py) | Delete. Replaced by `config_secrets.py` reading from Databricks Secrets. |
| [db.py](../../db.py) | Split into `delta_io.py` (write path, Spark) and updated read functions in a renamed module. |
| [snapshot.py](../../snapshot.py) | Rewrite. Top-level shape preserved; `db.*` → `delta_io.*`; auth via secrets; `snapshot_id` generated up front. |
| [diff.py](../../diff.py) | Rewrite SQL. Logic identical. |
| [user_diff.py](../../user_diff.py) | Rewrite SQL. Logic identical. |
| [formatting.py](../../formatting.py) | **Unchanged.** Pure Python. |
| [app.py](../../app.py) | Unchanged (registers pages by path). |
| `pages/*.py` | Minimal changes — swap `db.get_connection()` for `dbsql.get_connection()`. pandas + Streamlit code stays. |
| [requirements.txt](../../requirements.txt) | Add `databricks-sql-connector`, `databricks-sdk`. Remove `python-dotenv`. |
| New: `notebooks/01_create_tables.py` | Phase 1 DDL. Idempotent. |
| New: `notebooks/02_run_snapshot.py` | Phase 3 Job entry point. |
| New: `notebooks/backfill.py` | Phase 2 one-time backfill. Archived after use. |
| New: `databricks.yml` | Asset Bundle defining the App + Job. |
| [data/groups.db](../../data/groups.db) | Archived after Phase 6, then removed from the repo's working tree. |

## Risks and call-outs

1. **`snapshot_id` re-keying breaks any saved URLs.** Existing SQLite IDs (1, 2, 3, …) become epoch seconds. If anyone bookmarks `?snapshot_id=42`, those break. Check if external bookmarks exist before Phase 2.

2. **Timezone of `last_login`.** TSC returns datetimes (tz-aware in newer versions). SQLite stores as ISO strings via `.isoformat()` (preserves offset). Delta TIMESTAMP stores as UTC. Verify during backfill that the values round-trip correctly — write a probe that compares the same `(snapshot_id, user_id)` pair's `last_login` between SQLite and Delta.

3. **Diff is best-effort.** Currently `snapshot.py` keeps a snapshot at `success` even if diff computation fails (per the spec in `snapshot.py`). Same posture in Databricks, but the Job's alerting needs to distinguish "snapshot failed" (page someone) from "diff failed" (warn-level only). Separate `try` blocks; different failure annotations.

4. **Cost.** A serverless SQL warehouse running 24/7 is real money. With `auto_stop_mins=10` and the App used during business hours, expect ~$5–20/month for this workload. Confirm with whoever pays the Databricks bill before Phase 5.

5. **Schema evolution story changes.** The `schema_version` table (currently `CURRENT_SCHEMA_VERSION = 3` in [db.py:13](../../db.py#L13)) goes away. Delta's `ALTER TABLE ADD COLUMN` handles additive changes; breaking changes need an `OVERWRITE` with `mergeSchema`. Document this in the new README so future maintainers know the playbook.

6. **No more "first import creates the DB" magic.** [db.py:541](../../db.py#L541) calls `init_db()` at module load time. After migration, tables are created once via `notebooks/01_create_tables.py` and are otherwise managed in Databricks. The implicit "just `import db` and the schema exists" convenience disappears. Worth a note in PROJECT_STRUCTURE.md.

7. **Streamlit re-execution + cold warehouse = first-click pain.** Streamlit re-runs the whole page on every interaction. If the SQL warehouse is cold-starting, the first interaction after idle takes ~10–30s. Mitigations: serverless warehouse cold starts are fast (~5s); add `@st.cache_data(ttl=300)`; surface a `st.spinner("Querying...")` so the user knows what's happening.

8. **Single-writer assumption.** The whole plan assumes only the Job writes. If someone hand-runs an ad-hoc backfill from a notebook while the Job is mid-run, Delta's optimistic concurrency will retry one of them, but you can get duplicate `snapshot_id` values if both writers picked the same epoch second. Mitigation: the Job runs once a day; manual backfills should use a clearly-different `snapshot_id` (e.g., `int(time.time()) * -1` for the historical backfill).

## Open questions

- Cadence: daily 6am UTC, or some other time / frequency?
- Schedule cutover order: rewrite write path first (Phase 3 before 4) or read path first? **Recommendation: write path first** — gives one day's lead time to confirm new snapshots look right before any user-facing change.
- Do we want `OPTIMIZE` + `VACUUM` automated as a separate scheduled Job, or piggybacked onto the snapshot Job once a week? Piggybacking is simpler; separating is cleaner.
- Should the Backfill in Phase 2 also be replayed periodically as a "regression test" (re-run against `data/groups.db.archive`, compare to live Delta)? Probably over-engineering for v1.
