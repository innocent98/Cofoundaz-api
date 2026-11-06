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
  table has an amount or share price (409 `CAP_TABLE_HAS_AMOUNTS`).
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
        "id": "1b03ba97-72a6-47a7-8aba-13c3b7f64eeb",
        "name": "Ada Obi",
        "kind": "founder",
        "email": null,
        "notes": null,
        "shares": 8000000,
        "pct_bps": 8439,
        "entries": [
          {
            "id": "3c0ea2a5-8d76-4941-8462-4abef0214b63",
            "holder_id": "1b03ba97-72a6-47a7-8aba-13c3b7f64eeb",
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
        "id": "771f2875-fe93-4962-ba4c-8e6e19854d37",
        "name": "Option pool",
        "kind": "pool",
        "email": null,
        "notes": null,
        "shares": 1000000,
        "pct_bps": 1055,
        "entries": [
          {
            "id": "17fc2baf-6cd2-4143-b0fc-b2d3165640c0",
            "holder_id": "771f2875-fe93-4962-ba4c-8e6e19854d37",
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
        "id": "a1dccab4-095b-4b26-b4e8-9f847a1a6f61",
        "name": "Eve Placeholder",
        "kind": "employee",
        "email": "eve@example.com",
        "notes": null,
        "shares": 480000,
        "pct_bps": 506,
        "entries": [
          {
            "id": "836039da-0c2a-4760-8ab9-0f509318c33c",
            "holder_id": "a1dccab4-095b-4b26-b4e8-9f847a1a6f61",
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
        "id": "1b03ba97-72a6-47a7-8aba-13c3b7f64eeb",
        "name": "Ada Obi",
        "kind": "founder",
        "email": null,
        "notes": null,
        "shares": 8000000,
        "pct_bps": 10000,
        "entries": [
          {
            "id": "3c0ea2a5-8d76-4941-8462-4abef0214b63",
            "holder_id": "1b03ba97-72a6-47a7-8aba-13c3b7f64eeb",
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
        "id": "9ba0e312-8f32-434c-b457-9b9421ef2cbf",
        "name": "Angel One",
        "kind": "investor",
        "email": null,
        "notes": null,
        "shares": 0,
        "pct_bps": null,
        "entries": [
          {
            "id": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
            "holder_id": "9ba0e312-8f32-434c-b457-9b9421ef2cbf",
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
            "id": "61551201-04b8-4973-b586-f0570fca70a3",
            "holder_id": "9ba0e312-8f32-434c-b457-9b9421ef2cbf",
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
        "id": "a1dccab4-095b-4b26-b4e8-9f847a1a6f61",
        "name": "Eve Placeholder",
        "kind": "employee",
        "email": "eve@example.com",
        "notes": null,
        "shares": 0,
        "pct_bps": null,
        "entries": [
          {
            "id": "836039da-0c2a-4760-8ab9-0f509318c33c",
            "holder_id": "a1dccab4-095b-4b26-b4e8-9f847a1a6f61",
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
`PATCH /holders/{id}` takes `name`, `email`, `notes`. `DELETE /holders/{id}` removes the holder **and their
entries** (confirm in the UI); everything removed is in the change history.

capture `holder_created`
```json
{
  "data": {
    "id": "1b03ba97-72a6-47a7-8aba-13c3b7f64eeb",
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
| `safe`, `note` | `amount_minor` | `valuation_cap_minor`, `discount_bps` (1 to 9999), `safe_kind` (`post_money` default, or `pre_money`), `mfn`, `issued_on`, `notes`; `interest_bps` (**notes only**) | `shares`, `price_minor`, vesting |

capture `entry_common`
```json
{
  "data": {
    "id": "3c0ea2a5-8d76-4941-8462-4abef0214b63",
    "holder_id": "1b03ba97-72a6-47a7-8aba-13c3b7f64eeb",
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
    "id": "836039da-0c2a-4760-8ab9-0f509318c33c",
    "holder_id": "a1dccab4-095b-4b26-b4e8-9f847a1a6f61",
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
    "id": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
    "holder_id": "9ba0e312-8f32-434c-b457-9b9421ef2cbf",
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
    "id": "ff576dbc-f5ca-4b99-8774-2903a747998d",
    "holder_id": "ad81a427-06e7-4022-8fbb-0e322c3aaa29",
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
    "id": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
    "holder_id": "9ba0e312-8f32-434c-b457-9b9421ef2cbf",
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
company before the round (SAFEs included); a **pre-money** cap prices on the company without SAFEs. `mfn`
instruments adopt the best later terms of the same cap type. Notes convert with simple interest to `as_of`.
Each conversion reports which term won (`term`: `cap`, `discount` or `round`).

capture `model_seed`
```json
{
  "data": {
    "currency": "NGN",
    "as_of": "2026-06-01",
    "headline": {
      "founders_before_bps": 8439,
      "founders_after_bps": 5358
    },
    "steps": [
      {
        "name": "Seed",
        "pre_money_minor": 40000000,
        "new_money_minor": 10000000,
        "post_money_minor": 50000000,
        "price_per_share": "3.348735",
        "pre_round_shares": 9480000,
        "pool_top_up_shares": 493102,
        "new_investor_shares": 2986202,
        "post_round_shares": 14931011,
        "conversions": [
          {
            "ref": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
            "name": "Angel One",
            "security": "safe",
            "amount_minor": 1000000,
            "term": "cap",
            "conversion_price": "0.873232",
            "shares": 1145170
          },
          {
            "ref": "61551201-04b8-4973-b586-f0570fca70a3",
            "name": "Angel One",
            "security": "safe",
            "amount_minor": 500000,
            "term": "discount",
            "conversion_price": "2.678988",
            "shares": 186637
          },
          {
            "ref": "ff576dbc-f5ca-4b99-8774-2903a747998d",
            "name": "Lagos Bank",
            "security": "note",
            "amount_minor": 810000,
            "term": "cap",
            "conversion_price": "1.265823",
            "shares": 639900
          }
        ],
        "founders_before_bps": 8439,
        "founders_after_bps": 5358
      }
    ],
    "dilution": [
      {
        "ref": "1b03ba97-72a6-47a7-8aba-13c3b7f64eeb",
        "name": "Ada Obi",
        "kind": "founder",
        "before_shares": 8000000,
        "before_bps": 8439,
        "after_shares": 8000000,
        "after_bps": 5358,
        "change_bps": -3081
      },
      {
        "ref": "round-1",
        "name": "Seed investors",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 2986202,
        "after_bps": 2000,
        "change_bps": 2000
      },
      {
        "ref": "771f2875-fe93-4962-ba4c-8e6e19854d37",
        "name": "Option pool",
        "kind": "pool",
        "before_shares": 1000000,
        "before_bps": 1055,
        "after_shares": 1493102,
        "after_bps": 1000,
        "change_bps": -55
      },
      {
        "ref": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
        "name": "Angel One",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 1145170,
        "after_bps": 767,
        "change_bps": 767
      },
      {
        "ref": "ff576dbc-f5ca-4b99-8774-2903a747998d",
        "name": "Lagos Bank",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 639900,
        "after_bps": 429,
        "change_bps": 429
      },
      {
        "ref": "a1dccab4-095b-4b26-b4e8-9f847a1a6f61",
        "name": "Eve Placeholder",
        "kind": "employee",
        "before_shares": 480000,
        "before_bps": 506,
        "after_shares": 480000,
        "after_bps": 321,
        "change_bps": -185
      },
      {
        "ref": "61551201-04b8-4973-b586-f0570fca70a3",
        "name": "Angel One",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 186637,
        "after_bps": 125,
        "change_bps": 125
      }
    ],
    "total_shares_before": 9480000,
    "total_shares_after": 14931011,
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
- `dilution[]`: the before/after table, one row per holder (and per converting SAFE/note and per round's new
  investors). **Trap:** two SAFEs held by one person appear as two rows (`ref` is the entry id for a
  conversion, the holder id otherwise); merge by `name` if you want one line. `before_*` is 0 for rows that
  only exist after the round. `after_bps` sums to 10 000.
- Later rounds dilute everyone, including earlier investors; SAFEs convert in the first round only.

capture `model_seed_and_series_a`
```json
{
  "data": {
    "currency": "NGN",
    "as_of": "2026-06-01",
    "headline": {
      "founders_before_bps": 8439,
      "founders_after_bps": 4019
    },
    "steps": [
      {
        "name": "Seed",
        "pre_money_minor": 40000000,
        "new_money_minor": 10000000,
        "post_money_minor": 50000000,
        "price_per_share": "3.348735",
        "pre_round_shares": 9480000,
        "pool_top_up_shares": 493102,
        "new_investor_shares": 2986202,
        "post_round_shares": 14931011,
        "conversions": [
          {
            "ref": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
            "name": "Angel One",
            "security": "safe",
            "amount_minor": 1000000,
            "term": "cap",
            "conversion_price": "0.873232",
            "shares": 1145170
          },
          {
            "ref": "61551201-04b8-4973-b586-f0570fca70a3",
            "name": "Angel One",
            "security": "safe",
            "amount_minor": 500000,
            "term": "discount",
            "conversion_price": "2.678988",
            "shares": 186637
          },
          {
            "ref": "ff576dbc-f5ca-4b99-8774-2903a747998d",
            "name": "Lagos Bank",
            "security": "note",
            "amount_minor": 810000,
            "term": "cap",
            "conversion_price": "1.265823",
            "shares": 639900
          }
        ],
        "founders_before_bps": 8439,
        "founders_after_bps": 5358
      },
      {
        "name": "Series A",
        "pre_money_minor": 150000000,
        "new_money_minor": 50000000,
        "post_money_minor": 200000000,
        "price_per_share": "10.046205",
        "pre_round_shares": 14931011,
        "pool_top_up_shares": 0,
        "new_investor_shares": 4977003,
        "post_round_shares": 19908014,
        "conversions": [],
        "founders_before_bps": 5358,
        "founders_after_bps": 4019
      }
    ],
    "dilution": [
      {
        "ref": "1b03ba97-72a6-47a7-8aba-13c3b7f64eeb",
        "name": "Ada Obi",
        "kind": "founder",
        "before_shares": 8000000,
        "before_bps": 8439,
        "after_shares": 8000000,
        "after_bps": 4019,
        "change_bps": -4420
      },
      {
        "ref": "round-2",
        "name": "Series A investors",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 4977003,
        "after_bps": 2500,
        "change_bps": 2500
      },
      {
        "ref": "round-1",
        "name": "Seed investors",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 2986202,
        "after_bps": 1500,
        "change_bps": 1500
      },
      {
        "ref": "771f2875-fe93-4962-ba4c-8e6e19854d37",
        "name": "Option pool",
        "kind": "pool",
        "before_shares": 1000000,
        "before_bps": 1055,
        "after_shares": 1493102,
        "after_bps": 750,
        "change_bps": -305
      },
      {
        "ref": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
        "name": "Angel One",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 1145170,
        "after_bps": 575,
        "change_bps": 575
      },
      {
        "ref": "ff576dbc-f5ca-4b99-8774-2903a747998d",
        "name": "Lagos Bank",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 639900,
        "after_bps": 321,
        "change_bps": 321
      },
      {
        "ref": "a1dccab4-095b-4b26-b4e8-9f847a1a6f61",
        "name": "Eve Placeholder",
        "kind": "employee",
        "before_shares": 480000,
        "before_bps": 506,
        "after_shares": 480000,
        "after_bps": 241,
        "change_bps": -265
      },
      {
        "ref": "61551201-04b8-4973-b586-f0570fca70a3",
        "name": "Angel One",
        "kind": "investor",
        "before_shares": 0,
        "before_bps": 0,
        "after_shares": 186637,
        "after_bps": 94,
        "change_bps": 94
      }
    ],
    "total_shares_before": 9480000,
    "total_shares_after": 19908014,
    "disclaimer": "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed documents. Check them with a lawyer before you rely on these numbers."
  },
  "meta": null
}
```

Refusals are 422 with a code the page can word: `EMPTY_CAP_TABLE` (add founder shares first),
`INVALID_ROUND`, `POOL_TARGET_TOO_HIGH` (the pool cannot exceed the existing holders' room),
`SAFE_CAPS_EXCEED` (post-money SAFEs own 100% or more), `CONVERSION_DID_NOT_SETTLE` (SAFEs and notes exceed
the pre-money). Only `EMPTY_CAP_TABLE` and `POOL_TARGET_TOO_HIGH` are captured live; the others are
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
    "id": "8f25c8b0-dac3-4f79-b374-0149dc327bd7",
    "name": "Seed at 40M",
    "created_at": "2026-11-06T11:58:08.425059+00:00",
    "headline": {
      "founders_after_bps": 5358,
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
              "ref": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
              "name": "Angel One",
              "term": "cap",
              "shares": 1145170,
              "security": "safe",
              "amount_minor": 1000000,
              "conversion_price": "0.873232"
            },
            {
              "ref": "61551201-04b8-4973-b586-f0570fca70a3",
              "name": "Angel One",
              "term": "discount",
              "shares": 186637,
              "security": "safe",
              "amount_minor": 500000,
              "conversion_price": "2.678988"
            },
            {
              "ref": "ff576dbc-f5ca-4b99-8774-2903a747998d",
              "name": "Lagos Bank",
              "term": "cap",
              "shares": 639900,
              "security": "note",
              "amount_minor": 810000,
              "conversion_price": "1.265823"
            }
          ],
          "new_money_minor": 10000000,
          "pre_money_minor": 40000000,
          "price_per_share": "3.348735",
          "post_money_minor": 50000000,
          "pre_round_shares": 9480000,
          "post_round_shares": 14931011,
          "founders_after_bps": 5358,
          "pool_top_up_shares": 493102,
          "founders_before_bps": 8439,
          "new_investor_shares": 2986202
        }
      ],
      "currency": "NGN",
      "dilution": [
        {
          "ref": "1b03ba97-72a6-47a7-8aba-13c3b7f64eeb",
          "kind": "founder",
          "name": "Ada Obi",
          "after_bps": 5358,
          "before_bps": 8439,
          "change_bps": -3081,
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
          "after_shares": 2986202,
          "before_shares": 0
        },
        {
          "ref": "771f2875-fe93-4962-ba4c-8e6e19854d37",
          "kind": "pool",
          "name": "Option pool",
          "after_bps": 1000,
          "before_bps": 1055,
          "change_bps": -55,
          "after_shares": 1493102,
          "before_shares": 1000000
        },
        {
          "ref": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
          "kind": "investor",
          "name": "Angel One",
          "after_bps": 767,
          "before_bps": 0,
          "change_bps": 767,
          "after_shares": 1145170,
          "before_shares": 0
        },
        {
          "ref": "ff576dbc-f5ca-4b99-8774-2903a747998d",
          "kind": "investor",
          "name": "Lagos Bank",
          "after_bps": 429,
          "before_bps": 0,
          "change_bps": 429,
          "after_shares": 639900,
          "before_shares": 0
        },
        {
          "ref": "a1dccab4-095b-4b26-b4e8-9f847a1a6f61",
          "kind": "employee",
          "name": "Eve Placeholder",
          "after_bps": 321,
          "before_bps": 506,
          "change_bps": -185,
          "after_shares": 480000,
          "before_shares": 480000
        },
        {
          "ref": "61551201-04b8-4973-b586-f0570fca70a3",
          "kind": "investor",
          "name": "Angel One",
          "after_bps": 125,
          "before_bps": 0,
          "change_bps": 125,
          "after_shares": 186637,
          "before_shares": 0
        }
      ],
      "headline": {
        "founders_after_bps": 5358,
        "founders_before_bps": 8439
      },
      "disclaimer": "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed documents. Check them with a lawyer before you rely on these numbers.",
      "total_shares_after": 14931011,
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
        "id": "8f25c8b0-dac3-4f79-b374-0149dc327bd7",
        "name": "Seed at 40M",
        "created_at": "2026-11-06T11:58:08.425059+00:00",
        "headline": {
          "founders_after_bps": 5358,
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
    "id": "8f25c8b0-dac3-4f79-b374-0149dc327bd7",
    "name": "Seed at 40M (base)",
    "created_at": "2026-11-06T11:58:08.425059+00:00",
    "headline": {
      "founders_after_bps": 5358,
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
    "id": "8f25c8b0-dac3-4f79-b374-0149dc327bd7",
    "name": "Seed at 40M (base)",
    "created_at": "2026-11-06T11:58:08.425059+00:00",
    "headline": {
      "founders_after_bps": 5358,
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
              "ref": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
              "name": "Angel One",
              "term": "cap",
              "shares": 1145170,
              "security": "safe",
              "amount_minor": 1000000,
              "conversion_price": "0.873232"
            },
            {
              "ref": "61551201-04b8-4973-b586-f0570fca70a3",
              "name": "Angel One",
              "term": "discount",
              "shares": 186637,
              "security": "safe",
              "amount_minor": 500000,
              "conversion_price": "2.678988"
            },
            {
              "ref": "ff576dbc-f5ca-4b99-8774-2903a747998d",
              "name": "Lagos Bank",
              "term": "cap",
              "shares": 639900,
              "security": "note",
              "amount_minor": 810000,
              "conversion_price": "1.265823"
            }
          ],
          "new_money_minor": 10000000,
          "pre_money_minor": 40000000,
          "price_per_share": "3.348735",
          "post_money_minor": 50000000,
          "pre_round_shares": 9480000,
          "post_round_shares": 14931011,
          "founders_after_bps": 5358,
          "pool_top_up_shares": 493102,
          "founders_before_bps": 8439,
          "new_investor_shares": 2986202
        }
      ],
      "currency": "NGN",
      "dilution": [
        {
          "ref": "1b03ba97-72a6-47a7-8aba-13c3b7f64eeb",
          "kind": "founder",
          "name": "Ada Obi",
          "after_bps": 5358,
          "before_bps": 8439,
          "change_bps": -3081,
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
          "after_shares": 2986202,
          "before_shares": 0
        },
        {
          "ref": "771f2875-fe93-4962-ba4c-8e6e19854d37",
          "kind": "pool",
          "name": "Option pool",
          "after_bps": 1000,
          "before_bps": 1055,
          "change_bps": -55,
          "after_shares": 1493102,
          "before_shares": 1000000
        },
        {
          "ref": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
          "kind": "investor",
          "name": "Angel One",
          "after_bps": 767,
          "before_bps": 0,
          "change_bps": 767,
          "after_shares": 1145170,
          "before_shares": 0
        },
        {
          "ref": "ff576dbc-f5ca-4b99-8774-2903a747998d",
          "kind": "investor",
          "name": "Lagos Bank",
          "after_bps": 429,
          "before_bps": 0,
          "change_bps": 429,
          "after_shares": 639900,
          "before_shares": 0
        },
        {
          "ref": "a1dccab4-095b-4b26-b4e8-9f847a1a6f61",
          "kind": "employee",
          "name": "Eve Placeholder",
          "after_bps": 321,
          "before_bps": 506,
          "change_bps": -185,
          "after_shares": 480000,
          "before_shares": 480000
        },
        {
          "ref": "61551201-04b8-4973-b586-f0570fca70a3",
          "kind": "investor",
          "name": "Angel One",
          "after_bps": 125,
          "before_bps": 0,
          "change_bps": 125,
          "after_shares": 186637,
          "before_shares": 0
        }
      ],
      "headline": {
        "founders_after_bps": 5358,
        "founders_before_bps": 8439
      },
      "disclaimer": "Estimates for planning, not legal or tax advice. Real conversion terms are set by your signed documents. Check them with a lawyer before you rely on these numbers.",
      "total_shares_after": 14931011,
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
        "id": "f892c74b-9a48-4049-9293-a89076e16830",
        "at": "2026-11-06T11:58:08.470268+00:00",
        "actor_id": "e1b8c33e-2e91-4826-a849-eb81b3d01c59",
        "entity_type": "entry",
        "entity_id": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
        "action": "update",
        "before": {
          "id": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
          "mfn": false,
          "notes": null,
          "shares": null,
          "security": "safe",
          "holder_id": "9ba0e312-8f32-434c-b457-9b9421ef2cbf",
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
          "id": "1acd1238-ec18-4cfa-ac8a-db4303c2f902",
          "mfn": false,
          "notes": null,
          "shares": null,
          "security": "safe",
          "holder_id": "9ba0e312-8f32-434c-b457-9b9421ef2cbf",
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
        "id": "2b0f66be-03e5-40bc-bd0d-2c3337eb4ad6",
        "at": "2026-11-06T11:58:08.455674+00:00",
        "actor_id": "e1b8c33e-2e91-4826-a849-eb81b3d01c59",
        "entity_type": "scenario",
        "entity_id": "8f25c8b0-dac3-4f79-b374-0149dc327bd7",
        "action": "update",
        "before": {
          "id": "8f25c8b0-dac3-4f79-b374-0149dc327bd7",
          "name": "Seed at 40M",
          "headline": {
            "founders_after_bps": 5358,
            "founders_before_bps": 8439
          },
          "created_at": "2026-11-06T11:58:08.425059+00:00"
        },
        "after": {
          "id": "8f25c8b0-dac3-4f79-b374-0149dc327bd7",
          "name": "Seed at 40M (base)",
          "headline": {
            "founders_after_bps": 5358,
            "founders_before_bps": 8439
          },
          "created_at": "2026-11-06T11:58:08.425059+00:00"
        }
      },
      {
        "id": "b215d79c-b68d-46aa-bd8e-b8df408d5ac7",
        "at": "2026-11-06T11:58:08.427634+00:00",
        "actor_id": "e1b8c33e-2e91-4826-a849-eb81b3d01c59",
        "entity_type": "scenario",
        "entity_id": "8f25c8b0-dac3-4f79-b374-0149dc327bd7",
        "action": "create",
        "before": null,
        "after": {
          "id": "8f25c8b0-dac3-4f79-b374-0149dc327bd7",
          "name": "Seed at 40M",
          "headline": {
            "founders_after_bps": 5358,
            "founders_before_bps": 8439
          },
          "created_at": "2026-11-06T11:58:08.425059+00:00"
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
| Maths worked examples (hand-derived), rounding rules, MFN, interest, vesting, largest-remainder | unit tests (`tests/services/funding/test_cap_engine.py`) |
| Concurrent edits (workspace row lock) | unit tests (lock taken on every write; stale-row merge); not a two-connection race test |
| Data room snapshot of the cap table | not built (needs a decision, see the SOP) |
