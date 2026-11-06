# FE Integration Guide: Investor Readiness (Module 15, slices A to D: score, history, deck review, mock Q&A, narrative check)

Every request and response body below is pasted **verbatim** from live captures taken by
`e2e/test_investor_readiness.py` against a real server (`e2e/_captures/investor_readiness/*.json`).
Long bodies are trimmed with `// ...` and a note, never re-shaped. Placeholder values in the
captures (`Placeholder Co`, `example.com`) are fake. **What is not covered live is listed in the
verification table at the end and marked inline.**

Scope: this guide covers the **Score & Gaps** page, the **History** page and (section 12) the **Deck Review** page.
Section 13 covers **Mock Q&A**. Section 14 covers **Narrative Check** (slice C).
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
(section 13.9). `markers` now also carries "Deck reviewed (vN)" (captured in section 12).

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
`unreadable` (damaged or password-protected PDF), `file_missing`, `over_budget` (today's AI allowance ran out) `generation_failed`
(the model errored on every retry) or `job_failed` (the job died or ran out of retries without finishing; a review that is still
`queued` or `running` but whose job is dead is reported `failed` with this code the next time it is read). Only `no_text` was captured live:

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
| Failed `unreadable`, `file_missing`, `over_budget`, `generation_failed`, `job_failed`; 429 on POST when out of budget; 409 `FILE_NOT_CLEAN` | unit-tested only |
| Not a PDF 422, file not found 404, invalid body 422, review 404, cross-workspace 404 | live |
| Team member read OK and create 403, mentor 403 | live; all 7 roles on create and read: unit-tested |
| Must-have unmet then met, Story lifted, history log and marker | live |
| Cloudinary-backed thumbnails (provider-signed URLs instead of the local signed link) | **not verified**: local storage only |

## 13. Mock Q&A (slice D)

Routes (`/api/v1/readiness`): `POST /qa-sessions`, `POST /qa-sessions/{id}/messages`, `POST /qa-sessions/{id}/end`, `GET /qa-sessions`,
`GET /qa-sessions/{id}`. Captures are `e2e/_captures/readiness_qa/*.json`.

**What was and was not live.** The API server in this run used the stub language model, so every investor question in the captures reads
`[stub-llm] AI-generated assessment narrative.` (a real model asks real questions). The **scoring job** was drained in-process with a scripted
scorer (fixed ratings: answer 1 rated 4, answer 2 rated 2, answer 3 rated 5), so the ratings and feedback texts are the script's. What is live:
the API, the transcript handling, the one-session rule, the queue, the scoring maths, the score and history wiring, the notification and the
privacy shapes.

### 13.1 Who sees what (D-IR-4, D-IR-7)

| Role | Start, answer, end | List, one session |
|---|---|---|
| founder | yes | full: transcript and scorecard text |
| team_member | **403** | **ratings and counts only**: no transcript, no scorecard, no answers |
| mentor, consultant, accountant, legal advisor, investor | 403 | 403 |

The words a founder typed in an answer practice interview are never returned to anyone else. Do not build a team-member transcript view.

### 13.2 The flow

1. `POST /qa-sessions` `{"focus", "intensity"}`. `focus` is `general | financials | market | team`; `intensity` is `friendly_angel | skeptical_vc | term_sheet_grilling`
   (the FE maps these to "Friendly angel", "Skeptical VC", "Term-sheet grilling"). It returns **201** with the session and the investor's opening question.
2. `POST /qa-sessions/{id}/messages` `{"content", "expected_turn"}` once per answer. `content` is 1 to 2000 characters. **`expected_turn` is required**:
   the session's current `turns` (0 for the first answer, then the `turns` from the last response). It makes a retry safe: see 13.5. Each call is one model call and returns the next question.
   Show a typing state: it takes a few seconds with a real model. **The 10th answer returns `reply: null, ended: true`** and starts scoring by itself.
3. `POST /qa-sessions/{id}/end` ends early and starts scoring (the Q&A "End session" button). Ending twice is harmless.
4. Poll `GET /api/v1/jobs/{job_id}` every 2 s (stop after about 90 s), or poll the session, until `status` is `scored` or `failed`. Show
   "Generating scorecard…" meanwhile.
5. `GET /qa-sessions/{id}` for the scorecard.

### 13.3 List, nothing yet

```json
{
  "data": {
    "sessions": []
  },
  "meta": null
}
```

### 13.4 Start

Rejected input (nothing is spent):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "focus",
        "message": "Input should be 'general', 'financials', 'market' or 'team'"
      }
    ]
  }
}
```

Started. The founder's response includes the full `session` (with `transcript`), and `reply` repeats the opening question:

```json
{
  "data": {
    "session": {
      "id": "2f66a465-bff4-4464-a1e2-e525133066e5",
      "focus": "financials",
      "intensity": "skeptical_vc",
      "status": "active",
      "error": null,
      "turns": 0,
      "max_answers": 10,
      "rating": null,
      "rating_label": null,
      "average": null,
      "job_id": null,
      "started_at": "2026-11-05T20:00:46.166375+00:00",
      "ended_at": null,
      "duration_minutes": null,
      "transcript": [
        {
          "at": "2026-11-05T20:00:46.170561+00:00",
          "role": "investor",
          "text": "[stub-llm] AI-generated assessment narrative."
        }
      ],
      "transcript_purged": false,
      "scorecard": null
    },
    "reply": "[stub-llm] AI-generated assessment narrative."
  },
  "meta": null
}
```

Only one session can be open at a time; starting another returns:

```json
{
  "error": {
    "code": "SESSION_ACTIVE",
    "message": "You already have a session in progress.",
    "field_errors": []
  }
}
```

The FE should resume the open session (find it with `GET /qa-sessions`, `status: "active"`). A session left untouched for 2 hours stops blocking and
becomes `abandoned`.

**Not enough allowance left.** Starting needs about 40,000 tokens of the day's 100,000 allowance (a session costs roughly 35,000). With less
left, `POST /qa-sessions` returns **409** `AI_BUDGET_LOW` ("Not enough of today's AI allowance is left for a full session (it needs about 40,000
tokens). Try again tomorrow."). Unit-tested, not captured live. If the allowance runs out mid-session, an answer returns **429**
`AI_BUDGET_EXHAUSTED` and that answer is not recorded; the founder can end the session to score what they have (also unit-tested only).

### 13.5 Answer

The response carries a summary `session` (no transcript), the next `reply`, and `ended`:

```json
{
  "data": {
    "session": {
      "id": "2f66a465-bff4-4464-a1e2-e525133066e5",
      "focus": "financials",
      "intensity": "skeptical_vc",
      "status": "active",
      "error": null,
      "turns": 1,
      "max_answers": 10,
      "rating": null,
      "rating_label": null,
      "average": null,
      "job_id": null,
      "started_at": "2026-11-05T20:00:46.166375+00:00",
      "ended_at": null,
      "duration_minutes": null
    },
    "reply": "[stub-llm] AI-generated assessment narrative.",
    "ended": false
  },
  "meta": null
}
```

**Double submits and retries.** Send `expected_turn` equal to the `turns` you last saw. If a request is repeated (a lost response, a double click, a
network retry), the server has already recorded that answer, `turns` has moved on, and the repeat is refused with **409 `TURN_MISMATCH`** instead of being
recorded as the answer to the next question and charged again:

```json
{
  "error": {
    "code": "TURN_MISMATCH",
    "message": "That answer was already received. Reload the session to see the investor's reply.",
    "field_errors": []
  }
}
```

FE: on `TURN_MISMATCH`, do not resend. Reload the session (`GET /qa-sessions/{id}`) and show the investor's reply from `transcript`, then continue with
the new `turns`. A missing `expected_turn` is a 422:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "expected_turn",
        "message": "Field required"
      }
    ]
  }
}
```

**Running low mid-session.** Before each answer (except the 10th, which needs no reply) the server checks that the day's allowance can pay for one more turn
and for scoring (about 12,000 tokens). If not it returns **409 `AI_BUDGET_LOW`** ("Not enough of today's AI allowance is left to keep going. End the
session to score your answers so far, or come back tomorrow.") and records nothing: offer "End and score" or "Come back tomorrow". Unit-tested and
API-tested, not captured live.

Rejected:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Type an answer first.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "content",
        "message": "String should have at most 2000 characters"
      }
    ]
  }
}
```

After the session has ended (or is scoring):

```json
{
  "error": {
    "code": "SESSION_NOT_ACTIVE",
    "message": "This session has ended.",
    "field_errors": []
  }
}
```

### 13.6 End

```json
{
  "data": {
    "session": {
      "id": "2f66a465-bff4-4464-a1e2-e525133066e5",
      "focus": "financials",
      "intensity": "skeptical_vc",
      "status": "ending",
      "error": null,
      "turns": 3,
      "max_answers": 10,
      "rating": null,
      "rating_label": null,
      "average": null,
      "job_id": "6404e4ca-8f40-45c9-9f6a-d091b6a75e8d",
      "started_at": "2026-11-05T20:00:46.166375+00:00",
      "ended_at": "2026-11-05T20:00:46.563408+00:00",
      "duration_minutes": 1
    }
  },
  "meta": null
}
```

Ending a session with **no answers** is not scored and costs nothing: its status becomes `abandoned` and `job_id` stays `null`:

```json
{
  "data": {
    "session": {
      "id": "5610cd73-d6ae-432a-b1c9-1cbe26ab7038",
      "focus": "team",
      "intensity": "friendly_angel",
      "status": "abandoned",
      "error": null,
      "turns": 0,
      "max_answers": 10,
      "rating": null,
      "rating_label": null,
      "average": null,
      "job_id": null,
      "started_at": "2026-11-05T20:00:51.349732+00:00",
      "ended_at": "2026-11-05T20:00:51.373382+00:00",
      "duration_minutes": 1
    }
  },
  "meta": null
}
```

### 13.7 The scoring job and the scorecard

Queued, then after the worker ran it:

```json
{
  "data": {
    "id": "6404e4ca-8f40-45c9-9f6a-d091b6a75e8d",
    "type": "ai.readiness.qa_score",
    "status": "queued",
    "result": null,
    "error": null
  },
  "meta": null
}
```

```json
{
  "data": {
    "id": "6404e4ca-8f40-45c9-9f6a-d091b6a75e8d",
    "type": "ai.readiness.qa_score",
    "status": "succeeded",
    "result": {
      "status": "scored",
      "session_id": "2f66a465-bff4-4464-a1e2-e525133066e5"
    },
    "error": null
  },
  "meta": null
}
```

Like deck reviews, a job can end `succeeded` while the session says `failed`: **read the session's `status`**. `error` is `over_budget`,
`generation_failed`, `transcript_gone` or `job_failed` (the job died; set the next time the session is read).

**A failed scoring is recoverable.** While the transcript still exists (not purged, at least one answer), calling `POST /qa-sessions/{id}/end` on a
`failed` session queues scoring again (status goes back to `ending`, `error` clears, a new `job_id`). Show a "Try scoring again" button on a failed
session with `transcript_purged: false`; the ten answers are not lost. A `failed` session with `error: "transcript_gone"` cannot be re-queued.

The founder's scorecard (transcript and answers trimmed here; the rest has the same shape):

```json
{
  "data": {
    "id": "2f66a465-bff4-4464-a1e2-e525133066e5",
    "focus": "financials",
    "intensity": "skeptical_vc",
    "status": "scored",
    "error": null,
    "turns": 3,
    "max_answers": 10,
    "rating": "good",
    "rating_label": "Good",
    "average": 3.67,
    "job_id": "6404e4ca-8f40-45c9-9f6a-d091b6a75e8d",
    "started_at": "2026-11-05T20:00:46.166375+00:00",
    "ended_at": "2026-11-05T20:00:46.563408+00:00",
    "duration_minutes": 1,
    "transcript": [
      {
        "at": "2026-11-05T20:00:46.170561+00:00",
        "role": "investor",
        "text": "[stub-llm] AI-generated assessment narrative."
      },
      {
        "at": "2026-11-05T20:00:46.342190+00:00",
        "role": "founder",
        "text": "Our runway is 4.2 months and we are talking to two banks. (0)"
      },
      {
        "at": "2026-11-05T20:00:46.342775+00:00",
        "role": "investor",
        "text": "[stub-llm] AI-generated assessment narrative."
      },
      // ... the rest of the transcript, same shape (7 entries in this session)
    ],
    "transcript_purged": false,
    "scorecard": {
      "trend": null,
      "rating": "good",
      "answers": [
        {
          "i": 1,
          "answer": "Our runway is 4.2 months and we are talking to two banks. (0)",
          "rating": 4,
          "feedback": "Specific and numbers-backed.",
          "question": "[stub-llm] AI-generated assessment narrative."
        },
        {
          "i": 2,
          "answer": "Our runway is 4.2 months and we are talking to two banks. (1)",
          "rating": 2,
          "feedback": "Vague about competitors.",
          "question": "[stub-llm] AI-generated assessment narrative."
        },
        // ... the third answer, same shape
      ],
      "average": 3.67,
      "weakest": {
        "i": 2,
        "answer": "Our runway is 4.2 months and we are talking to two banks. (1)",
        "rating": 2,
        "feedback": "Vague about competitors.",
        "question": "[stub-llm] AI-generated assessment narrative.",
        "model_answer": "We compete with X on workflow and Y on data, and own the gap between."
      },
      "strongest": {
        "i": 3,
        "answer": "Our runway is 4.2 months and we are talking to two banks. (2)",
        "rating": 5,
        "feedback": "Owned the weak spot.",
        "question": "[stub-llm] AI-generated assessment narrative."
      },
      "rating_label": "Good"
    }
  },
  "meta": null
}
```

Notes:

- `rating` is `needs_work` (average under 2.5), `good` (under 4) or `strong`; `rating_label` is the display text. Each answer is rated 1 to 5.
- `strongest` and `weakest` are chosen **in code from the ratings**, not by the model. `weakest.model_answer` is a suggested better answer and is
  `null` whenever the model's suggestion was written for a different answer than the weakest one: **hide the "Model answer" block when it is null**.
- `trend` is `null` for the first scored session; afterwards it compares this average with up to the last five scored sessions.
- Answers the scorer did not rate default to 1. With a real model every answer is rated; this capture's scripted scorer rated only three.
- `transcript[].role` is `investor` or `founder`. Transcripts are kept for **90 days**; after that `transcript` is `[]` and `transcript_purged` is
  `true`, and **the scorecard loses all its text too**: it keeps only the numbers and which answers were picked. The purged shape (unit-tested, not
  captured live because it needs a 90-day-old session):
  `{"rating", "rating_label", "average", "answers": [{"i": 1, "rating": 4}, ...], "strongest": {"i": 3}, "weakest": {"i": 2}, "trend", "text_purged": true}`.
  On a purged session show the ratings only and say the words were removed after 90 days. Do not assume `question`, `answer`, `feedback` or
  `model_answer` exist when `scorecard.text_purged` is `true`.
- Closing copy "Rough edges found here are cheap. Found in the room, they're expensive." is the FE's.

### 13.8 What a team member gets

The same session, as a team member sees it. No `transcript`, `scorecard` or `answers` keys exist in this response:

```json
{
  "data": {
    "id": "2f66a465-bff4-4464-a1e2-e525133066e5",
    "focus": "financials",
    "intensity": "skeptical_vc",
    "status": "scored",
    "error": null,
    "turns": 3,
    "max_answers": 10,
    "rating": "good",
    "rating_label": "Good",
    "average": 3.67,
    "job_id": "6404e4ca-8f40-45c9-9f6a-d091b6a75e8d",
    "started_at": "2026-11-05T20:00:46.166375+00:00",
    "ended_at": "2026-11-05T20:00:46.563408+00:00",
    "duration_minutes": 1
  },
  "meta": null
}
```

And the list (same summary per session, newest first):

```json
{
  "data": {
    "sessions": [
      {
        "id": "2f66a465-bff4-4464-a1e2-e525133066e5",
        "focus": "financials",
        "intensity": "skeptical_vc",
        "status": "scored",
        "error": null,
        "turns": 3,
        "max_answers": 10,
        "rating": "good",
        "rating_label": "Good",
        "average": 3.67,
        "job_id": "6404e4ca-8f40-45c9-9f6a-d091b6a75e8d",
        "started_at": "2026-11-05T20:00:46.166375+00:00",
        "ended_at": "2026-11-05T20:00:46.563408+00:00",
        "duration_minutes": 1
      }
    ]
  },
  "meta": null
}
```

Refused:

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

Another workspace's session id:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### 13.9 Score, history, notification

The Story dimension has a new signal, `story.mock_qa` ("Defended the pitch"): the latest scored session's average rating out of 5, against a target of
3 (early stages) up to 4 (scale); `0` and a "run a mock session" note when there is none. The score refreshes the moment scoring finishes (it
bypasses the 15 minute throttle), so refetch `GET /score` when the job succeeds. The signal as captured (`note` is `null` because 3.67 meets the
target of 3.5 for a launch-stage startup):

```json
{
  "key": "story.mock_qa",
  "href": "/investor-readiness/qa",
  "note": null,
  "unit": "/5",
  "label": "Defended the pitch (mock Q&A rating, out of 5)",
  "score": 100.0,
  "value": 3.67,
  "target": 3.5
}
```

History: `log` now also lists scored sessions (`type: "qa_session"`, newest first, mixed with deck reviews), which is the data for the History
page's session table (Date, Focus, Intensity, Duration, Rating); `markers` gets "Mock Q&A scored (Good)":

```json
{
  "data": {
    "range": 30,
    "points": [
      {
        "at": "2026-11-05T20:00:47.369119+00:00",
        "score": 4,
        "band": "not_yet",
        "capped": false,
        "dimensions": {
          "team": 8,
          "story": 29,
          "market": 0,
          "product": 0,
          "traction": 0,
          "financials": 0
        }
      }
    ],
    "markers": [
      {
        "at": "2026-11-05T20:00:47.386528+00:00",
        "label": "Mock Q&A scored (Good)"
      }
    ],
    "log": [
      {
        "type": "qa_session",
        "at": "2026-11-05T20:00:47.386528+00:00",
        "id": "2f66a465-bff4-4464-a1e2-e525133066e5",
        "focus": "financials",
        "intensity": "skeptical_vc",
        "duration_minutes": 1,
        "rating": "good",
        "rating_label": "Good",
        "average": 3.67
      }
    ]
  },
  "meta": null
}
```

The bell notification (founders only) when scoring finishes. Route it to the scorecard with `data.session_id` (module `readiness`):

```json
{
  "id": "e2172c0a-8e3d-4c2c-adbd-f1d1f18671a8",
  "type": "readiness.qa.scored",
  "title": "Mock Q&A session summary ready",
  "body": "See scorecard",
  "data": {
    "rating": "good",
    "average": 3.67,
    "session_id": "2f66a465-bff4-4464-a1e2-e525133066e5",
    "startup_id": "711cc824-9685-47d6-8b59-4aeb264a8ebd"
  },
  "read": false,
  "archived": false,
  "priority": "normal",
  "module": "readiness",
  "created_at": "2026-11-05T20:00:47.369119+00:00"
}
```

### 13.10 Cost and limits

About 35,000 tokens per full session (10 answers plus scoring), so at most two a day on the default allowance. 10 answers per session, 2000
characters per answer, one open session per workspace.

### 13.11 Mock Q&A verification

| Behaviour | Verified |
|---|---|
| Start 201, opening question, one-session 409, answers, blank and over-long 422, answer after end 409, retry 409 `TURN_MISMATCH`, missing `expected_turn` 422 | live (stub investor) |
| End (early and with no answers), job queued then succeeded, scorecard shape, ratings, strongest, weakest | live (scripted scorer) |
| Real model questions, real ratings and feedback, real token cost | **not verified**: stub and scripted models only |
| Story signal, score refresh, history log and marker, bell notification | live |
| Team member sees ratings and counts only; start and answer 403; mentor 403; other workspace 404 | live; all 7 roles on every route: unit-tested |
| 10th answer ends the session by itself | unit-tested and API-tested; not in the live run (it would need 10 calls) |
| 409 `AI_BUDGET_LOW` (start and mid-session), 429 mid-call, scoring failures (`over_budget`, `generation_failed`, `job_failed`, `transcript_gone`), re-queue of a failed scoring | unit-tested only |
| Transcripts and scorecard text cleared after 90 days, forgotten sessions closed after a day (daily job) | unit-tested; the daily job was not run live |
| Prompt-injection fence on founder answers | unit-tested (prompt shape), not tested against a real model |


## 14. Narrative Check (slice C)

Routes (`/api/v1/readiness`): `GET /narrative`, `PUT /narrative`, `POST /narrative/check`, `GET /narrative/checks/latest`.
Captures: `e2e/_captures/readiness_narrative_real/*.json` (this section) and `e2e/_captures/readiness_narrative/*.json`
(same journey with a scripted model), both from `e2e/test_readiness_narrative.py` against a live server.

**What was live.** The claim extraction ran on the **real model** (`NARRATIVE_E2E_REAL_LLM=1`, the worker's own client),
so every claim, quote and slide number below is what the model actually returned. The deck review that the check reads was
drained with the canned reviewer from slice B (its scores are the script's; the deck PDF and its text are real). The job was
drained in the test process, as the worker would. Measured cost: **1,329 tokens** for an 8-slide deck plus a 124-character
draft, and 1,355 for the second run (the spec estimated about 8k; a long deck and draft will cost more).

### 14.1 What the page does, in FE terms

The founder writes their financial story (one draft per workspace, autosaved). "Check my narrative" compares the numbers in
the **latest reviewed pitch deck** and the **draft** against what Cofoundaz already knows: the books (Finance transactions),
the financial model, the team, and the Funding round target. The model only **reads the numbers out of the text**; every
right/wrong verdict is computed in code with a 10% tolerance, so the model cannot invent a discrepancy. A number whose quote
is not in the text, or whose value is not written in that quote, is thrown away (`dropped_claims` on the job).

### 14.2 Who can call what

| Role | `GET /narrative`, `GET /narrative/checks/latest` | `PUT /narrative`, `POST /narrative/check` |
|---|---|---|
| founder | 200 | 200 / 202 |
| team_member | 200 (draft and findings, read only) | **403** |
| mentor, business_consultant, accountant, legal_advisor, investor | **403** | **403** |

Hide the editor's save state, the textarea's editability and the "Check" button for a team member: show the draft read only.

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

### 14.3 `GET /narrative`: the draft

Empty workspace (`draft_empty.json`):

```json
{
  "data": {
    "body": "",
    "chars": 0,
    "max_chars": 20000,
    "draft_sha256": null,
    "updated_at": null,
    "updated_by_id": null,
    "has_reviewed_deck": false,
    "can_check": false
  },
  "meta": null
}
```

`can_check` is true when the draft has text **or** a deck has been reviewed. When false, disable "Check" and say
"Write your narrative or have your pitch deck reviewed first." (`has_reviewed_deck` tells you which half is missing.)

### 14.4 `PUT /narrative` `{"body": "..."}`: save (autosave)

Send the **whole** text every time (last write wins; there is no merge). Empty string clears it. Max 20,000 characters;
debounce autosave (about 1 s after typing stops). Response is the same shape as `GET` (`draft_put_200.json`):

```json
{
  "data": {
    "body": "We make 2m NGN MRR today and burn 500k NGN a month. We are raising 90m NGN: 60% for engineering hires and 40% to open Ghana.",
    "chars": 124,
    "max_chars": 20000,
    "draft_sha256": "a28f354cd266a373f591e16a4409961358e39950e8b4a9053df625ae07fa0447",
    "updated_at": "2026-11-06T22:14:16.886553+00:00",
    "updated_by_id": "1ae75c62-c853-4900-b12e-5599d4c1f5b8",
    "has_reviewed_deck": true,
    "can_check": true
  },
  "meta": null
}
```

Over the limit (`draft_put_too_long_422.json`). **Trap:** `field` is an empty string, not `"body"`; show the message under
the textarea yourself and enforce `max_chars` client side with a counter.

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "String should have at most 20000 characters"
      }
    ]
  }
}
```

### 14.5 `POST /narrative/check`: three outcomes

No body. Read `data.outcome`:

| HTTP | `outcome` | Meaning | FE does |
|---|---|---|---|
| **202** | `queued` | a model call was queued | poll (14.6) |
| 200 | `in_progress` | a check is already running; that one is returned | poll it; do not show an error |
| 200 | `reused` | neither the draft nor the reviewed deck changed since the last finished check: the old claims were reused against **today's** numbers, `tokens_used: 0`, already `ready` | render it at once |

`reused` is what makes "I fixed my books, check again" free. Tell the founder in small print: "No AI used: your text
had not changed." Pressing again within a minute with nothing at all changed returns the same check (same `id`).

Queued (`check_202.json`):

```json
{
  "data": {
    "check": {
      "id": "6e97ee9c-2cba-4a45-8adf-bf3ea1663110",
      "status": "queued",
      "error": null,
      "job_id": "4e04dff4-e41f-4047-afa1-c89a164fe46d",
      "deck_review_id": "a64bd40d-d2ce-40a4-ba8a-37db6eb23151",
      "draft_sha256": "a28f354cd266a373f591e16a4409961358e39950e8b4a9053df625ae07fa0447",
      "tokens_used": 0,
      "counts": {
        "high": 0,
        "medium": 0,
        "info": 0,
        "ok": 0
      },
      "findings": [],
      "claims": [],
      "use_of_funds": [],
      "facts": {},
      "stale": false,
      "created_at": "2026-11-06T22:14:16.957778+00:00",
      "checked_at": null
    },
    "outcome": "queued"
  },
  "meta": null
}
```

`check_in_progress_200.json` is the identical `check` with `"outcome": "in_progress"`.

Reused (`check_reused_200.json`, trimmed): `"job_id": null`, `"tokens_used": 0`, `"status": "ready"`, `"outcome": "reused"`,
with findings computed against the books as they are now.

### 14.6 Polling

Poll `GET /api/v1/jobs/{job_id}` every 2 s, stop after about 60 s, then read `GET /narrative/checks/latest`.
(Or poll `checks/latest` directly until `status` is `ready` or `failed`.) With the real model it took about 7 s.

`job_queued.json`:

```json
{
  "data": {
    "id": "4e04dff4-e41f-4047-afa1-c89a164fe46d",
    "type": "ai.readiness.narrative_check",
    "status": "queued",
    "result": null,
    "error": null
  },
  "meta": null
}
```

`job_succeeded.json` (`dropped_claims`: numbers the model returned that failed the quote check, for support only):

```json
{
  "data": {
    "id": "4e04dff4-e41f-4047-afa1-c89a164fe46d",
    "type": "ai.readiness.narrative_check",
    "status": "succeeded",
    "result": {
      "claims": 6,
      "status": "ready",
      "check_id": "6e97ee9c-2cba-4a45-8adf-bf3ea1663110",
      "dropped_claims": 1
    },
    "error": null
  },
  "meta": null
}
```

A job can also end `succeeded` with a **failed check** (the check says why); always read the check's `status`.

### 14.7 `GET /narrative/checks/latest`: the result

Nothing yet: `{"data": {"check": null}, "meta": null}` (`latest_none.json`). Otherwise the newest check, any status.

`latest_ready.json`, top-level fields:

```json
{
  "id": "6e97ee9c-2cba-4a45-8adf-bf3ea1663110",
  "status": "ready",
  "error": null,
  "job_id": "4e04dff4-e41f-4047-afa1-c89a164fe46d",
  "tokens_used": 1329,
  "counts": {
    "high": 1,
    "medium": 1,
    "info": 2,
    "ok": 3
  },
  "stale": false,
  "created_at": "2026-11-06T22:14:16.957778+00:00",
  "checked_at": "2026-11-06T22:14:23.661043+00:00"
}
```

(plus `deck_review_id`, `draft_sha256`, `findings`, `claims`, `use_of_funds`, `facts`; shown below).

**`findings`** are sorted worst first: `high`, `medium`, `info`, `ok`. Show `message` as the line text (it is final copy, the
PRD wording); colour by `severity`. The first two from the live run:

```json
[
  {
    "fact": null,
    "kind": "inconsistent",
    "claim": {
      "unit": "minor",
      "quote": "We make 2m NGN MRR today",
      "slide": null,
      "value": 200000000,
      "metric": "mrr",
      "source": "draft",
      "currency": "NGN"
    },
    "metric": "mrr",
    "message": "Deck (slide 5) says ₦1.6M MRR; your narrative says ₦2M. Pick one number.",
    "severity": "high",
    "other_claim": {
      "unit": "minor",
      "quote": "MRR 1.6m NGN",
      "slide": 5,
      "value": 160000000,
      "metric": "mrr",
      "source": "deck",
      "currency": "NGN"
    }
  },
  {
    "fact": {
      "says": "your books show",
      "unit": "minor",
      "value": 160000000,
      "source": "books",
      "currency": "NGN"
    },
    "kind": "mismatch",
    "claim": {
      "unit": "minor",
      "quote": "We make 2m NGN MRR today",
      "slide": null,
      "value": 200000000,
      "metric": "mrr",
      "source": "draft",
      "currency": "NGN"
    },
    "metric": "mrr",
    "message": "Your narrative says ₦2M MRR; your books show ₦1.6M. Fix the number or the story.",
    "severity": "medium"
  }
]
```

The other five findings of that run, `message` only (verbatim):

- info: `Your narrative says ₦500k monthly burn; your books show no net burn (income covers costs), so a gross burn figure cannot be checked.`
- info: `Deck (slide 5) says 12% monthly growth, but Cofoundaz has no figure to check it against yet.`
- ok: `Deck (slide 5) says ₦1.6M MRR; your books show ₦1.6M. Consistent.`
- ok: `Deck (slide 8) says ₦90M raise; your Funding round targets ₦90M. Consistent.`
- ok: `Your narrative says ₦90M raise; your Funding round targets ₦90M. Consistent.`

| `kind` | `severity` | Meaning | `fact` | `other_claim` |
|---|---|---|---|---|
| `inconsistent` | high | deck and narrative give different numbers for the same metric | **null** | present (the deck's claim) |
| `mismatch` | high (> 25% off) or medium (10-25%) | a claim disagrees with the app's figure | present | absent |
| `match` | ok | within 10% | present | absent |
| `unverified` | info | no figure to compare, a different currency, a team bigger than the workspace's members, or burn against zero net burn | present or **null** | absent |

**Field-nesting traps:**
- `other_claim` exists **only** on `inconsistent`. On that kind, `claim` is the narrative's and `other_claim` the deck's.
- `fact` is `null` on `inconsistent` and on `unverified` with no figure; it is an object on everything else.
- `claim.slide` is `null` for draft claims and may be `null` for a deck claim whose quote could not be placed on a slide.
- Money is always **minor units** (`unit: "minor"`, kobo for NGN); `growth_pct` is `unit: "percent"` (12 = 12%),
  `runway_months` is `"months"`, `headcount` is `"people"`. `value` can be a float for percent/months.
- `facts` is keyed by metric and only has the metrics Cofoundaz could read; a missing key is not zero.

**`claims`**: every number extracted, with its verbatim `quote` (highlight it in the draft or link to the deck slide).
Metrics: `mrr`, `arr`, `burn`, `runway_months`, `cash`, `headcount`, `ask`, `growth_pct`. Targets and projections ("we will
reach", "by 2027") are not claims and never appear.

**`use_of_funds`** (live run, first entry; the run found four, two from the deck, two from the draft):

```json
{
  "label": "Hire engineers",
  "quote": "hire 3 engineers",
  "source": "deck",
  "percent": null,
  "amount_minor": null
}
```

**`facts`** (live run, excerpt): where each comparison number came from. `source` is `books` (recorded transactions,
3-month average), `model` (financial model month 1), `team` (active founder and team members) or `funding` (open round target).

```json
{
  "mrr": {
    "says": "your books show",
    "unit": "minor",
    "value": 160000000,
    "source": "books",
    "currency": "NGN"
  },
  "burn": {
    "says": "your books show",
    "unit": "minor",
    "value": 0,
    "source": "books",
    "currency": "NGN"
  },
  "headcount": {
    "says": "your workspace has",
    "unit": "people",
    "value": 2,
    "source": "team",
    "currency": null
  }
}
```

### 14.8 Stale and failed checks

- `stale: true` (`latest_stale.json`): the draft was edited, or a newer deck was reviewed, after this check ran. Keep the
  findings visible but show a banner "Your narrative changed since this check. Check again." The next check is a real model call.
- `status: "failed"` with `error`:

| `error` | Show |
|---|---|
| `over_budget` | "Today's AI allowance ran out before the check ran. Try again tomorrow." |
| `generation_failed` | "The check could not run. Try again." |
| `processing_failed` | same as above (the call was billed) |
| `nothing_to_check` | "Your narrative was cleared before the check ran." |
| `job_failed` | "The check stopped. Try again." |

- `status: "ready"` with `error: "deck_unavailable"`: the reviewed deck's file was deleted; only the draft was checked. Say so.

`over_budget`, `generation_failed`, `processing_failed`, `job_failed` and `deck_unavailable` were **not** exercised live (unit-tested only).

### 14.9 Errors on `POST /narrative/check`

| HTTP | `code` | When | Captured |
|---|---|---|---|
| 422 | `NOTHING_TO_CHECK` | no draft text and no reviewed deck | `check_nothing_422.json` |
| 409 | `AI_BUDGET_LOW` | less than about 10,000 tokens left today | `check_budget_low_409.json` |
| 429 | `AI_BUDGET_EXHAUSTED` | today's allowance used up | `check_budget_exhausted_429.json` |
| 429 | rate limit | more than 10 presses a minute | not captured (limiter relaxed on the e2e server) |
| 403 | `FORBIDDEN` | not the founder | `check_team_member_403.json` |

```json
{
  "error": {
    "code": "NOTHING_TO_CHECK",
    "message": "Write your narrative or have your pitch deck reviewed first, then check it.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "AI_BUDGET_LOW",
    "message": "Not enough of today's AI allowance is left for a narrative check (it needs about 10,000 tokens). Try again tomorrow.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "AI_BUDGET_EXHAUSTED",
    "message": "Today's AI allowance for this workspace is used up. Try again tomorrow.",
    "field_errors": []
  }
}
```

### 14.10 Score and history

Two signals now read the latest finished check (`score_after_check.json`, verbatim):

```json
{
  "key": "financials.use_of_funds",
  "href": "/investor-readiness/narrative",
  "note": null,
  "unit": "step",
  "label": "Use of funds defined",
  "score": 100.0,
  "value": 1.0,
  "target": 1
}
```

```json
{
  "key": "story.narrative",
  "href": "/investor-readiness/narrative",
  "note": "Your deck or narrative disagrees with your numbers, or states none that Cofoundaz can check. Fix the number or the story.",
  "unit": "step",
  "label": "Narrative consistent with the numbers",
  "score": 50.0,
  "value": 1.0,
  "target": 2
}
```

`story.narrative` is 0 (never checked), 50 (a high finding, or no number that could be compared), or 100 (at least one number
compared and no high finding). A finished check refreshes the score at once (no 15-minute wait).

History (`history_after_check.json`): a log entry and a marker per finished check.

```json
[
  {
    "type": "narrative_check",
    "at": "2026-11-06T22:14:23.661043+00:00",
    "id": "6e97ee9c-2cba-4a45-8adf-bf3ea1663110",
    "counts": {
      "high": 1,
      "medium": 1,
      "info": 2,
      "ok": 3
    }
  }
]
```

Marker: `{"at": "2026-11-06T22:14:23.661043+00:00", "label": "Narrative checked"}`.

### 14.11 UX consequences to surface

- **The verdicts can change without a new check.** A `reused` check re-reads the books: fixing a transaction can turn a red
  line green, or a green line red (in the live run, after the books moved to ₦2M, the deck's ₦1.6M became a medium mismatch).
  Show `checked_at` next to the results.
- **"Unverified" is not "wrong".** Render info lines neutrally, not as warnings.
- **A check costs AI allowance only when the text changed.** Say "Uses AI" on the button only when the last check is
  `stale` or absent.
- **Headcount and burn are lenient on purpose**: a bigger claimed team, or a burn when the books show none, is never flagged.

### 14.12 Narrative verification

| Behaviour | Verified |
|---|---|
| Draft get/put, 20,000-char 422, empty state, `can_check` | live |
| Check 202, `in_progress` 200, job queued then succeeded, findings, claims, facts, use of funds | **live on the real model** (deck review itself canned) |
| Quote check drops invented or misread numbers; targets dropped; dash rewrite survives | real model run dropped 1 claim; each rule unit-tested |
| `reused` after fixing the books (0 tokens), `stale` after an edit, second real call after the edit | live |
| 409 `AI_BUDGET_LOW`, 429 `AI_BUDGET_EXHAUSTED`, 422 `NOTHING_TO_CHECK` | live |
| Team member reads, cannot put or check (403); mentor 403 | live; all 7 roles on every route unit-tested |
| Score signals and history log/marker | live |
| Failed checks (`over_budget`, `generation_failed`, `processing_failed`, `job_failed`), `deck_unavailable`, rate limit 429 | unit-tested only |
| Token cost on a long deck (30 slides, 20k-char draft) | **not measured**; 1,329 tokens measured on 8 slides |
