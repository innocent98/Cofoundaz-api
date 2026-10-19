# SOP: invited members no longer land in founder onboarding

| | |
|---|---|
| **What shipped** | `GET /onboarding/state` answers invited members with the workspace they joined; `/auth/me` hides untouched drafts. Branch `fix/invited-member-login`. |
| **Why** | Found in the Step 1 re-walk: every invited mentor/consultant/team member who logged in was sent to `/onboarding`. Root cause: `resolve_or_create_workspace` ignores the active workspace and **creates a founder draft** for anyone who doesn't own one, so the state read `completed: false`. All three local role accounts had such a stray draft, which also showed as a nameless workspace. |
| **How** | `joined_workspace()` (`app/services/onboarding/workspace.py`) finds an onboarded workspace the user joined (not created): the `X-Workspace-Id` one if they're in it, else the earliest joined when they own nothing. `get_state` returns `serialize_member_state()` for it (completed, founder answers and invites private) and never creates a draft. `/auth/me` drops `is_untouched_draft()` workspaces when the user has another one, so `active_workspace_id` points at the joined workspace. Rejected: deleting stray drafts with a migration (many FKs, and hiding them is enough; an untouched draft has no data). |
| **What's involved** | `app/api/v1/endpoints/onboarding/state.py`, `app/services/onboarding/workspace.py`, `app/api/v1/endpoints/auth/me.py`, `tests/api/onboarding/test_state_invited_members.py` (6), guide `docs/fe-integration-guide-invited-member-login.md`, captures `e2e/_captures/invited_member_login/`. |
| **Verification** | 6 new tests; onboarding + auth + migration tests 126 passed; full suite otherwise green (2355 passed; the 15 others were scratch-DB migration-state failures, re-run green). Live: mentor login now opens the NovaPay dashboard. |
| **Operate / roll back** | No migration or config. Revert to roll back (members would be routed to onboarding again). |
| **Follow-ups** | `PATCH /onboarding/state` still uses the owner rules (a member who wants their own startup creates a draft on purpose). The invite → signup → verify flow loses the `/invite/...` next path after email verification (FE, Team page). |
