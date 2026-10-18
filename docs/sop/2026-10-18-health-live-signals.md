# SOP — Health Score live signals (PRD Module 06 completion, backend)

**What shipped (one line):** The Health Score is now computed from live signals across every hub, blended with the
assessment, refreshed on events, money writes, stale views and nightly. With it: real recommendations with deep links,
real dimension sparklines, PRD summary copy, a full drill-down row (target + source link), history markers and a 365d range,
real benchmark percentiles, and PRD notification copy. Branch `feat/health-live-signals` (stacked on #135) → PR.

## Why
Walkthrough against PRD Module 06 (`cofoundaz/docs/checklist/health-prd-gaps.md`): the score was **assessment-only**.
Nothing a founder did in the app moved it, recommendations were empty for most founders, sparklines were decorative,
and benchmarks were a stub that never computed percentiles.

## How (decisions D-HEA-1, 1b, 2–4)
| Decision | Why | Rejected |
|---|---|---|
| Dimension = assessment baseline blended with live signals (target share 50%) | Self-report stays meaningful; real work moves the score | Live-only (punishes new founders); assessment-only (the old bug) |
| **D-HEA-1b ramp**: live share 0 on the first assessment day → 50% after 14 days | A just-assessed founder isn't halved by signals they haven't had time to move | Full 50% from day one (score fell 60 → 30 in tests) |
| Declarative signal table (`live_signals.py`): value, stage-scaled target, href, recommendation | One place to add signals (Legal module will add more) | Ad-hoc queries per dimension |
| Signal score = `min(value/target, 1)×100`; `None` = not applicable (runway without finance data) | Simple, explainable, capped | Unbounded scores |
| Contributions in points, summing to the score | "Every point traces to something real" (PRD) | — |
| Live recs from weakest signals by potential lift, merged with catalog; dismissed stay dismissed | Always-actionable top 3 with a deep link | Catalog-only |
| AI rec rewrite enqueued only when new recs appear or on assessment | Live recomputes would otherwise spend LLM tokens on every change | Enqueue every recompute |
| Triggers: events (throttled 15 min) · finance writes (forced, immediate) · stale-on-read (>15 min) · nightly | PRD cadence; money writes must show instantly | Polling |
| History point only when the score/dimensions change (assessment always) | No flat-line spam from frequent recomputes | — |
| Benchmarks: p25/p50/p75 + own percentile, only with ≥5 peers | Real comparison, no reverse identification | The old always-`insufficient_data` stub |

## Bugs fixed along the way
- Stale-on-read refresh was **rolled back** (identity-check commit). Now always commits. Regression-tested.
- `health_scores.updated_at` never advanced (`ON CONFLICT DO UPDATE` bypasses the ORM `onupdate`). Now set explicitly.

## What's involved
**New** `app/services/health_score/live_signals.py`, `app/services/health_score/triggers.py`; `service.py` (blend,
ramp, live recs, refresh, overview/dimension/history/markers/benchmarks); `recommendations.py` (live merge, created count);
`app/services/finance/runway.py` (money writes → forced refresh; the old display-only signal is removed, and its key
`money.runway_live` is reused by the scoring signal); `app/api/v1/endpoints/health_score.py` (`/history/markers`, `365d`);
`app/api/v1/api.py` + `app/worker/__main__.py` (register triggers); `app/worker/scheduler.py` + `handlers/scheduled.py`
(nightly `scheduled.health.recompute`, `HEALTH_NIGHTLY_HOUR`); notifications registry/categories. Tests: new
`tests/services/test_health_live.py` (29); rewritten `tests/services/test_runway_health_signal.py`; updated health/recompute/
invoices/expenses tests. e2e: `test_finance_runway.py` (assessment step, new runway semantics), `test_health_score.py`
(markers). No migration.

## Verification
Fresh-DB pytest under UTC 2301+ passed, 97.55%; static gates; e2e 69/69; live on local docker (`e2e/_captures/health_live/`).

## Operate / roll back
**Restart the worker** (new handlers + triggers). No migration. Roll back: revert the PR (scores fall back to assessment-only on
the next recompute).

## Follow-ups (written up for the user in the handoff)
- Tune the 50% share, the 14-day ramp and the per-stage targets once real usage data exists.
- More Legal signals when the Legal module ships. Pipeline/Sales signals when Sales ships.
