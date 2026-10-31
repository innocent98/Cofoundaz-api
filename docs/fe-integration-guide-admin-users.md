# FE integration guide: Admin slice B (Users, Workspaces, Dashboard KPIs)

Every body below is pasted from a live run against a real server: `e2e/test_admin_users.py`, captures in `e2e/_captures/admin_users/<name>.json`. Emails and IDs are throwaway values from that run (placeholders). Base path `/api/v1/admin`. Success: `{"data": ..., "meta": ...}`; errors: `{"error": {"code","message","field_errors"}}`, **except the hidden 404 `{"detail":"Not Found"}`** (see the staff guide, `fe-integration-guide-admin-staff.md`, which also covers auth, step-up and the audit explorer). Accepted calls this follows: P1 (a fresh code on every staff mutation), P2, P3, P11, P12, P13 of `cofoundaz/docs/checklist/admin-prd-gaps.md`.

## Who can call what

| Route | Needs |
|---|---|
| `GET /users`, `GET /users/{id}`, the four `POST /users/{id}/...` actions, `GET /workspaces`, `GET /workspaces/{id}` | active staff with scope `support` (a Super Admin passes every scope). Staff without it: 403 `FORBIDDEN` (unit-tested, not captured live) |
| `GET /dashboard/kpis` | any active staff |
| Everyone else (signed out, ordinary founders, revoked staff) | `{"detail":"Not Found"}` (`users_list_non_staff_404`) |

Search is **per page** (P13): the top-bar global search is removed; Users and Workspaces each have their own search box.

## Users

### `GET /users`

Query (all optional): `query` (max 120; matches email, name, workspace name, or an exact user id or workspace id; `%` and `_` are literal), `billing_status` (`free`, `trialing`, `active`, `past_due`, `canceled`), `activity` (`active` = signed in within 30 days, `dormant`), `limit` (1 to 100, default 25), `cursor`. Newest account first. `users_search`:

```json
{"data":{"users":[{"id":"8a15664e-7be0-4fda-b3c9-ac58cb98049f","email":"delivered+e2e-d9aefb27dbf1@resend.dev","full_name":"Ada Obi","status":"active","email_verified":true,"created_at":"2026-10-31T13:37:39.610949+00:00","last_login_at":"2026-10-31T13:37:39.891539+00:00","activity":"active","workspace_count":1,"primary_workspace":{"workspace_id":"39d2aada-9f02-495f-bf3a-5f72a65da00f","workspace_name":"NovaPay","role":"founder","plan_key":"starter","billing_status":"free"}}]},"meta":{"next_cursor":null,"total_estimate":null}}
```

**Nesting traps**
- Rows are in **`data.users`**; the paging cursor is in **`meta.next_cursor`** (`users_page1` has a string, the last page `null`).
- `primary_workspace` is `null` for someone with no workspace (a signed-up, not-yet-onboarded account: `users_page1`, first row, `"workspace_count":0,"primary_workspace":null`). It is the user's own founder workspace if they have one, else their oldest membership; `workspace_count` says how many more.
- `last_login_at` and `full_name` are `null` (present, not absent) until set. `activity` is `"dormant"` when `last_login_at` is null.
- `status` values: `pending_verification`, `active`, `locked`, `disabled`. **`disabled` means suspended.** The mock's billing and activity columns: `primary_workspace.billing_status` and `activity`.
- A bad filter is a 422 whose field is prefixed `query.` (`users_bad_filter`: `{"field":"query.billing_status","message":"Input should be 'free', 'trialing', 'active', 'past_due' or 'canceled'"}`).
- Searching by workspace works: `users_search_by_workspace` (`query=NovaPay&billing_status=free`).

### `GET /users/{id}`

`user_profile` (the full object; `sessions` shows the 10 most recent):

```json
{"data":{"id":"8a15664e-7be0-4fda-b3c9-ac58cb98049f","email":"delivered+e2e-d9aefb27dbf1@resend.dev","full_name":"Ada Obi","role_title":"Founder & CEO","country":null,"phone":null,"status":"active","email_verified":true,"two_factor":false,"created_at":"2026-10-31T13:37:39.610949+00:00","last_login_at":"2026-10-31T13:37:39.891539+00:00","activity":"active","is_staff":false,"workspaces":[{"workspace_id":"39d2aada-9f02-495f-bf3a-5f72a65da00f","name":"NovaPay","role":"founder","membership_status":"active","plan_key":"starter","billing_status":"free","trial_ends_at":null,"current_period_end":null,"cancel_at_period_end":false}],"sessions":[{"id":"09c6289e-fb90-43be-ada1-bc4f044adbbd","ip":"127.0.0.1","user_agent":"python-httpx/0.28.1","signed_in_at":"2026-10-31T13:37:39.892640+00:00","expires_at":"2026-11-30T13:37:40.133703+00:00","revoked_at":null}],"can":{"suspend":true,"unsuspend":false,"reset_password":true,"resend_verification":false}},"meta":null}
```

- **`can` decides the buttons**: enable Suspend, Unsuspend, Send password reset, Resend verification from these four booleans, never from your own logic. They are all `false` for your own account and for staff accounts you may not act on (below).
- `two_factor` is only whether an authenticator app is on; no secret is ever returned. `is_staff` is true for staff accounts.
- Unknown id: 404 `USER_NOT_FOUND`, message "No such user." (`user_profile_unknown`).
- **Staff targets** (`user_profile_staff_as_admin`, an Admin looking at a Super Admin): `"sessions":[],"phone":null,"is_staff":true` and every `can` flag `false`. An Admin never sees a staff member's sign-in places or phone; a Super Admin does.
- No documents, journal or AI chat data is anywhere in these responses, by design (D-ADM-9).

### The four actions: shared rules

`POST /users/{id}/suspend`, `/unsuspend`, `/reset-password`, `/resend-verification`. Body for all four:

```json
{"reason":"Abuse report 17 confirmed","totp_code":"123456"}
```

- `reason`: 10 to 500 characters after trimming. `totp_code`: the **signed-in staff member's own** 6-digit authenticator code, fresh for every action, single use (rules and UX in the staff guide). Extra fields are rejected.
- Validation first: `suspend_reason_too_short` (422, `{"field":"reason","message":"String should have at least 10 characters"}`). Wrong or reused code: 403 `STEP_UP_INVALID` (`suspend_wrong_code`, message "That authenticator code isn't right, or was already used. Enter a new one."). Do **not** sign the user out on it.
- Every action writes an audit row (`user.suspended`, `user.unsuspended`, `user.password_reset_sent`, `user.verification_resent`) with your reason, visible in the Super Admin audit explorer (`audit_user_actions`).
- A failed business check (the 409s below) still spends the code: tell the staff member to wait for the next one.
- Staff targets: an Admin gets 403 `STAFF_TARGET` ("Only a Super Admin can act on a staff account.", `suspend_staff_as_admin`); nobody can act on themselves, 422 `CANNOT_ACT_ON_SELF` ("You can't do that to your own account.", `suspend_self`).

### Suspend

`suspend_ok` (200): `{"data":{"id":"8a15664e-7be0-4fda-b3c9-ac58cb98049f","status":"disabled","sessions_revoked":1},"meta":null}`

Confirm copy from the PRD: **"Suspend {email}? They're signed out everywhere immediately."** What really happens, all verified live:
- The user's **existing access token fails on the very next call** with 401 `UNAUTHORIZED` "Not authenticated." (`suspended_user_old_token_401`). Their app will bounce to sign-in; that is expected.
- Their sign-in is refused with the same message as a wrong password: 401 `INVALID_CREDENTIALS` "That email and password don't match." (`suspended_user_login_401`).
- `sessions_revoked` counts the refresh sessions ended. Pending password-reset and verification links are killed too.
- Already suspended: 409 `ALREADY_SUSPENDED` "That account is already suspended." (`suspend_already_suspended`). Their profile then shows `"status":"disabled"` and `can.unsuspend: true` (`user_profile_suspended`).

### Unsuspend

`unsuspend_ok` (200): `{"data":{"id":"8a15664e-7be0-4fda-b3c9-ac58cb98049f","status":"active"},"meta":null}`. Someone who never verified their email comes back as `"status":"pending_verification"` (unit-tested, not captured live), so unsuspending cannot skip verification. Not suspended: 409 `NOT_SUSPENDED` "That account isn't suspended." (unit-tested only).

### Send password reset

`reset_password_ok` (200): `{"data":{"id":"8a15664e-7be0-4fda-b3c9-ac58cb98049f","sent":true},"meta":null}`. Sends the normal reset email to the user; staff never see or set a password. One email arrived in the run (counted in the file mailbox). Rules:
- Only `active` or `locked` accounts: otherwise 409 `RESET_NOT_ALLOWED` "A password reset can only be sent to an active account." (`reset_password_suspended`, for a suspended user).
- A second send within 60 seconds: 429 `TOO_SOON` "An email was just sent to that person. Wait a minute." (`reset_password_too_soon`). Disable the button for a minute after success.
- If the mail provider refuses: 503 `EMAIL_DELIVERY_FAILED` (standard envelope; **not exercised live**, unit-tested). Nothing is sent and the 60-second wait is released.

### Resend verification

`resend_verification_ok` (200): `{"data":{"id":"8151f29b-a795-4b12-81a5-7df58e2495c6","sent":true},"meta":null}` (email counted in the mailbox). Only for `pending_verification`: otherwise 409 `ALREADY_VERIFIED` "That account has already verified its email." (`resend_verification_already_verified`). Same 60-second `TOO_SOON` and 503 behaviour as above.

## Workspaces (read-only)

### `GET /workspaces`

Query (optional): `query` (name, owner email, or exact workspace id), `stage` (`idea`, `validation`, `build`, `launch`, `growth`, `scale`), `billing_status` (as above), `limit` (1 to 100, default 25), `cursor`. Newest first. `workspaces_search`:

```json
{"data":{"workspaces":[{"id":"39d2aada-9f02-495f-bf3a-5f72a65da00f","name":"NovaPay","owner_email":"delivered+e2e-d9aefb27dbf1@resend.dev","owner_name":"Ada Obi","stage":"idea","country":null,"created_at":"2026-10-31T13:37:40.142587+00:00","seats":1,"members":1,"plan_key":"starter","billing_status":"free","health_score":null,"health_band":null,"last_active_at":"2026-10-31T13:38:00.076654+00:00"}]},"meta":{"next_cursor":null,"total_estimate":null}}
```

- Rows in `data.workspaces`, cursor in `meta.next_cursor`. `seats` counts founders and team members (paid seats); `members` counts everyone active (mentors and others are free).
- `health_score` and `health_band` are `null` until an assessment exists. `last_active_at` is the latest sign-in among active members (`null` if none). `stage` and `country` can be `null`. Search for a bad `stage` value is 422.

### `GET /workspaces/{id}`

`workspace_detail`:

```json
{"data":{"id":"39d2aada-9f02-495f-bf3a-5f72a65da00f","name":"NovaPay","industry":"Fintech","stage":"idea","country":null,"created_at":"2026-10-31T13:37:40.142587+00:00","owner_email":"delivered+e2e-d9aefb27dbf1@resend.dev","onboarding_completed_at":"2026-10-31T13:37:40.212583+00:00","health_score":null,"health_band":null,"members":[{"user_id":"8a15664e-7be0-4fda-b3c9-ac58cb98049f","email":"delivered+e2e-d9aefb27dbf1@resend.dev","full_name":"Ada Obi","role":"founder","status":"active","joined_at":"2026-10-31T13:37:40.154264+00:00"}],"subscription":{"plan_key":"starter","plan_name":"Starter","interval":"monthly","status":"free","trial_ends_at":null,"current_period_end":null,"cancel_at_period_end":false,"grace_ends_at":null},"usage":{"ai_credits_30d":0,"ai_credits_limit":500,"seats_used":1,"seats_limit":1,"storage_mb":0.0,"storage_limit_mb":5120},"integrations":[]},"meta":null}
```

- A workspace with no subscription row reads as `starter` / `free`. `seats_limit` is `null` for unlimited plans. `integrations` lists connected payment or bank providers (`[{"provider":"paystack","status":"active"}]`, unit-tested; empty in the live run), never keys.
- Unknown or deleted workspace: 404 `WORKSPACE_NOT_FOUND` "No such workspace." (`workspace_unknown`).
- Row links in the Users table go to `GET /users/{id}` (use `members[].user_id`).

## Dashboard KPIs: `GET /dashboard/kpis`

`dashboard_kpis` (series trimmed to its last two days; the real list has 30):

```json
{"data":{"window_days":30,"generated_at":"2026-10-31T13:41:00.227010+00:00","total_workspaces":{"value":1,"new_in_window":1,"new_in_previous_window":0},"activation_rate":{"percent":100.0,"previous_percent":null,"cohort":1,"activated":1},"paid_conversion":{"percent":0.0,"paying_workspaces":0,"previous_percent":null},"mrr":{"minor":0,"currency":"NGN","previous_minor":null},"signups_series":[{"date":"2026-10-30","signups":0,"activations":0},{"date":"2026-10-31","signups":4,"activations":1}]},"meta":null}
```

Definitions (P3, shown to staff as tooltips):
- **Total workspaces**: all non-deleted workspaces. The "+218 this month" figure is `new_in_window` (compare `new_in_previous_window` for the trend).
- **Activation rate**: of workspaces created in the last 30 days, the share that finished onboarding within 14 days of creation. Young cohorts read low until their 14 days pass. `previous_percent` is the same figure for the 30 days before; **`null` when that cohort was empty** (as in the capture), so show no delta.
- **Paid conversion**: workspaces on an active paid plan over all workspaces. **`previous_percent` is always `null` for now** (no history is stored): do not render a delta.
- **MRR**: `minor` is kobo, `NGN`; active paid plans only, annual priced at one twelfth. Divide by 100 for naira. **`previous_minor` is always `null` for now**: no delta.
- `signups_series` is 30 daily points, oldest first, ending today; `signups` counts new accounts and `activations` counts workspaces that finished onboarding that day (UTC dates).

## UX the FE must surface

1. Every action opens a dialog with the reason (min 10 characters, show a counter) and the staff member's authenticator code; the PRD's suspend confirm copy is above.
2. After a successful action, a second one within the same 30-second code window fails with `STEP_UP_INVALID`: tell them to wait for the next code.
3. Suspend ends the user's session on their very next request and blocks sign-in until someone unsuspends. Say so in the confirm.
4. Password reset and verification emails go to the user, not the staff member; disable the button for 60 seconds after a send.
5. Never show or ask for a password. Never show workspace content: only the fields this API returns.
6. Treat `meta.next_cursor` as the only way to page; do not compute offsets.

## Verification table

| Behaviour | Status |
|---|---|
| Non-staff 404 on users list | verified live (`users_list_non_staff_404`) |
| Search by email, by workspace name, `billing_status`, paging with a cursor, bad filter 422 | verified live |
| Profile, unknown user 404, staff target redaction and `can` flags | verified live |
| Suspend: ok, old token 401 next call, login 401, profile shows `disabled`, already suspended 409 | verified live |
| Unsuspend ok | verified live; restoring `pending_verification`, `NOT_SUSPENDED` unit-tested only |
| Reset password ok (real email counted), too soon 429, suspended refused 409 | verified live; `EMAIL_DELIVERY_FAILED` 503 unit-tested only |
| Resend verification ok (real email counted), already verified 409 | verified live |
| Step-up wrong code 403, reason too short 422, staff target 403, self 422 | verified live |
| Workspaces list, detail, unknown 404 | verified live; `integrations` with a connection, deleted workspace hidden unit-tested only |
| KPI shape and zero or null cases | verified live; non-zero MRR, paid conversion, activation windows unit-tested only |
| Scope `support` required (403 for a staff member with only another scope) | unit-tested only |
| Suspended user blocked at MFA challenge, refresh and password reset | unit-tested only |
| Audit rows for each action | verified live (`audit_user_actions`) |
