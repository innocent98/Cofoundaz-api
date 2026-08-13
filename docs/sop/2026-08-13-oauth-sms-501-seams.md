# SOP: OAuth + SMS 501 seams

## What shipped

- Commit: `24df12b` — `feat(auth): OAuth + SMS 501 seams`
- Branch: `feat/auth-endpoints`
- Task 13 of the Auth Endpoints plan
  (`.superpowers/sdd/2026-08-13-auth-endpoints/task-13-brief.md`).

Three 501 seam routes — a forward-compatible API surface for OAuth and SMS MFA:
- `POST /api/v1/auth/oauth/{provider}` — OAuth start (Google, Apple, etc.)
- `POST /api/v1/auth/mfa/sms/setup` — SMS MFA enrollment
- `POST /api/v1/auth/mfa/sms/verify` — SMS code verification

All routes exist in the API contract and return `FeatureNotEnabled` (501) until the
features are built.

## Why

Keep the API contract honest — clients (web, mobile) need to know the routes exist
(so they can wire them up in the UI flow) but also need a clear, non-ambiguous signal
that they aren't ready yet. A 404 would be confusing (is it a typo? will it exist
tomorrow?); a 501 + `FEATURE_NOT_ENABLED` error code is the HTTP standard for "this
is planned, just not implemented." The frontend can show a clear "coming soon" message
or disable the OAuth/SMS buttons until the backend is ready.

Deferred per the auth design spec — OAuth requires provider credentials (Google,
Apple) and a new `oauth_accounts` table (already shipped in Task 10); SMS MFA
requires an SMS provider integration (Twilio, etc.).

## How

**`app/api/v1/endpoints/auth/seams.py`** (new):

- Thin router with three endpoints, each raising the existing `FeatureNotEnabled`
  exception from `app/core/errors.py` (which translates to 501 + error envelope
  `{error: {code: "FEATURE_NOT_ENABLED", ...}}`).
- No business logic, no dependencies, no DB queries — pure seams.
- Return type `-> None` (matching the brief and mypy enforcement); the exception is
  raised before any return.
- Routes:
  - `@router.post("/oauth/{provider}")` — accepts a `provider: str` path param (used
    in the URL; concrete values like "google", "apple" are backend specifics, not
    validated yet).
  - `@router.post("/mfa/sms/setup")` and `@router.post("/mfa/sms/verify")` — no
    params needed for the seams (will accept JSON bodies later; ignored for now).

**`app/api/v1/endpoints/auth/__init__.py`** (modified): added `seams` to the imports
and `router.include_router(seams.router)` at the end of the aggregation list, after
all real endpoints. Placement matters: seams router mounted last means any future
real OAuth or SMS routes (if wired to a different prefix or in a different sub-router)
won't be shadowed.

**Route collision check**: The existing MFA router defines `/mfa/totp/*` (TOTP routes)
and `/mfa/challenge` (challenge-response for MFA). The seams define `/mfa/sms/*`
(SMS routes). No literal segment overlap — FastAPI routes by exact prefix match, so
`/mfa/sms/setup` ≠ `/mfa/totp/start` and won't interfere. Both routers coexist
cleanly.

## What's involved

- `app/api/v1/endpoints/auth/seams.py` (new, 20 lines) — the seams router.
- `app/api/v1/endpoints/auth/__init__.py` (modified, +1 line) — mounts seams.router.
- `tests/api/auth/test_seams.py` (new, 16 lines) — parametrized test covering all 4
  paths (2 OAuth providers, 2 SMS methods).
- Consumes (no changes): `app/core/errors.py::FeatureNotEnabled`.
- No DB models, no migration, no config changes, no new dependencies.

## Verification

RED (routes not mounted):
```
$ poetry run pytest tests/api/auth/test_seams.py -v
tests/api/auth/test_seams.py::test_seams_return_501[/api/v1/auth/oauth/google] FAILED
  assert 404 == 501
tests/api/auth/test_seams.py::test_seams_return_501[/api/v1/auth/oauth/apple] FAILED
  assert 404 == 501
tests/api/auth/test_seams.py::test_seams_return_501[/api/v1/auth/mfa/sms/setup] FAILED
  assert 404 == 501
tests/api/auth/test_seams.py::test_seams_return_501[/api/v1/auth/mfa/sms/verify] FAILED
  assert 404 == 501
4 failed
```

GREEN:
```
$ poetry run pytest tests/api/auth/test_seams.py -v
tests/api/auth/test_seams.py::test_seams_return_501[/api/v1/auth/oauth/google] PASSED
tests/api/auth/test_seams.py::test_seams_return_501[/api/v1/auth/oauth/apple] PASSED
tests/api/auth/test_seams.py::test_seams_return_501[/api/v1/auth/mfa/sms/setup] PASSED
tests/api/auth/test_seams.py::test_seams_return_501[/api/v1/auth/mfa/sms/verify] PASSED
4 passed
```

Full suite:
```
$ poetry run pytest -q
102 passed
```
(98 pre-existing + 4 new seams tests, no regressions.)

Lint:
```
$ make lint
black --check app tests       -> All done! 98 files would be left unchanged.
isort --check-only app tests  -> clean
ruff check app tests          -> All checks passed!
mypy app                      -> Success: no issues found in 55 source files
```

## Operate / roll back

- Pure additive route wiring — no migration, no config change. Roll back by
  reverting `24df12b`.
- Read-only endpoints that throw exceptions immediately (no writes, no `db.commit()`,
  no side effects).
- When real OAuth or SMS routes are implemented, replace the seams by either:
  1. Replacing the seams.py endpoints with real logic (simplest), or
  2. Creating a separate real router and removing the seams.router include.

## Follow-ups

- **OAuth**: Needs provider credentials (Google, Apple OAuth apps), the `oauth_accounts`
  table (Task 10, already shipped), and the actual OAuth flow (token exchange, user
  creation/lookup, linking to existing users). Estimated future task.
- **SMS MFA**: Needs SMS provider integration (Twilio, etc.), rate limiting on
  verification attempts (to prevent brute force), and the challenge-response loop.
  Estimated future task.
- These seams can be left in place permanently as documentation of the feature
  roadmap — clients will see 501 responses until the real features ship.
