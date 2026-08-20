# SOP — Health Score (Module 06)

**What shipped** — the whole Health Score module: 7 read/write endpoints under
`/api/v1/health-score`, a pure scoring/config layer, an inline
recompute-on-assessment-complete pipeline (replacing the `healthscore.recalculate`
job stub), rule-based recommendation generation with durable user decisions,
a cohort-gated benchmarks empty-state, and 4 new tables (migration `0005`).
This is the **single consolidated SOP for the whole module** — three
per-task SOPs (`2026-08-19-health-score-overview-endpoint.md`,
`2026-08-19-health-score-recommendations.md`,
`2026-08-19-health-score-dimension-history-endpoints.md`) were written
during Tasks 5/7/8 and are superseded and deleted by this one, per the
same one-doc-per-feature pattern `docs/sop/2026-08-16-assessment.md` used
for the Assessment module.

Commits (branch `feat/health-score`): `4a579b3` (enums/models/factories) ..
`366bbe1` (black formatting) for Tasks 1–10, plus this task's
`e2e/test_health_score.py` / SOP / FE guide / checklist commit (Task 11 —
final task of the 11-task plan, `.superpowers/sdd/2026-08-19-health-score/`).

## Why

Module 07 (Assessment) gives a founder a provisional per-dimension score the
moment they finish the kickoff questionnaire, but `overall_provisional` is
explicitly just that — provisional, assessment-only, with no history, no
"why," and no "what to do next." Module 06's job: turn that provisional
score into a durable, explainable 0–100 Health Score with 5 dimension
sub-scores, a trend history, ranked recommendations a founder can act on,
and a benchmarks surface for later cohort comparison — the single number a
founder checks to know "am I on track."

## How

**Inline recompute + lazy-on-read, no worker.** `recompute_health_score(db,
startup, *, trigger)` (`app/services/health_score/service.py`) is the one
function that writes a Health Score. It is called from two places:
synchronously inside `complete_assessment`
(`app/services/assessment/service.py:199`, `trigger="assessment_complete"`)
right after the assessment-completion claim and `AssessmentResult` insert —
same transaction, no job queue round-trip — and lazily from `get_overview`
(`trigger="lazy_read"`) if a `GET /health-score` finds a completed
assessment but no `HealthScore` row yet (e.g. data written through some
other path). Both call sites are idempotent, upsert-based, and safe to
re-run.

**Signals as a replaced projection, not an append-only log.** Every recompute
deletes and rewrites `health_signals` for the startup (currently one row per
dimension, `key="assessment.{dim}"`, sourced from the assessment's
`dimension_scores`) — it is always the *current* explanation for the score,
not a history. `health_score_history` is the append-only ledger instead:
one row per recompute, carrying `score`, the full `dimension_scores`
snapshot, a `delta` (vs. the closest point ≥7 days old, falling back to the
earliest point if none is that old), the `trigger`, and `config_version`.

**Deterministic weighted scoring, versioned config.**
`app/services/health_score/config.py` holds `DIMENSION_WEIGHTS` (0.20 each,
product/market/money/legal/team), `BANDS` (`at_risk` 0–39, `needs_work`
40–59, `healthy` 60–79, `thriving` 80–100), `DIMENSION_LABELS` (`money` →
"Financial" is the only internal-key-vs-display-label divergence; the other
four keys are also their own labels), `MIN_COHORT_SIZE` (5), and
`RECOMMENDATION_CATALOG`. `HEALTH_CONFIG_VERSION` is stamped onto every
`HealthScore` and `HealthScoreHistory` row so a change to weights/bands/catalog
later is attributable to the config version that produced a given score, not
silently reinterpreted. `app/services/health_score/scoring.py` is pure:
`weighted_overall` (clamped 0–100) and `band_for`.

**Rule-based recommendations, reconciled by catalog key, not row id**
(`app/services/health_score/recommendations.py`, called from
`recompute_health_score` on every recompute). Each catalog entry
(`RECOMMENDATION_CATALOG[dim]`) carries a `triggers_below` score threshold;
candidates below threshold are ranked by `(triggers_below − score) ×
DIMENSION_WEIGHTS[dim]`, then `estimated_lift`, both descending, and that
rank becomes the row's `priority`. Reconciliation on every recompute:
existing **pending** row whose key stopped being a candidate (dimension
recovered) → deleted (auto-generated + unacted is ephemeral); new candidate
key → inserted `pending`; **dismissed** key → skipped entirely, never
revived (a user's "no" is durable); **pending or accepted** key → kept,
only `priority`/`estimated_lift` refreshed, `status` untouched. A UNIQUE
index on `(startup_id, key)` backs the identity but the logic itself never
attempts a duplicate insert.

**Benchmarks: honest cohort-size gate, no aggregation pipeline yet.**
`get_benchmarks` counts peer `HealthScore` rows sharing the startup's
`stage` + `industry`; below `MIN_COHORT_SIZE` (5) it returns
`status: "insufficient_data"` with the cohort descriptor and the gate. The
≥-cohort-size branch exists in the code but currently returns the identical
`insufficient_data` shape — real percentile aggregation is deliberately
deferred (see Follow-ups), so both branches are honest about not having real
peer data yet rather than fabricating percentiles.

**Uniform 404s everywhere a resource id is looked up.** `get_dimension`
404s on an unknown dimension key (validated against `DIMENSION_LABELS`
before any query runs) and `resolve_recommendation` 404s whenever
`(id, startup_id)` doesn't match a row — this is one query that can't
distinguish "doesn't exist" from "exists but belongs to another workspace,"
which is the point: it never leaks cross-tenant existence. Accept/dismiss
are idempotent on a same-status repeat and 409 `RECOMMENDATION_RESOLVED` on
a cross-transition (accepted↔dismissed) — `RecommendationResolved`
(`app/core/errors.py`).

**`healthscore.initialize` is still enqueued — intentionally, as an
unconsumed stub.** `complete_onboarding` (`app/services/onboarding/complete.py:44`)
still enqueues `healthscore.initialize` the same way it always has. This
module does **not** retire or consume it: no worker drains it, and nothing
in the Health Score module needs it to run anything, because the module's
actual empty-state design is "no `HealthScore` row until the first
assessment completes" (`get_overview` returns `pending_assessment` for that
case) — the job was never load-bearing for that state, so leaving it
enqueued-but-unconsumed costs nothing and avoids churning onboarding's
`job_ids` response shape and its own E2E for a job type this module never
needed to touch. **Only `healthscore.recalculate`** — the job
`complete_assessment` used to enqueue on every assessment completion — was
replaced, by the inline `recompute_health_score` call
(`app/services/assessment/service.py:197-199`); `roadmap.replan`
(same call site) is untouched and still queues for a future Module 05
worker. *(Correction: an earlier per-task SOP for this module claimed
`healthscore.initialize` was "never enqueued anywhere" / "retired" — that
was inaccurate; the job **is** enqueued at onboarding-complete and is simply
not consumed by anything, by design. This document is the corrected,
authoritative version.)*

## What's involved

**Data model / migration**
- `alembic/versions/0005_health_score.py` — 4 new tables, no lock on any
  existing table (autogenerated from the Task 1 ORM models, so
  Alembic-built and `metadata.create_all`-built schemas stay in parity):
  - `health_scores` (`startup_id` UNIQUE — one live score per startup).
  - `health_score_history` (+ composite index `(startup_id, created_at)`) —
    append-only.
  - `health_signals` (+ index on `startup_id`) — replaced-in-full each
    recompute.
  - `health_recommendations` (+ UNIQUE `(startup_id, key)`).
  All FKs `startup_id → startups.id` are `ondelete="CASCADE"`.
- `app/db/models/health_score.py` — `HealthScore`, `HealthScoreHistory`,
  `HealthSignal`, `HealthRecommendation`.
- `app/db/models/enums.py` — `RecommendationEffort` (`low`/`medium`/`high`),
  `RecommendationStatus` (`pending`/`accepted`/`dismissed`).

**Endpoints** (all under `/api/v1/health-score`, `app/api/v1/endpoints/health_score.py`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/health-score` | any active member | `pending_assessment` or `ok` |
| GET | `/api/v1/health-score/dimensions/{dim}` | any active member | 404 on unknown `dim` |
| GET | `/api/v1/health-score/history?range=` | any active member | `7d`/`30d`/`90d`/`all`, 422 on bad value |
| GET | `/api/v1/health-score/benchmarks` | any active member | cohort-size gate |
| GET | `/api/v1/health-score/recommendations?status=` | any active member | defaults to `pending` only |
| POST | `/api/v1/health-score/recommendations/{rec_id}/accept` | founder only | idempotent; 409 on cross-transition |
| POST | `/api/v1/health-score/recommendations/{rec_id}/dismiss` | founder only | idempotent; 409 on cross-transition |

Read routes gate on `require_workspace` (any active member); the two
resolve-recommendation routes gate on `require_role(founder)`, same
founder-only pattern as write routes elsewhere in the codebase (e.g.
assessment's `POST`/`PATCH` routes). `app/api/v1/api.py` registers the
router at `prefix="/health-score"`.

**Services / config / errors**
- `app/services/health_score/service.py` — `recompute_health_score`,
  `latest_completed_result`, `get_overview`, `get_dimension`, `get_history`,
  `get_benchmarks`, `list_recommendations`, `resolve_recommendation`,
  `_serialize_rec`, `_delta_7d`.
- `app/services/health_score/scoring.py` — `weighted_overall`, `band_for`.
- `app/services/health_score/config.py` — `DIMENSION_WEIGHTS`, `BANDS`,
  `DIMENSION_LABELS`, `MIN_COHORT_SIZE`, `RECOMMENDATION_CATALOG`,
  `HEALTH_CONFIG_VERSION`.
- `app/services/health_score/recommendations.py` — `generate_recommendations`.
- `app/core/errors.py` — `RecommendationResolved` (409
  `RECOMMENDATION_RESOLVED`); `NotFound` (404, existing) covers unknown
  dimension keys and unknown/cross-tenant recommendation ids.
- **Wiring change**: `app/services/assessment/service.py` — `complete_assessment`
  calls `recompute_health_score(db, startup, trigger="assessment_complete")`
  inline (function-local import to avoid an import cycle) where it used to
  enqueue `healthscore.recalculate`. `roadmap.replan`'s enqueue at the same
  call site is unchanged.

**Events** (log-only `event_bus`, `app/platform/events.py`) — published from
`recompute_health_score`: `healthscore.updated` (every recompute — score,
previous score, band, `delta_7d`, `config_version`); `healthscore.dropped`
(additionally, when `delta_7d <= -5`); `healthscore.record` (additionally,
when the new score exceeds every prior history point). Nothing consumes
these yet (same log-only stub as every other event in the codebase).

**Tests**
- `tests/services/test_health_models.py`, `tests/services/test_health_scoring.py`,
  `tests/services/test_health_recompute.py`,
  `tests/services/test_health_recommendations.py`,
  `tests/api/test_health_score.py`, `tests/api/test_health_recommendations.py`
  — 38 health-score-focused unit tests (real Postgres, per-test rollback),
  part of the full **254-test, 98%-coverage** suite (`poetry run pytest -q`).
- `e2e/test_health_score.py` (this task) — one live end-to-end journey.

## Verification

- **Live E2E: 24 passed** (`make e2e`) — 23 prior (auth journeys + onboarding
  + assessment + smoke) + the new `test_health_score_journey`: founder
  signup → verify → login → onboard → `GET /health-score` while
  `pending_assessment` (score `None`) → start + adaptively answer the
  kickoff assessment with deliberately weak answers (`product_stage: idea`,
  `has_revenue: no`, `incorporated: no`, `team_size: solo`, plus mid-scale
  answers on the 3 scale questions — the same fixture the unit suite already
  uses in `tests/api/test_health_recommendations.py` /
  `tests/services/test_health_recompute.py`) so **every** dimension lands
  below 60 → complete → `GET /health-score` flips to `status: "ok"` with an
  int score, 5 dimensions, non-empty `top_recommendations` → dimension
  drill-down on `money` (label "Financial", ≥1 signal) → `GET
  /history?range=all` (≥1 point) → `GET /recommendations` (non-empty, all
  `pending` — 9 rows generated from the weak answers) → accept the first
  (→ `status: "accepted"`) → dismiss the **same** id (→ 409
  `RECOMMENDATION_RESOLVED`) → `GET /benchmarks` (→
  `insufficient_data`, cohort too small in a fresh e2e DB) → a **second**
  founder, fresh signup + their own workspace, `POST accept` on the first
  founder's recommendation id using their own `X-Workspace-Id` → 404
  `NOT_FOUND` (uniform cross-tenant enumeration guard). Every response body
  along the way is captured verbatim to `e2e/_captures/health_score/*.json`
  — the source for `docs/fe-integration-guide-health-score.md`.
- **Unit suite: 254 passed, 98% coverage** (`poetry run pytest -q`).
- `poetry run black --check app tests` / `poetry run isort --check app tests`
  / `poetry run ruff check app tests e2e` / `poetry run mypy app` — all
  clean.

## Operate / roll back

- No new env vars or deploy steps beyond the existing `make e2e` / `alembic
  upgrade head` flow.
- **Rollback:** `alembic downgrade -1` from `0005_health_score` drops all
  four new tables (`health_signals`, `health_scores`, `health_score_history`,
  `health_recommendations` — order doesn't matter for FK safety, none
  reference each other, only `startups`). This is **lossy**: any Health
  Score data written while `0005` was applied is destroyed on downgrade,
  same as any brand-new-table migration. Rolling back the code without also
  rolling back the migration would make `complete_assessment` fail on the
  now-missing tables; roll back both together.
- Rolling back just the assessment-service wiring change (reverting
  `recompute_health_score`'s call site back to an enqueue) without restoring
  a job consumer would silently stop Health Score updates on assessment
  completion — same caution `docs/sop/2026-08-16-assessment.md` already
  notes for its own job stubs.

## Follow-ups

- **Real benchmark cohort aggregation** — `get_benchmarks`' ≥-cohort-size
  branch is currently identical to the below-threshold branch (no
  percentile computation exists yet); only the honest empty-state and the
  gate itself are real today.
- **Async worker for the unconsumed stub jobs** (Module 05) —
  `healthscore.initialize` (onboarding) and `roadmap.generate` /
  `roadmap.replan` (onboarding + assessment) are persisted as `queued` and
  never drained; no worker exists yet.
- **AI-generated summary + recommendations** (Module 03) — `get_overview`'s
  `summary` and every `RECOMMENDATION_CATALOG` entry are static/templated;
  Module 03 is expected to replace or augment both with model-generated
  content once it exists.
- **`get_dimension`'s `trend` is unbounded** — it returns every
  `HealthScoreHistory` row for the startup with no range/limit, unlike
  `get_history` which windows by `range`. Fine at today's data volumes;
  worth capping if a startup accumulates a long history.
- **The unknown-dimension API test asserts only the 404 status**, not the
  error code — `test_dimension_unknown_key_404` doesn't distinguish "this
  is `NotFound`'s `NOT_FOUND` code" from any other 404 source, since
  FastAPI's own route-not-found also 404s on a bad path shape.
- **No test asserts `resolved_at` is stamped on accept/dismiss** —
  `resolve_recommendation` sets it (`app/services/health_score/service.py`)
  but no unit or E2E test checks the value.
- **`resolve_recommendation` uses an unlocked check-then-act** (load the
  row, branch on its status, write) — a theoretical race between two
  near-simultaneous accept/dismiss calls on the same recommendation, same
  class of relaxation the rest of the codebase accepts elsewhere (e.g.
  `docs/sop/2026-08-16-assessment.md`'s answer-upsert is the one place that
  *does* close this with `ON CONFLICT`; recommendations don't have an
  equivalent single-writer-per-key constraint to lean on the same way).
  Not fixed here — consistent with the codebase's existing risk posture,
  flagged for anyone hardening this path later.
- **`complete_assessment`'s response still doesn't return `job_ids`** — the
  pre-existing asymmetry with `complete_onboarding` noted in
  `docs/sop/2026-08-16-assessment.md`'s Follow-ups is unchanged; out of
  scope for this module.
