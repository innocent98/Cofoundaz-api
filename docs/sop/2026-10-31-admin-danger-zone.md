# Admin slice E: maintenance mode and global sign-out (second approver)

**What shipped:** Super Admin "Danger zone" API (D-ADM-4): a Super Admin requests maintenance mode or a global sign-out, a **different** active Super Admin approves or rejects it, an unanswered request expires. One middleware blocks founder writes while maintenance is on. A public status read feeds the app banner. PR: see the branch `feature/admin-danger-zone` (migration `0085_admin_danger_zone`, chained on `0083`; re-chain after slices C and D merge).

**Why:** the Super Admin mock has two dangerous buttons (S22, S23). Neither may be pressed by one person, and neither may leave the platform in a state that cannot be undone or lock the recovering staff out.

**How, and the calls made**

| Topic | Decision |
|---|---|
| Approval | `staff_approval_requests` (action `maintenance_enable` or `global_signout`, status pending / approved / rejected / expired / cancelled). The requester can never approve or reject (422 `CANNOT_DECIDE_OWN_REQUEST`); they may cancel. Optional `approver_user_id` sends it to one Super Admin only. Approval runs the action and records the decision in one transaction. |
| Who counts as an approver | Another Super Admin who passes the staff guard right now (staff row active, user active and verified, TOTP on). Re-read under row locks at approval. If the requester has since lost that, the request cannot run (409 `REQUESTER_NOT_ACTIVE`; reject still works). Revoking or demoting someone cancels their open requests and those sent to them. |
| Expiry | 1 hour. Overdue requests are settled lazily (on list, request or answer) with an `approval.expired` audit row. One open request per person per action (partial unique index). |
| Step-up | Request, approve, reject, cancel and disable each need a reason (10+ characters) and a fresh single-use authenticator code (slice A rules). |
| Maintenance on | Needs an approver. Message (max 280) is stored with the request. |
| **Maintenance off** | **One active Super Admin alone, with step-up, reason and audit.** Not an approval. Settled by the brief's rule that no state may need two people to undo when one is available. (Needing two to turn it ON is safe: with one Super Admin it simply cannot be turned on.) |
| Block | `app/middleware/maintenance.py`: any non-GET/HEAD/OPTIONS request gets 503 `MAINTENANCE_MODE` (`Retry-After: 60`) unless its path is allow-listed: sign-in and recovery under `/auth` (login, refresh, logout, mfa/challenge, oauth, forgot, reset, verify), `/admin`, `/super-admin`, `/health`, `/webhooks` (Paystack, payment providers, Mono: not one event is lost), sales one-click unsubscribe, notification stream ticket. Signup, profile and MFA setup are blocked. Background jobs (worker, scheduled runs) are not paused. GET routes that write (billing verify, OAuth callbacks) are not paused. Lookup is one primary-key read per blocked-candidate request; a failed lookup lets the request through (logged). |
| Status read | `GET /api/v1/platform/status` (public). |
| Global sign-out | Stamps `platform_settings.session_cutoff`, revokes every `AuthSession`. Access tokens now carry a float `iat`; `get_current_user` refuses `iat <= cutoff`, and a token with no `iat` once a cutoff exists (all pre-change tokens expire within 15 min anyway). An advisory lock (shared in `issue_token_pair` and `rotate_refresh`, exclusive in the sign-out) makes a sign-in in flight either finish before the revoke or start after it. Open notification streams close with a `revoked` event at their next check. |
| **Staff on sign-out** | Staff are signed out like everyone, including the approver. They sign back in with password + authenticator code; `/auth` sign-in stays open during maintenance, so nobody is locked out. The FE must send the approver to the sign-in page after approving. |

**What's involved**
- `app/services/staff/danger_zone.py`, `app/platform/state.py`, `app/middleware/maintenance.py`, `app/main.py` (middleware order: inside CORS and the rate limiter).
- `app/api/v1/endpoints/super_admin.py` (`/danger-zone`, `/approvals`, `/maintenance/disable`), `platform_status.py`, `app/schemas/staff.py`.
- `app/api/deps.py`, `app/core/security.py`, `app/services/auth/sessions.py`, `notifications.py`, `access.py` (cancel open requests on revoke/demote).
- Models `platform_setting.py`, `staff_approval.py`; migration `0085_admin_danger_zone`.
- Tests `tests/api/test_staff_danger_zone.py`; live journey `e2e/test_admin_danger_zone.py` (captures `e2e/_captures/admin_danger_zone/`).

**Verification:** see the PR. Three Opus reviews (approval flow, middleware, sign-out) found no High; Medium and Low findings fixed as listed above.

**Operate / roll back:** if maintenance is stuck on and no one can sign in, set `platform_settings` key `maintenance` value `{"enabled": false}` in SQL. Roll back by reverting and `alembic downgrade 0083_platform_staff` (drops both tables). Old tokens without `iat` stay valid until no cutoff exists.

**Follow-ups / not done**
- Smoke-page signups (`/validation/pages/{slug}/signups`, `/events`) are blocked during maintenance, so visitors to founders' landing pages get 503. Product call whether to allow them.
- Receipt and document signed links, calendar OAuth tokens and a stream ticket minted in the last 30 s before a sign-out live until they expire.
- Cutoff and `iat` use the API server clock (fine on one host; use the DB clock if the API scales to several).
- An approval that is refused because maintenance is already on stays pending until expiry.
- A script to switch maintenance off with no Super Admin able to sign in.
