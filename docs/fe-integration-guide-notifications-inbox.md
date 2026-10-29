# FE Integration Guide: Notifications inbox, archive, filters, preferences and digest (Module 20)

**Extends** `fe-integration-guide-notifications.md`, `-read-filter.md` and `-realtime.md` (still true for the feed, mark-read, the
audience rules and the SSE stream). Read this one for everything added by the FE hand-off pass. Every body below is pasted
verbatim from `e2e/test_notifications_gaps.py` run against a live server (`e2e/_captures/notifications_gaps/`); only the long
lists are trimmed, and each trim is marked inline. IDs and addresses are from the journey run. The last section says what was
and was not exercised live.

Base path `/api/v1/notifications`. Every route needs `Authorization: Bearer <access token>` and `X-Workspace-Id`. Success bodies are
`{"data": ..., "meta": null}`; errors are `{"error": {"code", "message", "field_errors": []}}`.

## 0. What changed for the FE

- **Every row now carries `archived`, `priority` and `module`** (also on the realtime `notification.created` payload).
  `priority` is `"critical"` or `"normal"`; **critical rows are the "Urgent" ones** (pin them with the brass left border). `module`
  is the product area for the icon: `missions, roadmap, health, team, documents, business, finance, dashboard, marketing, sales,
  validation, academy, billing, journal, other`. **Stop deriving category or urgency from the type string on the client.**
- **Filters, counts and archive are server side.** Stop filtering the loaded page; the badge numbers come from `GET /counts`.
- **Dismiss (X) = Archive.** There is no delete. Archived rows leave the inbox and the unread count and are listed under Archived,
  where they can be restored.
- **Preferences are a grid**: In app and Email per category, with critical alerts locked on. **There is no Push column** (not built).
- **Digest and quiet hours now actually work** (they were stored only). See 6.

## 1. `GET /notifications`: the inbox list

Query: `filter=all|unread|urgent|mentions|ai|deadlines|billing` (default `all`; anything else is 422), `archived=true` (the Archived
list; default false), `unread=true` (legacy, same as `filter=unread`), `limit` (1 to 50, default 20, larger is clamped to 50), `cursor`.
Newest first. `next_cursor` is `null` on the last page. **The cursor works together with `filter` and `archived`**: send the same
filter on every page.

@@notifications_gaps/inbox_page1:rows@@

The first page above is trimmed to its first row here; the live response held three. A malformed filter:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "query.filter",
        "message": "String should match pattern '^(all|unread|urgent|mentions|ai|deadlines|billing)$'"
      }
    ]
  }
}
```

Chip mapping (PRD 20.1 plus the designed Unread / Urgent): **All, Unread, Urgent, Mentions, AI, Deadlines, Billing**.
Urgent filters to the critical types. Mentions = comments that mention you. AI = briefing, plans, suggestions, insights, re-plans, course
recommendations. Deadlines = milestone due / overdue, invoice overdue, quarterly assessment. Billing = every `billing.*` type.

Example, `filter=urgent` (two critical rows in the journey: runway low and milestone overdue):

```json
{
  "data": {
    "notifications": [
      {
        "id": "34f63573-8ce3-462e-a10b-506a114367e3",
        "type": "finance.runway.low",
        "title": "Runway below 6 months, review recommended",
        "body": "Open Finance",
        "data": {
          "startup_id": "c7de4a98-465c-4d73-82bf-6b73a932f1f0"
        },
        "read": false,
        "archived": false,
        "priority": "critical",
        "module": "finance",
        "created_at": "2026-10-29T20:27:53.650219+00:00"
      },
      "... more rows, same shape ..."
    ],
    "next_cursor": null
  },
  "meta": null
}
```

## 2. `GET /notifications/counts`: chip badges and the header line

Counts are over the rows this role may see, **not archived**. `urgent_unread` drives "N need you today". `archived` is the Archived
tab badge.

```json
{
  "data": {
    "all": 6,
    "unread": 6,
    "urgent": 2,
    "urgent_unread": 2,
    "mentions": 0,
    "ai": 1,
    "deadlines": 2,
    "billing": 0,
    "archived": 0
  },
  "meta": null
}
```

Refetch counts after any mark-read, archive, restore or realtime event (or adjust locally and refetch on focus).

## 3. Archive and restore

`POST /notifications/{id}/archive` archives one row and **also marks it read**, so it leaves the unread count for good.
`POST /notifications/{id}/restore` brings it back (it stays read). Both return the row. Both are 404 for another user's row, an unknown id
or a type your role cannot see.

```json
{
  "data": {
    "id": "aad25ee0-df5e-474f-9750-fb43a18d2df3",
    "type": "finance.invoice.paid",
    "title": "Invoice paid: INV-2026-001 \ud83c\udf89",
    "body": "View invoice",
    "data": {
      "number": "INV-2026-001",
      "startup_id": "c7de4a98-465c-4d73-82bf-6b73a932f1f0"
    },
    "read": true,
    "archived": true,
    "priority": "normal",
    "module": "finance",
    "created_at": "2026-10-29T20:27:53.654747+00:00"
  },
  "meta": null
}
```
```json
{
  "data": {
    "notifications": [
      {
        "id": "aad25ee0-df5e-474f-9750-fb43a18d2df3",
        "type": "finance.invoice.paid",
        "title": "Invoice paid: INV-2026-001 \ud83c\udf89",
        "body": "View invoice",
        "data": {
          "number": "INV-2026-001",
          "startup_id": "c7de4a98-465c-4d73-82bf-6b73a932f1f0"
        },
        "read": true,
        "archived": true,
        "priority": "normal",
        "module": "finance",
        "created_at": "2026-10-29T20:27:53.654747+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```
```json
{
  "data": {
    "unread": 5
  },
  "meta": null
}
```
```json
{
  "data": {
    "id": "aad25ee0-df5e-474f-9750-fb43a18d2df3",
    "type": "finance.invoice.paid",
    "title": "Invoice paid: INV-2026-001 \ud83c\udf89",
    "body": "View invoice",
    "data": {
      "number": "INV-2026-001",
      "startup_id": "c7de4a98-465c-4d73-82bf-6b73a932f1f0"
    },
    "read": true,
    "archived": false,
    "priority": "normal",
    "module": "finance",
    "created_at": "2026-10-29T20:27:53.654747+00:00"
  },
  "meta": null
}
```

`POST /notifications/read-all` skips archived rows. `POST /notifications/{id}/read` is unchanged.

## 4. Preferences: the grid

`GET /notifications/preferences` returns the stored values merged over defaults. New fields: `in_app` (per category), and `groups` (the
rows of the grid, in display order: `key`, `title`, `description`, and `critical_types`, the locked-on alert types inside that row).
**Render the grid from `groups`**; do not hardcode categories. The first two rows are shown here, the real list has 12.

```json
{
  "data": {
    "master_email": true,
    "categories": {
      "documents": true,
      "business": true,
      "roadmap_missions": true,
      "health_assessment": true,
      "team": true,
      "finance": false,
      "marketing": false,
      "sales": false,
      "validation": false,
      "academy": false,
      "ai_insights": false,
      "billing": true
    },
    "in_app": {
      "documents": true,
      "business": true,
      "roadmap_missions": true,
      "health_assessment": true,
      "team": true,
      "finance": true,
      "marketing": true,
      "sales": true,
      "validation": true,
      "academy": true,
      "ai_insights": true,
      "billing": true
    },
    "groups": [
      {
        "key": "documents",
        "title": "Documents",
        "description": "Shares, versions and e-signature updates.",
        "critical_types": [
          "document.signature.requested"
        ]
      },
      {
        "key": "business",
        "title": "Business Builder",
        "description": "Suggestions, plans and completed artifacts.",
        "critical_types": []
      },
      "... 10 more rows, same shape ..."
    ],
    "digest_mode": "realtime",
    "digest_time": "08:00",
    "digest_day": 0,
    "quiet_enabled": false,
    "quiet_from": "20:00",
    "quiet_until": "06:00",
    "timezone": "Africa/Lagos"
  },
  "meta": null
}
```

Rules the UI must show:
- **In app** and **Email** are one switch each per row (`in_app[key]`, `categories[key]`). The master email switch (`master_email`) turns all
  non-critical email off.
- **Critical alerts can't be turned off.** A row with a non-empty `critical_types` shows a lock and the tooltip "Critical alerts can't be turned
  off. They protect your business." Switching the row's In app or Email off only affects its non-critical types; the critical ones are always
  delivered in app and by email, ignoring the master switch, the digest and quiet hours.
- Defaults: In app is ON everywhere. Email is ON for Documents, Business Builder, Roadmap & missions, Health & assessment, Team and Billing;
  OFF for Finance, Marketing, Sales, Validation, Academy and AI & insights (opt-in).
- Journal reminders are not in the grid (they have their own switches on the Journal page).

`PUT /notifications/preferences` takes any subset of `master_email`, `categories`, `in_app`, `digest_mode`, `digest_time`, `digest_day`,
`quiet_enabled`, `quiet_from`, `quiet_until`, `timezone` and returns the effective object. Switching In app off for a row **stops new
non-critical notifications of that row from being created at all** (existing rows stay). An unknown category key is 422:

```json
{
  "data": {
    "master_email": true,
    "categories": {
      "documents": true,
      "business": true,
      "roadmap_missions": true,
      "health_assessment": true,
      "team": true,
      "finance": false,
      "marketing": false,
      "sales": false,
      "validation": false,
      "academy": false,
      "ai_insights": false,
      "billing": true
    },
    "in_app": {
      "documents": true,
      "business": true,
      "roadmap_missions": false,
      "health_assessment": true,
      "team": true,
      "finance": true,
      "marketing": true,
      "sales": true,
      "validation": true,
      "academy": true,
      "ai_insights": true,
      "billing": true
    },
    "groups": [
      {
        "key": "documents",
        "title": "Documents",
        "description": "Shares, versions and e-signature updates.",
        "critical_types": [
          "document.signature.requested"
        ]
      },
      {
        "key": "business",
        "title": "Business Builder",
        "description": "Suggestions, plans and completed artifacts.",
        "critical_types": []
      },
      {
        "key": "roadmap_missions",
        "title": "Roadmap & missions",
        "description": "Re-plans, milestones, missions and streaks.",
        "critical_types": [
          "roadmap.milestone.overdue"
        ]
      },
      {
        "key": "health_assessment",
        "title": "Health & assessment",
        "description": "Score changes and assessments.",
        "critical_types": []
      },
      {
        "key": "team",
        "title": "Team",
        "description": "New members and comments that mention you.",
        "critical_types": []
      },
      {
        "key": "finance",
        "title": "Finance",
        "description": "Runway, invoices and payments.",
        "critical_types": [
          "finance.invoice.overdue",
          "finance.runway.low"
        ]
      },
      {
        "key": "marketing",
        "title": "Marketing",
        "description": "Posts published and campaign results.",
        "critical_types": []
      },
      {
        "key": "sales",
        "title": "Sales",
        "description": "Deals won or stalled and sequence sending.",
        "critical_types": [
          "sales.sending.paused"
        ]
      },
      {
        "key": "validation",
        "title": "Validation",
        "description": "Smoke-test milestones and new customer insights.",
        "critical_types": []
      },
      {
        "key": "academy",
        "title": "Academy",
        "description": "Courses completed and recommended.",
        "critical_types": []
      },
      {
        "key": "ai_insights",
        "title": "AI & insights",
        "description": "Your daily briefing and KPI alerts.",
        "critical_types": []
      },
      {
        "key": "billing",
        "title": "Billing",
        "description": "Plan changes and usage.",
        "critical_types": [
          "billing.payment.failed"
        ]
      }
    ],
    "digest_mode": "realtime",
    "digest_time": "08:00",
    "digest_day": 0,
    "quiet_enabled": false,
    "quiet_from": "20:00",
    "quiet_until": "06:00",
    "timezone": "Africa/Lagos"
  },
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
        "field": "in_app",
        "message": "Value error, unknown categories: ['nope']"
      }
    ]
  }
}
```
```json
{
  "data": {
    "all": 7,
    "unread": 6,
    "urgent": 3,
    "urgent_unread": 3,
    "mentions": 0,
    "ai": 1,
    "deadlines": 3,
    "billing": 0,
    "archived": 0
  },
  "meta": null
}
```

(The count above rose by exactly one: the switched-off mission notification was not created and the critical milestone alert was.)

## 5. Digest and quiet hours settings

`digest_mode` is `realtime | daily | weekly`; `digest_time` is `HH:MM` in the user's `timezone`; `digest_day` is 0 (Monday) to 6 for weekly;
`quiet_enabled` + `quiet_from` + `quiet_until` define the window (it may cross midnight); `timezone` must be an IANA name from the list.

```json
{
  "data": {
    "master_email": true,
    "categories": {
      "documents": true,
      "business": true,
      "roadmap_missions": true,
      "health_assessment": true,
      "team": true,
      "finance": true,
      "marketing": false,
      "sales": false,
      "validation": false,
      "academy": false,
      "ai_insights": false,
      "billing": true
    },
    "in_app": {
      "documents": true,
      "business": true,
      "roadmap_missions": false,
      "health_assessment": true,
      "team": true,
      "finance": true,
      "marketing": true,
      "sales": true,
      "validation": true,
      "academy": true,
      "ai_insights": true,
      "billing": true
    },
    "groups": [
      {
        "key": "documents",
        "title": "Documents",
        "description": "Shares, versions and e-signature updates.",
        "critical_types": [
          "document.signature.requested"
        ]
      },
      {
        "key": "business",
        "title": "Business Builder",
        "description": "Suggestions, plans and completed artifacts.",
        "critical_types": []
      },
      {
        "key": "roadmap_missions",
        "title": "Roadmap & missions",
        "description": "Re-plans, milestones, missions and streaks.",
        "critical_types": [
          "roadmap.milestone.overdue"
        ]
      },
      {
        "key": "health_assessment",
        "title": "Health & assessment",
        "description": "Score changes and assessments.",
        "critical_types": []
      },
      {
        "key": "team",
        "title": "Team",
        "description": "New members and comments that mention you.",
        "critical_types": []
      },
      {
        "key": "finance",
        "title": "Finance",
        "description": "Runway, invoices and payments.",
        "critical_types": [
          "finance.invoice.overdue",
          "finance.runway.low"
        ]
      },
      {
        "key": "marketing",
        "title": "Marketing",
        "description": "Posts published and campaign results.",
        "critical_types": []
      },
      {
        "key": "sales",
        "title": "Sales",
        "description": "Deals won or stalled and sequence sending.",
        "critical_types": [
          "sales.sending.paused"
        ]
      },
      {
        "key": "validation",
        "title": "Validation",
        "description": "Smoke-test milestones and new customer insights.",
        "critical_types": []
      },
      {
        "key": "academy",
        "title": "Academy",
        "description": "Courses completed and recommended.",
        "critical_types": []
      },
      {
        "key": "ai_insights",
        "title": "AI & insights",
        "description": "Your daily briefing and KPI alerts.",
        "critical_types": []
      },
      {
        "key": "billing",
        "title": "Billing",
        "description": "Plan changes and usage.",
        "critical_types": [
          "billing.payment.failed"
        ]
      }
    ],
    "digest_mode": "daily",
    "digest_time": "23:59",
    "digest_day": 0,
    "quiet_enabled": true,
    "quiet_from": "20:00",
    "quiet_until": "06:00",
    "timezone": "UTC"
  },
  "meta": null
}
```

## 6. What the settings do (and what the UI must tell the user)

- **Digest.** With `daily` or `weekly`, non-critical email is no longer sent one by one: one email per day (or per chosen weekday) at
  `digest_time` lists what arrived since the last one, grouped by module with counts and up to three titles each. Subject
  `"{startup name}: your {daily|weekly} Cofoundaz digest"`. A digest with nothing to say is not sent. The explainer line to show on the
  Digest screen: "Urgent alerts always arrive straight away, so they are not repeated in your digest."
- **Quiet hours.** While the window is on, **non-critical email is not sent** (not delayed; those items still sit in your inbox, and appear in a
  digest if you use one). In-app delivery is unaffected, and so are critical alerts. There is no push channel, so the "Push pauses" wording from
  the PRD does not apply: say "Email pauses from {from} to {until}". This is a deliberate limit (D-NOT-5).
- The digest email the worker actually sent in the journey (the file mail backend; the address is the journey's throwaway recipient):

```json
{
  "subject": "Acme Labs: your daily Cofoundaz digest",
  "to": "delivered+e2e-eaa34b3529cb@resend.dev",
  "body": "<div style=\"font-family:system-ui,sans-serif;max-width:560px\"><h2>Your daily digest</h2><h3>Finance (2)</h3><ul><li>Invoice paid: INV-2026-001 \ud83c\udf89</li><li>Invoice paid: INV-2026-001 \ud83c\udf89</li></ul><h3>Missions (1)</h3><ul><li>Your mission for today is ready</li></ul><h3>Roadmap (1)</h3><ul><li>Milestone due soon: Validate demand, tomorrow</li></ul><p><a href=\"http://localhost:3000/notifications\">Open your notifications</a></p><hr><p style=\"font-size:12px;color:#666\">Urgent alerts always arrive straight away, so they are not repeated here. Change this in your notification settings.</p></div>"
}
```

## 7. Error codes

| Status | `error.code` | When |
|---|---|---|
| 422 | `VALIDATION_ERROR` | unknown `filter`, unknown category in `categories` or `in_app`, bad time or timezone, bad cursor (`field: cursor`) |
| 404 | `NOT_FOUND` | archive / restore / read of an id that is not yours or not visible to your role |
| 401 / 403 | (standard) | unauthenticated, unverified, no workspace |

## 8. Verification table

| Behaviour | Status |
|---|---|
| Inbox filters, counts, priority and module fields, cursor with filter | verified live (captured) |
| Archive, restore, archived list, unread count excludes archived | verified live |
| Preferences grid, `in_app` switch dropping non-critical rows, critical delivered, unknown key 422 | verified live |
| Digest settings stored; digest email built and sent by the worker through the scheduler | verified live (scheduler ticked in the journey process, worker drained in-process, mail read from the file backend) |
| Quiet hours suppressing non-critical email, weekly digest weekday, critical bypass of master/digest/quiet, an empty digest not sent | unit-tested only |
| The `billing` chip with real rows | the chip and filter are verified, but no billing notification exists on this branch (Billing PR #196 adds the types); counted 0 here |
| Mentions chip with a row | unit-tested only (the journey did not create a mention) |
| Real SMTP delivery of the digest | not exercised (file backend only) |
