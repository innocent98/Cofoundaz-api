# SOP — Health Score: inline recompute wired into assessment-complete

**What shipped** — `complete_assessment` (`app/services/assessment/service.py`)
now calls `recompute_health_score(db, startup, trigger="assessment_complete")`
inline, in the same transaction, instead of enqueueing a `healthscore.recalculate`
job. This is Task 6 (the final task) of the 11-task Health Score module plan
(`.superpowers/sdd/2026-08-19-health-score/`); Tasks 1–5 shipped the models,
migration, config/scoring, the recompute pipeline, and recommendation
generation that this task hooks up to the assessment flow.

## Why

Tasks 1–5 built `recompute_health_score` and everything it writes (score,
signals, history, recommendations, events) as a self-contained, uncommitted
function ready to be called from anywhere in a transaction. Until this task,
nothing called it in production — `complete_assessment` only enqueued a
`healthscore.recalculate` job stub that no worker ever drained (per
`docs/sop/2026-08-16-assessment.md`'s and `docs/sop/2026-08-15-onboarding.md`'s
Follow-ups). This task closes that gap: a completed assessment must produce a
`HealthScore` row synchronously, not depend on a worker that doesn't exist.

## How

**Inline call, not a job.** `recompute_health_score` was already designed to
flush-not-commit, so calling it directly from `complete_assessment` lets it
participate in the same transaction as the assessment-completion claim,
`AssessmentResult` insert, and `assessment_pending` flip — one atomic unit,
same as before. No job queue round-trip, no separate worker, no eventual
consistency window between "assessment completed" and "health score updated."

**Only `healthscore.recalculate` retired, not `roadmap.replan`.** The two
enqueues were adjacent but independent: `roadmap.replan` still feeds Module 05
(Roadmap), which doesn't exist yet, so it stays a job stub. Only the
Health-Score-specific enqueue is replaced.

**Ordering preserved.** The call site sits exactly where the enqueue used to
be — after the atomic in-progress→completed claim, after the `AssessmentResult`
is added, after `assessment_pending` is cleared. `recompute_health_score`'s
`latest_completed_result` query needs `Assessment.status == completed` with
`completed_at` set, which is already true at this point (`assessment.status`
and `assessment.completed_at` are set in-memory earlier in the same call,
`app/services/assessment/service.py:181-182`), so it finds the just-completed
result without an extra flush.

**Import placement.** `recompute_health_score` is imported with a
function-local import inside `complete_assessment`, matching the same
avoid-an-import-cycle pattern Task 5 used for `generate_recommendations`
inside `recompute_health_score` — `assessment/service.py` and
`health_score/service.py` would otherwise risk a cycle if either grows a
module-top import of the other later.

## What's involved

- `app/services/assessment/service.py` — in `complete_assessment`, replaced
  `job_dispatcher.enqueue(db, "healthscore.recalculate", job_payload, startup.id)`
  with a function-local `from app.services.health_score.service import
  recompute_health_score` + `recompute_health_score(db, startup,
  trigger="assessment_complete")`. `roadmap.replan`'s enqueue is unchanged.
- `tests/services/test_health_recompute.py` — new
  `test_complete_assessment_triggers_health_score`: fully answers a minimal
  `initial` assessment, calls `complete_assessment`, asserts exactly one
  `HealthScore` row exists for the startup.
- `tests/api/assessment/test_complete.py::test_complete_scores_and_sideeffects` —
  updated: no longer asserts a `healthscore.recalculate` job is queued (it
  isn't, by design); asserts `roadmap.replan` still is, and that a
  `HealthScore` row now exists instead.
- `tests/services/assessment/test_complete_concurrency.py` — updated: the
  two-racer regression test now asserts exactly one `roadmap.replan` job
  (not `healthscore.recalculate` + `roadmap.replan`) and exactly one
  `HealthScore` / `HealthScoreHistory` row, confirming the inline recompute
  itself is claim-protected (runs once, not once per racer) the same way the
  scoring and job-enqueue already were.
- `docs/sop/2026-08-16-assessment.md`, `docs/sop/2026-08-15-onboarding.md` —
  one-line Follow-ups note each pointing here.
- Consumes (unchanged): `recompute_health_score`
  (`app/services/health_score/service.py`).

## Verification

- RED: `pytest tests/services/test_health_recompute.py::test_complete_assessment_triggers_health_score -q`
  failed — `assert 0 == 1` (no `HealthScore` row), confirming the test fails
  for the right reason before the wiring change.
- GREEN: same test passes after the change;
  `pytest tests/services/test_health_recompute.py -q` → 8 passed.
- Regression: `pytest tests/ -k assessment -q` → 64 passed (includes the two
  updated tests above, which failed against the old
  `["healthscore.recalculate", "roadmap.replan"]` assertion before being
  updated, and pass now).
- Full suite: `pytest -q` → 238 passed.
- `ruff check app/services/assessment/service.py` and the three touched test
  files → clean. `mypy app/services/assessment` → clean (5 source files).
- Self-review: confirmed only `healthscore.recalculate` was retired
  (`roadmap.replan` untouched); confirmed the inline call sits after the
  atomic claim so `completed_at` is set when `recompute_health_score` queries
  for it; confirmed no double-processing via the concurrency test's
  single-row assertions; confirmed every pre-existing test that asserted the
  retired job (two found, listed above under What's involved) was updated to
  match the new behavior rather than left to bit-rot.

## Operate / roll back

No migration, no config, no new env vars. Purely a call-site swap inside an
existing transaction — reverting `app/services/assessment/service.py`'s few
changed lines (enqueue → inline call) fully rolls this back; no data cleanup
needed. Note: rolling back without also restoring a `healthscore.recalculate`
worker would silently stop Health Score updates on assessment completion
entirely (the job would queue but nothing drains it, per the now-updated SOP
Follow-ups this doc replaces).

## Follow-ups

- **`healthscore.initialize`** (referenced in
  `docs/sop/2026-08-15-onboarding.md`'s original Follow-ups as a Module 06
  hand-off) was never enqueued anywhere in the codebase as of this task — it
  was aspirational, not a live stub. Nothing to retire there; noted for
  anyone searching for it after reading the onboarding SOP's cross-reference.
- **`complete_assessment`'s response still doesn't return `job_ids` or the
  new health score** — the pre-existing asymmetry with `complete_onboarding`
  noted in `docs/sop/2026-08-16-assessment.md`'s Follow-ups is unchanged by
  this task. A caller that wants the fresh score has to `GET` it separately;
  out of scope here since the brief only asked for the inline compute, not a
  response-shape change.
