# Spec: Entity-Relationship Diagram for `groups.db`

**Date:** 2026-05-07
**Status:** Draft — awaiting implementation plan

## Goal

Produce a single human-readable Entity-Relationship Diagram (ERD) of the SQLite schema defined in [db.py](../../../db.py), so future contributors (and future Nick) can understand the data model without reading the `SCHEMA` constant line by line.

## Non-Goals

- Not a generator. The ERD is hand-maintained markdown; it does not introspect the live database or `db.py`.
- Not a migration tool. The ERD reflects the current schema (`CURRENT_SCHEMA_VERSION = 2`); previous schema versions are not depicted.
- Not a query reference. The ERD shows tables and relationships, not example queries.
- No rendered PNG/SVG artifact committed alongside. GitHub renders Mermaid inline; a binary image would drift from the markdown source.

## Artifact

A single new file: `docs/erd.md`.

## Document Structure

The file has three sections, in this order.

### 1. Prose preamble (~150 words)

Explains the schema's distinctive shape so the diagram is interpretable:

- **Snapshot-anchored design.** Every domain row carries a `snapshot_id` foreign key to `snapshots`. The same logical entity (e.g., a workbook) appears once per snapshot — rows are append-only, never updated in place. Change detection compares two snapshots rather than mutating state.
- **Soft entities.** "Groups" and "users" are *not* first-class tables. Their identifiers (`group_id`, `group_name`, `user_id`, `user_name`, `site_role`, `domain_name`) are denormalized into `group_members`, `membership_changes`, `workbook_group_access`, and `view_group_access`. There is no `groups` or `users` table to FK against.
- **Logical vs declared FKs.** SQLite-declared foreign keys (`REFERENCES`) only point to `snapshots`. Cross-domain relationships — `views.workbook_id → workbooks`, `view_group_access.view_id → views`, `workbook_group_access.workbook_id → workbooks` — are *logical*, enforced by composite `(snapshot_id, *_id)` joins in application code, not by SQLite.

### 2. Mermaid `erDiagram` block

Renders inline on GitHub. Includes all 8 tables defined in the `SCHEMA` constant:

| Table | Purpose |
|---|---|
| `snapshots` | The temporal anchor. One row per collection run. |
| `group_members` | Group membership flattened, one row per (group, user) per snapshot. |
| `membership_changes` | Add/remove events between two snapshots. References `snapshots` *twice*. |
| `workbooks` | Workbooks visible at snapshot time. |
| `workbook_group_access` | Group permissions on workbooks at snapshot time. |
| `views` | Views (within workbooks) visible at snapshot time. |
| `view_group_access` | Group permissions on views at snapshot time. |
| `schema_version` | Single-row sentinel. Disconnected from other entities. |

**Columns shown for each table:** every column from `db.py`'s `SCHEMA`, with its SQLite type. Annotation rules:

- `PK` — primary key.
- `FK` — column has a declared `REFERENCES` clause in `SCHEMA`. In practice this applies *only* to `snapshot_id` columns (and `previous_snapshot_id` / `current_snapshot_id` on `membership_changes`). Logical foreign keys (`workbook_id`, `view_id`, `group_id`, `user_id`) are **not** marked `FK` at the column level — their cross-table meaning is carried by the relationship line's `logical …` label instead.
- `UK` — column participates in a `UNIQUE` constraint.

**Relationship lines and their labels:**

| From | To | Cardinality | Label | Kind |
|---|---|---|---|---|
| `snapshots` | `group_members` | `||--o{` | `"FK snapshot_id"` | Declared |
| `snapshots` | `membership_changes` | `||--o{` | `"FK current_snapshot_id"` | Declared |
| `snapshots` | `membership_changes` | `||--o{` | `"FK previous_snapshot_id (nullable)"` | Declared |
| `snapshots` | `workbooks` | `||--o{` | `"FK snapshot_id"` | Declared |
| `snapshots` | `workbook_group_access` | `||--o{` | `"FK snapshot_id"` | Declared |
| `snapshots` | `views` | `||--o{` | `"FK snapshot_id"` | Declared |
| `snapshots` | `view_group_access` | `||--o{` | `"FK snapshot_id"` | Declared |
| `workbooks` | `views` | `||--o{` | `"logical (snapshot_id, workbook_id)"` | Logical |
| `workbooks` | `workbook_group_access` | `||--o{` | `"logical (snapshot_id, workbook_id)"` | Logical |
| `views` | `view_group_access` | `||--o{` | `"logical (snapshot_id, view_id)"` | Logical |

`schema_version` has no relationships.

Mermaid's `erDiagram` cannot style declared and logical relationships with different line shapes; the distinction lives entirely in the relationship label (`FK …` vs `logical …`).

### 3. Legend (~50 words)

A short reading guide:

- Cardinality glyphs: `||--o{` = one-to-many; the `||` end is mandatory, the `o{` end is zero-or-more.
- A relationship label starting with `FK` is a SQLite-enforced foreign key. A label starting with `logical` is enforced by application code via a composite join.
- Column annotations: `PK` = primary key, `UK` = part of a `UNIQUE` constraint, `FK` = declared `REFERENCES`. Logical foreign keys are intentionally not marked `FK` at the column level.

## What is Deliberately Omitted

- **Indexes.** They are query-performance metadata, not schema entities, and would clutter the diagram.
- **Status / change-type enums** (`STATUS_IN_PROGRESS`, `CHANGE_ADDED`, etc.). These are application-level constants, not stored as separate tables. Mentioned in prose only if helpful, not in the diagram.
- **Previous schema versions.** The ERD is a snapshot of v2.

## Maintenance

Hand-maintained. When `db.py`'s `SCHEMA` changes:

1. Bump `CURRENT_SCHEMA_VERSION` in `db.py` (already required by existing code).
2. Update `docs/erd.md` in the same commit.

No automated drift check, no generator script, no CI hook. The maintenance burden is one markdown file edit per schema change, which matches the project's current pace of schema evolution (v1 → v2 took the lifetime of the project so far).

## Acceptance Criteria

The spec is satisfied when:

1. `docs/erd.md` exists and contains all three sections (prose, Mermaid block, legend) in that order.
2. The Mermaid block lists all 8 tables from `db.py`'s `SCHEMA`.
3. Every column declared in `SCHEMA` appears in the diagram with its SQLite type.
4. Every `REFERENCES` clause in `SCHEMA` corresponds to a relationship line labeled `FK …`.
5. The three logical relationships (`workbooks→views`, `workbooks→workbook_group_access`, `views→view_group_access`) appear with labels prefixed `logical …`.
6. The Mermaid block renders without syntax errors when previewed on GitHub.
