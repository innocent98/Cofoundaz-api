"""Investor Readiness configuration (PRD 15.1; decisions D-IR-1..7, accepted 2026-11-05).

Weights, bands, verdict copy and the 70+ cap live here so a change is one reviewed edit and
every stored score carries the `READINESS_CONFIG_VERSION` it was computed under.
"""

READINESS_CONFIG_VERSION = 1

# D-IR-2. Sums to 100.
DIMENSION_WEIGHTS: dict[str, int] = {
    "traction": 25,
    "financials": 20,
    "product": 15,
    "market": 15,
    "team": 15,
    "story": 10,
}
DIMENSION_LABELS: dict[str, str] = {
    "traction": "Traction",
    "financials": "Financials",
    "product": "Product",
    "market": "Market",
    "team": "Team",
    "story": "Story",
}
# Order the PRD lists them in; the response keeps it.
DIMENSION_ORDER: tuple[str, ...] = ("team", "traction", "market", "product", "financials", "story")

READY_AT = 70
GETTING_CLOSE_AT = 40
CAP_SCORE = 69  # until every must-have exists

# (low_inclusive, band key, verdict). Verdict copy is the PRD wording; our rule strips dashes
# from output, so the em dash in "Not yet, and that's fine" is a comma.
BANDS: tuple[tuple[int, str, str], ...] = (
    (READY_AT, "ready", "You're ready to run a process."),
    (GETTING_CLOSE_AT, "getting_close", "Getting close. Close these gaps before outreach."),
    (0, "not_yet", "Not yet, and that's fine. Here's the shortest path."),
)

MIN_RECOMPUTE_INTERVAL_MINUTES = 15
GAP_LIMIT = 5
GAPS_PER_DIMENSION = 2

# Notification: at most one per day, on a band change or a rise of this many points.
NOTIFY_MIN_RISE = 5
