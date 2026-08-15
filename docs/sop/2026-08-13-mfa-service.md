# SOP: MFA service (TOTP encrypt/verify + backup codes + ticket)

## What shipped

- Commit: (this shipment) — `feat(auth): MFA service (TOTP encrypt/verify + backup codes + ticket)`
- Branch: `feat/auth-endpoints`
- Task 6 of the Auth Endpoints plan
  (`.superpowers/sdd/2026-08-13-auth-endpoints/task-6-brief.md`).

New `app/services/auth/mfa.py`: the service layer behind TOTP-based MFA. Generates and
encrypts TOTP secrets at rest, verifies 6-digit codes with clock-skew tolerance, issues/consumes
single-use backup codes, and issues short-lived Redis "MFA tickets" — the token a client holds
between "password verified, MFA pending" and "MFA code verified" during login (consumed by
Task 9's login/MFA-challenge endpoints).

## Why

A TOTP secret is a long-lived shared secret — if the DB leaks, every enrolled user's 2FA is
permanently broken (unlike a password, it can't be salted+hashed because the server needs the
plaintext to compute the running code). It must be encrypted at rest, not just hashed. Backup
codes are the account-recovery path when a user loses their authenticator device, so they need
the same single-use discipline as refresh tokens. The MFA ticket exists so the login flow can
prove "this request already produced a valid password" without re-sending the password on the
second (MFA-code) request, without extending the *access* token lifecycle to a not-yet-fully-
authenticated session.

## How

**`app/services/auth/mfa.py`** (new):

- `_fernet() -> Fernet` — private; raises `MfaNotConfigured` (new `AppError` subclass,
  `MFA_NOT_CONFIGURED` / 500) if `settings.MFA_ENCRYPTION_KEY` is unset, rather than letting
  `Fernet(None)` throw an unhelpful `TypeError` at the call site.
- `generate_totp_secret() -> str` — `pyotp.random_base32()`.
- `encrypt_secret(secret) -> str` / `decrypt_secret(token) -> str` — Fernet symmetric encryption
  keyed by `settings.MFA_ENCRYPTION_KEY`; the encrypted token (not the raw secret) is what's
  persisted on the user record (persistence itself is a later task — this ships the primitive).
- `provisioning_uri(secret, email) -> str` — `otpauth://` URI via `pyotp.TOTP(...)
  .provisioning_uri(name=email, issuer_name="Cofoundaz")`, for QR-code enrollment.
- `verify_totp(secret, code) -> bool` — `pyotp.TOTP(secret).verify(code, valid_window=1)`, i.e.
  accepts the current 30s window plus one window on either side (±30s clock skew tolerance).
- `generate_backup_codes(db, user) -> list[str]` — deletes any prior `MfaBackupCode` rows for
  the user first (re-enrollment invalidates old codes), generates 10 random 10-digit codes,
  stores only `hash_token(code)` (reusing `sessions.hash_token`, sha256), returns the 10
  plaintext codes once — the only time they're ever available in plaintext.
- `consume_backup_code(db, user, code) -> bool` — looks up by `hash_token(code)` +
  `consumed_at IS NULL`; stamps `consumed_at = now` and returns `True` on first use, `False` for
  an already-consumed code, an unknown code, or any non-code string (no exception — the caller
  decides whether to raise `MfaInvalidCode`).
- `issue_mfa_ticket(user_id) -> str` / `resolve_mfa_ticket(ticket) -> uuid.UUID | None` —
  Redis-backed, key `mfa_ticket:{token}`, `secrets.token_urlsafe(32)` value, 5-minute TTL
  (`setex`). `resolve_mfa_ticket` deletes the key on read (single-resolve — a ticket can't be
  replayed even within its TTL window) and returns `None` for a missing/expired/already-resolved
  ticket.

**Key decision — mypy on `redis.Redis.get()`**: the installed `redis` stubs type `.get()` as
returning `Awaitable[Any] | Any` (the sync client shares overloaded signatures with the async
one). `resolve_mfa_ticket` casts the result with `cast(str | None, get_redis().get(key))` —
same `cast()`-at-the-boundary pattern already used in `sessions.py` for the `samesite` Literal
mismatch. This is the first module to actually call `get_redis()`, so this typing gap wasn't
visible until now.

**Deviation from the brief's literal snippet**: the brief's pseudocode left `issue_mfa_ticket`'s
`user_id` and `resolve_mfa_ticket`'s return type unannotated, and used inline `import uuid` /
`from datetime import UTC, datetime` inside function bodies. Matched this project's established
convention instead (see `sessions.py`, `tokens.py`): all imports at module top, `user_id:
uuid.UUID` typed per the "typed everywhere" rule, `-> uuid.UUID | None` explicit on
`resolve_mfa_ticket`. Same code, brief-compliant behavior, just conforming to house style and
`disallow_untyped_defs`.

## What's involved

- `app/services/auth/mfa.py` (new, 86 lines) — full interface above, plus the `MfaNotConfigured`
  `AppError` subclass.
- `tests/services/auth/test_mfa.py` (new, 3 tests, per the brief's literal Step 2 snippet) —
  encrypt/decrypt round-trip, TOTP verify (valid + invalid code), backup-code generate/consume
  single-use + unknown-code rejection.
- `pyproject.toml` — added `pyotp = "^2.10.0"` (new) and `cryptography = "^50.0.0"` (was already
  present transitively via `python-jose[cryptography]`; now a direct dependency). `poetry.lock`
  updated locally but not committed — it's gitignored in this repo.
- Depends on: `app/db/models/auth.py::MfaBackupCode` (Task 1), `app/services/auth/sessions.py
  ::hash_token` (Task 4), `app/core/config.py::settings.MFA_ENCRYPTION_KEY` (pre-existing),
  `app/core/redis.py::get_redis` (pre-existing), `app/core/errors.py::AppError` (pre-existing),
  `tests/factories.py::create_user`.
- Nothing calls this service yet — MFA enrollment/verify endpoints and the login MFA-challenge
  branch (later tasks in this plan) are the consumers. Pure service-layer, no route wiring, no
  migration.

## Verification

RED (module missing):
```
$ poetry run pytest tests/services/auth/test_mfa.py -v
ImportError: cannot import name 'mfa' from 'app.services.auth'
```

GREEN (target file):
```
$ poetry run pytest tests/services/auth/test_mfa.py -v
3 passed
```
Coverage on `app/services/auth/mfa.py`: 81% (58 statements, 11 missed — `MfaNotConfigured`'s
raise path and the Redis ticket functions aren't exercised by this test file per the brief
("Redis-backed ticket functions are covered in Task 9's endpoint tests"); manually verified the
ticket round-trip below since Redis is running locally.

Manual Redis ticket verification (issue → resolve → single-resolve → bogus ticket):
```
$ poetry run python -c "..."
resolved == issued user_id: True
resolved again (should be None): None
bogus ticket: None
```

Full suite:
```
$ poetry run pytest -q
69 passed
```
(66 baseline + 3 new, no regressions.)

Lint:
```
$ make lint
black --check app tests    -> All done! 81 files would be left unchanged.
isort --check-only app tests -> clean
ruff check app tests        -> All checks passed!
mypy app                    -> Success: no issues found in 46 source files
```

## Operate / roll back

- Pure application-layer addition; no migration, no config change, no deployment side effects.
- **Operational requirement**: `MFA_ENCRYPTION_KEY` must be set (a `Fernet.generate_key()`
  value) in any environment where MFA enrollment will actually run — `encrypt_secret`/
  `decrypt_secret` raise `MfaNotConfigured` (500) otherwise. Not required for this task's tests
  (the fixture monkeypatches it per-test) but will be required before Task 9's enrollment
  endpoint is usable end-to-end. Not a secret to invent ad hoc in prod — generate once, store in
  the environment's secret manager, same handling as `SECRET_KEY`.
- Nothing else imports this module yet, so reverting the commit is a clean, zero-fallout
  removal.

## Follow-ups

- MFA enrollment/verify HTTP endpoints and the login MFA-challenge branch (later tasks) are the
  actual callers of `provisioning_uri`/`verify_totp`/`generate_backup_codes`/
  `consume_backup_code`/the ticket functions — this task ships the logic, not the wiring.
- `MfaInvalidCode` (existing `AppError`) is listed as a consumed interface in the brief but this
  module never raises it directly — `verify_totp`/`consume_backup_code` return `bool`, and it's
  the caller's job (the future endpoint) to turn a `False` into `MfaInvalidCode`. Confirmed this
  matches the brief's intent (`consume_backup_code(db, u, "not-a-code") is False`, not a raise).
- No rate limiting on `verify_totp`/`consume_backup_code` in this task — a 6-digit TOTP code has
  only 10^6 possibilities, so the future MFA-challenge endpoint (Task 9, already has `slowapi`
  wired per the sessions SOP) must rate-limit attempts; this service layer intentionally has no
  opinion on that, same separation as `rotate_refresh`.
