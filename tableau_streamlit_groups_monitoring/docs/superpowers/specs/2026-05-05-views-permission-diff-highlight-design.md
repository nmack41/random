# Views Page — Workbook-vs-View Permission Diff Highlight — Design

## Goal

On the Views page, surface view-vs-workbook permission divergence at a glance.
A view's row is highlighted (row tint plus a `Differs from workbook` column)
when the view's effective group set is non-equal to its parent workbook's
group set. The expander label gets a `(N views differ)` suffix when N > 0.
Skip the diff entirely for workbooks with zero group grants.

This is a follow-on to the Views page (`2026-05-04-views-page-design.md`),
walking back the original "distinguishing inherited vs explicit access in the
UI" non-goal — but in a more useful form: not "did someone override?" but
"does the override result in a different effective set?" That is the
audit-relevant question the Views page exists to answer.

## Non-goals

- **Direction-aware diff.** No broader / narrower / disjoint distinction.
  Binary set inequality only.
- **Itemized per-row diff.** No "+marketing-team / −finance-readonly"
  annotation. Future layer (tooltip or sub-row) if needed.
- **Provenance.** No distinction between "inherited and equal" and "explicit
  and equal." Set semantics only.
- **Highlighting under no-access workbooks.** Workbooks with zero group
  grants suppress the diff. Documented under Known limitations.
- **Schema changes, snapshot changes, migrations.** Feature is read-only
  Python in `pages/views.py`.
- **Cross-snapshot diff.** View-permission drift over time remains a future
  Changes-page concern.
- **Automated tests.** Verification is manual against the seeded fake-data
  DB.

## Architecture

No new files. No schema changes. No `db.py` changes. No `snapshot.py`
changes. The feature is contained in `pages/views.py`.

```
db.get_workbooks_for_snapshot(snapshot_id)   ← reused as-is
        │ (workbook + workbook_group_access rows)
        ▼
   wb_groups: dict[workbook_id, set[group_id]]

db.get_views_for_snapshot(snapshot_id)       ← reused as-is
        │ (LEFT JOIN: workbooks × views × view_group_access)
        ▼
pages/views.py
  └─ collapse loop  ← extended to also accumulate group_id sets per view
        │
        ▼
  └─ diff pass      ← NEW: for each workbook with non-empty wb_groups,
                      mark each view's `differs` flag
        │
        ▼
  └─ render         ← Pandas Styler row tint where differs=True
                      + "Differs from workbook" column ("Yes" or blank)
                      + expander label suffix "(N views differ)"
```

The existing `db.get_workbooks_for_snapshot` already returns
`(workbook_id, group_id, group_name)` rows, exactly what is needed to build
the per-workbook group set. Reusing it keeps the SQL surface area stable and
the diff logic in one Python file. Two local-SQLite round-trips instead of
one wider join is a free trade at this scale.

## Comparison semantics

For each view under a workbook with at least one group grant:

```
differs = bool(wb_groups) and view_groups != wb_groups
```

Where `wb_groups` and `view_groups` are sets of `group_id` strings.

- `group_id` is the comparison key, not `group_name`. Names can be null for
  unresolved (stale) groups; IDs are the stable key. ID-based comparison
  produces a correct truthy diff even when one side has a stale reference.
- LEFT JOIN nulls (rows where `group_id is None`) are filtered out when
  populating sets — they represent "no rows to join," not "a group with id
  None."
- Sets deduplicate join fanout. The LEFT JOIN can repeat workbook-grant
  rows once per joined view-grant row; collecting into a set absorbs that.
- Inheriting views always have `view_groups == wb_groups` because
  `snapshot.py` stores inherited grants verbatim into `view_group_access`.
  They will correctly not highlight.
- A view with explicit rules whose effective set happens to mirror the
  workbook will also not highlight. Provenance is intentionally not
  surfaced.
- A workbook with zero group grants suppresses the diff for all its views.
  See Known limitations for the trade-off.

## Implementation details

All changes live in `pages/views.py`. References to existing line numbers
are against the current file.

### (a) Fetch workbook groups alongside view rows

Inside the existing `with closing(db.get_connection()) as conn:` block at
`pages/views.py:19`, add:

```python
wb_rows = db.get_workbooks_for_snapshot(conn, selected_id)
```

Build the `dict[workbook_id, set[group_id]]`:

```python
wb_groups: dict[str, set[str]] = {}
for r in wb_rows:
    s = wb_groups.setdefault(r["workbook_id"], set())
    if r["group_id"] is not None:
        s.add(r["group_id"])
```

### (b) Accumulate view groups as a set during collapse

Today the collapse loop at `pages/views.py:42-58` appends to
`view["groups"]` (a list of display strings preserving SQL order). Extend
the view dict to also track the underlying ID set:

```python
view = wb["views"].setdefault(r["view_id"], {
    "name": r["view_name"],
    "groups": [],          # display list, unchanged
    "group_ids": set(),    # NEW: for diff comparison
})
if r["group_id"] is not None:
    view["groups"].append(r["group_name"] or f"<unresolved:{r['group_id']}>")
    view["group_ids"].add(r["group_id"])
```

The display list keeps its current ordering (used for the human-readable
column); the set is for set-equality only.

### (c) Compute per-view diff flag

Single pass after the collapse loop, before metrics rendering:

```python
for wb_id, wb in workbooks.items():
    wb_set = wb_groups.get(wb_id, set())
    skip_diff = not wb_set
    for v in wb["views"].values():
        v["differs"] = (not skip_diff) and (v["group_ids"] != wb_set)
```

### (d) Workbook expander label suffix

At `pages/views.py:89`, where the label is built:

```python
n_diff = sum(1 for v in wb["views"].values() if v["differs"])
label = f"{wb['project']} / {wb['name']}" if wb["project"] else wb["name"]
if n_diff > 0:
    noun = "view" if n_diff == 1 else "views"
    label = f"{label} ({n_diff} {noun} differ)"
```

(Singular "view" when N == 1, "views" otherwise.)

### (e) Per-row rendering

Extend the records dict at `pages/views.py:94-100` to include the new
column:

```python
records = [
    {
        "View": v["name"],
        "Groups with access": ", ".join(v["groups"]) if v["groups"] else PLACEHOLDER,
        "Differs from workbook": "Yes" if v["differs"] else "",
    }
    for v in wb["views"].values()
]
```

The placeholder row for zero-views workbooks gets the new column too:

```python
records = [{"View": PLACEHOLDER, "Groups with access": PLACEHOLDER, "Differs from workbook": PLACEHOLDER}]
```

### (f) Row tint via Pandas Styler

```python
def _highlight_differs(row):
    if row["Differs from workbook"] == "Yes":
        return ["background-color: #fff3cd"] * len(row)
    return [""] * len(row)

styled = df.style.apply(_highlight_differs, axis=1)
st.dataframe(styled, use_container_width=True, hide_index=True)
```

`#fff3cd` is Bootstrap's "warning" tint — light amber, readable on white
Streamlit theme. Dark-theme readability is part of the verification
checklist (item 9 below). If it washes out in dark mode, swap to a darker
amber.

## Files touched

**Modified**

- `pages/views.py` — add the workbook-groups fetch, extend the collapse
  loop with `group_ids` sets, add the diff pass, append the expander label
  suffix, add the new column, wrap the DataFrame in a Pandas Styler.

**Possibly modified (verification scaffold only)**

- `fake_data/fixtures.py` — only if the existing fixtures do not already
  cover the four cases listed under Verification step 3-6.

**Unchanged**

- `db.py`, `snapshot.py`, `app.py`, `pages/workbooks.py`, `pages/changes.py`,
  `pages/current_state.py`, `config.py`, `requirements.txt`.

## Error handling

None added. The diff logic is pure dict/set work over already-validated SQL
rows. Failure modes the existing page already handles (no snapshot, empty
rows, stale group references, unresolved group names) carry through
unchanged. The `wb_groups` dict defaults to an empty set for any workbook
missing from `get_workbooks_for_snapshot` (cannot happen at the data layer
but is crash-free if data ever drifts).

## Known limitations

- **No-grant workbooks suppress the diff.** A view-level grant on a
  workbook with zero group grants will not be flagged on the Views page.
  The Workbooks page's "Workbooks with no group access" metric remains the
  place to spot those workbooks; an auditor cross-references the two
  pages.
- **Binary diff only.** Does not distinguish broader (potential
  privilege escalation) from narrower (often intentional). A future v2
  could split the column or add a tooltip, without schema change.
- **Intra-snapshot only.** Cross-snapshot view-permission drift remains
  out of scope.
- **Pandas Styler is absolute about colors.** `#fff3cd` is fixed across
  Streamlit themes. Dark-mode contrast is verified in the checklist; if it
  is unreadable, the fix is a follow-on tweak to the styler function, not
  a design change.

## Verification

Manual checklist after implementation, run against the fake-data DB:

1. `python -m fake_data.seed` rebuilds `fake_data/groups.db` without
   errors.
2. `FAKE_DATA=1 streamlit run app.py` starts; Views page loads.
3. **Inheriting view** (uses workbook grants verbatim): row not tinted,
   "Differs from workbook" blank.
4. **Explicit-override view with different set**: row tinted, "Differs
   from workbook" = "Yes".
5. **Explicit view that mirrors workbook**: row not tinted, "Differs from
   workbook" blank — confirms set-based diff handles "explicit but equal"
   correctly.
6. **Workbook with zero group grants**: no rows in that expander tinted,
   regardless of view-level grants. (Trade-off documented above.)
7. **Workbook with N differing views**: expander label reads
   `Project / Name (N views differ)`, with singular "view" when N == 1.
8. Search box filtering still works (existing behavior — searches
   project / workbook / view / group names; the diff column is not in the
   search index). Streamlit's built-in column-header click-to-sort on
   "Differs from workbook" is the way to bring differing rows to the top
   within an expander.
9. **Dark mode**: toggle Streamlit theme to dark; row tint remains
   readable. If unreadable, swap `#fff3cd` for a tone that survives both
   themes.
10. Snapshot selector switches between snapshots without errors; a
    snapshot with no grants anywhere shows zero highlights everywhere.

If any of cases 3-6 is missing from `fake_data/fixtures.py`, extend the
fixtures minimally to add it before declaring verification complete.
