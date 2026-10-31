# Admin slice B: Users, Workspaces (read-only), Dashboard KPIs

**What shipped:** `PATCH /super-admin/staff/{id}` (change a staff member's role or scopes, gap P9) and the staff Users console (search, profile, suspend, unsuspend, send password reset, resend verification), a read-only Workspaces list and detail, and Dashboard KPIs, all under `/api/v1/admin`. Branch `feature/admin-users-workspaces`. No migration. Spec section 4 slice B, D-ADM-5, 7, 9; gap list `cofoundaz/docs/checklist/admin-prd-gaps.md` (A4 to A15, S14, P1 to P3, P11, P12).

**Why:** support staff needed to find an account, see its state, and act on it without database access, and Adebayo wanted the first Admin dashboard numbers. The FE consoles are built by the FE developer on these routes (guide `docs/fe-integration-guide-admin-users.md`).

**How, and the calls made**

| Topic | Decision |
|---|---|
| Guards | Users and Workspaces need the `support` scope (a Super Admin passes every scope); the dashboard needs any active staff. Non-staff get the unknown-route 404 (slice A). A staff member without the scope gets 403 `FORBIDDEN`. |
| Step-up (P1, accepted 2026-10-31) | **Every** staff mutation, Admin or Super Admin, needs a reason (10+ characters) and the actor's own fresh single-use authenticator code (slice A rules). Reads need neither. Slice A's staff invite and revoke already had it, and the new PATCH does too; the audit export is a read, so it takes a reason but no code. |
| Staff targets (P2, accepted) | Nobody acts on themselves (422 `CANNOT_ACT_ON_SELF`). An Admin cannot act on a staff account (403 `STAFF_TARGET`); a Super Admin can act on other staff. The profile's `can` object tells the FE which buttons to enable. An Admin reading a staff member's profile gets no sessions and no phone (the Opus review's design note). |
| Suspend (P12, accepted) | Sets `status = disabled` and revokes every `AuthSession`. `get_current_user` and login already reject `disabled`, so access tokens stop working on the next request. Also invalidates unconsumed reset and verification tokens. Row-locks the target against concurrent suspends. The Opus review found three narrow ways around it, all closed: an MFA ticket issued before the suspension (`mfa/challenge` now refuses disabled or deleted users), a refresh in flight (`rotate_refresh` refuses them and mints nothing), and a reset token issued in the same instant (`/password/reset` refuses a disabled user). |
| Unsuspend | Restores `active`, or `pending_verification` if the email was never verified, so unsuspending cannot skip verification. |
| Reset password | Sends the normal reset email (the staff member never sees or sets a password). Allowed only for `active` or `locked` accounts. 60-second per-user cooldown (429 `TOO_SOON`); a failed send rolls the token back and clears the cooldown (503 `EMAIL_DELIVERY_FAILED`, shown to staff since they are authorized). |
| Resend verification | Only for `pending_verification` (409 `ALREADY_VERIFIED` otherwise); same cooldown and failure handling. |
| Search | Users: email, name, workspace name, or an exact user or workspace id; filters `billing_status` (free, trialing, active, past_due, canceled) and `activity` (active = signed in within 30 days). Workspaces: name, owner email, id; filters `stage`, `billing_status`. `%`, `_` and `\` are literal. Cursor on (created_at, id). |
| Plan on a user | The primary workspace's plan and billing status: the user's own founder workspace first, else the oldest membership. |
| Workspace detail (P11) | Members (email, name, role), plan and subscription state, usage totals (AI credits over 30 days, seats, storage MB), integration provider and status only. |
| KPIs (P3 definitions) | Total workspaces; activation = workspaces created in the window that finished onboarding within 14 days, over workspaces created in the window; paid conversion = active non-Starter subscriptions over all non-deleted workspaces; MRR = active non-Starter subscriptions, annual priced at one twelfth, kobo, NGN. Deltas only where history exists: total workspaces and activation compare to the previous 30 days; paid conversion and MRR return `previous: null` (no snapshot table yet). |
| D-ADM-9 (no content) | Every query names its columns; no ORM row is serialized whole. Secret columns (password hash, MFA secret, token hashes, finance ciphertexts) are never selected. The document table is touched only for a SUM of sizes. Tests assert exact key sets and that sentinel secrets never appear in any response. |
| Staff list | `last_login_at` added to each staff row (gap S14). |
| Edit staff (P9) | `PATCH /super-admin/staff/{id}` {role, scopes, reason, totp_code}: Super Admin only, step-up, not on yourself (422 `CANNOT_EDIT_SELF`), no-op refused (422 `NO_CHANGE`), a revoked row is 409 `ALREADY_REVOKED` (reinstate by inviting). Same locked actor re-check as revoke. Audit `staff.updated` with `details.from` and `details.to`. |

**Also fixed here:** `POST /auth/verify` set `status = active` on any token holder, so a verification link issued before a suspension would un-suspend the account. It now leaves `disabled` alone (and suspend also kills such tokens).

**What's involved**
- `app/api/v1/endpoints/admin.py` (routes), `app/schemas/staff.py` (`StaffActionRequest`).
- `app/services/staff/users.py`, `workspaces.py` (list, detail, KPIs), `like.py`; `access.py` (`last_login_at`).
- `app/api/v1/endpoints/auth/registration.py` (verify no longer lifts a suspension), `auth/mfa.py`, `auth/password.py`, `app/services/auth/sessions.py` (suspended users refused at challenge, reset and refresh).
- `app/api/v1/endpoints/super_admin.py`, `app/services/staff/access.py` (`update_staff`, shared `_lock_for_change`), `app/schemas/staff.py` (`StaffUpdateRequest`).
- Tests `tests/api/test_staff_users.py`, live journey `e2e/test_admin_users.py` (captures in `e2e/_captures/admin_users/`).

**Verification**
- 41 unit tests in `tests/api/test_staff_users.py` (plus the slice A file) (99 to 100% of the new modules): every route hidden from non-staff and scope-checked, step-up on each mutation, search and filters and paging, profile shape and no secrets, suspend end to end (token dead on the next call, sessions and tokens revoked, login refused, audit row), the verification-link bypass, unsuspend states, staff-target rules, both email actions including send failure and cooldown, workspace list and detail and exact key sets, deleted workspaces hidden, KPI arithmetic.
- Live e2e (passed, 203 s): a real Super Admin (command) invites a real support Admin; the Admin searches, suspends a founder whose old token then returns 401, unsuspends, sends a reset (email counted in the file mailbox), resends a verification, reads workspaces and KPIs; the Super Admin edits the Admin's scopes (the Admin's next call shows them).
- Opus review of the guards and suspend path: no High or Medium; its three Low findings and its design note are fixed (above) and covered by unit tests.
- Not verified: scope-restricted Admin (a staff member holding only `content`) live (unit-tested), a real mail provider.

**Operate / roll back:** no migration; revert the PR. Suspended accounts stay suspended (status is data). Needs `MFA_ENCRYPTION_KEY` (step-up).

**Follow-ups**
- Daily platform snapshots so paid conversion and MRR can show deltas.
- `GET /admin/users` has no export; add if support asks.
- Billing overrides are slice C; flags D; maintenance mode and global sign-out E.
