"""Fictional 'AcmeCo' org used to populate fake_data/groups.db.

Three snapshots over two weeks with light churn:
- snapshot 1 → 2: paul.quinn (u-016) joins Sales; nick.owen (u-014) leaves
- snapshot 2 → 3: mia.nelson (u-013) is promoted from Engineering to Sales Leadership

Multi-group user: grace.hill (u-007) sits in both Marketing Analysts and
Sales Leadership across all three snapshots.
"""

DOMAIN = "acmeco"

# group_id -> group_name
GROUPS = {
    "g-sales-lead":  "Sales Leadership",
    "g-sales-reps":  "Sales Reps",
    "g-marketing":   "Marketing Analysts",
    "g-engineering": "Engineering",
    "g-all":         "All Employees",
}

# user_id -> (user_name, site_role)
USERS = {
    "u-001": ("alice.brennan", "Creator"),
    "u-002": ("bob.choi",      "Creator"),
    "u-003": ("carol.davis",   "Explorer"),
    "u-004": ("david.evans",   "Explorer"),
    "u-005": ("eve.foster",    "Explorer"),
    "u-006": ("frank.garcia",  "Explorer"),
    "u-007": ("grace.hill",    "Explorer"),
    "u-008": ("henry.ito",     "Viewer"),
    "u-009": ("iris.jones",    "Viewer"),
    "u-010": ("jack.kim",      "Viewer"),
    "u-011": ("kate.lewis",    "Viewer"),
    "u-012": ("leo.martin",    "Viewer"),
    "u-013": ("mia.nelson",    "Viewer"),
    "u-014": ("nick.owen",     "Viewer"),
    "u-015": ("olivia.park",   "Viewer"),
    "u-016": ("paul.quinn",    "Viewer"),
}

# workbook_id -> (workbook_name, project_name, [group_ids_with_access])
# Permissions are stable across all snapshots.
WORKBOOKS = {
    "wb-001": ("Sales Pipeline Q1",        "Sales",       ["g-sales-lead", "g-sales-reps", "g-all"]),
    "wb-002": ("Revenue Forecast",         "Sales",       ["g-sales-lead", "g-all"]),
    "wb-003": ("Account Health Dashboard", "Sales",       ["g-sales-lead", "g-sales-reps"]),
    "wb-004": ("Win/Loss Analysis",        "Sales",       ["g-sales-lead", "g-all"]),
    "wb-005": ("Campaign Performance",     "Marketing",   ["g-marketing",  "g-all"]),
    "wb-006": ("Lead Scoring",             "Marketing",   ["g-marketing",  "g-all"]),
    "wb-007": ("Brand Sentiment",          "Marketing",   ["g-marketing"]),
    "wb-008": ("Service Latency",          "Engineering", ["g-engineering"]),
    "wb-009": ("Deploy Frequency",         "Engineering", ["g-engineering"]),
    "wb-010": ("Error Budget",             "Engineering", ["g-engineering", "g-all"]),
    "wb-011": ("Stranded Dashboard",       "Marketing",   []),
}

# view_id -> (view_name, workbook_id, explicit_rules)
#
# explicit_rules is None    => no explicit view-level group rules; view inherits parent workbook grants
# explicit_rules is []      => (not used) — distinguish "no rules" from "rules with no Allow" via Deny entry
# explicit_rules is [(gid, "Allow"|"Deny"), ...] => any explicit Read rule blocks inheritance.
#                                                   Only "Allow" entries are surfaced as group access;
#                                                   "Deny" entries are present to block inheritance only.
#
# Coverage required by spec:
#   wb-001: pure inheritance (None)                       -> v-001-1, v-001-2
#   wb-002: a Deny-only view (audit-safe zero)            -> v-002-2
#   wb-003: mixed Allow + Deny on same view               -> v-003-1
#   wb-005: explicit Allow override + plural-diff label   -> v-005-1, v-005-3
#   wb-007: stale group reference (g-removed)             -> v-007-1
#   wb-008: explicit Allow that mirrors workbook (no-op)  -> v-008-2
#   wb-009: zero views                                    -> intentionally absent
#   wb-011: zero-grant workbook with view-level grants    -> v-011-1
VIEWS = {
    "v-001-1": ("Q1 Pipeline by Region",     "wb-001", None),
    "v-001-2": ("Q1 Forecast Detail",        "wb-001", None),
    "v-002-1": ("Quarterly Roll-up",         "wb-002", None),
    "v-002-2": ("Restricted Forecast",       "wb-002", [("g-sales-reps", "Deny")]),
    "v-003-1": ("Account Detail (Curated)",  "wb-003", [("g-sales-lead", "Allow"), ("g-sales-reps", "Deny")]),
    "v-004-1": ("Win Reasons",               "wb-004", None),
    "v-005-1": ("Campaign A Funnel",         "wb-005", [("g-marketing",  "Allow")]),
    "v-005-2": ("Campaign B Funnel",         "wb-005", None),
    "v-005-3": ("Campaign C Funnel",         "wb-005", [("g-marketing",  "Deny")]),
    "v-006-1": ("Lead Score Distribution",   "wb-006", None),
    "v-007-1": ("Sentiment Daily",           "wb-007", [("g-removed",    "Allow")]),
    "v-008-1": ("p99 Latency",               "wb-008", None),
    "v-008-2": ("p50 Latency",               "wb-008", [("g-engineering", "Allow")]),
    "v-010-1": ("Burn Rate",                 "wb-010", None),
    "v-011-1": ("Stranded View",             "wb-011", [("g-marketing",  "Allow")]),
}

# Each snapshot: (timestamp_iso, {group_id: [user_ids]})
SNAPSHOTS = [
    ("2026-04-20T09:00:00", {
        "g-sales-lead":  ["u-001", "u-007", "u-015"],
        "g-sales-reps":  ["u-003", "u-004", "u-008", "u-009", "u-010", "u-014"],
        "g-marketing":   ["u-005", "u-007", "u-012"],
        "g-engineering": ["u-002", "u-006", "u-011", "u-013"],
        "g-all":         ["u-001", "u-002", "u-003", "u-004", "u-005", "u-006", "u-007",
                          "u-008", "u-009", "u-010", "u-011", "u-012", "u-013", "u-014", "u-015"],
    }),
    ("2026-04-27T09:00:00", {
        "g-sales-lead":  ["u-001", "u-007", "u-015"],
        "g-sales-reps":  ["u-003", "u-004", "u-008", "u-009", "u-010", "u-016"],
        "g-marketing":   ["u-005", "u-007", "u-012"],
        "g-engineering": ["u-002", "u-006", "u-011", "u-013"],
        "g-all":         ["u-001", "u-002", "u-003", "u-004", "u-005", "u-006", "u-007",
                          "u-008", "u-009", "u-010", "u-011", "u-012", "u-013", "u-015", "u-016"],
    }),
    ("2026-05-04T09:00:00", {
        "g-sales-lead":  ["u-001", "u-007", "u-013", "u-015"],
        "g-sales-reps":  ["u-003", "u-004", "u-008", "u-009", "u-010", "u-016"],
        "g-marketing":   ["u-005", "u-007", "u-012"],
        "g-engineering": ["u-002", "u-006", "u-011"],
        "g-all":         ["u-001", "u-002", "u-003", "u-004", "u-005", "u-006", "u-007",
                          "u-008", "u-009", "u-010", "u-011", "u-012", "u-013", "u-015", "u-016"],
    }),
]
