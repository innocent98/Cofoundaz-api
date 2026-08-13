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
