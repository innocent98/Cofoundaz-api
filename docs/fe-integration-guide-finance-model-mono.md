# FE Integration Guide: Finance model (editable, 36 months) and the Mono bank link

> **Provenance.** `e2e/test_finance_model_mono.py::test_finance_model_and_mono_journey` ran against the live app
> (`scripts/e2e_run.sh`, scratch DB, **90 e2e passed**). Every **response body** below is pasted from the files it
> wrote to `e2e/_captures/finance_model_mono/` (named under each block) and from `e2e/_captures/financial_model/`.
> Nothing was written from the schema. **Trimming is marked**: a numeric array of 36 values is shown as its first 4
> values followed by `/* … 32 more */` (a comment, not valid JSON; the full arrays are in the capture files), and a
> few bodies are cut to the keys under discussion (said above the block). **Request** bodies are the exact JSON the
> journey sends.
>
> **What "live" means here.** The server was the real uvicorn app with `PAYMENT_PROVIDER_STUB=true` and **fake Mono
> values** in the environment. Calls to Mono were answered by a handwritten stub inside the server
> (`app/services/finance/payments/stub_transport.py`). So our routes, roles, encryption, import, idempotency, the
> webhook secret check and every model calculation are verified live. **The real Mono sandbox was NOT called**
> (it needs the real keys, which this build never reads); real Mono response shapes, the unit of Mono's `amount`
> (assumed kobo) and real webhook payloads are **unverified**. See the verification table at the end.
> All ids, keys and emails in captures are throwaway placeholders from one run.

Base path `/api/v1`. Every route here needs `Authorization: Bearer <token>` and `X-Workspace-Id`, except the Mono
webhook (called by Mono, never by the FE). Success is `{"data": …, "meta": null}`; errors are
`{"error": {"code", "message", "field_errors"}}`. **Roles:** founder, team member and accountant may use every route
below; **only the founder may connect or disconnect the bank**. Mentor, consultant, investor get `403 FORBIDDEN`.

---

# Part 1: the financial model

## What changed, in the FE's terms
- The model is now **36 months** (was 12) and comes with a **yearly rollup** in the same response.
- Assumptions are **editable**. Saving edits does not change the statements: the FE shows an **"Edited, not yet
  recalculated"** state (`stale: true`) and a **Recalculate** button. Recalculate is instant and **costs no AI**.
  Only `POST /finance/model/generate` calls the AI (and is still limited to one at a time, `409 MODEL_ALREADY_GENERATING`).
- The balance sheet is real: it has totals and a **Balance check** row that must be 0 every month, and the API says so
  in `balance_check`.

| Route | Purpose |
|---|---|
| `GET /finance/model` / `GET /finance/model/{id}` | unchanged routes, new fields (below) |
| `PATCH /finance/model/{id}/assumptions` | save the founder's edits. Returns the model, statements unchanged, `stale: true` |
| `POST /finance/model/{id}/recalculate` | re-run the engine with the saved edits. Body optional: `{"refresh_actuals": true}` |

## The model object (new and changed fields)
Fresh model, before any edit (cut to the non-statement keys):

```json
{
  "data": {
    "id": "4d76f19e-629e-41e8-ad71-ce225fddea4e",
    "status": "complete",
    "horizon_months": 36,
    "currency": "NGN",
    "stale": false,
    "overrides": null,
    "recalculated_at": null,
    "balance_check": {
      "balanced": true,
      "max_abs_difference_minor": 0
    },
    "assumptions": {
      "ap_days": 0,
      "ar_days": 0,
      "narrative": "[stub-llm] narrative",
      "revenue_source": "actuals",
      "revenue_0_minor": 2000000,
      "cogs_pct_of_revenue": 0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 0.0,
      "monthly_costs_0_minor": 10666667
    },
    "ai_assumptions": {
      "ap_days": 0,
      "ar_days": 0,
      "narrative": "[stub-llm] narrative",
      "revenue_source": "actuals",
      "revenue_0_minor": 2000000,
      "cogs_pct_of_revenue": 0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 0.0
    }
  },
  "_other_keys": "pnl, cash_flow, balance_sheet, months, yearly, error, generated_at, created_at (see below)"
}
```
*capture: `model_complete.json`*

Field-nesting traps (read these):
- **`assumptions` is what the engine used** (AI values with the founder's edits on top, and `revenue_0_minor` /
  `monthly_costs_0_minor` showing the starting figures actually used). Bind the **editor inputs** to it.
- **`ai_assumptions` is the AI's baseline.** Show it as "AI suggested X" next to an edited field. `overrides` holds only
  what the founder changed (`null` when nothing is edited); to revert one field, PATCH it with `null`.
- `months` is **top level** (one list of 36 `YYYY-MM` strings). Statements are `{rows: [{label, values}]}` with 36 values
  each. `recalculated_at` is `null` until the first recalculation.
- `balance_check` is `null` on a model generated before this release (no check line existed); it is an object on every
  new or recalculated model.
- Money is integer **kobo** everywhere (`amount_minor`, `*_minor`, every statement value).

### Statements (months and rows, 36 values each)
```json
{
  "data": {
    "months": [
      "2026-11",
      "2026-12",
      "2027-01", /* … 33 more */ 
    ],
    "pnl": {
      "rows": [
        {
          "label": "Revenue",
          "values": [
            2000000,
            2000000,
            2000000,
            2000000, /* … 32 more */ 
          ]
        },
        {
          "label": "COGS",
          "values": [
            0,
            0,
            0,
            0, /* … 32 more */ 
          ]
        },
        {
          "label": "Gross profit",
          "values": [
            2000000,
            2000000,
            2000000,
            2000000, /* … 32 more */ 
          ]
        },
        {
          "label": "Opex",
          "values": [
            10666667,
            10666667,
            10666667,
            10666667, /* … 32 more */ 
          ]
        },
        {
          "label": "Depreciation",
          "values": [
            0,
            0,
            0,
            0, /* … 32 more */ 
          ]
        },
        {
          "label": "Net income",
          "values": [
            -8666667,
            -8666667,
            -8666667,
            -8666667, /* … 32 more */ 
          ]
        }
      ]
    },
    "cash_flow": {
      "rows": [
        {
          "label": "Opening cash",
          "values": [
            74000000,
            65333333,
            56666666,
            47999999, /* … 32 more */ 
          ]
        },
        {
          "label": "Collections",
          "values": [
            2000000,
            2000000,
            2000000,
            2000000, /* … 32 more */ 
          ]
        },
        {
          "label": "Payments",
          "values": [
            10666667,
            10666667,
            10666667,
            10666667, /* … 32 more */ 
          ]
        },
        {
          "label": "Capex",
          "values": [
            0,
            0,
            0,
            0, /* … 32 more */ 
          ]
        },
        {
          "label": "Net cash flow",
          "values": [
            -8666667,
            -8666667,
            -8666667,
            -8666667, /* … 32 more */ 
          ]
        },
        {
          "label": "Closing cash",
          "values": [
            65333333,
            56666666,
            47999999,
            39333332, /* … 32 more */ 
          ]
        }
      ]
    },
    "balance_sheet": {
      "rows": [
        {
          "label": "Cash",
          "values": [
            65333333,
            56666666,
            47999999,
            39333332, /* … 32 more */ 
          ]
        },
        {
          "label": "Accounts receivable",
          "values": [
            0,
            0,
            0,
            0, /* … 32 more */ 
          ]
        },
        {
          "label": "Fixed assets (net)",
          "values": [
            0,
            0,
            0,
            0, /* … 32 more */ 
          ]
        },
        {
          "label": "Total assets",
          "values": [
            65333333,
            56666666,
            47999999,
            39333332, /* … 32 more */ 
          ]
        },
        {
          "label": "Accounts payable",
          "values": [
            0,
            0,
            0,
            0, /* … 32 more */ 
          ]
        },
        {
          "label": "Total liabilities",
          "values": [
            0,
            0,
            0,
            0, /* … 32 more */ 
          ]
        },
        {
          "label": "Paid-in capital",
          "values": [
            100000000,
            100000000,
            100000000,
            100000000, /* … 32 more */ 
          ]
        },
        {
          "label": "Retained earnings",
          "values": [
            -34666667,
            -43333334,
            -52000001,
            -60666668, /* … 32 more */ 
          ]
        },
        {
          "label": "Total equity",
          "values": [
            65333333,
            56666666,
            47999999,
            39333332, /* … 32 more */ 
          ]
        },
        {
          "label": "Total liabilities + equity",
          "values": [
            65333333,
            56666666,
            47999999,
            39333332, /* … 32 more */ 
          ]
        },
        {
          "label": "Balance check",
          "values": [
            0,
            0,
            0,
            0, /* … 32 more */ 
          ]
        }
      ]
    }
  }
}
```
*capture: `model_complete.json`*

Rows by statement, in this order (match on `label`):
- **P&L:** Revenue, COGS, Gross profit, Opex, **Depreciation** (new), Net income.
- **Cash flow:** Opening cash, Collections, Payments, **Capex** (new), Net cash flow, Closing cash.
- **Balance sheet:** Cash, Accounts receivable, **Fixed assets (net)**, **Total assets**, Accounts payable,
  **Total liabilities**, Paid-in capital, Retained earnings, **Total equity**, **Total liabilities + equity**,
  **Balance check**. (Bold = new. Order differs from before: do not index rows by position.)

**Paid-in capital** is the sum of ledger inflows whose category is **`Funding`**, so a founder who records a raise under
another category (for example "Fundraising") sees paid-in capital 0 and the raise inside retained earnings. The Finance
category list now offers `Funding`; point the "money raised" flow at it.

### Yearly rollup
`yearly.periods` are projection years (Year 1 = months 1-12). Flow rows add up, **Opening cash** is the year's first
month, every balance (closing cash, receivables, equity, check…) is the year's last month. Same row labels as the monthly
statements. Trimmed to the `yearly` key of the recalculated model:

```json
{
  "data": {
    "yearly": {
      "periods": [
        {
          "label": "Year 1",
          "start_month": "2026-11",
          "end_month": "2027-10"
        },
        {
          "label": "Year 2",
          "start_month": "2027-11",
          "end_month": "2028-10"
        },
        {
          "label": "Year 3",
          "start_month": "2028-11",
          "end_month": "2029-10"
        }
      ],
      "pnl": {
        "rows": [
          {
            "label": "Revenue",
            "values": [
              35764275,
              71964748,
              144807213
            ]
          },
          {
            "label": "COGS",
            "values": [
              8941069,
              17991188,
              36201804
            ]
          },
          {
            "label": "Gross profit",
            "values": [
              26823206,
              53973560,
              108605409
            ]
          },
          {
            "label": "Opex",
            "values": [
              139000004,
              140000004,
              140000004
            ]
          },
          {
            "label": "Depreciation",
            "values": [
              1800000,
              2400000,
              600000
            ]
          },
          {
            "label": "Net income",
            "values": [
              -113976798,
              -88426444,
              -31994595
            ]
          }
        ]
      },
      "cash_flow": {
        "rows": [
          {
            "label": "Opening cash",
            "values": [
              74000000,
              -43663792,
              -133084799
            ]
          },
          {
            "label": "Collections",
            "values": [
              33739882,
              67891272,
              136610578
            ]
          },
          {
            "label": "Payments",
            "values": [
              146603674,
              157312279,
              174835702
            ]
          },
          {
            "label": "Capex",
            "values": [
              4800000,
              0,
              0
            ]
          },
          {
            "label": "Net cash flow",
            "values": [
              -117663792,
              -89421007,
              -38225124
            ]
          },
          {
            "label": "Closing cash",
            "values": [
              -43663792,
              -133084799,
              -171309923
            ]
          }
        ]
      },
      "balance_sheet": {
        "rows": [
          {
            "label": "Cash",
            "values": [
              -43663792,
              -133084799,
              -171309923
            ]
          },
          {
            "label": "Accounts receivable",
            "values": [
              4024393,
              8097869,
              16294504
            ]
          },
          {
            "label": "Fixed assets (net)",
            "values": [
              3000000,
              600000,
              0
            ]
          },
          {
            "label": "Total assets",
            "values": [
              -36639399,
              -124386930,
              -155015419
            ]
          },
          {
            "label": "Accounts payable",
            "values": [
              8448510,
              9127423,
              10493529
            ]
          },
          {
            "label": "Total liabilities",
            "values": [
              8448510,
              9127423,
              10493529
            ]
          },
          {
            "label": "Paid-in capital",
            "values": [
              100000000,
              100000000,
              100000000
            ]
          },
          {
            "label": "Retained earnings",
            "values": [
              -145087909,
              -233514353,
              -265508948
            ]
          },
          {
            "label": "Total equity",
            "values": [
              -45087909,
              -133514353,
              -165508948
            ]
          },
          {
            "label": "Total liabilities + equity",
            "values": [
              -36639399,
              -124386930,
              -155015419
            ]
          },
          {
            "label": "Balance check",
            "values": [
              0,
              0,
              0
            ]
          }
        ]
      }
    }
  }
}
```
*capture: `recalculated.json`*

## Edit assumptions
Editable fields (all optional; omit = untouched, `null` = clear that edit, a list replaces the whole list):

| Field | Type / range |
|---|---|
| `monthly_revenue_growth_pct`, `monthly_opex_growth_pct` | number, -50 to 100 |
| `cogs_pct_of_revenue` | number, 0 to 100 |
| `ar_days`, `ap_days` | integer, 0 to 120 |
| `revenue_0_minor`, `monthly_costs_0_minor` | integer kobo ≥ 0: the starting monthly revenue / total costs |
| `depreciation_months` | integer 6 to 120 (default 36) |
| `hiring_plan` | up to 50 × `{role, start_month, monthly_cost_minor}`: a monthly cost from `start_month` onward |
| `one_offs` | up to 50 × `{label, month, amount_minor}`: a one-time operating cost in that month |
| `capex` | up to 50 × `{label, month, amount_minor}`: cash out that month, depreciated straight-line from the next month |

`month` values are `YYYY-MM` and must fall inside `months[0]..months[35]` of this model. Unknown fields are rejected.
Request used in the journey:

```json
{
  "monthly_revenue_growth_pct": 6, "cogs_pct_of_revenue": 25, "ar_days": 30, "ap_days": 20,
  "hiring_plan": [{"role": "Backend engineer", "start_month": "2027-01", "monthly_cost_minor": 1500000}],
  "one_offs": [{"label": "Legal setup", "month": "2026-12", "amount_minor": 2000000}],
  "capex": [{"label": "Laptops", "month": "2027-01", "amount_minor": 4800000}],
  "depreciation_months": 24
}
```

Response `200` (cut to the non-statement keys; **statements are unchanged and `stale` is true**):

```json
{
  "data": {
    "id": "4d76f19e-629e-41e8-ad71-ce225fddea4e",
    "status": "complete",
    "horizon_months": 36,
    "currency": "NGN",
    "stale": true,
    "overrides": {
      "capex": [
        {
          "label": "Laptops",
          "month": "2027-01",
          "amount_minor": 4800000
        }
      ],
      "ap_days": 20,
      "ar_days": 30,
      "one_offs": [
        {
          "label": "Legal setup",
          "month": "2026-12",
          "amount_minor": 2000000
        }
      ],
      "hiring_plan": [
        {
          "role": "Backend engineer",
          "start_month": "2027-01",
          "monthly_cost_minor": 1500000
        }
      ],
      "cogs_pct_of_revenue": 25.0,
      "depreciation_months": 24,
      "monthly_revenue_growth_pct": 6.0
    },
    "recalculated_at": null,
    "balance_check": {
      "balanced": true,
      "max_abs_difference_minor": 0
    },
    "assumptions": {
      "ap_days": 20,
      "ar_days": 30,
      "narrative": "[stub-llm] narrative",
      "revenue_source": "actuals",
      "revenue_0_minor": 2000000,
      "cogs_pct_of_revenue": 25.0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 6.0,
      "capex": [
        {
          "label": "Laptops",
          "month": "2027-01",
          "amount_minor": 4800000
        }
      ],
      "one_offs": [
        {
          "label": "Legal setup",
          "month": "2026-12",
          "amount_minor": 2000000
        }
      ],
      "hiring_plan": [
        {
          "role": "Backend engineer",
          "start_month": "2027-01",
          "monthly_cost_minor": 1500000
        }
      ],
      "depreciation_months": 24,
      "monthly_costs_0_minor": 10666667
    },
    "ai_assumptions": {
      "ap_days": 0,
      "ar_days": 0,
      "narrative": "[stub-llm] narrative",
      "revenue_source": "actuals",
      "revenue_0_minor": 2000000,
      "cogs_pct_of_revenue": 0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 0.0
    }
  },
  "_other_keys": "pnl, cash_flow, balance_sheet, months, yearly, error, generated_at, created_at (see below)"
}
```
*capture: `assumptions_saved_stale.json`*

Errors (`422`, all `field_errors[].field` are the request path; list items are `name[index].field`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "monthly_revenue_growth_pct",
        "message": "Input should be less than or equal to 100"
      },
      {
        "field": "ar_days",
        "message": "Input should be greater than or equal to 0"
      }
    ]
  }
}
```
*capture: `assumptions_invalid.json`*

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "capex[0].month",
        "message": "Choose a month between 2026-11 and 2029-10."
      }
    ]
  }
}
```
*capture: `assumptions_month_outside.json`*

`403` for roles without Finance access:

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
*capture: `assumptions_mentor_403.json`*

`409 MODEL_NOT_READY` if the model is still `generating` or `failed`, and `404` for another workspace's model
(both unit-tested; not captured live).

## Recalculate
`POST /finance/model/{id}/recalculate`, empty body or `{"refresh_actuals": false}`. Returns the full model with
`stale: false`, `recalculated_at` set and new statements. Same edits give identical numbers (verified live), so it is safe
to call on every "Recalculate" click. It reuses the cash, revenue, costs and funding captured when the model was
generated: **adding a transaction later does not move the model.** `{"refresh_actuals": true}` re-reads the ledger
(use for a "Refresh from my latest numbers" action); it also restarts the month window from today.

```json
{
  "data": {
    "id": "4d76f19e-629e-41e8-ad71-ce225fddea4e",
    "status": "complete",
    "horizon_months": 36,
    "currency": "NGN",
    "stale": false,
    "overrides": {
      "capex": [
        {
          "label": "Laptops",
          "month": "2027-01",
          "amount_minor": 4800000
        }
      ],
      "ap_days": 20,
      "ar_days": 30,
      "one_offs": [
        {
          "label": "Legal setup",
          "month": "2026-12",
          "amount_minor": 2000000
        }
      ],
      "hiring_plan": [
        {
          "role": "Backend engineer",
          "start_month": "2027-01",
          "monthly_cost_minor": 1500000
        }
      ],
      "cogs_pct_of_revenue": 25.0,
      "depreciation_months": 24,
      "monthly_revenue_growth_pct": 6.0
    },
    "recalculated_at": "2026-10-24T12:15:04.909417Z",
    "balance_check": {
      "balanced": true,
      "max_abs_difference_minor": 0
    },
    "assumptions": {
      "ap_days": 20,
      "ar_days": 30,
      "narrative": "[stub-llm] narrative",
      "revenue_source": "actuals",
      "revenue_0_minor": 2000000,
      "cogs_pct_of_revenue": 25.0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 6.0,
      "capex": [
        {
          "label": "Laptops",
          "month": "2027-01",
          "amount_minor": 4800000
        }
      ],
      "one_offs": [
        {
          "label": "Legal setup",
          "month": "2026-12",
          "amount_minor": 2000000
        }
      ],
      "hiring_plan": [
        {
          "role": "Backend engineer",
          "start_month": "2027-01",
          "monthly_cost_minor": 1500000
        }
      ],
      "depreciation_months": 24,
      "monthly_costs_0_minor": 10666667
    },
    "ai_assumptions": {
      "ap_days": 0,
      "ar_days": 0,
      "narrative": "[stub-llm] narrative",
      "revenue_source": "actuals",
      "revenue_0_minor": 2000000,
      "cogs_pct_of_revenue": 0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 0.0
    }
  },
  "_other_keys": "pnl, cash_flow, balance_sheet, months, yearly, error, generated_at, created_at (see below)"
}
```
*capture: `recalculated.json`*

With `refresh_actuals`:

```json
{
  "data": {
    "id": "4d76f19e-629e-41e8-ad71-ce225fddea4e",
    "status": "complete",
    "horizon_months": 36,
    "currency": "NGN",
    "stale": false,
    "overrides": {
      "capex": [
        {
          "label": "Laptops",
          "month": "2027-01",
          "amount_minor": 4800000
        }
      ],
      "ap_days": 20,
      "ar_days": 30,
      "one_offs": [
        {
          "label": "Legal setup",
          "month": "2026-12",
          "amount_minor": 2000000
        }
      ],
      "hiring_plan": [
        {
          "role": "Backend engineer",
          "start_month": "2027-01",
          "monthly_cost_minor": 1500000
        }
      ],
      "cogs_pct_of_revenue": 25.0,
      "depreciation_months": 24,
      "monthly_revenue_growth_pct": 6.0
    },
    "recalculated_at": "2026-10-24T12:15:04.948123Z",
    "balance_check": {
      "balanced": true,
      "max_abs_difference_minor": 0
    },
    "assumptions": {
      "ap_days": 20,
      "ar_days": 30,
      "narrative": "[stub-llm] narrative",
      "revenue_source": "actuals",
      "revenue_0_minor": 2000000,
      "cogs_pct_of_revenue": 25.0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 6.0,
      "capex": [
        {
          "label": "Laptops",
          "month": "2027-01",
          "amount_minor": 4800000
        }
      ],
      "one_offs": [
        {
          "label": "Legal setup",
          "month": "2026-12",
          "amount_minor": 2000000
        }
      ],
      "hiring_plan": [
        {
          "role": "Backend engineer",
          "start_month": "2027-01",
          "monthly_cost_minor": 1500000
        }
      ],
      "depreciation_months": 24,
      "monthly_costs_0_minor": 10666667
    },
    "ai_assumptions": {
      "ap_days": 0,
      "ar_days": 0,
      "narrative": "[stub-llm] narrative",
      "revenue_source": "actuals",
      "revenue_0_minor": 2000000,
      "cogs_pct_of_revenue": 0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 0.0
    }
  },
  "_other_keys": "pnl, cash_flow, balance_sheet, months, yearly, error, generated_at, created_at (see below)"
}
```
*capture: `recalculated_refresh_actuals.json`*

After clearing three edits (`{"hiring_plan": null, "one_offs": null, "capex": null}`), `overrides` keeps only the remaining
edits and `stale` is true again:

```json
{
  "data": {
    "id": "4d76f19e-629e-41e8-ad71-ce225fddea4e",
    "status": "complete",
    "horizon_months": 36,
    "currency": "NGN",
    "stale": true,
    "overrides": {
      "ap_days": 20,
      "ar_days": 30,
      "cogs_pct_of_revenue": 25.0,
      "depreciation_months": 24,
      "monthly_revenue_growth_pct": 6.0
    },
    "recalculated_at": "2026-10-24T12:15:04.948123Z",
    "balance_check": {
      "balanced": true,
      "max_abs_difference_minor": 0
    },
    "assumptions": {
      "ap_days": 20,
      "ar_days": 30,
      "narrative": "[stub-llm] narrative",
      "revenue_source": "actuals",
      "revenue_0_minor": 2000000,
      "cogs_pct_of_revenue": 25.0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 6.0,
      "depreciation_months": 24,
      "monthly_costs_0_minor": 10666667
    },
    "ai_assumptions": {
      "ap_days": 0,
      "ar_days": 0,
      "narrative": "[stub-llm] narrative",
      "revenue_source": "actuals",
      "revenue_0_minor": 2000000,
      "cogs_pct_of_revenue": 0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 0.0
    }
  },
  "_other_keys": "pnl, cash_flow, balance_sheet, months, yearly, error, generated_at, created_at (see below)"
}
```
*capture: `assumptions_cleared_edits.json`*

**`warnings`** (array of strings, empty by default, present on the Recalculate response only): saved edits whose month is outside the projection, which matters after `refresh_actuals` moves the window (for example `"One-off 'Audit' (2025-03) is outside the projection 2026-11 to 2029-10 and is not included."`). Show them next to the editor; the edits stay saved. Unit-tested, not captured live.

**A model generated before this release** (12 months, no stored starting figures): the first Recalculate upgrades it to 36
months, keeping its original month labels (unit-tested, not captured live).

## UX consequences the FE must surface
1. **`stale: true` means the statements on screen do not include the founder's latest edits.** Show it and offer Recalculate;
   do not show edited inputs next to old numbers without saying so.
2. **`balance_check.balanced: false` must be shown as an error** ("The balance sheet does not balance by X") and never hidden.
   It is expected to be `true` always; it is reported rather than thrown so a bug cannot blank the page.
3. **Fixed assets and depreciation appear only when the founder adds capex.** Totals and the check line always appear.
4. Revenue in the model starts from the **trailing 3 months of money in, which includes any `Funding` inflow in that
   window** (existing behaviour, see Follow-ups). A raise recorded this month will inflate starting revenue; the editor's
   `revenue_0_minor` field is how the founder corrects it.
5. The AI note and §1.4 financial disclaimer still apply; edits do not make the model advice.

---

# Part 2: Mono bank link (sandbox only)

## What the feature is
On **Finance, Integrations** a new card, **Bank (Mono)**, sits beside Paystack/Flutterwave. The founder clicks Connect, the
FE opens **Mono Connect** (their JS widget, with the public key we return), the founder links their bank, and the widget
hands the FE a one-time **`code`**. The FE sends it to us; we exchange it and store the linked account **encrypted**. Bank
lines then flow into the ledger as `source: "bank"` rows (credits = money in, debits = money out, **no category**, so they
show under "Needs a category"). A Paystack payout seen in the bank is tagged **`Paystack payout`** and left out of revenue.

| Route | Who | Purpose |
|---|---|---|
| `GET /finance/integrations` | F, TM, AC | now returns a third provider entry `mono` |
| `POST /finance/integrations/mono/connect` | **founder only** | body `{"code": "<from Mono Connect>"}` |
| `POST /finance/integrations/mono/sync` | F, TM, AC | import now (same as Paystack) |
| `POST /finance/integrations/mono/disconnect` | **founder only** | forget the link; imported rows stay |
| `POST /api/v1/webhooks/mono` | public, Mono only | not an FE route |

Do **not** call `POST /finance/integrations/mono/connect` with `secret_key`: Mono has no pasted key (it returns `422`).

## `GET /finance/integrations`: the new entry
The `providers` list is now `[paystack, flutterwave, mono]`. Each entry gained `kind` (`"payments"` or `"bank"`) and
`connect_config` (`null` for payments). Entry `mono`, nothing connected yet (other entries omitted):

```json
{
  "data": {
    "encryption_configured": true,
    "providers": [
      {
        "provider": "mono",
        "label": "Bank (Mono)",
        "key_hint": "Link your bank account with Mono Connect",
        "kind": "bank",
        "connection": null,
        "connect_config": {
          "configured": true,
          "public_key": "test_pk_e2e_stub_key",
          "sandbox": true
        }
      }
    ]
  }
}
```
*capture: `integrations_before.json`*

Traps:
- **A bank link and a payment provider can both be live.** Paystack's entry shows its connection and `mono` shows its own;
  each is looked up independently. (Paystack and Flutterwave still exclude each other.)
- `connect_config.public_key` is Mono's **public** key: pass it to the widget. `configured: false` means the server has no Mono
  keys yet: show the card as "Coming soon", do not offer Connect. `sandbox: true` means test banks only.
- `mode` on a connection is `"test"` for sandbox keys. Show a "Sandbox" tag.
- `key_last4` is the last 4 digits of the **bank account number**. The account id is never returned.
- `webhook_url` for Mono is the same global URL for everyone; it is informational (the founder does not paste it anywhere).

## Connect
Request: `{"code": "code_ok"}` (placeholder; a real code comes from the widget). Response `200`:

```json
{
  "data": {
    "id": "e3afbff2-e973-40d9-8d1c-25da6650340b",
    "provider": "mono",
    "status": "active",
    "mode": "test",
    "key_last4": "6789",
    "account_name": "Stub Bank · NovaPay Ltd",
    "last_sync_at": null,
    "last_error": null,
    "last_sync_summary": null,
    "webhook_url": "http://127.0.0.1:8011/api/v1/webhooks/mono",
    "created_at": "2026-10-24T12:15:05.096448Z"
  },
  "meta": null
}
```
*capture: `mono_connected.json`*

Errors:

```json
{
  "error": {
    "code": "BANK_LINK_REJECTED",
    "message": "That bank link expired or was not accepted. Start again.",
    "field_errors": []
  }
}
```
*capture: `mono_connect_bad_code.json`*

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "code",
        "message": "Field required"
      }
    ]
  }
}
```
*capture: `mono_connect_missing_code.json`*

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
*capture: `mono_connect_team_member_403.json`*

Other codes (unit-tested, **not captured live**): `503 MONO_NOT_CONFIGURED` (server has no Mono key),
`503 MONO_LIVE_DISABLED` (a non-sandbox key without `MONO_ALLOW_LIVE`), `503 FINANCE_CONNECTIONS_NOT_CONFIGURED`
(no encryption key), `502 PAYMENT_PROVIDER_UNAVAILABLE` (Mono down), `409 BANK_ACCOUNT_ALREADY_LINKED` (that bank account
belongs to another workspace), `409 BANK_LINK_CONFLICT` (a double-submit or simultaneous link; refresh and read the status). Connecting again replaces the link (audit action `replaced`); **one bank account per workspace**.

**Connecting does not import anything.** Mono prepares the data asynchronously, then sends our webhook, which queues a sync.
The FE should call **Sync now** once after connecting, and otherwise rely on the hourly sync.

## Sync
Same envelope as Paystack's: `{connection, summary}`. The summary has four new counters, `credits`, `debits`,
`paystack_payouts`, `paystack_unmatched` (always present, on every provider; zero when not applicable). First sync:

```json
{
  "data": {
    "summary": {
      "imported": 0,
      "duplicates": 0,
      "invoice_matched": 0,
      "skipped_currency": 0,
      "skipped_oversize": 0,
      "fees": 0,
      "refunds": 0,
      "credits": 2,
      "debits": 1,
      "paystack_payouts": 1,
      "paystack_unmatched": 0,
      "fetched": 3
    }
  }
}
```
*capture: `mono_sync.json`*

Second sync is idempotent (`duplicates` counts what was already there; `credits`/`debits` count only new rows):

```json
{
  "data": {
    "summary": {
      "imported": 0,
      "duplicates": 3,
      "invoice_matched": 0,
      "skipped_currency": 0,
      "skipped_oversize": 0,
      "fees": 0,
      "refunds": 0,
      "credits": 0,
      "debits": 0,
      "paystack_payouts": 0,
      "paystack_unmatched": 0,
      "fetched": 3
    }
  }
}
```
*capture: `mono_sync_again_idempotent.json`*

## What lands in the ledger (and D-FIN-10)
Ledger rows after the first sync, bank rows only (the journey had connected Paystack first, so the payout is recognised):

```json
{
  "data": {
    "transactions": [
      {
        "id": "552a07c6-15a4-44ba-8e73-500dc0b0243c",
        "date": "2026-10-01",
        "description": "PAYSTACK TRANSFER SETTLEMENT",
        "category": "Paystack payout",
        "amount_minor": 492500,
        "currency": "NGN",
        "direction": "in",
        "source": "bank",
        "created_at": "2026-10-24T12:15:05.126118Z",
        "updated_at": "2026-10-24T12:15:05.126118Z"
      },
      {
        "id": "45d2abf7-9d8c-4f42-b771-b36353458e78",
        "date": "2026-10-01",
        "description": "POS purchase office supplies",
        "category": null,
        "amount_minor": 250000,
        "currency": "NGN",
        "direction": "out",
        "source": "bank",
        "created_at": "2026-10-24T12:15:05.126118Z",
        "updated_at": "2026-10-24T12:15:05.126118Z"
      },
      {
        "id": "321cc631-b4fc-47f8-a521-5f83152683ff",
        "date": "2026-10-01",
        "description": "NIP transfer from ACME Ltd",
        "category": null,
        "amount_minor": 1500000,
        "currency": "NGN",
        "direction": "in",
        "source": "bank",
        "created_at": "2026-10-24T12:15:05.126118Z",
        "updated_at": "2026-10-24T12:15:05.126118Z"
      }
    ]
  },
  "meta": {
    "next_cursor": null,
    "total_estimate": 9
  }
}
```
*capture: `ledger_after_mono_sync.json`*

- `source: "bank"`, `category: null` for ordinary lines. **The ledger API does not return `external_ref`** (same as before).
- The credit has `category: "Paystack payout"` only when **both** its narration names Paystack **and** the ledger's Paystack charges of the 10 days before it add up to at least its amount (the journey syncs Paystack first so they do). A Paystack-narrated credit the charges cannot explain (a client paying by Paystack Transfer) stays ordinary income and is counted in the sync summary as `paystack_unmatched`; you may show "n bank credits mention Paystack but were counted as income". It is shown in the ledger, but it is **left out
  of cash on hand, money in, revenue and runway**, because the original Paystack charges are already counted. The founder
  can change its category to put it back (the only edit allowed on an imported row). **Show a "Paystack payout, not counted
  as income" note on those rows and add a "Paystack payout" option to the category Select.**
- Without Paystack charges in the ledger, nothing is tagged.
- After the sync the journey's cash on hand moved by exactly +1,500,000 −250,000 kobo; the 492,500 payout did not move it.
  Cash flow after sync (cut to the last two months):

```json
{
  "data": {
    "cash_on_hand": 75742500,
    "monthly_burn": 8085833,
    "monthly_revenue": 2666667,
    "runway_months": 9.4,
    "runway_low": false,
    "currency": "NGN",
    "by_month": [
      {
        "month": "2026-09",
        "inflow": 0,
        "outflow": 0,
        "net": 0
      },
      {
        "month": "2026-10",
        "inflow": 8000000,
        "outflow": 32257500,
        "net": -24257500
      }
    ]
  },
  "meta": null
}
```
*capture: `cash_flow_after_mono_sync.json`*

## Status, errors and webhooks
A bank link can become **`needs_attention`** (Mono says the founder must re-authorise, or rejects the saved link). `last_error`
carries the sentence to show, and the fix is **Connect again** (founder only). Journey capture after a
`reauthorisation_required` webhook (Mono entry only):

```json
{
  "data": {
    "providers": [
      {
        "provider": "mono",
        "label": "Bank (Mono)",
        "key_hint": "Link your bank account with Mono Connect",
        "kind": "bank",
        "connection": {
          "id": "e3afbff2-e973-40d9-8d1c-25da6650340b",
          "provider": "mono",
          "status": "needs_attention",
          "mode": "test",
          "key_last4": "6789",
          "account_name": "Stub Bank · NovaPay Ltd",
          "last_sync_at": "2026-10-24T12:15:05.292422Z",
          "last_error": "Your bank needs you to re-authorise access. Reconnect your bank to resume syncing.",
          "last_sync_summary": {
            "fees": 0,
            "debits": 0,
            "credits": 0,
            "fetched": 3,
            "refunds": 0,
            "imported": 0,
            "duplicates": 3,
            "invoice_matched": 0,
            "paystack_payouts": 0,
            "skipped_currency": 0,
            "skipped_oversize": 0,
            "paystack_unmatched": 0
          },
          "webhook_url": "http://127.0.0.1:8011/api/v1/webhooks/mono",
          "created_at": "2026-10-24T12:15:05.096448Z"
        },
        "connect_config": {
          "configured": true,
          "public_key": "test_pk_e2e_stub_key",
          "sandbox": true
        }
      }
    ]
  }
}
```
*capture: `integrations_needs_attention.json`*

A Mono `401` during a sync means Mono refused **our platform key**, not the founder's link: the connection stays `active` with the "could not reach Mono" message (operators are alerted in logs). A `403` marks the link `needs_attention`.

Mono's own outage during a sync leaves the status `active` and sets `last_error` to "We could not reach Mono. We will retry
automatically." (unit-tested). Reconnect after `needs_attention`: `mono_reconnected.json` (same shape as connect).

Disconnect (founder only; forgets the account, imported rows stay, a later Sync returns `404`):

```json
{
  "data": {
    "id": "e3afbff2-e973-40d9-8d1c-25da6650340b",
    "provider": "mono",
    "status": "disconnected",
    "mode": "test",
    "key_last4": "6789",
    "account_name": "Stub Bank · NovaPay Ltd",
    "last_sync_at": "2026-10-24T12:15:05.292422Z",
    "last_error": null,
    "last_sync_summary": {
      "fees": 0,
      "debits": 0,
      "credits": 0,
      "fetched": 3,
      "refunds": 0,
      "imported": 0,
      "duplicates": 3,
      "invoice_matched": 0,
      "paystack_payouts": 0,
      "skipped_currency": 0,
      "skipped_oversize": 0,
      "paystack_unmatched": 0
    },
    "webhook_url": "http://127.0.0.1:8011/api/v1/webhooks/mono",
    "created_at": "2026-10-24T12:15:05.096448Z"
  },
  "meta": null
}
```
*capture: `mono_disconnected.json`*

Webhook (for reference; **Mono calls this, the FE never does**). The inbound body we read carries the account as `data.account._id` (and `data.meta.data_status`); **that payload shape is from Mono's docs, not captured live** (the journey posts a body of that shape itself). Missing or wrong `mono-webhook-secret` header:

```json
{
  "error": {
    "code": "INVALID_SIGNATURE",
    "message": "Invalid signature.",
    "field_errors": []
  }
}
```
*capture: `webhook_missing_secret_401.json`*

A valid event is acknowledged with `{"received": true}` and any unknown account or event type is acknowledged too:

```json
{
  "data": {
    "received": true
  },
  "meta": null
}
```
*capture: `webhook_account_updated.json`*

---

## Verification table
| Behaviour | Status |
|---|---|
| Model: 36 months, yearly rollup, Balance check row = 0, `balance_check` | **verified live** (stub LLM for assumptions) |
| Model: PATCH assumptions, `stale`, Recalculate, determinism, `refresh_actuals`, clearing edits | **verified live** |
| Model: 422 shapes, mentor 403, team member allowed | **verified live** (422 and mentor 403 captured; team member 200 asserted) |
| Model: `409 MODEL_NOT_READY`, cross-workspace 404, legacy 12-month upgrade | unit-tested only |
| Model: first sync of real AI assumptions | not part of this change (stub LLM) |
| Mono: connect, encrypted at rest, last4, sandbox mode, founder-only 403 | **verified live** against the **stub** (the DB ciphertext check is unit-tested) |
| Mono: sync, idempotency, `bank` rows, Paystack payout tagging, cash exclusion | **verified live** against the **stub** |
| Mono: webhook secret check (401), reauthorisation flag, account-updated acknowledged | **verified live** (account-updated's queued sync ran in the worker) |
| Mono: error codes `MONO_NOT_CONFIGURED`, `MONO_LIVE_DISABLED`, `BANK_ACCOUNT_ALREADY_LINKED`, `BANK_LINK_CONFLICT`, outage and 401-vs-403 handling, payouts unexplained by charges, webhook replay, recalculate `warnings` | unit-tested only |
| Mono webhook body shape (`data.account._id`) | **from Mono's docs, not captured live** |
| **Real Mono sandbox** (code exchange, account lookup, transactions, `amount` unit, pagination, webhook payload and header) | **NOT verified**: needs the real sandbox keys; shapes follow Mono's v2 docs and are untested against Mono. Check in the FE walk and tell BE if any differ |
| Mono Connect widget handoff | not verified (FE) |
