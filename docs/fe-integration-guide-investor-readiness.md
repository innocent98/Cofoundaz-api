# FE Integration Guide: Investor Readiness (Module 15, slice A: score and history)

Every request and response body below is pasted **verbatim** from live captures taken by
`e2e/test_investor_readiness.py` against a real server (`e2e/_captures/investor_readiness/*.json`).
Long bodies are trimmed with `// ...` and a note, never re-shaped. Placeholder values in the
captures (`Placeholder Co`, `example.com`) are fake. **What is not covered live is listed in the
verification table at the end and marked inline.**

Scope: this guide covers the **Score & Gaps** page and the **History** page. Deck Review, Mock Q&A and
Narrative Check have no API yet (slices B, D, C); keep them on the mock until their guides exist.
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

403 body (verified live for a user with no membership in the workspace; role-by-role 403s are unit-tested,
same guard):

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

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
        "label": "A financial model"
      },
      {
        "key": "pitch_deck",
        "met": false,
        "href": "/documents",
        "label": "A pitch deck"
      },
      {
        "key": "traction_proof",
        "met": false,
        "href": "/validation",
        "label": "One proof point: revenue, a won deal, or a validated assumption with demand"
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
        "href": "/documents",
        "lift": 5,
        "note": "You have no pitch deck. Create one in Documents.",
        "dimension": "story",
        "signal_key": "story.pitch_deck"
      }
    ],
    "computed_at": "2026-11-05T09:49:51.239590+00:00",
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
    "score": 32,
    "band": "not_yet",
    "verdict": "Not yet, and that's fine. Here's the shortest path.",
    "ready": false,
    "capped": false,
    "raw_score": 32,
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
        "label": "A financial model"
      },
      {
        "key": "pitch_deck",
        "met": true,
        "href": "/documents",
        "label": "A pitch deck"
      },
      {
        "key": "traction_proof",
        "met": true,
        "href": "/validation",
        "label": "One proof point: revenue, a won deal, or a validated assumption with demand"
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
        "href": "/validation/experiments",
        "lift": 4,
        "note": "0 experiment(s) run. Launch a test that shows people want it.",
        "dimension": "product",
        "signal_key": "product.experiments"
      },
      {
        "href": "/mission",
        "lift": 4,
        "note": "Mission completion is 0% against 70%. Finish today's mission.",
        "dimension": "product",
        "signal_key": "product.mission_completion"
      }
    ],
    "computed_at": "2026-11-05T09:49:51.575170+00:00",
    "config_version": 1
  },
  "meta": null
}
```

Field notes:

- `score` is what to show. `raw_score` is the number before the cap. `capped: true` means the raw
  score reached 70 or more but a must-have is missing, so `score` is held at 69. **Show the reason**: list the
  `must_haves` with `met: false` ("Capped at 69: add {label}") with a link to `href`. `capped: true` was not reached
  live (see verification table).
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
    "score": 32,
    "band": "not_yet",
    "verdict": "Not yet, and that's fine. Here's the shortest path.",
    "ready": false,
    "capped": false,
    "raw_score": 32,
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
        "label": "A financial model"
      },
      {
        "key": "pitch_deck",
        "met": true,
        "href": "/documents",
        "label": "A pitch deck"
      },
      {
        "key": "traction_proof",
        "met": true,
        "href": "/validation",
        "label": "One proof point: revenue, a won deal, or a validated assumption with demand"
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
        "href": "/validation/experiments",
        "lift": 4,
        "note": "0 experiment(s) run. Launch a test that shows people want it.",
        "dimension": "product",
        "signal_key": "product.experiments"
      },
      {
        "href": "/mission",
        "lift": 4,
        "note": "Mission completion is 0% against 70%. Finish today's mission.",
        "dimension": "product",
        "signal_key": "product.mission_completion"
      }
    ],
    "computed_at": "2026-11-05T09:49:51.733982+00:00",
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
        "at": "2026-11-05T09:49:51.239590+00:00",
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
        "at": "2026-11-05T09:49:51.575170+00:00",
        "score": 32,
        "band": "not_yet",
        "capped": false,
        "dimensions": {
          "team": 8,
          "story": 75,
          "market": 56,
          "product": 25,
          "traction": 12,
          "financials": 42
        }
      }
    ],
    "markers": []
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

The PRD also shows a session and review log on the History page. That log arrives with the deck review and
Mock Q&A slices; keep the Q&A table on the mock until then.

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

Validation (SAM larger than TAM). Note the empty `field` for a cross-field rule:

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

A malformed `source_url` or an unknown key also returns 422 `VALIDATION_ERROR` (unit-tested; same shape). A successful
`PUT` recomputes the score straight away, so refetch `GET /score` to show the new number.

## 7. `GET /readiness/snapshot`

The share-safe subset Funding shows investors: no gap notes, no signal detail.

```json
{
  "data": {
    "score": 32,
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
        "score": 75
      }
    ],
    "computed_at": "2026-11-05T09:49:51.733982+00:00"
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
| Per-role 200/403 for all 7 roles on every route | unit-tested (`tests/api/test_investor_readiness_api.py`), not live |
| `capped: true`, `ready: true`, `band` other than `not_yet` | unit-tested (scoring and service tests); not reached live because it needs a financial model plus much more data |
| Dimension `score: null` ("Not enough data yet") | not reachable with the current signals; shape from the serializer |
| History `markers` with entries | unit-tested; none occurred in the live run |
| `readiness.improved` notification and bell link | unit-tested; not captured live |
| Event-triggered and nightly recompute | unit-tested; live run showed the `PUT /inputs` recompute only |
| `team.assessment`, `financials.runway` signals present | not captured live (need an assessment and cash-flow history); present in unit tests |
