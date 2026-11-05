# FE integration guide: Admin Reported issues (founder "report this" + staff Reports tab)

Every body below is pasted from a live run against a real server: `e2e/test_admin_reports.py`, captures in `e2e/_captures/admin_reports/<name>.json`. Ids, emails and names are throwaway values from that run (placeholders). Base path `/api/v1`. Success bodies are `{"data": ..., "meta": ...}`; errors are `{"error": {"code", "message", "field_errors"}}`, **except the hidden 404 on staff routes, which is `{"detail": "Not Found"}`**. Long lists are trimmed to their first item and say so.

The AI replies in the captures read `[stub-llm] ...` because the e2e server runs the stub model; real replies are real text.

## What this is, in FE terms

1. **Report this (founder app).** Any workspace member can report **one AI Co-Founder reply** or **one comment**, with a reason and an optional note. It lives as a small "Report" action on each AI reply and each comment. After reporting, mark that item "Reported" using `GET /reports/mine`.
2. **Reports tab (Admin console, scope `moderation`; Super Admin too).** A queue with tabs Open / Escalated / Closed, filters by type and reason, per-page search. Each report shows only the snippet the founder reported. Actions: dismiss, escalate, remove content, restore.
3. **Removed content.** When staff remove an item, the author (and everyone who can see it) gets it back with `removed_by_moderation: true` and the text replaced by `Removed by moderation`. It is a soft hide and can be restored.

Marketplace slice B added `listing` and `provider` as reportable types (see `docs/fe-integration-guide-marketplace-listings.md`); `review` is accepted by the schema but answers 404 until Marketplace reviews exist. In this guide's journey the report types are `ai_message` and `comment`.

## Product calls (R1 to R12, defaults set by the build session; Adebayo may change them)

| # | Call |
|---|---|
| R1 | Reasons: `spam`, `abusive`, `harmful`, `inaccurate`, `privacy`, `other`. `other` requires a note. Note max 500 chars. |
| R2 | Reportable: AI Co-Founder assistant replies (only in your own conversations) and comments (not document comments). |
| R3 | You cannot report your own comment. One live (open or escalated) report per person per item; you can report again once it is closed. 10 reports per person per hour. |
| R4 | The snippet (max 2000 chars) is copied when the report is filed and kept even if the item is later edited or deleted. |
| R5 | Staff see the snippet, reason, note, reporter name and email, the author's **name only** (no email), and the workspace name and plan. Nothing else. No route reads the chat or comment thread (D-ADM-9). |
| R6 | Statuses: `open`, `escalated`, `dismissed`, `removed`, `restored`. Tabs: Open = open, Escalated = escalated, Closed = dismissed + removed + restored. |
| R7 | A moderator may dismiss, remove or escalate an **open** report. Only a Super Admin may decide an **escalated** one (a moderator gets 403 `SUPER_ADMIN_ONLY`). The same applies to removing an item that has an escalated report on it, and to restoring a removal that closed an escalated report. |
| R8 | Remove closes **all** live reports on the same item as `removed`. Dismiss closes only that report. |
| R9 | Remove and restore need a fresh authenticator code (`totp_code`) because they change what an author sees. Dismiss and escalate need only a reason and send **no** code (same exception as Tickets, Adebayo 2026-11-02). Every action writes an audit row. |
| R10 | Restore undoes a removal (reason + code) and sets the report(s) to `restored`. |
| R11 | The author sees the removed comment or AI reply as `Removed by moderation`. No notification yet. |
| R12 | Founder routes: `POST /reports`, `GET /reports/mine`. Staff routes: `/admin/reports...`. |

## Founder endpoints (need `Authorization` and `X-Workspace-Id`)

### `POST /reports` (201)

Body: `{"item_type": "ai_message" | "comment" | "listing" | "provider", "item_id": uuid, "reason": spam|abusive|harmful|inaccurate|privacy|other, "note": optional, 1-500 chars}`. Any other field is a 422. `item_id` is the id the FE already has: the AI message `id` from `GET /ai/conversations/{id}`, or the comment `id` from `GET /team/comments`. The response never reveals what staff decide: `status` is always `received`.


`report_comment_ok` (201):
```json
{
  "data": {
    "id": "e086ac18-fb11-47b9-be02-24e5a732c6f2",
    "item_type": "comment",
    "item_id": "b86a41c8-0fb1-45ee-815a-f48e3e0551c3",
    "reason": "abusive",
    "status": "received",
    "created_at": "2026-11-02T14:31:30.166167+00:00"
  },
  "meta": null
}
```

`report_ai_message_a_ok` (201):
```json
{
  "data": {
    "id": "89e488de-2e3a-4761-9e4e-d437094ce4a0",
    "item_type": "ai_message",
    "item_id": "a5d17cae-15d2-4fd0-b26d-ccdb1387ff9c",
    "reason": "inaccurate",
    "status": "received",
    "created_at": "2026-11-02T14:31:30.200966+00:00"
  },
  "meta": null
}
```

**Errors the FE must handle**

`report_bad_item_type` (422):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "item_type",
        "message": "Input should be 'ai_message', 'comment', 'listing', 'provider' or 'review'"
      }
    ]
  }
}
```

`report_bad_reason` (422):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "reason",
        "message": "Input should be 'spam', 'abusive', 'harmful', 'inaccurate', 'privacy' or 'other'"
      }
    ]
  }
}
```

`report_other_needs_note` (422):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "Value error, Tell us what's wrong when the reason is 'other'."
      }
    ]
  }
}
```

`report_extra_field` (422):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "x",
        "message": "Extra inputs are not permitted"
      }
    ]
  }
}
```

`report_no_auth` (401):
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

`report_own_comment_422` (422):
```json
{
  "error": {
    "code": "OWN_COMMENT",
    "message": "You can't report your own comment.",
    "field_errors": []
  }
}
```

`report_other_workspace_404` (404):
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

`report_duplicate_409` (409):
```json
{
  "error": {
    "code": "ALREADY_REPORTED",
    "message": "You've already reported this. We'll take a look.",
    "field_errors": []
  }
}
```

Notes:
- **`other` without a note** comes back with `"field": ""` (a whole-form error, not on `note`). Show its `message` next to the note box, or just make the note required when `other` is picked so the user never hits it. (verified-live)
- `404 NOT_FOUND` covers every case where the item isn't yours to report: another workspace's item, another person's AI chat, one of your own prompts (only assistant replies are reportable), a reply that failed or is still generating, a document comment, an item staff already removed, or an unknown id. The FE can't tell these apart and shouldn't try.
- `429 RATE_LIMITED` after 10 reports in an hour has the message "You've sent several reports recently. Please wait a little before sending another." It is covered by an API test and **not exercised in the live run** (it would flood the shared e2e database).

### `GET /reports/mine`

Your reports that are still with staff (open or escalated), newest first, up to 200. Use it to mark items "Reported" and to disable the Report action on them. A report drops off this list once staff decide it.


`mine` (200):
```json
{
  "data": {
    "reports": [
      {
        "id": "51de0fac-ab33-414b-9fea-0b324b613117",
        "item_type": "ai_message",
        "item_id": "ef27fd03-5af5-4787-89ff-47fffead343c",
        "reason": "harmful",
        "status": "received",
        "created_at": "2026-11-02T14:31:30.221510+00:00"
      },
      {
        "id": "89e488de-2e3a-4761-9e4e-d437094ce4a0",
        "item_type": "ai_message",
        "item_id": "a5d17cae-15d2-4fd0-b26d-ccdb1387ff9c",
        "reason": "inaccurate",
        "status": "received",
        "created_at": "2026-11-02T14:31:30.200966+00:00"
      },
      {
        "id": "e086ac18-fb11-47b9-be02-24e5a732c6f2",
        "item_type": "comment",
        "item_id": "b86a41c8-0fb1-45ee-815a-f48e3e0551c3",
        "reason": "abusive",
        "status": "received",
        "created_at": "2026-11-02T14:31:30.166167+00:00"
      }
    ]
  },
  "meta": null
}
```

`mine_after_decisions` (200 after all three were decided):
```json
{
  "data": {
    "reports": []
  },
  "meta": null
}
```

## What the author sees after a removal

The existing endpoints return two new facts on every comment and every AI message: `removed_by_moderation` (boolean, always present) and, when it is `true`, the text replaced by `Removed by moderation` (`body` for a comment, `content` for an AI reply; a removed comment's `mention_user_ids` is `[]`). Render a muted "Removed by moderation" row and hide edit, reply and report actions on it. The comments threads index (`GET /team/comments/threads`) uses the same placeholder in its `snippet`. **This applies to everyone who can see the item, not only the author.** For an **AI reply** that means the removal also hides it from the conversation owner's own view (the person who reported it is that owner, since AI chats are private to one person): their chat shows `Removed by moderation` in its place. The author of a comment never learns who reported it: no comment, thread or report route returns reporter information to anyone but staff.



`ai_conversation_after_removal` (200 after the second reply was removed):
```json
{
  "data": {
    "id": "1ffd1deb-e585-453e-a6af-6f3c81a0a1cd",
    "title": "How long is my runway?",
    "created_at": "2026-11-02T14:31:28.303416+00:00",
    "updated_at": "2026-11-02T14:31:30.024901+00:00",
    "messages": [
      {
        "id": "6138438b-969a-4cb4-aaf5-d418271532fd",
        "role": "user",
        "status": "ready",
        "content": "How long is my runway?",
        "removed_by_moderation": false,
        "created_at": "2026-11-02T14:31:28.315811+00:00"
      },
      {
        "id": "a5d17cae-15d2-4fd0-b26d-ccdb1387ff9c",
        "role": "assistant",
        "status": "ready",
        "content": "[stub-llm] AI-generated assessment narrative.",
        "removed_by_moderation": false,
        "created_at": "2026-11-02T14:31:28.316811+00:00"
      },
      {
        "id": "98235b78-ceb0-413e-8ed2-220bec4e74a7",
        "role": "user",
        "status": "ready",
        "content": "And what should I charge?",
        "removed_by_moderation": false,
        "created_at": "2026-11-02T14:31:30.023901+00:00"
      },
      {
        "id": "ef27fd03-5af5-4787-89ff-47fffead343c",
        "role": "assistant",
        "status": "ready",
        "content": "Removed by moderation",
        "removed_by_moderation": true,
        "created_at": "2026-11-02T14:31:30.024901+00:00"
      }
    ]
  },
  "meta": null
}
```

`comment_thread_author_after_removal` (200 after the comment was removed):
```json
{
  "data": [
    {
      "id": "b86a41c8-0fb1-45ee-815a-f48e3e0551c3",
      "entity_type": "roadmap_milestone",
      "entity_id": "65777ccc-b5f9-4b9f-9721-bf49e3820d6f",
      "anchor": null,
      "author": {
        "id": "c35089c7-11f6-4301-a3de-cd65cb0691d7",
        "name": null
      },
      "body": "Removed by moderation",
      "removed_by_moderation": true,
      "mention_user_ids": [],
      "created_at": "2026-11-02T14:31:28.281642+00:00",
      "edited_at": null,
      "resolved_at": null,
      "is_mine": true
    }
  ],
  "meta": null
}
```

A removed comment cannot be edited back into view:


`comment_edit_after_removal_403` (403):
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

`comment_thread_author_after_restore` (200 after a Super Admin restored it):
```json
{
  "data": [
    {
      "id": "b86a41c8-0fb1-45ee-815a-f48e3e0551c3",
      "entity_type": "roadmap_milestone",
      "entity_id": "65777ccc-b5f9-4b9f-9721-bf49e3820d6f",
      "anchor": null,
      "author": {
        "id": "c35089c7-11f6-4301-a3de-cd65cb0691d7",
        "name": null
      },
      "body": "Your launch plan is hopeless and so are you.",
      "removed_by_moderation": false,
      "mention_user_ids": [],
      "created_at": "2026-11-02T14:31:28.281642+00:00",
      "edited_at": null,
      "resolved_at": null,
      "is_mine": true
    }
  ],
  "meta": null
}
```

## Staff endpoints (Admin console)

Every route is under `/admin/reports`, scope `moderation`, Super Admin included. Anyone who is not active staff (including a founder) gets the hidden 404; staff without the scope get 403. There is no workspace header.


`staff_list_founder_404` (404 for a founder):
```json
{
  "detail": "Not Found"
}
```

`staff_list_wrong_scope_403` (403 for a support-only Admin):
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

### `GET /admin/reports/counts`

Tab badges.


`counts` (200):
```json
{
  "data": {
    "open": 3,
    "escalated": 0,
    "closed": 6
  },
  "meta": null
}
```

### `GET /admin/reports`

Query: `tab` = `open` (default) | `escalated` | `closed`; `type` = `ai_message` | `comment`; `reason`; `query` (per-page search, max 120 chars: matches the snippet, the workspace name, and the reporter's name or email); `limit` 1-100 (default 25); `cursor` (opaque, from `meta.next_cursor`). Newest report first. An unknown `tab`, `type` or `reason`, or a bad `cursor`, is a 422.

List rows carry `snippet` cut to 200 chars with `snippet_truncated` telling you if it was cut; the detail has the full text.


`list_open_search_workspace` (200):
```json
{
  "data": {
    "reports": [
      {
        "id": "51de0fac-ab33-414b-9fea-0b324b613117",
        "item_type": "ai_message",
        "reason": "harmful",
        "note": "Wrong figures",
        "status": "open",
        "snippet": "[stub-llm] AI-generated assessment narrative.",
        "snippet_truncated": false,
        "reporter": {
          "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
          "name": "Ada Obi",
          "email": "delivered+e2e-bb3da4790da4@resend.dev"
        },
        "author": null,
        "workspace": {
          "id": "7557040c-f857-4dfc-a796-bd32953acb81",
          "name": "Report Co",
          "plan_key": "starter"
        },
        "created_at": "2026-11-02T14:31:30.221510+00:00",
        "escalated_at": null,
        "decided_at": null
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```
(list trimmed to its first item; the live response had 3)

`list_filtered_type_reason` (200):
```json
{
  "data": {
    "reports": [
      {
        "id": "e086ac18-fb11-47b9-be02-24e5a732c6f2",
        "item_type": "comment",
        "reason": "abusive",
        "note": "Personal attack",
        "status": "open",
        "snippet": "Your launch plan is hopeless and so are you.",
        "snippet_truncated": false,
        "reporter": {
          "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
          "name": "Ada Obi",
          "email": "delivered+e2e-bb3da4790da4@resend.dev"
        },
        "author": {
          "id": "c35089c7-11f6-4301-a3de-cd65cb0691d7",
          "name": "delivered+e2e-a8874c9c7471@resend.dev"
        },
        "workspace": {
          "id": "7557040c-f857-4dfc-a796-bd32953acb81",
          "name": "Report Co",
          "plan_key": "starter"
        },
        "created_at": "2026-11-02T14:31:30.166167+00:00",
        "escalated_at": null,
        "decided_at": null
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```

`list_closed_tab` (200):
```json
{
  "data": {
    "reports": [
      {
        "id": "89e488de-2e3a-4761-9e4e-d437094ce4a0",
        "item_type": "ai_message",
        "reason": "inaccurate",
        "note": "Wrong figures",
        "status": "dismissed",
        "snippet": "[stub-llm] AI-generated assessment narrative.",
        "snippet_truncated": false,
        "reporter": {
          "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
          "name": "Ada Obi",
          "email": "delivered+e2e-bb3da4790da4@resend.dev"
        },
        "author": null,
        "workspace": {
          "id": "7557040c-f857-4dfc-a796-bd32953acb81",
          "name": "Report Co",
          "plan_key": "starter"
        },
        "created_at": "2026-11-02T14:31:30.200966+00:00",
        "escalated_at": null,
        "decided_at": "2026-11-02T14:31:30.351805+00:00"
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```
(list trimmed to its first item; the live response had 7)

`list_escalated_tab` (200):
```json
{
  "data": {
    "reports": [
      {
        "id": "e086ac18-fb11-47b9-be02-24e5a732c6f2",
        "item_type": "comment",
        "reason": "abusive",
        "note": "Personal attack",
        "status": "escalated",
        "snippet": "Your launch plan is hopeless and so are you.",
        "snippet_truncated": false,
        "reporter": {
          "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
          "name": "Ada Obi",
          "email": "delivered+e2e-bb3da4790da4@resend.dev"
        },
        "author": {
          "id": "c35089c7-11f6-4301-a3de-cd65cb0691d7",
          "name": "delivered+e2e-a8874c9c7471@resend.dev"
        },
        "workspace": {
          "id": "7557040c-f857-4dfc-a796-bd32953acb81",
          "name": "Report Co",
          "plan_key": "starter"
        },
        "created_at": "2026-11-02T14:31:30.166167+00:00",
        "escalated_at": "2026-11-02T14:31:30.394716+00:00",
        "decided_at": null
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```

### `GET /admin/reports/{id}`

The full report. **Nesting traps:** `author` is an object with only `id` and `name` (never an email) and is `null` for an AI reply (it has no human author); `reporter` has `email` too; `escalation_reason`, `decision_reason`, `escalated_by` and `decided_by` only exist on the detail, not on list rows; `reports_on_item` counts how many people reported the same item (a number only: no other report's text). An unknown id is a 404 `NOT_FOUND`.

**The Reports tab shows nothing but `snippet`.** There is no link to the chat, thread or document, and no route returns one. Do not build a "view in context" control.


`detail_comment` (200):
```json
{
  "data": {
    "id": "e086ac18-fb11-47b9-be02-24e5a732c6f2",
    "item_type": "comment",
    "reason": "abusive",
    "note": "Personal attack",
    "status": "open",
    "snippet": "Your launch plan is hopeless and so are you.",
    "snippet_truncated": false,
    "reporter": {
      "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
      "name": "Ada Obi",
      "email": "delivered+e2e-bb3da4790da4@resend.dev"
    },
    "author": {
      "id": "c35089c7-11f6-4301-a3de-cd65cb0691d7",
      "name": "delivered+e2e-a8874c9c7471@resend.dev"
    },
    "workspace": {
      "id": "7557040c-f857-4dfc-a796-bd32953acb81",
      "name": "Report Co",
      "plan_key": "starter"
    },
    "created_at": "2026-11-02T14:31:30.166167+00:00",
    "escalated_at": null,
    "decided_at": null,
    "escalation_reason": null,
    "decision_reason": null,
    "escalated_by": null,
    "decided_by": null,
    "reports_on_item": 1
  },
  "meta": null
}
```

`detail_ai_message` (200):
```json
{
  "data": {
    "id": "89e488de-2e3a-4761-9e4e-d437094ce4a0",
    "item_type": "ai_message",
    "reason": "inaccurate",
    "note": "Wrong figures",
    "status": "open",
    "snippet": "[stub-llm] AI-generated assessment narrative.",
    "snippet_truncated": false,
    "reporter": {
      "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
      "name": "Ada Obi",
      "email": "delivered+e2e-bb3da4790da4@resend.dev"
    },
    "author": null,
    "workspace": {
      "id": "7557040c-f857-4dfc-a796-bd32953acb81",
      "name": "Report Co",
      "plan_key": "starter"
    },
    "created_at": "2026-11-02T14:31:30.200966+00:00",
    "escalated_at": null,
    "decided_at": null,
    "escalation_reason": null,
    "decision_reason": null,
    "escalated_by": null,
    "decided_by": null,
    "reports_on_item": 1
  },
  "meta": null
}
```

## Actions

Each action returns the full updated report (same shape as the detail). All need a `reason` of 10-500 chars; any other field is a 422.

| Action | Route | Body | Who |
|---|---|---|---|
| Dismiss | `POST /admin/reports/{id}/dismiss` | `{"reason"}` | moderation on an open report; Super Admin on open or escalated |
| Escalate | `POST /admin/reports/{id}/escalate` | `{"reason"}` | moderation or Super Admin, on an open report |
| Remove content | `POST /admin/reports/{id}/remove` | `{"reason", "totp_code"}` | as dismiss |
| Restore | `POST /admin/reports/{id}/restore` | `{"reason", "totp_code"}` | moderation, or Super Admin if an escalated report was involved; only on a `removed` report |

`totp_code` is the staff member's own 6-digit authenticator code, **single use** (a code that was just used is refused, so ask for a new one if a retry is needed).


`dismiss_reason_too_short` (422):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "reason",
        "message": "String should have at least 10 characters"
      }
    ]
  }
}
```

`dismiss` (200):
```json
{
  "data": {
    "id": "89e488de-2e3a-4761-9e4e-d437094ce4a0",
    "item_type": "ai_message",
    "reason": "inaccurate",
    "note": "Wrong figures",
    "status": "dismissed",
    "snippet": "[stub-llm] AI-generated assessment narrative.",
    "snippet_truncated": false,
    "reporter": {
      "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
      "name": "Ada Obi",
      "email": "delivered+e2e-bb3da4790da4@resend.dev"
    },
    "author": null,
    "workspace": {
      "id": "7557040c-f857-4dfc-a796-bd32953acb81",
      "name": "Report Co",
      "plan_key": "starter"
    },
    "created_at": "2026-11-02T14:31:30.200966+00:00",
    "escalated_at": null,
    "decided_at": "2026-11-02T14:31:30.351805+00:00",
    "escalation_reason": null,
    "decision_reason": "Figures match the workspace data; not an error",
    "escalated_by": null,
    "decided_by": {
      "id": "3f38ef34-9c57-42a9-9b1f-6cdb54c284ee",
      "name": "delivered+e2e-fa1913781717@resend.dev",
      "email": "delivered+e2e-fa1913781717@resend.dev"
    },
    "reports_on_item": 1
  },
  "meta": null
}
```

`dismiss_closed_409` (409):
```json
{
  "error": {
    "code": "REPORT_CLOSED",
    "message": "This report is already closed.",
    "field_errors": []
  }
}
```

`escalate` (200):
```json
{
  "data": {
    "id": "e086ac18-fb11-47b9-be02-24e5a732c6f2",
    "item_type": "comment",
    "reason": "abusive",
    "note": "Personal attack",
    "status": "escalated",
    "snippet": "Your launch plan is hopeless and so are you.",
    "snippet_truncated": false,
    "reporter": {
      "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
      "name": "Ada Obi",
      "email": "delivered+e2e-bb3da4790da4@resend.dev"
    },
    "author": {
      "id": "c35089c7-11f6-4301-a3de-cd65cb0691d7",
      "name": "delivered+e2e-a8874c9c7471@resend.dev"
    },
    "workspace": {
      "id": "7557040c-f857-4dfc-a796-bd32953acb81",
      "name": "Report Co",
      "plan_key": "starter"
    },
    "created_at": "2026-11-02T14:31:30.166167+00:00",
    "escalated_at": "2026-11-02T14:31:30.394716+00:00",
    "decided_at": null,
    "escalation_reason": "Targeted abuse of a named person; needs a Super Admin call",
    "decision_reason": null,
    "escalated_by": {
      "id": "3f38ef34-9c57-42a9-9b1f-6cdb54c284ee",
      "name": "delivered+e2e-fa1913781717@resend.dev",
      "email": "delivered+e2e-fa1913781717@resend.dev"
    },
    "decided_by": null,
    "reports_on_item": 1
  },
  "meta": null
}
```

`escalate_again_409` (409):
```json
{
  "error": {
    "code": "ALREADY_ESCALATED",
    "message": "This report is already escalated.",
    "field_errors": []
  }
}
```

`dismiss_escalated_by_moderator_403` (403):
```json
{
  "error": {
    "code": "SUPER_ADMIN_ONLY",
    "message": "Only a Super Admin can decide an escalated report.",
    "field_errors": []
  }
}
```

**UX consequence:** for a moderator, hide Dismiss, Remove and Restore on an escalated report (show "Waiting for a Super Admin") instead of letting them hit the 403. The same 403 `SUPER_ADMIN_ONLY` also appears when removing an item that has an escalated report on it, and when restoring a removal that closed an escalated report: show its `message`.


`remove_escalated_by_moderator_403` (403):
```json
{
  "error": {
    "code": "SUPER_ADMIN_ONLY",
    "message": "Only a Super Admin can decide an escalated report.",
    "field_errors": []
  }
}
```

`remove_missing_code_422` (422):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "totp_code",
        "message": "Field required"
      }
    ]
  }
}
```

`remove_bad_code_403` (403):
```json
{
  "error": {
    "code": "STEP_UP_INVALID",
    "message": "That authenticator code isn't right, or was already used. Enter a new one.",
    "field_errors": []
  }
}
```

**Not `401`:** a wrong code is `403 STEP_UP_INVALID` so the FE doesn't sign the staff member out. Show the message and ask for a new code.


`remove_ai_message` (200):
```json
{
  "data": {
    "id": "51de0fac-ab33-414b-9fea-0b324b613117",
    "item_type": "ai_message",
    "reason": "harmful",
    "note": "Wrong figures",
    "status": "removed",
    "snippet": "[stub-llm] AI-generated assessment narrative.",
    "snippet_truncated": false,
    "reporter": {
      "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
      "name": "Ada Obi",
      "email": "delivered+e2e-bb3da4790da4@resend.dev"
    },
    "author": null,
    "workspace": {
      "id": "7557040c-f857-4dfc-a796-bd32953acb81",
      "name": "Report Co",
      "plan_key": "starter"
    },
    "created_at": "2026-11-02T14:31:30.221510+00:00",
    "escalated_at": null,
    "decided_at": "2026-11-02T14:31:30.490871+00:00",
    "escalation_reason": null,
    "decision_reason": "Harmful financial advice to a founder",
    "escalated_by": null,
    "decided_by": {
      "id": "3f38ef34-9c57-42a9-9b1f-6cdb54c284ee",
      "name": "delivered+e2e-fa1913781717@resend.dev",
      "email": "delivered+e2e-fa1913781717@resend.dev"
    },
    "reports_on_item": 1
  },
  "meta": null
}
```

`remove_comment_by_super_admin` (200):
```json
{
  "data": {
    "id": "e086ac18-fb11-47b9-be02-24e5a732c6f2",
    "item_type": "comment",
    "reason": "abusive",
    "note": "Personal attack",
    "status": "removed",
    "snippet": "Your launch plan is hopeless and so are you.",
    "snippet_truncated": false,
    "reporter": {
      "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
      "name": "Ada Obi",
      "email": "delivered+e2e-bb3da4790da4@resend.dev"
    },
    "author": {
      "id": "c35089c7-11f6-4301-a3de-cd65cb0691d7",
      "name": "delivered+e2e-a8874c9c7471@resend.dev"
    },
    "workspace": {
      "id": "7557040c-f857-4dfc-a796-bd32953acb81",
      "name": "Report Co",
      "plan_key": "starter"
    },
    "created_at": "2026-11-02T14:31:30.166167+00:00",
    "escalated_at": "2026-11-02T14:31:30.394716+00:00",
    "decided_at": "2026-11-02T14:31:30.520445+00:00",
    "escalation_reason": "Targeted abuse of a named person; needs a Super Admin call",
    "decision_reason": "Abuse of a named person confirmed",
    "escalated_by": {
      "id": "3f38ef34-9c57-42a9-9b1f-6cdb54c284ee",
      "name": "delivered+e2e-fa1913781717@resend.dev",
      "email": "delivered+e2e-fa1913781717@resend.dev"
    },
    "decided_by": {
      "id": "210254f9-f135-4d34-bebd-0117b1651d68",
      "name": "delivered+e2e-27877180d2aa@resend.dev",
      "email": "delivered+e2e-27877180d2aa@resend.dev"
    },
    "reports_on_item": 1
  },
  "meta": null
}
```

**UX consequence (Remove):** removing closes *every* live report on that item (R8), so the Open tab loses all of them at once. The confirm dialog should say that the author will see "Removed by moderation" and that it can be restored.


`restore_not_removed_409` (409):
```json
{
  "error": {
    "code": "NOT_REMOVED",
    "message": "Only a removed item can be restored.",
    "field_errors": []
  }
}
```

`restore_escalated_by_moderator_403` (403):
```json
{
  "error": {
    "code": "SUPER_ADMIN_ONLY",
    "message": "Only a Super Admin can decide an escalated report.",
    "field_errors": []
  }
}
```

`restore` (200):
```json
{
  "data": {
    "id": "e086ac18-fb11-47b9-be02-24e5a732c6f2",
    "item_type": "comment",
    "reason": "abusive",
    "note": "Personal attack",
    "status": "restored",
    "snippet": "Your launch plan is hopeless and so are you.",
    "snippet_truncated": false,
    "reporter": {
      "id": "f0753ee3-b676-41c0-a769-9c562917eb7d",
      "name": "Ada Obi",
      "email": "delivered+e2e-bb3da4790da4@resend.dev"
    },
    "author": {
      "id": "c35089c7-11f6-4301-a3de-cd65cb0691d7",
      "name": "delivered+e2e-a8874c9c7471@resend.dev"
    },
    "workspace": {
      "id": "7557040c-f857-4dfc-a796-bd32953acb81",
      "name": "Report Co",
      "plan_key": "starter"
    },
    "created_at": "2026-11-02T14:31:30.166167+00:00",
    "escalated_at": "2026-11-02T14:31:30.394716+00:00",
    "decided_at": "2026-11-02T14:32:01.218225+00:00",
    "escalation_reason": "Targeted abuse of a named person; needs a Super Admin call",
    "decision_reason": "Second look: context was a quoted example",
    "escalated_by": {
      "id": "3f38ef34-9c57-42a9-9b1f-6cdb54c284ee",
      "name": "delivered+e2e-fa1913781717@resend.dev",
      "email": "delivered+e2e-fa1913781717@resend.dev"
    },
    "decided_by": {
      "id": "210254f9-f135-4d34-bebd-0117b1651d68",
      "name": "delivered+e2e-27877180d2aa@resend.dev",
      "email": "delivered+e2e-27877180d2aa@resend.dev"
    },
    "reports_on_item": 1
  },
  "meta": null
}
```

## Audit trail

Every action (and the founder's filing) writes an audit row, visible to Super Admin in the audit explorer. Rows carry ids and your reason, **never the snippet**.


`audit_report_actions` (200):
```json
{
  "data": {
    "entries": [
      {
        "id": "c734c801-2a90-41f2-ad8b-33b26a485b6c",
        "created_at": "2026-11-02T14:32:01.190122+00:00",
        "action": "report.item_restored",
        "actor_user_id": "210254f9-f135-4d34-bebd-0117b1651d68",
        "actor_email": "delivered+e2e-27877180d2aa@resend.dev",
        "workspace_id": "7557040c-f857-4dfc-a796-bd32953acb81",
        "entity_type": "content_report",
        "entity_id": "e086ac18-fb11-47b9-be02-24e5a732c6f2",
        "reason": "Second look: context was a quoted example",
        "details": {
          "item_id": "b86a41c8-0fb1-45ee-815a-f48e3e0551c3",
          "item_type": "comment",
          "item_existed": true,
          "reports_reopened_as_restored": 1
        },
        "ip": "127.0.0.1"
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```
(list trimmed to its first item; the live response had 10)

Actions seen live: `report.filed`, `report.dismissed`, `report.escalated`, `report.item_removed`, `report.item_restored`.


## Verification table

| Behaviour | Status |
|---|---|
| Founder reports a comment and an AI reply; response shape | verified-live |
| Validation errors (bad type, bad reason, `other` without note, extra field), no auth, own comment, other workspace 404, duplicate 409 | verified-live |
| `GET /reports/mine` before and after decisions | verified-live |
| Rate limit 429 on the 11th report in an hour | not live (flood risk on the shared e2e DB); API test only. Shape follows the standard `RATE_LIMITED` envelope used by Tickets |
| Moderator guards: founder 404, wrong scope 403 | verified-live |
| Counts, open list with search, type/reason filter, closed and escalated tabs, cursor paging | verified-live except cursor paging, which is API-tested only |
| Detail for a comment and an AI reply; staff never see the author's email or the source thread | verified-live |
| Dismiss, escalate, double-escalate 409, dismiss on closed 409, moderator blocked on escalated 403 | verified-live |
| Remove with a bad code 403, no code 422, good code 200 (AI reply by moderator, comment by Super Admin) | verified-live |
| Author sees `Removed by moderation` on a comment and an AI reply, cannot edit it, restore brings it back | verified-live |
| Restore on a non-removed report 409; moderator restoring an escalated removal 403 | verified-live |
| An author can't learn who reported them (API test: reporter id, name, email, note absent from every author-facing response) | API-tested only |
| Removing when an escalated sibling report exists (moderator 403); the threads-index snippet is hidden | API-tested only |
| Remove after the author already deleted the item (report still closes) | API-tested only |
| Audit rows for all five actions | verified-live |
| Email or in-app notification to the author or reporter | not built (R11) |
