# SOP: GET /auth/me identity endpoint

## What shipped

- Commit: `5d815b8` — `feat(auth): GET /auth/me identity endpoint`
- Branch: `feat/auth-endpoints`
- Task 12 of the Auth Endpoints plan
  (`.superpowers/sdd/2026-08-13-auth-endpoints/task-12-brief.md`).

`GET /api/v1/auth/me` — the identity/hydration endpoint the frontend calls right after
login (or on app boot with a stored access token) to get the authenticated user, their
profile, their startup memberships, and which workspace should be active by default.

## Why

Every prior auth task (signup, login, MFA, refresh/logout, password reset) issues or
validates tokens but none of them return a client-friendly snapshot of "who am I / what
can I see". Without `/me`, a frontend has no single call to resolve the logged-in state
into `{user, profile, memberships, active_workspace_id}` — it would have to decode the
JWT client-side (fragile, and doesn't carry profile/membership data at all).

## How

**`app/api/v1/endpoints/auth/me.py`** (new):

- `GET /me`, gated by `Depends(get_current_user)` — reuses the existing Bearer-token
  dependency from `app/api/deps.py` (401 on missing/invalid/disabled-user token; already
  excludes soft-deleted users).
- Queries `Membership` joined to `Startup`, filtered to `Membership.user_id == user.id`
  and `Membership.status == MembershipStatus.active` — mirrors the active-membership
  filter already established in `app/db/tenancy.py:22`, so a user's revoked/pending
  memberships never leak into this payload.
- Builds `memberships: [{startup_id, name, role}]` with `role` serialized via `.value`
  (plain string, not the raw enum) — same convention as `user.status.value` on the `user`
  block.
- `active_workspace_id` = the first membership's `startup_id`, or `null` if the user
  belongs to no active startup yet (e.g. mid-onboarding).
- `profile` is `null` when `user.profile` is `None`, otherwise
  `{full_name, role_title, country, avatar_url}` straight off `UserProfile`.
- Thin handler, no business logic beyond the query/shape — returns via
  `success_response(...)`, `-> dict[str, Any]` return type matching every other route in
  this router.

**`app/api/v1/endpoints/auth/__init__.py`** (modified): added `me` to the existing
`from app.api.v1.endpoints.auth import ...` aggregation import and
`router.include_router(me.router)`, extending the same mounting pattern used by
`login`/`mfa`/`sessions`/`password`/`registration`.

Implemented per the brief verbatim; the only deviation from the brief's inline snippet is
cosmetic `black` reformatting (wrapped the `profile` ternary and the memberships
list-comprehension onto multiple lines for the project's line-length limit) — no logic
change.

## What's involved

- `app/api/v1/endpoints/auth/me.py` (new) — the `GET /me` route.
- `app/api/v1/endpoints/auth/__init__.py` (modified) — mounts `me.router`.
- `tests/api/auth/test_me.py` (new, 2 tests) — identity + profile + membership happy
  path, and auth-required 401.
- Consumes (no changes): `app/api/deps.py::get_current_user`,
  `app/db/models/membership.py::Membership, MembershipStatus`,
  `app/db/models/startup.py::Startup`, `app/db/models/user.py::User, UserProfile`,
  `app/core/envelope.py::success_response`.
- No new DB models, no migration, no config changes.

## Verification

RED (route not mounted):
```
$ poetry run pytest tests/api/auth/test_me.py -v
FAILED test_me_returns_identity_and_memberships - assert 404 == 200
FAILED test_me_requires_auth - assert 404 == 401
2 failed
```

GREEN:
```
$ poetry run pytest tests/api/auth/test_me.py -v
2 passed
```

Full suite:
```
$ poetry run pytest -q
98 passed
```
(96 pre-existing + 2 new, no regressions, no unexpected new passes.)

Lint:
```
$ make lint
black --check app tests      -> All done! 96 files would be left unchanged.
isort --check-only app tests -> clean
ruff check app tests         -> All checks passed!
mypy app                     -> Success: no issues found in 54 source files
```

## Operate / roll back

- Pure additive route wiring over already-shipped models (`Membership`, `Startup`,
  `UserProfile`) and the already-shipped `get_current_user` dependency — no migration, no
  config change. Roll back by reverting `5d815b8`.
- Read-only endpoint (no writes, no `db.commit()`), so it carries no idempotency or
  transaction-boundary concerns.

## Follow-ups

- No test exercises the zero-active-memberships branch (`memberships: []`,
  `active_workspace_id: null`) or a membership with `status != active` being excluded.
  Both are handled by the implementation (same filter pattern as `app/db/tenancy.py`) but
  aren't asserted by a test yet — worth adding if `/me` becomes more central to
  onboarding-state decisions on the frontend.
- No pagination/limit on the memberships query — fine while a founder belongs to a
  handful of startups; revisit if that assumption changes.
