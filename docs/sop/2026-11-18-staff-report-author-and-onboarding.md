# SOP: nameless report authors and staff skipping founder onboarding (SO1, SO4)

**What shipped:** two fixes from the every-control re-walk of the Admin console (FE #269, `docs/checklist/admin-rewalk.md` rows SO1 and SO4; Adebayo approved both 2026-11-18). API branch `fix/staff-report-author-and-onboarding`; FE companion branch `fix/staff-skip-onboarding` (cofoundaz). No migration.

## SO1: moderators saw a comment author's email

| | Before | Now |
|---|---|---|
| `GET /admin/reports`, `GET /admin/reports/{id}`, and the dismiss / escalate / remove / restore bodies | `author.name` fell back to the author's **email** when they had no profile name (captured: `"name": "delivered+e2e-…@resend.dev"`) | falls back to `"A team member"` (`NAMELESS_MEMBER`, `app/core/names.py`) |

**Why:** `_people()` in `app/services/staff/reports.py` used `name or email`. The author entry drops the `email` key (R5), but the email had already been copied into `name`. Breaks D-ADM-9 ("author name only") and the 2026-11-12 rule that member addresses are shown to founder + team member only.

**How:** the fallback is now `name or NAMELESS_MEMBER` for every person on a report (reporter, author, escalated_by, decided_by). Reporter and staff entries keep their separate `email` field (staff are allowed to see the reporter's address, R5), so the console can show it beside the label.

**Other serializers checked** (grep for `name or email` and similar across `app/`):

| Place | Who reads it | Verdict |
|---|---|---|
| `app/services/staff/support.py` `_people` (ticket requester / assignee) and staff list | staff | left: D-ADM-9 shows a ticket's founder **name and email**, so the address is already sanctioned there |
| `app/services/support/core.py::display_names` (founder's own ticket thread) | the workspace's founder / TM | left: founder + TM may see addresses |
| `app/services/sales/analytics.py`, `marketplace/calendar_link.py`, `calendar/ics.py`, `worker/handlers/documents_sign.py` | founder/TM screens or the email recipient themself | left (already reviewed in `2026-11-12-member-email-leaks.md`) |

## SO4: staff sign-ins created ghost founder workspaces

**Why:** every staff sign-in landed on `/onboarding`, whose first `GET /onboarding/state` created an empty founder draft owned by the staff member. 5 of 7 local workspaces were staff ghosts, inflating total workspaces and activation.

**How (API):**

| Piece | Change |
|---|---|
| `app/services/staff/roster.py` (new) | `staff_role(db, user_id)` and `active_staff_ids()` subquery. Staff = an **unrevoked** `platform_staff` row; deliberately ignores the TOTP check `/admin/me` applies, so a staff member who has not turned on 2FA yet still never becomes a founder |
| `GET /auth/me` (`app/api/v1/endpoints/auth/me.py`) | new top-level `staff_role`: `"admin"`, `"super_admin"` or `null`. It only ever describes the caller, so it doesn't reveal the hidden console to anyone else |
| `resolve_or_create_workspace` (`app/services/onboarding/workspace.py`) | refuses with **409 `ONBOARDING_NOT_FOR_STAFF`** instead of creating a draft. Every onboarding route goes through it (`GET`/`PATCH /state`, `POST /complete`, `/invites`, `/logo`) |
| `dashboard_kpis` (`app/services/staff/workspaces.py`) | total workspaces, new-in-window, activation, paid conversion, MRR and the activations series leave out **staff ghost drafts** only: owned by current staff **and** never onboarded (`_customer()`). A dual-role staff member's real, onboarded (or paid) workspace still counts, and revoking someone's staff access never rewrites past KPIs for an onboarded workspace (Opus review L1). The sign-ups series and window counts leave staff users out |

Decisions: a staff member who **already owns** a workspace keeps it and gets it back from `/onboarding/state` (no deletion; it stops counting in KPIs only while it is an unfinished draft). A staff member who is also a founder (has a membership) follows the normal founder flow on the FE, including finishing their own onboarding. Staff joining someone's workspace through an invitation is unchanged (members never get a draft anyway). Revoked staff count as ordinary users again.

**How (FE, cofoundaz `fix/staff-skip-onboarding`):** `lib/auth/staff.ts` maps `staff_role` to `/admin-portal` (admin) or `/super-admin`; `lib/auth/post-login.ts` routes staff there after password, MFA and Google sign-in (a console deep link in `next` is kept) and on the verify page's Continue; `/onboarding` sends staff away before reading state and treats `ONBOARDING_NOT_FOR_STAFF` as a redirect. Samuel's Admin pages were not touched.

## What's involved

`app/services/staff/reports.py`, `app/services/staff/roster.py`, `app/services/staff/workspaces.py`, `app/services/onboarding/workspace.py`, `app/api/v1/endpoints/auth/me.py`; tests `tests/api/test_staff_reports.py`, `tests/api/onboarding/test_staff_skip_onboarding.py`; e2e `e2e/test_admin_reports.py`; captures `e2e/_captures/admin_reports/` (the 9 bodies whose author changed) and `e2e/_captures/staff_skip_onboarding/`; guides `docs/fe-integration-guide-admin-reports.md`, `docs/fe-integration-guide-auth-onboarding.md` ("Platform staff skip the wizard").

## Verification

- Opus review fixes (lead 27): KPIs exclude only unfinished staff drafts (a staff-owned onboarded paid workspace counts); a staff member who already owns a workspace gets it back (200); revoked staff can start a draft.
- Unit: nameless author → `"A team member"` on list and detail, the address absent from the body; `staff_role` for admin / super admin / founder / revoked; 409 on all four onboarding routes with no workspace created; a founder still gets a draft; KPIs ignore a staff-owned ghost and staff sign-ups. Plus `tests/api/onboarding/`, `tests/api/auth/`, `test_staff_reports.py`, `test_dashboard_kpis` green on a scratch DB.
- Live (uvicorn from the worktree on a scratch DB): `e2e/test_admin_reports.py` passes with the new SO1 and SO4 asserts; captures committed.
- Browser on localhost (FE branch against this API): a staff sign-in lands on the console with no new workspace; a founder still goes through onboarding (see the FE PR).
- black, ruff, mypy clean.

## Operate / roll back

No migration, no env change. Roll back by reverting the PR; the FE treats a missing `staff_role` as "not staff", so the FE PR is safe to merge first or second. Existing ghost workspaces stay in the DB; they only stop counting in KPIs.

## Follow-ups

- Existing staff ghost workspaces could be soft-deleted by a Super Admin through the danger zone if Adebayo wants them gone (not done: data change).
- The Admin workspace list (`GET /admin/workspaces`) still lists staff-owned ghosts; filter there too if it confuses support.
- Samuel's Admin FE, when it returns, should read `staff_role` instead of probing `/admin/me` to decide where to land.
- When Samuel's console lands, its "2FA not set up" path must **not** send staff to `/dashboard` or `/onboarding`: the `staff_role` landing sends them straight back to the console, so that would loop. Show a "turn on 2FA" screen inside the console instead.
