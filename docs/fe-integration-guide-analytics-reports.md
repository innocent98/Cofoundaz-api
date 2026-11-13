# FE integration guide: Analytics & Reports, slice B (reports, runs, shares)

Every body below is pasted from a real run of `e2e/test_analytics_reports.py` against a live server
(captures in `e2e/_captures/analytics_reports/`). IDs, names and timestamps are from that run and are
placeholders. All routes are under `/api/v1/analytics`, JSON envelope `{data}` / `{error}`, the usual
`Authorization` + `X-Workspace-Id` headers. Money is in minor units (kobo). Slice A's guide
(`fe-integration-guide-analytics.md`) covers the catalog, Overview and custom metrics.

## 1. What it is, in the founder's terms

"Make a report of the numbers I care about, keep it, run it to get a frozen copy, and show it to my
mentor, accountant or team." A **report** is a named list of **blocks** (a metric from the catalog, or a
text note) with a default date range. A **run** is a frozen copy of every number at one moment. A
**share** lets the Founder give a team member, accountant, mentor or business consultant read access to one report.

## 2. Who can do what

| Role | List / open | Create, preview, duplicate | Edit, delete | Share | Run |
|---|---|---|---|---|---|
| Founder | all reports | yes | any report | yes | yes |
| Team member | own + shared with them | yes | own only | no (403) | yes |
| Accountant, mentor, business consultant | only reports shared with them | 403 | 403 | 403 | yes (read-only copy under their own access) |
| Investor, legal advisor | 403 | 403 | 403 | 403 | 403 |

A report you cannot see is a **404** (same as another workspace's), never a 403. A visible report you may not change is a **403**.
Response fields `can_edit`, `can_share`, `is_owner`, `shared_with_me` tell the FE which controls to render.

## 3. Per-metric authorisation, and locked blocks (UX consequence)

Every metric belongs to a hub. A viewer receives a block's numbers only if their role may open that hub.
Mentor and business consultant get the non-financial hubs (sales, marketing, validation, health, mission and roadmap); never Finance, Funding or custom metrics.
The accountant gets Finance only. A block over a hub the viewer cannot open comes back as a bare lock, in
the report definition **and** in every run (including runs someone else made):

`{"id", "type": "metric", "locked": true, "needs": "<hub>", "needs_label": "<Hub label>"}`

**FE must** draw a lock with "needs <needs_label>" and must not expect `metric`, `title`, `range` or any number on it.
The founder's shares dialog shows `locked_hubs` per person so they know who will see locks before sharing.

## 4. Endpoints

### GET /reports/templates (founder, team member)

The three chips. Blocks the caller cannot see are left out; `omitted_hubs` says which hubs.

`e2e/_captures/analytics_reports/templates.json`

```json
{
  "data": {
    "templates": [
      {
        "key": "monthly_investor",
        "name": "Monthly investor snapshot",
        "description": "Revenue, burn, runway, cash and pipeline for the month, with the change on the month before.",
        "default_range": {
          "range": "30d",
          "from": null,
          "to": null,
          "compare": "previous"
        },
        "blocks": [
          {
            "type": "metric",
            "metric": "finance.revenue",
            "viz": "line"
          },
          {
            "type": "metric",
            "metric": "finance.net_burn",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "finance.runway_months",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "finance.cash_balance",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "sales.pipeline_value",
            "viz": "number"
          }
        ],
        "omitted_hubs": []
      },
      {
        "key": "weekly_operating_review",
        "name": "Weekly operating review",
        "description": "What the team shipped and sold this week.",
        "default_range": {
          "range": "7d",
          "from": null,
          "to": null,
          "compare": "previous"
        },
        "blocks": [
          {
            "type": "metric",
            "metric": "execution.tasks_done",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "execution.milestones_done_pct",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "sales.deals_won",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "marketing.clicks",
            "viz": "line"
          },
          {
            "type": "metric",
            "metric": "health.score",
            "viz": "number"
          }
        ],
        "omitted_hubs": []
      },
      {
        "key": "growth_funnel",
        "name": "Growth funnel",
        "description": "From impressions to closed deals over the quarter.",
        "default_range": {
          "range": "90d",
          "from": null,
          "to": null,
          "compare": "previous"
        },
        "blocks": [
          {
            "type": "metric",
            "metric": "marketing.impressions",
            "viz": "line"
          },
          {
            "type": "metric",
            "metric": "marketing.clicks",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "marketing.conversions",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "marketing.ctr",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "sales.pipeline_value",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "sales.deals_won",
            "viz": "number"
          },
          {
            "type": "metric",
            "metric": "sales.win_rate",
            "viz": "number"
          }
        ],
        "omitted_hubs": []
      }
    ]
  },
  "meta": null
}
```

### POST /reports/preview (founder, team member): stateless, nothing stored

Body `{blocks, default_range?}`. Rate limited per workspace (429 `RATE_LIMITED`). `results[i].result` is the same shape as slice A's resolve.

`e2e/_captures/analytics_reports/preview.json`

```json
{
  "data": {
    "period": {
      "from": "2026-10-15",
      "to": "2026-11-13"
    },
    "as_of": "2026-11-13T16:29:24.304791+00:00",
    "results": [
      {
        "block_id": "ce4258d4",
        "type": "metric",
        "hub": "finance",
        "viz": "line",
        "title": null,
        "result": {
          "key": "finance.revenue",
          "label": "Revenue",
          "unit": "money",
          "hub": "finance",
          "kind": "flow",
          "good": "up",
          "source_note": "Finance ledger",
          "definition": "Money in from customers over the period. Funding and payout transfers are excluded.",
          "currency": "NGN",
          "status": "ok",
          "value": 500000,
          "period": {
            "from": "2026-10-15",
            "to": "2026-11-13"
          },
          "as_of": "2026-11-13T16:29:24.291697+00:00",
          "hub_path": "/finance",
          "compare": {
            "period": {
              "from": "2026-09-15",
              "to": "2026-10-14"
            },
            "value": 0,
            "delta": 500000,
            "delta_pct": null
          },
          "series": [
            {
              "from": "2026-10-15",
              "to": "2026-10-21",
              "value": 0
            },
            {
              "from": "2026-10-22",
              "to": "2026-10-28",
              "value": 0
            },
            {
              "from": "2026-10-29",
              "to": "2026-11-04",
              "value": 0
            },
            {
              "from": "2026-11-05",
              "to": "2026-11-11",
              "value": 500000
            },
            {
              "from": "2026-11-12",
              "to": "2026-11-13",
              "value": 0
            }
          ]
        }
      },
      {
        "block_id": "e3c7da24",
        "type": "metric",
        "hub": "marketing",
        "viz": "number",
        "title": null,
        "result": {
          "key": "marketing.clicks",
          "label": "Clicks",
          "unit": "count",
          "hub": "marketing",
          "kind": "flow",
          "good": "up",
          "source_note": "Marketing metrics",
          "definition": "Recorded clicks across channels.",
          "currency": null,
          "status": "ok",
          "value": 50,
          "period": {
            "from": "2026-10-15",
            "to": "2026-11-13"
          },
          "as_of": "2026-11-13T16:29:24.303790+00:00",
          "hub_path": "/marketing",
          "compare": {
            "period": {
              "from": "2026-09-15",
              "to": "2026-10-14"
            },
            "value": null,
            "delta": null,
            "delta_pct": null
          },
          "series": []
        }
      },
      {
        "block_id": "8b2df972",
        "type": "text",
        "title": "Note",
        "text": "Clicks are paid plus organic."
      }
    ]
  },
  "meta": null
}
```

### POST /reports (201)

Body `{name, description?, blocks?, default_range?, template_key?}`. Send `template_key` OR `blocks`, not both. Max 30 blocks, 100 reports. Block ids are assigned if omitted. `hub` on a block is server-added; do not send it.

`e2e/_captures/analytics_reports/create.json`

```json
{
  "data": {
    "id": "1cdb9c97-21d5-4c24-b4e1-297935df8fe4",
    "name": "Board pack",
    "description": "For the board",
    "owner": {
      "id": "08ce6af3-2a2f-4d46-8551-61491778a7ac",
      "name": "Ada F"
    },
    "template_key": null,
    "default_range": {
      "to": null,
      "from": null,
      "range": "30d",
      "compare": "previous"
    },
    "block_count": 3,
    "version": 1,
    "can_edit": true,
    "can_share": true,
    "is_owner": true,
    "shared_with_me": false,
    "last_run": null,
    "created_at": "2026-11-13T16:29:24.328305+00:00",
    "updated_at": "2026-11-13T16:29:24.328305+00:00",
    "share_count": 0,
    "blocks": [
      {
        "id": "64c3aafd",
        "hub": "finance",
        "viz": "line",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "finance.revenue",
        "compare": null
      },
      {
        "id": "a73f8eca",
        "hub": "marketing",
        "viz": "number",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "marketing.clicks",
        "compare": null
      },
      {
        "id": "a1ae7466",
        "text": "Clicks are paid plus organic.",
        "type": "text",
        "title": "Note"
      }
    ]
  },
  "meta": null
}
```

`e2e/_captures/analytics_reports/create_from_template.json`

```json
{
  "data": {
    "id": "160b3717-d4ba-4630-a4a6-6a15a6def7e5",
    "name": "Monthly investor snapshot",
    "description": "Revenue, burn, runway, cash and pipeline for the month, with the change on the month before.",
    "owner": {
      "id": "08ce6af3-2a2f-4d46-8551-61491778a7ac",
      "name": "Ada F"
    },
    "template_key": "monthly_investor",
    "default_range": {
      "to": null,
      "from": null,
      "range": "30d",
      "compare": "previous"
    },
    "block_count": 5,
    "version": 1,
    "can_edit": true,
    "can_share": true,
    "is_owner": true,
    "shared_with_me": false,
    "last_run": null,
    "created_at": "2026-11-13T16:29:24.309498+00:00",
    "updated_at": "2026-11-13T16:29:24.309498+00:00",
    "share_count": 0,
    "blocks": [
      {
        "id": "94aea1a8",
        "hub": "finance",
        "viz": "line",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "finance.revenue",
        "compare": null
      },
      {
        "id": "04411d36",
        "hub": "finance",
        "viz": "number",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "finance.net_burn",
        "compare": null
      },
      {
        "id": "f62b0690",
        "hub": "finance",
        "viz": "number",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "finance.runway_months",
        "compare": null
      },
      {
        "id": "633202b2",
        "hub": "finance",
        "viz": "number",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "finance.cash_balance",
        "compare": null
      },
      {
        "id": "7745fa50",
        "hub": "sales",
        "viz": "number",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "sales.pipeline_value",
        "compare": null
      }
    ]
  },
  "meta": null
}
```

### GET /reports?q=&sort=updated|name|created&limit=&offset=

List (no `blocks`, has `block_count`). Founder rows also carry `share_count`.

`e2e/_captures/analytics_reports/list_empty.json`

```json
{
  "data": {
    "reports": [],
    "total": 0
  },
  "meta": null
}
```

`e2e/_captures/analytics_reports/list_mentor.json`

```json
{
  "data": {
    "reports": [
      {
        "id": "1cdb9c97-21d5-4c24-b4e1-297935df8fe4",
        "name": "Board pack Q4",
        "description": "For the board",
        "owner": {
          "id": "08ce6af3-2a2f-4d46-8551-61491778a7ac",
          "name": "Ada F"
        },
        "template_key": null,
        "default_range": {
          "to": null,
          "from": null,
          "range": "30d",
          "compare": "previous"
        },
        "block_count": 3,
        "version": 2,
        "can_edit": false,
        "can_share": false,
        "is_owner": false,
        "shared_with_me": true,
        "last_run": {
          "id": "4af80920-1d48-45c0-b9ac-6a1301da8520",
          "status": "ready",
          "as_of": "2026-11-13T16:29:26.309754+00:00",
          "error": null,
          "created_at": "2026-11-13T16:29:24.392255+00:00"
        },
        "created_at": "2026-11-13T16:29:24.328305+00:00",
        "updated_at": "2026-11-13T16:29:24.368782+00:00"
      }
    ],
    "total": 1
  },
  "meta": null
}
```

### GET /reports/{id}, as the mentor

Note the locked first block and `can_edit: false`. `last_run` may belong to someone else; it carries status and time only.

`e2e/_captures/analytics_reports/get_mentor.json`

```json
{
  "data": {
    "id": "1cdb9c97-21d5-4c24-b4e1-297935df8fe4",
    "name": "Board pack Q4",
    "description": "For the board",
    "owner": {
      "id": "08ce6af3-2a2f-4d46-8551-61491778a7ac",
      "name": "Ada F"
    },
    "template_key": null,
    "default_range": {
      "to": null,
      "from": null,
      "range": "30d",
      "compare": "previous"
    },
    "block_count": 3,
    "version": 2,
    "can_edit": false,
    "can_share": false,
    "is_owner": false,
    "shared_with_me": true,
    "last_run": {
      "id": "4af80920-1d48-45c0-b9ac-6a1301da8520",
      "status": "ready",
      "as_of": "2026-11-13T16:29:26.309754+00:00",
      "error": null,
      "created_at": "2026-11-13T16:29:24.392255+00:00"
    },
    "created_at": "2026-11-13T16:29:24.328305+00:00",
    "updated_at": "2026-11-13T16:29:24.368782+00:00",
    "blocks": [
      {
        "id": "64c3aafd",
        "type": "metric",
        "locked": true,
        "needs": "finance",
        "needs_label": "Finance"
      },
      {
        "id": "a73f8eca",
        "hub": "marketing",
        "viz": "number",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "marketing.clicks",
        "compare": null
      },
      {
        "id": "a1ae7466",
        "text": "Clicks are paid plus organic.",
        "type": "text",
        "title": "Note"
      }
    ]
  },
  "meta": null
}
```

### PATCH /reports/{id}: optimistic lock

Body must include the `version` you loaded; send only the fields you change (`name`, `description`, `blocks`, `default_range`). Success returns the report with `version + 1`. A stale version is a 409.

`e2e/_captures/analytics_reports/patch.json`

```json
{
  "data": {
    "id": "1cdb9c97-21d5-4c24-b4e1-297935df8fe4",
    "name": "Board pack Q4",
    "description": "For the board",
    "owner": {
      "id": "08ce6af3-2a2f-4d46-8551-61491778a7ac",
      "name": "Ada F"
    },
    "template_key": null,
    "default_range": {
      "to": null,
      "from": null,
      "range": "30d",
      "compare": "previous"
    },
    "block_count": 3,
    "version": 2,
    "can_edit": true,
    "can_share": true,
    "is_owner": true,
    "shared_with_me": false,
    "last_run": null,
    "created_at": "2026-11-13T16:29:24.328305+00:00",
    "updated_at": "2026-11-13T16:29:24.368782+00:00",
    "share_count": 0,
    "blocks": [
      {
        "id": "64c3aafd",
        "hub": "finance",
        "viz": "line",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "finance.revenue",
        "compare": null
      },
      {
        "id": "a73f8eca",
        "hub": "marketing",
        "viz": "number",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "marketing.clicks",
        "compare": null
      },
      {
        "id": "a1ae7466",
        "text": "Clicks are paid plus organic.",
        "type": "text",
        "title": "Note"
      }
    ]
  },
  "meta": null
}
```

`e2e/_captures/analytics_reports/patch_stale_409.json`

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "Someone else changed this report. Reload it and apply your change again.",
    "field_errors": []
  }
}
```

**FE must** on `VERSION_CONFLICT` show "Someone else changed this report", reload, and let the user re-apply. Never retry silently.

### DELETE /reports/{id} (soft)

Afterwards every read is a 404. There is no restore endpoint yet.

`e2e/_captures/analytics_reports/delete.json`

```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```

`e2e/_captures/analytics_reports/get_deleted_404.json`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Report not found.",
    "field_errors": []
  }
}
```

### POST /reports/{id}/duplicate (201)

Owner is the caller. Blocks the caller cannot see are dropped; `dropped_blocks` says how many.

`e2e/_captures/analytics_reports/duplicate.json`

```json
{
  "data": {
    "id": "b299c413-ae78-4fe8-bcab-724563d8a89e",
    "name": "Copy of Board pack Q4",
    "description": "For the board",
    "owner": {
      "id": "08ce6af3-2a2f-4d46-8551-61491778a7ac",
      "name": "Ada F"
    },
    "template_key": null,
    "default_range": {
      "to": null,
      "from": null,
      "range": "30d",
      "compare": "previous"
    },
    "block_count": 3,
    "version": 1,
    "can_edit": true,
    "can_share": true,
    "is_owner": true,
    "shared_with_me": false,
    "last_run": null,
    "created_at": "2026-11-13T16:29:26.605309+00:00",
    "updated_at": "2026-11-13T16:29:26.605309+00:00",
    "share_count": 0,
    "blocks": [
      {
        "id": "1880ff0f",
        "hub": "finance",
        "viz": "line",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "finance.revenue",
        "compare": null
      },
      {
        "id": "06b7f6b7",
        "hub": "marketing",
        "viz": "number",
        "type": "metric",
        "range": null,
        "title": null,
        "metric": "marketing.clicks",
        "compare": null
      },
      {
        "id": "cea7c19f",
        "text": "Clicks are paid plus organic.",
        "type": "text",
        "title": "Note"
      }
    ],
    "dropped_blocks": 0
  },
  "meta": null
}
```

### POST /reports/{id}/run (202), then poll the run

A double click returns the same `run_id`. Poll `GET /reports/{id}/runs/{run_id}` every 1-2 s until `status` is `ready` or `failed` (a run normally takes under a few seconds; `failed` carries `error`). Do not poll `/jobs/{id}`; the run row is the source of truth.

`e2e/_captures/analytics_reports/founder_run_start.json`

```json
{
  "data": {
    "run_id": "4af80920-1d48-45c0-b9ac-6a1301da8520",
    "job_id": "f7a53b35-48ca-44a0-b6b6-a3dda03e08ff",
    "status": "pending"
  },
  "meta": null
}
```

`e2e/_captures/analytics_reports/founder_run.json`

```json
{
  "data": {
    "id": "4af80920-1d48-45c0-b9ac-6a1301da8520",
    "report_id": "1cdb9c97-21d5-4c24-b4e1-297935df8fe4",
    "status": "ready",
    "triggered_by": "manual",
    "period": {
      "from": "2026-10-15",
      "to": "2026-11-13"
    },
    "tz": "UTC",
    "run_by": {
      "id": "08ce6af3-2a2f-4d46-8551-61491778a7ac",
      "name": "Ada F"
    },
    "as_of": "2026-11-13T16:29:26.309754+00:00",
    "error": null,
    "created_at": "2026-11-13T16:29:24.392255+00:00",
    "snapshot": {
      "report": {
        "name": "Board pack Q4",
        "range": {
          "to": "2026-11-13",
          "from": "2026-10-15",
          "range": "custom",
          "compare": "previous"
        },
        "blocks": [
          {
            "id": "64c3aafd",
            "hub": "finance",
            "viz": "line",
            "type": "metric",
            "range": null,
            "title": null,
            "metric": "finance.revenue",
            "compare": null
          },
          {
            "id": "a73f8eca",
            "hub": "marketing",
            "viz": "number",
            "type": "metric",
            "range": null,
            "title": null,
            "metric": "marketing.clicks",
            "compare": null
          },
          {
            "id": "a1ae7466",
            "text": "Clicks are paid plus organic.",
            "type": "text",
            "title": "Note"
          }
        ],
        "description": "For the board"
      },
      "results": [
        {
          "hub": "finance",
          "viz": "line",
          "type": "metric",
          "title": null,
          "result": {
            "hub": "finance",
            "key": "finance.revenue",
            "good": "up",
            "kind": "flow",
            "unit": "money",
            "as_of": "2026-11-13T16:29:24.291697+00:00",
            "label": "Revenue",
            "value": 500000,
            "period": {
              "to": "2026-11-13",
              "from": "2026-10-15"
            },
            "series": [
              {
                "to": "2026-10-21",
                "from": "2026-10-15",
                "value": 0
              },
              {
                "to": "2026-10-28",
                "from": "2026-10-22",
                "value": 0
              },
              {
                "to": "2026-11-04",
                "from": "2026-10-29",
                "value": 0
              },
              {
                "to": "2026-11-11",
                "from": "2026-11-05",
                "value": 500000
              },
              {
                "to": "2026-11-13",
                "from": "2026-11-12",
                "value": 0
              }
            ],
            "status": "ok",
            "compare": {
              "delta": 500000,
              "value": 0,
              "period": {
                "to": "2026-10-14",
                "from": "2026-09-15"
              },
              "delta_pct": null
            },
            "currency": "NGN",
            "hub_path": "/finance",
            "definition": "Money in from customers over the period. Funding and payout transfers are excluded.",
            "source_note": "Finance ledger"
          },
          "block_id": "64c3aafd"
        },
        {
          "hub": "marketing",
          "viz": "number",
          "type": "metric",
          "title": null,
          "result": {
            "hub": "marketing",
            "key": "marketing.clicks",
            "good": "up",
            "kind": "flow",
            "unit": "count",
            "as_of": "2026-11-13T16:29:24.303790+00:00",
            "label": "Clicks",
            "value": 50,
            "period": {
              "to": "2026-11-13",
              "from": "2026-10-15"
            },
            "series": [],
            "status": "ok",
            "compare": {
              "delta": null,
              "value": null,
              "period": {
                "to": "2026-10-14",
                "from": "2026-09-15"
              },
              "delta_pct": null
            },
            "currency": null,
            "hub_path": "/marketing",
            "definition": "Recorded clicks across channels.",
            "source_note": "Marketing metrics"
          },
          "block_id": "a73f8eca"
        },
        {
          "text": "Clicks are paid plus organic.",
          "type": "text",
          "title": "Note",
          "block_id": "a1ae7466"
        }
      ]
    }
  },
  "meta": null
}
```

### Runs are frozen

The founder's run above was fetched again after the ledger changed (live test asserts the snapshot is byte-identical). Editing or deleting the report never changes a run. Each viewer reads a run through their own access:

`e2e/_captures/analytics_reports/founder_run_as_mentor.json`

```json
{
  "data": {
    "id": "4af80920-1d48-45c0-b9ac-6a1301da8520",
    "report_id": "1cdb9c97-21d5-4c24-b4e1-297935df8fe4",
    "status": "ready",
    "triggered_by": "manual",
    "period": {
      "from": "2026-10-15",
      "to": "2026-11-13"
    },
    "tz": "UTC",
    "run_by": {
      "id": "08ce6af3-2a2f-4d46-8551-61491778a7ac",
      "name": "Ada F"
    },
    "as_of": "2026-11-13T16:29:26.309754+00:00",
    "error": null,
    "created_at": "2026-11-13T16:29:24.392255+00:00",
    "snapshot": {
      "report": {
        "name": "Board pack Q4",
        "range": {
          "to": "2026-11-13",
          "from": "2026-10-15",
          "range": "custom",
          "compare": "previous"
        },
        "blocks": [
          {
            "id": "64c3aafd",
            "type": "metric",
            "locked": true,
            "needs": "finance",
            "needs_label": "Finance"
          },
          {
            "id": "a73f8eca",
            "hub": "marketing",
            "viz": "number",
            "type": "metric",
            "range": null,
            "title": null,
            "metric": "marketing.clicks",
            "compare": null
          },
          {
            "id": "a1ae7466",
            "text": "Clicks are paid plus organic.",
            "type": "text",
            "title": "Note"
          }
        ],
        "description": "For the board"
      },
      "results": [
        {
          "id": "64c3aafd",
          "type": "metric",
          "locked": true,
          "needs": "finance",
          "needs_label": "Finance"
        },
        {
          "hub": "marketing",
          "viz": "number",
          "type": "metric",
          "title": null,
          "result": {
            "hub": "marketing",
            "key": "marketing.clicks",
            "good": "up",
            "kind": "flow",
            "unit": "count",
            "as_of": "2026-11-13T16:29:24.303790+00:00",
            "label": "Clicks",
            "value": 50,
            "period": {
              "to": "2026-11-13",
              "from": "2026-10-15"
            },
            "series": [],
            "status": "ok",
            "compare": {
              "delta": null,
              "value": null,
              "period": {
                "to": "2026-10-14",
                "from": "2026-09-15"
              },
              "delta_pct": null
            },
            "currency": null,
            "hub_path": "/marketing",
            "definition": "Recorded clicks across channels.",
            "source_note": "Marketing metrics"
          },
          "block_id": "a73f8eca"
        },
        {
          "text": "Clicks are paid plus organic.",
          "type": "text",
          "title": "Note",
          "block_id": "a1ae7466"
        }
      ]
    }
  },
  "meta": null
}
```

`e2e/_captures/analytics_reports/accountant_run.json`

```json
{
  "data": {
    "id": "fb31c0ad-6763-4a73-8f5c-ef97c1f7d790",
    "report_id": "1cdb9c97-21d5-4c24-b4e1-297935df8fe4",
    "status": "ready",
    "triggered_by": "manual",
    "period": {
      "from": "2026-10-15",
      "to": "2026-11-13"
    },
    "tz": "UTC",
    "run_by": {
      "id": "0709d0b8-b23d-4f28-a5e8-e14d311cf488",
      "name": "Member"
    },
    "as_of": "2026-11-13T16:29:26.556425+00:00",
    "error": null,
    "created_at": "2026-11-13T16:29:26.539196+00:00",
    "snapshot": {
      "report": {
        "name": "Board pack Q4",
        "range": {
          "to": "2026-11-13",
          "from": "2026-10-15",
          "range": "custom",
          "compare": "previous"
        },
        "blocks": [
          {
            "id": "64c3aafd",
            "hub": "finance",
            "viz": "line",
            "type": "metric",
            "range": null,
            "title": null,
            "metric": "finance.revenue",
            "compare": null
          },
          {
            "id": "a73f8eca",
            "type": "metric",
            "locked": true,
            "needs": "marketing",
            "needs_label": "Marketing"
          },
          {
            "id": "a1ae7466",
            "text": "Clicks are paid plus organic.",
            "type": "text",
            "title": "Note"
          }
        ],
        "description": "For the board"
      },
      "results": [
        {
          "hub": "finance",
          "viz": "line",
          "type": "metric",
          "title": null,
          "result": {
            "hub": "finance",
            "key": "finance.revenue",
            "good": "up",
            "kind": "flow",
            "unit": "money",
            "as_of": "2026-11-13T16:29:24.291697+00:00",
            "label": "Revenue",
            "value": 500000,
            "period": {
              "to": "2026-11-13",
              "from": "2026-10-15"
            },
            "series": [
              {
                "to": "2026-10-21",
                "from": "2026-10-15",
                "value": 0
              },
              {
                "to": "2026-10-28",
                "from": "2026-10-22",
                "value": 0
              },
              {
                "to": "2026-11-04",
                "from": "2026-10-29",
                "value": 0
              },
              {
                "to": "2026-11-11",
                "from": "2026-11-05",
                "value": 500000
              },
              {
                "to": "2026-11-13",
                "from": "2026-11-12",
                "value": 0
              }
            ],
            "status": "ok",
            "compare": {
              "delta": 500000,
              "value": 0,
              "period": {
                "to": "2026-10-14",
                "from": "2026-09-15"
              },
              "delta_pct": null
            },
            "currency": "NGN",
            "hub_path": "/finance",
            "definition": "Money in from customers over the period. Funding and payout transfers are excluded.",
            "source_note": "Finance ledger"
          },
          "block_id": "64c3aafd"
        },
        {
          "id": "a73f8eca",
          "type": "metric",
          "locked": true,
          "needs": "marketing",
          "needs_label": "Marketing"
        },
        {
          "text": "Clicks are paid plus organic.",
          "type": "text",
          "title": "Note",
          "block_id": "a1ae7466"
        }
      ]
    }
  },
  "meta": null
}
```

### GET /reports/{id}/runs

Last 20, newest first, no snapshot.

`e2e/_captures/analytics_reports/runs_list.json`

```json
{
  "data": {
    "runs": [
      {
        "id": "4af80920-1d48-45c0-b9ac-6a1301da8520",
        "report_id": "1cdb9c97-21d5-4c24-b4e1-297935df8fe4",
        "status": "ready",
        "triggered_by": "manual",
        "period": {
          "from": "2026-10-15",
          "to": "2026-11-13"
        },
        "tz": "UTC",
        "run_by": {
          "id": "08ce6af3-2a2f-4d46-8551-61491778a7ac",
          "name": "Ada F"
        },
        "as_of": "2026-11-13T16:29:26.309754+00:00",
        "error": null,
        "created_at": "2026-11-13T16:29:24.392255+00:00"
      }
    ]
  },
  "meta": null
}
```

### GET/PUT /reports/{id}/shares (founder only)

GET returns `shares` and `candidates` (eligible active members). PUT replaces the list with `{user_ids}`. Eligible: team member, accountant, mentor, business consultant (not the owner). Anything else, including unknown ids, is the same 422. Removing a share makes the report a 404 for that person again. Newly added people get an in-app notification.

`e2e/_captures/analytics_reports/shares_before.json`

```json
{
  "data": {
    "shares": [],
    "candidates": [
      {
        "user_id": "262d0e5e-3f79-497e-a46c-63fd87504614",
        "name": "Member",
        "role": "team_member",
        "locked_hubs": []
      },
      {
        "user_id": "0709d0b8-b23d-4f28-a5e8-e14d311cf488",
        "name": "Member",
        "role": "accountant",
        "locked_hubs": [
          "marketing"
        ]
      },
      {
        "user_id": "3b487d58-8b98-4c49-88fc-d79169536085",
        "name": "Member",
        "role": "mentor",
        "locked_hubs": [
          "finance"
        ]
      }
    ]
  },
  "meta": null
}
```

`e2e/_captures/analytics_reports/shares_put.json`

```json
{
  "data": {
    "shares": [
      {
        "user_id": "0709d0b8-b23d-4f28-a5e8-e14d311cf488",
        "name": "Member",
        "role": "accountant",
        "locked_hubs": [
          "marketing"
        ]
      },
      {
        "user_id": "3b487d58-8b98-4c49-88fc-d79169536085",
        "name": "Member",
        "role": "mentor",
        "locked_hubs": [
          "finance"
        ]
      }
    ],
    "candidates": [
      {
        "user_id": "262d0e5e-3f79-497e-a46c-63fd87504614",
        "name": "Member",
        "role": "team_member",
        "locked_hubs": []
      }
    ]
  },
  "meta": null
}
```

`e2e/_captures/analytics_reports/shares_put_422.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Pick active members of this workspace who can be given access.",
    "field_errors": [
      {
        "field": "user_ids",
        "message": "Pick active members of this workspace who can be given access."
      }
    ]
  }
}
```

`e2e/_captures/analytics_reports/shares_mentor_403.json`

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

### Notification (mentor, after the share)

Kinds `analytics.report.shared` (to the person shared with) and `analytics.report.generated` (to whoever started the run). In-app only; no email.

`e2e/_captures/analytics_reports/notifications_mentor.json`

```json
{
  "data": {
    "notifications": [
      {
        "id": "f0b88cca-39eb-4f1a-a01c-adeede349f53",
        "type": "analytics.report.shared",
        "title": "A report was shared with you: Board pack Q4",
        "body": "Open",
        "data": {
          "actor_id": "08ce6af3-2a2f-4d46-8551-61491778a7ac",
          "user_ids": [
            "3b487d58-8b98-4c49-88fc-d79169536085",
            "0709d0b8-b23d-4f28-a5e8-e14d311cf488"
          ],
          "report_id": "1cdb9c97-21d5-4c24-b4e1-297935df8fe4",
          "startup_id": "3155ebb1-cef2-4e6c-97dc-8a28bb00fc4f",
          "report_name": "Board pack Q4"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "other",
        "created_at": "2026-11-13T16:29:26.417072+00:00"
      },
      {
        "id": "2c47558a-4d8b-4013-9149-39cc0260de2f",
        "type": "workspace.member.joined",
        "title": "A new member joined your workspace",
        "body": "",
        "data": {
          "role": "investor",
          "user_id": "994eafa4-6b41-4379-81cb-bbecabb7dd3d",
          "startup_id": "3155ebb1-cef2-4e6c-97dc-8a28bb00fc4f"
        },
        "read": false,
        "archived": false,
        "priority": "normal",
        "module": "team",
        "created_at": "2026-11-13T16:29:24.110218+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

## 5. Errors the FE must handle

**422 plain text only (names, descriptions, text blocks reject HTML)**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Plain text only. Remove the HTML tags.",
    "field_errors": [
      {
        "field": "name",
        "message": "Plain text only. Remove the HTML tags."
      }
    ]
  }
}
```

**422 unknown metric key**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "That metric does not exist.",
    "field_errors": [
      {
        "field": "blocks[0].metric",
        "message": "That metric does not exist."
      }
    ]
  }
}
```

**404 not visible / not yours / other workspace**

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Report not found.",
    "field_errors": []
  }
}
```

**403 read-only role**

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

**403 role outside Analytics**

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

## 6. Traps

- `blocks` is only on `GET /reports/{id}` (and create/patch/duplicate responses); the list has `block_count`.
- `default_range` is `{range, from, to, compare}`; `from`/`to` are null unless `range` is `"custom"`. A block's own `range` is `null` (use the report's) or `{range, from, to}`.
- A run's `snapshot.report.range` is always explicit dates (`range: "custom"`), the exact days the run covers.
- Runs: `period` is `{from, to}` at the top, `snapshot.results[i].result` is the slice A resolve shape; text blocks appear as `{block_id, type: "text", title, text}`.
- A metric whose source broke shows `{"status": "error"}` inside its result; a retired custom metric shows `{"status": "unknown"}`.
- Notifications: `data.user_ids` is internal; use `type` plus `data.report_id` to deep link.

## 7. Verification

| Behaviour | Verified |
|---|---|
| Templates, preview, create, list, get, patch, 409, duplicate, delete | live (this run) |
| Run start, poll, frozen snapshot, mentor and accountant redaction | live |
| Shares put/get/422/403, removal gives 404 | live |
| Notifications `shared` and `generated` | live (`notifications_mentor.json`, mentor); generated seen in unit tests |
| Team member editing only own reports; cross-workspace 404 | unit tests (`tests/api/test_analytics_reports.py`), not in the live capture |
| Rate limit 429 on preview/run | not exercised live (limit is 40 heavy reads a minute per workspace) |
| `failed` run (requester lost access) | unit test only |
| Plan gate (402) | dark until `PLAN_ENFORCEMENT` is on; not exercised |
