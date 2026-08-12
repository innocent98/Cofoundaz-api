# SOP: Tenancy spine models (User/Startup/Membership) + factories

## What shipped

- Commit: `feat(db): tenancy spine models (user/startup/membership) + factories`
- Branch: `design/auth-onboarding-foundation`
- Task 12 of the Foundation & Tenancy Spine plan
  (`.superpowers/sdd/2026-08-12-foundation-tenancy-spine/task-12-brief.md`).

Added the core multi-tenant data model every later module depends on:
- Six shared enums (`app/db/models/enums.py`): `UserStatus`, `MfaType`, `BusinessModel`,
  `StartupStage`, `MembershipRole`, `MembershipStatus`.
- `User` + `UserProfile` (1:1, cascade delete) — auth/account state plus profile fields.
- `Startup` + `StartupProfile` (1:1, cascade delete) — workspace/tenant plus onboarding fields.
- `Membership` — join table linking a `User` to a `Startup` with a role, unique per
  `(user_id, startup_id)`.
- `tests/factories.py` — `create_user`, `create_startup`, `create_membership` helpers for tests.

## Why

This is the tenancy spine: every subsequent module (auth, RBAC, onboarding, startup features)
needs `User`, `Startup`, and `Membership` to exist with the exact columns/enums/relationships
specified in the Foundation & Tenancy Spine design, so downstream tasks can build on a stable
contract without churn.

## How

Followed the model style established in Tasks 10–11 (`app/db/models/job.py`, `audit.py`):
SQLAlchemy 2.0 typed `Mapped[...]` columns, `PGUUID(as_uuid=True)` for UUID FKs/PKs,
`sa.Enum(..., native_enum=False, length=N)` for Python enum columns (portable across DB
backends, no native Postgres enum type to migrate later), and the `UUIDMixin` / `TimestampMixin`
/ `SoftDeleteMixin` mixins from `app/db/mixins.py`.

Key decisions:
- **`users.email` as `CITEXT`**: case-insensitive unique email lookups without a functional
  index. Requires the Postgres `citext` extension, which is not enabled by default — added
  `CREATE EXTENSION IF NOT EXISTS citext` to the session-scoped `engine` fixture in
  `tests/conftest.py`, run once before `Base.metadata.create_all`.
- **`startup_profiles.goals` as `ARRAY(String)`**: matches the brief's requirement for a
  simple string list with a default empty list, avoiding a separate goals table for what is
  free-form onboarding data.
- **Enum column naming — no `SAEnum` alias**: the brief's snippet (and Task 10/11 precedent)
  aliases `from sqlalchemy import Enum as SAEnum`. In `job.py`, that alias is necessary because
  the file also does `import enum` (stdlib) to define its own enum class. `user.py`, `startup.py`,
  and `membership.py` only *consume* enums from `app/db/models/enums.py` — they never import the
  stdlib `enum` module — so there's no name collision. Aliasing anyway triggered a genuine
  `isort` vs `ruff` (`I001`) disagreement: whenever a plain multi-name import (e.g.
  `DateTime, ForeignKey, Integer, String`) must be split around an aliased import that sorts
  alphabetically *between* some of those names, `isort` and `ruff` pick different, mutually
  incompatible splits — no formatting of the merged/aliased import satisfies both tools
  simultaneously (verified experimentally: every arrangement failed one or the other). Dropping
  the alias and importing `Enum` directly removes the ambiguity and satisfies `black`, `isort`,
  and `ruff` at once. `Enum(...)` is used directly in place of `SAEnum(...)` in all three files.
- **Membership uniqueness**: explicit `UniqueConstraint("user_id", "startup_id",
  name="uq_memberships_user_id_startup_id")` rather than relying on the naming-convention
  default (which would only key off the first column) — required by the brief, and it's also
  what makes `test_membership_unique_per_workspace` meaningful.
- **Cascades**: `User.profile` and `Startup.profile` use `cascade="all, delete-orphan"` with a
  DB-level `ondelete="CASCADE"` FK, so profile rows are removed both via ORM-level delete and
  raw SQL delete.

## What's involved

- `app/db/models/enums.py` (new) — six `(str, enum.Enum)` classes.
- `app/db/models/user.py` (new) — `User`, `UserProfile`.
- `app/db/models/startup.py` (new) — `Startup`, `StartupProfile`.
- `app/db/models/membership.py` (new) — `Membership`.
- `app/db/models/__init__.py` (modified) — registers the five new models on `Base.metadata`
  (appended after the existing `AuditLog`, `Job` imports).
- `tests/factories.py` (new) — `create_user`, `create_startup`, `create_membership`.
- `tests/db/test_tenancy_models.py` (new) — 3 tests (see Verification).
- `tests/conftest.py` (modified) — `engine` fixture now runs
  `CREATE EXTENSION IF NOT EXISTS citext` before `create_all`.
- No Alembic migration (explicitly out of scope — Task 13). No security/RBAC (Tasks 14–15).

## Verification

RED (before implementation):
```
$ poetry run pytest tests/db/test_tenancy_models.py -v
ModuleNotFoundError: No module named 'app.db.models.enums'
```

GREEN (after implementation):
```
$ poetry run pytest tests/db/test_tenancy_models.py -v
tests/db/test_tenancy_models.py::test_user_defaults PASSED
tests/db/test_tenancy_models.py::test_membership_unique_per_workspace PASSED
tests/db/test_tenancy_models.py::test_startup_profile_goals_array PASSED
======================= 3 passed ========================
```

Full suite:
```
$ poetry run pytest -q
======================= 28 passed, 1545 warnings in 0.41s ========================
```
(Warnings are pre-existing `DeprecationWarning`s from `asyncio`/`fastapi`/`pydantic`, unrelated
to this change.)

Scoped lint (Task 12 files only — `black --check`, `isort --check-only`, `ruff check`, `mypy`
all pass clean):
```
$ poetry run black --check app/db/models/enums.py app/db/models/user.py app/db/models/startup.py \
    app/db/models/membership.py app/db/models/__init__.py tests/factories.py \
    tests/db/test_tenancy_models.py tests/conftest.py
All done! 8 files would be left unchanged.

$ poetry run isort --check-only <same files>
(no output — clean)

$ poetry run ruff check <same files>
All checks passed!

$ poetry run mypy app/db/models/enums.py app/db/models/user.py app/db/models/startup.py \
    app/db/models/membership.py app/db/models/__init__.py
Success: no issues found in 5 source files
```

Repo-wide `make lint` / `make format` still fail on pre-existing debt in `app/api/deps.py`
(`B008`, `B904`), `app/api/v1/endpoints/health.py`, and `app/core/security.py` — unrelated to
this task, not touched by it.

## Operate / roll back

- **Update (Task 13):** these tables now have a real Alembic migration —
  `alembic/versions/0001_initial_schema.py` — see
  `docs/sop/2026-08-12-initial-alembic-migration.md`. Roll back via
  `alembic downgrade -1`, not just a commit revert, once that migration has been
  applied to an environment.
- To roll back (pre-migration/model-only revert): `tests/conftest.py`'s `citext`
  extension bootstrap is safe to leave in place even without these models
  (idempotent `CREATE EXTENSION IF NOT EXISTS`).

## Follow-ups

- ~~Task 13: generate the Alembic migration for `users`, `user_profiles`,
  `startups`, `startup_profiles`, `memberships`.~~ Done — see
  `docs/sop/2026-08-12-initial-alembic-migration.md`.
- Tasks 14–15: security deps (password hashing, JWT) and RBAC will build on `User` /
  `Membership.role`.
- The `_Sample` test-only table in `tests/db/test_mixins.py` (flagged as an optional Task 12
  cleanup item in the Task 2 SOP) was left as-is — it's harmless, still test-only, and removing
  it wasn't part of this task's brief/file list.
