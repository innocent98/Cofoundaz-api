# SOP: Rate limiting + coverage gate

## What shipped

- Commit: `feat(platform): slowapi rate limiting + coverage config`
- Branch: `design/auth-onboarding-foundation`
- Task 16 of the Foundation & Tenancy Spine plan (final task)
  (`.superpowers/sdd/2026-08-12-foundation-tenancy-spine/task-16-brief.md`).

Wired a `slowapi` `Limiter` onto `app.state.limiter` (default limit
`RATE_LIMIT_PER_MINUTE`/minute, keyed by remote address) and a `RateLimitExceeded` exception
handler that renders 429 in the envelope shape (`error_response("RATE_LIMITED", ...)`) with a
`Retry-After: 60` header. Added `[tool.coverage.run]` to `pyproject.toml` (`source=["app"]`,
`omit=["app/main.py","alembic/*"]`) — coverage is recorded on every `pytest` run via the
already-existing `addopts`, not enforced as a failing threshold yet.

## Why

Task 3 already added `settings.RATE_LIMIT_PER_MINUTE` and `slowapi` was already a dependency, but
nothing used them. Without a registered limiter, no endpoint (present or future) can opt into rate
limiting, and an unhandled `RateLimitExceeded` would bypass the project's envelope error shape.
This task closes that gap and adds the coverage config so `--cov` runs against `app/` consistently
(excluding `main.py`'s wiring code and Alembic migrations, which aren't meaningfully unit-testable
the same way).

## How

**`app/main.py`** — added immediately after `register_exception_handlers(app)`, matching the
brief verbatim:
- `limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"])`
- `app.state.limiter = limiter`
- `@app.exception_handler(RateLimitExceeded) async def _rate_limit_exceeded_handler(...)` → 429,
  `error_response("RATE_LIMITED", "Too many requests. Slow down a moment.")`,
  `Retry-After: 60`.

Only `app.state.limiter` is registered here; no route is decorated with `@limiter.limit(...)` yet
and `SlowAPIMiddleware` is not added — the brief scopes this task to wiring the limiter and its
error handler, not to applying limits to specific endpoints (there are no non-health endpoints
with side effects yet). Later tasks that add rate-sensitive routes can depend on `limiter` via
`request.app.state.limiter` / the `@limiter.limit(...)` decorator.

**`pyproject.toml`** — `[tool.pytest.ini_options]` already had `asyncio_mode = "auto"` and an
`addopts` that runs `--cov=app --cov-report=term-missing --cov-report=html` on every invocation
(pre-existing from earlier tasks), so that part of the brief's Step 5 was already satisfied.
Added the missing `[tool.coverage.run]` table with `source = ["app"]` and
`omit = ["app/main.py", "alembic/*"]`.

## What's involved

- `app/main.py` — added `Limiter`/`RateLimitExceeded`/`get_remote_address` imports,
  `app.state.limiter`, `_rate_limit_exceeded_handler`.
- `pyproject.toml` — new `[tool.coverage.run]` table.
- `tests/api/test_rate_limit.py` (new) — `test_limiter_registered`.
- Depends on: `app.core.config.settings.RATE_LIMIT_PER_MINUTE` (Task 3), `app.core.envelope.error_response` (Task 4), `slowapi` (pre-existing dependency).
- No endpoints were changed to actually enforce limits — deferred until endpoints with
  side-effectful POSTs exist (auth/onboarding plan), consistent with the brief's self-review notes.

## Verification

RED (before implementation):
```
$ poetry run pytest tests/api/test_rate_limit.py -v
FAILED tests/api/test_rate_limit.py::test_limiter_registered - AssertionError: assert None is not None
1 failed
```

GREEN (after implementation):
```
$ poetry run pytest tests/api/test_rate_limit.py -v
tests/api/test_rate_limit.py::test_limiter_registered PASSED
1 passed
```

Full suite:
```
$ poetry run pytest
36 passed
```

Coverage (`poetry run pytest --cov=app --cov-report=term`):
```
TOTAL   502   38   92%
```
(`app/main.py` correctly excluded from the report by the new `omit` config; not enforced as a
failing gate per the brief.)

Scoped format/lint — `app/main.py`, `tests/api/test_rate_limit.py` clean on black/isort/ruff:
```
$ poetry run black --check app/main.py tests/api/test_rate_limit.py
All done! 2 files would be left unchanged.
$ poetry run isort --check-only app/main.py tests/api/test_rate_limit.py
(clean)
$ poetry run ruff check app/main.py tests/api/test_rate_limit.py
All checks passed!
```

`poetry run mypy app/main.py` reports 5 pre-existing `no-untyped-def` errors on lines 19
(`LoggingMiddleware.dispatch`), 76 (`root`), 85 (`health_check`), 89 (`start`) — all predate this
task. The code added by this task (`_rate_limit_exceeded_handler`, lines 42–56) is fully typed and
contributes zero mypy errors.

## Operate / roll back

- No migrations, no new config keys (reuses existing `settings.RATE_LIMIT_PER_MINUTE`).
- No route currently enforces a limit beyond the global default, and nothing calls
  `limiter.limit(...)`, so there is no behavioral change to existing endpoints' response codes.
- To roll back: revert this task's commit.

## Follow-ups

- Coverage is recorded, not gated — no `--cov-fail-under` threshold is set. Enforcement lands with
  the auth/onboarding plan once there are enough endpoints for a meaningful threshold.
- No route currently opts into a tighter-than-default limit via `@limiter.limit(...)`; future
  endpoints (e.g. login, password reset) should apply explicit stricter limits.
- `SlowAPIMiddleware` is not registered — if per-route `@limiter.limit(...)` decorators are added
  later, confirm whether they need it (slowapi's decorator-based usage generally works without the
  middleware; the middleware is mainly needed for enforcing `default_limits` on undecorated
  routes).
- **Pre-existing, out-of-scope working-tree drift** (carried over from prior tasks' SOPs, still
  unresolved): `app/api/v1/endpoints/health.py`, `app/core/logger.py`, and `app/core/security.py`
  remain modified/uncommitted in the working tree, unrelated to this task. Left untouched to keep
  this task's diff scoped to Task 16 files.
