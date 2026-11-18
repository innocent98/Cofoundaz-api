# FE integration guide: Auth & Onboarding API fixes (Module 01)

**API PR:** `feature/auth-onboarding-gaps` (migration `0096_auth_onboarding_gaps`). **FE work this unlocks:** gaps 2, 5, 8, 9, 10, 12, 14,
17, 18, 23 of `cofoundaz/docs/checklist/auth-onboarding-prd-gaps.md` (the FE PR), plus the token-storage move (D-AUTH-6).

**Where every body below comes from.** Each request/response in this guide is copied from a file in
[`e2e/_captures/auth_onboarding_gaps/`](../e2e/_captures/auth_onboarding_gaps/) (name in brackets), written by the live journey
`e2e/test_auth_onboarding_gaps.py` against a real uvicorn + Postgres + Redis. Tokens, tickets, secrets and passwords are replaced by
labelled placeholders (`<access-token>`, `<refresh-token>`, `<mfa-ticket>`, `<test-password>`); emails are fake test addresses. A
statement that was **not** exercised live says so inline, and the table at the end marks every behaviour verified-live or not.

Envelope reminder: success is `{"data": ..., "meta": null}`; errors are `{"error": {"code", "message", "field_errors": [], ...}}`.
Some errors now carry extra members inside `error` (`retry_after_seconds`, `attempts_remaining`): see below.

---

## 1. Sessions: tokens out of `localStorage` (D-AUTH-6, backend side)

The API already set an HttpOnly refresh cookie. What changed is that a client can now live on the cookie alone, and that doing so is
protected against CSRF. **The current FE keeps working unchanged** until it switches (it sends the refresh token in the body).

| | Cookie-mode client (new FE) | Header-auth client (current FE) |
|---|---|---|
| Identify yourself | send `X-Auth-Mode: cookie` on login, MFA challenge, Google exchange, refresh, logout | send nothing extra |
| Where the refresh token lives | only the `cfz_refresh` cookie; body says `"refresh_token": null` | body `refresh_token` (and also the cookie) |
| Refresh | `POST /auth/refresh` with the cookie, **no body**, header `X-Auth-Mode: cookie` | `POST /auth/refresh` with `{"refresh_token": "..."}` |
| Logout | `POST /auth/logout` with the cookie, header `X-Auth-Mode: cookie` | body token, or cookie |

Cookie attributes (the `Set-Cookie` the API sends): `cfz_refresh=<value>; HttpOnly; Max-Age=2592000; Path=/api/v1/auth; SameSite=lax`
plus `Secure` in every deployed environment. The live e2e server runs over plain HTTP with `REFRESH_COOKIE_SECURE=False`, so the
captured header has no `Secure`; the default (`Secure` on) is asserted in a unit test, not captured. The cookie is only sent to
`/api/v1/auth/*`, so **`fetch` to those routes needs `credentials: "include"`** and the FE and API must be same-site
(`www.cofoundaz.com` and the API host are; a `*.vercel.app` preview is not: it fails CORS first, as before).

### Login in cookie mode [`login_cookie_mode_200`]
```
POST /api/v1/auth/login            headers: X-Auth-Mode: cookie
{"email": "<email>", "password": "<test-password>"}
→ 200   Set-Cookie: cfz_refresh=<refresh-token>; HttpOnly; Max-Age=2592000; Path=/api/v1/auth; SameSite=lax
{"data": {"access_token": "<access-token>", "refresh_token": null, "expires_in": 900, "mfa_required": false}, "meta": null}
```
Same call **without** the header [`login_legacy_200`]: identical, but `"refresh_token": "<refresh-token>"` is a string. Both now include
`expires_in` (seconds, 900): use it to schedule the silent refresh a minute early. `refresh_token` is **present-but-null** in cookie
mode, never absent.

### Silent refresh [`refresh_cookie_mode_200`]
```
POST /api/v1/auth/refresh          headers: X-Auth-Mode: cookie       (no body, cookie sent by the browser)
→ 200   Set-Cookie: cfz_refresh=<refresh-token>; HttpOnly; Max-Age=2592000; Path=/api/v1/auth; SameSite=lax
{"data": {"access_token": "<access-token>", "refresh_token": null, "expires_in": 900}, "meta": null}
```
The cookie value **rotates on every refresh**. The previous value keeps working for a short **grace window** (20 s by default, see the
next section), so a lost response or a second tab no longer signs the user out; still share one in-flight refresh (single-flight, as the
current client does) so tabs don't churn tokens. On page load, call refresh once to get the first access token;
a `401` means "not signed in" [`refresh_after_logout_401`]: `{"error": {"code": "UNAUTHORIZED", "message": "Not authenticated.", "field_errors": []}}`.
Header-auth client with the body token [`refresh_body_token_200`]: same shape, `"refresh_token": "<refresh-token>"` is a string.

### CSRF: what the FE must do, and what 403 means
A request whose **only** credential is the cookie (no refresh token in the body) must send `X-Auth-Mode: cookie`, and if the browser
sends an `Origin` it must be one of the API's allowed origins (the same list as CORS). Otherwise:

[`refresh_cookie_no_header_403`], [`refresh_cookie_foreign_origin_403`], [`logout_cookie_no_header_403`]:
```
→ 403   {"error": {"code": "CSRF_REJECTED", "message": "This request didn't come from the Cofoundaz app.", "field_errors": []}}
```
A browser sends `Origin` by itself; you never set it. A refresh token supplied in the body needs neither header nor origin (an attacker
cannot know it). **Which token is used:** the body's when there is one, the cookie's only when the body is empty. So a decoy body token
next to the browser's cookie is just a wrong token: [`refresh_cookie_decoy_body_401`] `401 {"error": {"code": "UNAUTHORIZED", "message": "Not authenticated.", "field_errors": []}}`
with no `Set-Cookie` (the cookie is not rotated), and [`logout_cookie_decoy_body_200`] `200 {"data": {"logged_out": true}, "meta": null}`
with **no `Set-Cookie`** (a decoy cannot clear the browser's cookie or sign the user out; a body token that is a real session's still
signs out and clears the cookie). The real cookie session survives both (the journey refreshes with it afterwards). `CSRF_REJECTED` means the FE forgot the header (a bug), not a user problem: treat it as "signed out" and log it.
Why this works: a plain HTML form cannot add a custom header, and a cross-origin `fetch` that adds one triggers a CORS preflight that
only allowed origins pass; `SameSite=lax` keeps the cookie off cross-site sub-requests in the first place.

### Grace window: the previous refresh token still works for ~20 s [`grace_refresh_replay_in_window_200`], [`grace_refresh_other_tab_200`]
If a refresh response is lost (aborted request, browser quit mid-flight) or a second tab refreshes with the token the first tab just
used, presenting that **immediately-previous** token within `REFRESH_GRACE_SECONDS` (default **20**) of its rotation returns a normal `200`
with a **new, different** pair (it is *not* the pair the first call returned: the server only stores hashes). Captured body, same shape as
any refresh: `{"data": {"access_token": "<access-token>", "refresh_token": "<refresh-token>", "expires_in": 900}, "meta": null}`.
Rules the FE can rely on: always store and use the **newest** pair you receive; the window is measured from the moment *that token* was
rotated, so a token never works for more than 20 s after being superseded; once the window has passed, the next section applies.
Only the **immediately-previous** token gets grace: a token two rotations back, or the same old token replayed a second time after a
grace use, is reuse (`401`, sign-in revoked) even inside the 20 s. A tab holding the other tab's just-superseded token is the
immediate predecessor and is accepted, moving the family forward. The server keeps exactly one live token per sign-in.

### Refresh-token reuse kills the sign-in [`refresh_reuse_401`], [`refresh_newest_after_reuse_401`], [`grace_refresh_replay_after_window_401`]
Presenting an already-rotated refresh token **after the grace window** is treated as theft: the whole sign-in (every token in its family) is revoked and the
revocation is committed. Captured: reuse of the old token `401 UNAUTHORIZED`, then the newest token of the same sign-in also `401`.
**FE:** any `401` from refresh means "signed out": go to `/login`. Two tabs refreshing at the same instant no longer trigger this (grace
window), but a token that is older than the window still does, e.g. a tab that slept for minutes holding a superseded token: re-read the
cookie (cookie mode does this for free) rather than caching the refresh token in memory.

### Logout [`logout_cookie_mode_200`]
```
POST /api/v1/auth/logout           headers: X-Auth-Mode: cookie
→ 200   Set-Cookie: cfz_refresh=<refresh-token>; expires=<past date>; Max-Age=0; Path=/api/v1/auth; SameSite=lax
{"data": {"logged_out": true}, "meta": null}
```
The token is revoked server side, not just cleared in the browser (a later refresh with it is `401`).

### Access tokens outlive sign-out (gap 25, note for the security page) [`me_with_old_access_token_after_logout_200`]
A sign-out or a password reset revokes **refresh** tokens. An access token already issued stays valid until it expires (15 minutes):
captured, `GET /auth/me` with the pre-logout access token still returns `200`. Keep the access token in memory only, drop it on
logout, and do not promise "signed out everywhere instantly" in copy.

---

## 2. Sign-in lockout and rate limits (gaps 1, 2, 3, 4)

### Wrong password, unknown email: same answer [`login_wrong_password_401`], [`login_unknown_email_401`]
```
→ 401   {"error": {"code": "INVALID_CREDENTIALS", "message": "That email and password don't match.", "field_errors": []}}
```
### Locked: 429 `ACCOUNT_LOCKED`, identical for a real and an unknown email [`lockout_known_email_429`], [`lockout_unknown_email_429`]
```
→ 429   Retry-After: 900
{"error": {"code": "ACCOUNT_LOCKED", "message": "Too many attempts. Try again in 15 minutes or reset your password.", "field_errors": [], "retry_after_seconds": 900}}
```
- The lock trips on the **5th** wrong password for that **(email, IP)**; it holds 15 minutes from that moment. Attempts made while locked
  are refused and do not extend it. A different IP is not locked [`lockout_other_ip_login_200`], so a stranger cannot lock the owner out.
- The right password is refused too while locked [`lockout_right_password_429`] (same body). Do not tell the user which part was wrong.
- **Show `error.message` as it comes** (it already has the minutes and the PRD wording, `1 minute` singular at the end) and add the
  "reset your password" link to `/forgot-password`. For a live countdown use `retry_after_seconds`; the `Retry-After` header carries the same number.
- **A password reset lifts the lock** [`reset_while_locked_200`] then [`login_after_reset_from_locked_ip_200`]: `{"data": {"reset": true}, "meta": null}`,
  and the next login with the new password from the previously locked IP succeeds.
- A suspended account counts as a failure like any other (it never looks different), and an unknown email or a Google-only account still
  pays for one password check, so response time does not tell which addresses exist.
- **How it is counted (no race):** each attempt is reserved atomically before the password is checked, so a burst of parallel requests
  gets exactly 5 tries, not 60. A good sign-in clears the (email, IP) counter.
- **The email-wide ceiling (30 failures over all IPs) refuses only addresses with no good sign-in to that account in the last 30 days.**
  The owner's usual networks are never locked out by someone spreading guesses across many IPs; a stranger's address is.
- **IPv6** counters (here and in the limits below) are keyed on the /64, so rotating through addresses of one block does not help.

### Per-route limits: 429 `RATE_LIMITED`
Sign-up (10/hour per IP), forgot password (20/hour per IP; plus a silent cap of 5/hour per (email, caller IP) and 20/hour per email overall),
verify-resend (20/hour per IP; the same two silent caps) and login (60 attempts per 15 minutes per IP, across emails). The overall
per-email cap only counts calls the per-IP one let through, so one caller can use at most 5 an hour of an address's 20 and cannot silence its owner. Defaults, tunable by env. The strict test server lowered
three of them to 12 / 6 / 20 to reach them live. [`signup_rate_limited_429`], [`forgot_rate_limited_429`], [`login_ip_rate_limited_429`]:
```
→ 429   Retry-After: 3597
{"error": {"code": "RATE_LIMITED", "message": "Too many requests. Try again in 60 minutes.", "field_errors": [], "retry_after_seconds": 3597}}
```
Login's is `retry_after_seconds: 900` and "Try again in 15 minutes." Show `message`; disable the submit button for `retry_after_seconds`.
**Silent caps are not errors:** past either per-email hourly cap, forgot returns the normal `200 {"sent": true, ...}` and sends nothing
(by design: an error there would reveal whether the address has an account). The 60-second repeat gets the same body [`forgot_repeat_200`]:
`{"data": {"sent": true, "message": "If that email has an account, a reset link is on its way."}, "meta": null}`.
(The per-email cap itself is unit-tested, not captured live: the live run only reached the 60 s cooldown and the per-IP cap.)

---

## 3. Sign-up consent (gap 14)

`POST /auth/signup` now requires `"accepted_terms": true`. Missing or `false` creates nothing and sends no email
[`signup_without_terms_422`], [`signup_terms_false_422`] (identical):
```
→ 422   {"error": {"code": "TERMS_NOT_ACCEPTED", "message": "Accept the Terms and Privacy Policy to create your account.",
         "field_errors": [{"field": "accepted_terms", "message": "Accept the Terms and Privacy Policy to continue."}]}}
```
Success is unchanged [`signup_with_terms_201`]:
```
POST /api/v1/auth/signup   {"email": "<email>", "password": "<test-password>", "accepted_terms": true}
→ 201   {"data": {"user": {"id": "<uuid>", "email": "<email>"}, "verification_sent": true}, "meta": null}
```
The server stores the time and the version of the Terms/Privacy (`terms_version`, now `2026-11-05`); neither is returned by any endpoint,
so this guide cannot show it (verified in a unit test against the database). **Order of checks:** rate limit, then terms, then password
strength, then "email taken". Google-created accounts have no checkbox and no recorded consent (follow-up).

**Deploy ordering (read this):** the current FE does not send `accepted_terms`, so every sign-up from it gets `422 TERMS_NOT_ACCEPTED`
the moment this API is live. Either ship the FE change first, or set `SIGNUP_REQUIRE_TERMS=false` on the API until it is live.

---

## 4. Verify email: resend (gap 12)

`POST /auth/verify/resend` now returns `sent` and `retry_after` (seconds), for **every** address alike (unknown included):
[`resend_first_200`] `{"data": {"sent": true, "retry_after": 60}, "meta": null}`;
[`resend_unknown_email_200`] the same body; [`resend_within_60s_200`] `{"data": {"sent": false, "retry_after": 60}, "meta": null}`.
Drive the "Resend in {s}s" countdown from `retry_after`; when `sent` is `false` do **not** say "a fresh link has been sent". A hit on
the per-email hourly cap also answers `sent: false` with a longer `retry_after` (unit-tested, not captured). The IP cap is the
`429 RATE_LIMITED` above.

---

## 5. MFA challenge: the ticket survives a typo (gap 8)

Login with 2FA on [`login_mfa_required_200`]:
`{"data": {"mfa_required": true, "mfa_ticket": "<mfa-ticket>", "access_token": null}, "meta": null}`. The ticket lives 5 minutes.

| Situation | Response |
|---|---|
| Wrong code, 1st to 4th time [`mfa_wrong_code_401`] | `401 {"error": {"code": "MFA_INVALID_CODE", "message": "That code isn't right. Try again.", "field_errors": [], "attempts_remaining": 4}}`, ticket stays usable |
| Right code after a typo [`mfa_right_code_after_typo_200`] | `200 {"data": {"access_token": "<access-token>", "refresh_token": "<refresh-token>", "expires_in": 900}, "meta": null}` (+ cookie; `refresh_token` is `null` in cookie mode) |
| 5th wrong code [`mfa_fifth_wrong_code_401`] | `401 {"error": {"code": "MFA_TICKET_EXPIRED", "message": "Your sign-in timed out. Log in again.", "field_errors": []}}`, ticket burned |
| Any code on a burned, used or unknown ticket [`mfa_burned_ticket_right_code_401`], [`mfa_ticket_reuse_401`], [`mfa_unknown_ticket_401`] | the same `MFA_TICKET_EXPIRED` body |

**FE:** on `MFA_INVALID_CODE` stay on the code screen (show `attempts_remaining`); on `MFA_TICKET_EXPIRED` go back to `/login` with the
message. A backup code counts as a code. The ticket is single use: a successful challenge burns it. **Also:** more than 10 wrong
codes on one **account** across tickets (someone with the password minting tickets) answers `429 ACCOUNT_LOCKED` with `retry_after_seconds`
on the challenge call, even for the right code until it lifts (a password reset lifts it). That path is unit-tested, **not captured live**.

---

## 6. Onboarding state and input rules

### `GET /onboarding/state` now has `pending_invite` [`onboarding_state_200`]
```
{"data": {"step": 1, "completed": false, "assessment_pending": true, "pending_invite": false,
  "founder_profile": {"full_name": null, "role_title": "Founder & CEO", "country": null, "phone": null, "how_heard": null},
  "startup": {"id": "<uuid>", "name": null, "description": null, "website": null, "logo_url": null, "industry": null,
              "business_model": null, "stage": null},
  "goals": [], "ai_panel": null, "notes": null, "invites": []}, "meta": null}
```
(`assessment_pending: true` on a draft is how the API has always answered; it only matters after `completed`.)

### Someone the wizard is not for (gap 11)
**A person with a pending invitation and no workspace of their own** [`invitee_state_pending_invite_200`], the state is only a display:
```
{"data": {"step": 1, "completed": false, "assessment_pending": false, "pending_invite": true, "founder_profile": null,
  "startup": null, "goals": [], "ai_panel": null, "notes": null, "invites": []}, "meta": null}
```
**FE:** `pending_invite: true` means "show them their invitation" (with Accept and Decline), not "block the wizard". `GET` creates no draft
startup for them. `startup` is **null** here but an object elsewhere, and `founder_profile` is null here and for members: check before
reading. **A pending invitation never blocks writing:** anyone can invite any address, so the invitee can still `PATCH /onboarding/state`
and start their own startup [`pending_invitee_patch_200`] (the next `GET` then has `pending_invite: false`). To clear the invitation
without accepting, decline it:

```
POST /api/v1/invitations/decline    {"token": "<token from the invite link>"}     (signed in, verified, same email as the invite)
→ 200   {"data": {"declined": true}, "meta": null}                                            [decline_invite_200]
→ 400   {"error": {"code": "TOKEN_INVALID", "message": "That link is invalid or has expired.", "field_errors": []}}      [decline_invite_again_400]
→ 403   {"error": {"code": "INVITE_EMAIL_MISMATCH", "message": "This invitation was sent to a different email address.", "field_errors": []}}   [decline_invite_wrong_account_403]
```
After a decline, `GET /onboarding/state` is a normal draft (`pending_invite: false`, `startup` an object) [`state_after_decline_200`]. A declined
invitation cannot be accepted later (the same `400 TOKEN_INVALID`; the inviter can invite again).

**A member of a workspace** (after accepting) gets `409 ONBOARDING_NOT_FOR_INVITED` on `PATCH /onboarding/state` and `POST /onboarding/complete`
[`member_patch_409`], [`member_complete_409`]: `{"error": {"code": "ONBOARDING_NOT_FOR_INVITED", "message": "You joined a workspace as a team member, so there is no startup to set up.", "field_errors": []}}`.
Their state [`member_state_200`]: note `completed: true` even if the founder is still onboarding,
and `startup` has only `id` and `name`:
```
{"data": {"step": 4, "completed": true, "assessment_pending": false, "pending_invite": false, "founder_profile": null,
  "startup": {"id": "<uuid>", "name": "WalkCo"}, "goals": [], "ai_panel": null, "notes": null, "invites": []}, "meta": null}
```
**Role title:** an account now starts with `role_title: ""`; "Founder & CEO" is set only when a founder's draft is created. An invited
mentor's `/auth/me` shows `"role_title": ""` [`member_me_200`]: render nothing, not "Founder & CEO". Accounts created before this
change keep whatever title they already had.

### `PATCH /onboarding/state` validation (gaps 6, 18 BE) — all `422 VALIDATION_ERROR`
Shape [`patch_website_javascript_422`]: `{"error": {"code": "VALIDATION_ERROR", "message": "Please check the highlighted fields.",
"field_errors": [{"field": "website", "message": "Enter a web address starting with http or https."}]}}`. Captured field messages:

| Input | `field` / `message` | Capture |
|---|---|---|
| `website: "javascript:alert(1)"` | `website` / "Enter a web address starting with http or https." | `patch_website_javascript_422` |
| `phone: "abc"` | `phone` / "Enter a valid phone number." | `patch_phone_abc_422` |
| `goals: ["<script>x</script>"]` | `goals` / "Pick goals from the list." | `patch_goals_script_422` |
| `name: ""` | `name` / "This field can't be blank." | `patch_empty_name_422` |
| `full_name: "   "` | `full_name` / "This field can't be blank." | `patch_empty_full_name_422` |

Rules (the other limits are unit-tested, not captured): `website` http/https only, a bare `walkco.example` is stored as
`https://walkco.example` [`patch_website_bare_domain_200`, the response shows `"website": "https://walkco.example"`], blank clears it, no
credentials in the URL, 255 max. `phone`: 7 to 15 digits, optional leading `+`, spaces, `()-.` allowed; blank clears it (stored as typed).
`country`: two letters, upper-cased (`"ng"` is stored `"NG"` [`patch_step1_200`]). `full_name`, `name`, `industry`: trimmed, 1 to 120 / 120 / 80
characters. `role_title` 1 to 80. `how_heard` 100, `description` 1000, `notes` 2000. **Goals are the fixed list, up to 3, each once, stored as
these exact labels:** Validate my idea · Build my MVP · Get first customers · Grow revenue · Raise funding · Get legally set up · Get
financially organized · Build my team. Any other string, including the old free-text goals, is rejected: update the chips.

### Invites: a result per row (gap 7) [`invites_results_200`]
```
POST /api/v1/onboarding/invites
{"invites": [{"email": "nope", "role": "team_member"}, {"email": "<mate>", "role": "mentor"}, {"email": "<mate>", "role": "mentor"}, {"email": "<founder's own email>", "role": "team_member"}]}
→ 200   {"data": {"created": ["<mate>"], "skipped": ["<mate>", "<founder's own email>"], "undelivered": [],
  "results": [
    {"email": "nope", "status": "invalid", "reason": "invalid_email", "message": "That doesn't look like an email address."},
    {"email": "<mate>", "status": "created", "reason": null, "message": null},
    {"email": "<mate>", "status": "skipped", "reason": "duplicate_in_request", "message": "You listed this address more than once."},
    {"email": "<founder's own email>", "status": "skipped", "reason": "self_invite", "message": "That's your own email address."}]}, "meta": null}
```
`results` has one entry per submitted row, in order. `status` is `created`, `skipped` or `invalid`; `message` is the line to show beside the row.
Other reasons, unit-tested only: `already_invited`, `already_member`. The `investor` role is still accepted by this endpoint (unchanged; the wizard hides it per D-AUTH-4). An invalid address is never stored or emailed. `created`/`skipped`/
`undelivered` stay for older callers (invalid rows appear only in `results`). `POST /team/invitations` returns the same `results`.
**FE:** the request no longer fails as a whole for one bad address: render the failed rows and let the user fix them before Continue.

---

## 7. Emails (gaps 21, 22)

All captured from the file mail backend (`e2e/_captures/auth_onboarding_gaps/email_*.json`; the invite token in the link is replaced by `<invite-token>`).
- **Invitation** [`email_invitation`]: subject `Ada Founder invited you to join WalkCo on Cofoundaz` (falls back to `A founder invited you to join a startup on Cofoundaz`); button `#0F6AFC`, headline navy `#051736`; body "Ada Founder invited you to join WalkCo as Mentor." Link `…/invite/<token>`.
- **Welcome** [`email_welcome`]: sent once when `POST /onboarding/complete` first succeeds (not on a repeat call; not if completion is refused with 422). Subject `Welcome to Cofoundaz — let's build WalkCo`; button "Open your dashboard" → `{APP_BASE_URL}/dashboard`. A mail failure never fails the completion.
- **Password changed** [`email_password_changed`]: after a reset (and after a change from Settings). Subject `Your Cofoundaz password was changed`; button "This wasn't me: reset my password" → `{APP_BASE_URL}/forgot-password`. The change-from-Settings send is unit-tested, not captured.
- Verification and reset emails keep their subjects, now in the brand colours.
- **In-app "workspace is ready"** (gap 21's second half): finishing onboarding now posts a notification of `type: "notification.onboarding_complete"`, title "Your workspace is ready", to the founder only. It appears in `GET /notifications` [`notifications_after_complete_200`]:
  `{"type": "notification.onboarding_complete", "title": "Your workspace is ready", "body": "Your roadmap and Health Score are being set up. Take the Kickoff Assessment to sharpen them."}` (the first of the notifications in that capture, newest first; the second is the unrelated `workspace.member.joined`). Deep link: send the founder to the Kickoff Assessment.

---

## 8. UX consequences the FE must surface

1. **Locked screen:** show the API's message (minutes) with a "reset your password" link; a reset genuinely unlocks.
2. **A 429 is never "wrong password".** `RATE_LIMITED` and `ACCOUNT_LOCKED` mean wait; show the wait, do not clear the form silently.
3. **Verify page countdown** comes from `retry_after`; never claim a link was sent when `sent` is `false`.
4. **2FA code screen:** a typo no longer strands the person; after the 5th wrong code the next screen is `/login` with "Your sign-in timed out. Log in again."
5. **Invites step:** show per-row failures; do not assume `created` equals what was typed.
6. **Invitations:** show a pending invitation with Accept and Decline (`POST /invitations/decline`), but never block the wizard on it; a member (accepted) must not see the wizard. Empty `role_title` is normal for invited people.
7. **Goals are now eight fixed chips;** old free-text goals will be rejected with "Pick goals from the list."
8. **Sign-up must send `accepted_terms`,** or sign-up fails with `TERMS_NOT_ACCEPTED` (see the deploy-ordering note in section 3).
9. **Cookie mode:** `X-Auth-Mode: cookie` and `credentials: "include"` on `/auth/*`; access token in memory only; one shared refresh in flight.

---

## 9. Verification table

| Behaviour | Verified |
|---|---|
| Lockout per (email, IP); unknown email gets the identical 429; 5th failure trips it; other IP not locked; right password refused while locked | live (`lockout_*`) |
| `retry_after_seconds` in body and `Retry-After` header; message with minutes | live |
| Password reset lifts the lock, next login works from the locked IP | live (`reset_while_locked_200`, `login_after_reset_from_locked_ip_200`) |
| Per-email ceiling over many IPs; suspended account counts as failure | unit tests only |
| Per-IP limits on sign-up, forgot, login: 429 `RATE_LIMITED` + `retry_after_seconds` | live (strict server, limits lowered to 12 / 6 / 20) |
| Verify-resend per-IP limit; per-email hourly caps (forgot, resend) silent | unit tests only |
| Proxy IP handling (nginx overwrites `X-Forwarded-For`, gunicorn trusts it) so per-IP limits see the real client | **not verified on staging**; the e2e run sets `X-Forwarded-For` from `127.0.0.1`, which uvicorn trusts. Confirm on staging that two real clients are counted separately (SOP) |
| Sign-up consent: missing/false 422, true 201 | live (strict server) |
| `terms_accepted_at` / `terms_version` stored in the database | unit test only (no endpoint returns them) |
| Resend `{sent, retry_after}` for real, repeated and unknown addresses | live |
| MFA ticket: typo keeps it, 5th wrong burns it, burned/used/unknown give `MFA_TICKET_EXPIRED`, `attempts_remaining` | live |
| MFA per-account cap across tickets (`ACCOUNT_LOCKED` on the challenge), atomic cap and single-use claim under concurrency | unit tests only |
| Atomic attempt reservation (a parallel burst gets exactly the limit); known addresses exempt from the email-wide ceiling; IPv6 /64 keys; mail caps per (email, IP) and overall; counters that lost their expiry repair themselves; backup code spent once; bcrypt on unknown emails | unit tests only (the burst test uses 16 threads against Redis) |
| Refresh-token reuse (after the grace window) revokes the family and the revocation survives the failed request | live (`refresh_reuse_401`, `refresh_newest_after_reuse_401`, `grace_refresh_replay_after_window_401`, `grace_refresh_newest_after_reuse_401`) and unit-tested including the rollback |
| Previous refresh token accepted inside the grace window (lost response, second tab), new pair returned, family intact | live (`grace_refresh_replay_in_window_200`, `grace_refresh_other_tab_200`; the e2e server runs with `REFRESH_GRACE_SECONDS=5`, production default is 20) |
| Two simultaneous refreshes with a never-used token both succeed and leave one live token; two simultaneous replays of one old token let one win and revoke the family; replays past the window both fail and revoke; a token two rotations back gets no grace | unit only (`tests/services/auth/test_sessions.py`, `test_sessions_concurrency.py`, real committing connections) |
| Decoy body token cannot rotate the cookie or force a logout | live (`refresh_cookie_decoy_body_401`, `logout_cookie_decoy_body_200`) |
| Pending invitee can still write; decline invitation (200, again 400, wrong account 403); state after decline; member writes 409 | live |
| Cookie-mode login, silent refresh, rotation, logout revokes | live |
| CSRF: no header 403, foreign `Origin` 403, header-less body token OK | live (no header, foreign origin, logout no header); allowed-`Origin` pass-through is unit-tested |
| Cookie `Secure` flag | unit test only (the live server is HTTP with `Secure` off) |
| Google exchange honours cookie mode (`refresh_token: null`, `expires_in`, cookie set) | unit test only (the live journey uses the stubbed Google in a separate file, not in cookie mode) |
| Header-auth clients still get `refresh_token` in the body | live (`login_legacy_200`, `refresh_body_token_200`) |
| Access token valid after logout (gap 25) | live |
| Onboarding input validation: website, phone, goals, empty names | live; the remaining limits and the website/phone positive cases are unit-tested |
| Invite per-row results | live for `invalid`, `created`, `duplicate_in_request`, `self_invite`; `already_invited`, `already_member` unit-tested |
| No draft startup created by reading for a pending invitee / member | live (`invitee_state_pending_invite_200`, `member_state_200`) |
| `role_title` empty for invited people, "Founder & CEO" for founders | live (`member_me_200`, `onboarding_state_200`) |
| Welcome email once, invite subject and brand colours, password-changed email | live (reset path); change-from-Settings send unit-tested |
| In-app `notification.onboarding_complete` | live (asserted in the journey's feed read) and unit-tested |
| Real email delivery through the provider | **not verified** (file backend only) |
