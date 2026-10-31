# Admin slice A: staff access, Super Admin staff routes, audit explorer

**What shipped:** the platform's first staff roles. A `platform_staff` table, guards that every later admin slice builds on, a Super Admin API to list, invite and revoke staff (fresh TOTP on each mutation, reason on each), the first-Super-Admin command, an append-only `audit_log` (DB trigger) and a Super Admin audit explorer with an audited CSV export. Branch `feature/admin-staff-access`, migration `0083_platform_staff`. Spec: `cofoundaz/docs/superpowers/specs/2026-10-31-admin-portals-design.md` (D-ADM-1, 2, 7, 11).

**Why:** the API had no platform roles. Every staff console (Users, Workspaces, flags, overrides) needs a guard that cannot be bypassed from a workspace route, and an audit trail staff cannot rewrite.

**How, and the calls made**

| Topic | Decision |
|---|---|
| Role source | `platform_staff` row read from the DB on every request; the JWT is unchanged (`sub`, `exp`). A revoke, or turning 2FA off, applies on the next request. |
| Who counts as staff | An active row **and** a verified, `active` user with TOTP on. Anything else is a 404. |
| Hiding the routes | Callers who are not staff get the exact body of an unknown route (`{"detail":"Not Found"}`), not the app's error envelope. Also hidden: 405 on a wrong method, malformed JSON on a staff path, and the OpenAPI schema (`include_in_schema=False`). Active staff who lack a scope get 403 (`require_staff(scope)`); an Admin on a `/super-admin` route gets 404. |
| Step-up window | **Per action.** Every mutation carries its own `totp_code`: valid for the previous, current or next 30 s step (about 90 s), **single use** (Redis `staff_stepup_used:{user}:{code}`, TTL 150 s). Attempts are counted atomically before checking; more than 5 in 15 minutes gives 429 `TOO_MANY_ATTEMPTS`; a success resets the count. Wrong or reused code: 403 `STEP_UP_INVALID` (403, not 401, so the FE does not sign the user out). |
| Reason | Required on every staff mutation and on the audit export: 10 to 500 characters after trimming, stored in the new `audit_log.reason`. Non-secret context (invited email, role, scopes) goes in `audit_log.details` (JSONB). |
| Invite | Existing account only: must be verified, active and have TOTP on (`STAFF_MFA_REQUIRED`, `STAFF_ACCOUNT_NOT_ELIGIBLE`, `USER_NOT_FOUND`). Admin needs at least one of `support`, `content`, `moderation`; Super Admin has none. A revoked person is reinstated on the same row (`staff.reinstated`). |
| Revoke | Cannot revoke yourself. The actor's row is re-read under a row lock, so a Super Admin revoked a moment earlier cannot finish a revoke. Together with the self-revoke ban this means at least one Super Admin always remains, so there is no separate "last Super Admin" error. |
| Audit trigger | `BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW` raises `integrity_constraint_violation`. TRUNCATE and a superuser disabling the trigger are not covered (the immutable store comes later, D-ADM-11). |
| Export | `GET /super-admin/audit/export` (needs `reason`), max 5000 rows, formula-safe cells, writes an `audit.exported` row with the filters and row count. No step-up on the export (read-only, same data the paged list returns; a one-time code in a GET URL would land in access logs). |
| Alternatives rejected | A `role` column on `users` (mass-assignment risk); a short step-up session (a stolen session would inherit it); a JWT role claim (revoke would lag). |

**Also fixed here (found by the Opus review):** `get_current_user` now rejects any JWT carrying a `typ` claim. The calendar OAuth `state` token is signed with the same key, carries `sub`, and travels in a URL, so it could be used as a bearer token for its 10 minutes. This changes auth for every route; access tokens never carry `typ`.

**What's involved**
- `alembic/versions/0083_platform_staff.py`: table, `audit_log.reason`/`details`, trigger and function (`FUNCTION_SQL`/`TRIGGER_SQL` are importable for the test).
- `app/db/models/platform_staff.py`, `app/db/models/enums.py` (`StaffRole`), `app/db/models/audit.py`, `app/platform/audit.py` (`write_audit(reason=, details=)`).
- `app/api/staff_deps.py` (`require_staff`, `require_super_admin`), `app/api/deps.py` (`typ` rejection), `app/core/errors.py` (staff-path 405 and bad-JSON hiding).
- `app/services/staff/{access,stepup,audit_explorer}.py`, `app/schemas/staff.py`.
- `app/api/v1/endpoints/admin.py` (`GET /admin/me`), `super_admin.py` (`GET/POST /super-admin/staff`, `POST /super-admin/staff/{id}/revoke`, `GET /super-admin/audit`, `GET /super-admin/audit/export`).
- `app/scripts/create_super_admin.py`.

**Verification**
- `tests/api/test_staff_access.py` (43 tests; 100% of the new modules, 97% of the command): guard matrix, step-up (wrong, replay, window, lockout, unreadable secret), invite/revoke/reinstate, explorer filters and paging, export, command, trigger (the migration's own SQL inside a test transaction), `typ` tokens, hidden 405/bad JSON/OpenAPI.
- `e2e/test_admin_staff.py`: one live journey against a real server (command, sign in with TOTP, every error shape, trigger refused on a real database). Captures in `e2e/_captures/admin_staff/`; guide `docs/fe-integration-guide-admin-staff.md`.
- Migration: upgrade, downgrade to 0082, upgrade, downgrade to base, upgrade, `alembic check` clean, one head.
- An Opus review of the guards, step-up and trigger; confirmed items fixed (see "Also fixed", the 404 body, 405, schema, atomic throttle, replay TTL, actor-row lock, concurrent-invite 409).
- `tests/api/test_team_manage.py:347` UPDATEs `audit_log.created_at`. It still passes because the unit-test schema is built from the models (no trigger); only migrated databases have the trigger.

**Operate / roll back**
- First Super Admin, on the server: `docker compose exec api python -m app.scripts.create_super_admin <email>`. The account must exist, be verified, and have an authenticator app on. Re-running is safe.
- Roll back: revert the PR and `alembic downgrade 0082_ai_preferences` (drops the trigger, the two columns and `platform_staff`). Anything else that starts to UPDATE or DELETE `audit_log` will fail with `audit_log is append-only`.
- Needs a persistent `MFA_ENCRYPTION_KEY` (step-up decrypts the stored secret).

**Follow-ups**
- Step-up needs a code the app has not used in the last ~90 s: two mutations within one 30 s step need the next code. The FE must say so.
- Staff with TOTP off or removed lose access silently (404); Users slice B may want a visible reason.
- TRUNCATE on `audit_log` is not blocked; hardware keys, a separate immutable store and the FE console are later (spec section 6).
- Staff can be invited only if they already have an account. An emailed invite for new accounts is not built.
