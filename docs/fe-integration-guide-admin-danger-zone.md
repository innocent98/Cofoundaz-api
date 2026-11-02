# FE integration guide: Admin danger zone (maintenance mode and global sign-out)

Every body below is copied from `e2e/_captures/admin_danger_zone/` (a live run against the real server; the file name is given per section). Emails and ids are e2e placeholders. Base path `/api/v1`. All `/super-admin/*` routes need a Super Admin token; anyone else gets the plain `404 {"detail":"Not Found"}` (verified: `danger_zone_founder_404`).

## 1. What this is

Two buttons on the Super Admin console, each needing a **second Super Admin**:

- **Maintenance mode**: founders can read but not write; they see a message.
- **Global sign-out**: every user and every staff member is signed out.

Flow: Super Admin A **requests** (reason, authenticator code) → a different Super Admin B **approves or rejects** (reason, code) → the action runs when B approves. A can **cancel**. An unanswered request **expires after 60 minutes**. **Switching maintenance OFF needs no second person**: any one Super Admin does it alone.

Every mutation body carries `reason` (10 to 500 characters) and `totp_code` (6 digits, fresh, single use; same rule as the other staff screens). A wrong or reused code is `403 STEP_UP_INVALID` (do not sign the user out on it). Missing field: `422 VALIDATION_ERROR` with `field_errors`.

## 2. Endpoints

### Public status (for the banner). No auth. `status_on`
`GET /platform/status`
```json
{"data":{"maintenance":{"enabled":true,"message":"Cofoundaz is being upgraded. Your work is safe; we will be back by 10:00 WAT.","since":"2026-10-31T15:50:00.761568+00:00"}},"meta":null}
```
When off (`status_off`): `{"data":{"maintenance":{"enabled":false,"message":null,"since":null}},"meta":null}`. Poll about every 60 s, and also react to any `503 MAINTENANCE_MODE` (section 4).

### Overview. `danger_zone_overview`
`GET /super-admin/danger-zone`
```json
{"data":{"maintenance":{"enabled":false,"message":null,"since":null},"last_global_signout_at":null,"approval_ttl_minutes":60,"eligible_approvers":[{"user_id":"2cbb7dff-38d6-4293-9500-7e84bea541de","email":"delivered+e2e-db22e25ae38e@resend.dev","full_name":null}],"pending_count":0},"meta":null}
```
`eligible_approvers` is the "Approver" select: other Super Admins who can approve right now (never yourself). It can be empty; then the request buttons will fail with `NO_SECOND_APPROVER`, so disable them and say why.

### Request. `request_created`
`POST /super-admin/approvals` → `201`
Body: `{"action":"maintenance_enable"|"global_signout","reason":"...","totp_code":"123456","message":"...","approver_user_id":"<user_id>"}`. `message` (1 to 280 characters) only for `maintenance_enable` (else `422 MESSAGE_NOT_ALLOWED`); omit it to use the default text. `approver_user_id` is optional: omitted means any other Super Admin may answer.
```json
{"data":{"id":"faef240e-3fff-470c-bb4e-95b367ac118e","action":"maintenance_enable","status":"pending","reason":"Planned database upgrade window","message":"Cofoundaz is being upgraded. Your work is safe; we will be back by 10:00 WAT.","requested_by":{"user_id":"427e4d72-2f7f-4491-b10a-cbda93338740","email":"delivered+e2e-080feeca571c@resend.dev","full_name":null},"designated_approver":{"user_id":"2cbb7dff-38d6-4293-9500-7e84bea541de","email":"delivered+e2e-db22e25ae38e@resend.dev","full_name":null},"decided_by":null,"decided_at":null,"decision_reason":null,"outcome":null,"expires_at":"2026-10-31T16:48:18.722856+00:00","created_at":"2026-10-31T15:48:18.717689+00:00"},"meta":null}
```
Nothing happens until someone approves. Another request of the same kind from you while one is open: `409 APPROVAL_ALREADY_PENDING` (`request_already_pending`).

### List (pending and past). `approvals_list_pending`, `approvals_list_all`
`GET /super-admin/approvals?status=pending|approved|rejected|expired|cancelled&limit=50&cursor=...` (newest first; no `status` = all). Shape: `{"data":{"requests":[<request as above>]},"meta":{"next_cursor":null,"total_estimate":null}}`. Expired requests are settled when you list, so the list is always current.

### Approve / reject / cancel
`POST /super-admin/approvals/{id}/approve` · `/reject` · `/cancel`, body `{"reason":"...","totp_code":"..."}`.
- Approve (`request_approved_maintenance_on`): returns the request with `status:"approved"`, `decided_by`, and `outcome`. For maintenance, `outcome` is `{"maintenance":{"since":"...","enabled":true,"message":"..."}}`. For sign-out (`signout_approved`): `"outcome":{"signed_out_at":"2026-10-31T15:50:30.753104+00:00","sessions_signed_out":7}`.
- Reject (`request_rejected`): `status:"rejected"`, nothing runs. Cancel (`request_cancelled`): only the requester; `status:"cancelled"`.

### Switch maintenance off. `maintenance_disabled`
`POST /super-admin/maintenance/disable`, body `{"reason":"...","totp_code":"..."}` →
`{"data":{"maintenance":{"enabled":false,"message":null,"since":null}},"meta":null}`. If it is not on: `409 MAINTENANCE_NOT_ON` (`maintenance_disable_when_off`).

## 3. Errors the FE must handle (exact strings from live responses)

| Status / code | When | Message |
|---|---|---|
| 403 `STEP_UP_INVALID` | wrong or reused code (`request_wrong_code`) | That authenticator code isn't right, or was already used. Enter a new one. |
| 422 `VALIDATION_ERROR` | missing or short field (`request_missing_code`, `request_reason_too_short`) | Please check the highlighted fields. |
| 422 `CANNOT_DECIDE_OWN_REQUEST` | requester approves or rejects (`approve_own_request_refused`) | A different Super Admin has to answer your request. You can cancel it instead. |
| 409 `APPROVAL_ALREADY_PENDING` | `request_already_pending` | You already have an open request for this. Cancel it or wait for an answer. |
| 409 `APPROVAL_NOT_PENDING` | already decided (`approve_already_decided`) | That request is already rejected. (the word is the real status) |
| 409 `MAINTENANCE_NOT_ON` | `maintenance_disable_when_off` | Maintenance mode isn't on. |

Codes below are covered by unit tests (`tests/api/test_staff_danger_zone.py`) but were **not captured live**; messages are from the code:
409 `APPROVAL_EXPIRED` ("That request expired before it was answered."), 409 `MAINTENANCE_ALREADY_ON` ("Maintenance mode is already on."), 409 `REQUESTER_NOT_ACTIVE`, 403 `NOT_DESIGNATED_APPROVER`, 403 `NOT_YOUR_REQUEST`, 404 `APPROVAL_NOT_FOUND`, 422 `NO_SECOND_APPROVER`, 422 `APPROVER_INVALID`, 422 `MESSAGE_NOT_ALLOWED`.

## 4. What founders' apps see during maintenance (`write_blocked_503`)

Any write (POST, PUT, PATCH, DELETE) outside the allow-list:
`503`, header `Retry-After: 60`, body
```json
{"error":{"code":"MAINTENANCE_MODE","message":"Cofoundaz is being upgraded. Your work is safe; we will be back by 10:00 WAT.","field_errors":[]}}
```
**Instructions for the FE:** on `503 MAINTENANCE_MODE`, show the `message` in a banner, keep the page readable, disable save buttons, and do **not** sign the user out. GET requests keep working. Sign-in, refresh, logout, MFA challenge, password reset, email verify and OAuth sign-in keep working. Signup, profile edits and MFA setup are blocked like other writes. Staff consoles keep working. Payment and provider webhooks keep being processed (`paystack_webhook_during_maintenance` returned `{"data":{"received":true,"outcome":"not_successful"},"meta":null}`; a bad signature still gets 401, `paystack_webhook_bad_signature`).

## 5. After a global sign-out (`old_access_token_401`, `old_refresh_token_401`, `staff_old_token_404`)

- Every old access token: `401 {"error":{"code":"UNAUTHORIZED","message":"Not authenticated.","field_errors":[]}}`; every old refresh token: 401. The app's normal "session ended" path applies: go to sign-in.
- **The approver is signed out too.** Right after a successful approve of `global_signout`, send them to the sign-in page and tell them why. Staff old tokens get the plain 404 on `/super-admin/*` (the guard's usual answer when it cannot authenticate).
- Everyone, staff included, can sign in again at once (password, then authenticator code for staff). This works even while maintenance is on. `last_global_signout_at` in the overview shows the time (`danger_zone_after_signout`).
- Open live-update streams close with a `revoked` event (`{"reason":"signed_out"}`); the app should reconnect only after signing in again.

## 6. UX consequences to surface

- Confirm dialogs must say: maintenance blocks every founder's saves until someone switches it off; sign-out ends **everyone's** session including yours.
- Show the 60-minute expiry and the requester/approver names. A request is useless to approve after the requester lost access (`REQUESTER_NOT_ACTIVE`).
- Turning maintenance off is a one-person action: do not show an approver select for it.
- Revoking or demoting a Super Admin cancels their open requests (and those sent to them).

## 7. Verification table

| Behaviour | Status |
|---|---|
| Request, list, reject, cancel, approve, designated approver, own-request refusal, duplicate refusal | verified live |
| Maintenance on: write 503, reads and sign-in work, Paystack webhook processed, bad signature 401, staff console works, banner status | verified live |
| Disable by one Super Admin; disable when off 409 | verified live |
| Global sign-out: old access and refresh tokens dead, staff old token 404, re-login (founder and staff) works | verified live |
| Audit rows for approvals, maintenance and sign-out | verified live (`audit_*` captures) |
| Expiry, `REQUESTER_NOT_ACTIVE`, `NOT_DESIGNATED_APPROVER`, `APPROVER_INVALID`, `NO_SECOND_APPROVER`, stream close, allow-list prefixes | unit tests only (shape from code) |
| Real Paystack event during maintenance | not verified (stubbed provider, signed locally) |
| Two API instances / clock skew | not verified |
