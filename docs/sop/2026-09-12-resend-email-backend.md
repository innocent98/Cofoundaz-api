# SOP — Resend email backend

**Date:** 2026-09-12 · **PR:** #54 (→ `develop`) · **Commit:** `cddafdc`

## What shipped
A `resend` option for the email `EmailSender` abstraction, so all transactional mail (auth verification/reset, workspace invites, document-share notifications) can be delivered via [Resend](https://resend.com) by setting `EMAIL_BACKEND=resend`. Plus a best-effort hardening of the document-share notification send.

## Why
Prior state: `EMAIL_BACKEND` was `console` (dev log only) / `smtp` (placeholder creds) / `file` (e2e). Adebayo added `RESEND_API_KEY` to `.env` intending to move to Resend. Everything already routes through `get_email_sender()`, so a Resend backend flips real delivery on for the whole app with no per-caller change.

## How (key decisions)
- **httpx, not the `resend` SDK** — `httpx ^0.28.1` is already a dependency; the Resend API is a single authenticated POST, so no new package.
- **Fail-loud sender** — `ResendEmailSender.send` raises on missing `RESEND_API_KEY`, missing `EMAILS_FROM_EMAIL`, or a non-2xx response, mirroring `SMTPEmailSender`. Flows that must deliver (verification/reset) surface failures.
- **Best-effort share notification** — `create_share_endpoint` wraps its send in try/except + log: a flaky provider must not 500 share creation, and the link is already in the 201 response. Auth/invite sends stay fail-loud. (Closes the follow-up flagged in the Module 18 Slice 3 review.)
- Reuses `EMAILS_FROM_EMAIL`/`EMAILS_FROM_NAME` for the From address (must be a Resend-verified domain).

## What's involved
- `app/platform/email.py` — `ResendEmailSender` + `resend` branch in `get_email_sender()`; `import httpx`.
- `app/core/config.py` — `RESEND_API_KEY: str | None = None` (was only tolerated via `extra="ignore"`).
- `app/api/v1/endpoints/documents.py` — `create_share_endpoint` try/except around the send (+ `log` import).
- `.env.example` — documents `RESEND_API_KEY` + `resend` as an `EMAIL_BACKEND` option.
- Tests: `tests/platform/test_email.py` (5 new, httpx mocked), `tests/api/test_document_shares.py` (share-create survives email failure).

## Verification
Unit 998 passed (6 new); live e2e green; ruff/black/mypy clean. Default backend unchanged (`console`), so no behavior change until `EMAIL_BACKEND=resend` is set. Resend was NOT exercised against the live API (mocked) — first real send is a deploy-time verification.

## Operate / roll back
- **Enable:** set `EMAIL_BACKEND=resend`, `RESEND_API_KEY`, and a Resend-verified `EMAILS_FROM_EMAIL` in `.env.staging.enc` / `.env.production.enc` (needs the env passphrase).
- **Roll back:** set `EMAIL_BACKEND` back to `console`/`smtp`/`file` — no code change; or revert the commit (additive, safe).

## Follow-ups
- Verify a real Resend send in staging once the verified domain + key are in the encrypted env.
- Optional: apply the same best-effort try/except to the workspace-invite send if invite delivery should not block invite creation (currently fail-loud by design).
