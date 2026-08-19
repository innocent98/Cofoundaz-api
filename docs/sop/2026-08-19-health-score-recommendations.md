# SOP — Health Score: rule-based recommendation generation & reconciliation

**What shipped** — `generate_recommendations(db, startup_id, dimension_scores)`
(`app/services/health_score/recommendations.py`), wired into
`recompute_health_score` (`app/services/health_score/service.py`) so every
recompute also regenerates the startup's recommendation list from
`RECOMMENDATION_CATALOG`. This is Task 5 of the 11-task Health Score module plan
(`.superpowers/sdd/2026-08-19-health-score/`); Tasks 1–4 shipped the models,
migration, config/scoring, and the recompute pipeline this hooks into.

## Why

Task 4 left a comment marker (`# 4. Recommendations`) in `recompute_health_score`
for this task to fill: a founder's health score alone doesn't say what to do
next. The catalog (`RECOMMENDATION_CATALOG` in `config.py`) already carries
per-dimension, threshold-gated advice entries; this task turns "score dropped
below threshold X" into actual `HealthRecommendation` rows, and — the harder
part — keeps those rows correct across repeated recomputes without ever
overwriting a founder's own accept/dismiss decision.

## How

**Ranking.** For each dimension whose current score is below a catalog entry's
`triggers_below`, compute `priority_val = (triggers_below − score) ×
DIMENSION_WEIGHTS[dim]`. Candidates are sorted by `(priority_val,
estimated_lift)` descending; sort position becomes the integer `priority`
(1..N) written to each row.

**Reconciliation, keyed on catalog `key`** (not row id) — the crux of this
task, since `generate_recommendations` runs on every recompute and must not
duplicate rows or clobber user decisions:
- existing **pending** row whose key is no longer a candidate (dimension
  recovered) → **deleted** (auto-generated + unacted = ephemeral).
- candidate key with no existing row → **inserted** as `pending`.
- candidate key already **dismissed** → **skipped** entirely, including no
  priority/lift refresh — a user's "no" is never revived.
- candidate key already **pending or accepted** → kept; only `priority` and
  `estimated_lift` are refreshed, status is untouched.
- **accepted** rows are never deleted regardless of whether their dimension
  recovered (only `pending` rows are subject to the recovery-delete).

The UNIQUE index on `(startup_id, key)` backs this as a hard guarantee, but
the reconciliation logic itself never attempts a duplicate insert — it always
checks `existing` (a `key → row` dict built once per call) before deciding
insert vs. update vs. skip.

**Transaction ownership.** `generate_recommendations` takes the caller's `db`
session and ends with `db.flush()` — no commit. It mutates inside whatever
transaction `recompute_health_score` is already in, matching the pattern of
the other recompute steps (signals delete/replace, score upsert, history
append).

**Import placement.** `recompute_health_score` imports `generate_recommendations`
with a function-local import (not module-top) specifically to avoid a
`service.py` ↔ `recommendations.py` import cycle, per the task brief. All other
imports in `recommendations.py` are module-top (ruff E402-clean).

## What's involved

- `app/services/health_score/recommendations.py` (new) — `generate_recommendations`.
- `app/services/health_score/service.py:91-93` — replaced the Task-4 comment
  marker with the function-local import + call, before `db.flush()`.
- `tests/services/test_health_recommendations.py` (new) — 6 tests: generate for
  weak dimensions, none when all strong, dedupe on regeneration, never
  resurrect a dismissed key, accepted rows left untouched (status), recovered
  pending rows deleted.
- Consumes (unchanged): `RECOMMENDATION_CATALOG` / `DIMENSION_WEIGHTS`
  (`config.py`), `HealthRecommendation` / `RecommendationStatus`
  (`app/db/models/health_score.py`, `app/db/models/enums.py`).

## Verification

- RED: `pytest tests/services/test_health_recommendations.py -q` failed with
  `ModuleNotFoundError` before the module existed.
- GREEN: `pytest tests/services/test_health_recommendations.py
  tests/services/test_health_recompute.py -q` → 13 passed (the existing
  recompute tests still pass now that recompute also generates recommendations).
- Full suite: `pytest -q` → 237 passed.
- `ruff check app/services/health_score tests/services/test_health_recommendations.py`
  → clean. `mypy app/services/health_score` → clean (5 source files).
- Self-review walked each reconciliation branch (delete-recovered-pending only
  ever touches `pending` rows; dismissed keys are skipped, not revived;
  accepted rows survive with status intact) against concrete WEAK/STRONG
  dimension-score fixtures.

## Operate / roll back

No migration, no config, no new env vars. Purely additive service-layer logic
gated behind the existing recompute call path — reverting the two changed/new
files (`service.py`'s 3-line diff, deleting `recommendations.py`) fully rolls
this back; no data cleanup needed since `HealthRecommendation` rows it writes
are regenerated (and pruned) idempotently on the next recompute.

## Follow-ups

- No API surface yet for a founder to accept/dismiss a recommendation (rows
  can currently only reach `accepted`/`dismissed` via direct DB/factory
  writes in tests) — that's later tasks in the same 11-task plan (endpoints
  for list/accept/dismiss/overview, per `progress.md`).
- The brief's wording says "existing accepted rows are ALWAYS left
  untouched" alongside "refresh priority/estimated_lift" for
  pending-or-accepted rows; the reference implementation (and this one)
  refreshes `priority`/`estimated_lift` on accepted rows too — only `status`
  is truly immutable. Worth confirming that's the intended read before any
  UI depends on an accepted recommendation's priority staying fixed.
