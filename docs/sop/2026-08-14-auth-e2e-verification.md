# SOP — Auth API live E2E verification harness

**What shipped** — A committed, repeatable live end-to-end harness for the Module 01 auth API, plus a `file` email backend it depends on. Run with `make e2e` (or `./scripts/e2e_run.sh`). Surfaced and fixed one real dev-config bug (empty `MFA_ENCRYPTION_KEY`).

## Why
Plan 2 left the auth surface covered by 109 tests, but all at the in-process **TestClient** layer (rolled-back transactions, disabled rate limiter, monkeypatched MFA key). Before starting Plan 3 (Onboarding) we wanted the endpoints exercised **back-to-back against a real running server over HTTP** with persisted data — the path that catches config, middleware, cookie-transport, and serialization issues TestClient masks.

## How
Three layers, all against a real `uvicorn` server on an **isolated `cofoundaz_e2e`** Postgres DB (migrated from zero each run) + live Redis:

| Layer | File | What it proves |
|---|---|---|
| **Sanity** | `scripts/e2e_run.sh` preflight | db/redis up → recreate+`alembic upgrade head` on a clean DB → `import app.main` → boot server → `/health` green |
| **Smoke** | `e2e/test_smoke.py` | every auth route answers its *expected* status on the live stack (signup 201, `/me` 401, seams 501, unknown 404, validation → enveloped 422); OpenAPI lists the routes |
| **Journeys** | `e2e/test_journey.py` | full real-data flows + security negatives |

**Journeys (11):** signup→verify→login→`/me`; MFA setup→enable→logout→login-gate→challenge; refresh rotation + **reused-token→family revoked**; **cookie-only refresh**; forgot→reset→re-login (old pw rejected); **reset revokes existing sessions**; duplicate-email 409; weak-password 422; **login lockout after 5**; **60s resend throttle**; forgot generic-for-unknown.

Key harness mechanics: `EMAIL_BACKEND=file` writes each email as JSON to `var/mail-e2e/`; the suite reads the latest to extract the one-time verify/reset token (black-box). Real TOTP codes computed with `pyotp`. Server runs `REFRESH_COOKIE_SECURE=False` (HTTP dev-mode) and an ephemeral generated `MFA_ENCRYPTION_KEY`, so the run is self-contained and independent of `.env`.

## What's involved
- `app/platform/email.py` — new `FileEmailSender` + `EMAIL_BACKEND=file` in `get_email_sender()`.
- `app/core/config.py` — `EMAIL_FILE_DIR` setting (default `./var/mail`).
- `e2e/conftest.py`, `e2e/test_smoke.py`, `e2e/test_journey.py` — the harness (excluded from the unit suite by `testpaths=["tests"]`).
- `scripts/e2e_run.sh`, `Makefile` (`make e2e`).

## Verification
- **Live E2E: 18 passed** against the running server (`make e2e`).
- **Unit suite: 111 passed, 96% coverage** (109 baseline + 2 new email-backend tests), `make lint` clean.

## Findings & fixes
1. **`MFA_ENCRYPTION_KEY` was empty in `.env` → all MFA endpoints returned 500 `MFA_NOT_CONFIGURED`.** A genuine dev-config bug the unit tests couldn't catch (they monkeypatch a key). **Fixed:** set a valid dev Fernet key in the (gitignored) `.env`; the e2e harness generates its own ephemeral key so it never depends on `.env`. **Action for deploys:** every environment MUST set a persistent `MFA_ENCRYPTION_KEY` (rotating it invalidates enrolled TOTP secrets).
2. **Cookie-only refresh failed over plain HTTP — expected, not a bug.** `REFRESH_COOKIE_SECURE=True` marks the refresh cookie `Secure`; a client correctly won't send a `Secure` cookie over `http://`. The harness runs HTTP-dev-style (`REFRESH_COOKIE_SECURE=False`); production over HTTPS is unaffected.

## Operate
- Run: `make e2e`. Requires docker `db`+`redis` and a populated `.env` (`SECRET_KEY`; the harness supplies its own MFA key). Recreates `cofoundaz_e2e` each run; the dev DB is untouched.
- The server is launched/torn down by the script (trap on EXIT); mail capture lands in `var/mail-e2e/` (gitignored).

## Follow-ups
- **Harden MFA misconfig:** consider a fail-fast startup check (or a 503 with a clearer message) when MFA is reachable but `MFA_ENCRYPTION_KEY` is unset, instead of a request-time 500.
- Wire `make e2e` into CI once a CI Postgres/Redis service is available.
- The Plan 2 deferred list still stands (pre-SMTP email-as-link + send-after-commit, per-route brute-force limits, `poetry.lock` tracking, audit completeness).
