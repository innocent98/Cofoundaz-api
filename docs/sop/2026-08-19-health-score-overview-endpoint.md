# SOP — Health Score: `GET /health-score` overview (pending + lazy-on-read)

**What shipped** — `GET /api/v1/health-score` (`app/api/v1/endpoints/health_score.py`,
new), backed by `service.get_overview(db, startup)`
(`app/services/health_score/service.py`). Any active workspace member can now
read a startup's current Health Score: a `pending_assessment` payload before
the kickoff assessment is done, or the full scored payload (score, band,
5 dimensions, ≤3 top pending recommendations, a templated summary) once it
is. This is Task 7 of the 11-task Health Score module plan
(`.superpowers/sdd/2026-08-19-health-score/`); Tasks 1–6 shipped the models,
migration, config/scoring, the recompute pipeline, recommendation
generation, and the inline recompute-on-assessment-complete wiring this
endpoint reads.

## Why

Tasks 1–6 made `HealthScore` get computed and kept current, but nothing
exposed it to a client — a founder had no way to actually see their score.
This task adds the read surface, with two edge cases the brief called out
explicitly: (1) a startup that hasn't completed its kickoff assessment yet
must get a normal 200 with a "go complete the assessment" message, never a
404; (2) a startup whose assessment completed through some path that didn't
go through `complete_assessment`'s inline recompute (e.g. a backfill, or a
future async path) must still get a correct score on the very next read,
computed lazily rather than staying permanently blank.

## How

**Three-way branch in `get_overview`.** Look up the startup's `HealthScore`
row first. If present, serialize it — the common case. If absent, check
`latest_completed_result` (already built by Task 4): if a completed
assessment exists, call `recompute_health_score(db, startup,
trigger="lazy_read")` and commit right there, then fall through to
serialize the now-present row. If neither a `HealthScore` row nor a
completed assessment exists, return the pending payload. This means a GET
can, as a side effect, write data — deliberate per the brief ("lazy-on-read"),
and safe because `recompute_health_score` is the same idempotent,
upsert-based function every other trigger already uses.

**Trigger label distinguishes the code path.** `trigger="lazy_read"` (vs.
`"assessment_complete"`) is written to `HealthScoreHistory.trigger`, so a
score computed because someone happened to GET the endpoint (rather than
because the assessment-completion flow ran) is distinguishable later if
needed for analytics or debugging.

**Per-dimension bands, not just the overall band.** `band_for` (Task 3) is
reused per-dimension so the response can show e.g. "Product: needs_work"
even though the overall band is "healthy" — the brief's `{key, label,
score, band}` shape per dimension.

**Top recommendations, not all of them.** Only `status == pending`,
ordered by `priority.asc()`, `limit(3)` — accepted/dismissed
recommendations (Task 5's reconciliation already keeps `pending` rows to
exactly the live, unacted candidates) don't clutter the overview; a
separate list endpoint (not this task) would show the rest.

**Summary is templated, not generated.** `f"Your Health Score is {score}
({band}). Your weakest area is {label}."` — weakest dimension found via
`min(dimension_scores, key=...)`. Deliberately simple; no LLM call in the
read path.

**Import hygiene (controller ruling R1).** All new imports
(`HealthRecommendation`, `RecommendationStatus`, `DIMENSION_LABELS`) were
added to the existing module-top import block in `service.py`, not inline
at the append site — keeps ruff's E402 clean. The one function-local import
already in `service.py` (Task 5's `generate_recommendations`, to avoid an
import cycle) is untouched and remains the only exception.

**Router follows the `assessments.py` idiom exactly** — same `_startup(db,
membership)` helper (raises `NotFound` if the membership's startup can't be
loaded, which is how cross-workspace access 404s rather than needing a
separate check), same `require_workspace` + `get_verified_user` dependency
pair used by every other *read* endpoint in that file (writes there use
`require_role(founder)` instead; this endpoint is read-only so it doesn't).

## What's involved

- `app/api/v1/endpoints/health_score.py` (new) — `router`, `_startup`,
  `GET ""` → `get_overview`.
- `app/api/v1/api.py` — imports `health_score`; registers
  `api_router.include_router(health_score.router, prefix="/health-score",
  tags=["health-score"])`.
- `app/services/health_score/service.py` — added `get_overview(db,
  startup) -> dict` and `_serialize_rec(r) -> dict`; module-top imports
  extended with `HealthRecommendation`, `RecommendationStatus`,
  `DIMENSION_LABELS`.
- `tests/api/test_health_score.py` (new) — 3 tests (below).
- Consumes (unchanged): `require_workspace`, `get_verified_user`,
  `success_response`, `recompute_health_score`, `latest_completed_result`,
  `band_for`, `DIMENSION_LABELS`.

## Verification

- RED: `pytest tests/api/test_health_score.py -q` → 3 failed, all
  `404 Not Found` (`{"detail":"Not Found"}`) — route didn't exist yet,
  confirming the tests fail for the right reason before the endpoint was
  added.
- GREEN: same file → 3 passed after implementing `get_overview`, the
  router, and registration.
- Full suite: `pytest -q` → 241 passed (238 pre-existing + 3 new).
- `ruff check app/ tests/api/test_health_score.py` → clean (after
  `--fix` resolved two import-sort/wrap findings — both formatting-only,
  no logic touched). `mypy app` → clean, no issues found in 83 source
  files.
- Real fixtures used (discovered via `grep -rn "def client\|X-Workspace-Id"
  tests/api/`, not the brief's placeholder names): `client`/`db` fixtures
  from `tests/conftest.py`; a local `_founder(db)` helper copied verbatim
  from `tests/api/assessment/test_results.py` (creates a verified user +
  startup + founder membership + bearer/`X-Workspace-Id` headers via
  `tests/factories.create_user/create_startup/create_membership` and
  `app.core.security.create_access_token`).
- Test-by-test intent:
  - `test_overview_pending_before_assessment` — fresh founder, no
    assessment at all → asserts `status == "pending_assessment"`,
    `score`/`band is None`, `dimensions == []`, `top_recommendations == []`.
  - `test_overview_ok_after_assessment` — answers a minimal assessment
    (the same 8-key set `test_health_recompute.py` uses to skip every
    conditional follow-up) and calls `complete_assessment` (the real
    assessment-service function, which per Task 6 triggers the inline
    recompute) so a `HealthScore` row already exists before the GET →
    asserts `status == "ok"`, integer score, non-null band, exactly 5
    dimensions each shaped `{key, label, score, band}`, ≤3
    recommendations, non-empty summary.
  - `test_overview_lazy_computes_when_row_missing` — inserts a completed
    `Assessment` + `AssessmentResult` **directly via factories**, never
    calling `recompute_health_score` or `complete_assessment` — asserts
    zero `HealthScore` rows exist beforehand, then that the GET both
    returns `status == "ok"` *and* leaves exactly one `HealthScore` row
    behind, proving the lazy-on-read path (not a pre-existing row) is what
    answered the request.

## Operate / roll back

No migration, no config, no new env vars. Purely additive: a new router
file, one new `include_router` line, and two new functions appended to
`service.py`. Reverting all three fully rolls this back with no data
cleanup — any `HealthScore` rows a lazy read created remain valid,
idempotent data (identical to what `complete_assessment` would have
written) and don't need to be purged.

## Follow-ups

- No dedicated "list all recommendations" or accept/dismiss endpoints yet
  — this task only exposes the top-3 via the overview, per Task 5's SOP
  Follow-ups, which are still open and tracked there, not duplicated here.
- The lazy-on-read path calls `db.commit()` directly inside a service
  function (`get_overview`), which is a slight deviation from the
  read-only-services-commit-in-router convention the rest of the codebase
  leans toward (e.g. `assessments.py`'s routes call `db.commit()`
  themselves after their service calls). It matches the brief exactly and
  is scoped tightly (only fires when a write is genuinely needed to answer
  the read), but is worth a second look if this pattern needs to repeat
  elsewhere.
