# FE Integration Guide — Health Score, live signals (PRD Module 06 completion)

**Provenance.** The e2e journey `e2e/test_health_score.py` → `e2e/_captures/health_score/*.json` (fresh founder, assessed
the same minute), plus the local docker stack for the test founder (assessed the day before) →
`e2e/_captures/health_live/*.json`. Every excerpt below is verbatim, trimmed only to the named keys. This **extends**
`fe-integration-guide-health-score.md`; the gap list is `cofoundaz/docs/checklist/health-prd-gaps.md`.

## 0. What changed, in FE terms
The score now **moves with real work** in the app (roadmap, missions, validation, finance, e-signatures, team activity),
blended with the kickoff assessment, and refreshes on its own. Nothing to poll: re-read `GET /health-score` on page view.
- New founders see their assessment result first. The live part ramps in over the 14 days after the first assessment, so
  a brand-new score isn't dragged down by work there hasn't been time to do. Don't surface the ramp; it's internal.
- Recompute runs on relevant events (≤ every 15 min), immediately after any finance write, on view when older than 15
  minutes, and nightly.

## 1. `GET /health-score` (Overview, PRD 06.1)
New or changed fields (e2e `overview_ok.json`):
```json
{
  "status": "ok",
  "score": 31,
  "band": "at_risk",
  "delta_7d": 0,
  "summary": "You're at risk for idea stage. Team is carrying you; Legal is holding you back."
}
```
- `summary` now follows the PRD pattern. Render it as the AI summary line. (Evenly balanced dimensions read "Your five
  dimensions are evenly balanced.")
- Each `dimensions[]` item gains **`trend`**: the dimension's scores over the last up-to-8 recomputes, oldest first.
  **Draw the sparkline from this**, and delete the fixed per-band curve (that was decorative, not data). One point = a
  flat line or a dot.
```json
{"key": "team", "label": "Team", "score": 50, "band": "needs_work", "trend": [50]}
```
- `top_recommendations` (max 3) is now populated from live signals. This is the PRD's **"Top 3 fastest ways to raise
  your score"**. Each has **`href`**. Render "+{estimated_lift} pts est." and a **"Do it"** button → `href`:
```json
{
  "id": "568da4cc-e127-40df-afce-5c2e9cf4ab01",
  "dimension": "money",
  "key": "live.money.transactions_30d",
  "title": "Track your money in and out",
  "body": "Record this month's income and expenses so your runway and burn are based on real numbers.",
  "estimated_lift": 10,
  "effort": "low",
  "status": "pending",
  "priority": 1,
  "href": "/finance"
}
```
Live (test founder): top 3 = "Track your money in and out" (+10, /finance), "Get your key agreements signed" (+10,
/documents/signatures), "Close out your next roadmap tasks" (+5, /roadmap).

## 2. `GET /health-score/dimensions/{dim}` (PRD 06.2)
Each `signals[]` row is now a full PRD row. Render columns **Signal** (`label`), **Current** (`value` + `unit`), **Target
for stage** (`target`), **Contribution** (`+{contribution} pts`), **Source** (link to `href`). The first row is always the
assessment baseline. e2e `dimension_money.json`:
```json
[
  {
    "key": "assessment.money",
    "label": "Kickoff assessment",
    "value": 43.0,
    "unit": "score",
    "target": 100,
    "contribution": 8.6,
    "source": "assessment",
    "href": "/assessment",
    "source_ref": "assessment:fc02abb3-0a7c-49f1-8a77-5d81f817e828"
  },
  {
    "key": "money.transactions_30d",
    "label": "Transactions tracked (30 days)",
    "value": 0.0,
    "unit": "count",
    "target": 3,
    "contribution": 0.0,
    "source": "live",
    "status": "ok",
    "href": "/finance",
    "source_ref": "/finance"
  }
]
```
- `unit` ∈ `score | % | count | months | days | events`. Format accordingly ("0 of 3", "40%", "7 months").
- The runway row (`money.runway_live`) has **`status: "not_burning"`** with `value: 999` for a profitable startup. Show
  "Profitable" or "Not burning", **never "999 months"**. It's absent when there's no finance data.
- `contribution` is points of the overall score; all rows sum to about the score (rounding).
- A live row with `contribution` 0 is real (a signal at 0, or the day-one ramp). Keep it: it shows where the points will come from.

Signals per dimension: Product (roadmap tasks done %, mission completion 7 days) · Market (assumptions tested, interviews
30 days) · Financial (transactions 30 days, runway) · Legal (agreements signed) · Team (active members, activity 7 days,
mission streak).

## 3. History (PRD 06.3)
- `GET /health-score/history?range=` now accepts **`365d`** (keep `7d`/`30d`/`90d`/`all`). Show the PRD picker
  30/90/365 days. Each point gains `trigger` (what caused the recompute).
- **New** `GET /health-score/history/markers?range=` → annotated markers for the chart (same `range` values; 422 on an
  unknown range, "Use 7d, 30d, 90d, 365d, or all."). e2e `history_markers_365d.json`:
```json
{
  "data": [
    {
      "at": "2026-10-18T01:03:42.138134+00:00",
      "label": "Assessment completed",
      "action": "assessment.completed"
    }
  ],
  "meta": null
}
```
Labels: Assessment completed · Business plan generated · Milestone completed · Roadmap re-planned · Teammate joined ·
Agreement signed. Draw them as markers at `at` with the label as a tooltip.

## 4. Benchmarks (PRD 06.4): real percentiles now
Below the minimum cohort it's unchanged (e2e and live: `insufficient_data`, because the local cohort is too small):
```json
{
  "status": "insufficient_data",
  "cohort": {"stage": "idea", "industry": "Fintech"},
  "min_cohort_size": 5,
  "percentiles": null
}
```
With **at least 10 other** eligible startups (same stage, same industry case-insensitively, a verified creator, workspace at least 7
days old) it returns `status: "ok"`, `cohort.size_at_least` (rounded down to a multiple of 5), and `percentiles` for
`overall` and each dimension: `{p25, p50, p75, you, your_percentile}`. p25/p50/p75 are **bucketed to 5 points** for
privacy. Render a bar per row with your score against the p25 to p75 band, and the copy "Better than {your_percentile}%
of similar startups". ❌ **Not verified live** (the local cohort is too small); the shape comes from unit tests
`test_benchmarks_bucketed_percentiles_from_eligible_peers` and `test_benchmarks_ignore_self_new_and_unverified_peers`.
`min_cohort_size` is now **10**: use it in the empty-state copy.

## 5. Recommendations (PRD 06.5)
Every recommendation now has `href`. **"Start"** = `POST /health-score/recommendations/{id}/accept` and then navigate to
`href`. **"Dismiss"** is unchanged. Dismissed stays dismissed across recomputes.

## 6. Notifications
`healthscore.dropped` → **"Your Health Score dropped {n} points, worth a look"**, body "See why" (deep-link `/health`).
**New** `healthscore.record` → **"New personal best: {score}. Keep going."**

## 7. Error handling (FE gap row 7)
`GET /health-score` failing is an **error**, not "pending assessment". Only `status: "pending_assessment"` means the
founder hasn't been assessed. On a network/5xx error show "Couldn't load your Health Score. Retry".

## 8. Verification
| Behaviour | Live? | Source |
|---|---|---|
| PRD summary line, dimension `trend`, top 3 with `href` | ✅ | e2e `overview_ok.json`; local `health_live/overview.json` |
| Drill-down rows with label/target/contribution/href | ✅ | e2e `dimension_money.json`; local `health_live/dimension_market.json` |
| Markers + 365d range | ✅ | e2e `history_markers_365d.json` |
| Stale-on-read refresh persists + `computed_at` advances | ✅ | local (computed_at moved to 03:02Z; 14 signal rows) + regression test |
| Score moves with real work; contributions sum to score | ❌ unit | `test_recompute_moves_score_with_real_work_and_writes_contributions` |
| Ramp 0 → 50% over 14 days | ❌ unit | `test_live_share_ramps_over_fourteen_days` |
| Event triggers, throttle, nightly | ❌ unit | `test_events_trigger_a_refresh`, `test_refresh_is_throttled_unless_forced`; scheduler wiring |
| Benchmarks percentiles | ❌ unit | `test_benchmarks_compute_real_percentiles` |
| Runway `not_burning` = 999 | ✅ | e2e `e2e/test_finance_runway.py` asserts `status: "not_burning"` |
