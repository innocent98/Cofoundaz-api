# FE integration guide: Sales coach, call prep and analytics (slice C)

Module 11 (Sales Hub), slice C: the **role-play coach** (practice a call with an AI buyer, then get a
scorecard), **call prep** for a deal, and the **Analytics** page. Slice A (CRM core) is in
`docs/fe-integration-guide-sales-crm.md`; sequences (slice B) are not in this guide.

Every body below is pasted by script from a live run of `e2e/test_sales_c.py` (captures in
`e2e/_captures/sales_c/`, each named above its body). Ids and timestamps are throwaway test data.
Where a body was trimmed it says so; where something was not exercised live it says so inline and in
the table at the end.

> **The LLM in those captures is the deterministic STUB.** Buyer replies read `[stub-llm] ...`,
> every scorecard score is `0`, feedback is `[stub-llm] feedback`, and call prep text is
> `[stub-llm] <field>`. The *shapes, statuses and error codes* are real; the *wording and scores* are
> not. Do not design empty states or copy lengths from the stub text. Real-model quality is checked
> in the FE walk.

## 0. Ground rules

- Base path `/api/v1/sales`. Every route needs `Authorization: Bearer ...` and `X-Workspace-Id`.
- **Founders and team members only**, like the rest of Sales. Mentor, consultant and every other role
  get `403 FORBIDDEN` on every route in this guide (section 5). Sales is already hidden for them.
- Envelopes: success `{"data": ..., "meta": null}`; error `{"error": {"code", "message", "field_errors"}}`.
  Business-rule 422s have a top-level `message` equal to the one `field_errors[].message`. Pydantic-level
  422s (wrong type, bad enum, bad date) have the generic top-level message
  `Please check the highlighted fields.` and the real text only in `field_errors[].message`. Always render `field_errors`.
- **Money is integer kobo** (`*_minor`), currency `"NGN"`. Divide by 100.
- **Everything the AI writes is asynchronous.** The POST returns at once (`202`, or `201` for session
  creation) with a row whose status is `generating`/`scoring`; a worker fills it in; you **poll the GET**.
  This is the same protocol as the AI co-founder chat.
- AI work is metered against the workspace's daily token budget. Over budget is **not** an HTTP error:
  the row ends `failed` (section 4.3).

## 1. Role-play coach

**What it is:** the user rehearses a sales call. The AI plays the buyer. Setup is a **deal** (the buyer is
built from the deal, its account, its contact and recent activities) **or** a free **scenario** text,
plus a **persona** and a **difficulty**. When the user ends the call, the AI scores *the seller*.

Practice sessions are **private to the member who started them**: a teammate gets `404` for your session id
and does not see it in the list.

### 1.1 Setup fields (`POST /sales/coach/sessions`)

| Field | Type | Rules |
|---|---|---|
| `deal_id` | uuid or omitted | A deal in this workspace. |
| `scenario` | string or omitted | Max 2000 chars. Used when there is no deal (it can also accompany one). |
| `persona` | string | Required, 1 to 200 chars after trimming. Free text; offer presets in the UI. |
| `difficulty` | `friendly` \| `neutral` \| `tough` | Default `neutral`. |
| `message` | string or omitted | Optional opening line from the seller (max 4000). When sent, the buyer's reply is queued immediately. |

At least one of `deal_id` / `scenario` is required. Context is **snapshotted when the session starts**:
editing or deleting the deal afterwards does not change the buyer.

### 1.2 Create a session, `POST /sales/coach/sessions` -> `201`

Request used in the capture: `{"deal_id": "<deal>", "persona": "Skeptical CFO", "difficulty": "tough", "message": "Hi Tunde, thanks for making time."}`

`session_created_from_deal.json`
```json
{
  "data": {
    "id": "c023d44e-4c1f-4dfa-b72b-5e1c662fd4b5",
    "mode": "roleplay",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "deal_name": "Acme fleet payments",
    "scenario": null,
    "persona": "Skeptical CFO",
    "difficulty": "tough",
    "status": "active",
    "scorecard": null,
    "scorecard_error": null,
    "created_at": "2026-10-21T12:30:33.243880+00:00",
    "ended_at": null,
    "messages": [
      {
        "id": "97bf724e-a7b8-4380-8f2f-db225f0831ec",
        "role": "user",
        "status": "ready",
        "content": "Hi Tunde, thanks for making time.",
        "created_at": "2026-10-21T12:30:33.257655+00:00"
      },
      {
        "id": "5f5a0ad4-a472-4477-b2de-705a0171d21e",
        "role": "assistant",
        "status": "generating",
        "content": "",
        "created_at": "2026-10-21T12:30:33.258655+00:00"
      }
    ]
  },
  "meta": null
}
```

Scenario-only setup (no deal), `deal_id` and `deal_name` are `null`, difficulty defaulted, no `message` so `messages` is empty:

`session_created_from_scenario.json`
```json
{
  "data": {
    "id": "335f5ba2-1460-4e86-a1c9-1ecec66b7c64",
    "mode": "roleplay",
    "deal_id": null,
    "deal_name": null,
    "scenario": "Cold call to a bakery chain about card readers",
    "persona": "Busy owner",
    "difficulty": "neutral",
    "status": "active",
    "scorecard": null,
    "scorecard_error": null,
    "created_at": "2026-10-21T12:30:34.164924+00:00",
    "ended_at": null,
    "messages": []
  },
  "meta": null
}
```

**Field-nesting traps**
- `scorecard` is `null` until scoring finishes; `scorecard_error` is `null` unless scoring failed.
- `deal_name` is a convenience copy of the deal's name at session start (`null` for scenario sessions).
- `messages` is present on create, get and end; **absent** on the list endpoint.

### 1.3 Send a turn, `POST /sales/coach/sessions/{id}/messages` -> `202`

Body `{"content": "..."}` (1 to 4000 chars). The response is the **buyer's pending turn**, not the seller's:

`message_posted_202.json`
```json
{
  "data": {
    "id": "e8df4e6d-593f-4926-9223-f86fa6852a91",
    "role": "assistant",
    "status": "generating",
    "content": "",
    "created_at": "2026-10-21T12:30:34.003413+00:00"
  },
  "meta": null
}
```

Poll `GET /sales/coach/sessions/{id}` every ~1.5 s (give up and show a retry after ~60 s). While the buyer turn is
`generating` it has `content: ""`:

`session_polled_generating.json`
```json
{
  "data": {
    "id": "c023d44e-4c1f-4dfa-b72b-5e1c662fd4b5",
    "mode": "roleplay",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "deal_name": "Acme fleet payments",
    "scenario": null,
    "persona": "Skeptical CFO",
    "difficulty": "tough",
    "status": "active",
    "scorecard": null,
    "scorecard_error": null,
    "created_at": "2026-10-21T12:30:33.243880+00:00",
    "ended_at": null,
    "messages": [
      {
        "id": "97bf724e-a7b8-4380-8f2f-db225f0831ec",
        "role": "user",
        "status": "ready",
        "content": "Hi Tunde, thanks for making time.",
        "created_at": "2026-10-21T12:30:33.257655+00:00"
      },
      {
        "id": "5f5a0ad4-a472-4477-b2de-705a0171d21e",
        "role": "assistant",
        "status": "generating",
        "content": "",
        "created_at": "2026-10-21T12:30:33.258655+00:00"
      }
    ]
  },
  "meta": null
}
```

When it finishes, the same message id is `ready` with text:

`session_polled_ready.json`
```json
{
  "data": {
    "id": "c023d44e-4c1f-4dfa-b72b-5e1c662fd4b5",
    "mode": "roleplay",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "deal_name": "Acme fleet payments",
    "scenario": null,
    "persona": "Skeptical CFO",
    "difficulty": "tough",
    "status": "active",
    "scorecard": null,
    "scorecard_error": null,
    "created_at": "2026-10-21T12:30:33.243880+00:00",
    "ended_at": null,
    "messages": [
      {
        "id": "97bf724e-a7b8-4380-8f2f-db225f0831ec",
        "role": "user",
        "status": "ready",
        "content": "Hi Tunde, thanks for making time.",
        "created_at": "2026-10-21T12:30:33.257655+00:00"
      },
      {
        "id": "5f5a0ad4-a472-4477-b2de-705a0171d21e",
        "role": "assistant",
        "status": "ready",
        "content": "[stub-llm] AI-generated assessment narrative.",
        "created_at": "2026-10-21T12:30:33.258655+00:00"
      }
    ]
  },
  "meta": null
}
```

**Message states:** `generating` (show a typing indicator, disable the input), `ready` (show), `failed`
(section 4.3: show "The buyer couldn't reply. Try sending again." and re-enable the input).

**One reply at a time.** While a buyer turn is `generating`, another message **or ending the session** is `409`:

`message_error_reply_in_progress.json`
```json
{
  "error": {
    "code": "REPLY_IN_PROGRESS",
    "message": "The buyer is still replying. Please wait.",
    "field_errors": []
  }
}
```

`end_error_reply_in_progress.json`
```json
{
  "error": {
    "code": "REPLY_IN_PROGRESS",
    "message": "The buyer is still replying. Please wait.",
    "field_errors": []
  }
}
```

### 1.4 End the call, `POST /sales/coach/sessions/{id}/end` -> `202`

No body. The session goes to `status: "scoring"` and a scorecard job runs. Poll the GET until
`status` is `ended`.

`end_202_scoring.json`
```json
{
  "data": {
    "id": "c023d44e-4c1f-4dfa-b72b-5e1c662fd4b5",
    "mode": "roleplay",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "deal_name": "Acme fleet payments",
    "scenario": null,
    "persona": "Skeptical CFO",
    "difficulty": "tough",
    "status": "scoring",
    "scorecard": null,
    "scorecard_error": null,
    "created_at": "2026-10-21T12:30:33.243880+00:00",
    "ended_at": "2026-10-21T12:30:34.054821+00:00",
    "messages": [
      {
        "id": "97bf724e-a7b8-4380-8f2f-db225f0831ec",
        "role": "user",
        "status": "ready",
        "content": "Hi Tunde, thanks for making time.",
        "created_at": "2026-10-21T12:30:33.257655+00:00"
      },
      {
        "id": "5f5a0ad4-a472-4477-b2de-705a0171d21e",
        "role": "assistant",
        "status": "ready",
        "content": "[stub-llm] AI-generated assessment narrative.",
        "created_at": "2026-10-21T12:30:33.258655+00:00"
      },
      {
        "id": "e292cff4-a1ba-4261-8786-b198dc4ecb79",
        "role": "user",
        "status": "ready",
        "content": "What does switching cost you today?",
        "created_at": "2026-10-21T12:30:34.002413+00:00"
      },
      {
        "id": "e8df4e6d-593f-4926-9223-f86fa6852a91",
        "role": "assistant",
        "status": "ready",
        "content": "[stub-llm] AI-generated assessment narrative.",
        "created_at": "2026-10-21T12:30:34.003413+00:00"
      }
    ]
  },
  "meta": null
}
```

Ending again while scoring is a `409`:

`end_error_scoring_in_progress.json`
```json
{
  "error": {
    "code": "SCORING_IN_PROGRESS",
    "message": "The scorecard is already being written.",
    "field_errors": []
  }
}
```

When finished (`status: "ended"`, `scorecard` filled). Messages are trimmed to keep this readable:

`session_scored.json`  (trimmed: `messages` omitted, same shape as `session_created_from_deal`)
```json
{
  "data": {
    "id": "c023d44e-4c1f-4dfa-b72b-5e1c662fd4b5",
    "mode": "roleplay",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "deal_name": "Acme fleet payments",
    "scenario": null,
    "persona": "Skeptical CFO",
    "difficulty": "tough",
    "status": "ended",
    "scorecard": {
      "scores": {
        "discovery": 0,
        "next_step_close": 0,
        "objection_handling": 0
      },
      "overall": 0.0,
      "feedback": [
        "[stub-llm] feedback"
      ]
    },
    "scorecard_error": null,
    "created_at": "2026-10-21T12:30:33.243880+00:00",
    "ended_at": "2026-10-21T12:30:34.054821+00:00"
  },
  "meta": null
}
```

**Scorecard shape:** `scores.discovery`, `scores.objection_handling`, `scores.next_step_close` are whole
numbers 0 to 10 (the UI shows "/10"); `overall` is their mean to one decimal; `feedback` is 3 to 6
specific lines on the real model (1 line in the stub), up to 8 max. Key order inside `scores` is not meaningful.

Ending an **already scored** session is harmless and returns the same scorecard (still `202`):

`end_again_after_scored.json`  (trimmed: `messages` omitted, same shape as `session_created_from_deal`)
```json
{
  "data": {
    "id": "c023d44e-4c1f-4dfa-b72b-5e1c662fd4b5",
    "mode": "roleplay",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "deal_name": "Acme fleet payments",
    "scenario": null,
    "persona": "Skeptical CFO",
    "difficulty": "tough",
    "status": "ended",
    "scorecard": {
      "scores": {
        "discovery": 0,
        "next_step_close": 0,
        "objection_handling": 0
      },
      "overall": 0.0,
      "feedback": [
        "[stub-llm] feedback"
      ]
    },
    "scorecard_error": null,
    "created_at": "2026-10-21T12:30:33.243880+00:00",
    "ended_at": "2026-10-21T12:30:34.054821+00:00"
  },
  "meta": null
}
```

Ending a session in which the seller never spoke is a `422`:

`end_error_nothing_to_score.json`
```json
{
  "error": {
    "code": "NOTHING_TO_SCORE",
    "message": "Say something to the buyer before ending the session.",
    "field_errors": []
  }
}
```

Messaging an ended session is a `409`:

`message_error_session_ended.json`
```json
{
  "error": {
    "code": "SESSION_ENDED",
    "message": "This practice session has ended.",
    "field_errors": []
  }
}
```

**Session states**

| `status` | Meaning | FE |
|---|---|---|
| `active` | Call in progress | Chat input enabled unless a buyer turn is `generating`. "End session" enabled once the seller has sent a turn. |
| `scoring` | Scorecard being written | Show "Scoring your call...", poll. Chat disabled. |
| `ended` + `scorecard` | Done | Show the scorecard. |
| `ended` + `scorecard: null` + `scorecard_error` | Scoring failed (section 4.3) | Show "We couldn't score this call." with a **Try again** button that calls `end` again. |

### 1.5 "Practice this again"

There is no special endpoint. It is a **new session with the same setup**: take `deal_id`, `scenario`, `persona`
and `difficulty` from the session you are looking at and `POST /sales/coach/sessions` again.

### 1.6 History, `GET /sales/coach/sessions?limit=&offset=`

The caller's own sessions, newest first. `limit` 1 to 100 (default 20), `offset` >= 0. **A plain array, no
pagination envelope**, and no `messages` key. Trimmed to the first item here; the second item in the capture is the earlier ended session:

`sessions_list.json`  (trimmed: first item only)
```json
{
  "data": [
    {
      "id": "335f5ba2-1460-4e86-a1c9-1ecec66b7c64",
      "mode": "roleplay",
      "deal_id": null,
      "deal_name": null,
      "scenario": "Cold call to a bakery chain about card readers",
      "persona": "Busy owner",
      "difficulty": "neutral",
      "status": "active",
      "scorecard": null,
      "scorecard_error": null,
      "created_at": "2026-10-21T12:30:34.164924+00:00",
      "ended_at": null
    }
  ],
  "meta": null
}
```

### 1.7 Setup errors

`setup_error_no_deal_or_scenario.json`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Pick a deal or describe a scenario.",
    "field_errors": [
      {
        "field": "deal_id",
        "message": "Pick a deal or describe a scenario."
      }
    ]
  }
}
```

`setup_error_bad_difficulty.json`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "difficulty",
        "message": "Input should be 'friendly', 'neutral' or 'tough'"
      }
    ]
  }
}
```

A deal id from **another workspace** (or one that does not exist) is a 422 on `deal_id`, never a 404 and never a leak:

`setup_error_foreign_deal.json`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Deal not found.",
    "field_errors": [
      {
        "field": "deal_id",
        "message": "Deal not found."
      }
    ]
  }
}
```

## 2. Call prep

**What it is:** one click on a deal produces a prep sheet: where the deal stands, the likely objections
with a suggested response each, the goal of the call, and three discovery questions. It is built from the
**live** deal (not a snapshot).

### 2.1 Read the latest, `GET /sales/deals/{id}/call-prep` -> `200`

A deal that never had one returns `"data": null` (not a 404, it is a normal empty state):

`call_prep_none_yet.json`
```json
{
  "data": null,
  "meta": null
}
```

Once any exists you get the **latest** one (any status). An unknown or foreign deal id is `404`.

### 2.2 Generate, `POST /sales/deals/{id}/call-prep` -> `202`

No body. Poll the GET until `status` is not `generating`.

`call_prep_requested_202.json`
```json
{
  "data": {
    "id": "3bf2ffd3-12f0-4629-bd32-3f023c6604b6",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "status": "generating",
    "output": null,
    "error": null,
    "created_at": "2026-10-21T12:30:34.243166+00:00"
  },
  "meta": null
}
```

**One in flight per deal.** A second POST while one is generating is a `409`:

`call_prep_error_in_progress.json`
```json
{
  "error": {
    "code": "CALL_PREP_IN_PROGRESS",
    "message": "A call prep is already being written for this deal.",
    "field_errors": []
  }
}
```

When ready:

`call_prep_ready.json`
```json
{
  "data": {
    "id": "3bf2ffd3-12f0-4629-bd32-3f023c6604b6",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "status": "ready",
    "output": {
      "context": "[stub-llm] context",
      "call_goal": "[stub-llm] call_goal",
      "objections": [
        {
          "response": "[stub-llm] response",
          "objection": "[stub-llm] objection"
        }
      ],
      "discovery_questions": [
        "[stub-llm] discovery_questions"
      ]
    },
    "error": null,
    "created_at": "2026-10-21T12:30:34.243166+00:00"
  },
  "meta": null
}
```

**Output shape (real model):** `context` string; `objections` 3 to 5 items of `{objection, response}`;
`call_goal` string; `discovery_questions` exactly 3 strings (the API keeps at most 3). The stub returns one of each.

**Regenerate** is just another POST once the last one is not `generating`; it creates a new row and the GET
returns it. The old one is kept server side but is not exposed.

`call_prep_regenerated_202.json`
```json
{
  "data": {
    "id": "6292e4c1-64bd-4f2d-a7dc-2666c98617d0",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "status": "generating",
    "output": null,
    "error": null,
    "created_at": "2026-10-21T12:30:34.314681+00:00"
  },
  "meta": null
}
```

`status` values: `generating` (spinner), `ready` (show `output`), `failed` (section 4.3, with a **Try again** that POSTs again).

## 3. Analytics, `GET /sales/analytics?from=YYYY-MM-DD&to=YYYY-MM-DD`

Both params are optional **inclusive UTC calendar days**. Default: the last 30 days ending today. At most 366
days. `from` after `to`, or a longer range, is a 422 (below). Everything is computed server side from the CRM;
no AI is involved and it is synchronous.

Default range (the capture's workspace has 3 deals created in range, 1 won in range, 1 lost, and one older
won deal outside the range that only appears in `monthly_revenue`):

`analytics_default_range.json`
```json
{
  "data": {
    "from": "2026-09-22",
    "to": "2026-10-21",
    "currency": "NGN",
    "funnel": {
      "deals_created": 3,
      "stages": [
        {
          "stage_id": "1fd941b2-85ed-4b51-b5de-ac8c40ea2463",
          "name": "New",
          "kind": "open",
          "reached": 3,
          "conversion_from_previous_pct": null
        },
        {
          "stage_id": "a809a14b-d7ef-4211-bf28-9a422032318d",
          "name": "Contacted",
          "kind": "open",
          "reached": 3,
          "conversion_from_previous_pct": 100.0
        },
        {
          "stage_id": "2acaf14b-5d70-4c54-9747-ce7f27cc02c3",
          "name": "Qualified",
          "kind": "open",
          "reached": 3,
          "conversion_from_previous_pct": 100.0
        },
        {
          "stage_id": "6433582d-6519-4a7e-89d7-5c5e94b5f870",
          "name": "Proposal",
          "kind": "open",
          "reached": 3,
          "conversion_from_previous_pct": 100.0
        },
        {
          "stage_id": "c1cef1a9-aade-4e3b-b58c-1445ee7c79b1",
          "name": "Negotiation",
          "kind": "open",
          "reached": 2,
          "conversion_from_previous_pct": 66.7
        },
        {
          "stage_id": "8050ffca-f3cb-445a-8cdd-b881eb79aec6",
          "name": "Closed Won",
          "kind": "won",
          "reached": 1,
          "conversion_from_previous_pct": 50.0
        }
      ]
    },
    "avg_cycle_days": 12.0,
    "won": {
      "count": 1,
      "value_minor": 40000000
    },
    "win_loss": {
      "won": 1,
      "lost": 1,
      "win_rate_pct": 50.0,
      "loss_reasons": [
        {
          "reason": "price",
          "count": 1,
          "value_minor": 9000000
        }
      ]
    },
    "leaderboard": [
      {
        "user_id": "05f1601a-cd1e-4f71-8d2b-b72f5909d697",
        "name": "Ada Founder",
        "total": 3,
        "calls": 2,
        "emails": 1,
        "meetings": 0,
        "notes": 0
      }
    ],
    "monthly_revenue": [
      {
        "month": "2025-11",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2025-12",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-01",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-02",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-03",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-04",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-05",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-06",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-07",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-08",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-09",
        "value_minor": 15000000,
        "deal_count": 1
      },
      {
        "month": "2026-10",
        "value_minor": 40000000,
        "deal_count": 1
      }
    ]
  },
  "meta": null
}
```

Widening the range to 90 days pulls the older won deal in (`won.count` 2):

`analytics_wide_range.json`
```json
{
  "data": {
    "from": "2026-07-23",
    "to": "2026-10-21",
    "currency": "NGN",
    "funnel": {
      "deals_created": 4,
      "stages": [
        {
          "stage_id": "1fd941b2-85ed-4b51-b5de-ac8c40ea2463",
          "name": "New",
          "kind": "open",
          "reached": 4,
          "conversion_from_previous_pct": null
        },
        {
          "stage_id": "a809a14b-d7ef-4211-bf28-9a422032318d",
          "name": "Contacted",
          "kind": "open",
          "reached": 4,
          "conversion_from_previous_pct": 100.0
        },
        {
          "stage_id": "2acaf14b-5d70-4c54-9747-ce7f27cc02c3",
          "name": "Qualified",
          "kind": "open",
          "reached": 4,
          "conversion_from_previous_pct": 100.0
        },
        {
          "stage_id": "6433582d-6519-4a7e-89d7-5c5e94b5f870",
          "name": "Proposal",
          "kind": "open",
          "reached": 4,
          "conversion_from_previous_pct": 100.0
        },
        {
          "stage_id": "c1cef1a9-aade-4e3b-b58c-1445ee7c79b1",
          "name": "Negotiation",
          "kind": "open",
          "reached": 3,
          "conversion_from_previous_pct": 75.0
        },
        {
          "stage_id": "8050ffca-f3cb-445a-8cdd-b881eb79aec6",
          "name": "Closed Won",
          "kind": "won",
          "reached": 2,
          "conversion_from_previous_pct": 66.7
        }
      ]
    },
    "avg_cycle_days": 18.5,
    "won": {
      "count": 2,
      "value_minor": 55000000
    },
    "win_loss": {
      "won": 2,
      "lost": 1,
      "win_rate_pct": 66.7,
      "loss_reasons": [
        {
          "reason": "price",
          "count": 1,
          "value_minor": 9000000
        }
      ]
    },
    "leaderboard": [
      {
        "user_id": "05f1601a-cd1e-4f71-8d2b-b72f5909d697",
        "name": "Ada Founder",
        "total": 3,
        "calls": 2,
        "emails": 1,
        "meetings": 0,
        "notes": 0
      }
    ],
    "monthly_revenue": [
      {
        "month": "2025-11",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2025-12",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-01",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-02",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-03",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-04",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-05",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-06",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-07",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-08",
        "value_minor": 0,
        "deal_count": 0
      },
      {
        "month": "2026-09",
        "value_minor": 15000000,
        "deal_count": 1
      },
      {
        "month": "2026-10",
        "value_minor": 40000000,
        "deal_count": 1
      }
    ]
  },
  "meta": null
}
```

### 3.1 What each block means (read before charting)

| Block | Definition | Trap |
|---|---|---|
| `funnel.deals_created` | Deals **created** in the range (the cohort). | Deals created before `from` are never in the funnel, even if won in range. |
| `funnel.stages[]` | One row per live stage **except the lost stage**, in board order. `reached` = cohort deals that reached this stage **or any later one** (by stage position, from the stage history, including the creation row). | A deal created straight into Proposal counts as having reached New, Contacted and Qualified. A lost deal counts up to the furthest non-lost stage it reached, so it never counts as having reached Closed Won. |
| `funnel.stages[].conversion_from_previous_pct` | `reached / previous row's reached`, one decimal. | `null` for the first row **and whenever the previous row is 0**. |
| `avg_cycle_days` | Mean days from created to closed for deals **won in range** (by close date, regardless of when created). | `null` when nothing was won in range. A float with one decimal. |
| `won` | Deals currently in the won stage that **closed in range**: count and kobo total. | A reopened deal (dragged back out of won) no longer counts. |
| `win_loss.loss_reasons[]` | Deals currently in the lost stage that **closed in range**, grouped by reason, biggest first (ties by reason name). Only reasons with deals appear. | Not all six reasons are always present. A lost deal with no stored reason is reported as `other`. |
| `win_loss.win_rate_pct` | won / (won + lost) in range, one decimal. | `null` when both are 0. This is the range win rate, not the all-time `win_rate` on `GET /sales`. |
| `leaderboard[]` | Every **current founder / team member** with their activity counts in range (calls, emails, meetings, notes) and `total`; sorted by `total` desc then name. | Members with no activity are listed with zeros. Mentors and other roles never appear. `name` falls back to the member's email when they have no profile name, so do not assume it is a person's name. |
| `monthly_revenue[]` | Exactly **12 rows, oldest first**, the current month last, zero filled. Closed-won kobo by the UTC month the deal closed. | **Ignores `from`/`to`.** Month is `YYYY-MM` (UTC). |

### 3.2 Analytics errors

`analytics_error_from_after_to.json`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The start date must be on or before the end date.",
    "field_errors": [
      {
        "field": "from",
        "message": "The start date must be on or before the end date."
      }
    ]
  }
}
```

`analytics_error_range_too_long.json`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Pick a range of at most 366 days.",
    "field_errors": [
      {
        "field": "to",
        "message": "Pick a range of at most 366 days."
      }
    ]
  }
}
```

`analytics_error_bad_date.json`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "query.from",
        "message": "Input should be a valid date or datetime, input is too short"
      }
    ]
  }
}
```

Note the last one: the generic message, and the field name is `query.from` (not `from`).

## 4. Errors and edge cases the FE must handle

### 4.1 Roles

Mentor (captured; the consultant and every other non-Sales role use the same gate, see the table):

`mentor_coach_session_403.json`
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

Same `403` body on `POST /sales/deals/{id}/call-prep` (`mentor_call_prep_403.json`) and `GET /sales/analytics`:

`mentor_analytics_403.json`
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

### 4.2 Not found

Unknown ids **and ids from another workspace** are both 404 with the same body. For sessions that also covers
**a teammate's session**:

`session_foreign_404.json`
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

`call_prep_foreign_deal_404.json`
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### 4.3 Over the daily AI budget (and model errors)

Nothing fails at request time: the POST still answers `201`/`202`. The worker then marks the row **failed**
and you see it when you poll. A model/provider error produces the same `failed` rows with a different
`error`/`scorecard_error` value (`generation_failed`), so treat every `failed` the same way.

Buyer turn (the session stays `active`; the seller can just send again):

`session_buyer_turn_failed_over_budget.json`
```json
{
  "data": {
    "id": "7ef97964-7df3-492a-8aa7-067b27b8b712",
    "mode": "roleplay",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "deal_name": "Acme fleet payments",
    "scenario": null,
    "persona": "Skeptical CFO",
    "difficulty": "tough",
    "status": "active",
    "scorecard": null,
    "scorecard_error": null,
    "created_at": "2026-10-21T12:30:36.242852+00:00",
    "ended_at": null,
    "messages": [
      {
        "id": "ae38b4a0-9884-4d34-a427-cd70e50ea3cd",
        "role": "user",
        "status": "ready",
        "content": "Hello again",
        "created_at": "2026-10-21T12:30:36.255366+00:00"
      },
      {
        "id": "301d5cca-5e99-467a-b03c-f6e026e32a4c",
        "role": "assistant",
        "status": "failed",
        "content": "",
        "created_at": "2026-10-21T12:30:36.256366+00:00"
      }
    ]
  },
  "meta": null
}
```

Call prep (`error` is `over_budget`, or `generation_failed` after a provider error, or `deal_missing` if the deal was deleted meanwhile):

`call_prep_failed_over_budget.json`
```json
{
  "data": {
    "id": "42949237-ba1c-4051-8240-c5ea5cc6be20",
    "deal_id": "75dacf34-13a8-4443-bad7-7d1156873ade",
    "status": "failed",
    "output": null,
    "error": "over_budget",
    "created_at": "2026-10-21T12:30:36.273048+00:00"
  },
  "meta": null
}
```

Scorecard (`status: "ended"`, `scorecard: null`, `scorecard_error: "over_budget"`; other values: `generation_failed`, `bad_output`):

`session_scorecard_failed_over_budget.json`  (trimmed: `messages` omitted, same shape as `session_created_from_deal`)
```json
{
  "data": {
    "id": "630d402a-0560-4f90-b1fe-cda3a39fbd56",
    "mode": "roleplay",
    "deal_id": null,
    "deal_name": null,
    "scenario": "x",
    "persona": "CFO",
    "difficulty": "neutral",
    "status": "ended",
    "scorecard": null,
    "scorecard_error": "over_budget",
    "created_at": "2026-10-21T12:30:36.382538+00:00",
    "ended_at": "2026-10-21T12:30:36.462929+00:00"
  },
  "meta": null
}
```

Retry by calling `end` again once the budget resets (daily, UTC midnight; `GET /api/v1/ai/status` has
`over_budget` and `resets_at`). It returns to `scoring`:

`end_retry_after_failed_scorecard.json`  (trimmed: `messages` omitted, same shape as `session_created_from_deal`)
```json
{
  "data": {
    "id": "630d402a-0560-4f90-b1fe-cda3a39fbd56",
    "mode": "roleplay",
    "deal_id": null,
    "deal_name": null,
    "scenario": "x",
    "persona": "CFO",
    "difficulty": "neutral",
    "status": "scoring",
    "scorecard": null,
    "scorecard_error": null,
    "created_at": "2026-10-21T12:30:36.382538+00:00",
    "ended_at": "2026-10-21T12:30:36.462929+00:00"
  },
  "meta": null
}
```

## 5. UX consequences the FE must surface

1. **Never show the stub as product copy.** Real buyer replies are 1 to 4 sentences; scores are real 0 to 10.
2. **The buyer is a pretend person.** Label the chat "Practice call" and the buyer "AI buyer" so nobody mistakes a
   persona's objections for real customer feedback.
3. **Disable the input while a buyer turn is `generating`** and disable "End session" too; both would 409.
4. **A `failed` buyer turn is not a dead session.** Say the buyer couldn't reply and let the seller resend. The
   failed turn stays in `messages` with empty `content`: render or hide it, but do not treat the session as broken.
5. **Scoring can take longer than a reply.** Show a "Scoring your call" state; do not navigate away on `202`.
6. **Scorecard `/10` scores are the AI's judgement of this one call**, not a rating of the person: keep the copy neutral.
7. **Context is frozen at session start.** If the user edits the deal and wants the buyer to reflect it, they start a
   new session ("Practice this again").
8. **Call prep reflects the deal as it is now**, and regenerating replaces what the drawer shows. If the user
   edited the deal after the last prep, offer "Regenerate".
9. **Over budget** is a user-visible state, not an error toast: say the daily AI limit is used up and when it resets.
10. **`monthly_revenue` ignores the date range.** Label that chart "Last 12 months" so it is not read as the range.
11. **Funnel is a cohort of deals created in the range**, not "activity in the range". Label it "Deals created in
    this period" so a short range with few new deals does not look like a broken funnel.
12. No em or en dashes are produced by the API for AI text; do not add any in your own copy.

## 6. Verification table

| Behaviour | Status |
|---|---|
| Create session from a deal; from a scenario; opening `message` queues a buyer turn | verified live |
| Send turn `202`, poll `generating` -> `ready`, 409 while replying (message and end) | verified live |
| End -> `scoring` -> scorecard; end while scoring 409; end again after scored; end with nothing said 422; message after end 409 | verified live |
| Session list (own sessions, no `messages`) | verified live |
| Setup 422s (no deal/scenario, bad difficulty, foreign deal id) | verified live |
| Teammate cannot see your session (404) | unit/API test only (live capture is a cross-workspace 404, same body) |
| Call prep: none yet, request, 409 in flight, ready, regenerate | verified live |
| Foreign deal id on call prep (404 POST) | verified live (GET 404 is unit/API tested) |
| Analytics default range, wide range, 3 errors, all 5 blocks | verified live |
| Analytics edge cases (range edges, reopened deal, month boundaries, other workspaces) | unit tests only |
| Mentor 403 on session create, call prep, analytics | verified live |
| Consultant 403 (and the other non-Sales roles) | unit/API test only (same role gate) |
| Over budget: buyer turn failed, call prep failed, scorecard failed + retry | verified live (budget exhausted in the test process; the stub never raises) |
| Model/provider error mid call (`generation_failed`, `bad_output`) | unit tests only |
| Real model output quality: buyer realism, scorecard calibration, objection quality, dash stripping on real text | **not verified**: stub only; check in the FE walk with a real model |
| Real-model latency (how long polling takes) | **not verified** |
