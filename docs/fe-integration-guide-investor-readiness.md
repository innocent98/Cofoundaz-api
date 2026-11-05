# FE Integration Guide: Investor Readiness (Module 15, slices A and B: score, history, deck review)

Every request and response body below is pasted **verbatim** from live captures taken by
`e2e/test_investor_readiness.py` against a real server (`e2e/_captures/investor_readiness/*.json`).
Long bodies are trimmed with `// ...` and a note, never re-shaped. Placeholder values in the
captures (`Placeholder Co`, `example.com`) are fake. **What is not covered live is listed in the
verification table at the end and marked inline.**

Scope: this guide covers the **Score & Gaps** page, the **History** page and (section 12) the **Deck Review** page.
Mock Q&A and Narrative Check have no API yet (slices D, C); keep them on the mock until their guides exist.
Design: `cofoundaz/docs/superpowers/specs/2026-11-05-investor-readiness-design.md`. Product calls
D-IR-1..7 were accepted 2026-11-05.

Base path `/api/v1/readiness`. Every route needs `Authorization: Bearer <token>` and
`X-Workspace-Id: <workspace uuid>` (from `GET /auth/me` → `data.active_workspace_id`).
Success bodies are `{"data": ..., "meta": null}`; errors are `{"error": {"code", "message", "field_errors"}}`.

## 1. What the page is

A 0 to 100 score for "would an investor write a cheque today", from six dimensions (Team, Traction,
Market, Product, Financials, Story), computed from data the founder already has in the other hubs.
**No AI is called to compute it** and nothing on this page costs AI budget. Every dimension has signals; each
signal carries its real value, its target for the founder's stage, and, when below target, a gap note
with the real numbers plus a deep link to fix it.

## 2. Who can call what

| Role | `GET` score, history, snapshot, inputs | `POST` recompute, `PUT` inputs |
|---|---|---|
| founder | 200 | 200 |
| team_member | 200 (same numbers as the founder) | **403** |
| mentor, business_consultant, accountant, legal_advisor, investor | **403** | **403** |

403 body, verified live three ways: a user with no membership in the workspace, a **team member calling
`POST /score/recompute`**, and a **mentor calling `GET /score`** (all three bodies are identical):

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

The remaining roles are unit-tested (same guard, `tests/api/test_investor_readiness_api.py`).
FE: hide the Investor Readiness entry for roles that get 403, and show the read-only state (no
"Recalculate", no market-size form) to the team member. An investor sees a score only through a Funding
data-room share (not built here).

## 3. `GET /readiness/score`

Computes on first open, then serves the cached row; recomputes on read when the row is older than 15
minutes, and after relevant events (see section 8). First open of a brand-new workspace:

```json
{
  "data": {
    "score": 1,
    "band": "not_yet",
    "verdict": "Not yet, and that's fine. Here's the shortest path.",
    "ready": false,
    "capped": false,
    "raw_score": 1,
    "dimensions": [
      {
        "key": "team",
        "label": "Team",
        "score": 8,
        "weight": 15,
        "signals": [
          {
            "key": "team.members",
            "href": "/team",
            "note": "1 active member(s). Investors back teams: invite a co-founder or first hire.",
            "unit": "count",
            "label": "Active team members",
            "score": 33.3,
            "value": 1.0,
            "target": 3
          },
          {
            "key": "team.founder_agreement",
            "href": "/documents",
            "note": "No founder agreement yet. Diligence asks for it first.",
            "unit": "step",
            "label": "Founder agreement drafted and signed",
            "score": 0.0,
            "value": 0.0,
            "target": 2
          },
          {
            "key": "team.advisors",
            "href": "/team",
            "note": "No advisor or mentor attached. Invite one whose name an investor will know.",
            "unit": "count",
            "label": "Advisor or mentor on the team",
            "score": 0.0,
            "value": 0.0,
            "target": 1
          },
          {
            "key": "team.activity_7d",
            "href": "/dashboard",
            "note": "Only 0 of 20 workspace actions this week. Investors read momentum.",
            "unit": "events",
            "label": "Workspace activity (7 days)",
            "score": 0.0,
            "value": 0.0,
            "target": 20
          }
        ]
      },
      {
        "key": "traction",
        "label": "Traction",
        "score": 0,
        "weight": 25,
        "signals": [
          {
            "key": "traction.revenue",
            "href": "/finance",
            "note": "Revenue averages ₦0 a month against ₦1,000,000 for your stage. Log income in Finance.",
            "unit": "NGN",
            "label": "Monthly revenue (3 month average)",
            "score": 0.0,
            "value": 0.0,
            "target": 1000000
          },
          {
            "key": "traction.customers",
            "href": "/sales/pipeline",
            "note": "0 paying customer(s) against 10. Close a deal or record a paid invoice.",
            "unit": "count",
            "label": "Paying customers",
            "score": 0.0,
            "value": 0.0,
            "target": 10
          },
          {
            "key": "traction.pipeline",
            "href": "/sales",
            "note": "0 open deal(s) against 10. Add real prospects to the pipeline.",
            "unit": "count",
            "label": "Open deals in the pipeline",
            "score": 0.0,
            "value": 0.0,
            "target": 10
          },
          {
            "key": "traction.demand_proof",
            "href": "/validation",
            "note": "0 signups or survey answers against 100. Run a smoke test or a survey.",
            "unit": "count",
            "label": "Demand proof (signups and survey answers)",
            "score": 0.0,
            "value": 0.0,
            "target": 100
          }
        ]
      },
      // ... 4 more dimension objects (market, product, financials, story), same shape
    ],
    "must_haves": [
      {
        "key": "financial_model",
        "met": false,
        "href": "/finance/model",
        "label": "A financial model",
        "reason": "Generate a financial model in Finance."
      },
      {
        "key": "pitch_deck_reviewed",
        "met": false,
        "href": "/investor-readiness/deck",
        "label": "A reviewed pitch deck",
        "reason": "Upload your pitch deck PDF and run a deck review."
      },
      {
        "key": "traction_proof",
        "met": false,
        "href": "/validation",
        "label": "One proof point: revenue, a won deal, or a validated assumption with demand",
        "reason": "Record revenue, win a deal, or validate an assumption with real demand."
      }
    ],
    "gaps": [
      {
        "href": "/finance/model",
        "lift": 13,
        "note": "You have no financial model. Investors expect one.",
        "dimension": "financials",
        "signal_key": "financials.model"
      },
      {
        "href": "/finance",
        "lift": 10,
        "note": "Revenue averages ₦0 a month against ₦1,000,000 for your stage. Log income in Finance.",
        "dimension": "traction",
        "signal_key": "traction.revenue"
      },
      {
        "href": "/finance",
        "lift": 7,
        "note": "0 of 10 transactions logged this month. Keep the books current.",
        "dimension": "financials",
        "signal_key": "financials.books"
      },
      {
        "href": "/investor-readiness",
        "lift": 5,
        "note": "Add TAM, SAM and SOM with a source link so the size is checkable.",
        "dimension": "market",
        "signal_key": "market.size"
      },
      {
        "href": "/sales/pipeline",
        "lift": 5,
        "note": "0 paying customer(s) against 10. Close a deal or record a paid invoice.",
        "dimension": "traction",
        "signal_key": "traction.customers"
      }
    ],
    "computed_at": "2026-11-05T14:17:36.696981+00:00",
    "config_version": 1
  },
  "meta": null
}
```

After the founder created a pitch deck, a one-pager, a canvas (customer segment + solution), a competitor,
₦300,000 of recorded sales and a sourced market size (same workspace, same journey):

```json
{
  "data": {
    "score": 29,
    "band": "not_yet",
    "verdict": "Not yet, and that's fine. Here's the shortest path.",
    "ready": false,
    "capped": false,
    "raw_score": 29,
    "dimensions": [
      {
        "key": "team",
        "label": "Team",
        "score": 8,
        "weight": 15,
        "signals": [
          {
            "key": "team.members",
            "href": "/team",
            "note": "1 active member(s). Investors back teams: invite a co-founder or first hire.",
            "unit": "count",
            "label": "Active team members",
            "score": 33.3,
            "value": 1.0,
            "target": 3
          },
          {
            "key": "team.founder_agreement",
            "href": "/documents",
            "note": "No founder agreement yet. Diligence asks for it first.",
            "unit": "step",
            "label": "Founder agreement drafted and signed",
            "score": 0.0,
            "value": 0.0,
            "target": 2
          },
          {
            "key": "team.advisors",
            "href": "/team",
            "note": "No advisor or mentor attached. Invite one whose name an investor will know.",
            "unit": "count",
            "label": "Advisor or mentor on the team",
            "score": 0.0,
            "value": 0.0,
            "target": 1
          },
          {
            "key": "team.activity_7d",
            "href": "/dashboard",
            "note": "Only 0 of 20 workspace actions this week. Investors read momentum.",
            "unit": "events",
            "label": "Workspace activity (7 days)",
            "score": 0.0,
            "value": 0.0,
            "target": 20
          }
        ]
      },
      {
        "key": "traction",
        "label": "Traction",
        "score": 12,
        "weight": 25,
        "signals": [
          {
            "key": "traction.revenue",
            "href": "/finance",
            "note": "Revenue averages ₦300,000 a month against ₦1,000,000 for your stage. Log income in Finance.",
            "unit": "NGN",
            "label": "Monthly revenue (3 month average)",
            "score": 30.0,
            "value": 300000.0,
            "target": 1000000
          },
          {
            "key": "traction.customers",
            "href": "/sales/pipeline",
            "note": "0 paying customer(s) against 10. Close a deal or record a paid invoice.",
            "unit": "count",
            "label": "Paying customers",
            "score": 0.0,
            "value": 0.0,
            "target": 10
          },
          {
            "key": "traction.pipeline",
            "href": "/sales",
            "note": "0 open deal(s) against 10. Add real prospects to the pipeline.",
            "unit": "count",
            "label": "Open deals in the pipeline",
            "score": 0.0,
            "value": 0.0,
            "target": 10
          },
          {
            "key": "traction.demand_proof",
            "href": "/validation",
            "note": "0 signups or survey answers against 100. Run a smoke test or a survey.",
            "unit": "count",
            "label": "Demand proof (signups and survey answers)",
            "score": 0.0,
            "value": 0.0,
            "target": 100
          }
        ]
      },
      // ... 4 more dimension objects (market, product, financials, story), same shape
    ],
    "must_haves": [
      {
        "key": "financial_model",
        "met": false,
        "href": "/finance/model",
        "label": "A financial model",
        "reason": "Generate a financial model in Finance."
      },
      {
        "key": "pitch_deck_reviewed",
        "met": false,
        "href": "/investor-readiness/deck",
        "label": "A reviewed pitch deck",
        "reason": "Upload your pitch deck PDF and run a deck review."
      },
      {
        "key": "traction_proof",
        "met": true,
        "href": "/validation",
        "label": "One proof point: revenue, a won deal, or a validated assumption with demand",
        "reason": null
      }
    ],
    "gaps": [
      {
        "href": "/finance/model",
        "lift": 8,
        "note": "You have no financial model. Investors expect one.",
        "dimension": "financials",
        "signal_key": "financials.model"
      },
      {
        "href": "/finance",
        "lift": 7,
        "note": "Revenue averages ₦300,000 a month against ₦1,000,000 for your stage. Log income in Finance.",
        "dimension": "traction",
        "signal_key": "traction.revenue"
      },
      {
        "href": "/sales/pipeline",
        "lift": 5,
        "note": "0 paying customer(s) against 10. Close a deal or record a paid invoice.",
        "dimension": "traction",
        "signal_key": "traction.customers"
      },
      {
        "href": "/investor-readiness/deck",
        "lift": 4,
        "note": "Your deck has not been reviewed yet.",
        "dimension": "story",
        "signal_key": "story.deck_review"
      },
      {
        "href": "/validation/experiments",
        "lift": 4,
        "note": "0 experiment(s) run. Launch a test that shows people want it.",
        "dimension": "product",
        "signal_key": "product.experiments"
      }
    ],
    "computed_at": "2026-11-05T14:17:36.994486+00:00",
    "config_version": 1
  },
  "meta": null
}
```

Field notes:

- `score` is what to show. `raw_score` is the number before the cap. `capped: true` means the raw
  score reached 70 or more but a must-have is missing, so `score` is held at 69. **Show the reason**: list the
  `must_haves` with `met: false` ("Capped at 69: {label}") with `reason` as the explanation and a link to `href`.
  `reason` is `null` once a must-have is met. `capped: true` was not reached live (see verification table).
- **The reviewed-deck must-have** has the key `pitch_deck_reviewed` ("A reviewed pitch deck"). It is met only by a
  **finished deck review** (section 12), not by a file in Documents. Its `href` is `/investor-readiness/deck` and its `reason`
  says what to do. Until a deck is reviewed no workspace can pass 69 (D-IR-2); say so next to the gauge.
- `band` is `not_yet` (under 40), `getting_close` (40 to 69) or `ready` (70+). `verdict` is the headline copy for the band:
  render it verbatim. `ready: true` is what shows the **Open Funding Hub** button (route `/funding`).
- `dimensions` is always 6 entries in this order: team, traction, market, product, financials, story.
  Dimension `score` is 0 to 100, or **`null` when nothing can be judged yet**: show "Not enough data yet". `null` was
  not produced live (every dimension had at least one signal), so this is from the serializer, not a capture.
- A signal that does not apply yet is **absent**, not zero. In the captures `financials.runway` is missing until there
  is cash-flow data, and `team.assessment` is missing until the assessment is completed. Do not assume a fixed signal list.
- `gaps` is the ranked "Priority gaps to close" list: at most 5, at most 2 per dimension, biggest points first.
  `lift` is the estimated overall points gained by closing it. The mock's `actionRoute` is called `href` here, the mock's
  `note` is `note`. `href` values are FE routes; two are worth knowing: `"/investor-readiness"` (market size, the form on this page)
  and `"/validation"`.
- **Nesting trap:** signal `note` is `null` once a signal reaches its target, but a gap in `gaps` always has a string note.
- `value` and `target` are numbers in the signal's `unit` (`count`, `%`, `months`, `NGN` whole naira, `step` where 0, 1 or 2 are
  stages of completion). Format with the unit; never show `step` values.
- `computed_at` is when the score was last computed, an ISO timestamp with offset.
- The first call after a long gap returns a fresh score, so the number can differ from what the founder saw yesterday
  without them doing anything (data in finance, validation and sales is read, not pushed). This is expected.

## 4. `POST /readiness/score/recompute` (founder)

Forces a recompute. Returns the same body as `GET /score`. At most once a minute.

```json
{
  "data": {
    "score": 29,
    "band": "not_yet",
    "verdict": "Not yet, and that's fine. Here's the shortest path.",
    "ready": false,
    "capped": false,
    "raw_score": 29,
    "dimensions": [
      {
        "key": "team",
        "label": "Team",
        "score": 8,
        "weight": 15,
        "signals": [
          {
            "key": "team.members",
            "href": "/team",
            "note": "1 active member(s). Investors back teams: invite a co-founder or first hire.",
            "unit": "count",
            "label": "Active team members",
            "score": 33.3,
            "value": 1.0,
            "target": 3
          },
          {
            "key": "team.founder_agreement",
            "href": "/documents",
            "note": "No founder agreement yet. Diligence asks for it first.",
            "unit": "step",
            "label": "Founder agreement drafted and signed",
            "score": 0.0,
            "value": 0.0,
            "target": 2
          },
          {
            "key": "team.advisors",
            "href": "/team",
            "note": "No advisor or mentor attached. Invite one whose name an investor will know.",
            "unit": "count",
            "label": "Advisor or mentor on the team",
            "score": 0.0,
            "value": 0.0,
            "target": 1
          },
          {
            "key": "team.activity_7d",
            "href": "/dashboard",
            "note": "Only 0 of 20 workspace actions this week. Investors read momentum.",
            "unit": "events",
            "label": "Workspace activity (7 days)",
            "score": 0.0,
            "value": 0.0,
            "target": 20
          }
        ]
      },
      {
        "key": "traction",
        "label": "Traction",
        "score": 12,
        "weight": 25,
        "signals": [
          {
            "key": "traction.revenue",
            "href": "/finance",
            "note": "Revenue averages ₦300,000 a month against ₦1,000,000 for your stage. Log income in Finance.",
            "unit": "NGN",
            "label": "Monthly revenue (3 month average)",
            "score": 30.0,
            "value": 300000.0,
            "target": 1000000
          },
          {
            "key": "traction.customers",
            "href": "/sales/pipeline",
            "note": "0 paying customer(s) against 10. Close a deal or record a paid invoice.",
            "unit": "count",
            "label": "Paying customers",
            "score": 0.0,
            "value": 0.0,
            "target": 10
          },
          {
            "key": "traction.pipeline",
            "href": "/sales",
            "note": "0 open deal(s) against 10. Add real prospects to the pipeline.",
            "unit": "count",
            "label": "Open deals in the pipeline",
            "score": 0.0,
            "value": 0.0,
            "target": 10
          },
          {
            "key": "traction.demand_proof",
            "href": "/validation",
            "note": "0 signups or survey answers against 100. Run a smoke test or a survey.",
            "unit": "count",
            "label": "Demand proof (signups and survey answers)",
            "score": 0.0,
            "value": 0.0,
            "target": 100
          }
        ]
      },
      // ... 4 more dimension objects (market, product, financials, story), same shape
    ],
    "must_haves": [
      {
        "key": "financial_model",
        "met": false,
        "href": "/finance/model",
        "label": "A financial model",
        "reason": "Generate a financial model in Finance."
      },
      {
        "key": "pitch_deck_reviewed",
        "met": false,
        "href": "/investor-readiness/deck",
        "label": "A reviewed pitch deck",
        "reason": "Upload your pitch deck PDF and run a deck review."
      },
      {
        "key": "traction_proof",
        "met": true,
        "href": "/validation",
        "label": "One proof point: revenue, a won deal, or a validated assumption with demand",
        "reason": null
      }
    ],
    "gaps": [
      {
        "href": "/finance/model",
        "lift": 8,
        "note": "You have no financial model. Investors expect one.",
        "dimension": "financials",
        "signal_key": "financials.model"
      },
      {
        "href": "/finance",
        "lift": 7,
        "note": "Revenue averages ₦300,000 a month against ₦1,000,000 for your stage. Log income in Finance.",
        "dimension": "traction",
        "signal_key": "traction.revenue"
      },
      {
        "href": "/sales/pipeline",
        "lift": 5,
        "note": "0 paying customer(s) against 10. Close a deal or record a paid invoice.",
        "dimension": "traction",
        "signal_key": "traction.customers"
      },
      {
        "href": "/investor-readiness/deck",
        "lift": 4,
        "note": "Your deck has not been reviewed yet.",
        "dimension": "story",
        "signal_key": "story.deck_review"
      },
      {
        "href": "/validation/experiments",
        "lift": 4,
        "note": "0 experiment(s) run. Launch a test that shows people want it.",
        "dimension": "product",
        "signal_key": "product.experiments"
      }
    ],
    "computed_at": "2026-11-05T14:17:37.071387+00:00",
    "config_version": 1
  },
  "meta": null
}
```

Called again inside a minute:

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Recalculated a moment ago. Try again in a minute.",
    "field_errors": []
  }
}
```

FE: disable the button for a minute after success and show the `message` on 429.

## 5. `GET /readiness/history?range=30|90|365`

Default 90. A point is stored **only when the score, a dimension score or the cap state changes**, so points are
irregular; draw them on a real time axis. `markers` list band changes and "Cap lifted" (empty here: no band change).

```json
{
  "data": {
    "range": 30,
    "points": [
      {
        "at": "2026-11-05T14:17:36.696981+00:00",
        "score": 1,
        "band": "not_yet",
        "capped": false,
        "dimensions": {
          "team": 8,
          "story": 0,
          "market": 0,
          "product": 0,
          "traction": 0,
          "financials": 0
        }
      },
      {
        "at": "2026-11-05T14:17:36.994486+00:00",
        "score": 29,
        "band": "not_yet",
        "capped": false,
        "dimensions": {
          "team": 8,
          "story": 40,
          "market": 56,
          "product": 25,
          "traction": 12,
          "financials": 42
        }
      }
    ],
    "markers": [],
    "log": []
  },
  "meta": null
}
```

Bad range:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Unknown range.",
    "field_errors": [
      {
        "field": "range",
        "message": "Use 30, 90 or 365."
      }
    ]
  }
}
```

`log` lists finished deck reviews newest first (`type: "deck_review"`, `id`, `version`, `overall`); Mock Q&A sessions join it
with their slice, so keep the Q&A table on the mock until then. `markers` now also carries "Deck reviewed (vN)" (captured in section 12).

## 6. Market size: `GET` and `PUT /readiness/inputs`

TAM, SAM and SOM are the founder's own claim, so they only count when a source link is given. Amounts are
**minor units** of `currency` (kobo for NGN). `PUT` is a partial update: send only what changed.

Before anything is saved:

```json
{
  "data": {
    "tam_minor": null,
    "sam_minor": null,
    "som_minor": null,
    "currency": "NGN",
    "source_url": null
  },
  "meta": null
}
```

Saved:

```json
{
  "data": {
    "tam_minor": 500000000000,
    "sam_minor": 50000000000,
    "som_minor": 5000000000,
    "currency": "NGN",
    "source_url": "https://example.com/placeholder-market-report"
  },
  "meta": null
}
```

Validation (SAM larger than TAM in the same request). Note the empty `field` for this request-level rule:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "Value error, SAM cannot be larger than TAM."
      }
    ]
  }
}
```

Nesting is also checked against what is **already stored**: after saving TAM 100 and SAM 50, sending only `{"sam_minor": 500}` is
422 `VALIDATION_ERROR` with `field_errors: [{"field": "sam_minor", "message": "SAM cannot be larger than TAM."}]` (here `field` is
filled in; unit-tested, not captured live). `currency` accepts only `"NGN"` or `"USD"` (422 otherwise). A malformed `source_url` or
an unknown key also returns 422 `VALIDATION_ERROR` (unit-tested; same shape). A successful
`PUT` recomputes the score straight away, so refetch `GET /score` to show the new number.

## 7. `GET /readiness/snapshot`

The share-safe subset Funding shows investors: no gap notes, no signal detail.

```json
{
  "data": {
    "score": 29,
    "band": "not_yet",
    "ready": false,
    "dimensions": [
      {
        "key": "team",
        "label": "Team",
        "score": 8
      },
      {
        "key": "traction",
        "label": "Traction",
        "score": 12
      },
      {
        "key": "market",
        "label": "Market",
        "score": 56
      },
      {
        "key": "product",
        "label": "Product",
        "score": 25
      },
      {
        "key": "financials",
        "label": "Financials",
        "score": 42
      },
      {
        "key": "story",
        "label": "Story",
        "score": 40
      }
    ],
    "computed_at": "2026-11-05T14:17:37.071387+00:00"
  },
  "meta": null
}
```

## 8. When the score changes

Recomputed after these events (no more than once per 15 minutes per workspace): mission and roadmap completions, roadmap
re-plans, a completed signature, a member joining, a business plan, canvas edits, a won deal, a paid invoice, a financial model,
a validated or invalidated assumption, a new document. Inputs with no event are picked up on the next read after 15 minutes
and by a nightly pass. A bell notification **"Your investor readiness score improved to {score}"** goes to the founder only,
at most once a day, when the band improves or the score rises 5 or more points. Its `type` is `readiness.improved` and its module
is `readiness`: route the bell link to `/investor-readiness`. (Notification delivery is unit-tested, not captured live.)

## 9. Error shapes the FE must handle

No token:

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

Token but no `X-Workspace-Id` header:

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

Plus `429 RATE_LIMITED` (section 4), `422 VALIDATION_ERROR` (sections 5, 6) and `403 FORBIDDEN` (section 2).

## 10. UX consequences to surface

- **The cap.** A founder with a great raw score is shown 69, not 70. Always explain which must-have is missing, or the
  score looks broken.
- **A gap can be closed by work elsewhere.** The score updates after the work, not on this page: send them to the `href`
  and refetch on return.
- **Market size is a claim.** Say so on the form ("counts once you add a source").
- **Currency.** Revenue is scored in naira only. A workspace recording another currency has no `traction.revenue` signal
  (the signal is absent, not zero); do not show an empty row for it.
- **Stage matters.** Targets scale with the startup's stage, so the same revenue scores differently at "idea" and "launch".

## 11. Verification

| Behaviour | Verified |
|---|---|
| Score computed on first open, shape, six dimensions in order, must-haves, gaps | live |
| Score rises after real work in Documents, Business Builder, Finance and market-size input | live |
| Manual recompute, rate limit 429 | live |
| History points, default range, bad range 422 | live |
| Inputs empty, save, cross-field 422 | live |
| Snapshot shape (no gaps) | live |
| No token 401, no workspace header 422, other workspace 403 | live |
| Team member 403 on recompute; mentor 403 on score | live |
| Per-role 200/403 for all 7 roles on every route | unit-tested (`tests/api/test_investor_readiness_api.py`), not live |
| `pitch_deck_reviewed` must-have unmet with `reason`, then met after a review | live (section 12) |
| Nesting vs stored values, currency NGN/USD only | unit-tested; not captured live |
| `capped: true`, `ready: true`, `band` other than `not_yet` | unit-tested (scoring and service tests); not reached live because it needs a financial model plus much more data |
| Dimension `score: null` ("Not enough data yet") | not reachable with the current signals; shape from the serializer |
| History `markers` with entries | unit-tested; none occurred in the live run |
| `readiness.improved` notification and bell link | unit-tested; not captured live |
| Event-triggered and nightly recompute | unit-tested; live run showed the `PUT /inputs` recompute only |
| `team.assessment`, `financials.runway` signals present | not captured live (need an assessment and cash-flow history); present in unit tests |

## 12. Deck Review (slice B)

Routes (`/api/v1/readiness`): `POST /deck-reviews`, `GET /deck-reviews`, `GET /deck-reviews/{id}`, `GET /deck-thumbnails?token=`.
Access: the founder starts a review; founder and team member read; every other role gets 403 (captures in 12.7).
Captures are `e2e/_captures/readiness_deck/*.json`. **The language model was a canned fake in this run** (it returns fixed
scores, notes and fixes, so Narrative 7, Data clarity 5 and the fix texts are the fake's, not a real model's). What is live:
the API, the upload, the job queue, PDF text extraction, the layout check, thumbnail rendering and the score wiring.
What a real model changes: the Narrative and Data clarity numbers, `top_fixes` text and per-slide `notes`.

### 12.1 The flow

1. Upload the PDF with the existing Documents endpoint (`POST /documents/files`, folder "Pitch Deck"; 15 MB cap, malware scan).
2. `POST /readiness/deck-reviews` with `{"file_id": "<document file id>"}`.
3. Poll `GET /api/v1/jobs/{job_id}` every 2 s (stop after about 90 s) or poll the review itself; show
   "Reading your deck like a partner on a Monday morning…" while `status` is `queued` or `running`.
4. When the job is `succeeded`, **read the review's `status`**: a job can succeed with a review that `failed` (12.5).
5. `GET /readiness/deck-reviews/{id}` for the slide strip.

### 12.2 Start a review: 202, or 200 when the same file was already reviewed

```json
{
  "data": {
    "review": {
      "id": "52d82d23-6e3d-4aef-a690-418c476ebe50",
      "version": 1,
      "status": "queued",
      "error": null,
      "filename": "Acme Pay seed deck.pdf",
      "slide_count": null,
      "scores": null,
      "job_id": "3d3ee0de-e1ff-4bd9-8fa3-76e769d2ac49",
      "created_at": "2026-11-05T14:17:12.665161+00:00",
      "reviewed_at": null
    },
    "deduplicated": false
  },
  "meta": null
}
```

`review.status` is `queued`; `scores`, `slide_count` and `reviewed_at` are `null` until it is ready. The same bytes again (even re-uploaded
under another name) return **200**, the existing review, and queue nothing, so no tokens are spent:

```json
{
  "data": {
    "review": {
      "id": "52d82d23-6e3d-4aef-a690-418c476ebe50",
      "version": 1,
      "status": "queued",
      "error": null,
      "filename": "Acme Pay seed deck.pdf",
      "slide_count": null,
      "scores": null,
      "job_id": "3d3ee0de-e1ff-4bd9-8fa3-76e769d2ac49",
      "created_at": "2026-11-05T14:17:12.665161+00:00",
      "reviewed_at": null
    },
    "deduplicated": true
  },
  "meta": null
}
```

FE: a 200 with `deduplicated: true` means "this exact deck is already reviewed" and should open that review. A review that `failed` does
not block a retry: the same file starts a new review.

### 12.3 The job

Queued:

```json
{
  "data": {
    "id": "3d3ee0de-e1ff-4bd9-8fa3-76e769d2ac49",
    "type": "ai.readiness.deck_review",
    "status": "queued",
    "result": null,
    "error": null
  },
  "meta": null
}
```

After the worker ran it:

```json
{
  "data": {
    "id": "3d3ee0de-e1ff-4bd9-8fa3-76e769d2ac49",
    "type": "ai.readiness.deck_review",
    "status": "succeeded",
    "result": {
      "status": "ready",
      "review_id": "52d82d23-6e3d-4aef-a690-418c476ebe50"
    },
    "error": null
  },
  "meta": null
}
```

### 12.4 The review

`scores` has `narrative`, `data_clarity` and `design` (whole numbers 1 to 10), `overall` (their mean, one decimal) and `layout`. **Design is a
layout check, not a visual review** (D-IR-5): it is computed in code from slide count, words per slide, images and near-empty slides, and the model
never sees the slides. Label that tile "Layout check" or add a note; do not present it as an opinion on how the deck looks.
Slides are trimmed here to two; the others have the same shape:

```json
{
  "data": {
    "id": "52d82d23-6e3d-4aef-a690-418c476ebe50",
    "version": 1,
    "status": "ready",
    "error": null,
    "filename": "Acme Pay seed deck.pdf",
    "slide_count": 8,
    "scores": {
      "design": 10,
      "layout": {
        "slides": 8,
        "dense_slides": 0,
        "near_empty_slides": 0,
        "slides_with_images": 8,
        "avg_words_per_slide": 10.1
      },
      "overall": 7.3,
      "narrative": 7,
      "data_clarity": 5
    },
    "job_id": "3d3ee0de-e1ff-4bd9-8fa3-76e769d2ac49",
    "created_at": "2026-11-05T14:17:12.665161+00:00",
    "reviewed_at": "2026-11-05T14:17:13.427427+00:00",
    "top_fixes": [
      "Slide 4: cite the source for the market size.",
      "Slide 5: show cumulative revenue, not monthly.",
      "Slide 8: break the ask down by use of funds.",
      "Slide 2: quantify the problem for one customer.",
      "Slide 7: add advisors."
    ],
    "slides": [
      {
        "n": 1,
        "notes": "Strong hook.",
        "title": "Title",
        "words": 8,
        "images": 1,
        "thumbnail_url": "http://localhost:8000/api/v1/readiness/deck-thumbnails?token=eyJrIjoicmVhZGluZXNzLzhjNDA2ZDQ1LTliYzItNGI4MC04NGQwLWQ0MTIzZDAxMjA2MC81MmQ4MmQyMy02ZTNkLTRhZWYtYTY5MC00MThjNDc2ZWJlNTAvMS5wbmciLCJzIjoiOGM0MDZkNDUtOWJjMi00YjgwLTg0ZDAtZDQxMjNkMDEyMDYwIiwiZSI6MTc5Mzg4OTEzM30.sSmyxnL5e2ZQfARJV54HFDToxiz1LUJhlXsuy0DObq8"
      },
      {
        "n": 4,
        "notes": "SAM versus SOM is unclear.",
        "title": "Market",
        "words": 13,
        "images": 1,
        "thumbnail_url": "http://localhost:8000/api/v1/readiness/deck-thumbnails?token=eyJrIjoicmVhZGluZXNzLzhjNDA2ZDQ1LTliYzItNGI4MC04NGQwLWQ0MTIzZDAxMjA2MC81MmQ4MmQyMy02ZTNkLTRhZWYtYTY5MC00MThjNDc2ZWJlNTAvNC5wbmciLCJzIjoiOGM0MDZkNDUtOWJjMi00YjgwLTg0ZDAtZDQxMjNkMDEyMDYwIiwiZSI6MTc5Mzg4OTEzM30.wWJfLdbAvxJC-GqnYdPgdjyGdxsXGH5i2_8oA1S2f7A"
      },
      // ... 6 more slide objects, same shape (8 slides in this deck)
    ],
    "thumbnail_ttl_seconds": 900
  },
  "meta": null
}
```

Notes:

- `slides[].thumbnail_url` is a **signed, short-lived link** (`thumbnail_ttl_seconds`, 900). It needs no login header, so use it directly in
  `<img src>`. Refetch the review when it expires (a 404 on the image means expired or tampered). The host is the API's public URL setting
  (here `http://localhost:8000`, the local default). `thumbnail_url` is `null` for a slide that could not be rendered.
- `slides[].title` is the model's title, or the first line of the slide when the model gave none. `notes` can be an empty string. Only the
  first 30 slides are read and rendered; `slide_count` is the real page count.
- A bad token on the thumbnail route:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

- `reviewed_at` is the end of the job; the history point time can be a fraction earlier (it is the transaction start).

### 12.5 A review that fails

The job still ends `succeeded`; the review says why. `error` is one of `no_text` (a scanned or image-only deck, under 200 characters of text),
`unreadable` (damaged or password-protected PDF), `file_missing`, `over_budget` (today's AI allowance ran out) or `generation_failed`
(the model errored on every retry). Only `no_text` was captured live:

```json
{
  "data": {
    "id": "d4ebcc5c-cbf3-4f5c-b7a5-a856f8564ff4",
    "version": 3,
    "status": "failed",
    "error": "no_text",
    "filename": "scan.pdf",
    "slide_count": null,
    "scores": null,
    "job_id": "61c42f9d-82d8-43cd-9de0-ad452e2cf6b0",
    "created_at": "2026-11-05T14:17:14.025910+00:00",
    "reviewed_at": "2026-11-05T14:17:14.083709+00:00",
    "top_fixes": [],
    "slides": [],
    "thumbnail_ttl_seconds": 900
  },
  "meta": null
}
```

```json
{
  "data": {
    "id": "61c42f9d-82d8-43cd-9de0-ad452e2cf6b0",
    "type": "ai.readiness.deck_review",
    "status": "succeeded",
    "result": {
      "reason": "no_text",
      "status": "failed",
      "review_id": "d4ebcc5c-cbf3-4f5c-b7a5-a856f8564ff4"
    },
    "error": null
  },
  "meta": null
}
```

FE copy: `no_text` should say the deck has no readable text and to export it as a text PDF, not a scan. `over_budget` and
`generation_failed` should offer "try again later". When the allowance is already used up **before** a review starts, `POST` itself
returns **429** `AI_BUDGET_EXHAUSTED` (unit-tested, not captured live; same error shape as in section 9).

### 12.6 List and versions

Newest first, summaries only (no slides). "Re-review new version" is: upload the new PDF, `POST` again. `version` counts reviews per
workspace.

```json
{
  "data": {
    "reviews": [
      {
        "id": "b4e46f09-96f3-469c-b495-0ebb478f57e8",
        "version": 2,
        "status": "ready",
        "error": null,
        "filename": "Acme Pay seed deck v2.pdf",
        "slide_count": 9,
        "scores": {
          "design": 10,
          "layout": {
            "slides": 9,
            "dense_slides": 0,
            "near_empty_slides": 0,
            "slides_with_images": 9,
            "avg_words_per_slide": 9.6
          },
          "overall": 7.3,
          "narrative": 7,
          "data_clarity": 5
        },
        "job_id": "f0f4325c-d247-4d2a-af87-649f2ac0c979",
        "created_at": "2026-11-05T14:17:13.874438+00:00",
        "reviewed_at": "2026-11-05T14:17:13.913202+00:00"
      },
      {
        "id": "52d82d23-6e3d-4aef-a690-418c476ebe50",
        "version": 1,
        "status": "ready",
        "error": null,
        "filename": "Acme Pay seed deck.pdf",
        "slide_count": 8,
        "scores": {
          "design": 10,
          "layout": {
            "slides": 8,
            "dense_slides": 0,
            "near_empty_slides": 0,
            "slides_with_images": 8,
            "avg_words_per_slide": 10.1
          },
          "overall": 7.3,
          "narrative": 7,
          "data_clarity": 5
        },
        "job_id": "3d3ee0de-e1ff-4bd9-8fa3-76e769d2ac49",
        "created_at": "2026-11-05T14:17:12.665161+00:00",
        "reviewed_at": "2026-11-05T14:17:13.427427+00:00"
      }
    ]
  },
  "meta": null
}
```

Before any review:

```json
{
  "data": {
    "reviews": []
  },
  "meta": null
}
```

### 12.7 Score wiring

`must_haves` from `GET /score` before and after the first review (the rest of the body has the shape in section 3; Story and
the `pitch_deck_reviewed` entry are what changed):

**Before** (from `e2e/_captures/readiness_deck/must_have_before.json`, `must_haves` only):

```json
[
  {
    "key": "financial_model",
    "met": false,
    "href": "/finance/model",
    "label": "A financial model",
    "reason": "Generate a financial model in Finance."
  },
  {
    "key": "pitch_deck_reviewed",
    "met": false,
    "href": "/investor-readiness/deck",
    "label": "A reviewed pitch deck",
    "reason": "Upload your pitch deck PDF and run a deck review."
  },
  {
    "key": "traction_proof",
    "met": false,
    "href": "/validation",
    "label": "One proof point: revenue, a won deal, or a validated assumption with demand",
    "reason": "Record revenue, win a deal, or validate an assumption with real demand."
  }
]
```

**After:**

```json
[
  {
    "key": "financial_model",
    "met": false,
    "href": "/finance/model",
    "label": "A financial model",
    "reason": "Generate a financial model in Finance."
  },
  {
    "key": "pitch_deck_reviewed",
    "met": true,
    "href": "/investor-readiness/deck",
    "label": "A reviewed pitch deck",
    "reason": null
  },
  {
    "key": "traction_proof",
    "met": false,
    "href": "/validation",
    "label": "One proof point: revenue, a won deal, or a validated assumption with demand",
    "reason": "Record revenue, win a deal, or validate an assumption with real demand."
  }
]
```
The Story dimension gains two signals: `story.pitch_deck` (a deck exists or was reviewed) and `story.deck_review` (the latest review's `overall`
against a target of 6 for early stages, up to 8; 0 when there is no review). The score refreshes the moment a review finishes (it bypasses
the 15 minute throttle), so refetch `GET /score` when the job succeeds. History log and marker:

```json
{
  "data": {
    "range": 30,
    "points": [
      {
        "at": "2026-11-05T14:17:12.503312+00:00",
        "score": 1,
        "band": "not_yet",
        "capped": false,
        "dimensions": {
          "team": 8,
          "story": 0,
          "market": 0,
          "product": 0,
          "traction": 0,
          "financials": 0
        }
      },
      {
        "at": "2026-11-05T14:17:13.052605+00:00",
        "score": 7,
        "band": "not_yet",
        "capped": false,
        "dimensions": {
          "team": 8,
          "story": 60,
          "market": 0,
          "product": 0,
          "traction": 0,
          "financials": 0
        }
      }
    ],
    "markers": [
      {
        "at": "2026-11-05T14:17:13.427427+00:00",
        "label": "Deck reviewed (v1)"
      }
    ],
    "log": [
      {
        "type": "deck_review",
        "at": "2026-11-05T14:17:13.427427+00:00",
        "id": "52d82d23-6e3d-4aef-a690-418c476ebe50",
        "version": 1,
        "overall": 7.3
      }
    ]
  },
  "meta": null
}
```

### 12.8 Errors and roles

Not a PDF:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "A deck review needs a PDF.",
    "field_errors": []
  }
}
```

File id that does not exist (or belongs to another workspace, or is in the trash):

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

Malformed body:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "file_id",
        "message": "Input should be a valid UUID, invalid character: found `n` at 1"
      }
    ]
  }
}
```

Unknown review id, and another workspace's review (identical, by design):

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
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

A team member can read reviews but not start one; a mentor cannot read them:

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

A file that has not passed the malware scan returns `409 FILE_NOT_CLEAN` (unit-tested, not captured live: the local scanner marks files clean).

### 12.9 Cost

One review is one model call of roughly 10k tokens against the workspace's 100k a day, shared with the rest of the app. The same bytes never
spend twice. Warn the user before "Re-review" if they re-upload an unchanged file (it will come back as a 200 dedupe, which costs nothing).

### 12.10 Deck verification

| Behaviour | Verified |
|---|---|
| Upload, queue (202), job poll, ready review, scores, top fixes, slide notes | live (model canned) |
| Real model output quality, real token cost | **not verified**: canned model in this run |
| Thumbnail render and signed fetch (PNG), bad token 404 | live |
| Dedupe 200, version 2, list newest first, empty list | live |
| Failed review `no_text` (blank scan), job result | live |
| Failed `unreadable`, `file_missing`, `over_budget`, `generation_failed`; 429 on POST when out of budget; 409 `FILE_NOT_CLEAN` | unit-tested only |
| Not a PDF 422, file not found 404, invalid body 422, review 404, cross-workspace 404 | live |
| Team member read OK and create 403, mentor 403 | live; all 7 roles on create and read: unit-tested |
| Must-have unmet then met, Story lifted, history log and marker | live |
| Cloudinary-backed thumbnails (provider-signed URLs instead of the local signed link) | **not verified**: local storage only |
