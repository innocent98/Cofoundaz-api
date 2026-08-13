# SOP: Auth endpoints (signup → login → MFA → sessions → password reset → /me)

## What shipped

- Branch: `feat/auth-endpoints` (stacked on `feat/foundation-tenancy-spine`).
- Plan: `docs/superpowers/plans/2026-08-13-auth-endpoints.md` (14 tasks, all complete).
- Ledger: `.superpowers/sdd/2026-08-13-auth-endpoints/progress.md`; per-task briefs/reports
  `.superpowers/sdd/2026-08-13-auth-endpoints/task-{1..14}-{brief,report}.md`.
- Commit range: `4b5cb23` (auth models) through this task's commit (per-user rate-limit key).
  Key commits per surface are listed under "What's involved" below.

The full Module 01 auth HTTP surface under `/api/v1/auth/*`: signup/verify/resend, login with
lockout + MFA gate, TOTP MFA setup/verify/challenge + backup codes, refresh-token rotation +
logout, forgot/reset password, `GET /me`, and 501 seams for OAuth + SMS MFA. This task (14, the
plan's last) closes it out by switching the global rate limiter from per-IP to per-user keying.

## Why

Plan 1 (Foundation & Tenancy Spine) shipped `slowapi` wired to `get_remote_address` with a TODO to
switch to a per-user key "once auth context exists (Plan 2)" — see
`docs/sop/2026-08-12-rate-limiting-coverage-gate.md`. This plan built that auth context (JWT
access tokens via `create_access_token`/`get_current_user`), so the TODO's precondition is now
met. Per-IP keying also under-protects: users behind a shared IP (NAT, corporate proxy, mobile
carrier CGNAT) share one bucket and can be starved by each other's traffic, or conversely a single
abusive user can rotate IPs to dodge the limit once authenticated. Keying on the JWT `sub` when
present ties the limit to the actual caller.

## How

**`app/main.py`** — replaced `key_func=get_remote_address` with a new module-level
`_rate_limit_key(request: Request) -> str`:
- If `Authorization: Bearer <token>` is present and `jwt.decode(token, settings.SECRET_KEY,
  algorithms=[settings.ALGORITHM])` succeeds with a `sub` claim, return `f"user:{sub}"`.
- On any `JWTError` (expired, malformed, wrong signature) or a missing `sub`, fall back to
  `get_remote_address(request)` — same behavior as before this task, so unauthenticated traffic
  (including signup/login themselves, which can't carry a valid bearer token yet) is unaffected.
- The `user:` prefix keeps the key namespace disjoint from raw IP strings, so a crafted `sub`
  can't collide with a legitimate IP bucket.

Removed the stale "Plan 2" TODO comment now that the switch it described is done.

Deliberately **not** changed: `default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"]`,
`SlowAPIMiddleware` registration, and the `RateLimitExceeded` → 429 envelope handler — all
pre-existing and out of this task's scope. No endpoint currently applies a tighter
`@limiter.limit(...)` override; that remains a follow-up (see below).

## What's involved

**By surface (chronological):**

| Surface | Files | Commit(s) | SOP |
|---|---|---|---|
| Auth models + migration | `app/db/models/auth.py`, `app/db/models/enums.py`, alembic rev | `4b5cb23`, `0f0da72` | `2026-08-13-auth-tables-migration.md` |
| Password policy | `app/services/auth/password.py` | `146c3ed` | — |
| Refresh-session service | `app/services/auth/sessions.py` | `9217c88`, `5e229d4` (TOCTOU fix) | `2026-08-13-refresh-token-session-service.md` |
| Verification/reset tokens | `app/services/auth/tokens.py` | `671b73a` | — |
| MFA service (TOTP + backup codes) | `app/services/auth/mfa.py` | `cf59931` | `2026-08-13-mfa-service.md` |
| Signup/verify/resend | `app/api/v1/endpoints/auth/registration.py` | `43d3d69`, `a1eaaff` (fix) | `2026-08-13-signup-verify-resend-endpoints.md` |
| Login + lockout + MFA gate | `app/api/v1/endpoints/auth/login.py` | `1a4015f` | `2026-08-13-login-lockout-mfa-gate.md` |
| MFA TOTP setup/verify + challenge | `app/api/v1/endpoints/auth/mfa.py` | `4468a4c`, `0463aaa` (audit fix) | `2026-08-13-mfa-totp-challenge-endpoints.md` |
| Refresh rotation + logout | `app/api/v1/endpoints/auth/sessions.py` | `0281209`, `5e5ed04` (bodyless-cookie fix) | `2026-08-13-refresh-logout-endpoints.md` |
| Forgot/reset password | `app/api/v1/endpoints/auth/password.py` | `2016aa6`, `e6fcde8` (fix) | `2026-08-13-forgot-reset-password-endpoints.md` |
| `GET /me` | `app/api/v1/endpoints/auth/me.py` | `5d815b8` | `2026-08-13-me-identity-endpoint.md` |
| OAuth + SMS 501 seams | `app/api/v1/endpoints/auth/seams.py` | `24df12b` | `2026-08-13-oauth-sms-501-seams.md` |
| **Per-user rate-limit key (this task)** | `app/main.py`, `tests/api/test_rate_limit_key.py` | (this commit) | this doc |

**Router wiring:** `app/api/v1/endpoints/auth/__init__.py` aggregates `registration, login, mfa,
sessions, password, me, seams` under `app/api/v1/api.py`'s `/auth` prefix.

**Shared interfaces this plan introduced** (consumed across tasks, per the plan's Global
Constraints): `app/schemas/auth.py` request/response models; `AuthTokenPurpose`/`OAuthProvider`
enums; `create_access_token`/`verify_password`/`get_password_hash`
(`app/core/security.py`, pre-existing, unchanged); `get_current_user`/`get_optional_user`
(`app/api/deps.py`, pre-existing, unchanged).

## Verification

**This task (14):**

RED:
```
$ poetry run pytest tests/api/test_rate_limit_key.py -v
ImportError: cannot import name '_rate_limit_key' from 'app.main'
```

GREEN:
```
$ poetry run pytest tests/api/test_rate_limit_key.py -v
tests/api/test_rate_limit_key.py::test_key_uses_user_sub_when_authenticated PASSED
tests/api/test_rate_limit_key.py::test_key_falls_back_to_ip PASSED
2 passed
```

Full suite + coverage:
```
$ poetry run pytest --cov=app --cov-report=term
104 passed
TOTAL   971   41   96%
```

Lint:
```
$ make lint
black — All done, 99 files unchanged
isort — clean
ruff check — All checks passed
mypy — Success: no issues found in 55 source files
```

**Whole plan:** each task's SOP under `docs/sop/2026-08-13-*.md` (table above) carries its own
RED/GREEN evidence and fix-round history; `.superpowers/sdd/2026-08-13-auth-endpoints/progress.md`
is the authoritative task-by-task ledger (spec + quality review outcomes, fix rounds, deferred
items). Every task landed with a clean two-stage review (spec compliance, then code quality) after
at most one fix round.

## Operate / roll back

- No new config keys — reuses `settings.SECRET_KEY`, `settings.ALGORITHM`,
  `settings.RATE_LIMIT_PER_MINUTE`, all pre-existing.
- No migration in this task. Auth-surface migrations (Task 2) are the standard
  `alembic upgrade head` / `alembic downgrade -1` round trip — already verified in
  `docs/sop/2026-08-13-auth-tables-migration.md`.
- **Behavioral change:** authenticated requests (valid `Authorization: Bearer` header) now share a
  rate-limit bucket per user across source IPs, instead of per IP. Unauthenticated requests are
  unaffected (still keyed by remote address). If this causes unexpected 429s for a legitimate
  multi-device user, the fix is either raising `RATE_LIMIT_PER_MINUTE` or adding a per-route
  `@limiter.limit(...)` override — not reverting the key function.
- To roll back this task alone: revert its commit (restores `key_func=get_remote_address`).
- To roll back the whole plan: revert back to `4b5cb23^` (before auth models landed) — note this
  also removes the auth tables migration, so coordinate with anyone who has already migrated.

## Follow-ups

Carried forward from the plan's design spec and per-task self-review notes (none blocking; all
seams are honest 501s, not silent gaps):

- **SMS MFA** — `app/api/v1/endpoints/auth/seams.py` returns `FeatureNotEnabled` (501); real
  implementation needs an SMS provider integration (Twilio or similar) plus OTP delivery/rate
  limiting, deferred per the plan's Global Constraints.
- **Real Google/Apple OAuth** — same seam file, same 501 pattern; needs provider SDK integration
  and `OAuthAccount` linking flow (model already shipped in Task 1, unused until seams close).
- **`Idempotency-Key` middleware** — no money-write endpoints exist yet in this plan (auth only),
  so this wasn't in scope; flagged here so the next plan that adds a payment/money-write endpoint
  picks it up per the standing convention (Redis-backed cache + in-flight sentinel).
- **No per-route `@limiter.limit(...)` overrides** — login, password reset, and MFA challenge are
  natural candidates for tighter-than-default limits (brute-force/OTP-guessing surfaces); deferred
  across multiple task self-reviews (T8, T9, T11) as plan-wide, not blocking any single task.
- **Reset/verification emails embed a raw token, not a clickable URL** — codebase-wide convention
  per `app/platform/email.py`; product will need real links before this ships to real users.
- **Resend/login timing side-channels** — parked in Task 7/8 review as real but low-value (signup
  already discloses email existence by PRD design via the `EmailTaken` 409); proper fix is async
  email dispatch via a jobs worker, not urgent.
- **`poetry.lock` is gitignored repo-wide** — new deps (`pyotp`, `cryptography`) live only in
  `pyproject.toml`; flagged in Task 6's review as an infra/reproducible-build risk worth revisiting
  outside this plan's scope.

## Update — final whole-branch review fix wave (4 fixes, one commit-set)

A whole-branch review after Task 14 landed found four issues, all fixed in one pass (each with
its own RED test first). Three have dedicated SOP updates; the fourth (`tokens.py`, which never
had its own SOP — see the "Verification/reset tokens" table row above, commit `671b73a`, no SOP
column) is recorded here instead of a new file.

| Fix | Surface | Detail |
|---|---|---|
| A | `registration.py::resend`, `password.py::forgot` | 60s per-email Redis cooldown (spec §6.1) — see `2026-08-13-signup-verify-resend-endpoints.md` and `2026-08-13-forgot-reset-password-endpoints.md` |
| B | `me.py::me` | Deterministic `active_workspace_id` via `.order_by(Membership.created_at, Membership.id)` — see `2026-08-13-me-identity-endpoint.md` |
| C | `mfa.py::totp_setup` | Reject re-setup when already enrolled (409 `MFA_ALREADY_ENABLED`) — see `2026-08-13-mfa-totp-challenge-endpoints.md` |
| D | `tokens.py::consume_auth_token` | Atomic conditional-`UPDATE` claim, replacing read-check-write — detailed below |

**Fix D — `consume_auth_token` TOCTOU**: the original implementation read the `AuthToken` row,
checked `consumed_at is None and expires_at >= now` in Python, then wrote `consumed_at`. Two
concurrent callers consuming the same token (e.g. a doubly-submitted `/verify` or `/password/reset`
request) could both pass the Python check before either wrote, letting a single-use token be
consumed twice — the same class of race `sessions.py::rotate_refresh` was already hardened against
(see `2026-08-13-refresh-token-session-service.md`'s TOCTOU note). Fixed by conditioning the
`UPDATE` itself on the same predicate (`token_hash`, `purpose`, `consumed_at IS NULL`, `expires_at
>= now`) via `.update(..., synchronize_session=False)` and checking the returned row count — only
one concurrent claimant's `UPDATE` can match, exactly mirroring `rotate_refresh`'s pattern. All
three pre-existing token tests (`tests/services/auth/test_tokens.py`: consume-once → second call
fails, wrong-purpose rejected, expired rejected) pass unchanged against the new implementation — no
observable behavior change for the single-caller case, only for the race.

**Test added**: `tests/services/auth/test_tokens_concurrency.py::
test_concurrent_consume_of_same_token_only_one_winner` — same structure as
`test_sessions_concurrency.py`'s `rotate_refresh` race test (two threads on independent, real
`Session`s against the shared test-DB `engine`, synchronized with a `threading.Barrier`, since the
per-test `db` fixture's single never-committing transaction can't reproduce a cross-connection
race). Confirmed RED first: with the pre-fix read-check-write code, both threads won
(`assert 2 == 1` failed with `[(True, None), (True, None)]`); with the fix, exactly one thread wins
and the other raises `TokenInvalid`.

**Verification** (whole fix wave):
```
$ poetry run pytest -q
109 passed   (104 pre-wave + 5 new: 1 per fix for A/B/C/D, plus A covers two endpoints
              with one test each = signup/password suites +1 test apiece)

$ make lint
black  -> all files unchanged
isort  -> clean
ruff   -> all checks passed
mypy   -> success, no issues
```
Ran the full suite twice back-to-back (within the new 60s Redis cooldown window) to rule out
cross-run Redis-state flakiness from Fix A — both green. One pre-existing test
(`test_resend_invalidates_prior_unconsumed_verification_token`) needed a one-line fix (clear its
own cooldown key at the top) once Fix A's cooldown made it sensitive to being re-run within 60s of
itself — see the signup/resend SOP's update section for detail.

**Files changed (whole wave)**: `app/api/v1/endpoints/auth/registration.py`,
`app/api/v1/endpoints/auth/password.py`, `app/api/v1/endpoints/auth/me.py`,
`app/api/v1/endpoints/auth/mfa.py`, `app/services/auth/tokens.py`,
`tests/api/auth/test_registration.py`, `tests/api/auth/test_password_endpoints.py`,
`tests/api/auth/test_me.py`, `tests/api/auth/test_mfa_endpoints.py`,
`tests/services/auth/test_tokens_concurrency.py` (new), plus the SOP updates listed above and
the stale "Plan 2" line fixed in `docs/sop/2026-08-12-rate-limiting-coverage-gate.md`.

**Operate / roll back**: no migrations, no config changes. Fix A requires Redis reachable at
`settings.REDIS_URL` for `resend`/`forgot` to work at all now (previously optional for those two
routes) — matches the existing `mfa.py::challenge` precedent, which already hard-depends on Redis.
Roll back by reverting this fix-set's commit(s); each fix is independent and could also be reverted
individually if only one needs to be pulled.
