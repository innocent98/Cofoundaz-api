# SOP: Refresh-token session service (rotation + reuse detection)

## What shipped

- Commit: (this shipment) — `feat(auth): refresh-token session service with rotation + reuse detection`
- Branch: `feat/auth-endpoints`
- Task 4 of the Auth Endpoints plan
  (`.superpowers/sdd/2026-08-13-auth-endpoints/task-4-brief.md`).

New `app/services/auth/sessions.py`: the service layer behind login/refresh/logout. Issues
opaque refresh tokens, hashes them before storage, rotates them on use with reuse detection
(theft of a stolen-and-already-used token revokes the whole session family), and provides the
`httponly` cookie helpers the auth endpoints (Task 6+) will call.

## Why

Refresh tokens are long-lived (30 days) and, unlike short-lived access tokens, get stored
client-side across app restarts — they're the highest-value credential in the auth system if
leaked. Two properties are non-negotiable for that threat model:

1. **The raw token must never be persisted.** Only `sha256(raw)` goes in `auth_sessions
   .refresh_token_hash`; a DB dump or read-replica leak can't be turned back into a usable
   token.
2. **Reuse must be detectable and punished.** If a refresh token is used twice (attacker stole
   it and raced the legitimate client, or a client retried after already rotating), that's the
   strongest signal available that the token leaked. The response is to revoke every session in
   that token's `family_id` — not just the reused row — so the attacker's freshly-rotated
   session (if they won the race) dies too, not just the stale one.

## How

**`app/services/auth/sessions.py`** (new):

- `hash_token(raw) -> str` — `sha256(raw.encode()).hexdigest()`. Deterministic, so lookups are
  `WHERE refresh_token_hash = hash_token(candidate)` — no need to scan-and-compare.
- `_new_refresh() -> str` — `secrets.token_urlsafe(48)` (384 bits of entropy), private to this
  module; callers never construct raw tokens themselves.
- `issue_token_pair(db, user, *, ip=None, user_agent=None, family_id=None) -> (access, raw)` —
  inserts one `AuthSession` row (`expires_at = now + REFRESH_TOKEN_EXPIRE_DAYS`), mints a JWT
  access token via `create_access_token`, returns `(access, raw)`. `family_id` defaults to a
  fresh `uuid4()` on first login; rotation passes the *existing* family forward so every token
  descended from one login shares one family.
- `rotate_refresh(db, raw_refresh, *, ip=None, user_agent=None) -> (access, raw, user)`:
  1. Look up the session row by `hash_token(raw_refresh)`. Not found → `Unauthorized`.
  2. **Reuse/expiry check**: `rotated_at is not None OR revoked_at is not None OR expires_at <
     now` → revoke the entire family (`_revoke_family`) and raise `Unauthorized`. This is the
     reuse-detection path — it fires whether the token was already rotated, already explicitly
     revoked, or has simply expired.
  3. Otherwise: stamp `rotated_at = now` on the old row (marks it consumed — a second use now
     falls into the reuse branch above), issue a new session in the *same* `family_id`, return
     the new pair plus the resolved `User`.
- `_revoke_family(db, family_id)` — bulk `UPDATE auth_sessions SET revoked_at = now WHERE
  family_id = ? AND revoked_at IS NULL`, private helper shared by the reuse path.
- `revoke_session(db, raw_refresh)` — single-session logout; no-ops if already revoked/unknown
  (idempotent, so a double-click logout doesn't error).
- `revoke_all_for_user(db, user_id) -> int` — bulk-revokes every active session for a user
  ("log out everywhere"); returns the row count.
- `set_refresh_cookie(response, raw_refresh)` / `clear_refresh_cookie(response)` — wrap
  `Response.set_cookie`/`delete_cookie` with the project's settings
  (`REFRESH_COOKIE_NAME`/`_SECURE`/`_SAMESITE`, `httponly=True`, `path="/api/v1/auth"` so the
  cookie is only ever sent to auth endpoints, not the whole API surface).

**Key decision — mypy `samesite` typing**: `settings.REFRESH_COOKIE_SAMESITE` is a plain `str`
(env-configurable), but Starlette's `set_cookie` types `samesite` as
`Literal["lax", "strict", "none"] | None`. Rather than loosen the settings type or add a runtime
validator (out of scope for this task), cast at the call site:
`cast(Literal["lax", "strict", "none"], settings.REFRESH_COOKIE_SAMESITE)` — matches the
existing `cast()` pattern already used in `app/core/security.py` for the same class of
pydantic-settings/library-typing mismatch.

**Deviation from the brief's literal test file**: the brief's Step 1 snippet only exercises 4
of the 6 module-level functions (`issue_token_pair`, `rotate_refresh`, `revoke_all_for_user`,
`hash_token`) — `revoke_session`, `set_refresh_cookie`, and `clear_refresh_cookie` were part of
the "Produces" interface contract but had zero test coverage. Added 5 more tests before
committing: unknown-token rejection, `revoke_session` marks the row revoked, a revoked session
can't be rotated (belt-and-suspenders on the reuse path), and both cookie helpers assert on the
actual `Set-Cookie` header content. This is the same bar the auth-boundary code in this repo is
held to elsewhere (see `get_current_user`'s SOP) — untested code in the token-theft defense path
isn't acceptable for a security-critical service, brief-literal or not.

## What's involved

- `app/services/auth/sessions.py` (new, 123 lines) — the full interface described above.
- `tests/services/auth/test_sessions.py` (new, 9 tests) — the brief's 4 plus the 5 gap-fill
  tests noted above.
- Depends on: `app/db/models/auth.py::AuthSession` (Task 1), `app/core/security.py
  ::create_access_token` (pre-existing), `app/api/deps.py::Unauthorized` (pre-existing),
  `app/core/config.py` settings (`REFRESH_TOKEN_EXPIRE_DAYS`, `REFRESH_COOKIE_*`), `tests
  /factories.py::create_user`.
- Nothing calls this service yet — the login/refresh/logout endpoints (later tasks in this
  plan) are the consumers. This task is pure service-layer, no route wiring, no migration.

## Verification

RED (module missing):
```
$ poetry run pytest tests/services/auth/test_sessions.py -v
ModuleNotFoundError: No module named 'app.services.auth.sessions'
```

GREEN (target file):
```
$ poetry run pytest tests/services/auth/test_sessions.py -v
9 passed
```
Coverage on `app/services/auth/sessions.py`: 98% (55 statements, 1 missed — line 69, the
defensive `user is None` branch after a session row resolves to a deleted/missing user; not
reachable without deleting a user out from under a live session mid-test, judged not worth a
contrived test for one unreachable-in-practice guard clause).

Full suite:
```
$ poetry run pytest -q
57 passed
```
(48 baseline + 9 new, no regressions.)

Lint:
```
$ make lint
black --check app tests    -> All done! 76 files would be left unchanged.
isort --check-only app tests -> clean
ruff check app tests        -> All checks passed!
mypy app                    -> Success: no issues found in 44 source files
```

## Operate / roll back

- Pure application-layer addition; no migration, no config change, no deployment side effects.
- Nothing else imports this module yet, so reverting the commit is a clean, zero-fallout
  removal.
- Operationally: `REFRESH_COOKIE_SECURE` must be `true` in any non-local environment (default is
  `true`); only local dev over plain HTTP should ever set it `false`.

## Follow-ups

- The login/refresh/logout HTTP endpoints (later tasks) are the actual callers of
  `issue_token_pair`/`rotate_refresh`/`revoke_session`/the cookie helpers — this task ships the
  logic, not the wiring.
- `_revoke_family`'s bulk `.update()` relies on SQLAlchemy's default `synchronize_session=
  "evaluate"` to keep already-loaded `AuthSession` objects in the identity map in sync within
  the same transaction (needed for `test_reuse_revokes_family`'s post-call assertions). Fine for
  this codebase's simple `family_id`/`revoked_at IS NULL` filter; would need explicit
  `synchronize_session=False` + a manual refresh if the filter ever grows too complex for the
  evaluator.
- No rate limiting on `rotate_refresh` itself in this task — brute-forcing/guessing refresh
  tokens is defended by entropy (384 bits) rather than rate limiting; if that changes, rate
  limiting belongs on the endpoint layer (Task 16 already added `slowapi` for exactly this).

## Update — fix round 1: TOCTOU race in `rotate_refresh`

**What shipped**: the original `rotate_refresh` read the session row, checked
`rotated_at`/`revoked_at`/`expires_at` in Python, then wrote `rotated_at = now` — a
read-then-write with a window where two concurrent callers on the *same* refresh token could
both observe `rotated_at IS NULL` before either had written, both pass the check, and both
successfully rotate. This defeated the entire reuse-detection guarantee: a stolen token used
concurrently with the legitimate client wouldn't reliably revoke anything.

**Why (root cause)**: the "is this token still live" decision and the "consume it" write were
two separate steps under READ COMMITTED isolation, with no lock held across them.

**Fix**: made consumption atomic — the liveness check is now the `WHERE` clause of the
consuming `UPDATE` itself, not a prior `SELECT`:
```python
claimed = (
    db.query(AuthSession)
    .filter(
        AuthSession.id == session.id,
        AuthSession.rotated_at.is_(None),
        AuthSession.revoked_at.is_(None),
        AuthSession.expires_at >= now,
    )
    .update({AuthSession.rotated_at: now}, synchronize_session=False)
)
```
`claimed == 0` (already rotated/revoked/expired, or a concurrent caller just won the row) is
treated as reuse: revoke the whole family, raise `Unauthorized`. `claimed == 1` means this call
owns the row and proceeds to issue a new pair in the same family. Postgres's row-level locking
on the `UPDATE` makes this correct regardless of the callers' `SELECT` interleaving — a second
concurrent `UPDATE` on the same row blocks until the first transaction ends, then re-evaluates
its `WHERE` against the committed result.

**Accepted tradeoff**: a legitimate concurrent double-submit (not theft, e.g. a client retry
racing itself) still gets the whole family revoked, including the token the "winning" call just
issued — the server can't distinguish a race from theft, so both are treated as "burn the
family, force re-login." This matches standard refresh-token-rotation practice.

**Test**: `tests/services/auth/test_sessions_concurrency.py` —
`test_concurrent_rotate_of_same_token_only_one_winner`. Can't use the shared `db` fixture (one
connection, one never-committing savepoint, invisible across connections); instead runs two
real, independently-committing `Session`s against the `engine` fixture's connection pool, two
threads synchronized on a `threading.Barrier(2)` immediately before both call `rotate_refresh`
on the same raw token. Asserts exactly one thread succeeds and one raises `AppError`, and that
every session in the family (including the winner's new one) ends up revoked. Verified against
the *un*fixed code first (4/4 runs reproduced two successes, no revocation — the exact bug the
reviewer found), then against the fix (5/5 runs clean).

**Also in this pass**: `app/core/config.py`'s `REFRESH_COOKIE_SAMESITE` was an unconstrained
`str` — a misconfigured env value would only surface as a broken `Set-Cookie` header at
runtime. Added a `field_validator` that lowercases and restricts to `{"lax", "strict",
"none"}`, raising at `Settings()` construction time instead. Tests in `tests/test_config.py`.

**Files touched**: `app/services/auth/sessions.py` (`rotate_refresh` rewritten),
`app/core/config.py` (+validator), `tests/services/auth/test_sessions_concurrency.py` (new),
`tests/test_config.py` (+5 tests).

**Verification**: `poetry run pytest tests/services/auth/test_sessions.py -v` → 9 passed
(unchanged, no test needed updating). `poetry run pytest tests/services/auth/
test_sessions_concurrency.py -v` → 1 passed, run 5x clean. `poetry run pytest -q` → 63 passed
(no regressions). `make lint` → black/isort/ruff/mypy all clean.
