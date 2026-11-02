# Google sign-in and sign-up

**What shipped:** "Continue with Google" on login and sign-up. API only (FE brief: `cofoundaz/docs/fe-handoff/google-sign-in.md`). Branch `feature/google-sign-in`, PR: see the branch. **No migration**: it uses the `oauth_accounts` table that has existed since `0002_auth_tables`.

**Why:** the login page already shows the Google and Apple buttons with nothing behind them. Four product calls were accepted on 2026-11-02:

1. A verified Google email that matches an existing account signs into it and links Google to it. `email_verified` must be true.
2. 2FA still applies: a TOTP account ends in the same MFA challenge, and no session exists before the code.
3. A first Google sign-in creates the account with the email already verified, then onboarding is the same as email sign-up. An invite survives the round trip and is honoured only when the Google email is the invited email.
4. "Continue with Apple" is hidden by the FE until Apple is built.

**How**

| Topic | Decision |
|---|---|
| Flow | Authorization code + PKCE (S256) + OIDC `nonce`. `POST /auth/oauth/google/start` returns the Google URL; Google redirects the browser to `GET /auth/oauth/google/callback`; the API redirects to a **fixed** FE page `{APP_BASE_URL}/auth/google/callback?ticket=…` (or `?error=…`); the FE trades the ticket at `POST /auth/oauth/google/exchange` for exactly what `POST /auth/login` returns. No token ever sits in a URL. |
| Why a ticket | The callback is a browser navigation, so it cannot return JSON. A 120 s single-use Redis ticket carries the decision to a normal POST, where the refresh cookie and the MFA step work like password login. |
| State | A signed JWT (`typ=google_signin_state`, 10 min) holding only a random `jti`. PKCE verifier, nonce, invite token and FE return path stay **server-side in Redis** under that `jti` (an invite token in the browser-visible JWT would be readable). Popped atomically (MULTI/EXEC), so a state is good for one callback. |
| Browser binding | HttpOnly proof cookie `cfz_google_signin` (SameSite=Lax, path `/api/v1/auth/oauth/google`, 10 min) bound to the same `jti`. A callback URL started in another browser (login CSRF) has no cookie and is refused `invalid_state`. |
| id_token | Verified against Google's JWKS (fetched through the same stub-able client, cached 1 h, refetched on an unknown `kid`): RS256 only, `iss`, `aud` = our client id, `exp`/`iat`, `nonce`. Google tokens are used once and discarded. Scopes are `openid email profile` only, never Calendar. |
| Identity | Linked by Google `sub` in `oauth_accounts` (unique per provider + `sub`). Once linked, an email change at Google changes nothing. An account already tied to a **different** `sub` is refused `google_account_mismatch` (enforced in code under a user row lock; there is no `unique(user_id, provider)` index, see follow-ups). |
| Linking by email | Only with `email_verified=true`. If the matching row was an **unverified password sign-up**, Google has just proved the email is the caller's, so the row is taken over: password cleared, sessions revoked, pending verify/reset tokens killed, email marked verified. This stops pre-hijacking (an attacker pre-registers the victim's address). A verified account keeps its password. |
| New account | `status=active`, `email_verified_at=now`, no password (the user can set one with "forgot password"), name from Google. Publishes `auth.user.registered` and `auth.user.verified` (so email-based document grants bind, like sign-up). |
| 2FA | The exchange issues the same `mfa_ticket` as login; `POST /auth/mfa/challenge` finishes it. |
| Blocks | Disabled/suspended: `account_disabled` at callback, and the exchange re-checks (a suspension between the two steps still blocks). Soft-deleted: `account_unavailable`. Global sign-out: sessions are issued through `issue_token_pair`, same as password. **Maintenance**: the routes sit under the allowed `/auth/oauth` prefix so existing users can sign in, but the callback is a GET, which maintenance lets through, so it explicitly refuses to create a new account (`maintenance`). Password lockout (`locked_until`) is a guess counter and is not applied. |
| Invites | Checked **before** anything is created or linked: unknown/expired token `invite_invalid`; Google email ≠ invited email `invite_email_mismatch`; also refused if the resolved account's email is not the invited one. On success the exchange returns the `invite_token` and the FE calls the existing `POST /invitations/accept` once it holds a session (so a 2FA user joins only after the code). `start` refuses an unknown invite with 404 before the detour. |
| Open redirect | The API only redirects to the fixed FE path. The FE return path (`next`) must match a same-site path pattern (one leading `/`, no `//`, no `\`, no scheme) or it is dropped. |
| Abuse | Per-IP counter (30 per 10 min) on `start` (429 `TOO_MANY_ATTEMPTS`) and on the callback (`rate_limited`), plus the global limiter. |
| Audit | `auth.user.registered` (details method google), `auth.google.linked`, `auth.google.signin`, `auth.google.rejected` (details reason), `auth.login.success` (details method google). No token is logged. |

**What's involved**
- `app/services/auth/google_oidc.py` (Google client, PKCE, id_token check), `google_signin.py` (flow), `google_stub.py` (fake, non-production only), `app/api/v1/endpoints/auth/google.py` (routes), `app/schemas/auth.py`, `app/core/config.py` (`GOOGLE_SIGNIN_STUB`), `scripts/e2e_run.sh`.
- Tests `tests/api/auth/test_google_signin.py` (new account, link, 2FA, suspended, maintenance, invite match/mismatch, unverified email, bad/replayed state, wrong browser, nonce/aud/PKCE, redirect allowlist, rate limit, ticket reuse). Live journey `e2e/test_google_sign_in.py`, captures `e2e/_captures/google_sign_in/`. FE guide `docs/fe-integration-guide-google-sign-in.md`.

**Verification:** unit tests above; live e2e against a real uvicorn server with Google faked at the transport layer (the stub signs real RS256 tokens, so signature, `iss`, `aud`, `exp`, `nonce` and PKCE run for real). **Not exercised live: Google's real consent screen and a real Google account.** First check on staging after the console setup below.

**Operate / roll back**
- Env (same client as Calendar): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `API_PUBLIC_URL`, `APP_BASE_URL`, `REDIS_URL`. Without the first two, `start` answers 503 `GOOGLE_SIGNIN_NOT_CONFIGURED` and everything else keeps working. `GOOGLE_SIGNIN_STUB` is dev/e2e only and ignored when `ENVIRONMENT=production`.
- **Add these Authorized redirect URIs** to the existing OAuth client in the Google Cloud console (APIs & Services → Credentials), next to the Calendar ones:

| Environment | Redirect URI |
|---|---|
| Production | `https://api.cofoundaz.com/api/v1/auth/oauth/google/callback` |
| Staging | `https://staging-api.cofoundaz.com/api/v1/auth/oauth/google/callback` |
| Local | `http://localhost:8000/api/v1/auth/oauth/google/callback` |

  The OAuth consent screen needs the `openid`, `email` and `profile` scopes (non-sensitive, no verification review). In "Testing" mode only listed test users can sign in.
- Roll back: revert the PR. Linked `oauth_accounts` rows and Google-created users stay (those users have no password and can use "forgot password").

**Follow-ups**
- Add a unique index on `oauth_accounts(user_id, provider)` (needs a migration; today it is a lock-protected check).
- Email the account owner when Google is linked to an existing account.
- Show linked sign-in methods and an "unlink Google" action (needs a "has password" signal on `/auth/me`).
- Apple sign-in; the real consent screen on staging.
