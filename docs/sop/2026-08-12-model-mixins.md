# SOP: Model mixins (UUID/Timestamp/SoftDelete)

## What shipped

- Commit: `e0e91d6` — "feat(db): UUID/Timestamp/SoftDelete model mixins"
- Branch: `design/auth-onboarding-foundation`
- Task 2 of the Foundation & Tenancy Spine plan
  (`.superpowers/sdd/2026-08-12-foundation-tenancy-spine/task-2-brief.md`).

Added three reusable SQLAlchemy model mixins that every domain model will inherit:
- `UUIDMixin`: UUID v4 primary key with Postgres `UUID(as_uuid=True)` type.
- `TimestampMixin`: `created_at` and `updated_at` timezone-aware timestamps with server defaults.
- `SoftDeleteMixin`: nullable `deleted_at` column for soft deletes.

## Why

Domain models need consistent patterns for:
1. **Primary keys**: UUID v4 app-generated PKs (not database-generated sequences).
2. **Audit timestamps**: `created_at` (immutable) and `updated_at` (auto-updated on change).
3. **Soft deletes**: `deleted_at` column for logical deletion without data loss.

Mixins avoid repeated boilerplate and ensure uniform behavior across all models.

## How

**`app/db/mixins.py`**: Three mixin classes using SQLAlchemy 2.0 typed style:
- `UUIDMixin`: `id: Mapped[uuid.UUID]` with `PGUUID(as_uuid=True)` type column, `default=uuid.uuid4`.
- `TimestampMixin`: Two `DateTime(timezone=True)` columns with `server_default=func.now()` and `onupdate=func.now()` for `updated_at`.
- `SoftDeleteMixin`: One nullable `DateTime(timezone=True)` column with `default=None`.

**`tests/db/test_mixins.py`**: Single test that verifies:
1. Test model `_Sample` inherits all three mixins and `Base`.
2. Flush/refresh populates `id`, `created_at`, `updated_at` from defaults.
3. `deleted_at` starts as `None`.

The `_Sample` table is test-only and created by the session-scoped `engine` fixture's `create_all`
(acceptable per foundation design — will be removed in Task 12 cleanup).

## What's involved

- `app/db/mixins.py` (new) — three mixin classes, 25 lines total.
- `tests/db/__init__.py` (new) — empty package marker.
- `tests/db/test_mixins.py` (new) — single test verifying mixin behavior.
- No config changes, no migrations.

## Verification

RED (before implementation):
```
$ poetry run pytest tests/db/test_mixins.py -v
E   ModuleNotFoundError: No module named 'app.db.mixins'
```

GREEN (after implementation):
```
$ poetry run pytest tests/db/test_mixins.py -v
tests/db/test_mixins.py::test_mixins_populate_defaults PASSED
```

Full suite after implementation:
```
$ poetry run pytest
tests/db/test_mixins.py::test_mixins_populate_defaults PASSED
tests/test_harness.py::test_db_fixture_is_postgres PASSED
tests/test_harness.py::test_rollback_isolation_first PASSED
tests/test_harness.py::test_rollback_isolation_second PASSED
tests/test_health.py::test_health_check PASSED
tests/test_health.py::test_root PASSED
======================= 6 passed ========================
```

Coverage: `app/db/mixins.py` has 100% (12 statements).

## Operate / roll back

- Mixins are a pure foundation layer with no deployment/config side effects.
- To roll back: revert commit `e0e91d6` and remove `tests/db/` directory.
- Subsequent models will inherit these mixins; removing them later requires
  updating all model definitions.

## Follow-ups

- Task 3+ will create domain models inheriting these mixins.
- The `_Sample` test table in `Base.metadata` should be cleaned up in Task 12
  (per the plan's final cleanup pass).
