"""Build the self-contained RRG grade-based crag finder HTML (design §4.3).

Runs the tested climbing_density pipeline, builds the recommender payload, injects it
HTML-safely into recommender_template.html, and writes rrg_grade_recommender.html.

Run:  .venv/bin/python build_recommender.py
"""
import json
from pathlib import Path

import climbing_density as cd

# --- parameters (edit here to retarget) ------------------------------------- #
PARQUET_PATH = "data/usa-sport-climbs.parquet"
STATE = "Kentucky"
AREA = "Red River Gorge"
CRAG_OFFSET = 2                          # the single place the crag level is set (design §4.3)
TEMPLATE = "recommender_template.html"
OUTPUT = "rrg_grade_recommender.html"
PLACEHOLDER = "__PAYLOAD_JSON__"


def build_payload(parquet_path=PARQUET_PATH, state=STATE, area=AREA, crag_offset=CRAG_OFFSET):
    """Run the design §3 pipeline and return the recommender payload dict.

    Reuses load_climbs' missing-file error and filter_area's friendly "closest names" error.
    """
    df = cd.load_climbs(parquet_path)
    df = cd.filter_sport(df)
    df = cd.filter_area(df, state=state, area=area)
    df = cd.add_grade_columns(df)
    df = cd.derive_hierarchy(df, area=area, crag_offset=crag_offset)
    return cd.recommender_payload(df, area=area)


def render_html(payload, template_path=TEMPLATE, placeholder=PLACEHOLDER):
    """Inject the payload into the template, HTML-safely.

    Escapes every `<` to its unicode form so a stray `</script>` in a future crag name can't
    break out of the <script> block. `\\u003c` is valid in both JSON and the JS string it lands
    in, and `<` only ever appears inside JSON string values, so this never corrupts structure.
    Trusted CC0 data — this is cheap insurance, not a security boundary (design §4.3).
    """
    template = Path(template_path).read_text(encoding="utf-8")
    if placeholder not in template:
        raise ValueError(f"Template {template_path!r} has no {placeholder!r} placeholder.")
    injected = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return template.replace(placeholder, injected)


def main(parquet_path=PARQUET_PATH, output=OUTPUT):
    payload = build_payload(parquet_path)
    Path(output).write_text(render_html(payload), encoding="utf-8")
    n_crags = len(payload["crags"])
    n_routes = sum(c["total"] for c in payload["crags"])
    if n_crags == 0:
        print(f"WARNING: no crags found for {AREA!r} — wrote {output} showing the empty state.")
    else:
        print(f"Wrote {output}: {n_crags} crags, {n_routes} sport routes in {AREA}.")
    return output


if __name__ == "__main__":
    main()
