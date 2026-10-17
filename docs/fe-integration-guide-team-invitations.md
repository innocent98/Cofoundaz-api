# FE Integration Guide — "+ Invite" (`POST /team/invitations`)

**Provenance.** Every body below is a live capture:
- **e2e** `e2e/test_team_invitations.py` (real uvicorn) → `e2e/_captures/team_invitations/*.json`.
- **Local docker stack**, 2026-10-17: the local test founder invited a mentor, and the mentor signed up and accepted →
  `e2e/_captures/team_invitations_live/*`. IDs and emails are from those throwaway runs.

## 1. What it is, in FE terms
The top bar's **"+ Invite"** (PRD §2.1, founder only) opens the invite modal (PRD 01.6 step 5: repeatable rows of
Email + Role). Until now **Send** said "coming soon", because the only invite endpoint was onboarding's, which
409s once onboarding is complete. This endpoint works at any time. The Team page's invite modal (PRD 23.4)
uses the same endpoint.

## 2. Request
`POST /api/v1/team/invitations`. Bearer + `X-Workspace-Id`, **founder only**.
```json
{"invites": [{"email": "mate@example.com", "role": "team_member"}]}
```
- `invites`: 1 to 20 rows.
- `role` ∈ `founder | team_member | mentor | accountant | legal_advisor | business_consultant`.

**Map the modal's labels to these values:** Co-Founder → `founder` (joins with full founder access), Team Member →
`team_member`, Mentor → `mentor`, Accountant → `accountant`, Legal Advisor → `legal_advisor`, Business Consultant →
`business_consultant`. `investor` is **not** accepted (422), because investors come in through Funding. The dashboard
modal currently sends the **label** ("Co-Founder"), which would 422, so map it.

## 3. Response: 200 (`invite_created_and_skipped.json`, local)
```json
{
    "data": {
        "created": [
            "throwaway-mentor+1792274931@example.com"
        ],
        "skipped": [
            "throwaway-founder@example.com"
        ]
    },
    "meta": null
}
```
`skipped` = already an active member, already has a pending invite, or repeated within the same request. It is
**not an error**. Tell the user, e.g. "Invited 1. Skipped 1 (already a member or already invited)." Show the
per-email lists. Don't show a generic success when everything was skipped.

## 4. Errors
| Status | Body (verbatim) | When |
|---|---|---|
| 422 | see below (`invite_validation_error.json`) | bad email, disallowed role, 0 or >20 rows. `field_errors[].field` is `invites.{row}.{email\|role}`, so highlight that row |
| 403 | `{"error": {"code": "FORBIDDEN", "message": "You don't have permission to do that.", "field_errors": []}}` | caller isn't a founder (`invite_by_mentor_forbidden.json`). Hide **+ Invite** for non-founders |
| 429 | `code: "INVITE_LIMIT_REACHED"`, `message: "You've sent a lot of invitations today. Try again tomorrow."` | more than **50 invitations per workspace per rolling 24h** (onboarding + "+ Invite" combined). Show the message and keep the form. ❌ unit test only (`test_daily_invite_cap_per_workspace`) |
| 429 | the app's standard rate-limit response | more than **10 requests/minute** from one user on this route. ❌ unit test only (`test_route_is_rate_limited_per_user`) |

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "invites.0.email",
        "message": "value is not a valid email address: An email address must have an @-sign."
      },
      {
        "field": "invites.0.role",
        "message": "Input should be 'founder', 'team_member', 'mentor', 'accountant', 'legal_advisor' or 'business_consultant'"
      }
    ]
  }
}
```

## 5. What the invitee gets (no FE change needed, but know it)
The email now has an **"Accept invitation"** button linking to `{APP_BASE_URL}/invite/{token}`. Before this change
it showed only a raw token and had no link at all. That's the FE's existing `app/invite/[token]` page, which calls:
- `GET /api/v1/invitations/{token}` (no auth), live `invite_preview.json`:
```json
{
    "data": {
        "startup_name": "NovaPay",
        "role": "mentor",
        "inviter_name": "Victor Founder",
        "email": "throwaway-mentor+1792274931@example.com",
        "status": "pending"
    },
    "meta": null
}
```
- `POST /api/v1/invitations/accept` `{"token": "…"}` (signed-in user whose email matches) → `invite_accepted.json`:
```json
{"data": {"startup_id": "80f32194-615c-4d48-bc32-219d94bcc41f", "role": "mentor"}, "meta": null}
```
- After acceptance the same preview link is a uniform 404 (`invite_preview_after_accept.txt`):
  `{"error":{"code":"NOT_FOUND","message":"Not found.","field_errors":[]}}`

The founder then gets an in-app notification, "A new member joined your workspace", and the dashboard activity
shows "A teammate joined the workspace" (verified live).

## 6. Traps / UX consequences
- **Map the labels to role values** (§2). Sending "Co-Founder" gets a 422.
- **Co-Founder = full access.** Say so in the modal, e.g. a hint under the role select: "Co-Founders get full access."
- **Invitee with no profile name:** a mentor who signed up but never onboarded has `greeting.first_name: ""` on
  `/dashboard/summary` (verified live). Render "Good evening." and never "Good evening, ."
- Invites expire after **14 days** (stated in the email).

## 7. Verification
| Behaviour | Live? | Source |
|---|---|---|
| Founder invites after onboarding; skip of an existing member | ✅ | e2e + local `invite_created_and_skipped.json` |
| 422 bad email / investor role | ✅ | `invite_validation_error.json`; local 422 |
| 403 non-founder | ✅ | `invite_by_mentor_forbidden.json` (e2e + local) |
| Email contains a working `/invite/{token}` link | ✅ | local console email; e2e mailbox extracts the token from the link |
| Preview → signup → accept → mentor restrictions on the dashboard | ✅ | local; e2e |
| Founder notified on join | ✅ | local notifications feed |
| >20 rows / empty list | ❌ unit test only | `tests/api/test_team_invitations.py::test_validation` |
| HTML-escaping of workspace/inviter names in the email | ❌ unit test only | `test_invitation_email_escapes_user_supplied_names` |
