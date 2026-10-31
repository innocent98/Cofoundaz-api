# FE integration guide: Calendar v2 (feed, meetings, reminders, Google sync)

Covers gap-list rows B1 to B13 (`cofoundaz/docs/checklist/calendar-prd-gaps.md`). It **adds to** `fe-integration-guide-calendar.md`
(events CRUD and `/milestones` keep the shapes documented there, plus the new fields listed in section 2).

**Every body below is pasted verbatim from a live run** of `e2e/test_calendar_meetings_sync.py` against a running server
(captures in `e2e/_captures/calendar_v2/`, one file per body; the same run is re-derivable with `scripts/e2e_run.sh`). Emails
like `guest-xxxx@example.com` are throwaway test addresses. The OAuth `state` was replaced by `<signed-state-redacted>` before
capture (it is a signed token). **Google itself is a fake in these captures** (`CALENDAR_PROVIDER_STUB`): the shapes of OUR
endpoints are real, the account `founder.stub@gmail.example` and the events "(from Google)" come from the fake. The last
section lists exactly what was and was not exercised live.

Base URL `/api/v1/calendar`. Envelope: success `{"data": ..., "meta": null}`, failure `{"error": {"code", "message", "field_errors"}}`.
Auth: bearer token + `X-Workspace-Id`. **Founder and team member** manage everything. Every other role can reach only
`GET /meetings` and `GET /meetings/{id}`, for meetings it is invited to, read-only (section 4). Everything else is `403`.

## 1. What it is, in the FE's terms

One calendar page with five toggleable **layers** (Roadmap milestones, Compliance deadlines, Meetings, Team events, Marketing
schedule) plus the user's own Google events; **meetings** you can invite people to, with a post-meeting summary written from notes
you paste; **reminders** per kind of event; and a **Sync** page that connects the user's Google Calendar. Month, Week and Agenda
all call the same `GET /feed` with a different range.

## 2. Event fields added to the existing events API

`POST/PATCH /events` and every event body now also carry `end_date` (null = single day), `timezone` (IANA, default `Africa/Lagos`),
`location`, `video_url` (http/https only) and `visibility` (`team` | `private`). **Private events are visible only to their creator
and invitees everywhere** (list, get, feed, reminders): a teammate gets 404 on the id. `start_time`/`end_time` stay naive
wall-clock `HH:MM` in `timezone`. `type: "meeting"` created through `/events` also gets a meeting row, so it opens in the meeting drawer.

## 3. The feed (Month / Week / Agenda)

`GET /feed?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&layers=a,b` (both dates required, at most 366 days apart, `layers` optional =
all). Layers: `milestone`, `compliance` (always empty until Legal ships; the toggle should still exist), `meeting`, `team`
(events of type task/deadline/milestone made in Calendar), `marketing`, `external` (the viewer's OWN Google events), `busy`
(teammates' Google events).

```json
{
  "status": 200,
  "body": {
    "data": {
      "items": [
        {
          "end_date": null,
          "start_time": null,
          "end_time": null,
          "all_day": false,
          "timezone": "Africa/Lagos",
          "status": null,
          "ref": {
            "type": "event",
            "id": "dd667d8d-5704-4754-914e-23b234901e25"
          },
          "private": true,
          "id": "dd667d8d-5704-4754-914e-23b234901e25",
          "layer": "team",
          "kind": "task",
          "title": "Private errand",
          "date": "2026-11-01"
        },
        {
          "end_date": null,
          "start_time": null,
          "end_time": null,
          "all_day": true,
          "timezone": "Africa/Lagos",
          "status": null,
          "ref": {
            "type": "event",
            "id": "088fd45f-7277-4bc0-b368-e1ee859927bd"
          },
          "private": false,
          "id": "088fd45f-7277-4bc0-b368-e1ee859927bd",
          "layer": "team",
          "kind": "deadline",
          "title": "VAT filing",
          "date": "2026-11-01"
        },
        {
          "end_date": null,
          "start_time": "10:00",
          "end_time": "11:00",
          "all_day": false,
          "timezone": "Africa/Lagos",
          "status": null,
          "ref": {
            "type": "event",
            "id": "3e159768-b736-4b2d-829d-6db36d7246b7"
          },
          "private": false,
          "id": "3e159768-b736-4b2d-829d-6db36d7246b7",
          "layer": "meeting",
          "kind": "meeting",
          "title": "Seed round intro",
          "date": "2026-11-01"
        },
        {
          "end_date": null,
          "start_time": null,
          "end_time": null,
          "all_day": true,
          "timezone": "Africa/Lagos",
          "status": "not_started",
          "ref": {
            "type": "milestone",
            "id": "08d2feb0-680a-48cb-86ae-c2ef1ae77c6f"
          },
          "private": false,
          "id": "milestone-08d2feb0-680a-48cb-86ae-c2ef1ae77c6f",
          "layer": "milestone",
          "kind": "milestone",
          "title": "Validate demand",
          "date": "2026-11-12"
        }
      ],
      "counts": {
        "milestone": 1,
        "compliance": 0,
        "meeting": 1,
        "team": 2,
        "marketing": 0,
        "external": 0,
        "busy": 0
      },
      "layers": [
        "milestone",
        "compliance",
        "meeting",
        "team",
        "marketing",
        "external",
        "busy"
      ]
    },
    "meta": null
  }
}
```

Field notes (these are the nesting traps):
- `date` is the start day; `end_date` is null for single-day items. Multi-day items appear once, with `end_date`: draw the span.
- `start_time`/`end_time` are null with `all_day: true` (milestones, marketing items carry `start_time` and `all_day: false`).
- `ref` tells the drawer what to open: `{"type": "event"|"milestone"|"marketing_post"|"external", "id"}`. `milestone` opens in
  Roadmap, `marketing_post` in Marketing; only `event` is editable here. `ref` is **null** on `busy` items.
- A milestone's `id` is `milestone-<uuid>` and a marketing item's `marketing-<uuid>`: use `ref.id` for the real id.
- `status` is set only for `milestone` (`on_track | at_risk | complete | not_started`) and `marketing` (`scheduled | published`).
- `private: true` on an `external`/`busy` item is always true; on an event it means `visibility: "private"` (show a lock).
- `busy` items have `title: "Busy"` by construction. Never try to show a teammate's Google title; the server does not send one.
- `counts` has a key for every layer; use it for the legend badges. `layers` is the full list in legend order.

Filtered by layer, and the range error:

```json
{
  "status": 200,
  "body": {
    "data": {
      "items": [
        {
          "end_date": null,
          "start_time": "10:00",
          "end_time": "11:00",
          "all_day": false,
          "timezone": "Africa/Lagos",
          "status": null,
          "ref": {
            "type": "event",
            "id": "3e159768-b736-4b2d-829d-6db36d7246b7"
          },
          "private": false,
          "id": "3e159768-b736-4b2d-829d-6db36d7246b7",
          "layer": "meeting",
          "kind": "meeting",
          "title": "Seed round intro",
          "date": "2026-11-01"
        },
        {
          "end_date": null,
          "start_time": null,
          "end_time": null,
          "all_day": true,
          "timezone": "Africa/Lagos",
          "status": "not_started",
          "ref": {
            "type": "milestone",
            "id": "08d2feb0-680a-48cb-86ae-c2ef1ae77c6f"
          },
          "private": false,
          "id": "milestone-08d2feb0-680a-48cb-86ae-c2ef1ae77c6f",
          "layer": "milestone",
          "kind": "milestone",
          "title": "Validate demand",
          "date": "2026-11-12"
        }
      ],
      "counts": {
        "milestone": 1,
        "compliance": 0,
        "meeting": 1,
        "team": 0,
        "marketing": 0,
        "external": 0,
        "busy": 0
      },
      "layers": [
        "milestone",
        "compliance",
        "meeting",
        "team",
        "marketing",
        "external",
        "busy"
      ]
    },
    "meta": null
  }
}
```

```json
{
  "status": 422,
  "body": {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Ask for at most 366 days at a time.",
      "field_errors": [
        {
          "field": "date_to",
          "message": "Ask for at most 366 days at a time."
        }
      ]
    }
  }
}
```

Milestones page: `GET /milestones` now returns `days_left` (negative once overdue, null if undated), `tasks_done`/`tasks_total`
(real roadmap task counts, so "11 of 13 tasks complete" is backed) and a `summary {total, complete}`. **There is no "this quarter" count.**

```json
{
  "status": 200,
  "body": {
    "data": {
      "milestones": [
        {
          "id": "08d2feb0-680a-48cb-86ae-c2ef1ae77c6f",
          "title": "Validate demand",
          "due_on": "2026-11-12",
          "status": "todo",
          "calendar_status": "not_started",
          "progress": 0,
          "days_left": 14,
          "tasks_done": 0,
          "tasks_total": 3
        },
        {
          "id": "47f7cf65-be94-40c6-81e6-c13db9478c31",
          "title": "Pricing test",
          "due_on": "2026-12-03",
          "status": "todo",
          "calendar_status": "not_started",
          "progress": 0,
          "days_left": 35,
          "tasks_done": 0,
          "tasks_total": 2
        },
        {
          "id": "c5396bda-19f8-4b01-90e8-7fe53ff858c0",
          "title": "Core flow",
          "due_on": "2027-01-07",
          "status": "todo",
          "calendar_status": "not_started",
          "progress": 0,
          "days_left": 70,
          "tasks_done": 0,
          "tasks_total": 2
        }
      ],
      "summary": {
        "total": 3,
        "complete": 0
      }
    },
    "meta": null
  }
}
```

## 4. Meetings

`POST /meetings` creates the event and the meeting together. Attendees are matched by email to active workspace members
(`internal: true`, notified in-app, **never emailed**) or kept as externals (`invite_state` `pending` then `sent`, emailed with an
`.ics` by the worker within seconds; `failed` if the mail bounced at send). Emails are lower-cased and de-duplicated.

```json
{
  "status": 200,
  "body": {
    "data": {
      "id": "3e159768-b736-4b2d-829d-6db36d7246b7",
      "title": "Seed round intro",
      "event_date": "2026-11-01",
      "end_date": null,
      "start_time": "10:00",
      "end_time": "11:00",
      "timezone": "Africa/Lagos",
      "location": "Lagos office",
      "video_url": "https://meet.example.com/seed-intro",
      "description": null,
      "visibility": "team",
      "created_by": "3d130cd3-1f95-4602-b63e-e3e836950abe",
      "agenda": "1. Traction\n2. Ask",
      "assistant_enabled": true,
      "assistant_state": "idle",
      "assistant_error": null,
      "summary": null,
      "decisions": [],
      "action_items": [],
      "has_transcript": false,
      "summarised_at": null,
      "attendees": [
        {
          "email": "guest-14f871@example.com",
          "name": "Guest Investor",
          "user_id": null,
          "internal": false,
          "invite_state": "pending"
        }
      ],
      "can_manage": true,
      "transcript": null
    },
    "meta": null
  }
}
```

```json
{
  "status": 422,
  "body": {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Please check the highlighted fields.",
      "field_errors": [
        {
          "field": "title",
          "message": "String should have at least 1 character"
        },
        {
          "field": "timezone",
          "message": "Value error, timezone must be an IANA name such as Africa/Lagos"
        }
      ]
    }
  }
}
```

The invite email (external attendees only), and its `.ics`:

```json
{
  "to": "<guest placeholder>",
  "subject": "Ada Founder invited you: Seed round intro \u00b7 01 Nov 2026",
  "html": "<p>Ada Founder invited you to a meeting.</p><p><strong>Seed round intro</strong><br>Sun 01 Nov 2026, 10:00 (Africa/Lagos)<br>Lagos office</p><p><a href=\"https://meet.example.com/seed-intro\">Join the call</a></p>",
  "attachments": [
    "invite.ics"
  ]
}
```

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Cofoundaz//Calendar//EN
CALSCALE:GREGORIAN
METHOD:REQUEST
BEGIN:VEVENT
UID:3e159768-b736-4b2d-829d-6db36d7246b7@cofoundaz.com
SEQUENCE:0
DTSTAMP:20261029T150813Z
DTSTART:20261101T090000Z
DTEND:20261101T100000Z
SUMMARY:Seed round intro
STATUS:CONFIRMED
ORGANIZER;CN=Ada Founder:mailto:placeholder@example.invalid
ATTENDEE;CN=Guest Investor;RSVP=TRUE:mailto:placeholder@example.invalid
DESCRIPTION:https://meet.example.com/seed-intro
LOCATION:Lagos office
URL:https://meet.example.com/seed-intro
END:VEVENT
END:VCALENDAR
```

After the worker ran, `invite_state` is `sent`:

```json
{
  "status": 200,
  "body": {
    "data": {
      "id": "3e159768-b736-4b2d-829d-6db36d7246b7",
      "title": "Seed round intro",
      "event_date": "2026-11-01",
      "end_date": null,
      "start_time": "10:00",
      "end_time": "11:00",
      "timezone": "Africa/Lagos",
      "location": "Lagos office",
      "video_url": "https://meet.example.com/seed-intro",
      "description": null,
      "visibility": "team",
      "created_by": "3d130cd3-1f95-4602-b63e-e3e836950abe",
      "agenda": "1. Traction\n2. Ask",
      "assistant_enabled": true,
      "assistant_state": "idle",
      "assistant_error": null,
      "summary": null,
      "decisions": [],
      "action_items": [],
      "has_transcript": false,
      "summarised_at": null,
      "attendees": [
        {
          "email": "guest-14f871@example.com",
          "name": "Guest Investor",
          "user_id": null,
          "internal": false,
          "invite_state": "sent"
        }
      ],
      "can_manage": true,
      "transcript": null
    },
    "meta": null
  }
}
```

- `GET /meetings?when=upcoming|past|all`:

```json
{
  "status": 200,
  "body": {
    "data": {
      "meetings": [
        {
          "id": "3e159768-b736-4b2d-829d-6db36d7246b7",
          "title": "Seed round intro",
          "event_date": "2026-11-01",
          "end_date": null,
          "start_time": "10:00",
          "end_time": "11:00",
          "timezone": "Africa/Lagos",
          "location": "Lagos office",
          "video_url": "https://meet.example.com/seed-intro",
          "description": null,
          "visibility": "team",
          "created_by": "3d130cd3-1f95-4602-b63e-e3e836950abe",
          "agenda": "1. Traction\n2. Ask",
          "assistant_enabled": true,
          "assistant_state": "idle",
          "assistant_error": null,
          "summary": null,
          "decisions": [],
          "action_items": [],
          "has_transcript": false,
          "summarised_at": null,
          "attendees": [
            {
              "email": "guest-14f871@example.com",
              "name": "Guest Investor",
              "user_id": null,
              "internal": false,
              "invite_state": "sent"
            }
          ],
          "can_manage": true,
          "transcript": null
        }
      ]
    },
    "meta": null
  }
}
```

- `PATCH /meetings/{id}`: any subset; `attendees` replaces the whole set. Changing title, date/time, timezone, location or video link
  re-invites external attendees (higher SEQUENCE, same UID, so mail clients update the invite); changing only agenda/description does not.
  Removing an already-invited external sends them a cancellation. `DELETE /meetings/{id}` cancels all invited externals.
- **Other roles (mentor, accountant, legal advisor, consultant, investor)**: `GET /meetings` lists only meetings they are invited to
  (matched by user id or email), `GET /meetings/{id}` answers `404` for anything else, and the body has `can_manage: false` and **no
  `transcript` key**. They cannot write (`403`). Show them a read-only meeting drawer, no "+ New".

### Post-meeting summary (the "AI Meeting Assistant")

**There is no bot that joins the call** (decision D-CAL-1). The user pastes notes or a transcript (20 to 60,000 characters); the
toggle in the create form is "Summarise this meeting afterwards", not "Join the call". `POST /meetings/{id}/assistant` answers 202, then
poll `GET /meetings/{id}` every 2 to 3 seconds until `assistant_state` is `ready` or `failed` (a run takes seconds; give up and show
the error after 60 s). States: `idle` (never run), `queued`, `ready`, `failed` (`assistant_error` has the sentence to show; running
it again is allowed).

```json
{
  "status": 202,
  "body": {
    "data": {
      "assistant_state": "queued"
    },
    "meta": null
  }
}
```

A second click while queued:

```json
{
  "status": 409,
  "body": {
    "error": {
      "code": "SUMMARY_IN_PROGRESS",
      "message": "A summary is already being written.",
      "field_errors": []
    }
  }
}
```

```json
{
  "status": 200,
  "body": {
    "data": {
      "id": "3e159768-b736-4b2d-829d-6db36d7246b7",
      "title": "Seed round intro",
      "event_date": "2026-11-01",
      "end_date": null,
      "start_time": "10:00",
      "end_time": "11:00",
      "timezone": "Africa/Lagos",
      "location": "Lagos office",
      "video_url": "https://meet.example.com/seed-intro",
      "description": null,
      "visibility": "team",
      "created_by": "3d130cd3-1f95-4602-b63e-e3e836950abe",
      "agenda": "1. Traction\n2. Ask",
      "assistant_enabled": true,
      "assistant_state": "ready",
      "assistant_error": null,
      "summary": "[stub-llm] summary",
      "decisions": [
        "[stub-llm] decisions"
      ],
      "action_items": [
        {
          "id": "20fe6283",
          "title": "[stub-llm] action_items",
          "task_id": null,
          "assignee_id": null
        }
      ],
      "has_transcript": true,
      "summarised_at": "2026-10-29T15:08:13.554639+00:00",
      "attendees": [
        {
          "email": "guest-14f871@example.com",
          "name": "Guest Investor",
          "user_id": null,
          "internal": false,
          "invite_state": "sent"
        }
      ],
      "can_manage": true,
      "transcript": "We agreed to send the data room Monday. Ada will update the cap table by Friday. We agreed to send the data room Monday. Ada will update the cap table by Friday."
    },
    "meta": null
  }
}
```

- `summary`, `decisions[]`, `action_items[] {id, title, assignee_id, task_id}`; `task_id` is null until added to the roadmap.
- `PATCH /meetings/{id}/action-items/{item_id}` `{title?, assignee_id?}` edits one item (assignee must be an active member; `422` if not;
  `409 ITEM_ALREADY_ADDED` once it is a task).
- `POST /meetings/{id}/action-items/add-to-tasks` `{item_ids?: [..], milestone_id?}`: omitted `item_ids` = every item not yet added.
  Tasks land on the earliest unfinished roadmap milestone unless `milestone_id` is given. `409 NO_ROADMAP_MILESTONE` if the workspace
  has none (show "Create a roadmap milestone first"). Calling it again creates nothing new.

```json
{
  "status": 200,
  "body": {
    "data": {
      "created": [
        {
          "item_id": "20fe6283",
          "task_id": "787058d0-1a6c-42cd-a891-582772b401f3"
        }
      ],
      "milestone_id": "08d2feb0-680a-48cb-86ae-c2ef1ae77c6f",
      "action_items": [
        {
          "id": "20fe6283",
          "title": "[stub-llm] action_items",
          "task_id": "787058d0-1a6c-42cd-a891-582772b401f3",
          "assignee_id": null
        }
      ]
    },
    "meta": null
  }
}
```

When the summary is ready the creator and internal attendees get the in-app notification "Meeting summary ready: {title} · {n} action
items" (CTA "Review actions"). If the daily AI budget is spent the run fails with "The AI budget for today is used up. Try again tomorrow."

## 5. Reminder rules

`GET /reminder-rules` (founder and team member), `PUT /reminder-rules/{kind}` and `DELETE /reminder-rules/{kind}` (reset to default)
are **founder only** (`403` for a team member; `can_edit` tells you). Kinds: `milestone` (also used for roadmap milestones),
`compliance` (used for calendar events of type `deadline`), `meeting`. Events of type `task` have no reminder. Offsets are minutes
before the start, 1 to 20160 (14 days), at most 6, no repeats; the server returns them sorted descending. All-day items count from 09:00
in their timezone.

```json
{
  "status": 200,
  "body": {
    "data": {
      "rules": [
        {
          "kind": "milestone",
          "offsets_minutes": [
            10080,
            1440
          ],
          "is_default": true,
          "default_offsets_minutes": [
            10080,
            1440
          ]
        },
        {
          "kind": "compliance",
          "offsets_minutes": [
            20160,
            10080,
            1440
          ],
          "is_default": true,
          "default_offsets_minutes": [
            20160,
            10080,
            1440
          ]
        },
        {
          "kind": "meeting",
          "offsets_minutes": [
            15
          ],
          "is_default": true,
          "default_offsets_minutes": [
            15
          ]
        }
      ],
      "can_edit": true
    },
    "meta": null
  }
}
```

```json
{
  "status": 200,
  "body": {
    "data": {
      "rules": [
        {
          "kind": "milestone",
          "offsets_minutes": [
            10080,
            1440
          ],
          "is_default": true,
          "default_offsets_minutes": [
            10080,
            1440
          ]
        },
        {
          "kind": "compliance",
          "offsets_minutes": [
            20160,
            10080,
            1440
          ],
          "is_default": true,
          "default_offsets_minutes": [
            20160,
            10080,
            1440
          ]
        },
        {
          "kind": "meeting",
          "offsets_minutes": [
            60,
            15
          ],
          "is_default": false,
          "default_offsets_minutes": [
            15
          ]
        }
      ],
      "can_edit": true
    },
    "meta": null
  }
}
```

```json
{
  "status": 422,
  "body": {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Please check the highlighted fields.",
      "field_errors": [
        {
          "field": "offsets_minutes",
          "message": "Value error, each offset must be between 1 minute and 14 days"
        }
      ]
    }
  }
}
```

Meeting reminders use type `calendar.meeting.reminder` and also go by email: a new **Calendar** row in the notification preferences, on by default, delivered at once even for users on a digest, and skipped in quiet hours. The email was tested with unit tests only, not captured live. Other reminders stay in-app.

Notifications these produce (in-app, type `calendar.reminder` except meetings): "Meeting starting in 15 minutes: {title}", "Milestone deadline
tomorrow: {title}", "Deadline tomorrow: {title}", and "Milestone in 7 days: {title}". A reminder is never sent more than 12 hours late.
**Not captured live** (needs a wait for the clock): the scheduler firing; it is covered by unit tests that drive the scheduler tick with a fake clock.

## 6. Google sync (`/sync`)

State machine for the Google card:

| `google.status` | Show | Actions |
|---|---|---|
| `disconnected` (`connected: false`) | "Not connected" | **Connect** |
| `active` | "Connected as {account_email}", last sync time | direction radio, calendar picker, **Sync now**, **Disconnect** |
| `needs_attention` | banner "Google no longer allows access. Reconnect to resume syncing." (`last_error`) | **Reconnect** (same Connect call), Disconnect |

If `configured` is false the server has no Google credentials: show the card disabled with "Not available on this server".
Outlook is `{"available": false}`: show "Coming soon". There is no Apple card.

```json
{
  "status": 200,
  "body": {
    "data": {
      "configured": true,
      "google": {
        "provider": "google",
        "connected": false,
        "status": "disconnected",
        "account_email": null,
        "direction": "two_way",
        "calendar_id": "primary",
        "last_sync_at": null,
        "last_sync_summary": null,
        "last_error": null,
        "needs_attention": false
      },
      "outlook": {
        "provider": "outlook",
        "available": false
      }
    },
    "meta": null
  }
}
```

```json
{
  "status": 501,
  "body": {
    "error": {
      "code": "FEATURE_NOT_ENABLED",
      "message": "This feature isn't available yet.",
      "field_errors": []
    }
  }
}
```

**Connect** is a redirect flow:

1. `POST /sync/google/connect` returns `auth_url` (valid 10 minutes) **and sets an HttpOnly cookie** `cfz_cal_oauth` on the API origin.
   Call it with `credentials: "include"` exactly like the refresh-token calls, and then `window.location.assign(auth_url)`.
2. Google sends the browser to the API's callback, which redirects (302) to the FE: `{APP_BASE_URL}/calendar/sync?connected=google`
   or `?error=<code>`. **The FE must read that query string on load**, show a toast, and then `GET /sync`.
3. The same browser must finish what it started: the cookie ties the consent to it, so opening the link in another browser or
   profile ends in `?error=invalid_state`.

```json
{
  "status": 200,
  "body": {
    "data": {
      "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=e2e-google-client-id.apps.example&redirect_uri=http%3A%2F%2F127.0.0.1%3A8011%2Fapi%2Fv1%2Fcalendar%2Fsync%2Fgoogle%2Fcallback&response_type=code&scope=openid+email+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.events+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcalendar.calendarlist.readonly&access_type=offline&prompt=consent&state=<signed-state-redacted>",
      "expires_in": 600
    },
    "meta": null
  }
}
```

Callback outcomes (the API's 302 `Location`):

```json
{
  "status": 302,
  "location": "http://localhost:3000/calendar/sync?connected=google"
}
```

| `?error=` | Message to show |
|---|---|
| `access_denied` | "You cancelled the Google connection." |
| `invalid_state` | "That connection link expired or was started in another browser. Try Connect again." (also a replayed link) |
| `exchange_failed` | "Google rejected the connection. Try again." |
| `no_refresh_token` | "Google did not grant offline access. Remove Cofoundaz from your Google account permissions and connect again." |
| `not_a_member` | "You are no longer a member of this workspace." |

```json
{
  "status": 302,
  "location": "http://localhost:3000/calendar/sync?error=invalid_state"
}
```

```json
{
  "status": 302,
  "location": "http://localhost:3000/calendar/sync?error=invalid_state"
}
```

```json
{
  "status": 302,
  "location": "http://localhost:3000/calendar/sync?error=access_denied"
}
```

After a successful callback an initial sync runs in the background (a few seconds); `last_sync_at` is null until it finishes, so poll
`GET /sync` every 3 s up to 30 s, or just show "Syncing...".

```json
{
  "status": 200,
  "body": {
    "data": {
      "configured": true,
      "google": {
        "provider": "google",
        "connected": true,
        "status": "active",
        "account_email": "founder.stub@gmail.example",
        "direction": "two_way",
        "calendar_id": "primary",
        "last_sync_at": "2026-10-29T15:08:13.855863+00:00",
        "last_sync_summary": {
          "pulled": 2,
          "pushed": 3,
          "removed": 0
        },
        "last_error": null,
        "needs_attention": false
      },
      "outlook": {
        "provider": "outlook",
        "available": false
      }
    },
    "meta": null
  }
}
```

`last_sync_summary` is `{pulled, removed, pushed}` from the last cycle. **No token, ciphertext or nonce is ever returned.**

- `GET /sync/google/calendars` fills the picker. `writable: false` calendars (holidays, subscribed) cannot be chosen for two-way or push.

```json
{
  "status": 200,
  "body": {
    "data": {
      "calendars": [
        {
          "id": "primary",
          "name": "founder.stub@gmail.example",
          "primary": true,
          "writable": true
        },
        {
          "id": "team@group.calendar.stub",
          "name": "Team calendar",
          "primary": false,
          "writable": true
        },
        {
          "id": "holidays#stub@group.v.calendar.google.com",
          "name": "Holidays",
          "primary": false,
          "writable": false
        }
      ],
      "selected": "primary"
    },
    "meta": null
  }
}
```

- `PATCH /sync/google` `{direction?, calendar_id?}`. Directions: `two_way` ("Two-way sync (recommended)"), `pull_only` ("Pull external events only"),
  `push_only` ("Push Cofoundaz events only"). `422` on an unknown calendar or a read-only one with push. Changing the calendar drops the
  imported events and re-syncs.

```json
{
  "status": 200,
  "body": {
    "data": {
      "provider": "google",
      "connected": true,
      "status": "active",
      "account_email": "founder.stub@gmail.example",
      "direction": "pull_only",
      "calendar_id": "primary",
      "last_sync_at": "2026-10-29T15:08:13.855863+00:00",
      "last_sync_summary": {
        "pulled": 2,
        "pushed": 3,
        "removed": 0
      },
      "last_error": null,
      "needs_attention": false
    },
    "meta": null
  }
}
```

- `POST /sync/google/sync` (**Sync now**) runs a cycle and returns it. A second click within 10 s returns `{"summary": {"skipped": true}}`.

```json
{
  "status": 200,
  "body": {
    "data": {
      "summary": {
        "skipped": true
      },
      "connection": {
        "provider": "google",
        "connected": true,
        "status": "active",
        "account_email": "founder.stub@gmail.example",
        "direction": "two_way",
        "calendar_id": "primary",
        "last_sync_at": "2026-10-29T15:08:13.855863+00:00",
        "last_sync_summary": {
          "pulled": 2,
          "pushed": 3,
          "removed": 0
        },
        "last_error": null,
        "needs_attention": false
      }
    },
    "meta": null
  }
}
```

- Imported events appear in the feed as `external` (yours, with titles) and `busy` (teammates see only a "Busy" block). Privacy note to show
  verbatim: "External event details stay private to you; teammates see only busy blocks."

```json
{
  "status": 200,
  "body": {
    "data": {
      "items": [
        {
          "end_date": null,
          "start_time": "10:00",
          "end_time": "11:00",
          "all_day": false,
          "timezone": "Africa/Lagos",
          "status": null,
          "ref": {
            "type": "external",
            "id": "f8d800cd-2ead-4e04-91f0-4359230ad2da"
          },
          "private": true,
          "id": "ext-f8d800cd-2ead-4e04-91f0-4359230ad2da",
          "layer": "external",
          "kind": "external",
          "title": "Investor coffee (from Google)",
          "date": "2026-10-30"
        },
        {
          "end_date": null,
          "start_time": null,
          "end_time": null,
          "all_day": true,
          "timezone": "Africa/Lagos",
          "status": null,
          "ref": {
            "type": "external",
            "id": "4f0ab56b-b7fc-4824-90f2-9c745a5780c4"
          },
          "private": true,
          "id": "ext-4f0ab56b-b7fc-4824-90f2-9c745a5780c4",
          "layer": "external",
          "kind": "external",
          "title": "Family wedding (from Google)",
          "date": "2026-11-01"
        }
      ],
      "counts": {
        "milestone": 0,
        "compliance": 0,
        "meeting": 0,
        "team": 0,
        "marketing": 0,
        "external": 2,
        "busy": 0
      },
      "layers": [
        "milestone",
        "compliance",
        "meeting",
        "team",
        "marketing",
        "external",
        "busy"
      ]
    },
    "meta": null
  }
}
```

- `DELETE /sync/google` revokes at Google, deletes the stored tokens and the imported events, and keeps your Cofoundaz events. Confirm copy:
  "Disconnect Google? Imported events are removed; your Cofoundaz events stay." `404` if not connected.

```json
{
  "status": 200,
  "body": {
    "data": {
      "disconnected": true,
      "imported_events_removed": 2
    },
    "meta": null
  }
}
```

```json
{
  "status": 404,
  "body": {
    "error": {
      "code": "NOT_FOUND",
      "message": "Not found.",
      "field_errors": []
    }
  }
}
```

What gets pushed (decision D-CAL-2): only events created in Calendar by this user or where they are an invited member (including
meetings). Roadmap milestones, marketing posts and compliance dates are not. Cofoundaz sends its own invite emails; Google is told
not to send any, so attendees never get two.

## 7. UX consequences to surface

- A **private** event or meeting never shows for teammates; show a lock so the creator knows.
- Editing a meeting's time **re-emails** external attendees. Say so in the save confirmation ("Attendees will be sent an update").
- Deleting a meeting **cancels** it for invited externals. Confirm with that wording.
- A removed workspace member is disconnected from Google sync automatically on the next cycle.
- If Google access breaks (`needs_attention`) nothing is synced until the user reconnects; keep the banner on the Calendar page too,
  not only on Sync. While the Google app is in "Testing" this happens **every 7 days** (D-CAL-3).
- Teammates' calendars are not visible: only "Busy". Do not render titles even if you find one.
- The summary is generated from what the user pastes; label it "AI-generated, check before sharing".

## 8. Verification table

| Behaviour | Verified |
|---|---|
| Feed, filters, range error, milestones fields | Live (e2e captures above) |
| Meeting create/list/get, invite email + ICS, invite state | Live |
| Assistant (stub model): start, in-progress 409, ready, add to tasks | Live. The text is the stub model's; a real-model run is for the FE walk |
| Reminder rules get/put/invalid | Live |
| Reminder firing (scheduler, offsets, dedupe, recipients) | Unit tests only (fake clock); no live clock wait |
| Sync status/connect/callback/replay/other-browser/denied/calendars/direction/sync-now/disconnect | Live **against the in-process Google fake** |
| Real Google consent screen, real tokens, real refresh, real Calendar API payloads | **NOT verified.** Needs a Google test user; the first real connect is manual |
| Refresh failure → `needs_attention`, 410 resync, push recreate after Google delete, private/removal cleanup, removed member | Unit tests against the fake only |
| Invited-only access for mentor/accountant/investor, private visibility | Unit tests (a single-user e2e workspace cannot invite other roles) |
| Outlook | Not built (501 `FEATURE_NOT_ENABLED`) |
