# SOP — KPI snapshots: nightly capture, tile trends, threshold alerts

**What shipped (one line):** A nightly job stores one value per KPI per workspace (`kpi_snapshots`, PRD 02.1). The
dashboard tiles gain a week-over-week `delta` and a 14-day `series` for sparklines, and threshold crossings
notify the right roles. Branch `feat/kpi-snapshots` (stacked on #130) → PR.

## Why
Dashboard gap #18 and decision D-DASH-4: the PRD specifies `kpi_snapshots` (nightly 02:00), deltas/sparklines on
each StatCard, and "{Metric} crossed {threshold}: now {value}" alerts. None existed.

## How
| Decision | Why | Rejected |
|---|---|---|
| `scheduled.kpi.snapshot` on the existing scheduler (claim-once `ScheduledRun`, `KPI_SNAPSHOT_HOUR=2` in `SCHEDULER_TIMEZONE`) | Same once-per-period guarantee as missions/quarterly | A new cron mechanism |
| Upsert per (startup, metric, day) | The job is idempotent and a re-run updates the value | Append-only duplicates |
| Snapshot only real figures (revenue `ok`, runway `ok`/`out_of_cash`, CTR `ok`, tasks always) | Never chart a placeholder as 0 | Snapshot nulls as 0 |
| `trend` on visible tiles only, none on restricted ones | History would leak a value the role can't see | — |
| `delta` vs the snapshot **exactly** 7 days ago, else null | Honest "vs last week". A first week shows no delta | Nearest-point fudge |
| Alert rules (defaults): runway < 3 months, revenue past ₦100K/₦1M/₦10M, CTR < 1%. Recipients by role (`_kpi_audience`) | 6-month runway is already alerted (`finance.runway.low`). Alerts only to roles that can see the metric | Everyone in the workspace |
| Alerts only on crossing vs the previous snapshot | No daily repeats while a metric stays below a line | Alert on every capture |

## What's involved
Migration `alembic/versions/0049_kpi_snapshots.py` (new table, FK indexed, unique per day); `app/db/models/kpi_snapshot.py`;
`app/services/dashboard/kpi_history.py` (trend), `app/services/dashboard/kpi_snapshots.py` (capture + rules);
`app/services/dashboard/kpis.py` (`trend` on tiles), `app/services/dashboard/service.py` (`tasks_trend`; `tasks_done_this_week` public);
`app/worker/scheduler.py` + `app/worker/handlers/scheduled.py`; `app/services/notifications/registry.py` (`kpi.threshold.crossed`);
`app/core/config.py` (`KPI_SNAPSHOT_HOUR`). Tests `tests/services/dashboard/test_kpi_snapshots.py` (15). Guide: §7a/7b of
`docs/fe-integration-guide-dashboard-kpis.md`.

## Verification
- Fresh-DB pytest, gates and e2e on the PR tree (see the PR). New modules 100% covered.
- Live: after a worker restart, the real scheduler captured today's snapshots. With one synthetic prior point (runway 4),
  the founder got "Runway crossed 3 months: now 1.5 months" and the mentor got nothing. The tile `trend.series` showed both points.
  Seeded transactions and snapshot rows were removed afterwards.

## Operate / roll back
`alembic upgrade head` (new table) and **restart the worker**. `KPI_SNAPSHOT_HOUR` env overrides 02:00. Roll back: revert, then
`alembic downgrade 0048_briefing_structured`.

## Follow-ups
- Per-workspace timezone (all scheduled jobs use `SCHEDULER_TIMEZONE`).
- User-configurable thresholds. Pipeline snapshots once Sales exists.
