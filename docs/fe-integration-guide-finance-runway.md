# FE Integration Guide — Finance Hub: Runway & Scenarios (Module 12, Slice 2)

> **Provenance.** `e2e/test_finance_runway.py::test_finance_runway_journey` was run against the
> live app (`bash scripts/e2e_run.sh`, 60 e2e passed) and every **response body** below is pasted
> **verbatim** from the captures it wrote to `e2e/_captures/finance_runway/`. Nothing was written
> from the schema, the DTOs or memory. Two honest edits, both labelled where they occur: (a) the
> `by_month` array is 12 points per scenario; in every block except the first
> (`runway_default.json`) it is cut to its first 2 points and the cut is marked with a
> `"<... N more points elided ...>"` string that is **not** in the real response; (b) nothing else
> is trimmed. The harness captures **responses only**, so the **request** bodies are the exact JSON
> the e2e journey sends (read from `e2e/test_finance_runway.py`), not a capture. Anything **not**
> exercised live is marked **NOT VERIFIED LIVE** inline, and the table at the end says where each
> shape came from. All `id` / `startup_id` / `created_at` values in the captures are throwaway
> values from a disposable e2e database — treat them as **placeholders**. Auth headers below are
> written with placeholders (`<access_token>`, `<workspace-id>`); never a captured token.

Slice 2 turns the Slice-1 cash-flow numbers into a forward-looking **Runway** screen: three
projected scenarios (base / best / worst) over a 12-month horizon, driven by three editable
assumptions, plus a **low-runway alert** (in-app notification) and an **informational Health Score
signal**.

Base path: `/api/v1/finance`. Every route requires `Authorization: Bearer <access_token>` and
`X-Workspace-Id: <workspace-id>` (the workspace id from `GET /auth/me` → `active_workspace_id`).
Every success response is the standard envelope `{"data": …, "meta": null}`.

**Access: `founder`, `team_member` or `accountant`.** Any other membership role gets **403** on
both routes. **NOT VERIFIED LIVE** — unit-verified only
(`tests/api/test_finance_runway.py::test_runway_rbac_forbidden` for every forbidden role,
`::test_accountant_can_get_runway`). Unauthenticated → 401 (captured, see §3).

---

## Read this first — five things that will produce a wrong-looking screen

### (1) UNIT TRAP: the Runway screen shows ₦ THOUSANDS, the API speaks MINOR UNITS (kobo)

The FE Runway screen exposes **hiring spend** and **one-off costs** as ₦ **thousands** (an input
of `20` means ₦20,000). The API **stores and returns kobo** (integer minor units), exactly like the
rest of Finance. **The FE must convert on both sides, or every number will be off by 100 000×.**

| Direction | Formula | Example (from the captures) |
|---|---|---|
| FE input → API | `minor = thousands × 100 000` (thousands × 1 000 × 100 kobo) | FE `20` → `"hiring_spend_minor": 2000000` |
| FE input → API | | FE `50` → `"one_off_costs_minor": 5000000` |
| API → FE display | `thousands = minor ÷ 100 000` | `2000000` → `20` (₦20,000) |
| API → ₦ display | `naira = minor ÷ 100` | `2000000` → ₦20,000.00 |

`mom_growth_percent` is a plain integer percent (no conversion): `5` means 5% month-on-month.
The pair `20` ↔ `2000000` and `50` ↔ `5000000` is what the e2e journey sends in §2; the conversion
rule itself is arithmetic, not something the API enforces — the API only ever sees minor units.
Every other money field in this guide (`cash_on_hand`, `monthly_burn`, `monthly_revenue`,
`avg_net_burn_minor`, `by_month[].cash_balance`, `by_month[].net`) is **minor units** too: divide
by 100 for ₦.

### (2) `runway_months` and `cash_out_date` are `null` when the balance never reaches zero in the 12-month horizon — NOT "unknown"

Per scenario, **three states** (all three captured live):

| State | `runway_months` | `cash_out_date` | Meaning | Capture |
|---|---|---|---|---|
| Cash runs out inside the horizon | number, 1 decimal (e.g. `11.3`) | `"YYYY-MM"` | Show "N months, cash-out {month}" | `runway_default.json` → `base` |
| **Never runs out inside the 12 months** | **`null`** | **`null`** | Healthy over the horizon — render "12+ months" / "cash-positive", **not** "N/A" or an error | `runway_default.json` → `best`; all three scenarios in `runway_cash_positive.json` |
| **Already out of cash now** | **`0.0`** | current month (`"2026-09"` in the capture) | Critical — show "out of cash" | all three scenarios in `runway_out_of_cash.json` |

Details the FE must not get wrong:

* `null` does **not** mean "profitable today". `best` in `runway_default.json` is `null` while its
  first months are still burning (`net` is negative for 11 of 12 points) — cash bottoms out and
  recovers before reaching zero. It means only "the projected balance never hits zero within 12
  months".
* `0.0` is **not** falsy-safe in JS: `if (!scenario.runway_months)` treats `0.0` and `null` the
  same, and they are opposites. Test `=== null` and `=== 0` explicitly.
* `runway_months` is rounded to 1 decimal for display; `cash_out_date` is derived from the
  unrounded value, so `runway_months: 0.9` can have `cash_out_date` = the **current** month (see
  `runway_low.json`).
* When out of cash, `by_month` can still show the balance climbing back **above zero** later in
  the 12 points (see the `base.by_month` tail in `runway_out_of_cash.json`) because the projection
  assumes the trailing revenue continues. `runway_months: 0.0` is the authoritative "out of cash
  now" signal; do not infer it from `by_month`.

### (3) One call gives everything — assumptions + baseline + all three scenarios

`GET /finance/runway` **and** `PUT /finance/runway/assumptions` return the **same
`RunwayResponse` shape**. The PUT response is the fully recomputed runway under the new
assumptions — **do not issue a follow-up GET after a PUT**; render the PUT response directly
(the follow-up GET in the e2e journey returns an identical `assumptions` block and is only there to
prove persistence).

```
data
├── assumptions   { mom_growth_percent, hiring_spend_minor, one_off_costs_minor }   ← editable, persisted
├── baseline      { cash_on_hand, monthly_burn, monthly_revenue, currency }         ← actuals, NOT affected by assumptions
├── horizon_months  12
└── scenarios     { base, best, worst }
        └── each: { runway_months, cash_out_date, avg_net_burn_minor, by_month[12] }
                                                         └── { month, cash_balance, net }
```

Field-nesting traps:

* `baseline.monthly_burn` is the **Slice-1 net burn, clamped at 0**. It is **not** `scenarios.*.avg_net_burn_minor`.
* `baseline.*` is identical before and after a PUT (proven live: `runway_default.json` vs
  `assumptions_put.json`). Only `scenarios` move.
* `by_month[0].month` is the month **after** the current month (`"2026-10"` when today is in
  2026-09). `by_month[i].cash_balance` is the projected **end-of-month** balance. Always 12 points.
* `avg_net_burn_minor` is the mean projected burn over the 12 months, **clamped at 0** — it is `0`
  for a scenario that is net-positive on average (see `runway_cash_positive.json`).
* `runway_months` on this screen will **not** equal `runway_months` on `GET /finance/cash-flow`.
  Cash-flow's number is `cash_on_hand ÷ trailing net burn`; the scenarios are a month-by-month
  projection that applies the saved assumptions, the scenario's growth/cost multipliers, and the
  one-off cost. In `runway_low.json` cash-flow says `1.2` while `base` says `0.9` (assumptions of
  8% growth / ₦20k hiring / ₦50k one-off were still saved). Show each number under its own label.

### (4) Assumptions persist per workspace and silently shape later projections

The three assumptions are saved server-side and reused by every later `GET`. A user who set
`hiring_spend_minor` last week will see it applied today. Always render the current `assumptions`
block back into the inputs from the response — never assume zeros on load.

Fresh workspace (never saved): the API returns all three as `0` (proven live —
`runway_default.json`). `PUT` is a **partial update**: send only the fields being changed; omitted
fields keep their saved value (proven live — `assumptions_put_partial.json`).

### (5) Scenario definitions are fixed server-side (not editable)

Relative to the saved assumptions: **base** = as saved; **best** = growth +10 percentage points and
costs × 0.90; **worst** = growth −10 points (floored at 0%) and costs × 1.15. These offsets are
constants in `app/services/finance/scenario_config.py` — **NOT VERIFIED LIVE as constants**, but
the resulting ordering (`worst` shorter than `base`; `best` never worse) is visible in every
capture and asserted by the e2e journey.

---

## 1. `GET /finance/runway`

Auth: as above. No query params, no body.

### 1a. Fresh workspace, default assumptions — `runway_default.json` (full, verbatim)

Seed: cash raise ₦1,000,000 (4 months ago), ₦90,000 customer invoice + ₦210,000 payroll (last
month), ₦90,000 hosting (today). `cash_on_hand` = 79 000 000 kobo = ₦790,000.

**200 OK**

```json
{
  "data": {
    "assumptions": {
      "mom_growth_percent": 0,
      "hiring_spend_minor": 0,
      "one_off_costs_minor": 0
    },
    "baseline": {
      "cash_on_hand": 79000000,
      "monthly_burn": 7000000,
      "monthly_revenue": 3000000,
      "currency": "NGN"
    },
    "horizon_months": 12,
    "scenarios": {
      "base": {
        "runway_months": 11.3,
        "cash_out_date": "2027-08",
        "avg_net_burn_minor": 7000000,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": 72000000,
            "net": -7000000
          },
          {
            "month": "2026-11",
            "cash_balance": 65000000,
            "net": -7000000
          },
          {
            "month": "2026-12",
            "cash_balance": 58000000,
            "net": -7000000
          },
          {
            "month": "2027-01",
            "cash_balance": 51000000,
            "net": -7000000
          },
          {
            "month": "2027-02",
            "cash_balance": 44000000,
            "net": -7000000
          },
          {
            "month": "2027-03",
            "cash_balance": 37000000,
            "net": -7000000
          },
          {
            "month": "2027-04",
            "cash_balance": 30000000,
            "net": -7000000
          },
          {
            "month": "2027-05",
            "cash_balance": 23000000,
            "net": -7000000
          },
          {
            "month": "2027-06",
            "cash_balance": 16000000,
            "net": -7000000
          },
          {
            "month": "2027-07",
            "cash_balance": 9000000,
            "net": -7000000
          },
          {
            "month": "2027-08",
            "cash_balance": 2000000,
            "net": -7000000
          },
          {
            "month": "2027-09",
            "cash_balance": -5000000,
            "net": -7000000
          }
        ]
      },
      "best": {
        "runway_months": null,
        "cash_out_date": null,
        "avg_net_burn_minor": 3119322,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": 73300000,
            "net": -5700000
          },
          {
            "month": "2026-11",
            "cash_balance": 67930000,
            "net": -5370000
          },
          {
            "month": "2026-12",
            "cash_balance": 62923000,
            "net": -5007000
          },
          {
            "month": "2027-01",
            "cash_balance": 58315300,
            "net": -4607700
          },
          {
            "month": "2027-02",
            "cash_balance": 54146830,
            "net": -4168470
          },
          {
            "month": "2027-03",
            "cash_balance": 50461513,
            "net": -3685317
          },
          {
            "month": "2027-04",
            "cash_balance": 47307664,
            "net": -3153849
          },
          {
            "month": "2027-05",
            "cash_balance": 44738430,
            "net": -2569234
          },
          {
            "month": "2027-06",
            "cash_balance": 42812273,
            "net": -1926157
          },
          {
            "month": "2027-07",
            "cash_balance": 41593500,
            "net": -1218773
          },
          {
            "month": "2027-08",
            "cash_balance": 41152850,
            "net": -440650
          },
          {
            "month": "2027-09",
            "cash_balance": 41568135,
            "net": 415285
          }
        ]
      },
      "worst": {
        "runway_months": 9.3,
        "cash_out_date": "2027-06",
        "avg_net_burn_minor": 8500000,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": 70500000,
            "net": -8500000
          },
          {
            "month": "2026-11",
            "cash_balance": 62000000,
            "net": -8500000
          },
          {
            "month": "2026-12",
            "cash_balance": 53500000,
            "net": -8500000
          },
          {
            "month": "2027-01",
            "cash_balance": 45000000,
            "net": -8500000
          },
          {
            "month": "2027-02",
            "cash_balance": 36500000,
            "net": -8500000
          },
          {
            "month": "2027-03",
            "cash_balance": 28000000,
            "net": -8500000
          },
          {
            "month": "2027-04",
            "cash_balance": 19500000,
            "net": -8500000
          },
          {
            "month": "2027-05",
            "cash_balance": 11000000,
            "net": -8500000
          },
          {
            "month": "2027-06",
            "cash_balance": 2500000,
            "net": -8500000
          },
          {
            "month": "2027-07",
            "cash_balance": -6000000,
            "net": -8500000
          },
          {
            "month": "2027-08",
            "cash_balance": -14500000,
            "net": -8500000
          },
          {
            "month": "2027-09",
            "cash_balance": -23000000,
            "net": -8500000
          }
        ]
      }
    }
  },
  "meta": null
}
```

### 1b. Cash-positive — `runway_cash_positive.json`

After a ₦3,000,000 inflow: `monthly_burn` is `0`, so **all three scenarios** are `runway_months:
null`, `cash_out_date: null`. (`by_month` cut to 2 points for this doc.)

**200 OK**

```json
{
  "data": {
    "assumptions": {
      "mom_growth_percent": 8,
      "hiring_spend_minor": 2000000,
      "one_off_costs_minor": 5000000
    },
    "baseline": {
      "cash_on_hand": 328000000,
      "monthly_burn": 0,
      "monthly_revenue": 103000000,
      "currency": "NGN"
    },
    "horizon_months": 12,
    "scenarios": {
      "base": {
        "runway_months": null,
        "cash_out_date": null,
        "avg_net_burn_minor": 0,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": 405240000,
            "net": 82240000
          },
          {
            "month": "2026-11",
            "cash_balance": 496379200,
            "net": 91139200
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      },
      "best": {
        "runway_months": null,
        "cash_out_date": null,
        "avg_net_burn_minor": 0,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": 418240000,
            "net": 95240000
          },
          {
            "month": "2026-11",
            "cash_balance": 535357200,
            "net": 117117200
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      },
      "worst": {
        "runway_months": null,
        "cash_out_date": null,
        "avg_net_burn_minor": 0,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": 392950000,
            "net": 69950000
          },
          {
            "month": "2026-11",
            "cash_balance": 462900000,
            "net": 69950000
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      }
    }
  },
  "meta": null
}
```

### 1c. Low runway — `runway_low.json`

After a ₦500,000 outflow: `cash_on_hand` 29 000 000, `monthly_burn` 23 666 667. `cash_out_date`
of `"2026-09"` is the **current** month. (`by_month` cut to 2 points for this doc.)

**200 OK**

```json
{
  "data": {
    "assumptions": {
      "mom_growth_percent": 8,
      "hiring_spend_minor": 2000000,
      "one_off_costs_minor": 5000000
    },
    "baseline": {
      "cash_on_hand": 29000000,
      "monthly_burn": 23666667,
      "monthly_revenue": 3000000,
      "currency": "NGN"
    },
    "horizon_months": 12,
    "scenarios": {
      "base": {
        "runway_months": 0.9,
        "cash_out_date": "2026-09",
        "avg_net_burn_minor": 23542843,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": -1426667,
            "net": -25426667
          },
          {
            "month": "2026-11",
            "cash_balance": -26594134,
            "net": -25167467
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      },
      "best": {
        "runway_months": 1.1,
        "cash_out_date": "2026-10",
        "avg_net_burn_minor": 15695334,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": 1540000,
            "net": -22460000
          },
          {
            "month": "2026-11",
            "cash_balance": -20282800,
            "net": -21822800
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      },
      "worst": {
        "runway_months": 0.8,
        "cash_out_date": "2026-09",
        "avg_net_burn_minor": 29666667,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": -5666667,
            "net": -29666667
          },
          {
            "month": "2026-11",
            "cash_balance": -35333334,
            "net": -29666667
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      }
    }
  },
  "meta": null
}
```

### 1d. Out of cash — `runway_out_of_cash.json`

`cash_on_hand` is **negative**; every scenario has `runway_months: 0.0`. Note `baseline.monthly_revenue`
is `103000000` — see §5 trap "fundraises count as revenue". (`by_month` cut to 2 points for this doc.)

**200 OK**

```json
{
  "data": {
    "assumptions": {
      "mom_growth_percent": 8,
      "hiring_spend_minor": 2000000,
      "one_off_costs_minor": 5000000
    },
    "baseline": {
      "cash_on_hand": -72000000,
      "monthly_burn": 57333333,
      "monthly_revenue": 103000000,
      "currency": "NGN"
    },
    "horizon_months": 12,
    "scenarios": {
      "base": {
        "runway_months": 0.0,
        "cash_out_date": "2026-09",
        "avg_net_burn_minor": 0,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": -128093333,
            "net": -51093333
          },
          {
            "month": "2026-11",
            "cash_balance": -170287466,
            "net": -42194133
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      },
      "best": {
        "runway_months": 0.0,
        "cash_out_date": "2026-09",
        "avg_net_burn_minor": 0,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": -101760000,
            "net": -24760000
          },
          {
            "month": "2026-11",
            "cash_balance": -104642800,
            "net": -2882800
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      },
      "worst": {
        "runway_months": 0.0,
        "cash_out_date": "2026-09",
        "avg_net_burn_minor": 83383333,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": -160383333,
            "net": -83383333
          },
          {
            "month": "2026-11",
            "cash_balance": -243766666,
            "net": -83383333
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      }
    }
  },
  "meta": null
}
```

### 1e. Unauthenticated — `runway_unauthenticated_401.json`

Request sent with **no** `Authorization` header.

**401**

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

---

## 2. `PUT /finance/runway/assumptions`

Auth: as above. **Partial update** — every field optional; at least the fields you send are saved.
Returns the same shape as `GET /finance/runway`, recomputed (see §3 of "Read this first").

| Field | Type | Range (enforced) | FE unit |
|---|---|---|---|
| `mom_growth_percent` | integer | `0..1000` | percent, no conversion |
| `hiring_spend_minor` | integer | `0..2147483647` | **kobo** (FE shows ₦ thousands — ÷ 100 000) |
| `one_off_costs_minor` | integer | `0..2147483647` | **kobo** (FE shows ₦ thousands — ÷ 100 000) |

### 2a. Set all three — `assumptions_put.json`

FE inputs: growth `5`, hiring `20` (₦ thousands), one-off `50` (₦ thousands). **Request** (as sent by
the e2e journey):

```json
{
  "mom_growth_percent": 5,
  "hiring_spend_minor": 2000000,
  "one_off_costs_minor": 5000000
}
```

**200 OK** (`by_month` cut to 2 points for this doc)

```json
{
  "data": {
    "assumptions": {
      "mom_growth_percent": 5,
      "hiring_spend_minor": 2000000,
      "one_off_costs_minor": 5000000
    },
    "baseline": {
      "cash_on_hand": 79000000,
      "monthly_burn": 7000000,
      "monthly_revenue": 3000000,
      "currency": "NGN"
    },
    "horizon_months": 12,
    "scenarios": {
      "base": {
        "runway_months": 9.1,
        "cash_out_date": "2027-06",
        "avg_net_burn_minor": 7821754,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": 65150000,
            "net": -8850000
          },
          {
            "month": "2026-11",
            "cash_balance": 56457500,
            "net": -8692500
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      },
      "best": {
        "runway_months": null,
        "cash_out_date": null,
        "avg_net_burn_minor": 2662021,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": 66450000,
            "net": -7550000
          },
          {
            "month": "2026-11",
            "cash_balance": 59417500,
            "net": -7032500
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      },
      "worst": {
        "runway_months": 7.0,
        "cash_out_date": "2027-04",
        "avg_net_burn_minor": 10500000,
        "by_month": [
          {
            "month": "2026-10",
            "cash_balance": 63500000,
            "net": -10500000
          },
          {
            "month": "2026-11",
            "cash_balance": 53000000,
            "net": -10500000
          },
          "<... 10 more points elided in this doc; not part of the real response ...>"
        ]
      }
    }
  },
  "meta": null
}
```

The follow-up `GET /finance/runway` (`runway_after_assumptions.json`) returned the identical
`assumptions` block and the same `baseline` as `runway_default.json`, with different `scenarios`
(base `runway_months` moved from `11.3` to `9.1`) — asserted by the e2e journey, capture on disk
for anyone who needs the full body.

### 2b. Partial update — `assumptions_put_partial.json`

**Request**

```json
{ "mom_growth_percent": 8 }
```

**200 OK** — only `assumptions` shown; the `hiring_spend_minor` and `one_off_costs_minor` values
from 2a were kept. Full body is `assumptions_put_partial.json`.

```json
{
  "mom_growth_percent": 8,
  "hiring_spend_minor": 2000000,
  "one_off_costs_minor": 5000000
}
```

### 2c. Validation errors (all captured live)

The error envelope is `{"error": {"code", "message", "field_errors": [{"field", "message"}]}}`.
`code` is always `VALIDATION_ERROR`, `message` is always `"Please check the highlighted fields."`.

**Negative value** — request `{"hiring_spend_minor": -1}` — `assumptions_put_negative_422.json` — **422**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "hiring_spend_minor",
        "message": "Input should be greater than or equal to 0"
      }
    ]
  }
}
```

**Growth above 1000** — request `{"mom_growth_percent": 1001}` —
`assumptions_put_growth_over_1000_422.json` — **422**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "mom_growth_percent",
        "message": "Input should be less than or equal to 1000"
      }
    ]
  }
}
```

**Explicit `null`** — request `{"mom_growth_percent": null}` — `assumptions_put_null_422.json` — **422**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "Value error, mom_growth_percent may not be null"
      }
    ]
  }
}
```

> **Trap:** for the explicit-`null` case `field_errors[0].field` is the **empty string `""`** — the
> field name is only inside `message`. Do not key inline errors on `field` for this case; show it as
> a form-level message. To clear an input, send `0`, never `null` (omit the field to leave it alone).

Rejected writes change nothing — asserted live (assumptions after the three 422s equal those after
2b).

**NOT VERIFIED LIVE:** an over-int32 `hiring_spend_minor` / `one_off_costs_minor` (> 2 147 483 647)
also returns **422** — unit-verified (`test_put_over_int32_hiring_422`), no capture. Cross-workspace
isolation of assumptions is unit-verified (`test_cross_tenant_assumptions_isolated`).

---

## 3. The low-runway notification — `finance.runway.low`

**What the user experiences.** When the startup transitions into **low runway**, every active
member of the workspace gets one in-app notification titled **"Your runway is running low"**.
Surface it through the existing notifications feed (`GET /notifications`, unread badge via
`GET /notifications/unread-count`, and the realtime stream if the FE already uses it) — this slice
adds **no new endpoint** for it.

**Live proof.** Pushing the workspace into low runway with an outflow transaction produced exactly
this feed (`notifications_after_low.json`, verbatim):

```json
{
  "data": {
    "notifications": [
      {
        "id": "35006915-7106-484b-9095-0a5e71301bbb",
        "type": "finance.runway.low",
        "title": "Your runway is running low",
        "body": "",
        "data": {
          "currency": "NGN",
          "startup_id": "924e3cba-2f8b-4714-8f52-f65301801e19",
          "monthly_burn": 23666667,
          "runway_months": 1.2
        },
        "read": false,
        "created_at": "2026-09-29T14:22:26.410227+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

and `GET /notifications/unread-count` (`notifications_unread_count_after_low.json`):

```json
{
  "data": {
    "unread": 1
  },
  "meta": null
}
```

The triggering `POST /finance/transactions` response is `transaction_pushing_low.json` (unchanged
Slice-1 transaction shape).

**Rules the FE must build for:**

| Rule | Detail | Status |
|---|---|---|
| **When it fires** | "Low" = the workspace is burning (`monthly_burn > 0`) **and** (`runway_months` is `null` — out of cash — **or** `runway_months < 6`). This is exactly the `runway_low` flag on `GET /finance/cash-flow`. | predicate verified live (`cash_flow_low.json` shows `"runway_low": true`) |
| **Fire once per transition** | It fires when the workspace *enters* low. Further transactions while it is still low do **not** create another notification. | verified live (e2e asserts 1 notification after a second outflow while still low) |
| **Recovery re-arms it** | When runway recovers (e.g. an inflow makes the workspace cash-positive), the alert re-arms; the next time the workspace falls into low it fires **again**. | verified live — `notifications_after_rearm.json` shows two `finance.runway.low` rows: the newer with `"runway_months": null` (out of cash), the older with `1.2` |
| **What triggers the check** | Creating, updating or deleting a transaction (`POST` / `PATCH` / `DELETE /finance/transactions`). Changing assumptions via `PUT` does **not** trigger it (assumptions are not part of the predicate). | POST verified live; PATCH/DELETE unit-verified (`test_update_and_delete_transaction_run_the_alert_hook`); PUT-does-not-trigger is source-derived |
| **Recipients** | All active members of the workspace (no actor exclusion — the person who entered the transaction gets it too). | verified live for the founder; other-member fan-out unit-verified (`test_transition_creates_in_app_notification_for_active_members`) |
| **Channel** | **In-app only.** There is no email category for `finance.runway.low`, so no email is sent and it does not appear in the email-preference matrix. | source-derived (`app/services/notifications/categories.py` has no entry); NOT VERIFIED LIVE |
| **Payload** | `type: "finance.runway.low"`, `title: "Your runway is running low"`, `body: ""` (empty), `data: { currency, startup_id, monthly_burn, runway_months }`. **`data.runway_months` is `null` when out of cash** and `data.monthly_burn` is minor units. | verified live (both shapes captured) |

**Deep link.** `body` is empty by design — the FE renders copy and the link from `type` + `data`
(route to the Runway screen). Do not display `data.startup_id`.

**Do not rely on the notification for current state.** It is an edge event, not a status. The
banner on the Runway/Cash-flow screens should be driven by `runway_low` (cash-flow) or the
scenario values, which are always current; the notification is the "you just crossed the line"
nudge.

---

## 4. Health Score signal — `money.runway_live`

`GET /health-score/dimensions/money` now includes one **informational, non-scoring** signal in
`signals` once the workspace has at least one transaction. Nothing changes about how the Health
Score is computed — `contribution` is always `0.0` and the dimension `score` is not affected.

Live values (three captures): low runway (`health_dimension_money_low.json`, verbatim)

```json
{
  "data": {
    "key": "money",
    "label": "Financial",
    "score": null,
    "band": null,
    "signals": [
      {
        "key": "money.runway_live",
        "value": 1.2,
        "contribution": 0.0,
        "source_ref": "finance:cash-flow"
      }
    ],
    "trend": [],
    "recommendations": []
  },
  "meta": null
}
```

cash-positive (`health_dimension_money_cash_positive.json`) — only the signal shown:

```json
[
  {
    "key": "money.runway_live",
    "value": 0.0,
    "contribution": 0.0,
    "source_ref": "finance:cash-flow"
  }
]
```

out of cash (`health_dimension_money_out_of_cash.json`) — only the signal shown:

```json
[
  {
    "key": "money.runway_live",
    "value": 0.0,
    "contribution": 0.0,
    "source_ref": "finance:cash-flow"
  }
]
```

### THE VALUE-0 AMBIGUITY — read this before rendering the signal

`value` is **runway in months** (rounded to 2 decimals, capped at 999.99). **`value: 0.0` means
EITHER "cash-positive / not burning" OR "out of cash" — two opposite states.** The two captures
above are byte-identical apart from the file they came from; the signal alone cannot tell them
apart.

**Rules:**

1. **Key on `key == "money.runway_live"`** to find the signal. Do not find it by position.
2. **Never read `value: 0` as danger, or as healthy.** It is neither.
3. To render health/danger, **ignore the value and use `GET /finance/cash-flow`** (`runway_low`,
   `monthly_burn`, `cash_on_hand`) or the runway scenarios. Use the signal only as a display row
   when `value > 0` (a real number of months, e.g. `1.2`), and hide or suppress it at `0`.
4. The signal is **display-only**: `contribution` is `0.0` and `source_ref` is
   `"finance:cash-flow"`. Do not add it to any score arithmetic.

Other behaviours, all visible in the captures:

* The signal **can exist while `score` and `band` are `null`** — i.e. before the kickoff
  assessment is completed (`"score": null, "band": null`, `trend: []`, `recommendations: []`). Do
  not assume a signal implies a score.
* The FE must tolerate the signal being **absent** — before the first transaction there is none
  (unit-verified: `test_no_transactions_means_no_signal_and_no_crash`; NOT VERIFIED LIVE).
* It refreshes whenever a transaction is created/updated/deleted, and is rebuilt on a Health Score
  recompute (unit-verified: `test_transaction_endpoints_maintain_runway_signal`,
  `test_recompute_rebuilds_runway_signal_after_blanket_delete`).

---

## 5. Other traps carried from Slice 1 that hit this screen

* **Fundraises count as revenue.** `baseline.monthly_revenue` (and therefore every scenario's
  revenue line) counts *all* inflows in the trailing 3 months, including a raise. After the ₦3,000,000
  inflow in the capture, `monthly_revenue` is `103000000` and even the "out of cash" scenarios'
  `by_month` climb back positive. This is a known gap (revenue-vs-financing split, tracked as a
  follow-up) — the FE cannot correct it; do not present `monthly_revenue` as recurring revenue
  without that caveat.
* **Trailing-3-calendar-month average (÷ 3).** Young startups are diluted (lower burn, longer
  runway). Same as the Slice-1 guide (`docs/fe-integration-guide-finance-cashflow.md`).
* **Future-dated transactions are excluded** from `cash_on_hand` and burn; multi-currency is **not
  converted** (single-currency workspaces only).
* **Empty workspace reads as "out of cash" — detect it yourself.** With no transactions
  `GET /finance/runway` still returns 200 with a zeroed baseline and the default assumptions
  (unit-verified: `test_get_runway_default_no_settings_row`). By the projection rules a starting
  balance of 0 is "out of cash now", so every scenario's `runway_months` is **`0.0`**
  (source-derived; the math-layer rule is unit-verified by
  `test_zero_cash_is_out_of_cash_now_in_every_scenario`; the empty-workspace response values
  themselves are **NOT VERIFIED LIVE**). Do **not** show "out of cash" to a workspace that simply
  has no data: check `baseline.cash_on_hand == 0 && baseline.monthly_revenue == 0 &&
  baseline.monthly_burn == 0` first and show the empty state.

---

## 6. Suggested FE behaviour (state machine)

| Workspace state | Detect via | Runway screen |
|---|---|---|
| No data yet | `baseline.cash_on_hand == 0 && baseline.monthly_revenue == 0 && baseline.monthly_burn == 0` | Empty state: prompt to add transactions |
| Healthy over horizon | scenario `runway_months === null` | "12+ months" |
| Cash-out within horizon | `runway_months > 0` | "N months", cash-out `cash_out_date` |
| Low (< 6 months, burning) | `GET /finance/cash-flow` → `runway_low` | Danger banner (also drives the notification) |
| Out of cash | scenario `runway_months === 0` | Critical state |

No polling is needed: nothing on this screen changes unless the user changes a transaction or an
assumption, both of which return fresh data (transactions → re-`GET /finance/runway`; assumptions →
render the PUT response). Refetch `GET /notifications/unread-count` after any transaction write to
pick up a new `finance.runway.low` immediately (it is created synchronously in the same request).

---

## 7. Verification table

| # | Behaviour | Status | Source |
|---|---|---|---|
| 1 | `GET /finance/runway` shape: assumptions + baseline + `horizon_months: 12` + 3 scenarios × 12 `by_month` | **Verified live** | `runway_default.json` |
| 2 | Default assumptions are all `0` for a never-saved workspace | **Verified live** | `runway_default.json` |
| 3 | `PUT` set-all → 200, recomputed full payload, assumptions echoed | **Verified live** | `assumptions_put.json` |
| 4 | Follow-up `GET` reflects saved assumptions; `baseline` unchanged; scenarios changed | **Verified live** | `runway_after_assumptions.json` (asserted in e2e) |
| 5 | `PUT` is partial (omitted fields keep saved value) | **Verified live** | `assumptions_put_partial.json` |
| 6 | Runway `null` / `cash_out_date` `null` when balance never hits zero (single scenario, and all three) | **Verified live** | `runway_default.json` (best), `runway_cash_positive.json` |
| 7 | Runway `0.0` when already out of cash, all scenarios | **Verified live** | `runway_out_of_cash.json` |
| 8 | 422 on negative, on growth > 1000, on explicit `null` (incl. `field: ""` trap) | **Verified live** | the three `assumptions_put_*_422.json` |
| 9 | 422 on over-int32 hiring/one-off | Unit only | `test_put_over_int32_hiring_422` |
| 10 | 401 unauthenticated | **Verified live** | `runway_unauthenticated_401.json` |
| 11 | 403 for non-finance roles; accountant allowed | Unit only | `test_runway_rbac_forbidden`, `test_accountant_can_get_runway` |
| 12 | `finance.runway.low` notification appears on entering low runway (payload, title, unread) | **Verified live** | `notifications_after_low.json`, `notifications_unread_count_after_low.json` |
| 13 | Fires once (no repeat while still low) | **Verified live** (e2e assertion, no separate capture) | `test_finance_runway_journey` |
| 14 | Recovery re-arms; second fire with `runway_months: null` | **Verified live** | `notifications_after_rearm.json` |
| 15 | PATCH / DELETE of a transaction also run the alert check | Unit only | `test_update_and_delete_transaction_run_the_alert_hook` |
| 16 | Other active members receive it | Unit only | `test_transition_creates_in_app_notification_for_active_members` |
| 17 | In-app only, no email category | Source-derived (not exercised) | `app/services/notifications/categories.py` |
| 18 | `money.runway_live` signal: `value` = months, `contribution` 0.0, `source_ref` `finance:cash-flow` | **Verified live** | `health_dimension_money_low.json` |
| 19 | `value: 0.0` in both cash-positive and out-of-cash states | **Verified live** | `health_dimension_money_cash_positive.json`, `health_dimension_money_out_of_cash.json` |
| 20 | Signal present while `score`/`band` are `null` (no assessment) | **Verified live** | `health_dimension_money_low.json` |
| 21 | No signal before the first transaction | Unit only | `test_no_transactions_means_no_signal_and_no_crash` |
| 22 | Scenario offsets (best +10pp/×0.90, worst −10pp/×1.15) | Source-derived; ordering verified live | `scenario_config.py`, all `runway_*` captures |
| 23 | Empty-workspace `GET /finance/runway` → 200 zeroed baseline; scenario `runway_months` `0.0` | Unit only (200 + shape); `0.0` source-derived | `test_get_runway_default_no_settings_row`, `test_zero_cash_is_out_of_cash_now_in_every_scenario` |
