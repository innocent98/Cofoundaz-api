# SOP: Jobs table + JobDispatcher stub + read endpoint

## What shipped

- Commit: (this task's commit — see subject `feat(platform): jobs table + stub dispatcher + GET /jobs/{id}`)
- Branch: `design/auth-onboarding-foundation`
- Task 10 of the Foundation & Tenancy Spine plan
  (`.superpowers/sdd/2026-08-12-foundation-tenancy-spine/task-10-brief.md`).

Added the first real domain model (`Job`), a stub async-job dispatcher
(`JobDispatcher.enqueue`) that persists a queued row synchronously, and a read
endpoint `GET /api/v1/jobs/{job_id}` returning the job in the standard envelope.

## Why

Several later modules (roadmap generation, health-score initialization, etc.) need
to enqueue background work and let clients poll for status/result. This task lays
the persistence and API foundation — a `jobs` table, a dispatcher interface, and a
read endpoint — without wiring an actual worker/queue yet. A real worker draining
`queued` rows is deferred to Modules 05/06.

## How

- **`app/db/models/job.py`**: `JobStatus(str, enum.Enum)` with
  `queued|running|succeeded|failed|cancelled`. `Job(UUIDMixin, TimestampMixin, Base)`,
  table `jobs`, columns: `type: str`, `status` (`SAEnum(JobStatus, native_enum=False,
  length=20)`, default `queued`), `startup_id: uuid.UUID | None`, `payload: JSONB`
  (default `dict`), `result: JSONB | None`, `error: str | None`.
- **`app/platform/jobs.py`**: `JobDispatcher.enqueue(db, type, payload,
  startup_id=None) -> Job` builds a `Job` with `status=JobStatus.queued`, does
  `db.add` + `db.flush()` (no commit — caller controls the transaction), and
  returns it. Module-level singleton `job_dispatcher = JobDispatcher()` for future
  callers that don't need a fresh instance.
- **`app/api/v1/endpoints/jobs.py`**: `GET /{job_id}` looks up the job by primary
  key; raises `NotFound` (404, `NOT_FOUND`) if missing; otherwise returns
  `success_response({id, type, status: status.value, result, error})`.
- **`app/db/models/__init__.py`**: now imports `Job` so `Base.metadata` (and hence
  `create_all` in the test harness) picks up the `jobs` table.
- **`app/api/v1/api.py`**: mounts `jobs.router` at prefix `/jobs`, tag `jobs`.

Deviation from the brief's verbatim endpoint snippet: added `# noqa: B008` to the
`db: Session = Depends(get_db)` parameter. Ruff's bugbear rule `B008` (no function
calls in argument defaults) is enabled repo-wide and flags FastAPI's canonical
`Depends(...)`-as-default pattern as a false positive; the existing `app/api/deps.py`
already carries two un-suppressed instances of this as pre-existing debt. Since this
task's file must be lint-clean (unlike the pre-existing debt files), the noqa was
the minimal fix that preserves the brief's logic exactly.

## What's involved

- `app/db/models/job.py` (new) — `JobStatus` enum + `Job` model, 33 lines.
- `app/platform/jobs.py` (new) — `JobDispatcher` + `job_dispatcher` singleton.
- `app/api/v1/endpoints/jobs.py` (new) — `GET /{job_id}` route.
- `app/db/models/__init__.py` (modified) — registers `Job` on `Base.metadata`.
- `app/api/v1/api.py` (modified) — mounts the jobs router.
- `tests/platform/test_jobs.py` (new) — dispatcher unit test.
- `tests/api/test_jobs_endpoint.py` (new) — endpoint envelope + 404 tests.
- No Alembic migration (deferred to Task 13 — table exists only via
  `Base.metadata.create_all` in the test harness for now). No audit/tenancy models.

## Verification

RED (before implementation):
```
$ poetry run pytest tests/platform/test_jobs.py tests/api/test_jobs_endpoint.py -v
ModuleNotFoundError: No module named 'app.db.models.job'
ModuleNotFoundError: No module named 'app.platform.jobs'
```

GREEN (after implementation):
```
$ poetry run pytest tests/platform/test_jobs.py tests/api/test_jobs_endpoint.py -v
tests/platform/test_jobs.py::test_enqueue_writes_queued_row PASSED
tests/api/test_jobs_endpoint.py::test_get_job_returns_envelope PASSED
tests/api/test_jobs_endpoint.py::test_get_missing_job_404 PASSED
3 passed
```

Full suite:
```
$ poetry run pytest
24 passed
```

Coverage: `app/db/models/job.py` 100%, `app/platform/jobs.py` 100%,
`app/api/v1/endpoints/jobs.py` 100%.

Scoped lint (touched files only — repo-wide `make lint` still fails on pre-existing
debt in `security.py`/`deps.py`/`health.py`, unrelated to this task):
```
$ poetry run ruff check app/db/models/job.py app/platform/jobs.py \
    app/api/v1/endpoints/jobs.py app/db/models/__init__.py app/api/v1/api.py \
    tests/platform/test_jobs.py tests/api/test_jobs_endpoint.py
All checks passed!

$ poetry run black --check <same files>
All done! 7 files would be left unchanged.

$ poetry run isort --check-only <same files>
(no output — clean)
```

## Operate / roll back

- Pure additive change: new table (created via `create_all` in tests; no
  migration/deploy step yet), new router mount. No config or env changes.
- To roll back: revert this task's commit. No data migration concerns since no
  Alembic migration was created.
- **Update (Task 13):** the `jobs` table now has a real Alembic migration —
  `alembic/versions/0001_initial_schema.py` — see
  `docs/sop/2026-08-12-initial-alembic-migration.md`. Roll back via
  `alembic downgrade -1`, not just a commit revert, once that migration has been
  applied to an environment.

## Follow-ups

- ~~Task 13 adds the Alembic migration for `jobs` (and other models accumulated so
  far).~~ Done — see `docs/sop/2026-08-12-initial-alembic-migration.md`.
- A real worker to drain `queued` rows and transition status
  (`queued → running → succeeded|failed|cancelled`) is planned for Modules 05/06;
  `JobDispatcher.enqueue` is intentionally a synchronous stub until then.
- No update/cancel endpoints yet — only the read path (`GET /{job_id}`) exists per
  this task's scope.
