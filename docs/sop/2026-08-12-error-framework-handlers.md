# SOP: Error framework + exception handlers

## What shipped

- Commit: `e431c72` — "feat(core): AppError hierarchy + envelope exception handlers"
- Branch: `design/auth-onboarding-foundation`
- Task 5 of the Foundation & Tenancy Spine plan
  (`.superpowers/sdd/2026-08-12-foundation-tenancy-spine/task-5-brief.md`).

Added `app/core/errors.py`: an `AppError` exception hierarchy (base class + 9 PRD-defined
subclasses) and `register_exception_handlers(app)`, which installs FastAPI exception handlers
that render every `AppError` and every `RequestValidationError` through the standard envelope
shape from Task 4's `app/core/envelope.py`. Wired the handlers into `app/main.py`.

## Why

Every endpoint across the API needs to fail in the same shape: `{"error": {"code", "message",
"field_errors"}}`. Without a central handler, each endpoint would hand-roll `HTTPException`
detail payloads inconsistently, and FastAPI's default 422 validation response uses a different
shape (`{"detail": [...]}`) than the product's envelope. This task establishes the one error
vocabulary (`EmailTaken`, `WeakPassword`, `InvalidCredentials`, `AccountLocked`, `TokenInvalid`,
`MfaInvalidCode`, `FeatureNotEnabled`, `Forbidden`, `NotFound`) that auth/onboarding work in
later tasks will raise directly, plus a generic `AppError(code, message, http_status=...)`
escape hatch for one-off cases.

## How

**`app/core/errors.py`**:
- `AppError(Exception)` — carries `code` (SCREAMING_SNAKE, class-level default overridable per
  instance), `message`, `http_status` (default 400), and `field_errors` (default `[]`).
- 9 subclasses set `code`/`http_status`/`message` as class attributes, with PRD-verbatim copy
  (e.g. `EmailTaken` → `409 EMAIL_TAKEN` / "That email already has an account — log in
  instead?"; `AccountLocked` → `429 ACCOUNT_LOCKED`; `FeatureNotEnabled` → `501
  FEATURE_NOT_ENABLED`, etc. — see file for the full table).
- `register_exception_handlers(app)` installs two `@app.exception_handler` closures:
  - `AppError` → `JSONResponse(status_code=exc.http_status, content=error_response(exc.code,
    exc.message, exc.field_errors))`.
  - `RequestValidationError` → remapped to `422 VALIDATION_ERROR` with `field_errors` built
    from `exc.errors()`, dropping the leading `"body"` path segment from each `loc` tuple so
    `field` reads e.g. `"n"` instead of `"body.n"`.

**Key decision — `# noqa: N818`**: ruff's `N` (pep8-naming) ruleset flags exception classes
without an `Error` suffix (`EmailTaken` vs `EmailTakenError`). The brief's required interface
names are exact (future auth-onboarding tasks will `raise EmailTaken()` etc.), so renaming
would break the contract. Suppressed per-class with `# noqa: N818` rather than disabling the
rule project-wide or renaming.

**`app/main.py` wiring**: added `from app.core.errors import register_exception_handlers` to
the import block and `register_exception_handlers(app)` immediately after the `app = FastAPI(...)`
block, before the CORS/logging middleware and router include — matches the brief exactly, and
doesn't touch any existing middleware or route.

**Alternative rejected**: mapping `RequestValidationError` status codes per-field-type (e.g.
different codes for type errors vs missing fields) — out of scope per the brief; a flat
`VALIDATION_ERROR` code with per-field messages in `field_errors` is what's specified.

## What's involved

- `app/core/errors.py` (new, 85 lines) — `AppError` + 9 subclasses + `register_exception_handlers`.
- `app/main.py` (modified) — import + `register_exception_handlers(app)` call added; no
  existing middleware, routes, or startup logic removed.
- `tests/core/test_errors.py` (new) — 3 tests, builds its own mini `FastAPI` app + `TestClient`
  per the brief (does not use the module-level `app` from `app.main`).
- No config, migration, or dependency changes.
- Depends on `app/core/envelope.py::error_response` (Task 4) — not reimplemented.

## Verification

RED (before implementation):
```
$ poetry run pytest tests/core/test_errors.py -v
ImportError ... ModuleNotFoundError: No module named 'app.core.errors'
```

GREEN (after implementation):
```
$ poetry run pytest tests/core/test_errors.py -v
tests/core/test_errors.py::test_apperror_renders_envelope PASSED
tests/core/test_errors.py::test_generic_apperror_status PASSED
tests/core/test_errors.py::test_validation_error_remapped_to_field_errors PASSED
======================= 3 passed ========================
```

Full suite after implementation (confirms `tests/test_health.py` still passes since `main.py`
was touched):
```
$ poetry run pytest
======================= 14 passed ========================
```

`app/core/errors.py` coverage: 100% (49 statements).

Format/lint scoped to the 3 changed files — clean:
```
$ poetry run black --check app/core/errors.py app/main.py tests/core/test_errors.py
All done! 3 files would be left unchanged.
$ poetry run ruff check app/core/errors.py app/main.py tests/core/test_errors.py
All checks passed!
```

**Known caveat**: the repo-wide `make lint` target (`black --check app tests`, `ruff check app
tests`, `mypy app`) fails on pre-existing debt unrelated to this task — confirmed via
`git stash` against the pre-Task-5 baseline: `app/api/deps.py`, `app/api/v1/endpoints/health.py`,
and `app/core/security.py` already fail `black --check` on the base commit, and baseline
`ruff check` / `mypy app` already report 11 / 19 errors respectively before any Task 5 changes.
`make format` auto-reformats those unrelated files too (isort/black are repo-wide by default);
those changes were deliberately reverted before committing to keep the diff limited to Task 5
files, per task scope. `app/core/errors.py` adds 3 new mypy "missing type annotation" notes,
consistent with the same untyped-function style already used throughout `app/main.py`,
`deps.py`, `security.py`, `logger.py`, and `session.py` — not a new debt category.

## Operate / roll back

- Pure application-layer addition; no deployment or config side effects, no migrations.
- To roll back: revert commit `e431c72`. `app/main.py` reverts cleanly to the pre-Task-5 state
  (default FastAPI/Starlette error responses instead of the envelope shape).
- Any endpoint added after this task can `raise EmailTaken()` / `raise NotFound()` / etc.
  directly — no per-endpoint try/except needed.

## Follow-ups

- Auth/onboarding endpoints (later tasks) will raise these `AppError` subclasses directly
  instead of `HTTPException`.
- The pre-existing repo-wide `make lint` debt (black/ruff/mypy failures in `deps.py`,
  `health.py`, `security.py`, and untyped functions across most of `app/`) is untouched and
  out of scope here; worth a dedicated cleanup task before `make lint` can be used as a hard
  gate in CI.
