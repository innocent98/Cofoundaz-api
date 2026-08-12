# SOP: Final-review fix wave — mypy/lint green, JobStatus relocation, config + warnings hygiene

## What shipped

- Branch: `design/auth-onboarding-foundation`
- Follow-up commit(s) after Task 16, covering the MUST-FIX/SHOULD-FIX items from a whole-branch
  final review of the "Foundation & Tenancy Spine" plan
  (`.superpowers/sdd/2026-08-12-foundation-tenancy-spine/final-fix-report.md`).

This doc covers the lint/typing/config/enum-placement/warnings portion of that fix wave (findings
#3–#7). The rate-limiting middleware fix (#1) and the `get_current_user` soft-delete fix (#2) are
documented as updates to their own existing SOPs:
`docs/sop/2026-08-12-rate-limiting-coverage-gate.md` and
`docs/sop/2026-08-12-real-get-current-user-dependency.md`.

## Why

A whole-branch review found `make lint` red (19 mypy errors accumulated across tasks 1–16, never
cleared), first-party `datetime.utcnow()` deprecation warnings, a pydantic v2 class-based `Config`
deprecation, `JobStatus` defined inline in `job.py` inconsistent with every other enum living in
`app/db/models/enums.py`, and ~2000 pytest warnings (mostly third-party asyncio noise from running
a Python 3.14 venv against a Python 3.11-targeted project) drowning out anything first-party.

## How

**#3 — `make lint` green (19 → 0 mypy errors)**. Added missing type annotations across:
- `app/db/session.py:15` — `get_db() -> Generator[Session, None, None]` (was untyped).
- `app/core/security.py` — `cast(str, ...)`/`cast(bool, ...)` on the three functions returning
  values from untyped third-party calls (`jose.jwt.encode`, `passlib` `verify`/`hash`), which mypy
  flagged as `no-any-return`.
- `app/core/logger.py:8` — `setup_logging() -> "Logger"`, importing `Logger` from `loguru` only
  under `if TYPE_CHECKING:` (loguru ships a `.pyi` stub with a `Logger` class but does **not**
  export it at runtime — a naive `from loguru import Logger, logger` at module level raises
  `ImportError` at import time; caught by re-running the test suite after the first attempt, see
  Verification).
- `app/core/errors.py` — `AppError.__init__` params typed (`code: str | None`, etc.,
  `field_errors: list[dict[str, Any]] | None`) and `-> None`; both nested exception-handler
  closures (`_app_error`, `_validation`) annotated `-> JSONResponse`.
- `app/api/v1/endpoints/health.py:9`, `app/api/v1/endpoints/jobs.py:15` — `-> dict[str, str]` /
  `-> dict[str, Any]`.
- `app/main.py` — `LoggingMiddleware.dispatch` params typed
  (`call_next: Callable[[Request], Awaitable[Response]]`) and `-> Response`; `root`, `health_check`
  `-> dict[str, str]`; `start() -> None`.
- `app/core/config.py:80` — `Settings()` triggers a mypy `call-arg` false positive (mypy expects
  the constructor args that pydantic-settings actually resolves from the environment/`.env` at
  runtime). Suppressed with a targeted `# type: ignore[call-arg]` plus a comment explaining why,
  per the brief's guidance — not a blanket ignore.

**#4 — timezone-aware datetimes**. `app/core/security.py` and
`app/api/v1/endpoints/health.py`: replaced `datetime.utcnow()` with `datetime.now(timezone.utc)`.
Note: `make format`'s `ruff --fix` pass (pyupgrade/`UP` rule) subsequently rewrote
`timezone.utc` → the Python 3.11+ `datetime.UTC` alias and the `timezone`/`UTC` imports
accordingly — same runtime value, just ruff's preferred spelling on this target version.

**#5 — pydantic v2 settings config**. `app/core/config.py`: replaced the nested `class Config:
case_sensitive = True; env_file = ".env"` with `model_config = SettingsConfigDict(env_file=".env",
case_sensitive=True)`, importing `SettingsConfigDict` from `pydantic_settings`. Clears the
class-based-config deprecation warning; behavior unchanged (same two settings).

**#6 — `JobStatus` moved to `app/db/models/enums.py`**. Previously defined inline in
`app/db/models/job.py` (the only model-local enum; every other enum — `UserStatus`, `MfaType`,
`BusinessModel`, `StartupStage`, `MembershipRole`, `MembershipStatus` — lives in `enums.py`).
Moved `JobStatus` verbatim into `enums.py`, and `job.py` now does
`from sqlalchemy import Enum` (bare, matching `user.py`/`startup.py`/`membership.py`'s import
style) instead of `from sqlalchemy import Enum as SAEnum`, plus
`from app.db.models.enums import JobStatus`. Updated both other references:
`app/platform/jobs.py` (`from app.db.models.enums import JobStatus`, `from app.db.models.job
import Job`) and `tests/platform/test_jobs.py` (same). `app/api/v1/endpoints/jobs.py` only ever
imported `Job` (not `JobStatus`) so needed no change.
- **DB representation unchanged**: the column is still `Enum(JobStatus, native_enum=False,
  length=20)` → the same `VARCHAR(20)` storing the same string values. Confirmed zero migration
  drift: ran `poetry run alembic revision --autogenerate -m _tmp --rev-id _tmp` after the move,
  inspected the generated file (`upgrade()`/`downgrade()` both just `pass`), then deleted the temp
  revision file — no `alembic/versions/_tmp__tmp.py` committed.

**#7 — `filterwarnings` in `pyproject.toml`**. Added under `[tool.pytest.ini_options]`:
```toml
filterwarnings = [
    "ignore:.*iscoroutinefunction.*:DeprecationWarning",
    "ignore:.*get_event_loop_policy.*:DeprecationWarning",
]
```
Targets the specific third-party `asyncio.iscoroutinefunction`/`asyncio.get_event_loop_policy`
`DeprecationWarning`s emitted by `pytest_asyncio`, `fastapi`, and `slowapi` internals under Python
3.14 (the venv's interpreter; the project targets 3.11 per `pyproject.toml`'s
`target-version`/`python_version`). Deliberately **not** a blanket `ignore::DeprecationWarning` —
first-party `app.*` warnings (e.g. the `datetime.utcnow()` ones fixed in #4) remain visible if
reintroduced.

## What's involved

- Typing/lint: `app/db/session.py`, `app/core/security.py`, `app/core/logger.py`,
  `app/core/errors.py`, `app/api/v1/endpoints/health.py`, `app/api/v1/endpoints/jobs.py`,
  `app/main.py`, `app/core/config.py`.
- `datetime.utcnow()` → `datetime.now(UTC)`: `app/core/security.py`, `app/api/v1/endpoints/
  health.py`.
- Pydantic settings: `app/core/config.py`.
- `JobStatus` relocation: `app/db/models/enums.py` (new class), `app/db/models/job.py`,
  `app/platform/jobs.py`, `tests/platform/test_jobs.py`.
- Pytest config: `pyproject.toml` (`[tool.pytest.ini_options]`).
- No migrations added (drift-checked and confirmed empty, temp revision deleted).

## Verification

`make lint` (black + isort + ruff + mypy), before → after:
```
# before: 19 mypy errors across 8 files (session.py, security.py, logger.py, errors.py,
#         health.py, jobs.py, main.py, config.py); black/isort/ruff already clean.
# after:
$ make lint
poetry run black --check app tests      → All done! 67 files would be left unchanged.
poetry run isort --check-only app tests → (clean)
poetry run ruff check app tests         → All checks passed!
poetry run mypy app                     → Success: no issues found in 40 source files
```

`poetry run alembic revision --autogenerate -m _tmp --rev-id _tmp` after the `JobStatus` move
produced an empty `upgrade()`/`downgrade()` (no drift); file deleted immediately after inspection.

Full suite: `poetry run pytest -q` → 39 passed, 0 warnings (down from 36 passed / 2064 warnings
before this fix wave — the delta includes the 3 new tests from findings #1/#2 plus the resolved
warnings).

Full numbers, and the exact final `make lint` / `pytest` transcripts, are in
`.superpowers/sdd/2026-08-12-foundation-tenancy-spine/final-fix-report.md`.

## Operate / roll back

- No migrations, no config-value changes (only pydantic's config *declaration* style changed, not
  any setting's name/default/behavior).
- `JobStatus` import path changed (`app.db.models.job.JobStatus` → `app.db.models.enums
  .JobStatus`) — any code outside this repo importing the old path would break; grepped
  repo-wide, nothing else referenced it.
- To roll back: revert this fix-wave's commit(s). No data migration needed either direction (same
  VARCHAR values on the wire).

## Follow-ups

- None specific to this doc — see the DEFER list in `final-fix-report.md` for items explicitly
  out of scope for this pass (`db.get` vs `db.query().first()`, `tenant_scope` Protocol typing,
  validation-handler `body`-segment strip, `updated_at` onupdate note, empty-string-email
  handling, stale SOP rollback SHA, inert `# noqa: S106`, untested 403/expired-token/bad-uuid
  branches).
