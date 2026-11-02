# FE integration guide: Admin Tickets (founder Help intake + staff Support)

Every body below is pasted from a live run against a real server: `e2e/test_admin_tickets.py`, captures in `e2e/_captures/admin_tickets/<name>.json`. Ids, emails and names are throwaway values from that run (placeholders). Base path `/api/v1`. Success bodies are `{"data": ..., "meta": ...}`; errors are `{"error": {"code", "message", "field_errors"}}`, **except the hidden 404 on staff routes, which is `{"detail": "Not Found"}`**. Long objects are shown once and referenced by capture name after that.

## What this is, in FE terms

1. **Settings > Help (founder app).** Any workspace member opens a ticket (category, subject, message), sees their tickets, reads the thread and replies. Needs `Authorization` and `X-Workspace-Id`. No attachments in v1. The founder never sees priority, SLA, assignee, internal notes or staff names/emails; staff messages show as author "Cofoundaz Support".
2. **Support tab (Admin console, scope `support`; Super Admin too).** Queue with tabs Open / Mine / Resolved, filters, SLA chips, a thread with an internal-notes tab, canned replies, assign, priority, escalate, resolve, reopen. Per-page search (P13). No step-up code on these actions (T3).

Product calls T1 to T8 (accepted by Adebayo 2026-11-02): T1 SLA = first staff reply deadline, P1 1h / P2 4h / P3 24h / P4 72h. T2 no AI triage (hide the banner). T3 any `support` staff sees all tickets. T4 text only, 5000 chars. T5 rate limits. T6 visibility rules below. T7 reopen window 14 days. T8 escalate = P1.

**Exception to P1 (accepted by Adebayo, 2026-11-02):** no `totp_code` is sent on any ticket action, unlike other staff mutations. Do not add a code prompt here. Actions are still audited and escalate still needs a `reason`.

## Founder endpoints

### `POST /support/tickets` (201)

Body: `{"category": billing|account|bug|how_to|feature_request|other, "subject": 3-200 chars, "body": 1-5000 chars}`. Any other field (e.g. `priority`) is a 422. Priority is never the founder's to set; billing and account default to P2, the rest P3.

`open_ok`:
```json
{
  "data": {
    "id": "2028f4e6-a732-458e-a658-d0ff40f36033",
    "number": "T-1001",
    "category": "billing",
    "subject": "Invoice PDF is blank",
    "status": "open",
    "last_author": "founder",
    "created_at": "2026-11-02T13:55:05.118139+00:00",
    "last_message_at": "2026-11-02T13:55:05.133122+00:00",
    "resolved_at": null,
    "messages": [
      {
        "id": "84465c68-e23e-47ff-84cb-00e0a42f460c",
        "author_kind": "founder",
        "author_name": "Ada Obi",
        "is_mine": true,
        "body": "The PDF export of invoice INV-0004 comes out blank.\nIt worked last week.",
        "created_at": "2026-11-02T13:55:05.133122+00:00"
      }
    ]
  },
  "meta": null
}
```
`open_bad_category` (422):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "category",
        "message": "Input should be 'billing', 'account', 'bug', 'how_to', 'feature_request' or 'other'"
      }
    ]
  }
}
```
`open_extra_field` and `open_short_subject` are the same 422 shape (see captures). `open_no_auth` (401, no token):
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```
**Rate limit (T5), not exercised live** (proved by `tests/api/test_staff_support.py::test_ticket_creation_is_rate_limited`; the shape is the standard error envelope): 5 per hour and 20 per day per person, then `429` with `error.code` `RATE_LIMITED` and a plain-English `message`. Show the message; replies are limited to 30 per hour with the same code.

### `GET /support/tickets?status=open|resolved&limit=1..50&cursor=`

Newest activity first. `meta.next_cursor` is null on the last page. `founder_list`:
```json
{
  "data": {
    "tickets": [
      {
        "id": "2028f4e6-a732-458e-a658-d0ff40f36033",
        "number": "T-1001",
        "category": "billing",
        "subject": "Invoice PDF is blank",
        "status": "open",
        "last_author": "founder",
        "created_at": "2026-11-02T13:55:05.118139+00:00",
        "last_message_at": "2026-11-02T13:55:05.133122+00:00",
        "resolved_at": null
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```
Note: a list row has **no `messages`**; the thread comes from the detail call.

### `GET /support/tickets/{id}`

`founder_thread_after_staff_reply` (after staff left an internal note and replied; the note is absent and staff are "Cofoundaz Support"):
```json
{
  "data": {
    "id": "2028f4e6-a732-458e-a658-d0ff40f36033",
    "number": "T-1001",
    "category": "billing",
    "subject": "Invoice PDF is blank",
    "status": "open",
    "last_author": "staff",
    "created_at": "2026-11-02T13:55:05.118139+00:00",
    "last_message_at": "2026-11-02T13:55:05.580415+00:00",
    "resolved_at": null,
    "messages": [
      {
        "id": "84465c68-e23e-47ff-84cb-00e0a42f460c",
        "author_kind": "founder",
        "author_name": "Ada Obi",
        "is_mine": true,
        "body": "The PDF export of invoice INV-0004 comes out blank.\nIt worked last week.",
        "created_at": "2026-11-02T13:55:05.133122+00:00"
      },
      {
        "id": "a3aaf71c-5c2b-4f3d-93d7-74d00d43804d",
        "author_kind": "staff",
        "author_name": "Cofoundaz Support",
        "is_mine": false,
        "body": "Thanks for flagging this. We found the cause and are fixing it today.",
        "created_at": "2026-11-02T13:55:05.580415+00:00"
      }
    ]
  },
  "meta": null
}
```
`is_mine` is true only for messages the caller wrote. A ticket from another workspace, or (for a non-founder member) one opened by someone else, is `404 NOT_FOUND`: `founder_other_workspace_404`:
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### `POST /support/tickets/{id}/messages` (201)

Body `{"body": "..."}`; returns the full thread (`founder_reply`, same shape as the detail). On a **resolved** ticket the reply **reopens it** (T7): `founder_reply_reopens` returns `status: "open"`. More than 14 days after resolving it is `409 TICKET_CLOSED` (message: "This ticket was resolved more than 14 days ago. Please open a new one."), **not exercised live** (needs a 14-day-old ticket; covered by a unit test). Show an "Open a new ticket" button on that error.

### Who sees which tickets (T6)

A member sees the tickets they opened. The workspace's `founder` role also sees every ticket in that workspace. Everyone else's ids are 404. Always send `X-Workspace-Id`.

## Staff endpoints (`/admin/support/...`, scope `support`)

Anyone who is not active staff with TOTP gets the hidden 404 (`staff_list_founder_404`: `{"detail": "Not Found"}`); an Admin without the `support` scope gets 403 (`staff_list_wrong_scope_403`):
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

### Queue: `GET /admin/support/tickets`

Query: `tab=open|mine|resolved` (default open), `priority=P1..P4`, `category`, `sla=breached|due_soon`, `escalated=true|false`, `unassigned=true`, `query` (matches subject, ticket number like `T-1001` or `1001`, founder name/email, workspace name; `%` and `_` are literal), `limit` (1-100, default 25), `cursor`. Sorted by latest activity. `staff_list_search_by_number`:
```json
{
  "data": {
    "tickets": [
      {
        "id": "2028f4e6-a732-458e-a658-d0ff40f36033",
        "number": "T-1001",
        "subject": "Invoice PDF is blank",
        "category": "billing",
        "priority": "P2",
        "status": "open",
        "escalated": false,
        "last_author": "founder",
        "founder": {
          "id": "b7182fbe-437a-404c-af5c-05f647ad2ce2",
          "name": "Ada Obi",
          "email": "delivered+e2e-d6d4db192d15@resend.dev"
        },
        "workspace": {
          "id": "b93834b0-4c2b-4d05-a17c-f6e71907d056",
          "name": "Ticket Co",
          "plan_key": "starter"
        },
        "assignee": null,
        "sla": {
          "due_at": "2026-11-02T17:55:05.133122+00:00",
          "state": "ok"
        },
        "created_at": "2026-11-02T13:55:05.118139+00:00",
        "last_message_at": "2026-11-02T13:55:05.133122+00:00",
        "first_response_at": null,
        "resolved_at": null
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": null
  }
}
```
**SLA chips** come from `sla.state`: `ok`, `due_soon` (due within the hour), `breached` (no staff reply yet and past `sla.due_at`); once answered it is `met` or `late`. Show a countdown from `sla.due_at` only for `ok`/`due_soon`.

Badges: `counts` gives the tab numbers:
```json
{
  "data": {
    "open": 1,
    "mine": 0,
    "resolved": 0,
    "breached": 0,
    "unassigned": 1
  },
  "meta": null
}
```
`GET /admin/support/assignees` lists who can be assigned (`assignees`):
```json
{
  "data": {
    "staff": [
      {
        "id": "042b860d-1cbc-462c-ab78-bd7ff65b3970",
        "name": "delivered+e2e-484048ef0a4b@resend.dev",
        "email": "delivered+e2e-484048ef0a4b@resend.dev",
        "role": "super_admin"
      },
      {
        "id": "de9f1a8a-5538-49fb-a585-f27183272757",
        "name": "delivered+e2e-ec76b0082478@resend.dev",
        "email": "delivered+e2e-ec76b0082478@resend.dev",
        "role": "admin"
      }
    ]
  },
  "meta": null
}
```
Dashboard "Needs attention" (A9), any staff, even without `support` (`dashboard_attention`; KPI payload is unchanged):
```json
{
  "data": {
    "tickets": {
      "open": 1,
      "breached": 0
    }
  },
  "meta": null
}
```

### Thread: `GET /admin/support/tickets/{id}`

`staff_detail_new`:
```json
{
  "data": {
    "id": "2028f4e6-a732-458e-a658-d0ff40f36033",
    "number": "T-1001",
    "subject": "Invoice PDF is blank",
    "category": "billing",
    "priority": "P2",
    "status": "open",
    "escalated": false,
    "last_author": "founder",
    "founder": {
      "id": "b7182fbe-437a-404c-af5c-05f647ad2ce2",
      "name": "Ada Obi",
      "email": "delivered+e2e-d6d4db192d15@resend.dev"
    },
    "workspace": {
      "id": "b93834b0-4c2b-4d05-a17c-f6e71907d056",
      "name": "Ticket Co",
      "plan_key": "starter"
    },
    "assignee": null,
    "sla": {
      "due_at": "2026-11-02T17:55:05.133122+00:00",
      "state": "ok"
    },
    "created_at": "2026-11-02T13:55:05.118139+00:00",
    "last_message_at": "2026-11-02T13:55:05.133122+00:00",
    "first_response_at": null,
    "resolved_at": null,
    "escalation_reason": null,
    "resolved_by": null,
    "messages": [
      {
        "id": "84465c68-e23e-47ff-84cb-00e0a42f460c",
        "author_kind": "founder",
        "author": {
          "id": "b7182fbe-437a-404c-af5c-05f647ad2ce2",
          "name": "Ada Obi",
          "email": "delivered+e2e-d6d4db192d15@resend.dev"
        },
        "body": "The PDF export of invoice INV-0004 comes out blank.\nIt worked last week.",
        "created_at": "2026-11-02T13:55:05.133122+00:00"
      }
    ],
    "notes": []
  },
  "meta": null
}
```
`notes` are staff-only. `founder` and `workspace` are all the account context staff get (D-ADM-9: no documents, journal or AI chats exist on any route). `founder` is `null` if the author's account is gone. Every action below returns this same detail shape (the capture names: `assign`, `note`, `priority`, `staff_reply`, `escalate`, `resolve`, `staff_reopen`).

### Actions (all return the updated detail)

| Action | Request | Notes |
|---|---|---|
| `POST .../assign` | `{"assignee_user_id": "<id>"|null}` | `null` unassigns. Ids from `/assignees`. 422 `ASSIGNEE_NOT_ALLOWED`, 422 `NO_CHANGE`. |
| `POST .../reply` | `{"body"}` | Emails the founder. First reply stops the SLA clock and assigns the ticket to the replier if unassigned. |
| `POST .../notes` | `{"body"}` | Internal; never emailed or shown to the founder. Allowed on resolved tickets. |
| `PATCH .../priority` | `{"priority":"P1".."P4"}` | Before the first reply it moves the deadline by the difference in SLA hours. 422 `NO_CHANGE`. |
| `POST .../escalate` | `{"reason": 10-500 chars}` | Sets P1 + `escalated`, leaves a note, audit row with the reason. 409 `ALREADY_ESCALATED`. |
| `POST .../resolve` | `{"message"?: 1-5000}` | Optional closing message goes in the thread and the closing email. |
| `POST .../reopen` | `{"reason"?: 10-500}` | Resolved tickets only; restarts the SLA clock. 409 `TICKET_NOT_RESOLVED`. |

`escalate` capture (the detail has `escalated: true`, `priority: "P1"`, `escalation_reason`):
```json
{
  "data": {
    "id": "2028f4e6-a732-458e-a658-d0ff40f36033",
    "number": "T-1001",
    "subject": "Invoice PDF is blank",
    "category": "billing",
    "priority": "P1",
    "status": "open",
    "escalated": true,
    "last_author": "founder",
    "founder": {
      "id": "b7182fbe-437a-404c-af5c-05f647ad2ce2",
      "name": "Ada Obi",
      "email": "delivered+e2e-d6d4db192d15@resend.dev"
    },
    "workspace": {
      "id": "b93834b0-4c2b-4d05-a17c-f6e71907d056",
      "name": "Ticket Co",
      "plan_key": "starter"
    },
    "assignee": {
      "id": "de9f1a8a-5538-49fb-a585-f27183272757",
      "name": "delivered+e2e-ec76b0082478@resend.dev",
      "email": "delivered+e2e-ec76b0082478@resend.dev"
    },
    "sla": {
      "due_at": "2026-11-02T14:55:05.133122+00:00",
      "state": "met"
    },
    "created_at": "2026-11-02T13:55:05.118139+00:00",
    "last_message_at": "2026-11-02T13:55:05.622888+00:00",
    "first_response_at": "2026-11-02T13:55:05.580415+00:00",
    "resolved_at": null,
    "escalation_reason": "Founder is a design partner and invoices are blocked",
    "resolved_by": null,
    "messages": [
      {
        "id": "84465c68-e23e-47ff-84cb-00e0a42f460c",
        "author_kind": "founder",
        "author": {
          "id": "b7182fbe-437a-404c-af5c-05f647ad2ce2",
          "name": "Ada Obi",
          "email": "delivered+e2e-d6d4db192d15@resend.dev"
        },
        "body": "The PDF export of invoice INV-0004 comes out blank.\nIt worked last week.",
        "created_at": "2026-11-02T13:55:05.133122+00:00"
      },
      {
        "id": "a3aaf71c-5c2b-4f3d-93d7-74d00d43804d",
        "author_kind": "staff",
        "author": {
          "id": "de9f1a8a-5538-49fb-a585-f27183272757",
          "name": "delivered+e2e-ec76b0082478@resend.dev",
          "email": "delivered+e2e-ec76b0082478@resend.dev"
        },
        "body": "Thanks for flagging this. We found the cause and are fixing it today.",
        "created_at": "2026-11-02T13:55:05.580415+00:00"
      },
      {
        "id": "6dd7f5d7-c7ff-4e20-8985-0e35afd7ad88",
        "author_kind": "founder",
        "author": {
          "id": "b7182fbe-437a-404c-af5c-05f647ad2ce2",
          "name": "Ada Obi",
          "email": "delivered+e2e-d6d4db192d15@resend.dev"
        },
        "body": "Great, thank you!",
        "created_at": "2026-11-02T13:55:05.622888+00:00"
      }
    ],
    "notes": [
      {
        "id": "297b1eec-6ac9-49a2-a71d-cbe8126fbc19",
        "author": {
          "id": "de9f1a8a-5538-49fb-a585-f27183272757",
          "name": "delivered+e2e-ec76b0082478@resend.dev",
          "email": "delivered+e2e-ec76b0082478@resend.dev"
        },
        "body": "INTERNAL: invoice template changed in 0.9.4; check PDF worker.",
        "created_at": "2026-11-02T13:55:05.464314+00:00"
      },
      {
        "id": "630731f3-e2bb-4b79-b239-59f58a490912",
        "author": {
          "id": "de9f1a8a-5538-49fb-a585-f27183272757",
          "name": "delivered+e2e-ec76b0082478@resend.dev",
          "email": "delivered+e2e-ec76b0082478@resend.dev"
        },
        "body": "Escalated to P1: Founder is a design partner and invoices are blocked",
        "created_at": "2026-11-02T13:55:05.640935+00:00"
      }
    ]
  },
  "meta": null
}
```
Errors: `escalate_reason_too_short` (422):
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
`escalate_again_409`:
```json
{
  "error": {
    "code": "ALREADY_ESCALATED",
    "message": "This ticket is already escalated.",
    "field_errors": []
  }
}
```
`reply_on_resolved_409` (reply, resolve, escalate, assign and priority on a resolved ticket all return this):
```json
{
  "error": {
    "code": "TICKET_RESOLVED",
    "message": "This ticket is resolved. Reopen it first.",
    "field_errors": []
  }
}
```
`reopen_not_resolved_409`:
```json
{
  "error": {
    "code": "TICKET_NOT_RESOLVED",
    "message": "Only a resolved ticket can be reopened.",
    "field_errors": []
  }
}
```

### Canned replies: `GET/POST /admin/support/canned`, `PATCH/DELETE /admin/support/canned/{id}`

Shared by all support staff. Insert the text into the reply box client-side (there is no server-side expansion). Body `{"title": 1-80, "body": 1-2000}`; PATCH takes either; DELETE is a hard delete returning `{"id", "deleted": true}`. `canned_create` (201):
```json
{
  "data": {
    "id": "76cc61c7-290f-4dbc-b819-47eb2fed7129",
    "title": "Looking into it T-1001",
    "body": "Thanks for flagging this. We're looking into it now.",
    "created_at": "2026-11-02T13:55:05.508908+00:00",
    "updated_at": "2026-11-02T13:55:05.508908+00:00"
  },
  "meta": null
}
```
Also captured: `canned_update`, `canned_list_trimmed_to_this_run`, `canned_delete`.

## Emails the founder receives (verbatim from the file mailbox)

`email_staff_reply`:
```json
{
  "to": "delivered+e2e-d6d4db192d15@resend.dev",
  "subject": "Re: [T-1001] Invoice PDF is blank",
  "html": "<div style=\"font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;padding:8px;color:#1a1a1a\"><h2 style=\"margin:0 0 12px;font-size:20px\">The Cofoundaz team replied to your ticket</h2><blockquote style=\"margin:0 0 20px;padding:8px 14px;border-left:3px solid #ccc;color:#333;line-height:1.5\">Thanks for flagging this. We found the cause and are fixing it today.</blockquote><p style=\"margin:0 0 20px\"><a href=\"http://localhost:3000/settings/help/2028f4e6-a732-458e-a658-d0ff40f36033\" style=\"display:inline-block;padding:12px 22px;background:#4f46e5;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600\">View ticket</a></p></div>",
  "sent_at": "2026-11-02T13:55:05.592364+00:00"
}
```
`email_resolved` has subject `Resolved: [T-1001] Invoice PDF is blank` and the same layout (capture file). Both link to `{APP_BASE_URL}/settings/help/{ticket_id}`: **the FE must serve that route** (ticket detail inside Settings > Help). A failed send never fails the action. Delivery to a real inbox (Resend) was not exercised; only the rendered message.

## Field-nesting traps

- Founder detail vs list: `messages` only on detail. Staff list rows have no `messages`/`notes`/`escalation_reason`/`resolved_by`; the staff detail has them.
- Message author: founder side has `author_name` (string) and `is_mine`; staff side has `author` (object `{id, name, email}`, null for a deleted account).
- Staff `assignee` / `founder` / `resolved_by` are objects or `null`, never absent.
- `resolved_at` and `first_response_at` are present-but-null while open.
- The founder's ticket `number` is a string (`T-1001`); search accepts it.

## UX consequences to surface

- After a founder replies on a resolved ticket it silently becomes **open**; show the status change.
- A ticket reopened by anyone starts a fresh first-reply clock; chips reset.
- Resolve and reply both send email: confirm before resolve and say the founder will be emailed. Never put secrets in a reply; the full text is emailed.
- Escalate needs a written reason and cannot be undone from the UI (no "de-escalate" route); change priority separately.
- Hide the PRD's AI triage banner and attachments (not built).

## Audit

Every staff action writes an audit row (visible to Super Admin in `/super-admin/audit?workspace_id=`): `ticket.assigned`, `ticket.replied`, `ticket.note_added`, `ticket.priority_changed`, `ticket.escalated` (with reason), `ticket.resolved`, `ticket.reopened`, `canned.created|updated|deleted`. Rows carry ids and field changes, never message text. See `audit_ticket_actions`.

## Verification table

| Behaviour | Verified |
|---|---|
| Open ticket, validation errors, no-auth 401, list, thread | live (`e2e/test_admin_tickets.py`) |
| Other workspace's founder gets 404; founder hits staff route gets hidden 404; wrong scope 403 | live |
| Staff search by number, counts, detail, assign, mine tab, note, priority, escalate (+errors) | live |
| Canned create / update / list / delete | live |
| Staff reply emails founder; resolve emails founder (rendered message) | live, file mailbox (real inbox delivery not exercised) |
| Founder never sees notes or staff identity | live and unit |
| Founder reply reopens resolved ticket; staff reopen; resolved tab; dashboard attention; audit rows | live |
| Ticket and reply rate limits (429) | unit only |
| 14-day reopen cutoff (`TICKET_CLOSED`) | unit only |
| SLA `due_soon` / `breached` / `late` chips and filter | unit only (needs time travel); `ok` and `met` live |
| Team-member vs workspace-founder visibility (T6) | unit only |
| Email to a creator who left the workspace is skipped | unit only |
