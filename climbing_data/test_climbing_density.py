"""Unit tests for climbing_density (spec §7). TDD: written before the implementation.

Run: .venv/bin/pytest -q
"""
import pandas as pd
import pytest

import climbing_density as cd

RRG = "Red River Gorge"


def _frame(rows):
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# parse_yds_grade
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw, band, letter", [
    ("5.6", "5.6", "5.6"),
    ("5.10a", "5.10", "5.10a"),
    ("5.10", "5.10", "5.10"),
    ("5.10-", "5.10", "5.10-"),
    ("5.10+", "5.10", "5.10+"),
    ("5.10a/b", "5.10", "5.10a/b"),
    ("5.10d/5.11a", "5.10", "5.10d/5.11a"),   # cross-band slash -> FIRST band (pinned rule)
    ("5.11d", "5.11", "5.11d"),
])
def test_parse_yds_grade_band_and_letter(raw, band, letter):
    r = cd.parse_yds_grade(raw)
    assert r["parsed"] is True
    assert r["grade_band"] == band
    assert r["grade_letter"] == letter


@pytest.mark.parametrize("raw", ["V4", "8a", "", "   ", None, "5.abc", "5.", "junk", "5.10xyz"])
def test_parse_yds_grade_unparsed_bucket(raw):
    r = cd.parse_yds_grade(raw)
    assert r["parsed"] is False
    assert r["grade_band"] is None
    assert r["grade_rank"] is None


def test_parse_yds_grade_rank_is_monotonic_including_the_5_9_vs_5_10a_trap():
    sequence = ["5.6", "5.9", "5.10a", "5.10b", "5.10d", "5.11a", "5.11d"]
    ranks = [cd.parse_yds_grade(g)["grade_rank"] for g in sequence]
    assert ranks == sorted(ranks)                    # monotonic
    assert len(set(ranks)) == len(ranks)             # strictly increasing
    # the classic lexical trap: "5.9" must rank BELOW "5.10a"
    assert cd.parse_yds_grade("5.9")["grade_rank"] < cd.parse_yds_grade("5.10a")["grade_rank"]


# --------------------------------------------------------------------------- #
# add_grade_columns / unparsed_grades
# --------------------------------------------------------------------------- #

def test_add_grade_columns_expands_into_columns():
    df = _frame([{"climb_id": "1", "grade_yds": "5.10a"},
                 {"climb_id": "2", "grade_yds": "V4"}])
    out = cd.add_grade_columns(df)
    assert out.loc[out.climb_id == "1", "grade_band"].iloc[0] == "5.10"
    assert bool(out.loc[out.climb_id == "1", "parsed"].iloc[0]) is True
    assert bool(out.loc[out.climb_id == "2", "parsed"].iloc[0]) is False


def test_unparsed_grades_returns_only_unparseable_rows():
    df = _frame([{"climb_id": "1", "grade_yds": "5.10a"},
                 {"climb_id": "2", "grade_yds": "V4"},
                 {"climb_id": "3", "grade_yds": None}])
    out = cd.unparsed_grades(df)
    assert set(out["climb_id"]) == {"2", "3"}


# --------------------------------------------------------------------------- #
# filter_sport
# --------------------------------------------------------------------------- #

def test_filter_sport_keeps_sport_parseable_and_dedupes_by_climb_id():
    df = _frame([
        {"climb_id": "1", "is_sport": True,  "grade_yds": "5.10a"},
        {"climb_id": "1", "is_sport": True,  "grade_yds": "5.10a"},   # duplicate
        {"climb_id": "2", "is_sport": False, "grade_yds": "5.11a"},   # not sport
        {"climb_id": "3", "is_sport": True,  "grade_yds": "V4"},      # sport but non-YDS
    ])
    out = cd.filter_sport(df)
    assert set(out["climb_id"]) == {"1"}
    assert len(out) == 1


# --------------------------------------------------------------------------- #
# filter_area
# --------------------------------------------------------------------------- #

def _area_frame():
    return _frame([
        {"climb_id": "1", "state_province": "Kentucky",
         "path_tokens": ["USA", "Kentucky", RRG, "Muir Valley", "Sunnyside", "Wall A"]},
        {"climb_id": "2", "state_province": "Kentucky",
         "path_tokens": ["USA", "Kentucky", RRG, "Roadside Crag"]},        # RRG, shallow
        {"climb_id": "3", "state_province": "Kentucky",
         "path_tokens": ["USA", "Kentucky", "Some Other Area", "Crag X"]}, # KY, not RRG
        {"climb_id": "4", "state_province": "Colorado",
         "path_tokens": ["USA", "Colorado", "Rifle", "Wall Z"]},          # not KY
    ])


def test_filter_area_keeps_only_the_rrg_subtree_across_depths():
    out = cd.filter_area(_area_frame(), state="Kentucky", area=RRG)
    assert set(out["climb_id"]) == {"1", "2"}


def test_filter_area_absent_area_raises_with_close_match_suggestion():
    with pytest.raises(ValueError) as exc:
        cd.filter_area(_area_frame(), state="Kentucky", area="Red River")  # close but absent
    assert "Red River Gorge" in str(exc.value)


# --------------------------------------------------------------------------- #
# derive_hierarchy
# --------------------------------------------------------------------------- #

def test_derive_hierarchy_anchors_crag_one_level_below_area():
    df = _frame([{"climb_id": "1",
                  "path_tokens": ["USA", "Kentucky", RRG, "Muir Valley", "Sunnyside", "Wall A"]}])
    row = cd.derive_hierarchy(df, area=RRG).iloc[0]
    assert row["wall"] == "Wall A"
    assert row["crag"] == "Muir Valley"                      # area idx 2, +1 -> idx 3
    assert not row["crag_is_fallback"]
    assert row["wall_key"] == ("USA", "Kentucky", RRG, "Muir Valley", "Sunnyside", "Wall A")
    assert row["crag_key"] == ("USA", "Kentucky", RRG, "Muir Valley")


def test_derive_hierarchy_leaf_direct_child_of_area_falls_back_to_wall():
    df = _frame([{"climb_id": "1",
                  "path_tokens": ["USA", "Kentucky", RRG, "Roadside Crag"]}])
    row = cd.derive_hierarchy(df, area=RRG).iloc[0]
    assert row["wall"] == "Roadside Crag"
    assert row["crag"] == "Roadside Crag"                    # no intermediate level
    assert row["crag_is_fallback"]
    assert row["crag_key"] == row["wall_key"]


def test_derive_hierarchy_distinguishes_identically_named_walls():
    df = _frame([
        {"climb_id": "1",
         "path_tokens": ["USA", "Kentucky", RRG, "Muir Valley", "Sunnyside", "The Arsenal"]},
        {"climb_id": "2",
         "path_tokens": ["USA", "Kentucky", RRG, "The Gorge", "Military Wall", "The Arsenal"]},
    ])
    out = cd.derive_hierarchy(df, area=RRG)
    assert out.iloc[0]["wall"] == out.iloc[1]["wall"] == "The Arsenal"   # same display name
    assert out.iloc[0]["wall_key"] != out.iloc[1]["wall_key"]            # distinct path keys
    assert out.iloc[0]["crag"] == "Muir Valley"
    assert out.iloc[1]["crag"] == "The Gorge"


def test_derive_hierarchy_crag_offset_is_adjustable():
    df = _frame([{"climb_id": "1",
                  "path_tokens": ["USA", "Kentucky", RRG, "Muir Valley", "Sunnyside", "Wall A"]}])
    row = cd.derive_hierarchy(df, area=RRG, crag_offset=2).iloc[0]
    assert row["crag"] == "Sunnyside"                        # area idx 2, +2 -> idx 4
    assert row["crag_key"] == ("USA", "Kentucky", RRG, "Muir Valley", "Sunnyside")


# --------------------------------------------------------------------------- #
# density_matrix / top_units_for_grade
# --------------------------------------------------------------------------- #

def _density_frame():
    rows = [
        {"climb_id": "1", "grade_yds": "5.9",
         "path_tokens": ["USA", "Kentucky", RRG, "Muir Valley", "Sunnyside", "Wall A"]},
        {"climb_id": "2", "grade_yds": "5.10a",
         "path_tokens": ["USA", "Kentucky", RRG, "Muir Valley", "Sunnyside", "Wall A"]},
        {"climb_id": "3", "grade_yds": "5.10d",
         "path_tokens": ["USA", "Kentucky", RRG, "Muir Valley", "Sunnyside", "Wall A"]},
        {"climb_id": "4", "grade_yds": "5.10b",
         "path_tokens": ["USA", "Kentucky", RRG, "The Gorge", "Military Wall", "Wall B"]},
        {"climb_id": "5", "grade_yds": "5.12a",
         "path_tokens": ["USA", "Kentucky", RRG, "The Gorge", "Military Wall", "Wall B"]},
    ]
    df = cd.add_grade_columns(_frame(rows))
    return cd.derive_hierarchy(df, area=RRG)


def test_density_matrix_counts_with_rank_ordered_columns_and_totals():
    m = cd.density_matrix(_density_frame(), unit="crag", grade_level="band")
    assert m.loc["Muir Valley", "5.9"] == 1
    assert m.loc["Muir Valley", "5.10"] == 2                 # 5.10a + 5.10d roll up to 5.10
    assert m.loc["Muir Valley", "total_routes"] == 3
    assert m.loc["The Gorge", "5.10"] == 1
    assert m.loc["The Gorge", "5.12"] == 1
    assert m.loc["The Gorge", "total_routes"] == 2
    grade_cols = [c for c in m.columns if c != "total_routes"]
    assert grade_cols == ["5.9", "5.10", "5.12"]             # ordered by grade_rank, not lexically


def test_top_units_for_grade_ranks_units_by_count_descending():
    out = cd.top_units_for_grade(_density_frame(), grade="5.10", unit="crag", n=5)
    assert list(out["crag"]) == ["Muir Valley", "The Gorge"]
    assert list(out["count"]) == [2, 1]


# --------------------------------------------------------------------------- #
# load_climbs
# --------------------------------------------------------------------------- #

def test_load_climbs_missing_file_raises_error_pointing_at_data_md(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        cd.load_climbs(str(tmp_path / "does-not-exist.parquet"))
    assert "DATA.md" in str(exc.value)


def test_load_climbs_reads_parquet_and_coerces_path_tokens_to_lists(tmp_path):
    p = tmp_path / "tiny.parquet"
    pd.DataFrame({
        "climb_id": ["1"],
        "grade_yds": ["5.10a"],
        "is_sport": [True],
        "path_tokens": [["USA", "Kentucky", RRG, "Crag", "Wall"]],
    }).to_parquet(p)
    out = cd.load_climbs(str(p))
    val = out.iloc[0]["path_tokens"]
    assert isinstance(val, list)
    assert val == ["USA", "Kentucky", RRG, "Crag", "Wall"]


# --------------------------------------------------------------------------- #
# recommender_payload
# --------------------------------------------------------------------------- #

def _recommender_frame(crag_offset=2):
    """Prepared RRG frame for recommender_payload tests (mirrors _density_frame).

    Built so a single frame exercises every spec §5 case at crag_offset=2:
      - The Motherlode (PMRP): a deep, fallback-to-wall crag that still has a preserve;
      - The Arsenal under BOTH Muir Valley and PMRP: identically-named, must stay distinct;
      - Roadside Crag: directly under the area -> preserve None (never its own name).
    """
    rows = [
        {"climb_id": "1", "grade_yds": "5.13a",
         "path_tokens": ["USA", "Kentucky", RRG, "PMRP", "The Motherlode"]},
        {"climb_id": "2", "grade_yds": "5.12c",
         "path_tokens": ["USA", "Kentucky", RRG, "PMRP", "The Motherlode"]},
        {"climb_id": "3", "grade_yds": "5.12a",
         "path_tokens": ["USA", "Kentucky", RRG, "PMRP", "The Motherlode"]},
        {"climb_id": "4", "grade_yds": "5.9",
         "path_tokens": ["USA", "Kentucky", RRG, "Muir Valley", "The Arsenal"]},
        {"climb_id": "5", "grade_yds": "5.11a",
         "path_tokens": ["USA", "Kentucky", RRG, "PMRP", "The Arsenal"]},
        {"climb_id": "6", "grade_yds": "5.8",
         "path_tokens": ["USA", "Kentucky", RRG, "Roadside Crag"]},
    ]
    df = cd.add_grade_columns(_frame(rows))
    return cd.derive_hierarchy(df, area=RRG, crag_offset=crag_offset)


def _payload_crag(payload, name, preserve):
    """The single crag entry matching (display name, preserve) — keys are distinct by both."""
    hits = [c for c in payload["crags"] if c["crag"] == name and c["preserve"] == preserve]
    assert len(hits) == 1, f"expected one {name!r} under {preserve!r}, got {len(hits)}"
    return hits[0]


def test_recommender_payload_bands_are_rank_ordered_present_only():
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    assert p["area"] == RRG
    # rank order (5.9 before higher numbers), and 5.10 absent because no route is in it
    assert p["bands"] == ["5.8", "5.9", "5.11", "5.12", "5.13"]


def test_recommender_payload_counts_total_and_zero_omission():
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    ml = _payload_crag(p, "The Motherlode", "PMRP")
    assert ml["counts"] == {"5.12": 2, "5.13": 1}     # 5.12a + 5.12c roll up to 5.12
    assert "5.8" not in ml["counts"]                  # zero band omitted, not stored as 0
    assert ml["total"] == 3                           # == sum(counts) == the crag's route count


def test_recommender_payload_preserve_read_for_deep_crag():
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    arsenal_muir = _payload_crag(p, "The Arsenal", "Muir Valley")
    assert arsenal_muir["counts"] == {"5.9": 1}       # preserve = the AREA+1 token


def test_recommender_payload_preserve_none_never_own_name_directly_under_area():
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    road = [c for c in p["crags"] if c["crag"] == "Roadside Crag"]
    assert len(road) == 1
    assert road[0]["preserve"] is None                # NOT "Roadside Crag" — the strict-between guard


def test_recommender_payload_deep_fallback_crag_keeps_preserve_not_none_or_own_name():
    # The Motherlode falls back to the wall at crag_offset=2, yet PMRP sits strictly between
    # area and leaf -> preserve must be "PMRP", never None and never "The Motherlode".
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    assert _payload_crag(p, "The Motherlode", "PMRP")["preserve"] == "PMRP"


def test_recommender_payload_identically_named_crags_stay_distinct_by_key():
    p = cd.recommender_payload(_recommender_frame(), area=RRG)
    arsenals = sorted(c["preserve"] for c in p["crags"] if c["crag"] == "The Arsenal")
    assert arsenals == ["Muir Valley", "PMRP"]        # two distinct entries grouped by crag_key
