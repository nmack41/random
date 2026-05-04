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
