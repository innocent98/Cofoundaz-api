# SOP: Signup, verify, and resend-verification endpoints

## What shipped

- Commit: (this task's commit — see subject `feat(auth): signup, verify, resend endpoints`)
- Branch: `feat/auth-endpoints`
- Task 7 of the Auth Endpoints plan
  (`.superpowers/sdd/2026-08-13-auth-endpoints/task-7-brief.md`).

The first real auth HTTP endpoints: `POST /api/v1/auth/signup`,
`POST /api/v1/auth/verify`, and `POST /api/v1/auth/verify/resend`, plus the
`app/schemas/auth.py` request models and the `auth` router mount. This makes the
frontend's `/signup` flow real for the first time — previously only the
service-layer building blocks (password policy, token issue/consume, email
sender, audit, event bus) existed with no HTTP surface.

## Why

Module 01 of the PRD needs a working signup → verify email loop before login/MFA
(later tasks) can be layered on. The service-layer primitives were already built
and tested in isolation (Tasks 1–6); this task wires them into thin FastAPI routes
per the project's endpoint-is-a-thin-adapter convention.

## How

- **`app/schemas/auth.py`** (new): `SignupRequest`, `TokenRequest`, `EmailRequest`
  request models for this task's three endpoints, plus `LoginRequest`,
  `MfaChallengeRequest`, `TotpVerifyRequest`, `RefreshRequest`,
  `ForgotPasswordRequest`, `ResetPasswordRequest` — defined now (per the brief) so
  later auth tasks (login, MFA, refresh, password reset) reuse the same file
  without a follow-up schema PR.
- **`app/api/v1/endpoints/auth/registration.py`** (new):
  - `POST /signup` (201): `validate_password_strength` first (422 `WEAK_PASSWORD`
    on failure; **since 2026-08-29 also 422 `PASSWORD_TOO_LONG` above 72 UTF-8
    bytes** — see `docs/sop/2026-08-29-passlib-to-bcrypt5-migration.md`), then
    duplicate-email check (409 `EMAIL_TAKEN`), then creates a
    pending `User` + `UserProfile`, issues a 24h `email_verification` token, sends
    the verification email via `get_email_sender()`, writes an
    `auth.user.registered` audit row, publishes `auth.user.registered` on the
    event bus, and commits. Returns
    `{user: {id, email}, verification_sent: true}`.
  - `POST /verify` (200): consumes the token via `consume_auth_token` (raises
    `TokenInvalid` → 400 `TOKEN_INVALID` if bad/expired/consumed), sets
    `status=active` + `email_verified_at=now(UTC)`, publishes
    `auth.user.verified`, commits.
  - `POST /verify/resend` (200): looks up a still-`pending_verification` user by
    email; if found, re-issues a token and re-sends; always returns
    `{sent: true}` regardless of whether the user/email exists, so the endpoint
    can't be used to enumerate registered emails.
  - Shared `_send_verification(db, user)` helper issues the token and sends the
    email — used by both signup and resend.
- **`app/api/v1/endpoints/auth/__init__.py`** (new): aggregates
  `registration.router` under a plain `APIRouter()` — the pattern later auth
  tasks (login, mfa, refresh) will extend by adding more
  `router.include_router(...)` calls here.
- **`app/api/v1/api.py`** (modified): mounts the auth router at prefix `/auth`,
  tag `auth`.
- **`tests/conftest.py`** (modified): the `client` fixture now sets
  `app.state.limiter.enabled = False` before yielding the `TestClient` and
  restores `True` in the same teardown that clears `dependency_overrides`. The
  app has a live SlowAPI limiter (120/min per IP via `default_limits` +
  `SlowAPIMiddleware`); as the auth endpoint suite grows, many requests within one
  test run could otherwise trip that shared limit and produce flaky 429s
  unrelated to what's under test. `tests/api/test_rate_limit.py` is unaffected —
  it builds its own standalone `FastAPI` app rather than using the `client`
  fixture, so the shared limiter's `enabled` flag never applies to it.

Followed the brief's endpoint code verbatim except: added
`-> dict[str, Any]` return type annotations on all three route handlers (repo's
`mypy` config has `disallow_untyped_defs = true`; `app/api/v1/endpoints/jobs.py`
sets the existing precedent for this return type on envelope-returning routes),
and ran `black`/`isort` after writing the file (both reformatted whitespace only,
no logic changes).

## What's involved

- `app/schemas/auth.py` (new) — 8 request models.
- `app/api/v1/endpoints/auth/registration.py` (new) — signup/verify/resend
  routes + `_send_verification` helper.
- `app/api/v1/endpoints/auth/__init__.py` (new) — router aggregator.
- `app/api/v1/api.py` (modified) — mounts `auth_router` at `/auth`.
- `tests/conftest.py` (modified) — `client` fixture disables the rate limiter for
  the test lifetime.
- `tests/api/auth/__init__.py`, `tests/api/auth/test_registration.py` (new) — 6
  tests: signup happy path, weak password, duplicate email, verify happy path,
  bad token, generic resend response.
- No new DB models, no migration — reuses `User`/`UserProfile`/`AuthToken` from
  earlier tasks (Tasks 2–6).

## Verification

RED (before implementation, router not mounted):
```
$ poetry run pytest tests/api/auth/test_registration.py -v
FAILED test_signup_creates_pending_user - assert 404 == 201
FAILED test_signup_weak_password - assert 404 == 422
FAILED test_signup_duplicate_email - assert 404 == 409
FAILED test_verify_activates_user - assert 404 == 200
FAILED test_verify_bad_token - assert 404 == 400
FAILED test_resend_is_generic_for_unknown_email - assert 404 == 200
6 failed
```

GREEN (after implementation):
```
$ poetry run pytest tests/api/auth/test_registration.py -v
6 passed
```

Full suite:
```
$ poetry run pytest -q
75 passed
```
(69 pre-existing + 6 new; no regressions, no unexpected new passes.)

Lint:
```
$ make lint
poetry run black --check app tests     -> All done, 86 files unchanged
poetry run isort --check-only app tests -> clean
poetry run ruff check app tests         -> All checks passed!
poetry run mypy app                     -> Success: no issues found in 49 source files
```

## Operate / roll back

- Pure additive change: new router mount, new schema module, no migration, no
  config/env changes. Roll back by reverting this task's commit.
- The email-verification link the console/SMTP sender emits is a raw token
  string (`<code>{raw}</code>`), not a clickable frontend URL — the frontend is
  expected to construct its own verify page and call `POST /auth/verify` with
  that token. No frontend template change shipped in this task.

## Follow-ups

- Login, MFA challenge, refresh, and password-reset endpoints (schemas already
  scaffolded in `app/schemas/auth.py`) are later tasks in the same plan.
- No rate limiting is applied per-endpoint yet (only the app-wide default
  120/min). Per PRD's "money/auth endpoints get their own limits" convention,
  signup/verify/resend may want tighter per-IP or per-email limits in a later
  hardening pass — flagging, not blocking this task.
- Email content is a bare token, no branded template — acceptable for the
  current dev/console email backend; revisit before SMTP goes live in
  production.

## Update — final whole-branch review fix wave: 60s per-email resend throttle

**What was wrong**: `POST /verify/resend` had no cooldown — a caller (or attacker) could spam
the endpoint for the same email with no rate limit beyond the app-wide default, email-bombing
the target once real SMTP is wired (spec §6.1 calls for resend "rate-limited 60s"). Flagged
above as a follow-up at the time; closed now in the final fix wave that also touched
`password.py::forgot`, `mfa.py::totp_setup`, and `tokens.py::consume_auth_token`.

**Fix**: `resend` now gates the issue+send behind
`get_redis().set(f"verify_resend_cooldown:{email}", "1", ex=60, nx=True)` — same
`app.core.redis.get_redis()` primitive `mfa.py::issue_mfa_ticket` already uses for its `setex`
call. Using `SET NX` (atomic claim) rather than a literal check-then-`SETEX` avoids a TOCTOU race
between two concurrent resend calls for the same email both seeing "no cooldown key yet" and both
sending — same atomic-claim reasoning already applied to `rotate_refresh`/`consume_auth_token`
elsewhere in this codebase. On a throttled call, the block is skipped entirely (no DB query, no
token issue, no email) but the handler still returns the exact same `{sent: true}` — response is
byte-identical across unknown-email, throttled-known-email, and fresh-known-email, preserving the
no-enumeration guarantee.

**Test added**: `test_resend_is_throttled_60s_per_email` — two resend calls for the same email;
asserts the second call returns byte-identical JSON to the first and `ConsoleEmailSender.sent`
stays at length 1 (no second send). Confirmed RED first (`assert 2 == 1` on `sender.sent`) against
the pre-fix handler.

**Regression note**: the pre-existing `test_resend_invalidates_prior_unconsumed_verification_token`
reuses a hardcoded email (`old@x.com`) and asserts the resend actually fires — running the full
suite twice within 60s (e.g. iterating locally) now trips its own leftover cooldown key from the
prior run. Fixed by clearing that key at the top of the test
(`get_redis().delete("verify_resend_cooldown:old@x.com")`), matching the isolation pattern used by
the new throttle test. No test asserted the same email twice inside one run, so this is purely a
cross-run artifact, not a bug in the throttle logic itself.

**Verification**: `poetry run pytest tests/api/auth/test_registration.py -v` → 9 passed (was 8);
full suite 109 passed (104 pre-wave + 5 across all four fixes in this wave); `make lint` clean. Ran
the full suite twice back-to-back (within the 60s cooldown window) to confirm no Redis-state
flakiness — both green.

**Files touched**: `app/api/v1/endpoints/auth/registration.py` (`get_redis` import, cooldown gate
in `resend`), `tests/api/auth/test_registration.py` (+1 test, +`get_redis` import, +cooldown clear
in the pre-existing invalidation test), this SOP.
