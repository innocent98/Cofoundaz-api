# SOP: Login endpoint (brute-force lockout + MFA gate) + disabled-user enforcement

## What shipped

- Commit: (this task's commit — see subject
  `feat(auth): login with lockout + MFA gate + disabled-user enforcement`)
- Branch: `feat/auth-endpoints`
- Task 8 of the Auth Endpoints plan
  (`.superpowers/sdd/2026-08-13-auth-endpoints/task-8-brief.md`).

`POST /api/v1/auth/login` — the first real password-login endpoint. Checks lockout
state, verifies credentials, increments/resets a per-user failed-attempt counter
with a 5-strikes/15-minute lockout, blocks disabled accounts, and gates on MFA
(returns a short-lived ticket instead of tokens when `mfa_type != none`). Also
closes a previously-deferred gap in `get_current_user`: accounts with
`status == disabled` can no longer authenticate with an otherwise-valid JWT.

## Why

Module 01 needs a real login path before MFA challenge/refresh/logout (later
tasks in the same plan) can be layered on. Security-critical by nature — this is
the endpoint attackers hit first — so lockout, generic-401 (no email enumeration),
and MFA-gating all had to be correct from the first commit, not bolted on later.

The `get_current_user` disabled-user gap was called out explicitly in the
Task 14 SOP (`docs/sop/2026-08-12-real-get-current-user-dependency.md`) as
"Plan 2 follow-up — not implemented"; this task is that follow-up. Without it, a
user disabled *after* issuing a still-valid access token could keep using the API
until the token naturally expired (up to `ACCESS_TOKEN_EXPIRE_MINUTES`), even
though `login` itself would now correctly reject them.

## How

**`app/api/v1/endpoints/auth/login.py`** (new) — `POST /login`:
1. Load `user` by email (no `deleted_at` filter needed — same as `get_current_user`
   pattern isn't reused here since login re-derives everything from a fresh row,
   not a token).
2. `locked_until > now` → `AccountLocked()` (429) before touching credentials —
   locked-out users get the same response regardless of whether they now typed
   the right password, so a locked-then-correct attempt can't be used to confirm
   the lockout is about to expire.
3. Bad credentials (`user is None`, `password_hash is None`, or `verify_password`
   fails) — single branch, so an unknown email and a wrong password are
   indistinguishable to the caller (`INVALID_CREDENTIALS` / 401 either way, no
   enumeration). If the user *does* exist: increment `failed_login_count`; at
   `LOGIN_MAX_FAILS` (5) set `locked_until = now + LOGIN_LOCKOUT_MINUTES` (15) and
   reset the counter to 0 (so the next lockout cycle starts clean rather than
   compounding). Audit `auth.login.failed` unconditionally (even for unknown
   emails, with `actor_user_id=None`) and commit once.
4. `status == disabled` → `InvalidCredentials()` — deliberately the *same* error
   code as bad credentials, not a distinct `ACCOUNT_DISABLED` code, so a disabled
   account isn't distinguishable from a wrong password either. No audit row on
   this branch (matches the brief; the failed-attempt counter also isn't touched
   here since it's not a credential failure).
5. Success: reset `failed_login_count=0` / `locked_until=None`.
6. `mfa_type != none` → `issue_mfa_ticket(user.id)` (5-minute Redis-backed
   ticket), commit, return `{mfa_required: true, mfa_ticket, access_token: null}`
   — **no** `issue_token_pair` call, **no** refresh cookie. The MFA challenge
   endpoint (later task) is what actually mints tokens once the ticket + TOTP/
   backup code are verified.
7. No MFA: set `last_login_at`, `issue_token_pair` (mints access JWT + opaque
   refresh, persists an `AuthSession` row), `write_audit("auth.login.success")`,
   commit, `set_refresh_cookie`, return `{access_token, refresh_token,
   mfa_required: false}`.

**Deviation from the brief's literal snippet**: the brief's failed-credentials
branch commits twice (once right after the counter/lockout mutation, once again
after `write_audit`). Consolidated to a single `db.commit()` after `write_audit`
— both the counter update and the audit row are still part of one transaction
either way (SQLAlchemy just needed one flush point), so this is a no-op
behaviorally, just fewer round trips. Also added the `-> dict[str, Any]` return
type annotation (repo's `mypy` `disallow_untyped_defs` convention, same as
`registration.py`'s three routes).

**`app/api/deps.py`** (modified) — `get_current_user`: added
`if user.status == UserStatus.disabled: raise Unauthorized()` immediately after
the existing `deleted_at` soft-delete check and before `return user`. Removed the
inline comment noting this as an unimplemented Plan 2 follow-up (see Operate /
roll back below — the Task 14 SOP is updated to point here instead of leaving
stale text in both places).

**`app/api/v1/endpoints/auth/__init__.py`** (modified): mounted
`login.router` alongside the existing `registration.router` — the aggregator
pattern the Task 7 SOP flagged as the place later auth tasks would extend.

## What's involved

- `app/api/v1/endpoints/auth/login.py` (new) — the `login` route.
- `app/api/v1/endpoints/auth/__init__.py` (modified) — router mount.
- `app/api/deps.py` (modified) — `UserStatus` import + disabled-user check in
  `get_current_user`.
- `tests/api/auth/test_login.py` (new) — 6 tests: success (tokens + cookie),
  wrong password (401 + counter increments), lockout after 5 fails (429
  `ACCOUNT_LOCKED`), MFA-required (ticket, no tokens), unknown email (401, same
  code as wrong password — no enumeration), disabled user (401). The brief only
  specified the first 4; added the unknown-email and disabled-user cases since
  both are explicitly called out as security-critical behavior in the brief's
  "Behavior" section but weren't in its Step 1 test list.
- No new DB models, no migration, no schema changes — reuses `User` fields from
  earlier tasks (`failed_login_count`, `locked_until`, `last_login_at`, `status`,
  `mfa_type`) and `LoginRequest` from Task 7.
- Depends on: `verify_password` (`app/core/security.py`), `issue_token_pair` /
  `set_refresh_cookie` (`app/services/auth/sessions.py`, Task 6),
  `issue_mfa_ticket` (`app/services/auth/mfa.py`, Task ~), `write_audit`
  (`app/platform/audit.py`), `InvalidCredentials` / `AccountLocked`
  (`app/core/errors.py`), `settings.LOGIN_MAX_FAILS` /
  `settings.LOGIN_LOCKOUT_MINUTES` (`app/core/config.py`).

## Verification

RED (before implementation, route not mounted):
```
$ poetry run pytest tests/api/auth/test_login.py -v
FAILED test_login_success_returns_tokens - assert 404 == 200
FAILED test_login_wrong_password_401_and_increments - assert 404 == 401
FAILED test_login_lockout_after_5 - assert 404 == 429
FAILED test_login_mfa_required_returns_ticket_not_tokens - assert 404 == 200
FAILED test_login_unknown_email_401_no_enumeration - assert 404 == 401
FAILED test_login_disabled_user_401 - TypeError (test factory bug, fixed pre-GREEN)
6 failed
```

GREEN (after implementation):
```
$ poetry run pytest tests/api/auth/test_login.py -v
6 passed
```

Full suite:
```
$ poetry run pytest -q
83 passed
```
(77 pre-existing + 6 new; no regressions, no unexpected new passes.)

Lint:
```
$ make lint
poetry run black --check app tests      -> 1 file reformatted (login.py whitespace only,
                                             applied; re-run clean, 88 files unchanged)
poetry run isort --check-only app tests -> clean
poetry run ruff check app tests         -> All checks passed!
poetry run mypy app                     -> Success: no issues found in 50 source files
```

Integration coverage doubles as the smoke check: every test goes through the real
`TestClient` → FastAPI → real Postgres session (rollback per test), not a mocked
DB or a bare unit test — this is the equivalent of a `curl` smoke test for each
of the 4 behavior branches (success, bad credentials, lockout, MFA gate) plus the
2 added edge cases.

## Operate / roll back

- Pure additive change on the endpoint side; `get_current_user`'s new disabled
  check is a behavior change but a tightening one (previously-authenticated
  disabled users now get 401 where they'd have gotten 200) — no migration, no
  config/env changes.
- Updated `docs/sop/2026-08-12-real-get-current-user-dependency.md` in this same
  pass with a pointer to this SOP, since its Follow-ups section explicitly called
  the disabled-user check unimplemented — that text is now stale and would
  otherwise mislead a future reader into re-deferring work already done.
- To roll back: revert this task's commit. `get_current_user` reverts to
  accepting disabled users with a still-valid token; `POST /auth/login` 404s
  again.

## Follow-ups

- MFA challenge endpoint (verifies `mfa_ticket` + TOTP/backup code, then actually
  calls `issue_token_pair`) is a later task in the same plan — this task only
  issues the ticket.
- Login has no endpoint-specific rate limit yet (only the app-wide default via
  SlowAPI, disabled in tests). Lockout provides some brute-force protection per
  account, but a distributed attacker enumerating many emails at low
  per-account volume isn't slowed by lockout alone — worth a per-IP limit on
  `/auth/login` in a later hardening pass, consistent with the Task 7 SOP's
  same open item for signup/verify/resend.
- Timing side-channel: the bad-credentials branch only calls `verify_password`
  (bcrypt, deliberately slow) when a user row was actually found; an unknown
  email short-circuits without hashing. This is the brief's specified behavior
  verbatim and matches the existing codebase's pattern elsewhere, but it is a
  theoretical timing oracle for email enumeration despite the identical HTTP
  response body/code. Flagging, not blocking — would need a dummy-hash
  comparison on the not-found path to fully close.
