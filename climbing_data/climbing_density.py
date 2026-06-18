"""Pure, testable transforms for the sport-climbing grade-density explorer.

Read by grade_density.ipynb; unit-tested in test_climbing_density.py.
"""
import difflib
import re
from pathlib import Path

import pandas as pd

# A YDS grade is "5." + a number, optionally refined by a letter (a-d, with an optional
# same-band "/x" like 5.10a/b), a +/- modifier, or a cross-band slash like 5.10d/5.11a.
_YDS_RE = re.compile(
    r"^5\.\d+"
    r"(?:"
    r"[a-d](?:/[a-d]|/5\.\d+[a-d])?"   # 5.10a, 5.10a/b, 5.10d/5.11a
    r"|[+-]"                            # 5.10-, 5.10+
    r")?$"
)
_BAND_RE = re.compile(r"^5\.(\d+)")

# Letter/modifier weight within a band. Kept < 10 so a band's variants never collide with
# the next band: rank = 10*number + weight  =>  5.9 -> 90 sorts below 5.10a -> 101.
_LETTER_WEIGHT = {"-": 0, "a": 1, "b": 2, "c": 3, "d": 4, "+": 5}


def parse_yds_grade(grade_yds):
    """Parse a YDS grade string into {grade_letter, grade_band, grade_rank, parsed}.

    grade_band  : the band (letter/modifier stripped); cross-band slash -> first band.
    grade_letter: the finest form, preserved as-is (the cleaned input).
    grade_rank  : integer for ordering (10*number + letter/modifier weight); None if unparsed.
    parsed      : False for non-YDS/garbage -> these feed the reported "unparsed" bucket.
    """
    if grade_yds is None:
        return {"grade_letter": None, "grade_band": None, "grade_rank": None, "parsed": False}
    s = str(grade_yds).strip()
    if not _YDS_RE.match(s):
        return {"grade_letter": s or None, "grade_band": None, "grade_rank": None, "parsed": False}
    num = int(_BAND_RE.match(s).group(1))
    band = f"5.{num}"
    rest = s[len(band):]                     # chars after the band number, e.g. "a", "-", "d/5.11a"
    weight = _LETTER_WEIGHT.get(rest[0], 0) if rest else 0
    return {"grade_letter": s, "grade_band": band, "grade_rank": 10 * num + weight, "parsed": True}


def add_grade_columns(df, col="grade_yds"):
    """Add grade_letter / grade_band / grade_rank / parsed columns by parsing `col`."""
    parsed = df[col].apply(parse_yds_grade)
    out = df.copy()
    out["grade_letter"] = parsed.map(lambda d: d["grade_letter"])
    out["grade_band"] = parsed.map(lambda d: d["grade_band"])
    out["grade_rank"] = parsed.map(lambda d: d["grade_rank"])
    out["parsed"] = parsed.map(lambda d: d["parsed"])
    return out


def unparsed_grades(df, col="grade_yds"):
    """Rows whose grade does not parse as YDS — surfaced (count + samples), never dropped silently."""
    graded = add_grade_columns(df, col)
    return graded[~graded["parsed"]].copy()


def filter_sport(df):
    """Keep sport routes with a parseable YDS grade; dedupe by climb_id.

    Defensive: the export SQL already filters type.sport = true. The unparsed bucket is
    reported (via unparsed_grades) BEFORE this drop, so non-YDS sport routes are surfaced
    rather than silently discarded.
    """
    out = df
    if "is_sport" in out.columns:
        out = out[out["is_sport"].fillna(False).astype(bool)]
    out = out[out["grade_yds"].apply(lambda g: parse_yds_grade(g)["parsed"])]
    if "climb_id" in out.columns:
        out = out.drop_duplicates(subset="climb_id")
    return out.reset_index(drop=True)


def filter_area(df, state="Kentucky", area="Red River Gorge"):
    """Coarse-filter by state, then keep rows whose path_tokens CONTAINS `area` (membership,
    not a fixed depth — robust to RRG's uneven nesting). Raises a friendly ValueError listing
    the closest names present if `area` is absent (so "Red River" vs "Red River Gorge" is obvious).
    """
    out = df
    if state is not None and "state_province" in out.columns:
        out = out[out["state_province"] == state]
    if area is not None:
        mask = out["path_tokens"].apply(lambda toks: area in list(toks) if toks is not None else False)
        result = out[mask]
        if len(result) == 0:
            candidates = sorted({t for toks in out["path_tokens"] if toks is not None for t in list(toks)})
            close = difflib.get_close_matches(area, candidates, n=5, cutoff=0.5)
            hint = ", ".join(close) if close else ", ".join(candidates[:10])
            raise ValueError(
                f"No routes found with area {area!r}"
                + (f" under state={state!r}" if state is not None else "")
                + f". Closest names present: {hint}. "
                "(area is matched by membership in path_tokens, so the spelling must match.)"
            )
        out = result
    return out.reset_index(drop=True)


def derive_hierarchy(df, area="Red River Gorge", crag_offset=1):
    """Add wall (= path_tokens[-1], always a true leaf because the exporter fetches only leaf
    areas) and an `area`-anchored crag (= path_tokens[index(area) + crag_offset]), plus stable
    path-tuple keys so identically-named units in different parents never merge.

    crag_offset is exposed so the crag granularity (spec §10) can be tuned without code changes.
    If the anchored level would be the leaf itself (or overshoots the path), crag falls back to
    the wall and is flagged in crag_is_fallback.
    """
    walls, crags, wall_keys, crag_keys, fallbacks = [], [], [], [], []
    for toks in df["path_tokens"]:
        toks = list(toks) if toks is not None else []
        wall = toks[-1] if toks else None
        wall_key = tuple(toks)
        if area in toks:
            ci = toks.index(area) + crag_offset
            last = len(toks) - 1
            if ci < last:                                  # a genuine intermediate level exists
                crag, crag_key, fallback = toks[ci], tuple(toks[:ci + 1]), False
            else:                                          # ci is the leaf, or overshoots
                crag, crag_key, fallback = wall, wall_key, True
        else:                                              # area not in path (shouldn't happen post-filter)
            crag, crag_key, fallback = None, None, False
        walls.append(wall)
        crags.append(crag)
        wall_keys.append(wall_key)
        crag_keys.append(crag_key)
        fallbacks.append(fallback)
    out = df.copy()
    out["wall"] = walls
    out["crag"] = crags
    out["wall_key"] = wall_keys
    out["crag_key"] = crag_keys
    out["crag_is_fallback"] = fallbacks
    return out


def density_matrix(df, unit="crag", grade_level="band"):
    """Pivot of unit x grade with RAW route counts, grade columns ordered by grade_rank, plus a
    trailing total_routes column. Counting is grouped by the path-tuple key (so identically-named
    units don't merge); rows are labelled with the display name. `unit` can be "crag", "wall", or
    any other hierarchy column for roll-up.
    """
    name_col = unit
    key_col = f"{unit}_key"
    grade_col = "grade_band" if grade_level == "band" else "grade_letter"
    d = df[df[grade_col].notna()]
    order = d.groupby(grade_col)["grade_rank"].min().sort_values().index.tolist()
    counts = d.groupby([key_col, grade_col]).size().unstack(fill_value=0).reindex(columns=order)
    names = d.groupby(key_col)[name_col].first().to_dict()
    counts.index = [names[k] for k in counts.index]
    counts["total_routes"] = counts.sum(axis=1)
    counts = counts.sort_values("total_routes", ascending=False)
    counts.index.name = unit
    counts.columns.name = None
    return counts


def recommender_payload(df, area="Red River Gorge"):
    """Build the JSON-serializable payload for the client-side crag recommender (design §4.1).

    Takes a PREPARED frame (post add_grade_columns + derive_hierarchy): it must already have
    grade_band, grade_rank, crag, crag_key — the same contract as density_matrix. Returns:

        {"area": str,
         "bands": [band, ...],                      # present bands, ordered by grade_rank
         "crags": [{"crag": str, "preserve": str|None, "total": int, "counts": {band: int}}]}

    One entry per crag_key, so identically-named crags in different preserves stay distinct;
    `crag` is the display name. `total` is the crag's parsed-route count (the % fit denominator)
    and equals sum(counts). `preserve` is the AREA+1 orienting token, read from crag_key only
    when a token sits strictly between the area and the crag (guard i + 1 < len(key) - 1);
    otherwise None, so a crag directly under the area is never mislabeled with its own name.
    """
    d = df[df["grade_band"].notna()]
    bands = d.groupby("grade_band")["grade_rank"].min().sort_values().index.tolist()
    crags = []
    for crag_key, g in d.groupby("crag_key"):
        raw = g["grade_band"].value_counts().to_dict()
        counts = {b: int(raw[b]) for b in bands if b in raw}      # band-ordered; zero bands omitted
        key = list(crag_key)
        i = key.index(area)
        preserve = key[i + 1] if i + 1 < len(key) - 1 else None   # strict-between guard (§4.1)
        crags.append({
            "crag": g["crag"].iloc[0],
            "preserve": preserve,
            "total": int(sum(counts.values())),
            "counts": counts,
        })
    return {"area": area, "bands": bands, "crags": crags}


def top_units_for_grade(df, grade, unit="crag", n=5):
    """Top-N units (crag/wall/...) by raw count of routes at a given grade (band or letter)."""
    name_col = unit
    key_col = f"{unit}_key"
    mask = pd.Series(False, index=df.index)
    if "grade_band" in df.columns:
        mask = mask | (df["grade_band"] == grade)
    if "grade_letter" in df.columns:
        mask = mask | (df["grade_letter"] == grade)
    d = df[mask]
    if len(d) == 0:
        return pd.DataFrame({unit: [], "count": []})
    grp = d.groupby(key_col)
    res = pd.DataFrame({unit: grp[name_col].first().values, "count": grp.size().values})
    return res.sort_values("count", ascending=False).head(n).reset_index(drop=True)


def load_climbs(parquet_path):
    """Read the export parquet; raise an actionable error (pointing at DATA.md) if missing.

    Coerces path_tokens to plain Python lists — pyarrow may hand list columns back as ndarrays,
    and downstream code relies on list semantics (e.g. .index(area)).
    """
    path = Path(parquet_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Climbing parquet not found at {parquet_path!r}. Generate it with the one-time "
            "export described in DATA.md (it writes data/usa-sport-climbs.parquet)."
        )
    df = pd.read_parquet(path)
    if "path_tokens" in df.columns:
        df["path_tokens"] = df["path_tokens"].apply(lambda x: list(x) if x is not None else None)
    return df
