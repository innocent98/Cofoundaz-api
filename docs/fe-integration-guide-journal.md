# FE Integration Guide: Founder Journal (Module 21)

Every request and response body below is pasted verbatim from `e2e/test_journal.py` and
`e2e/test_journal_gaps.py` run against a live server (`e2e/_captures/journal/` and
`e2e/_captures/journal_gaps/`). Only two things are trimmed, and both are marked inline: the 12-week
array in `/stats` and the entry list in `/export`. Unlock tokens are replaced by
`<redacted-unlock-token>` (the real value is a signed string you must treat as opaque). IDs and dates
are from the journey run. The verification table at the end says what was and was not exercised live.

Base path `/api/v1/journal`. Every route needs `Authorization: Bearer <access token>` and
`X-Workspace-Id: <workspace id>` (`GET /auth/me` -> `data.active_workspace_id`). Success bodies are
`{"data": ..., "meta": null}`; errors are `{"error": {"code", "message", "field_errors": []}}`.

## 0. Rules that shape the whole UI

- **Founder only, and author only.** Every route answers **403** `FORBIDDEN` to every other role. A second founder in the same
  workspace has their own, separate journal. Do not show Journal navigation to anyone but a founder; if a non-founder opens
  `/journal` by URL, show "The journal is private to the founder" on a 403.
- **Optional passcode (the lock screen).** `GET /settings` says `passcode_enabled`. When it is `true`, every route **except**
  `/settings` (GET), `/passcode`, `/unlock` and `/lock` answers **423 `JOURNAL_LOCKED`** unless the request carries
  `X-Journal-Token: <token>` from `/unlock` (or `/passcode`). Tokens last 30 minutes (`expires_in: 1800`), are lost on
  "Lock journal", on changing or removing the passcode. Keep the token in memory (not localStorage) and send it on every
  journal call; on any **423**, show the lock screen. When `passcode_enabled` is `false` there is no lock screen at all.
- **The text is encrypted on the server, not by the passcode.** Copy must say entries are encrypted at rest and readable only by
  the founder in the app. It must **not** say the key is "derived from your PIN" or that nobody at Cofoundaz could ever read it
  (see `docs/fe-handoff/journal.md` for the approved copy).
- **One entry per founder per day.** `POST /entries` for a date that already has an entry **replaces** it (this is the autosave
  path). Load today's entry with `GET /entries/by-date/{date}` before showing the editor, so you never overwrite it blind.
- **Dates are the founder's local calendar date** (`YYYY-MM-DD`), not UTC. Never use `toISOString()`. The API accepts today and
  one day ahead (timezone skew) and rejects anything later with 422.

## 1. Entries

### `POST /entries`: create or replace the entry for a date (autosave)

Request `{"date": "YYYY-MM-DD", "content": "...", "mood": "rough|meh|okay|good|great", "stress": 1-10}`; `content` must not be blank.

Mood labels in the designed UI map as: Rough = `rough`, Heavy = `meh`, Steady = `okay`, Good = `good`, Great = `great`.

200 (not 201):

```json
{
  "data": {
    "id": "8a2c60fe-8df4-4715-a8d3-cacb2de9dac6",
    "startup_id": "3752df80-2c4b-4191-a244-e4cee1b00086",
    "founder_id": "81edbe13-47dd-4702-854c-edd2e3d978e4",
    "date": "2026-10-29",
    "content": "Shipped the invite flow.\nStill unsure about pricing.",
    "mood": "good",
    "stress": 4
  },
  "meta": null
}
```

### `GET /entries/by-date/{date}`: today's entry (or any day)

200 returns the same body as above (full `content`, `mood`, `stress`):

```json
{
  "data": {
    "id": "d8ca0d16-4d64-4220-ace4-3b09d7c6a616",
    "startup_id": "5c94ac56-9fed-4ed5-a3c8-6e0461b58695",
    "founder_id": "fa6983e9-a98f-478c-9e88-1ce0b8dfa709",
    "date": "2026-10-29",
    "content": "Day 6 first line\nSecond line about pricing and runway.",
    "mood": "great",
    "stress": 4
  },
  "meta": null
}
```

No entry for that day: **404**, which is the normal "empty editor" case, not an error to show:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### `GET /entries`: past entries list

Query: `skip` (default 0), `limit` (1 to 100, default 20), `month=YYYY-MM`, `search=<text>` (own entries only, case-insensitive,
matches the full text, scans the newest 2,000 entries). `total` is the count for the same filter. **List items carry `preview`
(first 280 characters) and `word_count`, not the full `content`**: load the full text with `GET /entries/{id}` when a card is
expanded. `first_line` is the first line only.

```json
{
  "data": {
    "entries": [
      {
        "id": "d8ca0d16-4d64-4220-ace4-3b09d7c6a616",
        "date": "2026-10-29",
        "mood": "great",
        "stress": 4,
        "first_line": "Day 6 first line",
        "preview": "Day 6 first line\nSecond line about pricing and runway.",
        "word_count": 10
      },
      {
        "id": "263838d2-40c3-4a8d-9c0c-049602192741",
        "date": "2026-10-28",
        "mood": "good",
        "stress": 5,
        "first_line": "Day 5 first line",
        "preview": "Day 5 first line\nSecond line about pricing and runway.",
        "word_count": 10
      },
      {
        "id": "9ea5c2b5-ff8e-4d23-9d81-a981ca0688a9",
        "date": "2026-10-27",
        "mood": "good",
        "stress": 6,
        "first_line": "Day 4 first line",
        "preview": "Day 4 first line\nSecond line about pricing and runway.",
        "word_count": 10
      }
    ],
    "total": 6
  },
  "meta": null
}
```

Month filter plus search (`?month=2026-10&search=Day%203`):

```json
{
  "data": {
    "entries": [
      {
        "id": "c3ffed77-3a9f-46a7-984e-6563dec9f0ff",
        "date": "2026-10-26",
        "mood": "okay",
        "stress": 7,
        "first_line": "Day 3 first line",
        "preview": "Day 3 first line\nSecond line about pricing and runway.",
        "word_count": 10
      }
    ],
    "total": 1
  },
  "meta": null
}
```

A malformed month is 422:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "month must look like 2026-09.",
    "field_errors": [
      {
        "field": "month",
        "message": "Use YYYY-MM."
      }
    ]
  }
}
```

### `GET /entries/months`: the "month jump" list

Newest first; use it to fill the month picker and to hide months with no entries.

```json
{
  "data": {
    "months": [
      {
        "month": "2026-10",
        "count": 6
      }
    ]
  },
  "meta": null
}
```

### `GET /entries/{id}`, `PATCH /entries/{id}`, `DELETE /entries/{id}`

`GET` returns the entry (shape as `POST`). `PATCH` takes any of `content`, `mood`, `stress` (explicit `null` is 422; omit the
field instead). `DELETE` returns:

```json
{
  "data": {
    "id": "8a2c60fe-8df4-4715-a8d3-cacb2de9dac6",
    "deleted": true
  },
  "meta": null
}
```

An id that is not yours, or does not exist, is the same **404** (no enumeration).

## 2. Stats (Today header pill and the Mood tab cards)

`GET /stats?today=YYYY-MM-DD` (send the founder's local date; the API ignores a value more than a day away from its own).

- `entries_this_month` -> "12 entries this month"; `entries_per_week` -> "about 3 a week" (average over the last 28 days).
- `average_mood` is the label for this calendar month; `previous_month_average_mood` -> "up from Heavy in June" (null when
  last month had no entries).
- `current_streak_days` stays alive until the day ends (it counts from yesterday if today is not written yet);
  `longest_streak_days` + `longest_streak_ended_on` -> "Longest streak 9 days, in May".
- `weeks`: the last 12 Monday-based weeks, oldest first. Empty weeks are present with `entries: 0` and null averages.
- `wrote_today` drives the "write today" nudge.

Empty journal (first run; show the first-run copy "The honest record of the journey. Two minutes a day is enough."):

```json
{
  "data": {
    "total_entries": 0,
    "entries_this_month": 0,
    "entries_per_week": 0.0,
    "average_mood": null,
    "average_mood_score": null,
    "previous_month_average_mood": null,
    "current_streak_days": 0,
    "longest_streak_days": 0,
    "longest_streak_ended_on": null,
    "wrote_today": false,
    "weeks": [
      {
        "week_start": "2026-08-10",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-08-17",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-08-24",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-08-31",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-09-07",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-09-14",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-09-21",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-09-28",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-10-05",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-10-12",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-10-19",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      {
        "week_start": "2026-10-26",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      }
    ]
  },
  "meta": null
}
```

With entries (the `weeks` array is trimmed here to its first and last item):

```json
{
  "data": {
    "total_entries": 6,
    "entries_this_month": 6,
    "entries_per_week": 1.5,
    "average_mood": "okay",
    "average_mood_score": 3.17,
    "previous_month_average_mood": null,
    "current_streak_days": 6,
    "longest_streak_days": 6,
    "longest_streak_ended_on": "2026-10-29",
    "wrote_today": true,
    "weeks": [
      {
        "week_start": "2026-08-10",
        "entries": 0,
        "average_mood": null,
        "average_stress": null
      },
      "... 10 more weeks, same shape ...",
      {
        "week_start": "2026-10-26",
        "entries": 4,
        "average_mood": 4,
        "average_stress": 5.5
      }
    ]
  },
  "meta": null
}
```

## 3. Mood trend and annotations

`GET /mood?range=4w|12w|6m|1y` (or `start_date` / `end_date`, plus `limit` 1 to 365, default 90). `range` is the chart's
window; an unknown value is 422. `annotations` are roadmap milestones completed inside the window ("Completed: ..."); draw
them as markers on the date. Points are one per day with an entry, oldest first.

```json
{
  "data": {
    "points": [
      {
        "date": "2026-10-24",
        "mood": "rough",
        "stress": 9
      },
      {
        "date": "2026-10-25",
        "mood": "meh",
        "stress": 8
      },
      {
        "date": "2026-10-26",
        "mood": "okay",
        "stress": 7
      },
      {
        "date": "2026-10-27",
        "mood": "good",
        "stress": 6
      },
      {
        "date": "2026-10-28",
        "mood": "good",
        "stress": 5
      },
      {
        "date": "2026-10-29",
        "mood": "great",
        "stress": 4
      }
    ],
    "annotations": [
      {
        "date": "2026-10-29",
        "label": "Completed: Validate demand"
      }
    ]
  },
  "meta": null
}
```

## 4. Insights (Reflections tab) and the supportive card

`GET /insights?today=YYYY-MM-DD`. These are **rule-based patterns computed from mood, stress and dates only** (never from what
was written, and never an AI reading the text). Under 5 entries `insights` is empty and `entries_needed` says how many more are
needed; show "Write {entries_needed} more entries to see patterns". `kind` is one of `weekday_stress`, `deadline_stress`,
`mood_shift`, `streak`, `great_days`; render `title` and `body` as given.

```json
{
  "data": {
    "insights": [
      {
        "kind": "streak",
        "title": "6 days in a row",
        "body": "You have written 6 days running. Your longest run is 6."
      }
    ],
    "supportive_card": null,
    "entries_needed": 0
  },
  "meta": null
}
```

`supportive_card` is `null` unless the last 14 days hold at least 7 entries and every one is `rough` or `meh`. Then it is an object
with `title` and `body` (copy from the PRD, show it as a dismissible, gentle card, never a warning). Dismiss it with
`POST /insights/supportive/dismiss`; it will not return for 30 days:

```json
{
  "data": {
    "dismissed": true
  },
  "meta": null
}
```

The card's shape when it appears (`{"title": "It looks like it's been heavy lately.", "body": "Building is hard. Talking to someone you trust, or a professional, can genuinely help."}`) was **verified by the unit test, not captured live** (the journey's moods are not low for 7 straight days).

## 5. Prompts

`GET /prompts/today` returns `{prompt, dismissed}`. The first read of the day can be a static prompt; the AI-personalised one
replaces it in place after the worker runs (poll again once if you want it), grounded only in the roadmap and mission, never in
the journal.

```json
{
  "data": {
    "prompt": "What would you change if you could replay today?",
    "dismissed": false
  },
  "meta": null
}
```

After the AI upgrade (live, stub model; the real text is a question):

```json
{
  "data": {
    "prompt": "[stub-llm] prompt",
    "dismissed": false
  },
  "meta": null
}
```

`POST /prompts/today/dismiss` hides the card for the rest of the day (`dismissed: true` on later reads):

```json
{
  "data": {
    "dismissed": true
  },
  "meta": null
}
```
```json
{
  "data": {
    "prompt": "What would you change if you could replay today?",
    "dismissed": true
  },
  "meta": null
}
```

`GET /prompts/library?theme=decisions|energy|team|wins` (no `theme` = all four). **"Another" in the Today tab cycles through
this list on the client** (pick the next prompt of the next theme); no extra call is needed.

```json
{
  "data": {
    "themes": {
      "wins": [
        "What went well today that you do not want to overlook?",
        "What did you finish this week that you were unsure you could?",
        "What is a small win from today worth remembering?",
        "What did a customer say that made the work feel real?",
        "What are you proud of from the last month?"
      ]
    }
  },
  "meta": null
}
```

## 6. Settings, passcode and lock

`GET /settings` (works while locked):

```json
{
  "data": {
    "passcode_enabled": false,
    "locked_until": null,
    "daily_prompt_notifications": false,
    "evening_reminder": false
  },
  "meta": null
}
```

`PUT /settings` takes `daily_prompt_notifications` and/or `evening_reminder` (booleans). Unknown fields are 422:

```json
{
  "data": {
    "passcode_enabled": false,
    "locked_until": null,
    "daily_prompt_notifications": true,
    "evening_reminder": false
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
        "field": "unknown",
        "message": "Extra inputs are not permitted"
      }
    ]
  }
}
```

- `evening_reminder`: an in-app notification (type `journal.reminder`) at 20:00 Lagos time if no entry exists for that day.
- `daily_prompt_notifications`: an in-app notification (type `journal.prompt.daily`) at 09:00. Both have fixed, generic copy and
  deep link `/journal` (`data.href`); neither contains any journal text.

### `PUT /passcode`: set or change the passcode

Body `{"passcode": "4821"}` for the first one; `{"passcode": "9999", "current_passcode": "4821"}` to change. 4 to 12 digits.
Returns an unlock token so the screen stays open:

```json
{
  "data": {
    "token": "<redacted-unlock-token>",
    "expires_in": 1800
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
        "field": "passcode",
        "message": "String should have at least 4 characters"
      }
    ]
  }
}
```

A wrong or missing `current_passcode` on a change is **403 `PASSCODE_INCORRECT`**.

### What a locked journal answers

```json
{
  "error": {
    "code": "JOURNAL_LOCKED",
    "message": "Unlock your journal to continue.",
    "field_errors": []
  }
}
```
```json
{
  "data": {
    "passcode_enabled": true,
    "locked_until": null,
    "daily_prompt_notifications": true,
    "evening_reminder": false
  },
  "meta": null
}
```

### `POST /unlock`: `{"passcode": "..."}`

```json
{
  "error": {
    "code": "PASSCODE_INCORRECT",
    "message": "That passcode did not match.",
    "field_errors": [
      {
        "field": "passcode",
        "message": "Incorrect passcode."
      }
    ]
  }
}
```
```json
{
  "data": {
    "token": "<redacted-unlock-token>",
    "expires_in": 1800
  },
  "meta": null
}
```

Five wrong tries in a row lock unlocking for 15 minutes (the lock doubles for every further five wrong tries, up to 24 hours; a correct passcode resets it): **429 `PASSCODE_LOCKED_OUT`** ("Too many wrong tries. Try again in a few
minutes."), and `GET /settings` then shows `locked_until` (ISO time) so the UI can show a countdown. *Not captured live (unit-tested):
the journey does not burn five attempts.* `POST /unlock` with no passcode set is 409 `PASSCODE_NOT_SET` (unit-tested).

### `POST /lock`: the "Lock journal" button

Invalidates every outstanding token; also clear the token in memory and show the lock screen.

```json
{
  "data": {
    "locked": true
  },
  "meta": null
}
```

### `DELETE /passcode`: forgot it, or turn the lock off

Body `{"password": "<the ACCOUNT password>"}`. Entries are untouched.

```json
{
  "error": {
    "code": "PASSWORD_INCORRECT",
    "message": "That password did not match.",
    "field_errors": [
      {
        "field": "password",
        "message": "Incorrect password."
      }
    ]
  }
}
```
```json
{
  "data": {
    "passcode_enabled": false,
    "locked_until": null,
    "daily_prompt_notifications": true,
    "evening_reminder": false
  },
  "meta": null
}
```

## 7. Retros (API only; no screen is designed yet)

A milestone that completed in the last 30 days is **offered** until the founder runs or dismisses a retro. `GET /retros/offers`:

```json
{
  "data": {
    "offers": [
      {
        "milestone_id": "b0699117-eb96-4ec6-b228-4975351469e6",
        "milestone_title": "Validate demand",
        "completed_at": "2026-10-29T20:07:48.171299Z",
        "prompt": "Run a 5-minute retro on Validate demand?"
      }
    ]
  },
  "meta": null
}
```

`POST /retros` (201): `{"milestone_id", "what_worked", "what_didnt", "what_change"}` (each up to 4,000 characters, at least one
non-blank). The text is encrypted at rest like entries.

```json
{
  "data": {
    "id": "df969b45-fefe-4b4c-b654-1ace731f6023",
    "milestone_id": "b0699117-eb96-4ec6-b228-4975351469e6",
    "milestone_title": "Validate demand",
    "created_at": "2026-10-29T20:07:49.792315Z",
    "updated_at": "2026-10-29T20:07:49.792315Z",
    "what_worked": "Talking to users first",
    "what_didnt": "Pricing guesses",
    "what_change": "Test price earlier"
  },
  "meta": null
}
```

Errors: a second retro for the same milestone is **409 `RETRO_EXISTS`**; a milestone that is not done is **409 `MILESTONE_NOT_DONE`**;
a milestone from another workspace is 404.

```json
{
  "error": {
    "code": "RETRO_EXISTS",
    "message": "You already ran a retro for this milestone.",
    "field_errors": []
  }
}
```
```json
{
  "error": {
    "code": "MILESTONE_NOT_DONE",
    "message": "A retro can be run once the milestone is done.",
    "field_errors": []
  }
}
```

`GET /retros` (list, newest first), `GET /retros/{id}`, `PUT /retros/{id}` (full replace of the three answers), `DELETE /retros/{id}`,
and `POST /retros/offers/{milestone_id}/dismiss` (`{"dismissed": true}`; the offer never returns, a retro can still be run later):

```json
{
  "data": {
    "retros": [
      {
        "id": "df969b45-fefe-4b4c-b654-1ace731f6023",
        "milestone_id": "b0699117-eb96-4ec6-b228-4975351469e6",
        "milestone_title": "Validate demand",
        "created_at": "2026-10-29T20:07:49.792315Z",
        "updated_at": "2026-10-29T20:07:49.792315Z",
        "what_worked": "Talking to users first",
        "what_didnt": "Pricing guesses",
        "what_change": "Test price earlier"
      }
    ]
  },
  "meta": null
}
```
```json
{
  "data": {
    "id": "df969b45-fefe-4b4c-b654-1ace731f6023",
    "milestone_id": "b0699117-eb96-4ec6-b228-4975351469e6",
    "milestone_title": "Validate demand",
    "created_at": "2026-10-29T20:07:49.792315Z",
    "updated_at": "2026-10-29T20:07:49.848690Z",
    "what_worked": "a",
    "what_didnt": "b",
    "what_change": "c"
  },
  "meta": null
}
```

## 8. Export and permanent delete

`GET /export` downloads **a JSON file** (not the usual envelope headers: `Content-Disposition: attachment; filename="journal-export.json"`,
`Cache-Control: no-store`). Fetch it with the bearer header, then save the blob; do not navigate to the URL. The body is still wrapped as
`{"data": ..., "meta": null}`. It is passcode-gated like everything else and contains the decrypted text, so the UI must say so before
the download ("Download all entries", not "encrypted export").

```json
{
  "date": "Thu, 29 Oct 2026 20:07:48 GMT",
  "server": "uvicorn",
  "content-disposition": "attachment; filename=\"journal-export.json\"",
  "cache-control": "no-store",
  "content-length": "1410",
  "content-type": "application/json"
}
```

Body (entries trimmed here to the first one):

```json
{
  "data": {
    "exported_at": "2026-10-29T20:07:49.877291+00:00",
    "entries": [
      {
        "date": "2026-10-24",
        "mood": "rough",
        "stress": 9,
        "content": "Day 1 first line\nSecond line about pricing and runway."
      },
      "... 5 more entries, same shape ..."
    ],
    "retros": [
      {
        "milestone_title": "Validate demand",
        "created_at": "2026-10-29T20:07:49.792315+00:00",
        "updated_at": "2026-10-29T20:07:49.848690+00:00",
        "what_worked": "a",
        "what_didnt": "b",
        "what_change": "c"
      }
    ]
  },
  "meta": null
}
```

`DELETE /journal` (the bare base path, with a JSON body) destroys the journal: entries, mood logs, retros, prompts, settings and the
passcode. The body must be exactly `{"confirm": "DELETE"}` (any other value is 422); the modal's "Type DELETE" box mirrors it. The count
for the modal ("All N entries will be destroyed") comes from `stats.total_entries`.

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "confirm",
        "message": "Input should be 'DELETE'"
      }
    ]
  }
}
```
```json
{
  "data": {
    "deleted": true,
    "entries": 6,
    "mood_logs": 6,
    "retros": 1,
    "prompts": 1
  },
  "meta": null
}
```
```json
{
  "data": {
    "entries": [],
    "total": 0
  },
  "meta": null
}
```

## 9. The bell: reminders

With `evening_reminder` on and no entry written, the notification appears in `GET /notifications` (verified live by running the
scheduler in the journey process):

```json
{
  "data": {
    "notifications": [
      {
        "id": "3444a37b-a315-4ae2-98d4-34063a7ff7cd",
        "type": "journal.reminder",
        "title": "A quiet moment for your journal",
        "body": "You have not written today. Two minutes is enough.",
        "data": {
          "href": "/journal",
          "founder_id": "fa6983e9-a98f-478c-9e88-1ce0b8dfa709",
          "startup_id": "5c94ac56-9fed-4ed5-a3c8-6e0461b58695"
        },
        "read": false,
        "created_at": "2026-10-29T20:07:51.833189+00:00"
      },
      {
        "id": "39647311-8a77-4750-85be-3d490ec7a1bb",
        "type": "mission.ready",
        "title": "Your mission for today is ready",
        "body": "Draft 3 pricing options",
        "data": {
          "mission_id": "3d0dfe91-fe5a-4bf9-80d1-9da112cc0d73",
          "startup_id": "5c94ac56-9fed-4ed5-a3c8-6e0461b58695",
          "first_task_title": "Draft 3 pricing options"
        },
        "read": false,
        "created_at": "2026-10-29T20:07:51.579999+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

(The list also shows other types for the same workspace; `journal.*` rows are only ever delivered to the founder who opted in.)

## 10. Error codes the FE must handle

| Status | `error.code` | Where | UI |
|---|---|---|---|
| 403 | `FORBIDDEN` | any route, non-founder | "Journal is private to the founder" |
| 423 | `JOURNAL_LOCKED` | any content route | show the lock screen, drop the token |
| 403 | `PASSCODE_INCORRECT` | `/unlock`, change passcode | "That passcode did not match." under the field |
| 429 | `PASSCODE_LOCKED_OUT` | `/unlock` | disable the form, show countdown from `locked_until` |
| 409 | `PASSCODE_NOT_SET` | `/unlock` | refresh settings, skip the lock screen |
| 403 | `PASSWORD_INCORRECT` | `DELETE /passcode` | "That password did not match." |
| 422 | `VALIDATION_ERROR` | any body or query | field errors from `field_errors[].field` |
| 404 | `NOT_FOUND` | entry, retro, by-date | by-date = empty editor; others = "not found" |
| 409 | `RETRO_EXISTS`, `MILESTONE_NOT_DONE` | retros | inline message |
| 500 | `JOURNAL_NOT_CONFIGURED` | writes and reads, when the server's journal key is missing | "Journal storage is temporarily unavailable." No retry fixes it (staging has this today, see handoff 0c) |

## 11. Verification table

| Behaviour | Status |
|---|---|
| Entries create / replace / read / by-date / list fields / month / months / search / delete | verified live (captured) |
| Stats, mood range + annotations, insights (streak rule), supportive-card dismissal | verified live |
| Supportive card **appearing**, other insight rules (weekday, deadline, mood shift) | unit-tested only: needs many days of data the journey cannot create |
| Prompt today, dismiss, library | verified live |
| Prompt AI upgrade | verified live with the stub model; real-model text not seen by this journey |
| Settings, passcode set / locked 423 / unlock wrong + ok / lock / remove | verified live |
| 5-try lockout (429 `PASSCODE_LOCKED_OUT`), token expiry after 30 minutes | unit-tested only |
| Retros offers / create / duplicate / not-done / list / update | verified live; delete and dismiss are unit-tested |
| Export, delete-all | verified live |
| Evening reminder via the scheduler and the bell | verified live (scheduler tick run inside the journey process, worker drained in-process) |
| Daily-prompt notification at 09:00 | unit-tested only (same code path as the reminder) |
| Role matrix (403 for every other role on every route) | unit-tested (`tests/api/test_journal_access.py`, `tests/api/test_journal_gaps.py`) |
