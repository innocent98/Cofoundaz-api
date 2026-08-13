# SOP: MFA TOTP setup/verify + login challenge endpoints

## What shipped

- Commit: `4468a4c` — `feat(auth): TOTP MFA setup/verify + challenge endpoints`
- Fix commit: `0463aaa` — `fix(auth): audit trail on MFA enable and challenge
  success/failure` (coordinator review, fix round 1)
- Branch: `feat/auth-endpoints`
- Task 9 of the Auth Endpoints plan
  (`.superpowers/sdd/2026-08-13-auth-endpoints/task-9-brief.md`).

Three new endpoints under `POST /api/v1/auth/mfa/*`: `totp/setup` and
`totp/verify` (both auth-required, let a logged-in user turn on TOTP MFA), and
`challenge` (no auth — the second-factor step `POST /auth/login` redirects
into when `mfa_type != none`, per `app/api/v1/endpoints/auth/login.py`).

## Why

Task 8 (`app/services/auth/mfa.py`) shipped the TOTP/backup-code/ticket
primitives with no HTTP surface. `login.py` already issues an `mfa_ticket` and
returns `mfa_required: true` when a user has MFA enabled, but nothing could
consume that ticket, and no endpoint existed to let a user enroll in TOTP in
the first place. This task closes both gaps.

## How

- **`app/api/v1/endpoints/auth/mfa.py`** (new), `APIRouter(prefix="/mfa")`:
  - `POST /totp/setup` (auth required): generates a secret with
    `mfa.generate_totp_secret()`, stores `mfa.encrypt_secret(secret)` on
    `user.mfa_secret` and commits — **`mfa_type` is deliberately left
    untouched** here (stays `none`) so a setup call that's never followed by
    `verify` doesn't silently turn on MFA. Returns `{secret, otpauth_uri}` for
    the client to render as a QR code.
  - `POST /totp/verify {code}` (auth required): rejects with `MfaInvalidCode`
    (401 `MFA_INVALID_CODE`) if there's no pending `user.mfa_secret` or the
    submitted code doesn't verify against `mfa.decrypt_secret(...)`. On
    success, flips `mfa_type=totp`, stamps `mfa_enabled_at=now(UTC)`,
    generates 10 backup codes via `mfa.generate_backup_codes` (which deletes
    any prior codes first — re-verifying rotates the whole set), commits, and
    returns the plaintext codes once (`{enabled: true, backup_codes: [...]}`)
    — they're stored hashed and are unrecoverable after this response.
  - `POST /challenge {mfa_ticket, code}` (**no auth** — this is what gets a
    user *to* a token in the first place): `mfa.resolve_mfa_ticket(ticket)` is
    single-resolve (deletes the Redis key on read), so a ticket only works
    once. Missing/expired ticket, missing user, or a user with no
    `mfa_secret` all collapse to the same `MfaInvalidCode` — no signal is
    leaked about which failed. Code is accepted via TOTP **or** a one-time
    backup code (`mfa.consume_backup_code`, short-circuits so TOTP is checked
    first and backup codes are only spent on a TOTP miss). On success:
    `last_login_at` is stamped, `issue_token_pair` + `set_refresh_cookie` run
    exactly as they do in `login.py`'s no-MFA branch, and the response shape
    matches login's `{access_token, refresh_token}`.
- **`app/api/v1/endpoints/auth/__init__.py`** (modified): added
  `router.include_router(mfa.router)` alongside the existing
  `registration.router` / `login.router` mounts.

Followed the task-9 brief's code verbatim, with the same `-> dict[str, Any]`
return-type annotations the repo's `mypy` (`disallow_untyped_defs = true`)
requires on every route (precedent: `registration.py`, `login.py`). Ran
`black` after writing the file — it reformatted two multi-line boolean/call
expressions for line length only, no logic change.

**Fix round 1** (coordinator review, commit `0463aaa`): the initial cut had
no `write_audit` calls, unlike `login.py`'s `auth.login.success`/
`auth.login.failed` posture. Added, mirroring that pattern:
- `totp_verify`: `write_audit(db, "auth.mfa.enabled", actor_user_id=user.id)`
  before the commit, on the enable path only (no IP — no `Request` param was
  in scope for that handler and one wasn't added just for this).
- `challenge`: `write_audit(db, "auth.mfa.challenge.failed",
  actor_user_id=user.id, ip=ip)` on a bad TOTP/backup code, committed
  **before** `raise MfaInvalidCode()` (same commit-before-raise ordering
  `login.py` uses for `auth.login.failed`, otherwise the row rolls back with
  the request); `write_audit(db, "auth.mfa.challenge.success", ...)` on
  success, before the existing commit. The two earlier failure branches
  (bad/expired ticket; ticket resolves but no matching/MFA-configured user)
  stay unaudited — there's no real user to attribute those rows to.

## What's involved

- `app/api/v1/endpoints/auth/mfa.py` (new) — the three routes.
- `app/api/v1/endpoints/auth/__init__.py` (modified) — mounts `mfa.router`.
- `tests/api/auth/test_mfa_endpoints.py` (new; extended in fix round 1) — 3
  tests: setup→verify enables MFA and returns 10 backup codes, challenge with
  a valid TOTP code issues tokens (now also asserts an
  `auth.mfa.challenge.success` `AuditLog` row exists), challenge with a wrong
  code returns 401 `MFA_INVALID_CODE` (now also asserts an
  `auth.mfa.challenge.failed` `AuditLog` row exists).
- No new DB models, no migration — reuses `User.mfa_secret`/`mfa_type`/
  `mfa_enabled_at` and `MfaBackupCode` from the Task 8 auth-tables migration.
- Consumed, unmodified: `app/services/auth/mfa.py` (Task 8),
  `app/services/auth/sessions.py::issue_token_pair`/`set_refresh_cookie`
  (Task 6), `app/api/deps.py::get_current_user`, `app/core/errors.py::MfaInvalidCode`,
  `app/schemas/auth.py::MfaChallengeRequest`/`TotpVerifyRequest` (both
  pre-existing, scaffolded in Task 7).

## Verification

RED (before implementation, routes not mounted):
```
$ poetry run pytest tests/api/auth/test_mfa_endpoints.py -v
FAILED test_totp_setup_then_verify_enables - assert 404 == 200
FAILED test_challenge_with_totp_issues_tokens - assert 404 == 200
FAILED test_challenge_bad_code_401 - assert 404 == 401
3 failed
```

GREEN (after implementation):
```
$ poetry run pytest tests/api/auth/test_mfa_endpoints.py -v
3 passed
```

Full suite:
```
$ poetry run pytest -q
86 passed
```
(83 pre-existing + 3 new; no regressions, no unexpected new passes.)

Lint:
```
$ make lint
poetry run black --check app tests      -> All done, 90 files unchanged
poetry run isort --check-only app tests -> clean
poetry run ruff check app tests         -> All checks passed!
poetry run mypy app                     -> Success: no issues found in 51 source files
```

Fix round 1 re-verification (after adding audit trail, commit `0463aaa`):
```
$ poetry run pytest tests/api/auth/test_mfa_endpoints.py -v
3 passed

$ poetry run pytest -q
86 passed   (same count — fix round added assertions to existing tests, no
             new test functions; no regressions)

$ make lint
-> all four checks clean, same as above
```

## Operate / roll back

- Pure additive change: new router mount, no migration, no config/env
  changes. Roll back by reverting commit `4468a4c`.
- `MFA_ENCRYPTION_KEY` (Fernet key, `settings.MFA_ENCRYPTION_KEY`) must be set
  in every environment that calls `totp/setup`, `totp/verify`, or
  `challenge` — `mfa.encrypt_secret`/`decrypt_secret` raise
  `MfaNotConfigured` (500) otherwise. This is an existing Task 8 requirement,
  not new to this task; tests satisfy it via the brief's autouse
  `_mfa_key` fixture (`monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", ...)`).
- Redis must be reachable for `challenge` — `mfa.resolve_mfa_ticket` reads/
  deletes the ticket key there; a Redis outage makes `challenge` fail closed
  (ticket resolves to `None` → 401), not fail open.

## Follow-ups

- No rate limiting on `POST /auth/mfa/challenge` beyond the app-wide default.
  This is a pre-token, unauthenticated endpoint that accepts a 6-digit TOTP
  guess (backup codes are 10 digits, lower brute-force risk) — worth a
  tighter per-ticket or per-IP limit in the hardening pass mentioned in the
  Task 7 SOP's follow-ups.
- No endpoint to disable MFA or regenerate backup codes without going through
  `totp/setup`→`totp/verify` again (which rotates the secret). Not in this
  task's scope per the brief.
