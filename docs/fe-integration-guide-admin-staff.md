# FE integration guide: Admin slice A (staff access, staff management, audit explorer)

Every body below is pasted from a live run against a real server: `e2e/test_admin_staff.py`, captures in `e2e/_captures/admin_staff/<name>.json`. Emails and IDs are throwaway values from that run (placeholders, not real people). Base path `/api/v1`. Success bodies are `{"data": ..., "meta": ...}`; errors are `{"error": {"code", "message", "field_errors"}}`, **except the hidden 404 (below), which is `{"detail": "Not Found"}`**.

## What this is, in FE terms

Two staff consoles (Admin, Super Admin) will live in the main Next app. This slice gives them: "who am I as staff", staff management for Super Admins, and the audit explorer. Access is decided **only by the API on every request**; hiding a page is not access control. Staff keep their normal founder login; the same access token works. There is no role in the token.

A person is staff only when **all** hold: an active `platform_staff` row, verified email, active account, and authenticator-app 2FA turned on. Turn 2FA off, or get revoked, and the very next staff call returns 404.

## Hidden 404 (the most important thing to handle)

Anyone who is not staff (signed out, an ordinary founder, revoked staff, staff with 2FA off, an Admin calling a `/super-admin` route) gets exactly what a nonexistent route returns. Capture `staff_list_plain_user_404`, `staff_list_anonymous_404`, `staff_list_as_admin_404`, `admin_me_after_revoke_404`, `unknown_route_404` are all:

```json
{"detail": "Not Found"}
```

- It is **not** the error envelope: do not read `error.code`. Branch on status 404.
- Use `GET /admin/me` as the "is this person staff" probe when entering a console: 200 = staff, 404 = send them to the normal app. Never show "you are not allowed" (that would reveal the console).
- Also hidden as 404: a wrong HTTP method on these paths and malformed JSON. These routes are not in the OpenAPI docs.
- An active Admin who lacks a **scope** gets 403 `FORBIDDEN`. No scoped routes exist yet (scoped routes arrive with later slices), so this was **not exercised live**; shape is the standard `FORBIDDEN` envelope.

## Endpoints

All need `Authorization: Bearer <access token>`.

### `GET /admin/me` (any active staff)

```json
{"data":{"user_id":"e5c34d48-9d41-4a01-ba44-34b34ce128bc","email":"delivered+e2e-ff8c41fb21a4@resend.dev","role":"super_admin","scopes":[]},"meta":null}
```

An Admin (`admin_me_admin`): `"role":"admin","scopes":["moderation","support"]`. Scopes are sorted. A Super Admin has `scopes: []` and passes every scope check; do not treat an empty list as "no access" when `role` is `super_admin`.

### `GET /super-admin/staff` (Super Admin)

Query: `include_revoked` (default `true`). `staff_list_two`:

```json
{"data":[{"id":"30395551-4498-49c2-aab3-e8169fe4d209","user_id":"e5c34d48-9d41-4a01-ba44-34b34ce128bc","email":"delivered+e2e-ff8c41fb21a4@resend.dev","full_name":null,"role":"super_admin","scopes":[],"invited_by_email":null,"created_at":"2026-10-31T12:39:41.595969+00:00","revoked_at":null},{"id":"00a50a9f-83b5-4789-9758-f5dd4560e20c","user_id":"9c36fdb0-5176-4697-8835-234bcbb4afc1","email":"delivered+e2e-a5de604fd277@resend.dev","full_name":null,"role":"admin","scopes":["content"],"invited_by_email":"delivered+e2e-ff8c41fb21a4@resend.dev","created_at":"2026-10-31T12:39:42.648082+00:00","revoked_at":null}],"meta":null}
```

- `id` is the staff row (use it for revoke); `user_id` is the account. Oldest first. A revoked row has `revoked_at` set (ISO string), active has `null`.
- `full_name` is `null` when the person never set one; `invited_by_email` is `null` for the first Super Admin.
- **Slice B added `last_login_at`** to every staff row (ISO string, or `null` if they never signed in): the console's "Last access" column. Live example: `e2e/_captures/admin_users/staff_list_with_last_login.json`; the captured rows in this guide predate the field.

### `POST /super-admin/staff` (Super Admin, step-up): invite or reinstate

Request:

```json
{"email":"person@example.com","role":"admin","scopes":["support","moderation"],"reason":"Onboarding a support lead","totp_code":"123456"}
```

- `role`: `admin` or `super_admin`. `scopes` items: `support`, `content`, `moderation`. Admin needs at least one; Super Admin must send none. Duplicates are merged.
- `reason`: 10 to 500 characters after trimming. `totp_code`: exactly 6 digits, the **inviting Super Admin's own** authenticator code (see Step-up). Extra fields are rejected.
- The person must already have an account, verified, with 2FA on.
- Success is **201**: `invite_ok` is the row shape above (`"scopes":["moderation","support"]`). Inviting someone previously revoked **reinstates the same row** (same `id`, new role/scopes, `revoked_at: null`), also 201 (`invite_reinstated`); the audit action differs (`staff.reinstated`).

Errors (each captured):

| Capture | Status | Body |
|---|---|---|
| `invite_target_without_mfa` | 422 | `{"error":{"code":"STAFF_MFA_REQUIRED","message":"Staff must have an authenticator app turned on. Ask them to enable it first.","field_errors":[]}}` |
| `invite_unknown_email` | 404 | `{"error":{"code":"USER_NOT_FOUND","message":"No account with that email. They need to sign up first.","field_errors":[]}}` |
| `invite_already_staff` | 409 | `{"error":{"code":"ALREADY_STAFF","message":"That person is already staff.","field_errors":[]}}` |
| `invite_wrong_code` / `invite_replayed_code` | 403 | `{"error":{"code":"STEP_UP_INVALID","message":"That authenticator code isn't right, or was already used. Enter a new one.","field_errors":[]}}` |
| `invite_reason_too_short` | 422 | `{"error":{"code":"VALIDATION_ERROR","message":"Please check the highlighted fields.","field_errors":[{"field":"reason","message":"String should have at least 10 characters"}]}}` |
| `invite_missing_code` | 422 | same envelope, `field_errors: [{"field":"totp_code","message":"Field required"}]` |
| `invite_admin_without_scopes` | 422 | same envelope, `field_errors: [{"field":"","message":"Value error, An Admin needs at least one scope."}]` (note the **empty `field`**: show it as a form-level message) |

Also possible, **not captured live** (shapes follow the same envelope; behaviour proven in unit tests only): 422 `STAFF_ACCOUNT_NOT_ELIGIBLE` (account unverified or not active), 429 `TOO_MANY_ATTEMPTS` (see Step-up).

### `POST /super-admin/staff/{staff_id}/revoke` (Super Admin, step-up)

Request: `{"reason":"Moved to another team","totp_code":"123456"}`. Success 200, `revoke_ok`: the row with `revoked_at` set (`"revoked_at":"2026-10-31T12:42:00.094231+00:00"`). The revoked person's very next staff call is a 404 (`admin_me_after_revoke_404`), same token.

| Capture | Status | Body |
|---|---|---|
| `revoke_self` | 422 | `{"error":{"code":"CANNOT_REVOKE_SELF","message":"You can't revoke your own access.","field_errors":[]}}` |
| `revoke_unknown` | 404 | `{"error":{"code":"STAFF_NOT_FOUND","message":"No such staff member.","field_errors":[]}}` |
| `revoke_already_revoked` | 409 | `{"error":{"code":"ALREADY_REVOKED","message":"That person's staff access is already revoked.","field_errors":[]}}` |
| `revoke_reason_too_short` | 422 | validation envelope on `reason` |

There is no "last Super Admin" error: you cannot revoke yourself, so at least one Super Admin always remains. Hide the revoke button on your own row.

### `PATCH /super-admin/staff/{staff_id}` (Super Admin, step-up): change role or scopes

Added in slice B (gap P9); its captures are in `e2e/_captures/admin_users/`. Body is the invite body without `email`: `{"role":"admin","scopes":["support","content"],"reason":"Adding the content scope","totp_code":"123456"}` (same rules: Admin needs at least one scope, Super Admin none, reason 10+, fresh code). Success 200 returns the staff row (`staff_patch_ok`): `"scopes":["content","support"]`, with `last_login_at` (see the list note). The person's access changes on their next request (`admin_me_after_patch`: `"scopes":["content","support"]`).

| Capture | Status | Body |
|---|---|---|
| `staff_patch_self` | 422 | `{"error":{"code":"CANNOT_EDIT_SELF","message":"You can't change your own role or scopes.","field_errors":[]}}` |
| `staff_patch_no_change` | 422 | `{"error":{"code":"NO_CHANGE","message":"That is already their role and scopes.","field_errors":[]}}` |
| `staff_patch_admin_without_scopes` | 422 | validation envelope, `field_errors: [{"field":"","message":"Value error, An Admin needs at least one scope."}]` |
| `staff_patch_wrong_code` | 403 | `STEP_UP_INVALID` (as for invite) |
| `staff_patch_as_admin_404` | 404 | `{"detail":"Not Found"}` (an Admin cannot reach it) |

A revoked staff row: 409 `ALREADY_REVOKED` (reinstate by inviting again); unknown id 404 `STAFF_NOT_FOUND` (unit-tested for PATCH; captured live for revoke). The audit action is `staff.updated` with `details.from` and `details.to` (`audit_staff_updated`).

### `GET /super-admin/audit` (Super Admin)

Query (all optional): `actor_id` (uuid), `action`, `workspace_id` (uuid), `date_from`, `date_to` (ISO datetimes; encode `+` as `%2B`), `limit` (1 to 100, default 50), `cursor`. Newest first. `action` is an exact match, **or a prefix when it ends with a dot** (`staff.` matches `staff.invited`, `staff.revoked`, ...). `audit_filter_action`:

```json
{"data":{"entries":[{"id":"dbae4e87-5cbe-49d8-aeec-fb1ee49f7200","created_at":"2026-10-31T12:42:00.079587+00:00","action":"staff.revoked","actor_user_id":"e5c34d48-9d41-4a01-ba44-34b34ce128bc","actor_email":"delivered+e2e-ff8c41fb21a4@resend.dev","workspace_id":null,"entity_type":"platform_staff","entity_id":"00a50a9f-83b5-4789-9758-f5dd4560e20c","reason":"Moved to another team","details":{"role":"admin","email":"delivered+e2e-a5de604fd277@resend.dev","scopes":["moderation","support"]},"ip":"127.0.0.1"}]},"meta":{"next_cursor":null,"total_estimate":null}}
```

**Nesting traps**
- The paging cursor is in **`meta.next_cursor`**, not inside `data` (`audit_page1` has a string; `audit_page2` ends with `null`). Entries are in `data.entries`.
- Rows with no human actor (the first-Super-Admin command) have `actor_user_id: null` **and** `actor_email: null` (`audit_page2`, action `staff.bootstrapped`, `ip: null`).
- `reason`, `details`, `workspace_id`, `entity_id` can each be `null` (present, not absent). Older rows written before this slice have `reason: null` and `details: null`. `details` is free-form per action: for `staff.*` it holds `email`, `role`, `scopes`; for `audit.exported` it holds `rows` and `filters`.
- `limit` above 100 returns 422 (not captured live).

Bad cursor, `audit_bad_cursor` (422): `{"error":{"code":"VALIDATION_ERROR","message":"Invalid pagination cursor.","field_errors":[{"field":"cursor","message":"This page link is no longer valid."}]}}`.

### `GET /super-admin/audit/export` (Super Admin)

Same filters as the list (no `limit`/`cursor`) plus a **required `reason`** query parameter (10 to 500 characters). Returns a CSV download, max 5000 rows, newest first. Captured as `audit_export_csv`: status 200, `content-type: text/csv; charset=utf-8`, `content-disposition: attachment; filename="audit-log.csv"`, body (CRLF line endings):

```
when,actor,action,workspace_id,entity_type,entity_id,reason
2026-10-31T12:43:00.377277+00:00,delivered+e2e-ff8c41fb21a4@resend.dev,staff.reinstated,,platform_staff,00a50a9f-83b5-4789-9758-f5dd4560e20c,Back on the content team
```

Cells that could run as spreadsheet formulas get a leading `'` (unit-tested, not part of this capture). Download it with an authenticated `fetch` and a Blob, not an `<a href>` (the token is a header). Missing reason, `audit_export_no_reason` (422): `{"error":{"code":"VALIDATION_ERROR","message":"Please check the highlighted fields.","field_errors":[{"field":"query.reason","message":"Field required"}]}}` (note the **`query.` prefix** here, versus bare names for body fields).

Every export writes its own audit row (`audit_export_is_audited`: action `audit.exported`, `reason` as sent, `details: {"rows":4,"filters":{...}}`).

## Step-up (fresh authenticator code on every mutation)

Invite and revoke need the signed-in Super Admin's own `totp_code`, typed fresh for **each** action. Rules, all enforced server-side:
- A code is accepted for roughly 90 seconds (the previous, current or next 30-second step) and is **single use**. A reused code gets 403 `STEP_UP_INVALID` (`invite_replayed_code`).
- More than 5 attempts in 15 minutes gives 429 `TOO_MANY_ATTEMPTS` (message "Too many attempts. Try again in 15 minutes."; **unit-tested, not captured live**). A correct code resets the count.
- Wrong code is **403, not 401**, on purpose: the FE must not sign the user out or refresh the token on it.

**UX the FE must surface**
1. Ask for the code in the confirm dialog of every invite and revoke, together with the reason. Do not cache or reuse it.
2. After a successful action, a second action within the same 30-second window will fail with `STEP_UP_INVALID` if the authenticator still shows the same code. Tell the user: "That code was already used. Wait for the next code and try again."
3. A reason under 10 characters is rejected: validate client-side, show the counter.
4. State plainly that every action is permanently logged (the audit log cannot be edited or deleted, even by the database).
5. An invited person must already have an account **with an authenticator app on**. On `STAFF_MFA_REQUIRED` or `USER_NOT_FOUND`, tell the Super Admin what the invitee must do first. There is no emailed invite yet.

## Audit actions this slice writes

`staff.invited`, `staff.reinstated`, `staff.revoked`, `staff.bootstrapped` (the server command), `audit.exported`. All appear in the explorer with the reason the staff member typed.

## Bootstrapping the first Super Admin (operators, not FE)

`python -m app.scripts.create_super_admin <email>` on the server. Captured stdout: `delivered+e2e-ff8c41fb21a4@resend.dev is now a Super Admin.` The account must exist, be verified and have 2FA on.

## Verification table

| Behaviour | Status |
|---|---|
| Hidden 404 for anonymous, ordinary founder, Admin on `/super-admin`, revoked, same body as an unknown route | verified live |
| `/admin/me` for Super Admin and Admin | verified live |
| Staff list, invite, reinstate, revoke, every error in the tables above except the two noted | verified live |
| Step-up wrong code, replay | verified live |
| Step-up lockout 429, 5/15 min | unit-tested only (needs 5+ failing calls and a lockout the journey avoids) |
| `STAFF_ACCOUNT_NOT_ELIGIBLE`, 403 `FORBIDDEN` for a missing scope, `limit>100` | unit-tested only |
| Hidden 405, bad-JSON 404, OpenAPI hiding | unit-tested only |
| Audit list, filter, cursor paging, bad cursor | verified live |
| CSV export, missing reason, export audited | verified live; formula escaping unit-tested only |
| Database refusing UPDATE and DELETE on `audit_log` (`audit_log_append_only`: `"audit_log is append-only: UPDATE is not allowed"`, same for DELETE) | verified live (direct SQL against the e2e database) |
| First-Super-Admin command | verified live (run as a subprocess against the e2e database) |
| Revoke taking effect on the next request, same token | verified live |
