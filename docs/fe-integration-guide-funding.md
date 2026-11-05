# FE Integration Guide: Funding Hub (Module 14, slice A: round and investor pipeline)

Every request and response body below is pasted **verbatim** from live captures taken by
`e2e/test_funding_pipeline.py` against a real server (`e2e/_captures/funding_pipeline/*.json`, the
capture name is given above each block). Nothing is retyped from the schema. IDs, timestamps and
the throwaway names (`Tolu Bello`, `Lagos Angels`, ...) are placeholders from the test run. **What is
not covered live is listed in the verification table at the end and marked inline.**

Scope: the **Pipeline** page (`/funding`) only: header bar, the seven-column board, add/edit card,
card drawer with the interaction log, and the "record the money in Finance" prompt. Data room, Cap
table, Term sheets, Grants and Analytics have no API yet (slices B to F): keep them on the mocks.
Hidden from the sidebar until all slices are built and re-walked. Design:
`cofoundaz/docs/superpowers/specs/2026-11-05-funding-hub-design.md`; calls D-FUND-1..8 accepted
2026-11-05. **The investor directory is not in this slice**: ship the empty state "Add your first
investor" and no "Find investors" control.

Base path `/api/v1/funding`. Every route needs `Authorization: Bearer <token>` and
`X-Workspace-Id: <workspace uuid>` (from `GET /auth/me` → `data.active_workspace_id`). Success bodies
are `{"data": ..., "meta": null}`; errors are `{"error": {"code", "message", "field_errors"}}` (no
`data` key).

## 1. What the page is

A founder raising money tracks one **round** (the raise: target, currency) and the **investors** they
talk to, each on one of seven fixed stages: `research`, `outreach`, `meeting`, `diligence`,
`term_sheet`, `committed`, `passed`. Stage names are fixed (no custom stages). Show them as
Research, Outreach, Meeting, Diligence, Term Sheet, Committed, Passed.

## 2. Who can call what

| Role | Every route in this guide |
|---|---|
| founder, team_member | allowed |
| mentor, business_consultant, accountant, legal_advisor, investor | **403** |

Plan: the module key is `funding` (Scale plan). Behind the `PLAN_ENFORCEMENT` flag, which is **off**
today, so every workspace is allowed. When it is on, a workspace without Scale gets **402** from every
route (role is checked first, so a refused role still sees 403). 402 body, from the existing gate
(*not captured here: the e2e server runs with enforcement off; shape taken from the Billing guide
and covered by `tests/api/test_funding_pipeline_api.py::test_plan_gate_is_dark_then_402_on_starter_and_open_on_scale`*):
`{"error": {"code": "PLAN_REQUIRED", "message": "Upgrade to Scale to use this.", "field_errors": [...]}}`.
Show the upgrade prompt on 402; never treat it as a generic failure.

A **mentor** inside the founder's workspace, refused on read and write (verified live; the other
refused roles share the guard and are unit tested):

capture `error_mentor_forbidden_read`
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
capture `error_mentor_forbidden_write`
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

403 for a user who is not in the workspace they name (verified live):

capture `error_other_workspace_header`
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

Not-found for another workspace's investor id is a **404**, not a 403 (verified live):

capture `error_other_workspace_investor`
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

## 3. Money (read this once)

All amounts are **integer minor units** (kobo for NGN, cents for USD): `9000000000` is ₦90,000,000.
Each round has **one currency** (`NGN` default, `USD`), and every amount on that round's investors is
in it. There is no conversion. Show the round's currency symbol everywhere on the page. Percentages
(`soft_pct`, `committed_pct`) are whole numbers and **are not capped at 100** (an oversubscribed raise
shows 120). They are `null` when the round has no target.

## 4. The round

One unfinished round (`planning` or `open`) per workspace; closed rounds stay as history.

### GET /round: before anything exists

capture `round_none`
```json
{
  "data": null,
  "meta": null
}
```
`data` is `null`, status 200. This is the empty state, not an error.

### POST /investors creates the round for you

The first investor added creates a `planning` round named "Funding round" in NGN with no target, so
"Add your first investor" works with no setup:

capture `round_auto_created` (GET /round straight after the first investor)
```json
{
  "data": {
    "id": "cce45c65-f17e-4530-b999-da4d670cb42c",
    "name": "Funding round",
    "status": "planning",
    "currency": "NGN",
    "target_minor": null,
    "pre_money_minor": null,
    "opened_at": null,
    "closed_at": null,
    "created_at": "2026-11-05T13:48:33.129466+00:00",
    "updated_at": "2026-11-05T13:48:33.129466+00:00"
  },
  "meta": null
}
```

### PUT /round: set the raise

Body fields are all optional: `name` (1 to 120), `status` (`planning|open|closed`), `currency`
(`NGN|USD`), `target_minor`, `pre_money_minor` (`>= 0`; send explicit `null` to clear). Unknown
fields are a 422. With no unfinished round, PUT **creates** one.

capture `round_set` (request: `{"name": "Seed", "status": "open", "target_minor": 9000000000}`)
```json
{
  "data": {
    "id": "cce45c65-f17e-4530-b999-da4d670cb42c",
    "name": "Seed",
    "status": "open",
    "currency": "NGN",
    "target_minor": 9000000000,
    "pre_money_minor": null,
    "opened_at": "2026-11-05T13:48:33.190929+00:00",
    "closed_at": null,
    "created_at": "2026-11-05T13:48:33.129466+00:00",
    "updated_at": "2026-11-05T13:48:33.180395+00:00"
  },
  "meta": null
}
```
Going to `open` stamps `opened_at`; going to `closed` stamps `closed_at`.

**Currency is locked once any amount exists on the round** (verified live):

capture `error_currency_locked`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This round already has amounts in NGN. Clear them before changing the currency.",
    "field_errors": [
      {
        "field": "currency",
        "message": "This round already has amounts in NGN. Clear them before changing the currency."
      }
    ]
  }
}
```
Offer "clear the amounts first", or let the founder pick the currency before adding investors.

### Closing a round

capture `round_closed` (request: `{"status": "closed"}`)
```json
{
  "data": {
    "id": "cce45c65-f17e-4530-b999-da4d670cb42c",
    "name": "Seed",
    "status": "closed",
    "currency": "NGN",
    "target_minor": 9000000000,
    "pre_money_minor": null,
    "opened_at": "2026-11-05T13:48:33.190929+00:00",
    "closed_at": "2026-11-05T13:48:35.527352+00:00",
    "created_at": "2026-11-05T13:48:33.129466+00:00",
    "updated_at": "2026-11-05T13:48:35.514099+00:00"
  },
  "meta": null
}
```
After closing: `GET /round` and `GET /investors` keep returning that closed round (history, read-only
in the UI). `POST /investors` is refused until the founder starts the next round with `PUT /round`:

capture `error_round_closed`
```json
{
  "error": {
    "code": "ROUND_CLOSED",
    "message": "Your last round is closed. Start a new round first.",
    "field_errors": []
  }
}
```
Show "Your last round is closed" with a **Start a new round** button (PUT /round with a name and
currency). Do not silently create one.

capture `round_usd_started` (request: `{"name": "Series A", "currency": "USD", "target_minor": 200000000}`)
```json
{
  "data": {
    "id": "e12c6f10-3b53-4127-9c27-14dbba6a25ee",
    "name": "Series A",
    "status": "planning",
    "currency": "USD",
    "target_minor": 200000000,
    "pre_money_minor": null,
    "opened_at": null,
    "closed_at": null,
    "created_at": "2026-11-05T13:48:35.581113+00:00",
    "updated_at": "2026-11-05T13:48:35.581113+00:00"
  },
  "meta": null
}
```

## 5. Overview (header bar and column counts)

`GET /overview`. Empty workspace (verified live):

capture `overview_empty`
```json
{
  "data": {
    "round": null,
    "empty": true,
    "stages": [
      {
        "stage": "research",
        "count": 0
      },
      {
        "stage": "outreach",
        "count": 0
      },
      {
        "stage": "meeting",
        "count": 0
      },
      {
        "stage": "diligence",
        "count": 0
      },
      {
        "stage": "term_sheet",
        "count": 0
      },
      {
        "stage": "committed",
        "count": 0
      },
      {
        "stage": "passed",
        "count": 0
      }
    ],
    "totals": null,
    "suggested_target": null,
    "counts": {
      "investors": 0,
      "warm": 0,
      "overdue_next_steps": 0
    }
  },
  "meta": null
}
```
`empty: true` → show "Add your first investor". `totals` is `null` until a round exists.

With data (a ₦90M target; one Term Sheet investor with ₦30M, one Committed with ₦20M, one Passed):

capture `overview_with_totals`
```json
{
  "data": {
    "round": {
      "id": "cce45c65-f17e-4530-b999-da4d670cb42c",
      "name": "Seed",
      "status": "open",
      "currency": "NGN",
      "target_minor": 9000000000,
      "pre_money_minor": null,
      "opened_at": "2026-11-05T13:48:33.190929+00:00",
      "closed_at": null,
      "created_at": "2026-11-05T13:48:33.129466+00:00",
      "updated_at": "2026-11-05T13:48:33.180395+00:00"
    },
    "empty": false,
    "stages": [
      {
        "stage": "research",
        "count": 0
      },
      {
        "stage": "outreach",
        "count": 1
      },
      {
        "stage": "meeting",
        "count": 0
      },
      {
        "stage": "diligence",
        "count": 0
      },
      {
        "stage": "term_sheet",
        "count": 1
      },
      {
        "stage": "committed",
        "count": 1
      },
      {
        "stage": "passed",
        "count": 1
      }
    ],
    "totals": {
      "currency": "NGN",
      "target_minor": 9000000000,
      "soft_minor": 3000000000,
      "committed_minor": 2000000000,
      "soft_pct": 33,
      "committed_pct": 22
    },
    "suggested_target": null,
    "counts": {
      "investors": 4,
      "warm": 2,
      "overdue_next_steps": 0
    }
  },
  "meta": null
}
```
- **Header bar:** two tones. `soft_minor` = investors at **Term Sheet** with an amount; `committed_minor`
  = investors at **Committed** with an amount. Passed investors and investors without an amount count
  for nothing. The old mock called everything "soft-committed"; do not.
- `stages` is always all seven, in board order, with counts.
- `suggested_target` is `null` here because this test workspace has no Finance burn. When Finance has
  a positive net burn and the round is in NGN it is `{"amount_minor", "currency": "NGN", "months": 18,
  "basis": "monthly_net_burn", "monthly_burn_minor"}`: show it as a **hint** next to the target input
  ("18 months of your net burn"), never apply it. For a USD round it is always `null`. *Not captured
  live (the test workspace has no burn): the non-null shape is asserted in
  `test_suggested_target_is_18_months_of_net_burn_for_ngn_only`.*
- `counts.overdue_next_steps`: open-stage investors whose `next_step_on` is in the past.

## 6. Investors (the board)

### GET /investors

Query: `stage`, `q` (name or firm, literal match), `warm` (`true|false`), `type`
(`angel|vc|syndicate|other`), `limit` (1 to 500, default 200), `offset`. Items are sorted in board order
(stage, then `position`), so group by `stage` client-side. Empty:

capture `investors_empty`
```json
{
  "data": {
    "round_id": null,
    "items": [],
    "total": 0,
    "limit": 200,
    "offset": 0
  },
  "meta": null
}
```
With data:

capture `investors_board`
```json
{
  "data": {
    "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
    "items": [
      {
        "id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
        "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
        "name": "Tolu Bello",
        "firm": "Lagos Angels",
        "type": "angel",
        "stage": "outreach",
        "warm": true,
        "check_min_minor": 500000000,
        "check_max_minor": 2000000000,
        "committed_minor": null,
        "next_step": "Send the deck",
        "next_step_on": "2026-11-08",
        "next_step_overdue": false,
        "last_touch_at": null,
        "thesis_notes": "Backs fintech founders in Lagos.",
        "source": "manual",
        "passed_reason": null,
        "stage_entered_at": "2026-11-05T13:48:33.388328+00:00",
        "days_in_stage": 0,
        "position": 0,
        "recorded_in_finance": false,
        "created_at": "2026-11-05T13:48:33.129466+00:00",
        "updated_at": "2026-11-05T13:48:33.371666+00:00"
      },
      {
        "id": "d61bd622-64c7-469e-abf2-8d99e6316a6a",
        "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
        "name": "Bola Adeyemi",
        "firm": "Kora Ventures",
        "type": "vc",
        "stage": "term_sheet",
        "warm": false,
        "check_min_minor": null,
        "check_max_minor": null,
        "committed_minor": 3000000000,
        "next_step": null,
        "next_step_on": null,
        "next_step_overdue": false,
        "last_touch_at": null,
        "thesis_notes": null,
        "source": "manual",
        "passed_reason": null,
        "stage_entered_at": "2026-11-05T13:48:33.477247+00:00",
        "days_in_stage": 0,
        "position": 0,
        "recorded_in_finance": false,
        "created_at": "2026-11-05T13:48:33.234531+00:00",
        "updated_at": "2026-11-05T13:48:33.461839+00:00"
      },
      {
        "id": "925e6ff2-61a1-4e3c-a68a-d1949ece7f22",
        "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
        "name": "Chidi Okafor",
        "firm": null,
        "type": "angel",
        "stage": "committed",
        "warm": true,
        "check_min_minor": null,
        "check_max_minor": null,
        "committed_minor": 2000000000,
        "next_step": null,
        "next_step_on": null,
        "next_step_overdue": false,
        "last_touch_at": null,
        "thesis_notes": null,
        "source": "manual",
        "passed_reason": null,
        "stage_entered_at": "2026-11-05T13:48:33.511554+00:00",
        "days_in_stage": 0,
        "position": 0,
        "recorded_in_finance": false,
        "created_at": "2026-11-05T13:48:33.301074+00:00",
        "updated_at": "2026-11-05T13:48:33.500247+00:00"
      },
      {
        "id": "4f0ad609-61b6-4ef9-be55-a90ddf00fc6a",
        "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
        "name": "Dami Lawal",
        "firm": "Sahara Syndicate",
        "type": "syndicate",
        "stage": "passed",
        "warm": false,
        "check_min_minor": null,
        "check_max_minor": null,
        "committed_minor": null,
        "next_step": null,
        "next_step_on": null,
        "next_step_overdue": false,
        "last_touch_at": null,
        "thesis_notes": null,
        "source": "manual",
        "passed_reason": "stage_mismatch",
        "stage_entered_at": "2026-11-05T13:48:33.443722+00:00",
        "days_in_stage": 0,
        "position": 0,
        "recorded_in_finance": false,
        "created_at": "2026-11-05T13:48:33.343592+00:00",
        "updated_at": "2026-11-05T13:48:33.429471+00:00"
      }
    ],
    "total": 4,
    "limit": 200,
    "offset": 0
  },
  "meta": null
}
```
Filtered (`?q=lagos&warm=true`):

capture `investors_filtered`
```json
{
  "data": {
    "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
    "items": [
      {
        "id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
        "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
        "name": "Tolu Bello",
        "firm": "Lagos Angels",
        "type": "angel",
        "stage": "outreach",
        "warm": true,
        "check_min_minor": 500000000,
        "check_max_minor": 2000000000,
        "committed_minor": null,
        "next_step": "Send the deck",
        "next_step_on": "2026-11-08",
        "next_step_overdue": false,
        "last_touch_at": null,
        "thesis_notes": "Backs fintech founders in Lagos.",
        "source": "manual",
        "passed_reason": null,
        "stage_entered_at": "2026-11-05T13:48:33.388328+00:00",
        "days_in_stage": 0,
        "position": 0,
        "recorded_in_finance": false,
        "created_at": "2026-11-05T13:48:33.129466+00:00",
        "updated_at": "2026-11-05T13:48:33.371666+00:00"
      }
    ],
    "total": 1,
    "limit": 200,
    "offset": 0
  },
  "meta": null
}
```
**After a round is closed the list is that closed round's investors** (history):

capture `investors_closed_round_history`
```json
{
  "data": {
    "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
    "items": [
      {
        "id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
        "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
        "name": "Tolu Bello",
        "firm": "Lagos Angels",
        "type": "angel",
        "stage": "outreach",
        "warm": false,
        "check_min_minor": 500000000,
        "check_max_minor": 2000000000,
        "committed_minor": null,
        "next_step": "Send the deck",
        "next_step_on": "2026-11-08",
        "next_step_overdue": false,
        "last_touch_at": "2026-11-04T13:48:33.654042+00:00",
        "thesis_notes": "Backs fintech founders in Lagos and Accra.",
        "source": "manual",
        "passed_reason": null,
        "stage_entered_at": "2026-11-05T13:48:33.388328+00:00",
        "days_in_stage": 0,
        "position": 0,
        "recorded_in_finance": false,
        "created_at": "2026-11-05T13:48:33.129466+00:00",
        "updated_at": "2026-11-05T13:48:33.657511+00:00"
      },
      {
        "id": "d61bd622-64c7-469e-abf2-8d99e6316a6a",
        "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
        "name": "Bola Adeyemi",
        "firm": "Kora Ventures",
        "type": "vc",
        "stage": "term_sheet",
        "warm": false,
        "check_min_minor": null,
        "check_max_minor": null,
        "committed_minor": 3000000000,
        "next_step": null,
        "next_step_on": null,
        "next_step_overdue": false,
        "last_touch_at": null,
        "thesis_notes": null,
        "source": "manual",
        "passed_reason": null,
        "stage_entered_at": "2026-11-05T13:48:33.477247+00:00",
        "days_in_stage": 0,
        "position": 0,
        "recorded_in_finance": false,
        "created_at": "2026-11-05T13:48:33.234531+00:00",
        "updated_at": "2026-11-05T13:48:33.461839+00:00"
      },
      {
        "id": "925e6ff2-61a1-4e3c-a68a-d1949ece7f22",
        "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
        "name": "Chidi Okafor",
        "firm": null,
        "type": "angel",
        "stage": "committed",
        "warm": true,
        "check_min_minor": null,
        "check_max_minor": null,
        "committed_minor": 2000000000,
        "next_step": null,
        "next_step_on": null,
        "next_step_overdue": false,
        "last_touch_at": null,
        "thesis_notes": null,
        "source": "manual",
        "passed_reason": null,
        "stage_entered_at": "2026-11-05T13:48:33.511554+00:00",
        "days_in_stage": 0,
        "position": 0,
        "recorded_in_finance": true,
        "created_at": "2026-11-05T13:48:33.301074+00:00",
        "updated_at": "2026-11-05T13:48:33.743818+00:00"
      }
    ],
    "total": 3,
    "limit": 200,
    "offset": 0
  },
  "meta": null
}
```

### Investor fields

| Field | Notes |
|---|---|
| `type` | `angel|vc|syndicate|other`. Default `angel`. |
| `warm` | boolean: the Warm / Cold chip. |
| `check_min_minor`, `check_max_minor` | cheque range, round currency, either may be `null`; max cannot be below min. Format "₦5M - ₦20M". |
| `committed_minor` | the amount this investor is putting in. Counts toward the bar only at Term Sheet (soft) or Committed. |
| `next_step`, `next_step_on`, `next_step_overdue` | `next_step_overdue` is computed server-side (past date and not Committed/Passed). |
| `last_touch_at` | `null` until a note/email/call/meeting is logged; moving a card is **not** a touch. |
| `stage_entered_at`, `days_in_stage` | for "stuck" hints. |
| `passed_reason` | non-null only at `passed`: `valuation|stage_mismatch|sector_conflict|ghosted|other` (Valuation, Stage mismatch, Sector conflict, Ghosted, Other). |
| `position` | order inside its column, starts at 0. |
| `recorded_in_finance` | true once the money was recorded (section 8). |
| `source` | `manual` today; `directory` arrives with the investor directory. |

### POST /investors (201)

Required: `name`. Optional: `firm`, `type`, `stage` (any except `passed`, default `research`), `warm`,
`check_min_minor`, `check_max_minor`, `committed_minor`, `next_step`, `next_step_on`, `thesis_notes`.
The card is added at the end of its column.

capture `investor_created_first`
```json
{
  "data": {
    "id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
    "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
    "name": "Tolu Bello",
    "firm": "Lagos Angels",
    "type": "angel",
    "stage": "research",
    "warm": true,
    "check_min_minor": 500000000,
    "check_max_minor": 2000000000,
    "committed_minor": null,
    "next_step": "Send the deck",
    "next_step_on": "2026-11-08",
    "next_step_overdue": false,
    "last_touch_at": null,
    "thesis_notes": "Backs fintech founders in Lagos.",
    "source": "manual",
    "passed_reason": null,
    "stage_entered_at": "2026-11-05T13:48:33.142920+00:00",
    "days_in_stage": 0,
    "position": 0,
    "recorded_in_finance": false,
    "created_at": "2026-11-05T13:48:33.129466+00:00",
    "updated_at": "2026-11-05T13:48:33.129466+00:00"
  },
  "meta": null
}
```

Validation errors (verified live). Field names are the body field names, so highlight the input:

capture `error_validation`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "name",
        "message": "String should have at least 1 character"
      }
    ]
  }
}
```
capture `error_cheque_range`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "check_max_minor",
        "message": "The maximum cheque can't be below the minimum."
      }
    ]
  }
}
```

### PATCH /investors/{id}

Any of the card fields except `stage`, `warm` included. Omitted = untouched, explicit `null` clears.
**PATCH rejects `stage`** (422): moving goes through `/stage` so history and the Passed reason stay honest.

capture `investor_patched`
```json
{
  "data": {
    "id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
    "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
    "name": "Tolu Bello",
    "firm": "Lagos Angels",
    "type": "angel",
    "stage": "outreach",
    "warm": false,
    "check_min_minor": 500000000,
    "check_max_minor": 2000000000,
    "committed_minor": null,
    "next_step": "Send the deck",
    "next_step_on": "2026-11-08",
    "next_step_overdue": false,
    "last_touch_at": null,
    "thesis_notes": "Backs fintech founders in Lagos and Accra.",
    "source": "manual",
    "passed_reason": null,
    "stage_entered_at": "2026-11-05T13:48:33.388328+00:00",
    "days_in_stage": 0,
    "position": 0,
    "recorded_in_finance": false,
    "created_at": "2026-11-05T13:48:33.129466+00:00",
    "updated_at": "2026-11-05T13:48:33.638055+00:00"
  },
  "meta": null
}
```

### GET /investors/{id}, DELETE /investors/{id}

capture `investor_detail`
```json
{
  "data": {
    "id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
    "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
    "name": "Tolu Bello",
    "firm": "Lagos Angels",
    "type": "angel",
    "stage": "outreach",
    "warm": false,
    "check_min_minor": 500000000,
    "check_max_minor": 2000000000,
    "committed_minor": null,
    "next_step": "Send the deck",
    "next_step_on": "2026-11-08",
    "next_step_overdue": false,
    "last_touch_at": "2026-11-04T13:48:33.654042+00:00",
    "thesis_notes": "Backs fintech founders in Lagos and Accra.",
    "source": "manual",
    "passed_reason": null,
    "stage_entered_at": "2026-11-05T13:48:33.388328+00:00",
    "days_in_stage": 0,
    "position": 0,
    "recorded_in_finance": false,
    "created_at": "2026-11-05T13:48:33.129466+00:00",
    "updated_at": "2026-11-05T13:48:33.657511+00:00"
  },
  "meta": null
}
```
capture `investor_deleted`
```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```
Delete is permanent and removes the card's interaction log. Confirm in the UI. Unknown id:

capture `error_not_found`
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

## 7. Moving cards: POST /investors/{id}/stage

Body: `stage`, optional `passed_reason`, `position` (0-based index in the target column), `note`.
Response is `{"investor": {...}, "record_in_finance": null | {...}}` (section 8). The server also writes
a `stage_change` row in the interaction log and resets `stage_entered_at`.

capture `stage_moved` (request: `{"stage": "outreach"}`)
```json
{
  "data": {
    "investor": {
      "id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
      "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
      "name": "Tolu Bello",
      "firm": "Lagos Angels",
      "type": "angel",
      "stage": "outreach",
      "warm": true,
      "check_min_minor": 500000000,
      "check_max_minor": 2000000000,
      "committed_minor": null,
      "next_step": "Send the deck",
      "next_step_on": "2026-11-08",
      "next_step_overdue": false,
      "last_touch_at": null,
      "thesis_notes": "Backs fintech founders in Lagos.",
      "source": "manual",
      "passed_reason": null,
      "stage_entered_at": "2026-11-05T13:48:33.388328+00:00",
      "days_in_stage": 0,
      "position": 0,
      "recorded_in_finance": false,
      "created_at": "2026-11-05T13:48:33.129466+00:00",
      "updated_at": "2026-11-05T13:48:33.371666+00:00"
    },
    "record_in_finance": null
  },
  "meta": null
}
```

**Passed needs a reason.** Open the reason modal (the five reasons) *before* calling, then send
`{"stage": "passed", "passed_reason": "stage_mismatch"}`. Without it:

capture `stage_passed_needs_reason`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Pick a reason for passing.",
    "field_errors": [
      {
        "field": "passed_reason",
        "message": "Pick a reason for passing."
      }
    ]
  }
}
```
capture `stage_passed` (request: `{"stage": "passed", "passed_reason": "stage_mismatch", "note": "Too early for them"}`)
```json
{
  "data": {
    "investor": {
      "id": "4f0ad609-61b6-4ef9-be55-a90ddf00fc6a",
      "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
      "name": "Dami Lawal",
      "firm": "Sahara Syndicate",
      "type": "syndicate",
      "stage": "passed",
      "warm": false,
      "check_min_minor": null,
      "check_max_minor": null,
      "committed_minor": null,
      "next_step": null,
      "next_step_on": null,
      "next_step_overdue": false,
      "last_touch_at": null,
      "thesis_notes": null,
      "source": "manual",
      "passed_reason": "stage_mismatch",
      "stage_entered_at": "2026-11-05T13:48:33.443722+00:00",
      "days_in_stage": 0,
      "position": 0,
      "recorded_in_finance": false,
      "created_at": "2026-11-05T13:48:33.343592+00:00",
      "updated_at": "2026-11-05T13:48:33.429471+00:00"
    },
    "record_in_finance": null
  },
  "meta": null
}
```
Sending `passed_reason` with any other stage is a 422. Moving a card **out** of Passed clears the reason.

**Reordering inside a column** is the same call with the *same* `stage` plus `position`. No history
row is written, and the response has `record_in_finance: null`:

capture `stage_reorder_same_column` (request: `{"stage": "outreach", "position": 0}`)
```json
{
  "data": {
    "investor": {
      "id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
      "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
      "name": "Tolu Bello",
      "firm": "Lagos Angels",
      "type": "angel",
      "stage": "outreach",
      "warm": true,
      "check_min_minor": 500000000,
      "check_max_minor": 2000000000,
      "committed_minor": null,
      "next_step": "Send the deck",
      "next_step_on": "2026-11-08",
      "next_step_overdue": false,
      "last_touch_at": null,
      "thesis_notes": "Backs fintech founders in Lagos.",
      "source": "manual",
      "passed_reason": null,
      "stage_entered_at": "2026-11-05T13:48:33.388328+00:00",
      "days_in_stage": 0,
      "position": 0,
      "recorded_in_finance": false,
      "created_at": "2026-11-05T13:48:33.129466+00:00",
      "updated_at": "2026-11-05T13:48:33.371666+00:00"
    },
    "record_in_finance": null
  },
  "meta": null
}
```
Positions are re-numbered 0..n-1 in both the source and the target column, so after a drag re-fetch
`GET /investors` (or apply the same renumbering) instead of trusting stale `position` values.

## 8. Recording the money in Finance (never automatic)

Moving a card to **Committed** when it has a `committed_minor` returns an **offer** in
`record_in_finance`. The server has written **nothing** to Finance at this point (verified live: the
Finance `Funding` list is empty before the founder confirms).

capture `stage_committed_with_offer` (request: `{"stage": "committed"}`)
```json
{
  "data": {
    "investor": {
      "id": "925e6ff2-61a1-4e3c-a68a-d1949ece7f22",
      "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
      "name": "Chidi Okafor",
      "firm": null,
      "type": "angel",
      "stage": "committed",
      "warm": true,
      "check_min_minor": null,
      "check_max_minor": null,
      "committed_minor": 2000000000,
      "next_step": null,
      "next_step_on": null,
      "next_step_overdue": false,
      "last_touch_at": null,
      "thesis_notes": null,
      "source": "manual",
      "passed_reason": null,
      "stage_entered_at": "2026-11-05T13:48:33.511554+00:00",
      "days_in_stage": 0,
      "position": 0,
      "recorded_in_finance": false,
      "created_at": "2026-11-05T13:48:33.301074+00:00",
      "updated_at": "2026-11-05T13:48:33.500247+00:00"
    },
    "record_in_finance": {
      "amount_minor": 2000000000,
      "currency": "NGN",
      "needs_ngn_amount": false,
      "fits_ledger": true
    }
  },
  "meta": null
}
```
capture `finance_before_confirm` (`GET /finance/transactions?category=Funding`)
```json
{
  "data": {
    "transactions": []
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": 0
  }
}
```

**UX instruction:** when `record_in_finance` is non-null, show a confirm: "Record this in Finance?
Only do this once the money has arrived." with the amount. The founder may dismiss it and do it later
from the card (call `record-funding` any time while the card is Committed). Do not call it
automatically.

On confirm: `POST /investors/{id}/record-funding` with optional `amount_minor` (naira, see below) and
`received_on` (date, defaults to today). It writes one `Funding`-category **inflow** through the
Finance ledger (cash on hand, never revenue) and marks the card `recorded_in_finance: true`.

capture `record_funding` (request: `{"received_on": "<today>"}`)
```json
{
  "data": {
    "investor": {
      "id": "925e6ff2-61a1-4e3c-a68a-d1949ece7f22",
      "round_id": "cce45c65-f17e-4530-b999-da4d670cb42c",
      "name": "Chidi Okafor",
      "firm": null,
      "type": "angel",
      "stage": "committed",
      "warm": true,
      "check_min_minor": null,
      "check_max_minor": null,
      "committed_minor": 2000000000,
      "next_step": null,
      "next_step_on": null,
      "next_step_overdue": false,
      "last_touch_at": null,
      "thesis_notes": null,
      "source": "manual",
      "passed_reason": null,
      "stage_entered_at": "2026-11-05T13:48:33.511554+00:00",
      "days_in_stage": 0,
      "position": 0,
      "recorded_in_finance": true,
      "created_at": "2026-11-05T13:48:33.301074+00:00",
      "updated_at": "2026-11-05T13:48:33.743818+00:00"
    },
    "transaction": {
      "id": "bc299daf-0652-4213-bdca-38b31f0cbded",
      "date": "2026-11-05",
      "description": "Funding from Chidi Okafor",
      "category": "Funding",
      "amount_minor": 2000000000,
      "currency": "NGN",
      "direction": "in",
      "source": "manual",
      "created_at": "2026-11-05T13:48:33.743818Z",
      "updated_at": "2026-11-05T13:48:33.743818Z"
    }
  },
  "meta": null
}
```
capture `finance_after_confirm`
```json
{
  "data": {
    "transactions": [
      {
        "id": "bc299daf-0652-4213-bdca-38b31f0cbded",
        "date": "2026-11-05",
        "description": "Funding from Chidi Okafor",
        "category": "Funding",
        "amount_minor": 2000000000,
        "currency": "NGN",
        "direction": "in",
        "source": "manual",
        "created_at": "2026-11-05T13:48:33.743818Z",
        "updated_at": "2026-11-05T13:48:33.743818Z"
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": 1
  }
}
```
It is **idempotent by refusal**: a second call is a 409 and nothing is written again. A card that is
not Committed with an amount is a 409 too:

capture `record_funding_again`
```json
{
  "error": {
    "code": "ALREADY_RECORDED",
    "message": "This money is already recorded in Finance.",
    "field_errors": []
  }
}
```
capture `record_funding_not_committed`
```json
{
  "error": {
    "code": "NOT_COMMITTED",
    "message": "Move the investor to Committed with an amount first.",
    "field_errors": []
  }
}
```
Once recorded, `record_in_finance` is `null` on later moves (no second offer).

### After the money is recorded, the amount and currency are frozen

So the pipeline and the ledger can't disagree, once an investor is `recorded_in_finance`:
changing that investor's `committed_minor` is a **409 `INVESTOR_RECORDED`**, and changing the round's
currency is a **409 `ROUND_HAS_RECORDED_MONEY`** (both verified live). Re-sending the same amount, or
editing any other field, still works. Disable the amount input on a recorded card with a note
"Recorded in Finance"; if the number was wrong, the fix is in Finance.

capture `error_amount_frozen_after_record` (request: `{"committed_minor": 2500000000}`)
```json
{
  "error": {
    "code": "INVESTOR_RECORDED",
    "message": "This money is already recorded in Finance, so the amount can't change here.",
    "field_errors": []
  }
}
```
capture `error_currency_frozen_after_record` (request: `{"currency": "USD"}`)
```json
{
  "error": {
    "code": "ROUND_HAS_RECORDED_MONEY",
    "message": "Money from this round is already recorded in Finance, so its currency is fixed.",
    "field_errors": []
  }
}
```

### USD rounds: Finance is naira only

Finance records NGN only. For a USD round the offer says so, and `amount_minor` (naira, kobo) is
**required** on confirm. Show a naira input ("How much did you receive in naira?"); do not guess a rate.

capture `usd_stage_committed_offer`
```json
{
  "data": {
    "investor": {
      "id": "40f152c2-5c97-4b59-b65b-f025d9753c35",
      "round_id": "e12c6f10-3b53-4127-9c27-14dbba6a25ee",
      "name": "Emeka Obi",
      "firm": "Atlas Capital",
      "type": "vc",
      "stage": "committed",
      "warm": false,
      "check_min_minor": null,
      "check_max_minor": null,
      "committed_minor": 50000000,
      "next_step": null,
      "next_step_on": null,
      "next_step_overdue": false,
      "last_touch_at": null,
      "thesis_notes": null,
      "source": "manual",
      "passed_reason": null,
      "stage_entered_at": "2026-11-05T13:48:35.725274+00:00",
      "days_in_stage": 0,
      "position": 0,
      "recorded_in_finance": false,
      "created_at": "2026-11-05T13:48:35.635349+00:00",
      "updated_at": "2026-11-05T13:48:35.685026+00:00"
    },
    "record_in_finance": {
      "amount_minor": null,
      "currency": "USD",
      "needs_ngn_amount": true,
      "fits_ledger": true
    }
  },
  "meta": null
}
```
capture `usd_record_needs_naira` (request: `{}`)
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This round is in USD and Finance records NGN. Enter the naira amount you received.",
    "field_errors": [
      {
        "field": "amount_minor",
        "message": "This round is in USD and Finance records NGN. Enter the naira amount you received."
      }
    ]
  }
}
```
capture `usd_record_funding` (request: `{"amount_minor": 75000000}`)
```json
{
  "data": {
    "investor": {
      "id": "40f152c2-5c97-4b59-b65b-f025d9753c35",
      "round_id": "e12c6f10-3b53-4127-9c27-14dbba6a25ee",
      "name": "Emeka Obi",
      "firm": "Atlas Capital",
      "type": "vc",
      "stage": "committed",
      "warm": false,
      "check_min_minor": null,
      "check_max_minor": null,
      "committed_minor": 50000000,
      "next_step": null,
      "next_step_on": null,
      "next_step_overdue": false,
      "last_touch_at": null,
      "thesis_notes": null,
      "source": "manual",
      "passed_reason": null,
      "stage_entered_at": "2026-11-05T13:48:35.725274+00:00",
      "days_in_stage": 0,
      "position": 0,
      "recorded_in_finance": true,
      "created_at": "2026-11-05T13:48:35.635349+00:00",
      "updated_at": "2026-11-05T13:48:35.850435+00:00"
    },
    "transaction": {
      "id": "6be6e056-043c-436c-a666-82c63349abb6",
      "date": "2026-11-05",
      "description": "Funding from Emeka Obi (Atlas Capital)",
      "category": "Funding",
      "amount_minor": 75000000,
      "currency": "NGN",
      "direction": "in",
      "source": "manual",
      "created_at": "2026-11-05T13:48:35.850435Z",
      "updated_at": "2026-11-05T13:48:35.850435Z"
    }
  },
  "meta": null
}
```

### Ledger size limit (known trap)

The Finance ledger stores each entry as a 32-bit integer, so one entry is at most **₦21,474,836.47**
(`2147483647` kobo). For a NGN commitment above that, the offer has `"fits_ledger": false` and
`record-funding` without an amount answers 422 on `amount_minor` with "Finance records up to
₦21,474,836.47 per entry. Enter a smaller amount, or add the rest in Finance." The founder may record
one part (a smaller `amount_minor`); the card is then `recorded_in_finance`, so the remainder has to
be added in Finance directly. *Not captured live (needs a ₦21M+ amount);
shape asserted in `test_record_funding_above_the_ledger_cap_is_refused_clearly`.* This limit is a
Finance gap, tracked as a follow-up.

## 9. Interaction log

`GET /investors/{id}/events` (newest first, `limit` 1 to 200 default 50, `offset`) and
`POST /investors/{id}/events` (201). `kind` for POST is `note|email|call|meeting`; `body` 1 to 4000;
`occurred_at` optional (default now, not in the future). `stage_change` rows are written by the server
and cannot be posted. Logging an interaction updates the card's `last_touch_at`.

capture `event_created` (request: `{"kind": "call", "body": "...", "occurred_at": "<yesterday>"}`)
```json
{
  "data": {
    "id": "0fdcafbc-18ac-44e8-bc08-8e6bf77c5895",
    "investor_id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
    "kind": "call",
    "body": "30 minute intro. Wants the cohort numbers.",
    "from_stage": null,
    "to_stage": null,
    "occurred_at": "2026-11-04T13:48:33.654042+00:00",
    "actor_id": "f25734ab-8edb-462a-9b47-5cb57d5c4184"
  },
  "meta": null
}
```
capture `events_list`
```json
{
  "data": {
    "items": [
      {
        "id": "577f87c4-3728-4130-a6a4-587802b58818",
        "investor_id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
        "kind": "stage_change",
        "body": null,
        "from_stage": "research",
        "to_stage": "outreach",
        "occurred_at": "2026-11-05T13:48:33.388328+00:00",
        "actor_id": "f25734ab-8edb-462a-9b47-5cb57d5c4184"
      },
      {
        "id": "4c487ee6-bc81-43c4-9313-bbd76f7096c3",
        "investor_id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
        "kind": "stage_change",
        "body": null,
        "from_stage": null,
        "to_stage": "research",
        "occurred_at": "2026-11-05T13:48:33.142920+00:00",
        "actor_id": "f25734ab-8edb-462a-9b47-5cb57d5c4184"
      },
      {
        "id": "0fdcafbc-18ac-44e8-bc08-8e6bf77c5895",
        "investor_id": "09b70b75-0e38-47f0-8584-a86e9ad4061f",
        "kind": "call",
        "body": "30 minute intro. Wants the cohort numbers.",
        "from_stage": null,
        "to_stage": null,
        "occurred_at": "2026-11-04T13:48:33.654042+00:00",
        "actor_id": "f25734ab-8edb-462a-9b47-5cb57d5c4184"
      }
    ],
    "total": 3,
    "limit": 50,
    "offset": 0
  },
  "meta": null
}
```
In the timeline, `stage_change` rows have `from_stage` (null on creation), `to_stage` and the optional
`body` note; the other kinds have `body` only.

## 10. Auth and tenancy errors

capture `error_no_token`
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```
capture `error_no_workspace_header`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "header.X-Workspace-Id",
        "message": "Field required"
      }
    ]
  }
}
```

## 11. Notification

`funding.round.committed_changed` is created when the **Committed** total changes (a move into or out
of Committed, an amount edit while Committed, or a delete). Recipients: founder and team members,
**excluding the person who made the change**. Title: "Funding committed is now ₦20,000,000 of
₦90,000,000" (without " of ..." when there is no target). CTA "See pipeline", `href` `/funding`.
Category in Preferences: Finance (no new row). Soft (Term Sheet) money never notifies. *Not live:* the
e2e founder is alone and is the actor, so nobody is notified; delivery, recipients and title are
asserted in `test_committed_change_notifies_founder_and_team_minus_actor_only`.

## 12. UX consequences the FE must surface

1. Money is minor units in the **round's** currency; show the right symbol and never mix currencies.
2. The currency cannot change once amounts exist (422), nor once money is recorded (409): say why when the control is refused. A recorded investor's amount is read-only.
3. After closing a round, the board is read-only history until a new round is started explicitly.
4. Passed always needs a reason; Passed investors never count toward the bar.
5. The Finance prompt is a prompt: nothing is written until the founder confirms the money arrived.
6. USD rounds need a naira amount to record the money.
7. Delete is permanent (it removes the interaction log).
8. Footer tip copy to correct (gap row 38): the brief is the "Prep me" button *inside* the drawer
   (slice F), not a click on the card.

## 13. Verification table

| Behaviour | Status |
|---|---|
| Empty state: round null, overview empty, investors empty | verified live |
| First investor auto-creates a planning NGN round | verified live |
| Set / open / close the round; closed round is history; new round needs PUT | verified live |
| Currency lock once amounts exist | verified live |
| Add, patch, detail, delete investor; validation errors with field names | verified live |
| Seven stages, Passed reason required, reason cleared on leaving | verified live (clearing: unit tested) |
| Reorder in a column; positions renumbered | verified live (renumbering: unit tested) |
| Header bar soft vs committed, percentages, null when no target | verified live |
| Filters `q`, `warm`, `stage`, `type`, pagination | verified live (`q`, `warm`); others unit tested |
| Interaction log, `last_touch_at` | verified live |
| Offer only, nothing written to Finance until confirm | verified live |
| `record-funding` NGN, 409 repeat, 409 not committed | verified live |
| `record-funding` on a USD round (naira amount) | verified live |
| Amount and currency frozen after recording (409s) | verified live |
| Two requests racing to create the first round (no 500; the loser joins the winner's round) | **not live** (needs true concurrency); unit tested by simulating the lost race |
| Ledger 32-bit cap message (`fits_ledger: false`) | **not live**; unit tested |
| Suggested target (18 months of net burn) | **not live**; unit tested |
| `next_step_overdue`, `overdue_next_steps` | **not live**; unit tested |
| 403 for a refused role (mentor) on read and write, and workspace mismatch | verified live; all 7 roles on every route unit tested |
| 402 plan gate | **not live** (enforcement off on the e2e server); unit tested |
| Notification delivery and recipients | **not live**; unit tested |
| Cross-workspace isolation (404 / 403) | verified live |
