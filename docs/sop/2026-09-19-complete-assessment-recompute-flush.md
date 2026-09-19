# SOP — `complete_assessment` recompute now materializes the Health Score inline

**Date:** 2026-09-19
**Type:** Bug fix (Module 06 Health Score / Module 07 Assessment)
**Scope:** one-line service fix + regression test + docs.

## What shipped

`complete_assessment` now `db.flush()`es the just-added `AssessmentResult` **before** calling
`recompute_health_score`, so completing the kickoff assessment (`POST /assessments/{id}/complete`)
directly creates the `HealthScore` row, the pending `HealthRecommendation` rows, and the
`ai.health.recommendations` job — instead of relying on a later `GET /health-score` to do it.

Commit: `<this branch>` → PR into `develop` (branch `fix/complete-assessment-recompute-flush`).
File: `app/services/assessment/service.py` (one added `db.flush()` + comment), regression test
`tests/services/assessment/test_complete_recompute_flush.py`.

## Why (root cause)

`SessionLocal` is created with `autoflush=False` (`app/db/session.py:14`). In `complete_assessment`
the `AssessmentResult` is `db.add(...)`-ed and then, in the same request/transaction,
`recompute_health_score` runs. Its first step, `latest_completed_result` (a `SELECT` joining
`Assessment` → `AssessmentResult`), does **not** autoflush under `autoflush=False`, so it did not
see the just-added-but-unflushed row: it returned `None`, and `recompute_health_score` bailed —
producing no `HealthScore`, no recommendations, and **no `ai.health.recommendations` job** from the
completion request. This was masked by `get_overview`'s lazy-on-read recompute (`app/services/
health_score/service.py:191-197`): the score/recommendations only actually materialized when a
client later called `GET /health-score` (a fresh request, with the assessment now committed).

Reproduced live (fresh signup → onboard → complete with weak answers → immediate
`GET /health-score/recommendations` returned `[]`; `psql` confirmed `health_scores`/
`health_recommendations` empty right after `/complete` despite `assessment_results` committed).

**Why every unit test missed it:** the per-test `db` fixture is `Session(bind=connection,
join_transaction_mode="create_savepoint")` — it defaults to `autoflush=True`, which silently flushes
before that `SELECT` and hides the bug. Only the e2e suite (real `SessionLocal`, `autoflush=False`)
and the fix's own regression test reproduce it.

## How (fix + key decisions)

- **The fix:** a single `db.flush()` after `db.add(AssessmentResult(...))` and before
  `recompute_health_score(...)`. Only the atomic-claim **winner** reaches this code (the concurrent
  loser returns earlier at the `claimed == 0` branch), so there is no double-fire or change to the
  completion race semantics — it only makes the pending result visible to the recompute's own read.
- **No behavior change under `autoflush=True`** (tests) — the flush is redundant there; it is
  load-bearing only under production `autoflush=False`.
- **Regression test uses its own `autoflush=False` session** (a non-committing `Session(bind=engine,
  autoflush=False)`, rolled back for isolation), because a test on the standard `db` fixture would
  pass with or without the fix. It asserts `HealthScore` + pending `HealthRecommendation` +
  `ai.health.recommendations` job all exist immediately after `complete_assessment` returns, with no
  intervening GET and no manual flush. Fails before the fix (`assert 0 == 1`), passes after.

## What's involved

| File | Change |
|------|--------|
| `app/services/assessment/service.py` | `db.flush()` before `recompute_health_score` (with explanatory comment) |
| `tests/services/assessment/test_complete_recompute_flush.py` | new autoflush=False regression test |
| `docs/fe-integration-guide-health-score.md` | §1 note + §5 + verification table updated: `/complete` now materializes inline |
| `docs/sop/2026-09-19-mission-health-ai.md` | follow-up marked resolved |
| `docs/checklist/PROJECT_CHECKLIST.md` | deferred follow-up checked off |

## Verification

Full local CI reproduction, green: black/isort/ruff · mypy 163 files · pylint 9.90/10 · bandit ·
**pytest 1289 passed, 97% coverage** (incl. the new autoflush=False regression: RED `assert 0 == 1`
before, GREEN after; the `complete_assessment` concurrency test still passes) · single alembic head
`0026`, no drift (no migration) · **e2e 48 passed** (the two health e2e tests keep their
`GET /health-score` as an honest journey step, now no longer load-bearing).

## Operate / roll back

Pure code change, no migration, no config. Revert the one-line `db.flush()` (and the test) to roll
back; behavior returns to lazy-read-only materialization.

## Follow-ups

- Minor (separate): `APP_BASE_URL` is documented in `.env.production.example` but absent from
  `.env.example` (dev template) — surfaced while auditing the doc/sign email FE-link fix (PR #73),
  unrelated to this fix.
