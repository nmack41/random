"""Smoke test for build_recommender (design §5): fixture parquet -> built HTML.

Run: .venv/bin/python -m pytest test_build_recommender.py -q
"""
import json
import re

import pandas as pd

import build_recommender as br

RRG = "Red River Gorge"


def _fixture_parquet(path):
    pd.DataFrame({
        "climb_id": ["1", "2", "3"],
        "grade_yds": ["5.12a", "5.13b", "5.9"],
        "is_sport": [True, True, True],
        "state_province": ["Kentucky", "Kentucky", "Kentucky"],
        "path_tokens": [
            ["USA", "Kentucky", RRG, "PMRP", "The Madness Cave"],
            ["USA", "Kentucky", RRG, "PMRP", "The Madness Cave"],
            ["USA", "Kentucky", RRG, "Muir Valley", "Sunnyside"],
        ],
    }).to_parquet(path)


def test_build_writes_html_with_valid_injected_json_and_no_placeholder(tmp_path):
    parquet = tmp_path / "tiny.parquet"
    _fixture_parquet(parquet)
    out = tmp_path / "out.html"

    br.main(parquet_path=str(parquet), output=str(out))

    html = out.read_text(encoding="utf-8")
    assert br.PLACEHOLDER not in html                  # no leftover placeholder
    assert "The Madness Cave" in html                  # a known crag name made it in

    m = re.search(r"const PAYLOAD = (\{.*?\});", html)  # extract the injected literal
    assert m, "payload assignment not found in output HTML"
    payload = json.loads(m.group(1))                    # round-trips as JSON (< is valid)
    assert payload["area"] == RRG
    assert payload["bands"] == ["5.9", "5.12", "5.13"]  # rank-ordered
    assert "The Madness Cave" in {c["crag"] for c in payload["crags"]}


def test_build_payload_counts_and_preserve_from_fixture(tmp_path):
    parquet = tmp_path / "tiny.parquet"
    _fixture_parquet(parquet)
    payload = br.build_payload(parquet_path=str(parquet))
    madness = next(c for c in payload["crags"] if c["crag"] == "The Madness Cave")
    assert madness["counts"] == {"5.12": 1, "5.13": 1}
    assert madness["total"] == 2
    assert madness["preserve"] == "PMRP"               # AREA+1 token, even though it fell back to wall
