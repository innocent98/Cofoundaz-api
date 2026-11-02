# SOP — Staging signup 500: unverified Resend domain + typed email-delivery failure

| | |
|---|---|
| **Shipped** | Branch `fix/staging-signup-500` → PR to `develop` (link in the PR) |
| **Date** | 2026-10-21 |
| **Area** | Auth (signup, verify/resend, password/forgot), `app/platform/email.py` |

## What shipped

1. **Diagnosis (not code):** every `CD (staging)` run from #152 (2026-10-20 14:05 UTC) through
   #157 failed at the live E2E gate on `e2e/test_journey.py::test_duplicate_email_409`, because
   `POST /api/v1/auth/signup` returned **500**. The cause is that **Resend has un-verified the
   `cofoundaz.com` sending domain**. The account fix is Adebayo's (see *Operate*).
2. **Code hardening:** a mail-provider failure is now a typed, enveloped
   **`503 EMAIL_DELIVERY_FAILED`** instead of a bare-text `500 Internal Server Error`. The two
   anti-enumeration endpoints swallow it, so their responses still look the same whether or
   not the account exists.

## Why — root cause, with evidence

| Check | Result |
|---|---|
| Failing test from #152 on | Only `test_duplicate_email_409` (its first step is a real signup); 12/13 others pass |
| Code diff #151 (`ed282f4`, green) → #152 (`5a25aae`, red) | Assessment-only (migration 0060, assessment service, notifications registry). Nothing in auth or email |
| Fresh throwaway signup on `staging-api.cofoundaz.com` (2026-10-21) | `500`, body `Internal Server Error` as plain text, not our envelope, so an exception escaped the handlers |
| Staging env var names, #151 vs `develop` (values masked) | Only `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` were added (#154). `EMAIL_BACKEND=resend`, `RESEND_API_KEY` and `EMAILS_FROM_EMAIL` are set, and the From domain is `cofoundaz.com`. The failure also predates #154 |
| One direct Resend send with the same key (it is shared by staging and local `.env`; checked by hash only) | **`403 validation_error: "The cofoundaz.com domain is not verified"`**, with `x-resend-daily-quota: 0` and `x-resend-monthly-quota: 36`, so this is **not** a quota problem |
| Public DNS (`ns2.cloudoon.net` / `ns3.cloudoon.org`, the live delegation) | **No `resend._domainkey.cofoundaz.com` TXT, no `send.cofoundaz.com` MX or TXT.** Only Brevo and cloudoon SPF records are present |

`ResendEmailSender.send` raised a bare `RuntimeError` on any non-2xx. Nothing handled it, so
signup answered with a plain-text 500.

The most likely history: the Resend DKIM/SPF records were lost in the October zone edits on the
DNS host (see the ns1 stale-zone incident, 2026-10-10 to 10-17). Resend's periodic re-check then
flipped the domain to *not verified* between 2026-10-19 22:28 (last green) and 2026-10-20 14:05.

## How

- `app/core/errors.py`: new `EmailDeliveryFailed(AppError)` (`503`, `EMAIL_DELIVERY_FAILED`,
  *"We couldn't send the email right now. Please try again in a few minutes."*). It is 503
  because a dependency failed and the request can be retried.
- `app/platform/email.py` `ResendEmailSender`: a non-2xx response **or** an `httpx.HTTPError`
  (timeout, connect) logs the provider status and body at ERROR and raises
  `EmailDeliveryFailed`. A missing key or From address stays a `RuntimeError`, because that is a
  deploy misconfiguration and must stay loud.
- `POST /auth/signup`: unchanged code. The `AppError` handler envelopes the 503, and the
  uncommitted user is rolled back when the session closes, so the person can simply retry.
- `POST /auth/verify/resend` and `POST /auth/password/forgot`: the issue-and-send step now runs
  in a `begin_nested()` SAVEPOINT, and `EmailDeliveryFailed` is caught and logged. A failed send
  rolls back the token it would have delivered (and, for forgot, the audit row). The response
  stays the generic 200. Without this, a 500 or 503 only for *registered* emails would leak
  which emails have accounts.
- Best-effort callers (document share and signature emails, invites, the notification worker)
  already catch `Exception`, so they are unaffected.

**Rejected alternative:** answering signup with `201 verification_sent:false` when the send
fails. It would keep the gate green during a real email outage, hiding exactly this kind of
incident, and it would change the FE contract. A typed 503 keeps the gate honest and gives the
FE a retryable error.

## What's involved

| File | Change |
|---|---|
| `app/core/errors.py` | `EmailDeliveryFailed` |
| `app/platform/email.py` | Resend non-2xx / transport → logged + `EmailDeliveryFailed` |
| `app/api/v1/endpoints/auth/registration.py` | resend: SAVEPOINT + swallow |
| `app/api/v1/endpoints/auth/password.py` | forgot: SAVEPOINT + swallow |
| `tests/platform/test_email.py` | non-2xx, 403-unverified-domain and transport-error cases |
| `tests/api/auth/test_registration.py` | signup → 503 envelope; resend stays generic |
| `tests/api/auth/test_password_endpoints.py` | forgot stays generic and leaves no token |

No migration, no env var, no config change.

## Verification

- TDD: the 5 behavioural tests were red on `develop`. The forgot test was confirmed red against
  the old `password.py` (`EmailDeliveryFailed` escaped), then all were green with the fix.
- Full local CI parity (pinned Poetry toolchain, `DATABASE_URL=…/ci_scratch`, `TZ=UTC`):
  black, isort, ruff, mypy, bandit, pylint ≥ 9.5, and the full pytest suite with the coverage
  gate. The final numbers are in the PR body.
- **Not verified live:** the 503 envelope on staging. Staging runs `develop` (`18949a6`), which
  does not contain this branch yet. After merge plus the DNS fix, signup should return 201 again.

## Operate / roll back

**Account action (Adebayo), which is what turns staging green:**

1. Resend dashboard → **Domains → `cofoundaz.com`**. It will show *Not verified / Failed*.
   Copy the DNS records it lists: the DKIM TXT at `resend._domainkey`, and the MX and SPF TXT
   on `send`. (Use the region or subdomain names the dashboard shows. Don't guess them.)
2. Add those records in the DNS host's zone for `cofoundaz.com` (Truehost / cloudoon, the zone
   served by `ns2.cloudoon.net` / `ns3.cloudoon.org`). Leave the existing Brevo and cloudoon
   SPF records as they are.
3. Check propagation:
   `dig +short TXT resend._domainkey.cofoundaz.com @ns2.cloudoon.net` should return the `p=` key.
4. Resend → **Verify DNS records**, then wait for *Verified*.
5. No secret change, re-encrypt or redeploy is needed: the key and env are fine. Re-run the
   latest failed `CD (staging)` run (`gh run rerun <id> -R innocent98/Cofoundaz-api --failed`)
   so the gate mints the staging-verified tag again.

**Roll back the code:** revert the PR. Behaviour returns to a bare 500 on provider failure.

## Follow-ups

- Any **other** unhandled exception still returns plain-text `Internal Server Error`. A generic
  `Exception` handler that returns the envelope (and logs the traceback) is worth its own change.
- Earlier checklist notes blamed the local key being "over quota". On 2026-10-21 the key showed
  0 daily and 36 monthly sends, so those were likely rate-limit 429s or this same domain issue.
- Add a cheap DNS assertion (DKIM record present) to the deploy smoke checks, so a lost
  record shows up as a named failure instead of a signup 500.
