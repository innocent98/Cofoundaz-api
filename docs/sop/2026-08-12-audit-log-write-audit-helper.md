# SOP: Audit log model + write_audit helper

## What shipped

- Commit: (this task's commit — see subject `feat(platform): audit_log model + write_audit helper`)
- Branch: `design/auth-onboarding-foundation`
- Task 11 of the Foundation & Tenancy Spine plan
  (`.superpowers/sdd/2026-08-12-foundation-tenancy-spine/task-11-brief.md`).

Added the `AuditLog` model (table `audit_log`) and a `write_audit(...)` helper that
builds, adds, and flushes an audit row. This is the write-side primitive that later
auth/onboarding/tenancy tasks will call from request handlers to record
security-relevant actions (logins, on-behalf-of actions, entity mutations, etc.).

## Why

Later modules (auth, admin impersonation, tenancy mutations) need a durable,
queryable trail of who did what, optionally on behalf of whom, against which
startup/entity, along with before/after hashes for tamper-evidence. This task lays
the model + single write helper so callers don't hand-roll `AuditLog(...)` +
`db.add` + `db.flush()` at every call site.

## How

- **`app/db/models/audit.py`**: `AuditLog(UUIDMixin, TimestampMixin, Base)`, table
  `audit_log`. Columns: `actor_user_id`, `on_behalf_of_user_id`, `startup_id`,
  `entity_id` (all `uuid.UUID | None`, `PGUUID(as_uuid=True)`); `action: str`
  (`String(120)`, required); `entity_type: str | None` (`String(80)`);
  `before_hash`/`after_hash: str | None` (`String(64)`); `ip: str | None`
  (`String(64)`); `user_agent: str | None` (unbounded `String`). Two composite
  indexes: `ix_audit_log_startup_created (startup_id, created_at)` and
  `ix_audit_log_actor_created (actor_user_id, created_at)` for the two expected
  query patterns (audit trail for a startup, audit trail for an actor).
- **`app/platform/audit.py`**: `write_audit(db, action, *, actor_user_id=None,
  on_behalf_of_user_id=None, startup_id=None, entity_type=None, entity_id=None,
  before_hash=None, after_hash=None, ip=None, user_agent=None) -> AuditLog`. Builds
  an `AuditLog`, `db.add` + `db.flush()` (no commit — caller controls the
  transaction, same convention as `JobDispatcher.enqueue` from Task 10), returns
  the row.
- **`app/db/models/__init__.py`**: now also imports `AuditLog` so `Base.metadata`
  (and `create_all` in the test harness) picks up the `audit_log` table.

Deviation from the brief's verbatim snippets: used the repo's established
`X | None` union-type style (matching `app/db/models/job.py`) instead of the
brief's `typing.Optional[X]`, since ruff's `UP` (pyupgrade) rule is enabled
repo-wide and `typing.Optional` would fail lint on a Python 3.11 target. No
behavioral difference — same nullability, same columns, same helper signature and
logic as specified.

## What's involved

- `app/db/models/audit.py` (new) — `AuditLog` model, 29 lines.
- `app/platform/audit.py` (new) — `write_audit` helper.
- `app/db/models/__init__.py` (modified) — registers `AuditLog` on `Base.metadata`.
- `tests/platform/test_audit.py` (new) — helper persistence test.
- No Alembic migration (deferred to Task 13, same as `jobs` — table exists only via
  `Base.metadata.create_all` in the test harness for now). No tenancy models.

## Verification

RED (before implementation — model/platform files temporarily removed, `__init__.py`
still importing them):
```
$ poetry run pytest tests/platform/test_audit.py -v
ModuleNotFoundError: No module named 'app.db.models.audit'
```

GREEN (after implementation):
```
$ poetry run pytest tests/platform/test_audit.py -v
tests/platform/test_audit.py::test_write_audit_persists PASSED
1 passed
```

Full suite:
```
$ poetry run pytest
25 passed
```

Coverage: `app/db/models/audit.py` 100%, `app/platform/audit.py` 100%.

Scoped lint/format/type-check (touched files only — repo-wide `make lint` still
fails on pre-existing debt in `app/api/deps.py` (B008/B904), unrelated to this
task):
```
$ poetry run black --check app/db/models/audit.py app/platform/audit.py \
    app/db/models/__init__.py tests/platform/test_audit.py
All done! 4 files would be left unchanged.

$ poetry run isort --check-only <same files>
(no output — clean)

$ poetry run ruff check <same files>
All checks passed!

$ poetry run mypy app/db/models/audit.py app/platform/audit.py
Success: no issues found in 2 source files
```

## Operate / roll back

- Pure additive change: new table (created via `create_all` in tests; no
  migration/deploy step yet), no config or env changes.
- To roll back: revert this task's commit. No data migration concerns since no
  Alembic migration was created.
- **Update (Task 13):** the `audit_log` table now has a real Alembic migration —
  `alembic/versions/0001_initial_schema.py` — see
  `docs/sop/2026-08-12-initial-alembic-migration.md`. Roll back via
  `alembic downgrade -1`, not just a commit revert, once that migration has been
  applied to an environment.

## Follow-ups

- ~~Task 13 adds the Alembic migration for `audit_log` (and other models
  accumulated so far).~~ Done — see
  `docs/sop/2026-08-12-initial-alembic-migration.md`.
- No read/query endpoint yet — only the write path (`write_audit`) exists per this
  task's scope. A future task will likely add an admin-facing read endpoint
  (e.g. `GET /audit-log` filtered by `startup_id` or `actor_user_id`, using the
  indexes added here).
- Callers (auth handlers, admin impersonation, tenancy mutation endpoints) still
  need to be wired up to call `write_audit(...)` — this task only ships the
  primitive.
