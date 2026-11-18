# SOP: auth + privacy follow-ups (refresh grace, Google consent, dashboard leaks, joined marker)

**What shipped:** four small fixes in one PR, branch `fix/auth-privacy-followups` (PR number: see the PR; no migration). Adebayo deferred hidden-module work on 2026-11-13; this visible-module batch stays active.

| # | Change | One line |
|---|---|---|
| 1 | Refresh-token grace window | The immediately-previous refresh token still works for `REFRESH_GRACE_SECONDS` (20) after rotation; replay after that is reuse and revokes the family as before. |
| 2 | Consent for Google-created accounts | `POST /auth/oauth/google/start` takes `accepted_terms`; a new account without it is refused before any row exists; with it `terms_accepted_at` / `terms_version` are stored. |
| 3 | Dashboard role leaks | `kpis.tasks_done_this_week` + `kpis.tasks_trend` (mission gate) and `calibration.assessment_complete` (assessment gate) are `null` for other roles; `assessment.*` activity rows (feed + live stream) follow the assessment read gate. |
| 5 | Review fixes (Opus, lead 27) | Health Score markers filtered by role; grace limited to the immediate predecessor; DB clock; bounded lock retry; consent on Google link/takeover. See "Review fixes" below. |
| 4 | "Teammate joined" marker | `MARKER_LABELS` keyed on `member.joined`, the action invitations really write (it had `workspace.member.joined`, which only the event bus carries). |

## Why

1. **Grace.** FE #245 moved tokens to a memory access token + HttpOnly refresh cookie with rotation (#227). Reuse detection treats any replay as theft, so an aborted refresh (response lost, tab closed mid-flight, pre-2022 browsers) or two tabs racing signed the user out everywhere.
2. **Consent.** Email sign-up records `terms_accepted_at` (gap 14, `SIGNUP_REQUIRE_TERMS`); Google-created accounts had none. The account is created inside the callback, a GET redirect from Google, so no request body can carry consent there.
3. **Leaks.** The checklist's open rows from #153: task counts are mission data (D-MIS-8) and "an assessment exists" is assessment data (D-ASM-1); mentors/investors got both, plus the `assessment.completed` activity row.
4. **Marker.** Never fired: no activity row had the key the label map expected.

## How

**1. Grace** (`app/services/auth/sessions.py`, `rotate_refresh` + `_rotate_in_grace`). The atomic claim is unchanged. When the claim matches zero rows, the row is re-read (a concurrent winner has committed `rotated_at` since our first read) and, if it is rotated, unrevoked, unexpired and `now - rotated_at <= grace`, we serve a *fresh equivalent pair*: only hashes are stored, so the first response cannot be replayed. The family's live head is locked `FOR UPDATE`, marked rotated, and a new child issued, so a family always has **exactly one live token** (`list_active` relies on that). Two concurrent callers serialise on the head lock and both succeed; the lock query is retried up to 3 times because Postgres returns no row (rather than the new head) when the lock wait ends on a just-rotated row.
- *Bounded:* the window is anchored to the old token's own `rotated_at`, never extended by replays, and a token only re-enters the window by being rotated itself, so a stolen old token is useless `REFRESH_GRACE_SECONDS` after the owner moved on. Trade-off: for those 20 s a thief and the owner can both hold working tokens; the next replay after the window still revokes everything. `REFRESH_GRACE_SECONDS=0` switches it off.
- Revoked, expired, suspended or deleted accounts never get a grace pair (revoked check is on the old row; user check mirrors the normal path).
- *Rejected:* returning the identical pair (needs raw tokens stored); a `previous_hash` column on the head (migration for no gain); extending the window on every replay (unbounded).

**2. Consent.** `GoogleStartRequest.accepted_terms` is stored in the server-side state record (`terms`), read back in the callback. `_resolve_user` refuses to create an account when `SIGNUP_REQUIRE_TERMS` and no consent: `?error=terms_not_accepted`, audit `auth.google.rejected {"reason": "terms_not_accepted"}`, no `User` / `OAuthAccount` row. With consent: `terms_accepted_at`, `terms_version`, and `terms_version` in the `auth.user.registered` audit (like signup). Existing accounts (linked by `sub` or verified email) are not asked. Chosen over a post-creation prompt so no account ever exists without a consent record; over putting consent on `/exchange` because the account is already created by then.

**3. Leaks** (`app/services/dashboard/service.py`, `app/platform/activity.py`, `app/db/models/enums.py`). New single-sourced `ASSESSMENT_ROLES` (founder + team member, also used by the assessment routes' `_READ_ROLES`). `get_summary` returns `null` for the gated fields (keys stay, mirroring the legacy null scalars). `assessment` moved from `_OPEN_NAMESPACES` to `_RESTRICTED_NAMESPACES`, so every `assessment.*` row fails closed to `ASSESSMENT_ROLES` in both the SQL filter and `activity_visible` (stream).

**4. Marker.** One key renamed (`app/services/health_score/service.py` `MARKER_LABELS`). Changing the stored action instead would have broken the activity-feed contract (`member.joined`) and existing rows; renaming the key makes past joins show up too.

## Review fixes (Opus review of #247, lead 27)

- **M1:** `GET /health-score/history/markers` leaked `Assessment completed` and `Agreement signed` to every role. `get_history_markers` now takes the caller's role and applies `activity_visibility_filter(role)`. Test: `tests/api/test_health_markers_role_gate.py` (all 7 roles).
- **M2 (accepted, docs only):** completion is implied by the open Health Score, by design (#153): `health.status` (`pending_assessment` vs `ok`) and the "Kickoff assessment" signals on `/health-score/dimensions` still tell every role an assessment exists. `calibration.assessment_complete` stays nulled.
- **L3:** grace is allowed only if no other token of the family was rotated after the presented one (checked under the head's row lock, no migration). A token two rotations back, or the same old token a second time after a grace use, is reuse. Consequence, by design: two *simultaneous* replays of one old token let one win and then revoke the family (the loser looks like reuse); a never-used token raced by two tabs still lets both through (claim + immediate predecessor).
- **L4:** the head-lock retry is 10 attempts, not 3.
- **L5:** rotation stamps and the window comparison use the database clock (`clock_timestamp()`), not the app host's.
- **L6:** linking or taking over an existing row with no `terms_accepted_at` records consent when the state carries `terms=true`, else is refused with `terms_not_accepted` when `SIGNUP_REQUIRE_TERMS` is on. Tests in `test_google_signin.py`.

## What's involved

| Area | Paths |
|---|---|
| Grace | `app/services/auth/sessions.py`, `app/core/config.py` (`REFRESH_GRACE_SECONDS`), `scripts/e2e_run.sh` (sets 5 for the journey) |
| Google consent | `app/services/auth/google_signin.py`, `app/api/v1/endpoints/auth/google.py`, `app/schemas/auth.py` |
| Leaks | `app/services/dashboard/service.py`, `app/platform/activity.py`, `app/db/models/enums.py`, `app/api/v1/endpoints/assessments.py` |
| Marker | `app/services/health_score/service.py` |
| Docs | `docs/fe-integration-guide-{auth-onboarding,google-sign-in,dashboard-role-leaks,health-live}.md`, checklist |

## Verification

- **Unit/API:** `tests/services/auth/test_sessions.py` (grace in/out, edges, revoked/expired/suspended, off switch), `tests/services/auth/test_sessions_concurrency.py` (real committing connections: fresh double-submit, concurrent in-grace replays, concurrent post-grace replays), `tests/api/auth/test_sessions_endpoints.py`, `test_review_hardening.py` (reuse tests now age the rotation), `tests/api/auth/test_google_signin.py` (consent recorded / refused / existing user / gate off), `tests/api/test_dashboard_summary_role_gates.py` (all 7 roles), `tests/api/test_dashboard_activity_roles.py`, `tests/platform/test_activity_visibility.py`, `tests/api/test_activity_wiring.py` (marker). 336 touched tests passed locally on scratch Postgres/Redis; CI runs the rest.
- **Live e2e** (scratch DB, both server shapes): `test_auth_onboarding_gaps.py` (grace journey on the CI shape; Google consent on the strict shape), `test_dashboard_role_leaks.py`, `test_dashboard_activity_roles.py`, `test_dashboard.py`, `test_dashboard_kpis.py`, `test_team_invitations.py` (marker), `test_google_sign_in.py`, `test_health_score.py` all green. New captures under `e2e/_captures/{auth_onboarding_gaps,dashboard_role_leaks,team_invitations}`; unrelated id churn from re-runs was reverted.
- Not exercised live: real Google consent screen; the 20 s production default (e2e runs 5 s); the concurrent paths are unit-tested only.

## Operate / roll back

- Config: `REFRESH_GRACE_SECONDS` (default 20, `0` disables). `SIGNUP_REQUIRE_TERMS` now also gates Google sign-up; **deploy order:** ship the FE change first (or set `SIGNUP_REQUIRE_TERMS=false`), otherwise first-time Google users land on `?error=terms_not_accepted` until the FE sends `accepted_terms`.
- Roll back: revert the PR (no migration, no data change).

## Follow-ups

- **FE:** send `accepted_terms: true` on the sign-up Google button and handle `terms_not_accepted`; treat `null` task KPIs / `assessment_complete` as "not available", not zero; stop assuming a second-tab refresh signs the user out.
- Google accounts created before this change still have no consent record (no backfill; they were never shown the checkbox).
- Not done on purpose (M2): the open Health Score still implies assessment completion to every role.
- The head-lock retry (10) in `_rotate_in_grace` is a bound, not a proof; the concurrency tests cover two callers, not N.
