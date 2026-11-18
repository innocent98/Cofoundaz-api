# FE integration guide: Sign in / sign up with Google

> **Update 2026-11-05:** the exchange response now also carries `expires_in`, and `"refresh_token": null` when the request sends `X-Auth-Mode: cookie`. See `fe-integration-guide-auth-onboarding.md` section 1. (Covered by a unit test, not captured live.)

Every body below is copied from `e2e/_captures/google_sign_in/` (a live run against the real server; the file is named per section). Base path `/api/v1`. Emails and ids in captures are e2e placeholders. **Tokens, tickets and the OAuth `state` / `nonce` / `code_challenge` are replaced by `<…-placeholder>` in the captures** (they are bearer secrets); their shape is a plain string. The e2e server runs over `http`, so its cookie has no `Secure` flag; staging and production set it.

**Not exercised live:** Google's real consent screen and a real Google account. In the e2e, Google is faked at the transport layer (the server signs real RS256 id_tokens, so signature, `iss`, `aud`, `exp`, `nonce` and PKCE run for real). Check the first real round trip on staging.

## 1. What it is

"Continue with Google" on the login and sign-up pages. It is one flow for both: a Google account that is not known creates an account; a known one signs in. The FE never sees a Google token.

```
FE  POST /auth/oauth/google/start ──────────────► { auth_url }          (sets an HttpOnly cookie)
FE  window.location = auth_url ─────────────────► Google (user picks account, consents)
Google ─► browser ─► API GET …/callback ────────► 302 {FE}/auth/google/callback#ticket=…   or  ?error=…
FE  (page /auth/google/callback) POST …/exchange { ticket }  (credentials: include) ─► same body as POST /auth/login (+3 fields)
```

The FE needs one new page, `/auth/google/callback`, and must call `start` with `credentials: "include"` (the proof cookie is how the API ties the return trip to this browser).

## 2. Endpoints

### Start. `new_user_start`, `new_user_start_set_cookie`
`POST /auth/oauth/google/start` (no auth). Body (all optional):
```json
{"invite_token": "<raw invite token from the emailed link>", "next": "/dashboard", "accepted_terms": true}
```
**`accepted_terms` (new, 2026-11-13):** the Terms + Privacy checkbox, as on email sign-up. It is only consulted if this sign-in would
**create a new account**; an existing account signs in without it. Send `true` from the sign-up screen's Google button (next to the
checkbox); omit it on the plain login screen. See `terms_not_accepted` in section 4.
Response `200`:
```json
{
  "data": {
    "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=e2e-google-client-id.apps.example&redirect_uri=http%3A%2F%2F127.0.0.1%3A8010%2Fapi%2Fv1%2Fauth%2Foauth%2Fgoogle%2Fcallback&response_type=code&scope=openid+email+profile&state=<state-placeholder>&nonce=<nonce-placeholder>&code_challenge=<code_challenge-placeholder>&code_challenge_method=S256&prompt=select_account",
    "expires_in": 600
  },
  "meta": null
}
```
Navigate the **whole window** to `auth_url` (not a popup or iframe). Response header (value redacted in the capture): `set-cookie: cfz_google_signin=<proof>; HttpOnly; Max-Age=600; Path=/api/v1/auth/oauth/google; SameSite=lax`. The flow must finish within 10 minutes.

- `next` is optional: a same-site path to return to after sign-in. Anything that is not a plain path (`https://…`, `//…`, backslashes) is silently dropped.
- Unknown, used or expired `invite_token`: `404 {"error":{"code":"NOT_FOUND","message":"Not found.","field_errors":[]}}` (`error_invite_unknown`). Show "this invitation is no longer valid" and do not start Google.
- Not configured on the server: `503 GOOGLE_SIGNIN_NOT_CONFIGURED` ("Google sign-in isn't available right now."). *Not captured live* (the e2e server is configured); the code and message are from the unit test.
- Too many starts from one IP: `429 TOO_MANY_ATTEMPTS`. *Not captured live.*

### Callback (browser only, the FE never calls it). `new_user_callback_redirect`
Google sends the browser to the API, which always answers `302` to a fixed FE page:
```json
{"status": 302, "location": "http://localhost:3000/auth/google/callback#ticket=<ticket-placeholder>"}
```
The **ticket is in the URL fragment** (`#ticket=…`, so it never reaches a server log or a Referer); on failure there is `?error=…` instead. `next` (when it was sent to `start`) is a normal query parameter, e.g. `?next=%2Fdashboard#ticket=…` (`invite_match_callback_redirect`). Read the ticket from `location.hash`. Errors in section 4.

### Exchange. `new_user_exchange`
`POST /auth/oauth/google/exchange` body `{"ticket": "<from the URL fragment>"}`, **with `credentials: "include"`**: the ticket only works in the browser that called `start` (the proof cookie must come back with it), so a ticket URL pasted into another browser fails. Call it immediately on page load, once. Clear the fragment from the address bar (`history.replaceState`) before anything else.

New account (`200`):
```json
{
  "status": 200,
  "body": {
    "data": {
      "access_token": "<access_token-placeholder>",
      "refresh_token": "<refresh_token-placeholder>",
      "mfa_required": false,
      "is_new_user": true,
      "invite_token": null,
      "next": null
    },
    "meta": null
  }
}
```
The refresh cookie is set exactly as for password login. The three extra fields are the only difference from `POST /auth/login`:

| Field | Meaning | FE does |
|---|---|---|
| `is_new_user` | the account was created by this sign-in | route like a fresh email sign-up: call `GET /auth/me` and continue to **onboarding** (`new_user_me`: `memberships: []`, `active_workspace_id: null`, `profile.full_name` filled from Google) |
| `invite_token` | the invite that survived the round trip (Google email matched) | after the session is established, `POST /invitations/accept {token}` (`invite_match_accept`: `{"data":{"startup_id":"…","role":"team_member"},"meta":null}`), then go to the workspace |
| `next` | the path sent to `start` | go there instead of the default landing page |

Returning user (`returning_exchange`): identical, `"is_new_user": false`. Existing password account linked by email (`link_existing_exchange`): identical, `"is_new_user": false`; the password keeps working.

Invite example (`invite_match_exchange`): `"invite_token": "<invite_token-placeholder>"`, `"is_new_user": true`, `"next": "/dashboard"`.

### 2FA accounts. `mfa_exchange`, `mfa_challenge`
If the account has two-factor on, `exchange` returns **no session**:
```json
{
  "status": 200,
  "body": {
    "data": {
      "mfa_required": true,
      "mfa_ticket": "<mfa_ticket-placeholder>",
      "access_token": null,
      "is_new_user": false,
      "invite_token": null,
      "next": null
    },
    "meta": null
  }
}
```
Show the existing 2FA code screen and call the existing `POST /auth/mfa/challenge {mfa_ticket, code}` (`mfa_challenge`: `{"data":{"access_token":"…","refresh_token":"…"},"meta":null}`). **Keep `invite_token` and `next` from this response in memory (not in the URL) and use them after the challenge succeeds**; the challenge response does not repeat them.

### Reused or expired ticket. `error_ticket_reused`
```json
{"status": 400, "body": {"error": {"code": "GOOGLE_TICKET_INVALID", "message": "That sign-in has expired. Start again.", "field_errors": []}}}
```
Tickets last 2 minutes and work once. Same code if the account was suspended between the callback and the exchange, or if the request came from a different browser than the one that started the flow. Send the user back to login with the message.

## 3. Traps

- **Do not reload `/auth/google/callback`.** The ticket is single use; a reload gets `GOOGLE_TICKET_INVALID`. Guard against React strict-mode double effects (call exchange once).
- **Different shape from login only by three extra fields**; `access_token` is `null` (present, not absent) when `mfa_required` is true, same as login.
- **`start` is a POST that returns a URL**, not a redirect. The cookie only works if the request is made with credentials and the FE and API share a registrable domain (they do on staging and production).
- **The invite is accepted by the FE**, not by the Google flow. The API only checks the email match and hands the token back.
- **Google-only accounts have no password.** Password login for them returns the usual `INVALID_CREDENTIALS`. "Forgot password" lets them set one.

## 4. Error codes in `?error=`

The callback never shows a JSON body, only `…/auth/google/callback?error=<code>`. Captured: `error_denied_callback_redirect`, `error_unverified_callback_redirect`, `error_replayed_state_callback_redirect`, `invite_mismatch_callback_redirect`. The rest come from the unit tests and were *not captured live*.

| `error` | When | Message to show |
|---|---|---|
| `access_denied` (live) | the person cancelled on Google | "You cancelled Google sign-in." with a retry button |
| `email_not_verified` (live) | Google says the email is unverified | "Google hasn't verified that email address. Use another Google account or sign up with email." |
| `invalid_state` (live) | replayed or expired callback, or started in another browser | "That sign-in expired. Please try again." |
| `invite_email_mismatch` (live) | the Google email is not the invited email | "This invitation was sent to a different email address. Sign in with the invited Google account." |
| `invite_invalid` | invite used or expired during the detour | "This invitation is no longer valid." |
| `account_disabled` | the account is suspended | "This account has been suspended. Contact support." |
| `account_unavailable` | the account was deleted | "This account is unavailable." |
| `google_account_mismatch` | the Cofoundaz account is already linked to a different Google account | "This account is linked to a different Google account. Use that one, or sign in with your password." |
| `exchange_failed` | Google or its token failed checks, or Google was unreachable | "We couldn't verify your Google sign-in. Try again." |
| `terms_not_accepted` (live, strict server only) | a **new** Google account was about to be created but `start` was called without `accepted_terms: true` (`SIGNUP_REQUIRE_TERMS` is on). **No account was created.** | Send the person to the sign-up screen with the Terms checkbox, "Accept the Terms and Privacy Policy to create your account.", and call `start` again with `accepted_terms: true`. Captured: `…/callback?error=terms_not_accepted` |
| `maintenance` | maintenance mode is on and the Google account has no Cofoundaz account yet | "Cofoundaz is in maintenance. New accounts can't be created right now." (existing users can still sign in) |
| `rate_limited` | too many attempts from this address | "Too many attempts. Try again in a few minutes." |
| `try_again` | two sign-ins raced | "Something went wrong. Please try again." |

## 5. UX consequences to surface

- **First Google sign-in creates an account without a password** and with the email already verified. After onboarding, offer "set a password" only if you want one (it goes through "forgot password").
- **Signing in with Google when a password account exists links the two.** No extra step; the password keeps working. If that older account was never email-verified, its password is removed (Google just proved the email is the user's), so a later password login for it fails until a reset. No UI is needed for this.
- **Consent for new Google accounts (FE change needed).** Today the login page's "Continue with Google" calls `start` without a consent field, so with `SIGNUP_REQUIRE_TERMS=true` a first-time Google user lands on `?error=terms_not_accepted`. Handle it: show the Terms checkbox (on the sign-up page, or as an inline prompt after this error) and re-call `start` with `accepted_terms: true`. When accepted, the account stores `terms_accepted_at` and `terms_version`, the same as email sign-up. Existing Google accounts created before this change have no consent record; they are not asked again (they sign in by `sub`). A password account with no consent record that Google would **link or take over** needs the checkbox too: without `accepted_terms: true` it gets `?error=terms_not_accepted` and nothing is linked; with it, consent is stored on that account (unit-tested only, not captured live).
- **2FA is not skipped.** A 2FA user still enters the code after Google.
- **Hide the "Continue with Apple" button** until Apple sign-in is built.

## 6. Verification table

| Behaviour | Verified |
|---|---|
| start → callback → exchange for a new account, `/auth/me` after | live (e2e, Google faked) |
| New account without `accepted_terms` is refused (`?error=terms_not_accepted`), nothing created; with it the account is created; existing user signs in without it | live on the strict server (`e2e/_captures/auth_onboarding_gaps/google_terms_*`; `start` captures are shape-only placeholders because the `auth_url` carries bearer-ish state) and unit-tested (`tests/api/auth/test_google_signin.py`) |
| `terms_accepted_at` / `terms_version` stored for a consenting Google sign-up | unit only (`test_new_google_account_records_terms_acceptance`); not visible in any response |
| returning user; link by verified email; password still works | live (e2e) |
| 2FA account: no session, then MFA challenge succeeds | live (e2e) |
| ticket reuse; replayed callback; denied consent; unverified email | live (e2e) |
| invite match then `invitations/accept`; mismatch; unknown invite at start | live (e2e) |
| suspended, maintenance, double-link, wrong browser, bad nonce / audience / signature / PKCE, rate limit, not-configured 503 | unit tests only (`tests/api/auth/test_google_signin.py`) |
| Google's real consent screen, real id_token from Google, `Secure` cookie over https | **not exercised** |
