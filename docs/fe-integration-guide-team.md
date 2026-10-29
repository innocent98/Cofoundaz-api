# FE Integration Guide: Team page (members, invitations, roles, audit, comments index)

Light version, by decision (Adebayo, 2026-10-29): the API covers the **designed** Team screens (Members, Roles, Activity, Comments, Audit log)
plus member removal, role change, leaving, invitation resend / revoke and Documents-grant cleanup. **Not built:** Team Tasks (PRD 23.1), the
Directory (23.5) and a per-module grants matrix (23.4): the PRD is overridden here on purpose. Every body below is pasted verbatim from
`e2e/test_team_manage.py` run against a live server (`e2e/_captures/team_manage/`); long lists are trimmed and marked. Emails in the captures are
the journey's throwaway addresses.

Base path `/api/v1/team`. Every route needs `Authorization: Bearer <token>` and `X-Workspace-Id`. Success bodies are `{"data": ..., "meta": null}`;
errors are `{"error": {"code", "message", "field_errors": []}}`.

## 0. Who can do what

| Route | Founder | Everyone else |
|---|---|---|
| `GET /members` | yes | yes (read-only; used by pickers) |
| `PATCH /members/{user_id}` (role), `DELETE /members/{user_id}` (remove) | yes | 403 `FORBIDDEN` |
| `POST /leave` | yes (not the last founder) | yes |
| `GET /invitations`, `POST /invitations`, `POST /invitations/{id}/resend`, `DELETE /invitations/{id}` | yes | 403 |
| `GET /audit`, `GET /audit/export` | yes | 403 |
| `GET /comments/threads` | yes | yes (only threads you wrote in or were mentioned in) |

`{user_id}` in the member routes is the person's **user id** (`user_id` in the list), not a membership id.

## 1. `GET /members`: the Members tab

Active members only, A to Z by name. **`name` can be `null`** (someone who signed up and never set a name): show the email instead. `joined_at`
is when they joined this workspace; `last_active_at` is their most recent sign-in (any workspace), or `null`. The row with your own `user_id` is "you".
Pending invitations are a separate list (section 3): render them as "invite pending" rows after the members.

```json
{
  "data": [
    {
      "user_id": "3584cf02-36a5-4657-92b0-e676e5a85523",
      "name": "Ada Founder",
      "email": "delivered+e2e-097d1ac440e8@resend.dev",
      "role": "founder",
      "joined_at": "2026-10-29T20:55:12.828374+00:00",
      "last_active_at": "2026-10-29T20:55:12.544740+00:00"
    },
    {
      "user_id": "65dbf6dd-5a07-4f6f-907e-1f6f4938cfd9",
      "name": null,
      "email": "delivered+e2e-29b959f2efbe@resend.dev",
      "role": "mentor",
      "joined_at": "2026-10-29T20:55:13.897938+00:00",
      "last_active_at": "2026-10-29T20:55:13.591280+00:00"
    },
    "... more members, same shape ..."
  ],
  "meta": null
}
```

## 2. Roles

`PATCH /members/{user_id}` body `{"role": "founder|team_member|mentor|accountant|legal_advisor|business_consultant"}` (the invite modal's list; `investor` is not
assignable). Founder only. Returns the new role. Toast copy from the PRD: "{name} is now {role}." Rules: you cannot change your own role
(409 `CANNOT_CHANGE_SELF`); an unknown or already-removed user, or one in another workspace, is 404 `NOT_FOUND`; `investor` is 422.

```json
{
  "data": {
    "user_id": "35f73327-7591-4703-9671-c187bb92f4d8",
    "role": "accountant"
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "CANNOT_CHANGE_SELF",
    "message": "You can't change your own role.",
    "field_errors": []
  }
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "role",
        "message": "Input should be 'founder', 'team_member', 'mentor', 'accountant', 'legal_advisor' or 'business_consultant'"
      }
    ]
  }
}
```

The effect is immediate: the person's next request is judged by the new role. **Their Documents grants are unchanged** (grants are per document).

## 3. Invitations

`POST /invitations` (existing, see `fe-integration-guide-team-invitations.md`) creates them. **`GET /invitations`** lists those still waiting, newest first; `expired: true`
means the 14 days ran out (it can still be re-sent). Accepted and revoked invitations are not listed.

```json
{
  "data": [
    {
      "id": "70442065-9b63-41b1-9e31-35af20c4c257",
      "email": "delivered+pending-909c0d29c317@resend.dev",
      "role": "accountant",
      "invited_by": "Ada Founder",
      "created_at": "2026-10-29T20:55:14.079742+00:00",
      "expires_at": "2026-11-12T20:55:14.090305+00:00",
      "expired": false
    }
  ],
  "meta": null
}
```

`POST /invitations/{id}/resend`: a new secret and a fresh 14 days; **the old link stops working**. `delivered: false` means the email failed
(show "Couldn't send the email, try again"); the new link exists either way. Rate limit 10 per minute. `DELETE /invitations/{id}` revokes it.
Resending or revoking one that was already accepted or revoked is **409 `INVITATION_NOT_PENDING`**; another workspace's id is 404.

```json
{
  "data": {
    "id": "70442065-9b63-41b1-9e31-35af20c4c257",
    "resent": true,
    "delivered": true
  },
  "meta": null
}
```
```json
{
  "data": {
    "id": "70442065-9b63-41b1-9e31-35af20c4c257",
    "revoked": true
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "INVITATION_NOT_PENDING",
    "message": "This invitation was already accepted or revoked.",
    "field_errors": []
  }
}
```

A non-founder gets 403:

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

**Inviting someone who was removed works**: when they accept, their old membership is reopened with the role they were just invited as (before this change the
accept succeeded but they stayed locked out). A suspended member is not reopened by an invitation.

## 4. Remove and leave

**`DELETE /members/{user_id}`** (founder): sets the membership to removed. **Effects, all at once:** the person loses every workspace route and the realtime
stream immediately; their **Documents grants in this workspace are revoked** (counted in `document_grants_revoked`); their past activity and comments stay attributed to them. Confirm copy from the PRD: "Remove {name}? They lose access immediately. Their past activity stays attributed."
You cannot remove yourself (409 `CANNOT_REMOVE_SELF`: use Leave).

```json
{
  "data": {
    "user_id": "35f73327-7591-4703-9671-c187bb92f4d8",
    "removed": true,
    "document_grants_revoked": 1
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
```json
{
  "error": {
    "code": "CANNOT_REMOVE_SELF",
    "message": "Use Leave workspace to remove yourself.",
    "field_errors": []
  }
}
```

**`POST /leave`** (any member): same effects for yourself. The **only** founder cannot leave: **409 `LAST_FOUNDER`**, "You are the only founder. Make someone else
a founder before you leave." After a successful leave, drop the workspace from the client (the next request is 403) and route to another workspace or the
sign-in screen.

```json
{
  "data": {
    "left": true,
    "document_grants_revoked": 0
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "LAST_FOUNDER",
    "message": "You are the only founder. Make someone else a founder before you leave.",
    "field_errors": []
  }
}
```

## 5. Audit tab (founder only)

`GET /audit?limit=30&cursor=` (limit 1 to 100). Newest first; `next_cursor` is opaque, `null` on the last page, and must be sent back unchanged.
Only workspace and team actions appear (never anything about the Journal). `actor` and `target` are display names (email when there is no name) or `null`.
Map `action` to words on the client:

| `action` | Text |
|---|---|
| `team.invitation.created` | "{actor} invited {target}" |
| `team.invitation.resent` | "{actor} re-sent the invitation to {target}" |
| `team.invitation.revoked` | "{actor} revoked the invitation for {target}" |
| `team.member.joined` | "{actor} joined" |
| `team.member.role_changed` | "{actor} changed {target}'s role" |
| `team.member.removed` | "{actor} removed {target}" |
| `team.member.left` | "{actor} left the workspace" |
| `workspace.updated` | "{actor} updated the workspace settings" |

The role a change set is not recorded (only that it changed): the audit log does not say "to what".

@@team_manage/audit_page1:audit@@

`GET /audit/export` downloads a CSV (`Content-Disposition: attachment; filename="team-audit-log.csv"`, newest 5,000 rows). Fetch it with the bearer header and save the
blob (do not navigate to it). Cells that start with `=`, `+`, `-` or `@` are prefixed with `'` so a spreadsheet cannot run them. The captured file
`e2e/_captures/team_manage/audit_export.csv` shows the exact columns: `when,actor,action,target`.

## 6. Comments tab

`GET /comments/threads?filter=all|mentions|unresolved`: one item per thread (an entity, plus its section for Business Builder canvases) that **you wrote
in or were mentioned in**, newest activity first (up to 30). `unresolved_count` is the number of open comments in the thread; `mentions_me` is true when any
comment mentions you. To open a thread use the existing endpoints (`GET /team/comments?entity_type=&entity_id=&anchor=` to read it,
`POST /team/comments` to reply, `POST /team/comments/{id}/resolve` to resolve). Bad filter is 422.

```json
{
  "data": [
    {
      "entity_type": "roadmap_milestone",
      "entity_id": "56ccc640-03c2-4cb2-8a10-4927cef7b5d5",
      "anchor": null,
      "context": "Validate demand",
      "comment_count": 1,
      "unresolved_count": 1,
      "mentions_me": true,
      "last_comment": {
        "author": "Ada Founder",
        "snippet": "Can you review the interview plan?",
        "created_at": "2026-10-29T20:55:13.967103+00:00"
      }
    }
  ],
  "meta": null
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "query.filter",
        "message": "String should match pattern '^(all|mentions|unresolved)$'"
      }
    ]
  }
}
```

## 7. Activity tab

Unchanged: `GET /dashboard/activity` (same source as the dashboard widget; role-filtered). See `fe-integration-guide-dashboard.md`.

## 8. Error codes

| Status | `error.code` | When |
|---|---|---|
| 403 | `FORBIDDEN` | not a founder on a founder-only route, or you are no longer a member |
| 404 | `NOT_FOUND` | member or invitation id unknown, removed, or in another workspace |
| 409 | `CANNOT_CHANGE_SELF`, `CANNOT_REMOVE_SELF`, `LAST_FOUNDER`, `INVITATION_NOT_PENDING` | rules above |
| 422 | `VALIDATION_ERROR` | `role` not assignable, bad `filter`, bad `cursor` (`field: cursor`) |
| 429 | `INVITE_LIMIT_REACHED` | `POST /invitations`, 50 per workspace per day |

## 9. Verification table

| Behaviour | Status |
|---|---|
| Members list fields, role change + rules, invitations list / resend / revoke, remove (lock-out + grant revoked), leave, last-founder guard, comments index, audit pages + CSV | verified live (captured) |
| A removed member invited back regains access | verified live (the journey re-invites and accepts) and unit-tested |
| Grants addressed by email only are also revoked | unit-tested only |
| `delivered: false` on resend when SMTP fails | unit-tested only |
| Resend of an expired invitation, formula-guarded CSV cells, scoping to other workspaces | unit-tested only |
| Realtime stream closing for a removed member | existing behaviour (the stream re-checks membership on a timer); not re-run by this journey |
