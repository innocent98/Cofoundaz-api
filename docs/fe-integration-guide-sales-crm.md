# FE integration guide: Sales CRM core (slice A)

Module 11 (Sales Hub), slice A: the pipeline board, the deal drawer, the stage editor, Leads (manual
and CSV import, "Make it a deal"), Accounts with the account page and contact drawer, the activity
timeline, stall alerts, the Dashboard pipeline tile, and Sales in search and the activity feed.
Sequences, the role-play coach and Sales analytics are later slices (B, C) and are not in this guide.

Every body below is pasted by script from a live run of `e2e/test_sales_a.py` (captures in
`e2e/_captures/sales_a/`, named in each section). Ids, timestamps and e-mail addresses are throwaway
test data (`*.example` addresses are placeholders). Where a body was trimmed it says so; where
something was not exercised live it says so inline and in the table at the end.

## 0. Ground rules

- Base path `/api/v1/sales`. Every route needs `Authorization: Bearer ...` and `X-Workspace-Id`.
- **Founders and team members only.** Every other role gets `403 FORBIDDEN` on every route (section 11).
  Hide Sales from the sidebar for those roles.
- Success envelope: `{"data": ..., "meta": null}`. Error envelope: `{"error": {"code", "message", "field_errors"}}`.
  Business-rule 422s carry `code: "VALIDATION_ERROR"` and a one-entry `field_errors` list whose `message`
  equals the top-level `message`: show it next to the named `field`. Pydantic-level 422s (wrong type,
  out of range) have the generic top-level message `Please check the highlighted fields.` and the real
  text only in `field_errors[].message` (it can start with `Value error, `). Always render `field_errors`.
- **Money is integer kobo** in `value_minor`, currency is always `"NGN"`. Divide by 100 for naira.
  Never send or expect decimals.
- **List envelope** for accounts, contacts, leads, deals and activities:
  `{"data": {"items": [...], "total": N, "limit": L, "offset": O}}`. `limit` 1 to 200 (default 50), `offset` >= 0.
  `total` is the count for the current filter, not the page size. Stages are a plain array (no envelope).
- Unknown ids, and ids that belong to another workspace, both return `404 NOT_FOUND` (shape in section 9).
- Ids in request bodies that must exist in the workspace (`account_id`, `contact_id`, `stage_id`,
  `owner_id`) return a 422 on the named field instead of a 404. Owners must be a current founder or team member.

---

## 1. Pipeline board

Load order: `GET /sales` (header numbers + per-stage counts), `GET /sales/stages` (column definitions and
order), then `GET /sales/deals?stage_id=...` per column (or one unfiltered call and group client side).

**The first Sales call in a new workspace seeds the default stages**; there is no "set up pipeline" step.

### 1.1 Overview, `GET /sales`

Fresh workspace (`overview_empty.json`), 7 default stages, all zeros, `win_rate` is `null`:
```json
{
  "data": {
    "total_pipeline_minor": 0,
    "weighted_forecast_minor": 0,
    "win_rate": null,
    "currency": "NGN",
    "stages": [
      {
        "stage_id": "42bb4376-6516-42e8-afa4-2868b9d57582",
        "name": "New",
        "kind": "open",
        "deal_count": 0,
        "value_minor": 0
      },
      {
        "stage_id": "18d4f674-b17f-4e3c-ad92-33288b28bed4",
        "name": "Contacted",
        "kind": "open",
        "deal_count": 0,
        "value_minor": 0
      },
      {
        "stage_id": "d4b9b58a-7bcd-48ec-8010-b9cd3961d642",
        "name": "Qualified",
        "kind": "open",
        "deal_count": 0,
        "value_minor": 0
      },
      {
        "stage_id": "d5ec6cf1-bdaf-4345-9a10-db64efcf02b8",
        "name": "Proposal",
        "kind": "open",
        "deal_count": 0,
        "value_minor": 0
      },
      {
        "stage_id": "a3f61ce8-663e-45c0-a5ec-e7fe04761418",
        "name": "Negotiation",
        "kind": "open",
        "deal_count": 0,
        "value_minor": 0
      },
      {
        "stage_id": "86b96932-77b7-4915-983e-00d5da346f36",
        "name": "Closed Won",
        "kind": "won",
        "deal_count": 0,
        "value_minor": 0
      },
      {
        "stage_id": "bbc20e32-c63a-4ed9-a153-5f269239e62a",
        "name": "Closed Lost",
        "kind": "lost",
        "deal_count": 0,
        "value_minor": 0
      }
    ]
  },
  "meta": null
}
```

After the journey (`overview.json`): one open deal (80,000,000 kobo = 800,000 naira) in Qualified, two
deals still in New (both created by lead conversion with value 0), one won deal worth 300,000,000 kobo:
```json
{
  "data": {
    "total_pipeline_minor": 80000000,
    "weighted_forecast_minor": 32000000,
    "win_rate": 100.0,
    "currency": "NGN",
    "stages": [
      {
        "stage_id": "42bb4376-6516-42e8-afa4-2868b9d57582",
        "name": "New",
        "kind": "open",
        "deal_count": 2,
        "value_minor": 0
      },
      {
        "stage_id": "18d4f674-b17f-4e3c-ad92-33288b28bed4",
        "name": "Discovery",
        "kind": "open",
        "deal_count": 0,
        "value_minor": 0
      },
      {
        "stage_id": "d4b9b58a-7bcd-48ec-8010-b9cd3961d642",
        "name": "Qualified",
        "kind": "open",
        "deal_count": 1,
        "value_minor": 80000000
      },
      {
        "stage_id": "d5ec6cf1-bdaf-4345-9a10-db64efcf02b8",
        "name": "Proposal",
        "kind": "open",
        "deal_count": 0,
        "value_minor": 0
      },
      {
        "stage_id": "a3f61ce8-663e-45c0-a5ec-e7fe04761418",
        "name": "Negotiation",
        "kind": "open",
        "deal_count": 0,
        "value_minor": 0
      },
      {
        "stage_id": "86b96932-77b7-4915-983e-00d5da346f36",
        "name": "Closed Won",
        "kind": "won",
        "deal_count": 1,
        "value_minor": 300000000
      },
      {
        "stage_id": "bbc20e32-c63a-4ed9-a153-5f269239e62a",
        "name": "Closed Lost",
        "kind": "lost",
        "deal_count": 0,
        "value_minor": 0
      }
    ]
  },
  "meta": null
}
```

How the numbers work (read from the response above):

| Field | Meaning |
|---|---|
| `total_pipeline_minor` | Sum of `value_minor` over deals in **open** stages only. Won and lost value is excluded. |
| `weighted_forecast_minor` | Sum over open stages of value x the stage's `win_probability` / 100, rounded. Here: 80,000,000 x 40% = 32,000,000. |
| `win_rate` | won / (won + lost) x 100, one decimal. `null` until at least one deal is closed. It counts deals **currently** in the won or lost stage (a reopened deal leaves the count). |
| `stages[]` | One row per live stage in board order. `stage_id` here is the same id as `id` on the stage list (note the different key name). `value_minor` is the sum for that stage, including won and lost stages. |

### 1.2 Deals for the board, `GET /sales/deals`

Query: `stage_id`, `account_id`, `owner_id`, `q` (name search), `open_only` (bool), `limit`, `offset`.
Live-verified: `open_only=true&limit=2` (`deals_list_page.json`); the other filters come from the route
signature and are not exercised live. Trimmed to the shape of one item plus the envelope (the capture holds both items):
```json
{
  "data": {
    "items": [
      {
        "id": "6c62e89d-651d-4634-9c3e-3a12319b17be",
        "name": "Quiet renewal",
        "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
        "account_name": "Acme Logistics",
        "contact_id": null,
        "contact_name": null,
        "value_minor": 80000000,
        "currency": "NGN",
        "stage_id": "d4b9b58a-7bcd-48ec-8010-b9cd3961d642",
        "next_step": null,
        "next_step_due": null,
        "closed_reason": null,
        "closed_note": null,
        "closed_at": null,
        "owner_id": null,
        "last_activity_at": "2026-10-21T11:58:05.392345Z",
        "is_stalled": false,
        "days_inactive": 0,
        "created_at": "2026-10-21T11:58:05.389138Z"
      }
    ],
    "total": 4,
    "limit": 2,
    "offset": 0
  },
  "meta": null
}
```

A deal card needs: `name`, `account_name`, `value_minor`, `next_step`, `next_step_due`, `owner_id`, and the
**stall badge** from `is_stalled` / `days_inactive` (section 8). `contact_name` and `account_name` are
`null` when the deal has no contact or account.

---

## 2. Stage editor

`GET /sales/stages` returns the live stages in order. Default seed (`stages_seeded.json`):
```json
{
  "data": [
    {
      "id": "42bb4376-6516-42e8-afa4-2868b9d57582",
      "name": "New",
      "position": 0,
      "kind": "open",
      "win_probability": 10
    },
    {
      "id": "18d4f674-b17f-4e3c-ad92-33288b28bed4",
      "name": "Contacted",
      "position": 1,
      "kind": "open",
      "win_probability": 20
    },
    {
      "id": "d4b9b58a-7bcd-48ec-8010-b9cd3961d642",
      "name": "Qualified",
      "position": 2,
      "kind": "open",
      "win_probability": 40
    },
    {
      "id": "d5ec6cf1-bdaf-4345-9a10-db64efcf02b8",
      "name": "Proposal",
      "position": 3,
      "kind": "open",
      "win_probability": 60
    },
    {
      "id": "a3f61ce8-663e-45c0-a5ec-e7fe04761418",
      "name": "Negotiation",
      "position": 4,
      "kind": "open",
      "win_probability": 80
    },
    {
      "id": "86b96932-77b7-4915-983e-00d5da346f36",
      "name": "Closed Won",
      "position": 5,
      "kind": "won",
      "win_probability": 100
    },
    {
      "id": "bbc20e32-c63a-4ed9-a153-5f269239e62a",
      "name": "Closed Lost",
      "position": 6,
      "kind": "lost",
      "win_probability": 0
    }
  ],
  "meta": null
}
```

### 2.1 Save, `PUT /sales/stages`

One call replaces the whole list. **The array order is the new order** (`position` is assigned from it).
Existing stages are sent with their `id`; a **new stage has no `id`**. A stage that is omitted is deleted
(section 2.3). Body fields per stage: `name` (1 to 60 chars), `kind` (`open` / `won` / `lost`),
`win_probability` (0 to 100, integer).

Request used for the capture below: renamed "Contacted" to "Discovery" (same `id`) and inserted a new open
stage "Demo" (no `id`) at index 3:
```json
{"stages": [
  {"id": "<New id>",        "name": "New",       "kind": "open", "win_probability": 10},
  {"id": "<Contacted id>",  "name": "Discovery", "kind": "open", "win_probability": 20},
  {"id": "<Qualified id>",  "name": "Qualified", "kind": "open", "win_probability": 40},
  {                         "name": "Demo",      "kind": "open", "win_probability": 50},
  "... Proposal, Negotiation, Closed Won, Closed Lost with their ids ..."
]}
```
(This request body is described, not pasted: the script that ran it built it from the previous response.)
Response, `200`, full new list (`stages_replaced.json`):
```json
{
  "data": [
    {
      "id": "42bb4376-6516-42e8-afa4-2868b9d57582",
      "name": "New",
      "position": 0,
      "kind": "open",
      "win_probability": 10
    },
    {
      "id": "18d4f674-b17f-4e3c-ad92-33288b28bed4",
      "name": "Discovery",
      "position": 1,
      "kind": "open",
      "win_probability": 20
    },
    {
      "id": "d4b9b58a-7bcd-48ec-8010-b9cd3961d642",
      "name": "Qualified",
      "position": 2,
      "kind": "open",
      "win_probability": 40
    },
    {
      "id": "60d6d78e-1a79-4bfc-a98b-dcb8c7f998c8",
      "name": "Demo",
      "position": 3,
      "kind": "open",
      "win_probability": 50
    },
    {
      "id": "d5ec6cf1-bdaf-4345-9a10-db64efcf02b8",
      "name": "Proposal",
      "position": 4,
      "kind": "open",
      "win_probability": 60
    },
    {
      "id": "a3f61ce8-663e-45c0-a5ec-e7fe04761418",
      "name": "Negotiation",
      "position": 5,
      "kind": "open",
      "win_probability": 80
    },
    {
      "id": "86b96932-77b7-4915-983e-00d5da346f36",
      "name": "Closed Won",
      "position": 6,
      "kind": "won",
      "win_probability": 100
    },
    {
      "id": "bbc20e32-c63a-4ed9-a153-5f269239e62a",
      "name": "Closed Lost",
      "position": 7,
      "kind": "lost",
      "win_probability": 0
    }
  ],
  "meta": null
}
```

### 2.2 Errors

Too many stages, 11 sent (`stages_error_too_many.json`, `422`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "A pipeline needs between 1 and 10 stages.",
    "field_errors": [
      {
        "field": "stages",
        "message": "A pipeline needs between 1 and 10 stages."
      }
    ]
  }
}
```
No won stage in the list (`stages_error_no_won.json`, `422`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The pipeline needs exactly one Won stage.",
    "field_errors": [
      {
        "field": "stages",
        "message": "The pipeline needs exactly one Won stage."
      }
    ]
  }
}
```
Removing a stage that still has deals (`stages_error_has_deals.json`, `422`). The text is built from the
stage's current name, so show it as is:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Move the deals out of Qualified first.",
    "field_errors": [
      {
        "field": "stages",
        "message": "Move the deals out of Qualified first."
      }
    ]
  }
}
```

Rules the editor must respect (from the service; the three above are live, the rest are unit-tested and **not exercised live**):

- Between 1 and **10** live stages. Exactly **one** Won and **one** Lost stage.
- A stage's `kind` cannot change, and Won / Lost stages cannot be removed (they can be renamed and reordered).
  Unknown or repeated `id` returns `Unknown stage.`; duplicate names (case-insensitive) return `Stage names must be unique: <name>.`
- **Delete only when empty.** An empty open stage is removed by leaving it out of the array
  (`stages_removed_empty_stage.json`, `200`, "Demo" gone from the returned list). It is a soft delete
  (archived), so history rows that mention it still show its name. If it holds deals, the 422 above is returned
  and nothing is saved: tell the user to move the deals first. Disable the delete control on a column whose
  `deal_count` is above 0 and keep the 422 as the backstop.

---

## 3. Settings

`GET /sales/settings` (`settings_default.json`):
```json
{
  "data": {
    "stall_days": 14,
    "outreach_postal_address": null
  },
  "meta": null
}
```
`PATCH /sales/settings` with `{"stall_days": 7}` (`settings_updated.json`):
```json
{
  "data": {
    "stall_days": 7,
    "outreach_postal_address": null
  },
  "meta": null
}
```
`stall_days` must be 3 to 90 (`settings_error_stall_days.json`, `422`, sent `1`; generic pydantic wording):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "stall_days",
        "message": "Input should be greater than or equal to 3"
      }
    ]
  }
}
```
`outreach_postal_address` (string or `null`, max 500) is stored but only used by Sequences in slice B; the
board does not need it yet. Changing `stall_days` applies to all open deals at the next scheduler tick.

---

## 4. Deal drawer

### 4.1 Create, `POST /sales/deals`

Body: `name` (required), `account_id`, `contact_id`, `value_minor` (>= 0, default 0), `stage_id` (default: the
first stage), `next_step` (max 300), `next_step_due` (`YYYY-MM-DD`), `owner_id`.

Created in the default stage (`deal_created.json`):
```json
{
  "data": {
    "id": "f9c69088-b134-4147-aa03-e424c2fd2320",
    "name": "Acme fleet rollout",
    "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
    "account_name": "Acme Logistics",
    "contact_id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
    "contact_name": "Tunde Okafor",
    "value_minor": 250000000,
    "currency": "NGN",
    "stage_id": "42bb4376-6516-42e8-afa4-2868b9d57582",
    "next_step": "Send proposal",
    "next_step_due": "2026-10-24",
    "closed_reason": null,
    "closed_note": null,
    "closed_at": null,
    "owner_id": null,
    "last_activity_at": "2026-10-21T11:58:05.379020Z",
    "is_stalled": false,
    "days_inactive": 0,
    "created_at": "2026-10-21T11:58:05.374886Z"
  },
  "meta": null
}
```
Created directly in a chosen stage (`deal_created_in_stage.json`, `stage_id` of "Qualified"):
```json
{
  "data": {
    "id": "6c62e89d-651d-4634-9c3e-3a12319b17be",
    "name": "Quiet renewal",
    "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
    "account_name": "Acme Logistics",
    "contact_id": null,
    "contact_name": null,
    "value_minor": 80000000,
    "currency": "NGN",
    "stage_id": "d4b9b58a-7bcd-48ec-8010-b9cd3961d642",
    "next_step": null,
    "next_step_due": null,
    "closed_reason": null,
    "closed_note": null,
    "closed_at": null,
    "owner_id": null,
    "last_activity_at": "2026-10-21T11:58:05.392345Z",
    "is_stalled": false,
    "days_inactive": 0,
    "created_at": "2026-10-21T11:58:05.389138Z"
  },
  "meta": null
}
```
Negative value (`deal_error_negative_value.json`, `422`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "value_minor",
        "message": "Input should be greater than or equal to 0"
      }
    ]
  }
}
```

### 4.2 Read, `GET /sales/deals/{id}`

Same fields as a list item **plus `stage_history`** (`deal_detail.json`):
```json
{
  "data": {
    "id": "f9c69088-b134-4147-aa03-e424c2fd2320",
    "name": "Acme fleet rollout",
    "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
    "account_name": "Acme Logistics",
    "contact_id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
    "contact_name": "Tunde Okafor",
    "value_minor": 250000000,
    "currency": "NGN",
    "stage_id": "42bb4376-6516-42e8-afa4-2868b9d57582",
    "next_step": "Send proposal",
    "next_step_due": "2026-10-24",
    "closed_reason": null,
    "closed_note": null,
    "closed_at": null,
    "owner_id": null,
    "last_activity_at": "2026-10-21T11:58:05.379020Z",
    "is_stalled": false,
    "days_inactive": 0,
    "created_at": "2026-10-21T11:58:05.374886Z",
    "stage_history": [
      {
        "id": "7fa57f46-87f9-4997-bb09-92cf0ad4218d",
        "from_stage_id": null,
        "from_stage_name": null,
        "to_stage_id": "42bb4376-6516-42e8-afa4-2868b9d57582",
        "to_stage_name": "New",
        "changed_by": "6d491050-3918-40c1-a09e-f4f6145278e6",
        "changed_at": "2026-10-21T11:58:05.379020Z"
      }
    ]
  },
  "meta": null
}
```

### 4.3 Edit, `PATCH /sales/deals/{id}`

Editable: `name`, `account_id`, `contact_id`, `value_minor`, `next_step`, `next_step_due`, `owner_id`.
`{"value_minor": 300000000}` returns the deal (`deal_updated.json`). **Trap: this response has no
`stage_history`**; only the single-deal GET and the stage-move response carry it. Keep your history from the
GET, or refetch.
```json
{
  "data": {
    "id": "f9c69088-b134-4147-aa03-e424c2fd2320",
    "name": "Acme fleet rollout",
    "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
    "account_name": "Acme Logistics",
    "contact_id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
    "contact_name": "Tunde Okafor",
    "value_minor": 300000000,
    "currency": "NGN",
    "stage_id": "42bb4376-6516-42e8-afa4-2868b9d57582",
    "next_step": "Send proposal",
    "next_step_due": "2026-10-24",
    "closed_reason": null,
    "closed_note": null,
    "closed_at": null,
    "owner_id": null,
    "last_activity_at": "2026-10-21T11:58:05.379020Z",
    "is_stalled": false,
    "days_inactive": 0,
    "created_at": "2026-10-21T11:58:05.374886Z"
  },
  "meta": null
}
```
The stage cannot be changed here (`deal_patch_stage_error.json`, `422`, sent `stage_id`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Use the stage endpoint.",
    "field_errors": [
      {
        "field": "stage_id",
        "message": "Use the stage endpoint."
      }
    ]
  }
}
```

### 4.4 Move between stages, `POST /sales/deals/{id}/stage`

Body: `stage_id`, and for a **Lost** stage `closed_reason` (`price`, `timing`, `competitor`, `no_budget`,
`ghosted`, `other`) plus an optional `closed_note` (max 2000). The response is the deal **with**
`stage_history`.

**Lost without a reason is rejected** (`deal_move_lost_without_reason.json`, `422`). Open the reason picker
before calling, and keep the 422 as the backstop:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Pick a reason for losing this deal.",
    "field_errors": [
      {
        "field": "closed_reason",
        "message": "Pick a reason for losing this deal."
      }
    ]
  }
}
```
Lost with a reason (`deal_move_lost.json`): `closed_reason`, `closed_note` and `closed_at` are set:
```json
{
  "data": {
    "id": "f9c69088-b134-4147-aa03-e424c2fd2320",
    "name": "Acme fleet rollout",
    "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
    "account_name": "Acme Logistics",
    "contact_id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
    "contact_name": "Tunde Okafor",
    "value_minor": 300000000,
    "currency": "NGN",
    "stage_id": "bbc20e32-c63a-4ed9-a153-5f269239e62a",
    "next_step": "Send proposal",
    "next_step_due": "2026-10-24",
    "closed_reason": "price",
    "closed_note": "Budget frozen",
    "closed_at": "2026-10-21T11:58:05.466067Z",
    "owner_id": null,
    "last_activity_at": "2026-10-21T11:58:05.466067Z",
    "is_stalled": false,
    "days_inactive": 0,
    "created_at": "2026-10-21T11:58:05.374886Z",
    "stage_history": [
      {
        "id": "7fa57f46-87f9-4997-bb09-92cf0ad4218d",
        "from_stage_id": null,
        "from_stage_name": null,
        "to_stage_id": "42bb4376-6516-42e8-afa4-2868b9d57582",
        "to_stage_name": "New",
        "changed_by": "6d491050-3918-40c1-a09e-f4f6145278e6",
        "changed_at": "2026-10-21T11:58:05.379020Z"
      },
      {
        "id": "3368d961-a0bd-44da-b5c6-003c875c90e8",
        "from_stage_id": "42bb4376-6516-42e8-afa4-2868b9d57582",
        "from_stage_name": "New",
        "to_stage_id": "bbc20e32-c63a-4ed9-a153-5f269239e62a",
        "to_stage_name": "Closed Lost",
        "changed_by": "6d491050-3918-40c1-a09e-f4f6145278e6",
        "changed_at": "2026-10-21T11:58:05.466067Z"
      }
    ]
  },
  "meta": null
}
```
**Reopen**: moving the deal to any open stage clears `closed_at`, `closed_reason` and `closed_note` back to `null`
(`deal_move_reopen.json`, `stage_history` trimmed to the last entry; the capture has all three):
```json
{
  "data": {
    "id": "f9c69088-b134-4147-aa03-e424c2fd2320",
    "name": "Acme fleet rollout",
    "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
    "account_name": "Acme Logistics",
    "contact_id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
    "contact_name": "Tunde Okafor",
    "value_minor": 300000000,
    "currency": "NGN",
    "stage_id": "d5ec6cf1-bdaf-4345-9a10-db64efcf02b8",
    "next_step": "Send proposal",
    "next_step_due": "2026-10-24",
    "closed_reason": null,
    "closed_note": null,
    "closed_at": null,
    "owner_id": null,
    "last_activity_at": "2026-10-21T11:58:05.482607Z",
    "is_stalled": false,
    "days_inactive": 0,
    "created_at": "2026-10-21T11:58:05.374886Z",
    "stage_history": [
      {
        "id": "43152cdf-44f2-4025-96e4-ca3fd094f3c6",
        "from_stage_id": "bbc20e32-c63a-4ed9-a153-5f269239e62a",
        "from_stage_name": "Closed Lost",
        "to_stage_id": "d5ec6cf1-bdaf-4345-9a10-db64efcf02b8",
        "to_stage_name": "Proposal",
        "changed_by": "6d491050-3918-40c1-a09e-f4f6145278e6",
        "changed_at": "2026-10-21T11:58:05.482607Z"
      }
    ]
  },
  "meta": null
}
```
**Won**: `closed_at` is set, `closed_reason` stays `null` (`deal_move_won.json`, `stage_history` trimmed to the last entry):
```json
{
  "data": {
    "id": "f9c69088-b134-4147-aa03-e424c2fd2320",
    "name": "Acme fleet rollout",
    "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
    "account_name": "Acme Logistics",
    "contact_id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
    "contact_name": "Tunde Okafor",
    "value_minor": 300000000,
    "currency": "NGN",
    "stage_id": "86b96932-77b7-4915-983e-00d5da346f36",
    "next_step": "Send proposal",
    "next_step_due": "2026-10-24",
    "closed_reason": null,
    "closed_note": null,
    "closed_at": "2026-10-21T11:58:05.502915Z",
    "owner_id": null,
    "last_activity_at": "2026-10-21T11:58:05.502915Z",
    "is_stalled": false,
    "days_inactive": 0,
    "created_at": "2026-10-21T11:58:05.374886Z",
    "stage_history": [
      {
        "id": "7e5511f7-de7e-4e65-ab6b-560b15b9621c",
        "from_stage_id": "d5ec6cf1-bdaf-4345-9a10-db64efcf02b8",
        "from_stage_name": "Proposal",
        "to_stage_id": "86b96932-77b7-4915-983e-00d5da346f36",
        "to_stage_name": "Closed Won",
        "changed_by": "6d491050-3918-40c1-a09e-f4f6145278e6",
        "changed_at": "2026-10-21T11:58:05.502915Z"
      }
    ]
  },
  "meta": null
}
```
Unknown stage id (`deal_move_unknown_stage.json`, `422`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Stage not found.",
    "field_errors": [
      {
        "field": "stage_id",
        "message": "Stage not found."
      }
    ]
  }
}
```

Notes: moving a deal to the stage it is already in is a no-op that returns the deal unchanged (no history row,
no notification). `stage_history` is oldest first; the first row has `from_stage_id: null`. Every move is also
counted as activity for the stall clock (section 8). A won/lost deal can be dragged back out at any time.

A deal that was reopened and won again on the same day (the e2e does exactly that) still produced exactly one
"won" notification (section 8.2). Full history after lost, reopen, won, reopen, won is in `deal_detail_with_history.json`.

### 4.5 Delete, `DELETE /sales/deals/{id}`

`200` `{"data": {"deleted": true}, "meta": null}` (`deal_deleted.json`); a second GET is the `404` below:
```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```

---

## 5. Leads, CSV import, "Make it a deal"

Lead fields: `name`, `company`, `email`, `source` (`manual`, `csv`, `referral`, `inbound`, `event`, `other`),
`status` (`new`, `working`, `qualified`, `disqualified`, `converted`), `owner_id`, plus the read-only
`converted_deal_id`, `converted_contact_id`, `converted_account_id`.

### 5.1 Create, `POST /sales/leads`

At least one of `name` or `company` is required. Created (`lead_created.json`):
```json
{
  "data": {
    "id": "2de2187e-63d9-4c0d-b1ed-838ee910fbe3",
    "name": "Chidi Eze",
    "company": "Zenith Foods",
    "email": "chidi@zenith.example",
    "source": "referral",
    "status": "new",
    "owner_id": null,
    "converted_deal_id": null,
    "converted_contact_id": null,
    "converted_account_id": null,
    "created_at": "2026-10-21T11:58:05.245348Z"
  },
  "meta": null
}
```
Neither name nor company (`lead_error_no_identity.json`, `422`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Add a name or a company.",
    "field_errors": [
      {
        "field": "name",
        "message": "Add a name or a company."
      }
    ]
  }
}
```
`status: "converted"` cannot be set by hand (`lead_error_status_converted.json`, `422`, generic wording):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "status",
        "message": "Value error, Use convert to mark a lead as converted."
      }
    ]
  }
}
```

List: `GET /sales/leads?status=&source=&owner_id=&q=&limit=&offset=`. Live-verified with `source=csv`
(`leads_list_filtered.json`, items trimmed to one):
```json
{
  "data": {
    "items": [
      {
        "id": "6ec51075-4066-4c33-8db9-0526fd69394a",
        "name": "Kemi Bello",
        "company": "Bello Print",
        "email": "kemi@bello.example",
        "source": "csv",
        "status": "new",
        "owner_id": null,
        "converted_deal_id": null,
        "converted_contact_id": null,
        "converted_account_id": null,
        "created_at": "2026-10-21T11:58:05.296179Z"
      }
    ],
    "total": 2,
    "limit": 50,
    "offset": 0
  },
  "meta": null
}
```
`PATCH /sales/leads/{id}` and `DELETE /sales/leads/{id}` exist (unit-tested, **not exercised live**). A converted lead
cannot be edited or deleted: the service answers `422` with `This lead was already converted.`

### 5.2 CSV import (two steps)

1. `POST /sales/leads/import/preview`, `multipart/form-data`, field `file` (a `.csv`, UTF-8, BOM allowed,
   2 MB and 5,000 data rows max; the first row is the header). Nothing is saved yet. The parsed file is held for **30 minutes**.
2. `POST /sales/leads/import` with `{"upload_id": "...", "mapping": {...}}`. This saves.

The file used in the e2e has five rows: two good, one duplicate of an existing lead's e-mail (different case),
one bad e-mail, one row with neither name nor company.

Preview (`import_preview.json`). `suggested_mapping` maps lead fields to the file's own header text, or `null`
when no header matched; `sample_rows` is at most the first 5 rows; `row_count` is all data rows:
```json
{
  "data": {
    "upload_id": "27b641a23ced46e2aafbbbecf69d2c58",
    "headers": [
      "Full Name",
      "Company",
      "E-mail",
      "Source"
    ],
    "sample_rows": [
      [
        "Kemi Bello",
        "Bello Print",
        "kemi@bello.example",
        "event"
      ],
      [
        "Femi Ade",
        "Ade Farms",
        "femi@ade.example",
        "inbound"
      ],
      [
        "Chidi Again",
        "Zenith Foods",
        "CHIDI@zenith.example",
        "manual"
      ],
      [
        "Bad Email",
        "Broken Ltd",
        "not-an-email",
        "manual"
      ],
      [
        "",
        "",
        "orphan@nowhere.example",
        "manual"
      ]
    ],
    "suggested_mapping": {
      "name": "Full Name",
      "company": "Company",
      "email": "E-mail",
      "source": "Source"
    },
    "row_count": 5
  },
  "meta": null
}
```
Unreadable file (`import_preview_unreadable.json`, `422`; bytes that are not UTF-8):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "We could not read that CSV file.",
    "field_errors": []
  }
}
```

Commit, with the suggested mapping posted back (the user can change any column in the mapping UI). A mapping
needs at least `name` or `company`. Result (`import_result.json`):
```json
{
  "data": {
    "added": 2,
    "duplicates_skipped": 1,
    "invalid_rows": [
      {
        "row": 4,
        "error": "Email is not valid."
      },
      {
        "row": 5,
        "error": "A name or company is required."
      }
    ],
    "invalid_total": 2
  },
  "meta": null
}
```

- `added` leads saved; `duplicates_skipped` matched an existing lead or contact e-mail (or an earlier row in the
  same file), case-insensitively; `invalid_rows` lists up to 50 `{row, error}` (`row` is 1-based over **data**
  rows, the header is not counted); `invalid_total` is the full count even when the list is capped.
  Row error strings seen live: `Email is not valid.`, `A name or company is required.` (the service also has
  `A value is too long.`, not exercised live). Cell text is never echoed back.
- **Trap: the `source` mapping is accepted but every imported lead gets `source: "csv"`** whatever the file's
  source column says (see the `source: "csv"` rows in `leads_list_filtered.json`: the file said `event` and `inbound`).
  Do not promise users that a source column is imported.
- Posting the same `upload_id` twice: the first call consumes it, the second is `404` (`import_error_upload_used.json`).
  The same `404` appears when the 30 minutes ran out or the id belongs to another workspace. Treat it as
  "upload expired, choose the file again".
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```
The 2 MB (`413 PAYLOAD_TOO_LARGE`, `CSV must be 2 MB or smaller.`) and 5,000-row (`422`, `CSV can have at most 5,000 rows.`)
limits and a mapping that names a column not in the file (`422`, `That column is not in the file.`) are unit-tested, **not exercised live**.

### 5.3 Make it a deal, `POST /sales/leads/{id}/convert`

No body. Creates (or reuses) an account, a contact and a deal in the **first stage**, owned by the person who
converted, and marks the lead `converted`. The account is matched by company name, case-insensitively, within the workspace.

Lead **with a company** (`lead_convert_with_company.json`): account, contact and deal all returned. The deal is
named after the company, value 0:
```json
{
  "data": {
    "deal": {
      "id": "1bdbf640-8f3a-4704-ad30-c10ac289581b",
      "name": "Zenith Foods",
      "account_id": "f990cd70-c326-4fc2-b2f8-07c3f527cf0c",
      "account_name": "Zenith Foods",
      "contact_id": "215b2111-b336-4399-92e9-d9ef5008b140",
      "contact_name": "Chidi Eze",
      "value_minor": 0,
      "currency": "NGN",
      "stage_id": "42bb4376-6516-42e8-afa4-2868b9d57582",
      "next_step": null,
      "next_step_due": null,
      "closed_reason": null,
      "closed_note": null,
      "closed_at": null,
      "owner_id": "6d491050-3918-40c1-a09e-f4f6145278e6",
      "last_activity_at": "2026-10-21T11:58:05.327975Z",
      "is_stalled": false,
      "days_inactive": 0,
      "created_at": "2026-10-21T11:58:05.320403Z"
    },
    "account": {
      "id": "f990cd70-c326-4fc2-b2f8-07c3f527cf0c",
      "name": "Zenith Foods",
      "website": null,
      "industry": null,
      "description": null,
      "owner_id": null,
      "created_at": "2026-10-21T11:58:05.320403Z"
    },
    "contact": {
      "id": "215b2111-b336-4399-92e9-d9ef5008b140",
      "account_id": "f990cd70-c326-4fc2-b2f8-07c3f527cf0c",
      "first_name": "Chidi",
      "last_name": "Eze",
      "email": "chidi@zenith.example",
      "phone": null,
      "title": null,
      "created_at": "2026-10-21T11:58:05.320403Z"
    }
  },
  "meta": null
}
```
Lead **without a company** (`lead_convert_no_company.json`): **`account` is `null` and `deal.account_id` is `null`**. The deal
is named after the contact. Do not assume an account exists after conversion:
```json
{
  "data": {
    "deal": {
      "id": "8ccd4b30-fb4c-4a27-ae5b-2b5fa9c5775b",
      "name": "Solo Prospect",
      "account_id": null,
      "account_name": null,
      "contact_id": "b986e1b6-014b-4b0f-b0f7-5c5ea9729efc",
      "contact_name": "Solo Prospect",
      "value_minor": 0,
      "currency": "NGN",
      "stage_id": "42bb4376-6516-42e8-afa4-2868b9d57582",
      "next_step": null,
      "next_step_due": null,
      "closed_reason": null,
      "closed_note": null,
      "closed_at": null,
      "owner_id": "6d491050-3918-40c1-a09e-f4f6145278e6",
      "last_activity_at": "2026-10-21T11:58:05.346170Z",
      "is_stalled": false,
      "days_inactive": 0,
      "created_at": "2026-10-21T11:58:05.341505Z"
    },
    "account": null,
    "contact": {
      "id": "b986e1b6-014b-4b0f-b0f7-5c5ea9729efc",
      "account_id": null,
      "first_name": "Solo",
      "last_name": "Prospect",
      "email": null,
      "phone": null,
      "title": null,
      "created_at": "2026-10-21T11:58:05.341505Z"
    }
  },
  "meta": null
}
```
The lead afterwards (`lead_after_convert.json`) is `converted` and links to all three:
```json
{
  "data": {
    "id": "2de2187e-63d9-4c0d-b1ed-838ee910fbe3",
    "name": "Chidi Eze",
    "company": "Zenith Foods",
    "email": "chidi@zenith.example",
    "source": "referral",
    "status": "converted",
    "owner_id": null,
    "converted_deal_id": "1bdbf640-8f3a-4704-ad30-c10ac289581b",
    "converted_contact_id": "215b2111-b336-4399-92e9-d9ef5008b140",
    "converted_account_id": "f990cd70-c326-4fc2-b2f8-07c3f527cf0c",
    "created_at": "2026-10-21T11:58:05.245348Z"
  },
  "meta": null
}
```
Converting twice (`lead_convert_again_error.json`, `422`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This lead was already converted.",
    "field_errors": [
      {
        "field": "status",
        "message": "This lead was already converted."
      }
    ]
  }
}
```

---

## 6. Accounts, account page, contacts

### 6.1 Accounts

Create (`account_created.json`). `website` is normalised: `acme.example` was stored as `https://acme.example`:
```json
{
  "data": {
    "id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
    "name": "Acme Logistics",
    "website": "https://acme.example",
    "industry": "Logistics",
    "description": null,
    "owner_id": null,
    "created_at": "2026-10-21T11:58:05.201333Z"
  },
  "meta": null
}
```
Only `http` and `https` websites are accepted (`account_error_website.json`, `422`, sent `javascript:alert(1)`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "website",
        "message": "Value error, Website must be an http or https address."
      }
    ]
  }
}
```
List `GET /sales/accounts?q=&owner_id=&limit=&offset=` (`accounts_list.json`, `q=acme`):
```json
{
  "data": {
    "items": [
      {
        "id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
        "name": "Acme Logistics",
        "website": "https://acme.example",
        "industry": "Logistics",
        "description": null,
        "owner_id": null,
        "created_at": "2026-10-21T11:58:05.201333Z"
      }
    ],
    "total": 1,
    "limit": 50,
    "offset": 0
  },
  "meta": null
}
```
`PATCH /sales/accounts/{id}` is unit-tested, **not exercised live**. `DELETE /sales/accounts/{id}` is **refused while the account
has deals** (`Remove or move its deals first.`, field `account_id`, from the service, not exercised live); on success its contacts and
converted leads are detached, not deleted.

### 6.2 Account page, `GET /sales/accounts/{id}`

One call for the whole page header: the account, its contacts, its **open** deals (with stall fields), and the
lifetime won value (`account_detail.json`). `lifetime_won_minor` is the sum of the account's deals currently in the won stage:
```json
{
  "data": {
    "account": {
      "id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
      "name": "Acme Logistics",
      "website": "https://acme.example",
      "industry": "Logistics",
      "description": null,
      "owner_id": null,
      "created_at": "2026-10-21T11:58:05.201333Z"
    },
    "contacts": [
      {
        "id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
        "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
        "first_name": "Tunde",
        "last_name": "Okafor",
        "email": "tunde@acme.example",
        "phone": null,
        "title": "Head of Ops",
        "created_at": "2026-10-21T11:58:05.220176Z"
      }
    ],
    "open_deals": [
      {
        "id": "6c62e89d-651d-4634-9c3e-3a12319b17be",
        "name": "Quiet renewal",
        "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
        "account_name": "Acme Logistics",
        "contact_id": null,
        "contact_name": null,
        "value_minor": 80000000,
        "currency": "NGN",
        "stage_id": "d4b9b58a-7bcd-48ec-8010-b9cd3961d642",
        "next_step": null,
        "next_step_due": null,
        "closed_reason": null,
        "closed_note": null,
        "closed_at": null,
        "owner_id": null,
        "last_activity_at": "2026-10-01T11:58:06.452292Z",
        "is_stalled": true,
        "days_inactive": 20,
        "created_at": "2026-10-21T11:58:05.389138Z"
      }
    ],
    "lifetime_won_minor": 300000000
  },
  "meta": null
}
```
Note `open_deals[]` items have the deal fields but **not** `stage_history`. The activity timeline is a separate call
(section 7).

### 6.3 Contacts

Create (`contact_created.json`); `account_id` is optional, `first_name` is required, `email` is validated:
```json
{
  "data": {
    "id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
    "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
    "first_name": "Tunde",
    "last_name": "Okafor",
    "email": "tunde@acme.example",
    "phone": null,
    "title": "Head of Ops",
    "created_at": "2026-10-21T11:58:05.220176Z"
  },
  "meta": null
}
```
List `GET /sales/contacts?account_id=&q=&limit=&offset=` (`contacts_list_by_account.json`, `account_id` filter):
```json
{
  "data": {
    "items": [
      {
        "id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
        "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
        "first_name": "Tunde",
        "last_name": "Okafor",
        "email": "tunde@acme.example",
        "phone": null,
        "title": "Head of Ops",
        "created_at": "2026-10-21T11:58:05.220176Z"
      }
    ],
    "total": 1,
    "limit": 50,
    "offset": 0
  },
  "meta": null
}
```
`GET`, `PATCH` and `DELETE /sales/contacts/{id}` are unit-tested, **not exercised live**. Deleting a contact clears `contact_id` on its deals.

---

## 7. Activity timeline

An activity is a call, e-mail, meeting or note (`kind`: `call`, `email`, `meeting`, `note`) attached to an
account, a contact, a deal, or a combination. It must have at least one of the three ids.

Create on a deal (`activity_created.json`; `occurred_at` defaults to now, `author_id` is the caller):
```json
{
  "data": {
    "id": "032749eb-a6b9-478f-ad2a-458a4d80efa5",
    "kind": "call",
    "body": "Intro call with Tunde",
    "occurred_at": "2026-10-21T11:58:05.628640Z",
    "author_id": "6d491050-3918-40c1-a09e-f4f6145278e6",
    "account_id": null,
    "contact_id": null,
    "deal_id": "6c62e89d-651d-4634-9c3e-3a12319b17be",
    "created_at": "2026-10-21T11:58:05.622902Z"
  },
  "meta": null
}
```
Create on an account and contact (`activity_created_on_account.json`):
```json
{
  "data": {
    "id": "87d5a240-6492-4cee-a17b-14b5a4d46d2b",
    "kind": "note",
    "body": "Account prefers WhatsApp",
    "occurred_at": "2026-10-21T11:58:05.649594Z",
    "author_id": "6d491050-3918-40c1-a09e-f4f6145278e6",
    "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
    "contact_id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
    "deal_id": null,
    "created_at": "2026-10-21T11:58:05.644856Z"
  },
  "meta": null
}
```
No link (`activity_error_no_link.json`, `422`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Link the activity to an account, contact or deal.",
    "field_errors": [
      {
        "field": "account_id",
        "message": "Link the activity to an account, contact or deal."
      }
    ]
  }
}
```
Edit, `PATCH /sales/activities/{id}` with `kind` / `body` / `occurred_at` (`activity_updated.json`; the links cannot be changed):
```json
{
  "data": {
    "id": "032749eb-a6b9-478f-ad2a-458a4d80efa5",
    "kind": "meeting",
    "body": "Intro meeting with Tunde",
    "occurred_at": "2026-10-21T11:58:05.628640Z",
    "author_id": "6d491050-3918-40c1-a09e-f4f6145278e6",
    "account_id": null,
    "contact_id": null,
    "deal_id": "6c62e89d-651d-4634-9c3e-3a12319b17be",
    "created_at": "2026-10-21T11:58:05.622902Z"
  },
  "meta": null
}
```

**List needs a filter.** `GET /sales/activities?account_id=` or `contact_id=` or `deal_id=` (any combination), newest
`occurred_at` first. Without one it is a 422 (`activities_list_error_no_filter.json`). The account timeline
(`activities_list_by_account.json`) shows activities whose `account_id` is that account; an activity logged only
on a deal does not appear there (it appeared under `deal_id`, `activities_list_by_deal.json`).
```json
{
  "data": {
    "items": [
      {
        "id": "87d5a240-6492-4cee-a17b-14b5a4d46d2b",
        "kind": "note",
        "body": "Account prefers WhatsApp",
        "occurred_at": "2026-10-21T11:58:05.649594Z",
        "author_id": "6d491050-3918-40c1-a09e-f4f6145278e6",
        "account_id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
        "contact_id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
        "deal_id": null,
        "created_at": "2026-10-21T11:58:05.644856Z"
      }
    ],
    "total": 1,
    "limit": 50,
    "offset": 0
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Filter by an account, contact or deal.",
    "field_errors": [
      {
        "field": "account_id",
        "message": "Filter by an account, contact or deal."
      }
    ]
  }
}
```
Delete (`activity_deleted.json`) and the repeat (`activity_delete_not_found.json`, `404`):
```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

An activity on a deal counts as deal activity (resets the stall clock, section 8). Logging an activity with a past
`occurred_at` never moves a deal's `last_activity_at` backwards (unit-tested).

---

## 8. Stalled deals and notifications

### 8.1 The stall badge

An **open** deal is stalled when it has had no activity for `stall_days` (workspace setting, default 14, 3 to 90).
"Activity" is: a logged activity on the deal, a stage move, or a next-step edit. Deals in the won and lost stages are never stalled.
Every deal object carries the computed fields; render the badge from them, do not recompute:

| Field | Meaning |
|---|---|
| `is_stalled` | `true` when quiet for at least `stall_days` and still in an open stage |
| `days_inactive` | whole days since `last_activity_at` |
| `last_activity_at` | the stall clock |

Before (`deal_before_stall.json`, trimmed to the top-level fields; `stage_history` omitted here):
```json
"last_activity_at": "2026-10-21T11:58:05.737573Z",
"is_stalled": false,
"days_inactive": 0
```
After the clock was pushed 20 days back with a direct database update (the e2e does this because no endpoint can),
with `stall_days` 7 (`deal_stalled.json`, trimmed the same way):
```json
"last_activity_at": "2026-10-01T11:58:06.452292Z",
"is_stalled": true,
"days_inactive": 20
```
The same flags appear on `open_deals[]` in the account page (`account_detail.json` above shows `is_stalled: true, days_inactive: 20`).

### 8.2 Notifications (the bell, `GET /api/v1/notifications`)

Two types, both delivered to founders and team members only (never mentors, consultants, investors, accountants, legal advisors).

**`sales.deal.won`**: title `Deal moved to Closed Won: <deal name> <party emoji>`, empty `body`. Sent when a deal enters the won stage,
to every founder and team member, the mover included. **At most one per deal per UTC day**: the e2e moved a
deal to won, reopened it, and won it again the same day, and the notification list held exactly one
(`notification_deal_won.json`). Do not expect a notification for every drag. Link with `data.href`
(`/sales?deal=<id>`: open the board with that deal's drawer):
```json
{
  "id": "fd3cc02d-d33b-4f9c-862b-b5c323e0fb1d",
  "type": "sales.deal.won",
  "title": "Deal moved to Closed Won: Acme fleet rollout 🎉",
  "body": "",
  "data": {
    "href": "/sales?deal=f9c69088-b134-4147-aa03-e424c2fd2320",
    "name": "Acme fleet rollout",
    "deal_id": "f9c69088-b134-4147-aa03-e424c2fd2320",
    "actor_id": "6d491050-3918-40c1-a09e-f4f6145278e6",
    "startup_id": "d8975b05-ca24-43da-bf4a-53171e492dbb",
    "to_stage_id": "86b96932-77b7-4915-983e-00d5da346f36",
    "from_stage_id": "d5ec6cf1-bdaf-4345-9a10-db64efcf02b8"
  },
  "read": false,
  "created_at": "2026-10-21T11:58:05.499756+00:00"
}
```

**`sales.deal.stalled`**: sent once per stall episode. The scheduler picks stalled deals up on its regular pass; the e2e ran one
scheduler tick plus the worker and got exactly one notification, and a second tick produced no second one. Recipient is the
deal's owner when the owner is still a founder or team member, otherwise every founder (in the capture the deal had no
owner, so the founder got it, `owner_id` is `null`). `data.href` ends in `&followup=1`: open the deal drawer and
offer a follow-up action. New activity on the deal starts a fresh episode, so it can alert again later.
```json
{
  "id": "c3ecf76c-2c26-4bca-b7d4-cae91e1a661d",
  "type": "sales.deal.stalled",
  "title": "Deal stalled: Quiet renewal has had no activity for 20 days",
  "body": "Suggest a follow-up",
  "data": {
    "days": 20,
    "href": "/sales?deal=6c62e89d-651d-4634-9c3e-3a12319b17be&followup=1",
    "name": "Quiet renewal",
    "deal_id": "6c62e89d-651d-4634-9c3e-3a12319b17be",
    "owner_id": null,
    "startup_id": "d8975b05-ca24-43da-bf4a-53171e492dbb"
  },
  "read": false,
  "created_at": "2026-10-21T11:58:07.352884+00:00"
}
```

**UX instructions:** show the stall badge on the card and in the drawer; make the notification click open
`data.href`; word the stalled alert as a nudge ("no activity for N days"), never as an error; do not show a toast
per drag to Closed Won, the server already dedupes it to one per day.

---

## 9. Error shapes you must handle

| Case | Status | Body (verbatim from captures) |
|---|---|---|
| Business-rule validation | 422 | `{"error": {"code": "VALIDATION_ERROR", "message": "<specific text>", "field_errors": [{"field": "<name>", "message": "<same text>"}]}}` |
| Pydantic validation (range, type, format) | 422 | `{"error": {"code": "VALIDATION_ERROR", "message": "Please check the highlighted fields.", "field_errors": [{"field": "...", "message": "..."}]}}` |
| Unreadable CSV | 422 | `{"error": {"code": "VALIDATION_ERROR", "message": "We could not read that CSV file.", "field_errors": []}}` |
| Unknown / foreign id | 404 | `{"error": {"code": "NOT_FOUND", "message": "Not found.", "field_errors": []}}` |
| Wrong role | 403 | `{"error": {"code": "FORBIDDEN", "message": "You don't have permission to do that.", "field_errors": []}}` |

The 404 (`deal_get_not_found.json`, after the deal was deleted):
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

All 422 strings seen live, by action: `Pick a reason for losing this deal.`, `Stage not found.`, `Use the stage endpoint.`,
`Move the deals out of <stage> first.`, `A pipeline needs between 1 and 10 stages.`, `The pipeline needs exactly one Won stage.`,
`Add a name or a company.`, `This lead was already converted.`, `Link the activity to an account, contact or deal.`,
`Filter by an account, contact or deal.`, `We could not read that CSV file.`, `Website must be an http or https address.`
(the last arrives as `Value error, Website must be an http or https address.`).

---

## 10. Dashboard tile, search, activity feed

**Dashboard pipeline tile.** `GET /api/v1/dashboard/summary` -> `kpis.metrics.pipeline` is now real. It is the same number as
`total_pipeline_minor` (open-stage deal value). `dashboard_kpi_pipeline.json`:
```json
{
  "status": "ok",
  "value_minor": 80000000,
  "currency": "NGN",
  "trend": {
    "delta": null,
    "series": [
      {
        "period": "2026-10-21",
        "value": 80000000
      }
    ]
  }
}
```
States: `ok` (as above), `no_data` when the workspace has no deals at all (not exercised live in this journey; the
empty state is covered by unit tests), `restricted` for roles outside founder / team member (live, section 11).
`trend.delta` was `null` in this capture, which has a single snapshot point. Value is kobo, currency `NGN`.

**Search.** `GET /api/v1/search?q=acme` returns deals by name, accounts by name, and contacts by name or e-mail, group `"Sales"`
(`search_acme.json`). Open the `href` as given. Contacts link to `/sales/contacts?contact=<id>`, which is a query-string
deep link, not a page; the FE must open the contact drawer for that id. Sales pages (Sales Hub, Leads, Accounts) also appear
as page hits, and leads are searchable by name, company or e-mail (type `sales_lead`, `href` `/sales/leads?lead=<id>`, also a
query-string deep link). Neither is captured: **not exercised live**, unit-tested.
```json
{
  "data": {
    "query": "Acme",
    "results": [
      {
        "type": "deal",
        "group": "Sales",
        "id": "f9c69088-b134-4147-aa03-e424c2fd2320",
        "title": "Acme fleet rollout",
        "subtitle": "Deal",
        "href": "/sales?deal=f9c69088-b134-4147-aa03-e424c2fd2320"
      },
      {
        "type": "sales_account",
        "group": "Sales",
        "id": "6a5e9104-94ee-4f8e-a78e-25bc7bce5292",
        "title": "Acme Logistics",
        "subtitle": "Account · Logistics",
        "href": "/sales/accounts/6a5e9104-94ee-4f8e-a78e-25bc7bce5292"
      },
      {
        "type": "sales_contact",
        "group": "Sales",
        "id": "a464ba09-10f9-4e0f-afc1-25e9ca7518b2",
        "title": "Tunde Okafor",
        "subtitle": "Contact · tunde@acme.example",
        "href": "/sales/contacts?contact=a464ba09-10f9-4e0f-afc1-25e9ca7518b2"
      }
    ]
  },
  "meta": null
}
```

**Activity feed.** `GET /api/v1/dashboard/activity` (`dashboard_activity_feed.json`, items trimmed to the first 2 of 20) has Sales
rows with `action` values `sales.activity.created`, `sales.activity.updated`, `sales.activity.deleted`, `sales.deal.created`,
`sales.deal.updated`, `sales.deal.stage_changed`, `sales.deal.won`, `sales.deal.lost` (all seen in the capture) and further
`sales.*` actions that the unit tests cover (account, contact, lead, stage and settings changes, imports). The summary text for activities
is fixed server copy: **`Logged an email`** is what the server writes for an e-mail activity; expect that wording.
```json
{
  "data": {
    "items": [
      {
        "id": "b443e786-7995-41eb-8356-825c159cfe9e",
        "action": "sales.activity.created",
        "entity_type": "sales_activity",
        "entity_id": "bb5265c7-3a8d-4fdf-917f-2d690c9b2d86",
        "summary": "Logged an email",
        "meta": null,
        "actor": {
          "id": "6d491050-3918-40c1-a09e-f4f6145278e6",
          "name": "Ada Founder"
        },
        "created_at": "2026-10-21T11:58:05.733888+00:00"
      },
      {
        "id": "bf67fa8b-1e1c-440e-aa8a-1985c9a0e0d2",
        "action": "sales.activity.deleted",
        "entity_type": "sales_activity",
        "entity_id": "032749eb-a6b9-478f-ad2a-458a4d80efa5",
        "summary": "Deleted a meeting",
        "meta": null,
        "actor": {
          "id": "6d491050-3918-40c1-a09e-f4f6145278e6",
          "name": "Ada Founder"
        },
        "created_at": "2026-10-21T11:58:05.710829+00:00"
      }
    ],
    "next_cursor": "MjAyNi0xMC0yMVQxMTo1ODowNS4yNTY3NDUrMDA6MDB8NThmMGVmN2MtMzRiNC00NzA4LWFhZGYtMjQ0MDMxYzdmMjlm"
  },
  "meta": null
}
```
Mentors, consultants and the other non-sales roles do not see these rows (unit-tested; the live mentor check in section 11 covers the
Dashboard tile and search, not the feed).

---

## 11. Roles: what a mentor gets (verified live)

The e2e invited a **mentor** into the workspace. Every Sales route is `403` for them. `GET /sales`
(`mentor_overview_403.json`):
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
A write (`mentor_create_deal_403.json`, `POST /sales/deals`): same body and status:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
The same mentor searching for "Acme" gets no Sales hits (`mentor_search_acme.json`):
```json
{
  "data": {
    "query": "Acme",
    "results": []
  },
  "meta": null
}
```
and their Dashboard pipeline tile is `restricted`, with no money (`mentor_kpi_pipeline.json`):
```json
{
  "status": "restricted",
  "value_minor": null,
  "currency": null
}
```

The business consultant, investor, accountant and legal advisor roles are gated by the same dependency and are
asserted `403` on every route by `tests/api/sales/test_permissions.py`; they were **not** invited in the live journey.

---

## 12. Field-nesting traps and UX checklist

- **`stage_history` only on single-deal reads and stage moves.** List items, `PATCH`, create, the account page's `open_deals[]` and
  `ConvertOut.deal` do not have it.
- **`stage_id` vs `id`:** overview rows use `stage_id`, the stage list uses `id`.
- **Convert response nests three objects** (`deal`, `account`, `contact`) and `account` can be `null` (no company on the lead).
- **Lists are `data.items`, stages are `data` (array), activity feed is `data.items` with `next_cursor`** (a cursor, not offset).
- **`closed_reason` / `closed_note` / `closed_at` are `null` on open deals and won deals** (won has `closed_at` only); only lost deals carry a reason.
- **Reopen clears the closed fields.** After dragging out of Won or Lost, discard any cached closed data.
- **CSV import ignores the `source` column**; every imported lead is `source: "csv"`.
- **Lost needs a reason; ask first.** Open the reason picker on drop into the Lost column, then send both fields in one call.
- **Won notifications are deduped per deal per day.** No notification on a same-day re-win is correct.
- **Stage delete only when empty**, soft delete; 10 stages max; one Won and one Lost always.
- **Owner can be `null`.** Deals and leads created through the API without an owner have `owner_id: null`; converted deals get the converter.
- **Dates:** `next_step_due` is a plain date (`YYYY-MM-DD`); everything else is an ISO timestamp in UTC.
- **403 for mentors and every non-sales role**: hide Sales navigation; do not rely on the 403 for UX.

---

## 13. Verification table

| Behaviour | Status |
|---|---|
| Lazy stage seed on first call, overview numbers, weighted forecast, `win_rate` null then set | **verified live** (sections 1, 2) |
| Stage replace: rename, insert, order, response shape | **verified live** (2.1) |
| Stage errors: more than 10, no Won stage, remove stage with deals | **verified live** (2.2) |
| Remove an empty stage (soft delete) | **verified live** (2.2) |
| Stage kind change, duplicate names, remove Won/Lost, unknown id | unit tests only |
| Settings get / patch / `stall_days` range | **verified live** (3) |
| `outreach_postal_address` round trip | unit tests only |
| Deal create (default stage, chosen stage), negative value, get, patch, patch-with-stage 422 | **verified live** (4) |
| Deal list: `open_only`, `limit`; other filters (`stage_id`, `account_id`, `owner_id`, `q`) | `open_only`+`limit` **verified live**; others unit tests only |
| Stage move: lost without reason 422, lost with reason, reopen clears fields, won, unknown stage | **verified live** (4.4) |
| Same-stage move no-op | unit tests only |
| Won notification once per day (reopen + re-win same day) | **verified live** (8.2); the "next UTC day notifies again" case is unit only |
| Deal delete, then 404 | **verified live** (4.5, 9) |
| Lead create, 422s, list by `source` | **verified live** (5.1) |
| Lead PATCH / DELETE / converted-lead guard | unit tests only |
| CSV preview, commit with duplicate + invalid rows, upload reuse 404, unreadable file 422 | **verified live** (5.2) |
| CSV 2 MB, 5,000 rows, bad mapping column, BOM, CRLF, quoted commas, expiry | unit tests only |
| Convert with company, without company, twice 422 | **verified live** (5.3) |
| Account create / list / website 422 / account page | **verified live** (6) |
| Account PATCH / DELETE (refused with deals), find-or-create account on convert | unit tests only |
| Contact create / list by account | **verified live** (6.3) |
| Contact GET / PATCH / DELETE | unit tests only |
| Activity create (deal, account+contact), no-link 422, patch, list by account and by deal, no-filter 422, delete, repeat delete 404 | **verified live** (7) |
| Activity with past `occurred_at` does not move the stall clock back | unit tests only |
| Stall flags, scheduler tick + worker, one stalled notification, second tick sends nothing | **verified live** (8) |
| Stalled recipient is the owner when set, new activity opens a new episode, concurrent workers notify once | unit tests only |
| Stall clock was moved by a direct database update | **not an API path**: the e2e writes `deals.last_activity_at` directly, as no endpoint can age a deal |
| Dashboard `pipeline` tile `ok` and mentor `restricted` | **verified live** (10, 11); `no_data` state: unit tests only |
| Search hits for deal, account, contact; mentor gets none | **verified live** (10, 11) |
| Search page hits (Sales Hub, Leads, Accounts) | unit tests only |
| Activity feed contains Sales rows | **verified live** (10) |
| Activity feed hides Sales rows from non-sales roles | unit tests only |
| Mentor 403 on read and write | **verified live** (11) |
| 403 for consultant, investor, accountant, legal advisor on every route | unit tests only (`tests/api/sales/test_permissions.py`) |
| Cross-workspace ids in bodies (account, contact, stage, owner) rejected | unit tests only |
| Real e-mail sending, sequences, coach, analytics | **not built** (slices B, C) |
