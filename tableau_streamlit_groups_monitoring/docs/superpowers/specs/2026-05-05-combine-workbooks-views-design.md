# Combine Workbooks and Views pages — design spec

## Summary

Merge `pages/workbooks.py` into `pages/views.py` so a single page surfaces both
workbook-level and view-level group access. Each workbook expander on the Views
page leads with a "Workbook groups" line, then the existing per-view table
underneath. The Workbooks page and its nav entry are removed.

## Motivation

Today the Workbooks and Views pages answer overlapping questions about the same
hierarchy. To audit a workbook a user has to flip between two pages: one for
its workbook-level rules, another for the view-level inheritance / divergence
flag. Surfacing both inside one expander keeps the workbook in context with its
views.

## Scope

In scope:

- Replace `pages/views.py` with a combined page that renders workbook-level
  groups inside each expander above the existing views table.
- Delete `pages/workbooks.py`.
- Remove the Workbooks entry from `app.py`'s nav list.
- Update the Views page header / caption to reflect the new combined scope.
- Extend the metrics row to include `Workbooks with no group access`.
- Extend search to match workbook-level group names.

Out of scope:

- DB / snapshot / query layer changes — `db.get_workbooks_for_snapshot` and
  `db.get_views_for_snapshot` remain as-is.
- Project-level locks, direct user grants, capabilities other than Read.
- Any change to the Current State or Changes pages.

## Decisions (confirmed in brainstorming)

- **C2** — workbook groups inside the expander render as a `st.columns([1, 4])`
  two-column block: bold label `Workbook groups` on the left, comma-joined group
  names on the right, `—` when there are no groups.
- **M1** — three metrics: `Workbooks`, `Views`, `Workbooks with no group access`.
- **N1** — delete `pages/workbooks.py`; remove from `app.py` nav. Page title
  remains `Views`.
- **S1** — single search box; matches `project / workbook / workbook-level
  group / view / view-level group`.
- **L1** — expander label is unchanged from today: `Project / Workbook` plus the
  existing orange-tinted "(N views differ)" suffix when applicable. No
  no-access tinting.

## Architecture

One Streamlit page file (`pages/views.py`) renders the combined view. The
hierarchy of computation is unchanged from today's Views page; the only new
data needed is the *names* of the workbook-level groups (today only the *ids*
are kept, since they're consumed only by the divergence comparison).

### Data flow inside `pages/views.py`

1. Open a connection, fetch `snapshots`, render the snapshot selectbox.
2. For the selected snapshot, fetch `wb_rows = db.get_workbooks_for_snapshot()`
   and `rows = db.get_views_for_snapshot()`.
3. Build a single per-workbook structure that carries everything the page
   needs:

   ```python
   workbooks: dict[str, dict] = {
       wb_id: {
           "project": str,
           "name": str,
           "wb_group_ids": set[str],   # for divergence comparison
           "wb_group_names": list[str],# for the C2 right-column display, SQL order
           "views": dict[str, dict],   # view_id -> {name, groups, group_ids, differs}
       }
   }
   ```

   The first pass walks `wb_rows` and seeds each workbook with its
   project/name plus the group id set and ordered name list (LEFT-JOIN null
   group rows are skipped — they mean "no grants," not "a group with id None").
   The second pass walks the view rows and fills in `views`, exactly as today.
   The third pass computes `differs` per view, identical to today's logic
   (`skip_diff` when the workbook has zero groups).

4. Compute metrics:
   - `len(workbooks)` workbooks.
   - `sum(len(wb["views"]) for wb in workbooks.values())` views.
   - `sum(1 for wb in workbooks.values() if not wb["wb_group_ids"])` workbooks
     with no group access.
5. Render the search input. `workbook_matches(wb, needle)` returns True when
   the needle hits any of: project, workbook name, any name in
   `wb_group_names`, any view name, any view group name.
6. Iterate the (already SQL-ordered) workbooks; for each, render an expander
   whose label and auto-expand-on-search behavior are unchanged. Inside the
   expander:

   - **Workbook groups block** — `st.columns([1, 4])`. Left column:
     `st.markdown("**Workbook groups**")`. Right column:
     `st.markdown(", ".join(wb["wb_group_names"]) or "—")`.
   - **Views table** — identical to today: a `pd.DataFrame` with columns
     `View`, `Groups with access`, `Differs from workbook`, with diff rows
     pre-sorted to the top via stable sort. Workbooks with no views render the
     placeholder row as today.

### `app.py` change

The `pages` list drops the Workbooks entry. The remaining order is:
`Current State`, `Views`, `Changes`.

### Header / caption copy

The header stays `Permissions` (or remains `View Permissions` — see open
question below). The caption is rewritten to a single paragraph that covers
both scopes:

> Group access shown at two levels: each workbook's own group rules, and a
> best-effort view of group access per view under a Read=Allow-only model.
> When a view has any explicit group Read rule, only its Read=Allow groups are
> shown; otherwise access is inherited from the parent workbook. Project-level
> locks, direct user grants, and capabilities other than Read are not
> represented — treat this page as a starting point for audit, not a final
> source of truth. A flagged row with no listed groups is an explicit Deny that
> blocks parent inheritance.

## What is removed

- `pages/workbooks.py` (file deleted).
- The Workbooks `st.Page(...)` entry in `app.py`.

## Edge cases

- **Workbook with zero group grants** — `wb_group_names` is empty. Right
  column renders `—`. Divergence flagging is suppressed for its views
  (existing `skip_diff` behavior). Metric `Workbooks with no group access`
  counts this workbook.
- **Workbook with zero views** — appears in `wb_rows` but the corresponding
  view rows are placeholder LEFT-JOIN rows with `view_id IS NULL`. The
  expander still renders; the workbook groups block is correct, and the views
  table falls back to today's placeholder row.
- **Unresolved (stale) group references** — handled identically to today: the
  display name falls back to `<unresolved:{group_id}>` for both the workbook
  block and the views table.
- **Search hitting only a workbook-level group** — `workbook_matches` returns
  True from the new `wb_group_names` check; expander auto-expands.
- **No snapshots / no rows** — same `st.info(...)` + `st.stop()` short-circuits
  as today.

## Testing

Manual smoke pass against the existing fake-data snapshot:

1. Page loads with the most recent snapshot selected; metrics show three
   numbers consistent with the data.
2. Each workbook expander shows a "Workbook groups" line above its views
   table. A workbook known to have no group rules shows `—`.
3. Searching for a group name that exists only on a workbook (not on any
   view) auto-expands that workbook.
4. Searching for a view name behaves as today (auto-expands the parent
   workbook).
5. A workbook with diverging views shows the orange-tinted label with the
   correct `(N views differ)` count, and diff rows are sorted to the top of
   the views table.
6. A workbook with zero group grants renders the views table without any
   `Differs from workbook` flags (skip_diff still active).
7. A workbook with zero views renders an expander with the workbook groups
   block plus the placeholder views row.
8. Nav shows three pages: Current State, Views, Changes — Workbooks is gone.

## Known limitations

Inherits all the limitations from the existing Views and Workbooks pages
(Read=Allow-only approximation, project-level locks not represented, direct
user grants not represented, capabilities other than Read not represented).
The combined page does not change the underlying model — it only changes
presentation.

## Open questions

- **Page header text** — today the Views page reads `View Permissions` and the
  Workbooks page reads `Workbook Permissions`. The combined page covers both;
  `Permissions` is the cleanest, but `Workbook & View Permissions` is more
  literal. Default to `Permissions` unless the user prefers otherwise during
  implementation.
