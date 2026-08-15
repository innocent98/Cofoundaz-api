# SOP: RBAC + tenancy resolution

## What shipped

- Commit: `feat(platform): RBAC role gate + workspace tenancy resolution`
- Branch: `design/auth-onboarding-foundation`
- Task 15 of the Foundation & Tenancy Spine plan
  (`.superpowers/sdd/2026-08-12-foundation-tenancy-spine/task-15-brief.md`).

Added the Redis singleton client (`app/core/redis.py`) and the workspace tenancy/RBAC layer
(`app/db/tenancy.py`): `resolve_workspace`, the `require_workspace` FastAPI dependency (reads
`X-Workspace-Id`), `require_role(*roles)` dependency factory, and `tenant_scope` query helper.

## Why

Every multi-tenant endpoint from here on needs to (a) resolve which workspace (startup) the
caller is acting in, (b) confirm the caller has an *active* membership in that workspace (not
suspended/removed, and not simply "any membership ever existed"), and (c) gate certain routes to
specific membership roles (e.g. only `founder`/`accountant` can see billing). This task builds
that resolution + gating layer on top of Task 14's `get_current_user` and Task 12's `Membership`
model, so later endpoint tasks can just depend on `require_workspace` / `require_role(...)`
instead of re-deriving tenancy checks per-route.

## How

**`app/core/redis.py`** — module-level `_client: redis.Redis | None` singleton; `get_redis()`
lazily creates it via `redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)` and
returns the cached instance thereafter. Matches the brief verbatim. No caching logic is wired
into `resolve_workspace` yet — the brief explicitly scopes that out as a later optimization; this
task only stands up the client.

**`app/db/tenancy.py`**:
- `resolve_workspace(db, user, workspace_id) -> Membership` — queries
  `Membership.user_id == user.id AND Membership.startup_id == workspace_id AND Membership.status
  == MembershipStatus.active`, `.first()`. `None` → raises `Forbidden` (403). One query covers
  both "user not a member of this workspace" and "workspace doesn't exist" — both look identical
  from the caller's side (no membership row matches), which is the desired behavior: don't leak
  workspace existence to non-members.
- `require_workspace(x_workspace_id, user, db) -> Membership` — FastAPI dependency; header param
  is `UUID = Header(..., alias="X-Workspace-Id")` (required, no default), `user` depends on
  `get_current_user` (Task 14), `db` depends on `get_db`. Delegates to `resolve_workspace`.
- `require_role(*roles) -> Callable[..., Membership]` — dependency factory; the returned `_dep`
  depends on `require_workspace` and 403s (`Forbidden`) unless `membership.role in roles`. Empty
  `roles` means no restriction (matches the brief's `if roles and ...` short-circuit).
- `tenant_scope(query, startup_id, model) -> Query` — thin `.filter(model.startup_id ==
  startup_id)` wrapper for scoping list/detail queries to the resolved workspace.

**Type annotations added beyond the brief's literal snippet** (needed for `mypy` with
`disallow_untyped_defs = true`, which the brief's snippet doesn't satisfy as-written):
- `require_role(*roles: MembershipRole) -> Callable[..., Membership]` (brief has bare `def
  require_role(*roles):`) — imports `MembershipRole` from `app.db.models.enums` and `Callable`
  from `collections.abc`.
- `tenant_scope(..., model: type[Any])` (brief has bare `model`) — imports `Any` from `typing`.

No behavioral deviation from the brief — only added static types so the module passes the
project's `mypy` gate.

**`.env` fix (local dev only, not committed — `.env` is gitignored)**: `REDIS_URL` was
`redis://localhost:6379/0`, but docker-compose maps the `redis` container's `6379` to host port
`6378` (`docker-compose.yml`: `redis: ports: ["6378:6379"]`), consistent with how
`DATABASE_URL`/`TEST_DATABASE_URL` already point at the host-mapped Postgres port (`5433`, not
`5432`). Updated to `redis://localhost:6378/0` so `get_redis()` actually reaches the running
compose Redis when the app/tests run outside Docker. Verified with a live `PING`/`SET`/`GET`
smoke test (see Verification). `.env.example` left as the generic `6379` default, matching its
existing convention for `DATABASE_URL` (`5432` there vs. `5433` in the real `.env`).

## What's involved

- `app/core/redis.py` (new, 12 lines) — `get_redis()` singleton.
- `app/db/tenancy.py` (new, ~48 lines) — `resolve_workspace`, `require_workspace`,
  `require_role`, `tenant_scope`.
- `tests/db/test_tenancy.py` (new) — 3 tests: `test_resolve_workspace_ok`,
  `test_resolve_workspace_forbidden_for_non_member`,
  `test_resolve_workspace_forbidden_for_unknown_workspace`.
- `.env` (local, untracked) — `REDIS_URL` port fix, `6379` → `6378`.
- Depends on: `app/api/deps.py::get_current_user` (Task 14), `app/db/models/membership.py::
  Membership`/`MembershipStatus` (Task 12), `app/db/models/enums.py::MembershipRole`,
  `app/core/errors.py::Forbidden` (Task 5), `app/db/session.py::get_db`, `app/core/config.py::
  settings.REDIS_URL` (Task 3), `tests/factories.py::create_user`/`create_startup`/
  `create_membership`, `tests/conftest.py`'s `db` fixture.
- No endpoints and no rate limiting — both explicitly out of scope for this task (rate limiting is
  Task 16). `require_workspace`/`require_role` are not yet wired into any route.

## Verification

RED (before implementation):
```
$ poetry run pytest tests/db/test_tenancy.py -v
ERROR tests/db/test_tenancy.py - ModuleNotFoundError: No module named 'app.db.tenancy'
Interrupted: 1 error during collection
```

GREEN (after implementation):
```
$ poetry run pytest tests/db/test_tenancy.py -v
tests/db/test_tenancy.py::test_resolve_workspace_ok PASSED
tests/db/test_tenancy.py::test_resolve_workspace_forbidden_for_non_member PASSED
tests/db/test_tenancy.py::test_resolve_workspace_forbidden_for_unknown_workspace PASSED
3 passed
```

Full suite:
```
$ poetry run pytest
35 passed
```

Scoped format/lint — `app/core/redis.py`, `app/db/tenancy.py`, `tests/db/test_tenancy.py` clean
on all 4 tools:
```
$ poetry run black --check app/core/redis.py app/db/tenancy.py tests/db/test_tenancy.py
All done! 3 files would be left unchanged.
$ poetry run isort --check-only app/core/redis.py app/db/tenancy.py tests/db/test_tenancy.py
(no output — clean)
$ poetry run ruff check app/core/redis.py app/db/tenancy.py tests/db/test_tenancy.py
All checks passed!
$ poetry run mypy app/core/redis.py app/db/tenancy.py
Success: no issues found in 2 source files
```

Repo-wide `poetry run mypy app` reports 19 pre-existing errors in 8 files (`config.py`,
`session.py`, `security.py`, `logger.py`, `errors.py`, `health.py`, `jobs.py`, `main.py`) —
**none in `redis.py` or `tenancy.py`**, confirmed by grepping the output. All pre-existing debt,
not introduced by this task.

Redis connectivity smoke test (after the `.env` port fix), against the compose Redis container:
```
$ poetry run python -c "from app.core.redis import get_redis; r = get_redis(); print(r.ping()); r.set('task15:smoke','ok',ex=5); print(r.get('task15:smoke'))"
True
ok
```

## Operate / roll back

- Pure application-layer addition; no migrations, no new config keys (reuses existing
  `settings.REDIS_URL`).
- Redis must be reachable at `settings.REDIS_URL` for `get_redis()` calls to succeed; nothing
  currently calls it outside this task's own manual smoke test, so there's no runtime dependency
  introduced yet.
- To roll back: revert this task's commit; delete/revert the local `.env` port change (does not
  affect other environments, since `.env` is gitignored).

## Follow-ups

- `resolve_workspace` is a direct DB query with no caching — the brief explicitly defers Redis
  caching of workspace/membership resolution to a later optimization pass.
- `require_workspace`/`require_role` aren't wired into any endpoint yet; the next task(s) that add
  workspace-scoped routes should depend on them instead of re-deriving tenancy checks.
- `tenant_scope` assumes `model` has a `startup_id` column; no runtime check enforces that — relies
  on call-site correctness (static `type[Any]` gives no guarantee here).
- Task 16 (rate limiting) is expected to use `app/core/redis.py::get_redis()` — out of scope here.
- **Pre-existing, out-of-scope working-tree drift** (carried over from Task 14's SOP, still
  unresolved): `app/api/v1/endpoints/health.py`, `app/core/logger.py`, and `app/core/security.py`
  remain modified/uncommitted in the working tree, unrelated to this task. Left untouched again to
  keep this task's diff scoped to Task 15 files.
