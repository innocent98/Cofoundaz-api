# SOP: Real `get_current_user` dependency

## What shipped

- Commit: `365b000` — "feat(auth): DB-backed get_current_user dependency"
- Branch: `design/auth-onboarding-foundation`
- Task 14 of the Foundation & Tenancy Spine plan
  (`.superpowers/sdd/2026-08-12-foundation-tenancy-spine/task-14-brief.md`).

Replaced the scaffold's stub `get_current_user` in `app/api/deps.py` (which decoded a JWT but
returned a hardcoded `{"user_id": ...}` dict, with a `# TODO: Fetch user from database`) with a
real DB-backed resolver that decodes the JWT, looks up the `User` row by `sub`, and returns the
ORM object. Added `get_optional_user` for endpoints where auth is optional, and an `Unauthorized`
`AppError` subclass so 401s render through the standard envelope instead of raw `HTTPException`.

## Why

Every authenticated endpoint from Task 15 (RBAC/tenancy) onward needs a dependency that resolves
the caller to an actual `User` row (for role checks, ownership checks, audit logging, etc.), not
just an opaque dict with a string id. The stub also used a raw `HTTPException` for auth failures,
which bypasses the envelope error shape (`{"error": {"code", "message", ...}}`) established in
Task 5 — every other failure mode in the API already renders through that shape, so 401s should
too.

## How

**`app/api/deps.py`** (full rewrite):
- `security = HTTPBearer(auto_error=False)` — `auto_error=False` (vs. the stub's `HTTPBearer()`)
  so a missing `Authorization` header reaches our code as `credentials=None` instead of FastAPI
  auto-raising its own unstyled 403.
- `class Unauthorized(AppError)` — `code="UNAUTHORIZED"`, `http_status=401`, generic message.
  Local to `deps.py` per the brief (not added to `app/core/errors.py`'s catalogue, since it's an
  auth-layer concern rather than a domain error).
- `get_current_user(credentials, db) -> User`:
  1. `credentials is None` → `Unauthorized()`.
  2. `jwt.decode(..., settings.SECRET_KEY, algorithms=[settings.ALGORITHM])`; missing/falsy `sub`
     claim → `Unauthorized()`.
  3. `uuid.UUID(user_id)` — parses the claim into a UUID before querying.
  4. `except (JWTError, ValueError)` → `Unauthorized()`. **Deviation from the brief's literal
     snippet**: the brief only caught `JWTError` around the `uuid.UUID(...)` call, which sits
     *outside* its `try` block — a malformed (non-UUID) `sub` claim would raise an unhandled
     `ValueError` (500) instead of 401. Moved the `uuid.UUID()` parse inside the `try` and added
     `ValueError` to the except clause so "invalid token" (per the task's global constraint)
     covers a malformed subject too, not just JWT signature/expiry failures.
  5. `db.query(User).filter(User.id == subject).first()`; `None` → `Unauthorized()`; otherwise
     return the `User`.
- `get_optional_user(credentials, db) -> User | None`: `None` credentials → `None`; otherwise
  delegates to `get_current_user` and swallows any `AppError` → `None`. Matches the brief exactly.

**Key decision — `# noqa: B008` on every `Depends(...)` default**: ruff's bugbear `B008` flags
function calls in argument defaults, which is exactly the idiomatic FastAPI `Depends()` pattern.
The existing codebase already suppresses this per-line (`app/api/v1/endpoints/jobs.py:15`), so
the same convention was applied here rather than introducing a blanket ignore or reworking the
dependency style.

**Test mini-app deviation**: the brief's `_mini_app()` builds a bare `FastAPI()` with no exception
handlers registered. Running it as-given, `test_missing_user_401` fails — not with a 401, but with
the raw `Unauthorized` exception propagating out of the ASGI app (verified during RED→GREEN: see
Verification below). This is because `AppError` is a plain `Exception`; it's `main.py`'s
`register_exception_handlers(app)` call that turns it into a `JSONResponse`, and the brief's test
helper never calls it. Fixed by adding `register_exception_handlers(app)` to `_mini_app()` — this
matches how the real app is wired (`app/main.py:35`) and is necessary for the test to exercise the
actual production error-rendering path rather than FastAPI's default unhandled-exception behavior.

**Alternative rejected**: making `Unauthorized` a subclass of `HTTPException` instead of
`AppError`, so it would 401 automatically via FastAPI's built-in handling with no handler
registration needed. Rejected because it would bypass the envelope shape (raw `{"detail": ...}`
instead of `{"error": {"code": "UNAUTHORIZED", ...}}`), contradicting the task's explicit
instruction to raise `Unauthorized()` "(envelope-rendered) instead of raw HTTPException".

## What's involved

- `app/api/deps.py` (rewritten, 52 lines) — `security`, `Unauthorized`, `get_current_user`,
  `get_optional_user`. No other module imports `app.api.deps` yet (grepped repo-wide), so this is
  a self-contained replacement with no call-site fallout.
- `tests/api/test_current_user.py` (new) — `_mini_app()` helper (registers exception handlers,
  one `/whoami` route depending on `get_current_user`) + 2 tests:
  `test_valid_token_resolves_user`, `test_missing_user_401`.
- Depends on: `app/core/security.py::create_access_token` (Task ~?, pre-existing), `app/db/models/
  user.py::User` (Task 12), `app/db/session.py::get_db`, `app/core/errors.py::AppError` /
  `register_exception_handlers` (Task 5), `tests/factories.py::create_user`, `tests/conftest.py`'s
  `db` fixture (savepoint-per-test Postgres session).
- No RBAC/tenancy logic (Task 15) or rate limiting (Task 16) — out of scope, not touched.

## Verification

RED (before implementation, stub still active):
```
$ poetry run pytest tests/api/test_current_user.py -v
FAILED tests/api/test_current_user.py::test_valid_token_resolves_user - AttributeError: 'dict' object has no attribute 'email'
FAILED tests/api/test_current_user.py::test_missing_user_401 - AttributeError: 'dict' object has no attribute 'email'
2 failed
```

Intermediate (implementation done, test's `_mini_app()` still brief-verbatim without exception
handlers registered):
```
$ poetry run pytest tests/api/test_current_user.py -v
tests/api/test_current_user.py::test_valid_token_resolves_user PASSED
tests/api/test_current_user.py::test_missing_user_401 FAILED - app.api.deps.Unauthorized: Not authenticated.
1 failed, 1 passed
```
(`Unauthorized` propagated unrendered through the ASGI app — confirmed the mini-app needed
`register_exception_handlers`, see How/deviation above.)

GREEN (after adding `register_exception_handlers(app)` to `_mini_app()`):
```
$ poetry run pytest tests/api/test_current_user.py -v
tests/api/test_current_user.py::test_valid_token_resolves_user PASSED
tests/api/test_current_user.py::test_missing_user_401 PASSED
2 passed
```

Full suite:
```
$ poetry run pytest
32 passed
```
`app/api/deps.py` coverage: 71% (34 statements, 10 missed — the missed lines are the `Unauthorized`
error-return branches not hit by the happy path plus `get_optional_user`'s `None`/`AppError`
branches, not exercised directly by this task's 2 tests; acceptable, not a regression).

Scoped format/lint — `deps.py` and the new test file are fully clean on all 4 tools:
```
$ poetry run black --check app/api/deps.py tests/api/test_current_user.py
All done! 2 files would be left unchanged.
$ poetry run isort --check-only app/api/deps.py tests/api/test_current_user.py
(no output — clean)
$ poetry run ruff check app/api/deps.py tests/api/test_current_user.py
All checks passed!
$ poetry run mypy app/api/deps.py
Success: no issues found in 1 source file
```

Repo-wide `poetry run mypy app` still reports 19 pre-existing errors, **none in `app/api/deps.py`**
— confirmed by grepping the output for `deps.py` (no matches). Remaining errors are in
`config.py`, `session.py`, `errors.py`, `security.py`, `logger.py`, `health.py`, `jobs.py`,
`main.py` — all pre-existing debt, none newly introduced. Repo-wide `black --check` / `isort
--check-only` / `ruff check` over `app tests` all pass clean (63 files unchanged) as of this
task — that debt was already cleared in the working tree by an earlier, uncommitted `make format`
run predating this task (see Concerns).

## Operate / roll back

- Pure application-layer change; no migrations, no config, no deployment side effects.
- To roll back: revert this task's commit. `app/api/deps.py` reverts to the stub; any endpoint
  that started depending on `get_current_user`/`get_optional_user` after this task would need the
  stub's dict-shaped return handled instead (none exist yet as of this task).

## Follow-ups

- Task 15 (RBAC/tenancy) will build membership/role checks on top of the `User` object returned
  here.
- `app/api/deps.py` test coverage is currently only the 2 brief-mandated cases (valid token,
  unknown user). Consider adding explicit coverage for: expired token, malformed/non-UUID `sub`
  claim (exercises the `ValueError` branch added in the deviation above), and `get_optional_user`'s
  both branches, when Task 15 wires up real endpoints that use it.
- **Pre-existing, out-of-scope working-tree drift**: `app/api/v1/endpoints/health.py`,
  `app/core/logger.py`, and `app/core/security.py` were already modified (uncommitted, formatting
  only) in the working tree before this task started — confirmed via `git diff HEAD` on those
  files, unrelated to this task's changes. Left untouched and uncommitted, consistent with keeping
  this task's diff limited to `app/api/deps.py` and `tests/api/test_current_user.py`. Worth a
  dedicated commit (or `git checkout` to discard) before it causes confusion in a future task.
