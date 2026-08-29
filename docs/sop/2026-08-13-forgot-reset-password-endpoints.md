# SOP: Forgot / reset password endpoints

## What shipped

- Commit: `2016aa6` — `feat(auth): forgot/reset password endpoints`
- Branch: `feat/auth-endpoints`
- Task 11 of the Auth Endpoints plan
  (`.superpowers/sdd/2026-08-13-auth-endpoints/task-11-brief.md`).

`POST /api/v1/auth/password/forgot` and `POST /api/v1/auth/password/reset` — self-service
password recovery over the `password_reset` token purpose (Task 7's `AuthToken`
infrastructure) and the session-revocation helper (Task 4's `revoke_all_for_user`).

## Why

Users who forget their password had no recovery path — `EmailTaken`/`InvalidCredentials`
existed for signup/login, but nothing let a locked-out user regain access without support
intervention. This closes that gap while defending against account enumeration, a common
attack against forgot-password flows (an attacker probing arbitrary emails to learn which
ones have accounts).

## How

**`app/api/v1/endpoints/auth/password.py`** (new):

- `POST /password/forgot` — looks up the user by email. If found **and not
  `UserStatus.disabled`** (see fix round 1 below): first calls
  `invalidate_unconsumed_tokens(db, user, AuthTokenPurpose.password_reset)` (marks any
  still-live prior reset tokens as consumed, mirroring `registration.py::_send_verification`'s
  use of the same helper for email-verification tokens) so only the newest reset link is
  ever valid, then issues a fresh 1-hour `password_reset` token (`issue_auth_token`),
  emails it (`get_email_sender().send(...)`,
  console backend in dev/test — see `app/platform/email.py`), writes an
  `auth.password.reset_requested` audit row, commits. **Always** returns the same 200
  `{sent: true, message: "If that email has an account, a reset link is on its way."}` —
  identical status and body across all three cases (unknown email, disabled account, active
  account), so a client (or attacker) cannot distinguish any of them. Matches the existing
  no-enumeration precedent in `registration.py::resend`. `pending_verification` users are
  *not* excluded — only `disabled`, matching `login.py`'s own status check.
- `POST /password/reset` — validates password strength first
  (`validate_password_strength`, raises `WeakPassword` → 422; **since 2026-08-29 also
  `PasswordTooLong` → 422 `PASSWORD_TOO_LONG` above 72 UTF-8 bytes** — see
  `docs/sop/2026-08-29-passlib-to-bcrypt5-migration.md`) *before* touching the token,
  so a rejected-password attempt doesn't burn a valid reset token. Then `consume_auth_token`
  (raises `TokenInvalid` → 400 on bad/expired/already-used tokens), sets
  `user.password_hash = get_password_hash(...)`, calls `revoke_all_for_user(db, user.id)`
  to force re-login on every device/session (a password reset is the standard trigger for
  "log out everywhere" — otherwise a session hijacked before the reset stays valid after
  it), writes an `auth.password.reset` audit row, commits, returns `{reset: true}`.

Both routes accept `request: Request` and pass `ip=request.client.host if
request.client else None` into `write_audit` — added beyond the brief's literal snippet to
match the established convention from `login.py`/`mfa.py`/`registration.py`, all of which
audit-log security-sensitive events with the requester's IP. (A prior task, MFA endpoints,
originally shipped *without* this and needed a follow-up fix commit `0463aaa` — added it
here up front instead of repeating that gap.)

**`app/api/v1/endpoints/auth/__init__.py`** (modified): added `password` to the import and
`router.include_router(password.router)`, extending the existing aggregation pattern.

## What's involved

- `app/api/v1/endpoints/auth/password.py` (new) — `forgot`/`reset` routes.
- `app/api/v1/endpoints/auth/__init__.py` (modified) — mounts `password.router`.
- `tests/api/auth/test_password_endpoints.py` (new, 4 tests: 3 verbatim from the brief +
  1 added in fix round 1) — generic-response-for-unknown-email, reset updates password +
  revokes all sessions, weak password rejected with 422, disabled account gets the generic
  response but no token issued.
- Consumes (no changes): `app/services/auth/tokens.py::issue_auth_token/consume_auth_token`,
  `app/services/auth/password.py::validate_password_strength`,
  `app/services/auth/sessions.py::revoke_all_for_user`, `app/core/security.py::
  get_password_hash`, `app/platform/email.py::EmailMessage/get_email_sender`,
  `app/platform/audit.py::write_audit`, `app/schemas/auth.py::ForgotPasswordRequest/
  ResetPasswordRequest`, `app/core/envelope.py::success_response`.
- No new DB models, no migration, no config changes.

## Verification

RED (routes not mounted):
```
$ poetry run pytest tests/api/auth/test_password_endpoints.py -v
FAILED test_forgot_is_generic_for_unknown - assert 404 == 200
FAILED test_reset_updates_password_and_revokes_sessions - assert 404 == 200
FAILED test_reset_weak_password_rejected - assert 404 == 422
3 failed
```

GREEN:
```
$ poetry run pytest tests/api/auth/test_password_endpoints.py -v
3 passed
```

Full suite:
```
$ poetry run pytest -q
95 passed
```
(92 pre-existing + 3 new, no regressions, no unexpected new passes.)

Lint:
```
$ make lint
black --check app tests      -> All done! 94 files would be left unchanged.
isort --check-only app tests -> clean
ruff check app tests         -> All checks passed!
mypy app                     -> Success: no issues found in 53 source files
```

No-enumeration smoke check (manual, against an email with no account):
```
POST /api/v1/auth/password/forgot {"email": "no-such-user@example.com"}
-> 200 {"data": {"sent": true, "message": "If that email has an account, a reset link is on its way."}, "meta": null}
```
Identical shape and status to the known-email path exercised by the test suite (the
`sent`/`message` fields never vary; the only difference is the DB write + email send, both
server-side and unobservable to the caller).

## Operate / roll back

- Pure additive route wiring over already-shipped services (token issuance/consumption,
  password hashing, session revocation) — no migration, no config change. Roll back by
  reverting the shipping commit.
- `_RESET_TTL = timedelta(hours=1)` — reset links expire after 1 hour; adjust in
  `app/api/v1/endpoints/auth/password.py` if the product requirement changes.
- Reset emails go through `get_email_sender()` — console backend by default
  (`EMAIL_BACKEND` unset/non-`smtp`), so in dev/test the token is only visible in logs, not
  actually emailed. No action needed to "operate" this beyond the existing SMTP config for
  production email delivery.

## Follow-ups

- No per-endpoint rate limit on `/password/forgot` yet — same acknowledged gap noted in the
  refresh/logout SOP; only the app-wide default limiter applies (disabled entirely in
  tests). Worth a dedicated per-IP limit given this endpoint is a classic
  enumeration/abuse target (mass reset-email spam against arbitrary addresses), even though
  the response itself doesn't leak account existence.

## Update — fix round 1: `forgot` issued reset tokens to disabled accounts

**What was wrong**: `forgot`'s gate was `if user is not None:`, so a `disabled` account
(deactivated by an admin, or self-locked) got a live `password_reset` token and email just
like an active account. `login.py` already blocks `UserStatus.disabled` at the credential
check, and the sibling `registration.py::resend` endpoint already establishes the
status-filtering precedent for this exact no-enumeration shape (only sends a fresh
verification email to `UserStatus.pending_verification` users) — `forgot` should have
matched that pattern from the start but didn't, since the brief's literal code snippet
didn't filter on status at all. Caught in code review, not by the original 3 tests (none
of which used a disabled user).

**Fix**: `app/api/v1/endpoints/auth/password.py::forgot` — gate changed to `if user is not
None and user.status != UserStatus.disabled:`. The response line stays *outside* that
block and unconditional, so the 200 body/status is byte-identical across unknown-email,
disabled-account, and active-account requests — only the internal side effects (token
issue, email send, audit write, commit) are gated. `pending_verification` is deliberately
left able to reset (not excluded) — the fix only adds the one check `login.py` itself
enforces.

**Test added** (`tests/api/auth/test_password_endpoints.py`):
`test_forgot_disabled_account_is_generic_and_issues_no_token` — creates a `UserStatus.disabled`
user, calls `/password/forgot` with their email, asserts (a) the response body is exactly
`{"sent": true, "message": "If that email has an account, a reset link is on its way."}`
(same as the unknown-email case) and (b) querying `AuthToken` filtered by that user's `id`
+ `purpose=password_reset` returns `[]` — no token was persisted.

**Confirmed RED first**: `git stash`ed just the fix (`app/api/v1/endpoints/auth/password.py`),
ran the new test alone against the pre-fix code — failed exactly as expected
(`assert [<AuthToken ...>] == []`, i.e. a token *was* issued for the disabled user).
Restored the fix (`git stash pop`), reran — passed.

**Verification**:
```
$ poetry run pytest tests/api/auth/test_password_endpoints.py -v
4 passed
```
```
$ poetry run pytest -q
96 passed
```
(95 pre-fix + 1 new, no regressions, no unexpected new passes.)
```
$ make lint
black --check app tests      -> All done! 94 files would be left unchanged.
isort --check-only app tests -> clean
ruff check app tests         -> All checks passed!
mypy app                     -> Success: no issues found in 53 source files
```

**Files touched (fix round 1)**: `app/api/v1/endpoints/auth/password.py` (`UserStatus`
import + status gate on `forgot`, comment updated), `tests/api/auth/test_password_endpoints.py`
(+1 test, +`AuthToken` import), this SOP.

## Update — fix round 2 (final whole-branch review fix wave): 60s per-email forgot throttle

**What was wrong**: `POST /password/forgot` had no cooldown — same class of gap as
`registration.py::resend` (spec §6.1 calls for resend "rate-limited 60s"; `forgot` shares the
identical no-enumeration shape and same email-bombing exposure once real SMTP is wired). Flagged
in the Task 11 SOP's original follow-ups; closed now alongside the sibling fix to `resend`,
`mfa.py::totp_setup`, and `tokens.py::consume_auth_token`.

**Fix**: `forgot` now gates the issue+send behind
`get_redis().set(f"password_reset_cooldown:{email}", "1", ex=60, nx=True)` — same
`app.core.redis.get_redis()` primitive `mfa.py::issue_mfa_ticket` uses for its `setex` call, and
the same atomic `SET NX` approach used for the sibling `resend` fix (avoids the TOCTOU race a
literal check-then-`SETEX` would have between two concurrent forgot calls for the same email). On
a throttled call, the block — including the existing `disabled`-account status gate from fix
round 1 — is skipped entirely, but the handler still returns the identical generic
`{sent: true, message: ...}` body. Response stays byte-identical across unknown-email,
disabled-account, throttled-known-email, and fresh-known-email — the no-enumeration guarantee
from fix round 1 is preserved, just with one more case folded into it.

**Test added**: `test_forgot_is_throttled_60s_per_email` — two forgot calls for the same email;
asserts the second call returns byte-identical JSON to the first and that only one `AuthToken`
row (`purpose=password_reset`) exists for that user afterward. Confirmed RED first
(`assert 2 == 1` on the token count) against the pre-fix handler.

**Verification**:
```
$ poetry run pytest tests/api/auth/test_password_endpoints.py -v
6 passed   (was 4)

$ poetry run pytest -q
109 passed   (104 pre-wave + 5 across all four fixes in this wave)

$ make lint
-> all four checks clean
```
Ran the full suite twice back-to-back (within the 60s cooldown window) to confirm no Redis-state
flakiness across repeated runs — both green.

**Files touched (fix round 2)**: `app/api/v1/endpoints/auth/password.py` (`get_redis` import,
cooldown gate in `forgot`), `tests/api/auth/test_password_endpoints.py` (+1 test, +`get_redis`
import), this SOP.
