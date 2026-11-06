# FE Integration Guide: Funding Hub (Module 14, slice C: the cap table)

Every JSON body below is pasted **verbatim** from live captures taken by `e2e/test_funding_cap_table.py`
against a real server (`e2e/_captures/funding_cap_table/*.json`; the capture name is given above each
block). Nothing is retyped from the schema. IDs, names and emails are throwaway values from that run.
Where a body was trimmed it says so. **What is not covered live is marked inline and listed in the
verification table at the end.**

Scope: the **Cap table** page (`/funding/cap-table`): ledger, add/edit holder and entry drawers, "Model a
round", scenarios, change history. Pipeline and Data room are in their own guides. Funding stays hidden from
the sidebar until all slices ship. Design: `cofoundaz/docs/superpowers/specs/2026-11-05-funding-hub-design.md`
section 6. Calls: D-FUND-4 (b) ledger + SAFEs and notes converted by the round model, deterministic, no AI;
D-FUND-5 (a) **founder only**; D-FUND-7 one currency per round.

Base path `/api/v1/funding/cap-table`. Every route needs `Authorization: Bearer <token>` and
`X-Workspace-Id`. Success bodies are `{"data": ..., "meta": null}`; errors are
`{"error": {"code", "message", "field_errors"}}`.

## 1. Who can call what

| Role | Every route in this guide |
|---|---|
| founder | allowed |
| team_member, mentor, business_consultant, accountant, legal_advisor, investor | **403** |

**Team members get 403 too** (the rest of Funding is open to them). Hide the Cap table tab for anyone who is
not the founder, and do not link to it from shared places. A data room share never exposes the cap table.
Plan module `funding` (Scale): after the role check, **402** when `PLAN_ENFORCEMENT` is on and the workspace
lacks it (off today). *The 403s and the 402 are not captured live (the journey has one user and enforcement
is off); both are covered by `tests/api/test_funding_cap_table_api.py`.*

## 2. Units and conventions (read this before building any number)

- **Money** is an integer in **minor units** (kobo/cents) of the round currency (`currency` in every
  response: `NGN` or `USD`, one per round). Never floats. The round currency cannot change once the cap
  table has an amount or share price (409 `CAP_TABLE_HAS_AMOUNTS`); that includes creating the first round,
  or the next one after closing, in another currency than the cap table currently shows.
- **Shares** are whole numbers. **Percentages are basis points** (`pct_bps`, 100 = 1%) shared out so a
  column always sums to exactly 10 000. Show `bps / 100` with two decimals.
- **A SAFE or note has no shares** until a round converts it. The ledger lists it as an amount with its cap
  and discount; only `POST /model` converts it. Never show a SAFE as a percentage in the donut.
- **Price per share** is a string with 6 decimals, in minor units per share (for example `"4.000000"`).
- **Rounding rules** (so you can explain a number): SAFE/note shares and new-investor shares round **down**;
  the option-pool top-up rounds **up**; note interest rounds half-even to a minor unit; vested shares round
  down; shares per holder come from whole numbers, percentages from the largest-remainder rule.
- The page must carry the disclaimer from the API (`disclaimer`): estimates for planning, not legal or tax
  advice.

## 3. Ledger

`GET /cap-table?fully_diluted=true&as_of=YYYY-MM-DD` (both optional; `fully_diluted` defaults to **true**,
`as_of` to today UTC). Empty state first, not an error: show "Add your first holder".

capture `ledger_empty`
```json
{
  "data": {
    "currency": "NGN",
    "fully_diluted": true,
    "as_of": "2026-11-06",
    "empty": true,
    "totals": {
      "issued_shares": 0,
      "granted_option_shares": 0,
      "unallocated_pool_shares": 0,
      "fully_diluted_shares": 0,
      "denominator_shares": 0,
      "convertible_amount_minor": 0
    },
    "holders": [],
    "disclaimer": "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed documents. Check them with a lawyer before you rely on these numbers."
  },
  "meta": null
}
```

Fully diluted (issued stock + granted options + the unallocated pool in the denominator). Trimmed to the
first three holders:

capture `ledger_fully_diluted`
```json
{
  "data": {
    "currency": "NGN",
    "fully_diluted": true,
    "as_of": "2026-06-01",
    "empty": false,
    "totals": {
      "issued_shares": 8000000,
      "granted_option_shares": 480000,
      "unallocated_pool_shares": 1000000,
      "fully_diluted_shares": 9480000,
      "denominator_shares": 9480000,
      "convertible_amount_minor": 2310000
    },
    "holders": [
      {
        "id": "cc9fa021-46ab-4f8b-ac8b-f39e412f6d50",
        "name": "Ada Obi",
        "kind": "founder",
        "email": null,
        "notes": null,
        "shares": 8000000,
        "pct_bps": 8439,
        "entries": [
          {
            "id": "c8d35898-2287-4886-ae18-35678f62f9e3",
            "holder_id": "cc9fa021-46ab-4f8b-ac8b-f39e412f6d50",
            "security": "common",
            "shares": 8000000,
            "price_minor": null,
            "amount_minor": null,
            "valuation_cap_minor": null,
            "discount_bps": null,
            "safe_kind": null,
            "mfn": false,
            "interest_bps": null,
            "issued_on": "2025-01-15",
            "vesting_cliff_months": null,
            "vesting_months": null,
            "vesting_start_on": null,
            "notes": null,
            "vested_shares": 8000000
          }
        ]
      },
      {
        "id": "bde5e177-9c7b-4e38-a328-f4a4fc8f0f69",
        "name": "Option pool",
        "kind": "pool",
        "email": null,
        "notes": null,
        "shares": 1000000,
        "pct_bps": 1055,
        "entries": [
          {
            "id": "5a52d8c4-9591-4a1b-931f-4b9f77dfac39",
            "holder_id": "bde5e177-9c7b-4e38-a328-f4a4fc8f0f69",
            "security": "option",
            "shares": 1000000,
            "price_minor": null,
            "amount_minor": null,
            "valuation_cap_minor": null,
            "discount_bps": null,
            "safe_kind": null,
            "mfn": false,
            "interest_bps": null,
            "issued_on": null,
            "vesting_cliff_months": null,
            "vesting_months": null,
            "vesting_start_on": null,
            "notes": null,
            "vested_shares": 1000000
          }
        ]
      },
      {
        "id": "54f82d59-7a3f-40d6-bd12-7e6a9fecaae7",
        "name": "Eve Placeholder",
        "kind": "employee",
        "email": "eve@example.com",
        "notes": null,
        "shares": 480000,
        "pct_bps": 506,
        "entries": [
          {
            "id": "d6a95418-3ebc-4a2e-ba4c-00f4482ab972",
            "holder_id": "54f82d59-7a3f-40d6-bd12-7e6a9fecaae7",
            "security": "option",
            "shares": 480000,
            "price_minor": 10,
            "amount_minor": null,
            "valuation_cap_minor": null,
            "discount_bps": null,
            "safe_kind": null,
            "mfn": false,
            "interest_bps": null,
            "issued_on": "2025-03-01",
            "vesting_cliff_months": 12,
            "vesting_months": 48,
            "vesting_start_on": "2025-03-01",
            "notes": null,
            "vested_shares": 150000
          }
        ]
      },
      {
        "...": "more holders"
      }
    ],
    "disclaimer": "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed documents. Check them with a lawyer before you rely on these numbers."
  },
  "meta": null
}
```

**Traps**

- With `fully_diluted=false` the denominator is issued stock only: option and pool holders have
  `pct_bps: null` (not 0), and SAFE/note holders always have `pct_bps: null` and `shares: 0`. Trimmed:

capture `ledger_basic`
```json
{
  "data": {
    "currency": "NGN",
    "fully_diluted": false,
    "as_of": "2026-06-01",
    "empty": false,
    "totals": {
      "issued_shares": 8000000,
      "granted_option_shares": 480000,
      "unallocated_pool_shares": 1000000,
      "fully_diluted_shares": 9480000,
      "denominator_shares": 8000000,
      "convertible_amount_minor": 2310000
    },
    "holders": [
      {
        "id": "cc9fa021-46ab-4f8b-ac8b-f39e412f6d50",
        "name": "Ada Obi",
        "kind": "founder",
        "email": null,
        "notes": null,
        "shares": 8000000,
        "pct_bps": 10000,
        "entries": [
          {
            "id": "c8d35898-2287-4886-ae18-35678f62f9e3",
            "holder_id": "cc9fa021-46ab-4f8b-ac8b-f39e412f6d50",
            "security": "common",
            "shares": 8000000,
            "price_minor": null,
            "amount_minor": null,
            "valuation_cap_minor": null,
            "discount_bps": null,
            "safe_kind": null,
            "mfn": false,
            "interest_bps": null,
            "issued_on": "2025-01-15",
            "vesting_cliff_months": null,
            "vesting_months": null,
            "vesting_start_on": null,
            "notes": null,
            "vested_shares": 8000000
          }
        ]
      },
      {
        "id": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
        "name": "Angel One",
        "kind": "investor",
        "email": null,
        "notes": null,
        "shares": 0,
        "pct_bps": null,
        "entries": [
          {
            "id": "937bba08-87a6-4a54-ade9-6526df522593",
            "holder_id": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
            "security": "safe",
            "shares": null,
            "price_minor": null,
            "amount_minor": 1000000,
            "valuation_cap_minor": 10000000,
            "discount_bps": null,
            "safe_kind": "post_money",
            "mfn": false,
            "interest_bps": null,
            "issued_on": "2025-09-01",
            "vesting_cliff_months": null,
            "vesting_months": null,
            "vesting_start_on": null,
            "notes": null,
            "converting_amount_minor": 1000000
          },
          {
            "id": "e7f89504-cdbf-4361-8afa-3155c9b605e2",
            "holder_id": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
            "security": "safe",
            "shares": null,
            "price_minor": null,
            "amount_minor": 500000,
            "valuation_cap_minor": null,
            "discount_bps": 2000,
            "safe_kind": "post_money",
            "mfn": false,
            "interest_bps": null,
            "issued_on": "2025-10-01",
            "vesting_cliff_months": null,
            "vesting_months": null,
            "vesting_start_on": null,
            "notes": null,
            "converting_amount_minor": 500000
          }
        ]
      },
      {
        "id": "54f82d59-7a3f-40d6-bd12-7e6a9fecaae7",
        "name": "Eve Placeholder",
        "kind": "employee",
        "email": "eve@example.com",
        "notes": null,
        "shares": 0,
        "pct_bps": null,
        "entries": [
          {
            "id": "d6a95418-3ebc-4a2e-ba4c-00f4482ab972",
            "holder_id": "54f82d59-7a3f-40d6-bd12-7e6a9fecaae7",
            "security": "option",
            "shares": 480000,
            "price_minor": 10,
            "amount_minor": null,
            "valuation_cap_minor": null,
            "discount_bps": null,
            "safe_kind": null,
            "mfn": false,
            "interest_bps": null,
            "issued_on": "2025-03-01",
            "vesting_cliff_months": 12,
            "vesting_months": 48,
            "vesting_start_on": "2025-03-01",
            "notes": null,
            "vested_shares": 150000
          }
        ]
      },
      {
        "...": "more holders"
      }
    ],
    "disclaimer": "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed documents. Check them with a lawyer before you rely on these numbers."
  },
  "meta": null
}
```

- `holders[].entries[]` for stock/options carries `vested_shares` (as of `as_of`); for a SAFE or note it
  carries `converting_amount_minor` instead (principal plus simple interest to `as_of`).
- `totals.convertible_amount_minor` is the sum of those converting amounts; it adds no shares.
- Several SAFEs for one person are separate entries under one holder.

## 4. Holders and entries

`POST /holders` `{name, kind, email?, notes?}`, `kind` is `founder`, `employee`, `advisor`, `investor` or
`pool` (the unallocated option pool; **one per workspace**, 409 `POOL_EXISTS`, and it can hold options only).
`PATCH /holders/{id}` takes `name`, `email`, `notes` (a holder's `name` cannot be cleared: explicit null is 422, capture `holder_name_cleared_422`). `DELETE /holders/{id}` removes the holder **and their
entries** (confirm in the UI); everything removed is in the change history.

capture `holder_created`
```json
{
  "data": {
    "id": "cc9fa021-46ab-4f8b-ac8b-f39e412f6d50",
    "name": "Ada Obi",
    "kind": "founder",
    "email": null,
    "notes": null
  },
  "meta": null
}
```

capture `holder_second_pool_409`
```json
{
  "error": {
    "code": "POOL_EXISTS",
    "message": "The option pool already exists. Add options to it.",
    "field_errors": []
  }
}
```

`POST /entries` `{holder_id, security, ...}`, `security` is `common`, `preferred`, `option`, `safe`,
`note`. The shape depends on the security (the API enforces it with 422, field named in `field_errors`):

| security | required | allowed | never |
|---|---|---|---|
| `common`, `preferred`, `option` | `shares` | `price_minor` (per share), `issued_on`, vesting (`vesting_start_on` + `vesting_months`, optional `vesting_cliff_months`, default 0), `notes` | amount, cap, discount, `safe_kind`, interest, `mfn` |
| `safe`, `note` | `amount_minor` | `valuation_cap_minor`, `discount_bps` (1 to 9999), `safe_kind` (default `post_money` for a SAFE and **`pre_money` for a note**, as note caps are conventionally pre-money; send it to override), `mfn`, `issued_on`, `notes`; `interest_bps` (**notes only; a note with interest needs `issued_on`**, else 422) | `shares`, `price_minor`, vesting |

capture `entry_common`
```json
{
  "data": {
    "id": "c8d35898-2287-4886-ae18-35678f62f9e3",
    "holder_id": "cc9fa021-46ab-4f8b-ac8b-f39e412f6d50",
    "security": "common",
    "shares": 8000000,
    "price_minor": null,
    "amount_minor": null,
    "valuation_cap_minor": null,
    "discount_bps": null,
    "safe_kind": null,
    "mfn": false,
    "interest_bps": null,
    "issued_on": "2025-01-15",
    "vesting_cliff_months": null,
    "vesting_months": null,
    "vesting_start_on": null,
    "notes": null
  },
  "meta": null
}
```

capture `entry_option_vesting`
```json
{
  "data": {
    "id": "d6a95418-3ebc-4a2e-ba4c-00f4482ab972",
    "holder_id": "54f82d59-7a3f-40d6-bd12-7e6a9fecaae7",
    "security": "option",
    "shares": 480000,
    "price_minor": 10,
    "amount_minor": null,
    "valuation_cap_minor": null,
    "discount_bps": null,
    "safe_kind": null,
    "mfn": false,
    "interest_bps": null,
    "issued_on": "2025-03-01",
    "vesting_cliff_months": 12,
    "vesting_months": 48,
    "vesting_start_on": "2025-03-01",
    "notes": null
  },
  "meta": null
}
```

capture `entry_safe_post_money`
```json
{
  "data": {
    "id": "937bba08-87a6-4a54-ade9-6526df522593",
    "holder_id": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
    "security": "safe",
    "shares": null,
    "price_minor": null,
    "amount_minor": 1000000,
    "valuation_cap_minor": 10000000,
    "discount_bps": null,
    "safe_kind": "post_money",
    "mfn": false,
    "interest_bps": null,
    "issued_on": "2025-09-01",
    "vesting_cliff_months": null,
    "vesting_months": null,
    "vesting_start_on": null,
    "notes": null
  },
  "meta": null
}
```

capture `entry_note`
```json
{
  "data": {
    "id": "21c51831-027a-4466-a431-d1b2c099e291",
    "holder_id": "b629b650-af2e-44bd-a9fa-ff22c1d7fb4e",
    "security": "note",
    "shares": null,
    "price_minor": null,
    "amount_minor": 750000,
    "valuation_cap_minor": 12000000,
    "discount_bps": null,
    "safe_kind": "pre_money",
    "mfn": false,
    "interest_bps": 800,
    "issued_on": "2025-06-01",
    "vesting_cliff_months": null,
    "vesting_months": null,
    "vesting_start_on": null,
    "notes": null
  },
  "meta": null
}
```

capture `entry_safe_with_shares_422`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "Value error, A SAFE or note has no shares or share price until it converts in a round."
      }
    ]
  }
}
```
capture `entry_note_interest_needs_date_422`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "Value error, A note with interest needs the date it was issued."
      }
    ]
  }
}
```
capture `holder_name_cleared_422`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "Value error, A holder needs a name."
      }
    ]
  }
}
```

capture `entry_on_pool_not_options_422`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The option pool holds options only.",
    "field_errors": [
      {
        "field": "security",
        "message": "The option pool holds options only."
      }
    ]
  }
}
```

`PATCH /entries/{id}` takes any field (and `holder_id` to move it). The merged result is validated as a
whole, so changing a field can fail on another. **Trap:** to clear an optional field send an explicit `null`
(leaving it out leaves it as is). `DELETE /entries/{id}` removes one entry.

capture `entry_updated`
```json
{
  "data": {
    "id": "937bba08-87a6-4a54-ade9-6526df522593",
    "holder_id": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
    "security": "safe",
    "shares": null,
    "price_minor": null,
    "amount_minor": 1000000,
    "valuation_cap_minor": 9000000,
    "discount_bps": null,
    "safe_kind": "post_money",
    "mfn": false,
    "interest_bps": null,
    "issued_on": "2025-09-01",
    "vesting_cliff_months": null,
    "vesting_months": null,
    "vesting_start_on": null,
    "notes": null
  },
  "meta": null
}
```

capture `entry_update_invalid_422`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "A SAFE or note has no shares or share price until it converts in a round.",
    "field_errors": [
      {
        "field": "security",
        "message": "A SAFE or note has no shares or share price until it converts in a round."
      }
    ]
  }
}
```

## 5. Model a round

`POST /model`. Nothing is saved. Body: `rounds` (1 to 5, applied in order), each
`{name, new_money_minor, pre_money_minor, pool_target_bps}`; optional `as_of`. `pool_target_bps` is the
**unallocated pool you want after the round, as a share of the post-round fully-diluted total** (1000 = 10%);
0 means do not top the pool up. Pre-money includes the SAFEs and the pool top-up (the usual term-sheet
convention), so both dilute the existing holders, not the new investors.

How the SAFEs and notes convert, to explain on the page: each converts at the **lowest** of its cap price,
its discount price and the round price. A **post-money** cap means the holder owns `amount / cap` of the
company before the round (SAFEs included); a **pre-money** cap prices on the company plus the option pool created in this round, without the converting
SAFEs. An `mfn` instrument converts on its own terms or on the terms of any one non-MFN
instrument issued on or after its own date (both dates needed), whichever gives the holder the most shares
in this round. Notes convert with simple interest to `as_of`.
Each conversion reports which term won (`term`: `cap`, `discount` or `round`).

capture `model_seed`
```json
{
  "data": {
    "currency": "NGN",
    "as_of": "2026-06-01",
    "headline": {
      "founders_before_bps": 8439,
      "founders_after_bps": 5339
    },
    "steps": [
      {
        "name": "Seed",
        "pre_money_minor": 40000000,
        "new_money_minor": 10000000,
        "post_money_minor": 50000000,
        "price_per_share": "3.336557",
        "pre_round_shares": 9480000,
        "pool_top_up_shares": 498551,
        "new_investor_shares": 2997101,
        "post_round_shares": 14985507,
        "conversions": [
          {
            "ref": "937bba08-87a6-4a54-ade9-6526df522593",
            "name": "Angel One",
            "security": "safe",
            "amount_minor": 1000000,
            "term": "cap",
            "conversion_price": "0.870333",
            "shares": 1148985
          },
          {
            "ref": "e7f89504-cdbf-4361-8afa-3155c9b605e2",
            "name": "Angel One",
            "security": "safe",
            "amount_minor": 500000,
            "term": "discount",
            "conversion_price": "2.669245",
            "shares": 187318
          },
          {
            "ref": "21c51831-027a-4466-a431-d1b2c099e291",
            "name": "Lagos Bank",
            "security": "note",
            "amount_minor": 810000,
            "term": "cap",
            "conversion_price": "1.202579",
            "shares": 673552
          }
        ],
        "founders_before_bps": 8439,
        "founders_after_bps": 5339
      }
    ],
    "dilution": [
      {
        "ref": "cc9fa021-46ab-4f8b-ac8b-f39e412f6d50",
        "name": "Ada Obi",
        "kind": "founder",
        "before_shares": 8000000,
        "before_bps": 8439,
        "after_shares": 8000000,
        "after_bps": 5339,
        "change_bps": -3100
      },
      {
        "ref": "round-1",
        "name": "Seed investors",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 2997101,
        "after_bps": 2000,
        "change_bps": 2000
      },
      {
        "ref": "bde5e177-9c7b-4e38-a328-f4a4fc8f0f69",
        "name": "Option pool",
        "kind": "pool",
        "before_shares": 1000000,
        "before_bps": 1055,
        "after_shares": 1498551,
        "after_bps": 1000,
        "change_bps": -55
      },
      {
        "ref": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
        "name": "Angel One",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 1336303,
        "after_bps": 892,
        "change_bps": 892
      },
      {
        "ref": "b629b650-af2e-44bd-a9fa-ff22c1d7fb4e",
        "name": "Lagos Bank",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 673552,
        "after_bps": 449,
        "change_bps": 449
      },
      {
        "ref": "54f82d59-7a3f-40d6-bd12-7e6a9fecaae7",
        "name": "Eve Placeholder",
        "kind": "employee",
        "before_shares": 480000,
        "before_bps": 506,
        "after_shares": 480000,
        "after_bps": 320,
        "change_bps": -186
      }
    ],
    "total_shares_before": 9480000,
    "total_shares_after": 14985507,
    "disclaimer": "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed documents. Check them with a lawyer before you rely on these numbers."
  },
  "meta": null
}
```

How to read it:

- `headline`: "You'd go from {founders_before_bps/100}% to {founders_after_bps/100}%" (the founders'
  combined fully-diluted stake, before the first round and after the last).
- `steps[]`: one per round. `pre_round_shares` is the fully-diluted share count going in;
  `pool_top_up_shares`, `new_investor_shares` and the SAFE `conversions[]` are what the round adds;
  `price_per_share` is the round price.
- `dilution[]`: the before/after table, **one row per holder** (converted SAFEs and notes land on the holder's
  own row, so a founder with stock and a SAFE is one line) plus one row per round's new investors. `ref` is
  the holder id (`round-N` for new investors); the entry id of a converted SAFE is `ref` in
  `steps[].conversions[]`. `before_*` is 0 for rows that only exist after the round. `after_bps` sums to
  10 000.
- Later rounds dilute everyone, including earlier investors; SAFEs convert in the first round only.

capture `model_seed_and_series_a`
```json
{
  "data": {
    "currency": "NGN",
    "as_of": "2026-06-01",
    "headline": {
      "founders_before_bps": 8439,
      "founders_after_bps": 4004
    },
    "steps": [
      {
        "name": "Seed",
        "pre_money_minor": 40000000,
        "new_money_minor": 10000000,
        "post_money_minor": 50000000,
        "price_per_share": "3.336557",
        "pre_round_shares": 9480000,
        "pool_top_up_shares": 498551,
        "new_investor_shares": 2997101,
        "post_round_shares": 14985507,
        "conversions": [
          {
            "ref": "937bba08-87a6-4a54-ade9-6526df522593",
            "name": "Angel One",
            "security": "safe",
            "amount_minor": 1000000,
            "term": "cap",
            "conversion_price": "0.870333",
            "shares": 1148985
          },
          {
            "ref": "e7f89504-cdbf-4361-8afa-3155c9b605e2",
            "name": "Angel One",
            "security": "safe",
            "amount_minor": 500000,
            "term": "discount",
            "conversion_price": "2.669245",
            "shares": 187318
          },
          {
            "ref": "21c51831-027a-4466-a431-d1b2c099e291",
            "name": "Lagos Bank",
            "security": "note",
            "amount_minor": 810000,
            "term": "cap",
            "conversion_price": "1.202579",
            "shares": 673552
          }
        ],
        "founders_before_bps": 8439,
        "founders_after_bps": 5339
      },
      {
        "name": "Series A",
        "pre_money_minor": 150000000,
        "new_money_minor": 50000000,
        "post_money_minor": 200000000,
        "price_per_share": "10.009671",
        "pre_round_shares": 14985507,
        "pool_top_up_shares": 0,
        "new_investor_shares": 4995169,
        "post_round_shares": 19980676,
        "conversions": [],
        "founders_before_bps": 5339,
        "founders_after_bps": 4004
      }
    ],
    "dilution": [
      {
        "ref": "cc9fa021-46ab-4f8b-ac8b-f39e412f6d50",
        "name": "Ada Obi",
        "kind": "founder",
        "before_shares": 8000000,
        "before_bps": 8439,
        "after_shares": 8000000,
        "after_bps": 4004,
        "change_bps": -4435
      },
      {
        "ref": "round-2",
        "name": "Series A investors",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 4995169,
        "after_bps": 2500,
        "change_bps": 2500
      },
      {
        "ref": "round-1",
        "name": "Seed investors",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 2997101,
        "after_bps": 1500,
        "change_bps": 1500
      },
      {
        "ref": "bde5e177-9c7b-4e38-a328-f4a4fc8f0f69",
        "name": "Option pool",
        "kind": "pool",
        "before_shares": 1000000,
        "before_bps": 1055,
        "after_shares": 1498551,
        "after_bps": 750,
        "change_bps": -305
      },
      {
        "ref": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
        "name": "Angel One",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 1336303,
        "after_bps": 669,
        "change_bps": 669
      },
      {
        "ref": "b629b650-af2e-44bd-a9fa-ff22c1d7fb4e",
        "name": "Lagos Bank",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 673552,
        "after_bps": 337,
        "change_bps": 337
      },
      {
        "ref": "54f82d59-7a3f-40d6-bd12-7e6a9fecaae7",
        "name": "Eve Placeholder",
        "kind": "employee",
        "before_shares": 480000,
        "before_bps": 506,
        "after_shares": 480000,
        "after_bps": 240,
        "change_bps": -266
      }
    ],
    "total_shares_before": 9480000,
    "total_shares_after": 19980676,
    "disclaimer": "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed documents. Check them with a lawyer before you rely on these numbers."
  },
  "meta": null
}
```

Refusals are 422 with a code the page can word: `EMPTY_CAP_TABLE` (add founder shares first),
`INVALID_ROUND`, `POOL_TARGET_TOO_HIGH` (the pool cannot exceed the existing holders' room),
`SAFE_CAPS_EXCEED` (post-money SAFEs own 100% or more), `CONVERSION_DID_NOT_SETTLE` (at these terms the SAFEs and
notes would own all of the company, for example a deep discount on an amount close to the pre-money). Only `EMPTY_CAP_TABLE` and `POOL_TARGET_TOO_HIGH` are captured live; the others are
unit-tested.

capture `model_empty_422`
```json
{
  "error": {
    "code": "EMPTY_CAP_TABLE",
    "message": "Add your founders' shares before modelling a round.",
    "field_errors": []
  }
}
```

capture `model_pool_too_high_422`
```json
{
  "error": {
    "code": "POOL_TARGET_TOO_HIGH",
    "message": "That option pool is bigger than the existing holders' share of the post-money.",
    "field_errors": []
  }
}
```

## 6. Scenarios

"Save as scenario" is `POST /scenarios` `{name, inputs}` where `inputs` is the same body as `/model`. The API
runs the model, refuses (422) one that cannot work, and stores **inputs and the result as of now**. A saved
scenario does not change when the cap table changes: re-run `/model` with its `inputs` to see it on today's
numbers. The list is lean (no `inputs` or `result`); open one for the full body. At most 100.

capture `scenario_created`
```json
{
  "data": {
    "id": "ad8b087e-2f78-4cce-811d-7aa2d8a353d8",
    "name": "Seed at 40M",
    "created_at": "2026-11-06T20:50:43.795017+00:00",
    "headline": {
      "founders_after_bps": 5339,
      "founders_before_bps": 8439
    },
    "inputs": {
      "as_of": "2026-06-01",
      "rounds": [
        {
          "name": "Seed",
          "new_money_minor": 10000000,
          "pool_target_bps": 1000,
          "pre_money_minor": 40000000
        }
      ]
    },
    "result": {
      "as_of": "2026-06-01",
      "steps": [
        {
          "name": "Seed",
          "conversions": [
            {
              "ref": "937bba08-87a6-4a54-ade9-6526df522593",
              "name": "Angel One",
              "term": "cap",
              "shares": 1148985,
              "security": "safe",
              "amount_minor": 1000000,
              "conversion_price": "0.870333"
            },
            {
              "ref": "e7f89504-cdbf-4361-8afa-3155c9b605e2",
              "name": "Angel One",
              "term": "discount",
              "shares": 187318,
              "security": "safe",
              "amount_minor": 500000,
              "conversion_price": "2.669245"
            },
            {
              "ref": "21c51831-027a-4466-a431-d1b2c099e291",
              "name": "Lagos Bank",
              "term": "cap",
              "shares": 673552,
              "security": "note",
              "amount_minor": 810000,
              "conversion_price": "1.202579"
            }
          ],
          "new_money_minor": 10000000,
          "pre_money_minor": 40000000,
          "price_per_share": "3.336557",
          "post_money_minor": 50000000,
          "pre_round_shares": 9480000,
          "post_round_shares": 14985507,
          "founders_after_bps": 5339,
          "pool_top_up_shares": 498551,
          "founders_before_bps": 8439,
          "new_investor_shares": 2997101
        }
      ],
      "currency": "NGN",
      "dilution": [
        {
          "ref": "cc9fa021-46ab-4f8b-ac8b-f39e412f6d50",
          "kind": "founder",
          "name": "Ada Obi",
          "after_bps": 5339,
          "before_bps": 8439,
          "change_bps": -3100,
          "after_shares": 8000000,
          "before_shares": 8000000
        },
        {
          "ref": "round-1",
          "kind": "investor",
          "name": "Seed investors",
          "after_bps": 2000,
          "before_bps": 0,
          "change_bps": 2000,
          "after_shares": 2997101,
          "before_shares": 0
        },
        {
          "ref": "bde5e177-9c7b-4e38-a328-f4a4fc8f0f69",
          "kind": "pool",
          "name": "Option pool",
          "after_bps": 1000,
          "before_bps": 1055,
          "change_bps": -55,
          "after_shares": 1498551,
          "before_shares": 1000000
        },
        {
          "ref": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
          "kind": "investor",
          "name": "Angel One",
          "after_bps": 892,
          "before_bps": 0,
          "change_bps": 892,
          "after_shares": 1336303,
          "before_shares": 0
        },
        {
          "ref": "b629b650-af2e-44bd-a9fa-ff22c1d7fb4e",
          "kind": "investor",
          "name": "Lagos Bank",
          "after_bps": 449,
          "before_bps": 0,
          "change_bps": 449,
          "after_shares": 673552,
          "before_shares": 0
        },
        {
          "ref": "54f82d59-7a3f-40d6-bd12-7e6a9fecaae7",
          "kind": "employee",
          "name": "Eve Placeholder",
          "after_bps": 320,
          "before_bps": 506,
          "change_bps": -186,
          "after_shares": 480000,
          "before_shares": 480000
        }
      ],
      "headline": {
        "founders_after_bps": 5339,
        "founders_before_bps": 8439
      },
      "disclaimer": "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed documents. Check them with a lawyer before you rely on these numbers.",
      "total_shares_after": 14985507,
      "total_shares_before": 9480000
    }
  },
  "meta": null
}
```

capture `scenarios_list`
```json
{
  "data": {
    "items": [
      {
        "id": "ad8b087e-2f78-4cce-811d-7aa2d8a353d8",
        "name": "Seed at 40M",
        "created_at": "2026-11-06T20:50:43.795017+00:00",
        "headline": {
          "founders_after_bps": 5339,
          "founders_before_bps": 8439
        }
      }
    ]
  },
  "meta": null
}
```

capture `scenario_renamed`
```json
{
  "data": {
    "id": "ad8b087e-2f78-4cce-811d-7aa2d8a353d8",
    "name": "Seed at 40M (base)",
    "created_at": "2026-11-06T20:50:43.795017+00:00",
    "headline": {
      "founders_after_bps": 5339,
      "founders_before_bps": 8439
    }
  },
  "meta": null
}
```

`GET /scenarios/{id}` returns the full saved body:

capture `scenario_open`
```json
{
  "data": {
    "id": "ad8b087e-2f78-4cce-811d-7aa2d8a353d8",
    "name": "Seed at 40M (base)",
    "created_at": "2026-11-06T20:50:43.795017+00:00",
    "headline": {
      "founders_after_bps": 5339,
      "founders_before_bps": 8439
    },
    "inputs": {
      "as_of": "2026-06-01",
      "rounds": [
        {
          "name": "Seed",
          "new_money_minor": 10000000,
          "pool_target_bps": 1000,
          "pre_money_minor": 40000000
        }
      ]
    },
    "result": {
      "as_of": "2026-06-01",
      "steps": [
        {
          "name": "Seed",
          "conversions": [
            {
              "ref": "937bba08-87a6-4a54-ade9-6526df522593",
              "name": "Angel One",
              "term": "cap",
              "shares": 1148985,
              "security": "safe",
              "amount_minor": 1000000,
              "conversion_price": "0.870333"
            },
            {
              "ref": "e7f89504-cdbf-4361-8afa-3155c9b605e2",
              "name": "Angel One",
              "term": "discount",
              "shares": 187318,
              "security": "safe",
              "amount_minor": 500000,
              "conversion_price": "2.669245"
            },
            {
              "ref": "21c51831-027a-4466-a431-d1b2c099e291",
              "name": "Lagos Bank",
              "term": "cap",
              "shares": 673552,
              "security": "note",
              "amount_minor": 810000,
              "conversion_price": "1.202579"
            }
          ],
          "new_money_minor": 10000000,
          "pre_money_minor": 40000000,
          "price_per_share": "3.336557",
          "post_money_minor": 50000000,
          "pre_round_shares": 9480000,
          "post_round_shares": 14985507,
          "founders_after_bps": 5339,
          "pool_top_up_shares": 498551,
          "founders_before_bps": 8439,
          "new_investor_shares": 2997101
        }
      ],
      "currency": "NGN",
      "dilution": [
        {
          "ref": "cc9fa021-46ab-4f8b-ac8b-f39e412f6d50",
          "kind": "founder",
          "name": "Ada Obi",
          "after_bps": 5339,
          "before_bps": 8439,
          "change_bps": -3100,
          "after_shares": 8000000,
          "before_shares": 8000000
        },
        {
          "ref": "round-1",
          "kind": "investor",
          "name": "Seed investors",
          "after_bps": 2000,
          "before_bps": 0,
          "change_bps": 2000,
          "after_shares": 2997101,
          "before_shares": 0
        },
        {
          "ref": "bde5e177-9c7b-4e38-a328-f4a4fc8f0f69",
          "kind": "pool",
          "name": "Option pool",
          "after_bps": 1000,
          "before_bps": 1055,
          "change_bps": -55,
          "after_shares": 1498551,
          "before_shares": 1000000
        },
        {
          "ref": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
          "kind": "investor",
          "name": "Angel One",
          "after_bps": 892,
          "before_bps": 0,
          "change_bps": 892,
          "after_shares": 1336303,
          "before_shares": 0
        },
        {
          "ref": "b629b650-af2e-44bd-a9fa-ff22c1d7fb4e",
          "kind": "investor",
          "name": "Lagos Bank",
          "after_bps": 449,
          "before_bps": 0,
          "change_bps": 449,
          "after_shares": 673552,
          "before_shares": 0
        },
        {
          "ref": "54f82d59-7a3f-40d6-bd12-7e6a9fecaae7",
          "kind": "employee",
          "name": "Eve Placeholder",
          "after_bps": 320,
          "before_bps": 506,
          "change_bps": -186,
          "after_shares": 480000,
          "before_shares": 480000
        }
      ],
      "headline": {
        "founders_after_bps": 5339,
        "founders_before_bps": 8439
      },
      "disclaimer": "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed documents. Check them with a lawyer before you rely on these numbers.",
      "total_shares_after": 14985507,
      "total_shares_before": 9480000
    }
  },
  "meta": null
}
```

`DELETE /scenarios/{id}` removes it.

## 7. Change history

`GET /audit?limit=&offset=` (limit 1 to 200, default 50), newest first: every create, update and delete of a
holder, entry or scenario with `before` and `after` (scenarios hold their headline, not the full result).
Founder only like everything else here. Trimmed to three rows:

capture `audit`
```json
{
  "data": {
    "total": 14,
    "items": [
      {
        "id": "37df3a12-0f23-4f28-8913-d7b017d71da7",
        "at": "2026-11-06T20:50:43.841886+00:00",
        "actor_id": "b7e7a238-815d-4ae7-94d9-829801b8afc7",
        "entity_type": "entry",
        "entity_id": "937bba08-87a6-4a54-ade9-6526df522593",
        "action": "update",
        "before": {
          "id": "937bba08-87a6-4a54-ade9-6526df522593",
          "mfn": false,
          "notes": null,
          "shares": null,
          "security": "safe",
          "holder_id": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
          "issued_on": "2025-09-01",
          "safe_kind": "post_money",
          "price_minor": null,
          "amount_minor": 1000000,
          "discount_bps": null,
          "interest_bps": null,
          "vesting_months": null,
          "vesting_start_on": null,
          "valuation_cap_minor": 10000000,
          "vesting_cliff_months": null
        },
        "after": {
          "id": "937bba08-87a6-4a54-ade9-6526df522593",
          "mfn": false,
          "notes": null,
          "shares": null,
          "security": "safe",
          "holder_id": "0d8df274-3438-4c3f-a5f4-14671b4448c7",
          "issued_on": "2025-09-01",
          "safe_kind": "post_money",
          "price_minor": null,
          "amount_minor": 1000000,
          "discount_bps": null,
          "interest_bps": null,
          "vesting_months": null,
          "vesting_start_on": null,
          "valuation_cap_minor": 9000000,
          "vesting_cliff_months": null
        }
      },
      {
        "id": "5c0a8d7b-bba3-47fa-a687-2bf020ff9f01",
        "at": "2026-11-06T20:50:43.824388+00:00",
        "actor_id": "b7e7a238-815d-4ae7-94d9-829801b8afc7",
        "entity_type": "scenario",
        "entity_id": "ad8b087e-2f78-4cce-811d-7aa2d8a353d8",
        "action": "update",
        "before": {
          "id": "ad8b087e-2f78-4cce-811d-7aa2d8a353d8",
          "name": "Seed at 40M",
          "headline": {
            "founders_after_bps": 5339,
            "founders_before_bps": 8439
          },
          "created_at": "2026-11-06T20:50:43.795017+00:00"
        },
        "after": {
          "id": "ad8b087e-2f78-4cce-811d-7aa2d8a353d8",
          "name": "Seed at 40M (base)",
          "headline": {
            "founders_after_bps": 5339,
            "founders_before_bps": 8439
          },
          "created_at": "2026-11-06T20:50:43.795017+00:00"
        }
      },
      {
        "id": "5c5c79d7-ca47-4625-a2c1-63eb99cd3fd1",
        "at": "2026-11-06T20:50:43.799121+00:00",
        "actor_id": "b7e7a238-815d-4ae7-94d9-829801b8afc7",
        "entity_type": "scenario",
        "entity_id": "ad8b087e-2f78-4cce-811d-7aa2d8a353d8",
        "action": "create",
        "before": null,
        "after": {
          "id": "ad8b087e-2f78-4cce-811d-7aa2d8a353d8",
          "name": "Seed at 40M",
          "headline": {
            "founders_after_bps": 5339,
            "founders_before_bps": 8439
          },
          "created_at": "2026-11-06T20:50:43.795017+00:00"
        }
      }
    ]
  },
  "meta": null
}
```

## 8. Currency

capture `round_currency_locked_409`
```json
{
  "error": {
    "code": "CAP_TABLE_HAS_AMOUNTS",
    "message": "Your cap table already has amounts in NGN. Clear them before changing the currency.",
    "field_errors": []
  }
}
```

Deletes return `{"data": {"deleted": true}, "meta": null}`:

capture `entry_deleted`
```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```

## 9. UX consequences the FE must surface

- Show the disclaimer on the page and on any export/print view.
- A SAFE or note is an **amount**, not a percentage; never put it in the ownership donut. Offer "Model a
  round" to see it converted.
- Deleting a holder removes their entries: confirm with the count.
- A scenario is a snapshot; label it with its date and offer "Re-run on today's numbers".
- If `after_bps` for a row is 0 or rounding makes a row look odd, trust the API; the column sums to 100.00%.
- Team members must not see the Cap table or Term sheets tabs.

## 10. Verification table

| Behaviour | Verified |
|---|---|
| Empty state, holders, all five security shapes, vesting, shape 422s, second pool 409, pool options-only 422 | live (`e2e/test_funding_cap_table.py`) |
| Ledger fully diluted and basic, vested shares, accrued interest | live |
| Model: SAFE cap/discount/note conversion, pool top-up, two rounds, headline, dilution table, refusals | live (`POOL_TARGET_TOO_HIGH`, `EMPTY_CAP_TABLE`); other codes unit tests |
| Scenarios create, list, open, rename, delete | live |
| Audit trail | live |
| Round currency locked once amounts exist | live |
| Founder-only (403 for team member and every other role), 402 gate, cross-workspace 404 | unit tests only |
| Currency lock on first/next round, workspace lock on round upserts, MFN donors, pre-money SAFE with pool, one row per holder, slow-converging and overpowered SAFEs | unit tests (`test_cap_engine.py`, `test_funding_cap_table_api.py`) |
| Maths worked examples (hand-derived), rounding rules, MFN, interest, vesting, largest-remainder | unit tests (`tests/services/funding/test_cap_engine.py`) |
| Concurrent edits (workspace row lock) | unit tests (lock taken on every write; stale-row merge); not a two-connection race test |
| Data room snapshot of the cap table | not built (needs a decision, see the SOP) |
