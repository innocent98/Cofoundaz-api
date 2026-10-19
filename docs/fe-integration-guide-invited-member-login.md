# FE integration guide: invited members and the onboarding state

**What it is:** a mentor, consultant, team member (any invited role) who logs in now lands in the workspace they
joined, not in founder onboarding. Before this fix `GET /onboarding/state` created a founder **draft workspace** for
them and returned `completed: false`, so `lib/auth/post-login.ts` routed every invited member to `/onboarding`.

Captures (local stack, 2026-10-19, mentor `bb-mentor-1@example.com` in NovaPay; ids are local throwaway data):
`e2e/_captures/invited_member_login/`.

## `GET /api/v1/auth/me` (behaviour change only)
An **untouched draft** (no name, onboarding not started) is left out of `memberships` when the user belongs to another
workspace, so `active_workspace_id` is the workspace they joined. A user whose only workspace is a draft still gets it.
Captured `me_mentor.json`, status 200:
```json
{"data": {"user": {"id": "91161181-…", "email": "bb-mentor-1@example.com", "status": "active"},
  "memberships": [{"startup_id": "11ba2aa0-8c8d-4035-adb8-62530988ed44", "name": "NovaPay", "role": "mentor"}],
  "active_workspace_id": "11ba2aa0-8c8d-4035-adb8-62530988ed44"}, "meta": null}
```

## `GET /api/v1/onboarding/state`: now reads `X-Workspace-Id`
| Caller | Response |
|---|---|
| Member (not creator) of the onboarded workspace in `X-Workspace-Id` | **member view** (below), `completed: true`; no draft is created |
| No header / header for a workspace they can't use, and they own **no** workspace but joined one | member view of the earliest joined workspace |
| Owns a workspace (draft or onboarded), header not a joined workspace | unchanged: their own workspace's full state |
| Belongs to nothing | unchanged: a draft is created, `step: 1`, `completed: false` |

Member view, captured `onboarding_state_mentor.json`, status 200 (same keys as the founder view; the founder's
answers and invite list are **not** shared, so `founder_profile` is `null` and the lists are empty):
```json
{"data": {"step": 6, "completed": true, "assessment_pending": false, "founder_profile": null,
  "startup": {"id": "11ba2aa0-8c8d-4035-adb8-62530988ed44", "name": "NovaPay"},
  "goals": [], "ai_panel": null, "notes": null, "invites": []}, "meta": null}
```
**Field-nesting trap:** `startup` in the member view has only `id` and `name` (no description, stage, …). Don't read
other `startup` fields from this endpoint for a member.

**FE consequence:** `completePostLogin` already stores `active_workspace_id` from `/auth/me` before asking for the
state, and `apiClient` sends it as `X-Workspace-Id`, so no FE change is required for login routing.

## Verification
| Behaviour | Live? | Source |
|---|---|---|
| Mentor login → `/dashboard` of NovaPay (was `/onboarding`) | ✅ | live browser, local stack |
| Member view shape | ✅ | `onboarding_state_mentor.json` |
| Stray draft hidden from `/auth/me` | ✅ | `me_mentor.json` (the mentor's stray draft from before the fix is gone from the list) |
| No draft created for a member; founder without a workspace still gets one; foreign header ignored | ❌ unit | `tests/api/onboarding/test_state_invited_members.py` |
