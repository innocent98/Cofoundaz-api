# SOP: Settings BE (a), Profile, Security, Workspace, notification schedule

**What shipped:** the non-Billing Settings APIs. Editable profile (name, role, country, phone) and avatar; change password;
two-factor status / disable / new backup codes; "where you're signed in" (list, revoke one, revoke the others); workspace settings
(`GET/PATCH /workspace`, logo); notification digest mode and quiet hours stored on the preferences. Migration
`0077_settings_notif_schedule`. Branch `feature/settings-be`. Closes gap rows S1 to S7, S9, S10 (partial) in
`cofoundaz/docs/checklist/settings-prd-gaps.md`. FE guide: `docs/fe-integration-guide-settings.md` (live-captured).

## Why

The built `/settings` page was a mock Billing screen and `/account` was read-only. `PATCH /auth/me` silently ignored every field
but `full_name`, a signed-in user could not change a password or turn 2FA off, and `auth_sessions` was written but never read.

## How: decisions and rejected alternatives

| Topic | Decision |
|---|---|
| **`PATCH /auth/me` strictness** | `extra="forbid"` + "send at least one field". Before, unknown fields were dropped and the call returned 200, so a typo looked like a save. Existing callers only send `full_name`. |
| **Session identity** | The access JWT carries no session id. A "sign-in" is a refresh-token *family* (rotation keeps the family id), so the family id is the public session id and "current" is the family of the refresh cookie (or a `refresh_token` body field). No cookie: nothing is current. Rejected: adding a `sid` claim (would invalidate every live token and change the JWT contract). |
| **Revoke-others without a known current session** | `409 CURRENT_SESSION_UNKNOWN`: refusing beats signing the caller out. Change-password in the same situation revokes everything (documented trap in the guide). |
| **Wrong-password throttle** | Change password, disable 2FA and regenerate codes share a per-user 5 strikes / 15 minutes Redis lock (`services/auth/throttle.py`), so a stolen access token cannot be used to guess the real password. |
| **2FA disable accepts a backup code** | A user who lost the device must still be able to turn it off. Password is always required too. |
| **Avatar / workspace logo** | PNG, JPG, WebP only (no SVG: the logo route for onboarding allows it, a user-uploaded avatar served inline is an XSS surface), 2 MB, magic bytes checked against the declared type. |
| **Workspace edits** | Founder only (`require_role(founder)`), audited (`workspace.updated`). Reads are open to every member so the hub can render read-only. Stage and business model reuse the onboarding enums. |
| **Notification schedule** | Columns on `notification_preferences` (digest mode/time/day, quiet hours, timezone). **Storage only**: no digest is sent and quiet hours are not enforced (gap S7, owner Notifications). |
| **Not built** | Account deletion / data export (D-SET-14), SMS 2FA (still a seam), password-changed email notice, billing grant for team members (S10: no grants storage exists; billing access will be founder-only). |

## What's involved

`app/api/v1/endpoints/auth/{me,mfa,password,sessions}.py`, new `app/api/v1/endpoints/workspace.py`, `app/services/auth/{sessions,throttle}.py`,
`app/services/notifications/preferences.py`, `app/schemas/{auth,notification}.py`, `app/db/models/notification_preference.py`,
migration `0077`. Tests: `tests/api/auth/test_account_settings.py`, `tests/api/test_workspace_settings.py`,
`tests/api/test_notification_preferences.py`; live journey `e2e/test_settings.py` (captures in `e2e/_captures/settings/`).

## Verification

53 new/changed unit tests green on a scratch Postgres 17; live e2e journey green (profile, avatar, 2FA on/off, sessions with a real
refresh cookie, password change with the old password rejected, workspace, notification schedule). CI gates reproduced locally (see PR).

## Operate / roll back

No new env vars. `alembic downgrade 0076_documents_signer_link_audit` drops the seven notification columns; the endpoints keep working
without them only after a code revert. `PATCH /auth/me` is stricter: a client that sends extra fields now gets 422.

## Follow-ups

Digest sender + quiet-hours enforcement (Notifications); account deletion/export; password-changed email; AI preferences (needs a memory
store); the hand re-walk of every Settings control at 1440 and 375 for every role after the FE lands.
