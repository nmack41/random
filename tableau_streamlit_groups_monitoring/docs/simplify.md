# Simplify plan: `pages/views.py`

Scope: review of commit `8dee844` ("Combine Workbooks page into Views page"),
which absorbed the deleted `pages/workbooks.py` into `pages/views.py`.

Reviewed by three local agents (reuse / quality / efficiency) and assessed by
`gpt-5.4` via the pal MCP server.

## Will apply

1. **Extract `NO_DATA_MSG` constant.** The string `"No data yet. Run \`python3
   snapshot.py\` to capture workbooks, views, and their group permissions."` is
   duplicated at `pages/views.py:25` and `pages/views.py:39`. One constant at
   module scope eliminates drift if the command name ever changes again.

2. **Extract `_group_label(r)` helper, local to `pages/views.py`.** The
   expression `r["group_name"] or f"<unresolved:{r['group_id']}>"` appears at
   `pages/views.py:57` and `pages/views.py:73`. Justification is policy
   centralization, not DRY: the `<unresolved:{id}>` format is a user-visible
   contract embedding a specific stale-reference policy. Keep the helper tiny
   and local — do not promote it to a shared utilities module unless a third
   call site appears.

3. **Replace `"Yes"` / `""` with `bool` in the "Differs from workbook" column.**
   Currently at `pages/views.py:143`. Justification is type correctness and
   sorting/export semantics: a bool column sorts and exports correctly
   regardless of renderer. Streamlit `st.dataframe` rendering bools as
   checkboxes is a bonus, not the case for the change.

4. **Drop "Second pass: collapse view rows..." comment** at `pages/views.py:59`.
   It narrates *what* the loop does; the surrounding "First pass" / "Third
   pass" comments encode real *why* and stay.

5. **Dedup guard on group-name appends.** At `pages/views.py:54-57` and
   `pages/views.py:72-74`, only append the display label when its `group_id` is
   first seen in the corresponding set. Aligns the display lists
   (`wb_group_names`, `view["groups"]`) with the set-based logic
   (`wb_group_ids`, `view["group_ids"]`) instead of relying on the
   `workbook_group_access` / `view_group_access` UNIQUE constraint to prevent
   duplicate rows from reaching the page.

6. **Rename `rows` → `view_rows`** at `pages/views.py:36` and at the loop on
   line 60. Side-by-side with `wb_rows`, the unqualified `rows` is opaque.

## Held for user decision

- **Single-row DataFrame for "Workbook groups"** at `pages/views.py:127-130`.
  The commit message documents this as a deliberate styling choice ("to match
  the views-table styling"). Alternative: `st.markdown(f"**Workbook groups:**
  {', '.join(wb['wb_group_names']) or PLACEHOLDER}")`, which drops the table
  header and fake row index but loses the visual rhyme with the views table
  below it. Tradeoff is visual consistency vs. simpler markup.

## Recommended as a follow-up pass

- **Cross-page snapshot-selector helper.** The pattern of
  `db.get_snapshot_list(conn)` → empty-state guard → `snapshot_options` dict →
  `st.selectbox` is duplicated across `pages/views.py`, `pages/current_state.py`,
  and `pages/changes.py` with drift:
  - command string: `python` (`current_state.py:12`) vs `python3`
    (`views.py:25, 39`)
  - severity: `st.warning` (`current_state.py:12`) vs `st.info` everywhere else
  - connection lifecycle: `with closing(...)` (`views.py:21`) vs manual
    `conn.close()` before `st.stop()` (sibling pages)

  A `render_snapshot_selector(conn, *, label, min_required=1) -> (selected_id,
  snapshot_options)` helper in a new `ui.py` would unify all three. Deferred
  because it touches three files and needs a real contract decision (does the
  helper own the connection? handle one-snapshot vs two-snapshot pages?). The
  drift is maintenance-drag, not user-impacting breakage, so a separate pass is
  appropriate.

## Leaving as-is

- **Three-pass aggregation** at `pages/views.py:42-82`. Pass 3 (divergence
  flag) has a strict dependency on the final state of passes 1 and 2; passes 1
  and 2 are integrating two distinct result sets (`wb_rows` and `view_rows`),
  which is fine. Folding into fewer passes would not simplify.

- **Two DB queries instead of one extended query.** `get_views_for_snapshot`
  already starts `FROM workbooks w` and could be extended to also carry
  workbook-level group rules, eliminating the second query — but the gain is
  sub-millisecond on a local SQLite file with `snapshot_id`-indexed reads, and
  the current shape (each query maps to one concept) is more readable.

- **No `@st.cache_data`.** Premature for current scale. Adds a "why didn't my
  data refresh after a new snapshot?" confusion tax that outweighs the
  imperceptible win.

- **Most existing comments.** The side-by-side `wb_group_ids`/`wb_group_names`
  comment (lines 42-44), the unresolved-fallback note (line 56), the LEFT JOIN
  null-row note (line 66), the divergence-suppression note (lines 76-78), the
  Streamlit reactivity gotcha (lines 116-117), and the stable-sort intent
  (lines 135-137) all encode non-obvious *why*.

- **`COLUMNS` constant** at `pages/views.py:114`. Initial reasoning ("pandas
  preserves insertion order, so the constant is unnecessary") was wrong: the
  constant's job is to declare the table's column schema explicitly, which it
  does well. Leave it.
