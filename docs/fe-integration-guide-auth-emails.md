# FE Integration Guide — Auth Email Links (verify & password reset)

> **For:** the frontend team. **Backend:** `cofoundaz-api`. **Status:** live on staging (2026-09-17).
> The verification and password-reset emails now contain a **clickable link to a front-end page**
> (previously they only showed a raw token). This guide is what the FE needs to handle those links.

---

## 1. The flow

1. A user signs up (or requests a password reset). The backend emails them a link.
2. **The link points at the FRONTEND, not the API:**
   - Verify email → **`{APP_BASE_URL}/verify-email/{token}`**
   - Password reset → **`{APP_BASE_URL}/reset-password/{token}`**
3. The user clicks it and lands on your page. **Your page extracts the `token` from the URL and
   calls the backend API** (below). The backend does the actual verification/reset.

`APP_BASE_URL` is the **web-app origin** (e.g. `https://app.cofoundaz.com`), set in the backend's
deploy env — it is what the email link is built from. See §4.

## 2. Pages the FE must serve

| Route | What it does |
|---|---|
| `/verify-email/{token}` (and `/verify-email`) | Read `token` from the path → `POST /api/v1/auth/verify` → show success ("Email verified, you can log in") or an error (invalid/expired link). |
| `/reset-password/{token}` | Read `token` from the path → show a "new password" form → `POST /api/v1/auth/password/reset` with the token + new password → show success or error. |

The `token` is an opaque URL-safe string (e.g. `Xy7...` — treat it as a blob; don't parse it). It is
**one-time use** and **expires** (verify: 24h, reset: 1h). Do not display it or log it.

## 3. API calls your pages make

Base path `/api/v1`. Standard envelope: success → `{"data": …, "meta": null}`; error →
`{"error": {"code": …, "message": …, "field_errors": […]}}`.

### Verify email — `POST /api/v1/auth/verify`
Request: `{"token": "<token-from-url>"}`
- **200** → `{"data": {"verified": true}, "meta": null}` — mark verified, send them to login.
- **400** → `{"error": {"code": "TOKEN_INVALID", "message": "…", "field_errors": []}}` — link is
  invalid, already used, or expired. Offer "resend verification email" (§5).

### Reset password — `POST /api/v1/auth/password/reset`
Request: `{"token": "<token-from-url>", "password": "<new-password>"}`
- **200** → `{"data": {"reset": true}, "meta": null}` — password changed; all their sessions are
  revoked, so send them to login.
- **400** → `TOKEN_INVALID` (invalid/used/expired link).
- **422** → `{"error": {"code": "WEAK_PASSWORD", …}}` — show the password-rules message.

## 4. ⚠️ `APP_BASE_URL` must be the front-end origin

The email link's base is the backend setting **`APP_BASE_URL`**, which **must be your web-app
origin** (e.g. `https://app.cofoundaz.com`, `https://staging-app.cofoundaz.com`). If it is left
unset, the backend falls back to `SERVER_HOST` (the **API** origin, `api.cofoundaz.com`) and the
email links would point at the API, which has no `/verify-email` page → broken.

**Action (backend/ops):** confirm `APP_BASE_URL` in the encrypted staging/prod env is the FE origin.
Quick check: the Resend dashboard → Emails log shows a real sent email; the "Verify email" link there
should read `https://<your-app-origin>/verify-email/…`, **not** `…api.cofoundaz.com/…`.

## 5. Related request-side endpoints (for context)

- **Signup** — `POST /api/v1/auth/signup` `{"email","password"}` → **201**
  `{"data": {"user": {"id","email"}, "verification_sent": true}, "meta": null}` (verified live on
  staging). Sends the verification email.
- **Resend verification** — `POST /api/v1/auth/verify/resend` `{"email"}` → always **200**
  `{"data": {"sent": true}, "meta": null}` (generic, no account-enumeration; throttled 60s/email).
- **Forgot password** — `POST /api/v1/auth/password/forgot` `{"email"}` → always **200**
  `{"data": {"sent": true, "message": "If that email has an account, a reset link is on its way."},
  "meta": null}` (verified live on staging; generic, throttled 60s/email). Sends the reset email.

## 6. Verification table

| Behaviour | Verified |
|---|---|
| Signup returns 201 + sends verification email (Resend) | ✅ live on staging 2026-09-17 |
| Forgot-password returns the generic 200 | ✅ live on staging 2026-09-17 |
| Email link format `/verify-email/{token}` & `/reset-password/{token}` | ✅ unit + e2e (token extracted from the link) |
| `verify` 200 `{verified:true}` / `reset` 200 `{reset:true}` | ✅ e2e journeys (shape shown is the standard envelope) |
| `verify`/`reset` 400 `TOKEN_INVALID`, `reset` 422 `WEAK_PASSWORD` | ✅ unit tests |
| Actual rendered link points at the FE origin | ⚠️ depends on `APP_BASE_URL` in the deploy env — confirm via Resend log (§4) |
