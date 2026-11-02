# FE integration guide: Admin slice D (feature flags)

Every body below is pasted from a live run against a real server: `e2e/test_admin_flags.py`, captures in `e2e/_captures/admin_flags/<name>.json`. Ids, emails and keys are throwaway values from that run (placeholders). Base path `/api/v1`. Success bodies are `{"data": ..., "meta": ...}`; errors are `{"error": {"code", "message", "field_errors"}}`, **except the hidden 404, which is `{"detail": "Not Found"}`**. Where a capture was trimmed it says so.

## What this is, in FE terms

Two audiences:

1. **Super Admin console, Feature flags tab** (replaces the mock): list flags, create one, change its on/off, rollout %, stage, text and workspace allow-list. Super Admin only; an Admin gets the hidden 404.
2. **The app itself**: one read call returns which flags are on for the current workspace.

How a workspace gets a flag: the flag must be `enabled`, and then either the workspace id is on `workspace_allowlist`, or its stable bucket (0 to 99, from a hash of key + workspace id) is below `rollout_pct`. Raising the percentage only ever **adds** workspaces; nobody flips off and on. `enabled: false` is the kill switch: off for everyone, allow-list included. `stage` (`beta` / `ga` / `internal`) is a **label for the badge only**; it changes nobody's access. There is no targeting by plan or stage (decision P7b: bucketing + allow-list only).

## Endpoints

Super Admin routes need `Authorization: Bearer <token>`; every mutation also needs `reason` (10 to 500 chars) and `totp_code` (fresh, single use; see the staff guide, "Step-up").

### `GET /super-admin/flags`

`list_trimmed_to_this_run` (the capture is trimmed to this run's two flags; the real list holds every flag, oldest first):

```json
{"data":{"flags":[{"id":"aab3b0a1-8e8e-4e01-9b1d-240737b0a6a5","key":"e2e_voice_ed11f57f","name":"AI voice notes","description":"Dictate journal entries and mission updates.","stage":"ga","enabled":true,"rollout_pct":0,"workspace_allowlist":["981dea5d-651e-41ca-9fee-89d5e86a43b2"],"affected_workspaces":1,"created_at":"2026-10-31T14:50:30.505453+00:00","updated_at":"2026-10-31T14:52:30.254098+00:00"},{"id":"b7871ed1-d411-4e5b-a8f6-d401784ade3c","key":"e2e_board_c2d3d121","name":"Roadmap Kanban v2","description":"","stage":"internal","enabled":false,"rollout_pct":0,"workspace_allowlist":[],"affected_workspaces":0,"created_at":"2026-10-31T14:55:00.110705+00:00","updated_at":"2026-10-31T14:55:00.110705+00:00"}],"total_workspaces":4},"meta":null}
```

`affected_workspaces` is the number of live workspaces that have the flag right now; `total_workspaces` is all live workspaces. Show "This affects ~{affected_workspaces} of {total_workspaces} workspaces." The mock's card maps to: badge = `stage`, toggle = `enabled`, buttons = `rollout_pct`.

### `GET /super-admin/flags/{key}/impact?enabled=&rollout_pct=`

Preview before a change; both query params optional (omitted = keep current). `impact_preview` (enable at 100 on a fresh flag):

```json
{"data":{"key":"e2e_voice_ed11f57f","total_workspaces":2,"affected_now":0,"affected_after":2},"meta":null}
```

Use it for the confirm dialog ("This affects ~{affected_after} workspaces."). Unknown key: 404 `FLAG_NOT_FOUND`; `rollout_pct` outside 0 to 100: 422. **Not captured live**: those two refusals (covered by unit tests; same shapes as the other errors below).

### `POST /super-admin/flags` (201)

Body: `key` (`^[a-z][a-z0-9_]{2,59}$`, never changes), `name` (2 to 120), `description` (up to 500, default `""`), `stage`, `enabled` (default `false`), `rollout_pct` (0 to 100, default 0), `workspace_allowlist` (up to 200 workspace ids, default `[]`), `reason`, `totp_code`. `create_ok`:

```json
{"data":{"id":"aab3b0a1-8e8e-4e01-9b1d-240737b0a6a5","key":"e2e_voice_ed11f57f","name":"AI voice notes","description":"Dictate journal entries and mission updates.","stage":"beta","enabled":false,"rollout_pct":0,"workspace_allowlist":[],"affected_workspaces":0,"created_at":"2026-10-31T14:50:30.505453+00:00","updated_at":"2026-10-31T14:50:30.505453+00:00"},"meta":null}
```

The flag-shaped body is the same on every response below. Errors, as returned:

| Case (capture) | Status | Body |
|---|---|---|
| Bad key (`create_bad_key`) | 422 | `{"error":{"code":"VALIDATION_ERROR","message":"Please check the highlighted fields.","field_errors":[{"field":"key","message":"String should match pattern '^[a-z][a-z0-9_]{2,59}$'"}]}}` |
| Rollout over 100 (`create_rollout_out_of_range`) | 422 | `field_errors:[{"field":"rollout_pct","message":"Input should be less than or equal to 100"}]` |
| Allow-list id isn't a live workspace (`create_unknown_workspace`) | 422 | `{"error":{"code":"UNKNOWN_WORKSPACE","message":"No such workspace: 289107e2-e85a-4036-a27f-8a6b85163506.","field_errors":[]}}` |
| Wrong or reused code (`create_wrong_code`) | 403 | `{"error":{"code":"STEP_UP_INVALID","message":"That authenticator code isn't right, or was already used. Enter a new one.","field_errors":[]}}` |
| Key taken (`create_duplicate`) | 409 | `{"error":{"code":"FLAG_KEY_EXISTS","message":"A flag with that key already exists.","field_errors":[]}}` |

### `PATCH /super-admin/flags/{key}`

Send **only the fields that change** (`name`, `description`, `stage`, `enabled`, `rollout_pct`, `workspace_allowlist`) plus `reason` and `totp_code`. Omitted = unchanged. `workspace_allowlist` **replaces** the whole list. Returns the flag. `update_kill_switch` (`{"enabled": false}`):

```json
{"data":{"id":"aab3b0a1-8e8e-4e01-9b1d-240737b0a6a5","key":"e2e_voice_ed11f57f","name":"AI voice notes","description":"Dictate journal entries and mission updates.","stage":"ga","enabled":false,"rollout_pct":100,"workspace_allowlist":[],"affected_workspaces":0,"created_at":"2026-10-31T14:50:30.505453+00:00","updated_at":"2026-10-31T14:52:00.074510+00:00"},"meta":null}
```

Note the percentage stays at 100: turning a flag off does not reset it, so turning it back on restores the old rollout. A "kill switch" button is `enabled:false`. Other captured responses: `update_enable_100` (`enabled:true, rollout_pct:100, stage:"ga"`) and `update_allowlist_only` (`enabled:true, rollout_pct:0, workspace_allowlist:[<id>]` gave `affected_workspaces: 1`).

| Case (capture) | Status | Body |
|---|---|---|
| Nothing actually changes (`update_no_change`) | 422 | `{"error":{"code":"NO_CHANGE","message":"Those are already this flag's settings.","field_errors":[]}}` |
| `null` for a field (`update_null_field`) | 422 | `field_errors:[{"field":"","message":"Value error, rollout_pct can't be null; leave it out to keep it."}]` (note `field` is empty) |
| Sending `key` (`update_key_is_immutable`) | 422 | `field_errors:[{"field":"key","message":"Extra inputs are not permitted"}]` |
| Unknown key (`update_unknown_flag`) | 404 | `{"error":{"code":"FLAG_NOT_FOUND","message":"No flag with that key.","field_errors":[]}}` |

An allow-list id that does not exist gives 422 `UNKNOWN_WORKSPACE` (same as create). Only ids you **add** are checked, so a workspace deleted since it was listed doesn't block saving; it keeps counting for nothing until removed. **Not captured live**: that deleted-workspace case, and a PATCH with no field at all (422 `NO_CHANGE`, `"Send at least one field to change."`); both covered by unit tests.

### Hidden 404

Signed out or not a Super Admin (including an Admin) gets `{"detail":"Not Found"}` on every `/super-admin/flags` route (`list_anonymous_404`, `list_founder_404`). Do not read `error.code`.

### `GET /flags` (the app, any workspace member)

Headers: `Authorization`, `X-Workspace-Id`. Returns **only the flags that are on** for that workspace; a key that is absent means off. `app_flags_on_trimmed_to_this_run` (trimmed to this run's key; the real body lists every flag that is on):

```json
{"data":{"flags":{"e2e_voice_ed11f57f":true}},"meta":null}
```

- Treat a missing key as `false`. Never branch on `flags[key] === false`.
- Not a member of that workspace: 403 `FORBIDDEN` (`app_flags_not_a_member`). No token: 401 `UNAUTHORIZED` (`app_flags_no_auth`).
- Fetch once per workspace load and again when the workspace switches; changes made by staff show on the next fetch (no push). The result for a workspace is stable between fetches unless staff change the flag.

## Audit

Each create and update writes one audit row (`flag.created`, `flag.updated`) with the reason, readable in the staff audit explorer with `?action=flag.`. `audit_for_this_flag` (trimmed to this flag; newest first) shows `details.from` / `details.to` holding **only the changed fields**, `details.key`, and for create `details.to` as the full snapshot. Add `flag.` to the "privileged actions" prefixes for the Overview tile.

## UX the FE must surface

- **A change is live at once for real users.** Always confirm with the blast radius from `/impact` and show the reason field.
- **Every save costs one authenticator code**, and the code is spent even when the save is refused afterwards (duplicate key, unknown workspace, no change). After a refusal the user may need to wait for the next 30 s code. Say so on the error.
- **Two saves within one 30 s window** need two different codes (see the staff guide).
- Raising rollout adds workspaces, lowering removes the newest-bucketed ones; the percentages are not per-user.
- `stage: internal` is only a badge. Do not tell staff it limits who sees the flag; use the allow-list for that.

## Verification table

| Behaviour | Verified |
|---|---|
| Hidden 404 for anonymous and founder | live |
| Create (ok, bad key, rollout out of range, unknown workspace, wrong code, duplicate) | live |
| Update: enable at 100, kill switch, allow-list only, no-change, null field, key rejected, unknown flag | live |
| Impact preview | live (success); refusals unit-tested only |
| List with `affected_workspaces` / `total_workspaces` | live |
| `GET /flags`: on-only map, non-member 403, no token 401 | live |
| Same workspace gets the same answer three times in a row | live (asserted in the journey) |
| Audit rows with before/after | live |
| Admin (scoped) gets 404 | unit-tested only |
| Deleted workspace on the allow-list, empty PATCH, impact refusals | unit-tested only |
| Rollout fairness (about 10% for 10%) | unit-tested only |
