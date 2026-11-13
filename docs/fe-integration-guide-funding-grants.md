# FE Integration Guide: Funding Hub (Module 14, slice E: grants)

Every body below is copied from a live run of `e2e/test_funding_grants.py` against a running server
(captures in `e2e/_captures/funding_grants/`, one file per body, named in each section). The AI parts
(match scores and reasons, drafted answers) ran with a **canned model**: the text in `why` and in
drafted `answer` is the fake's, not a real model's. Where something could not be run live, it says so.
IDs, emails and names are placeholders from a throwaway test workspace.

**The Grants tab stays hidden in the sidebar** (decision of 2026-11-12: Funding grants are built but
not shown). Build the page, route it, do not add the sidebar entry until told.

## 1. What the feature is

A curated, human-checked catalogue of grants, credits and programmes. Every week a job picks the best
ten for the workspace (plain code filters on country, then a model scores the ten and writes one line on
why each fits). The founder can dismiss a suggestion, mark it "not relevant" (both stay hidden on later
runs), or **Start application**: that creates a tracker row with starter questions, each of which can be
drafted with AI. A grant the catalogue does not have can be added by hand.

## 2. Who can call what

| Role | Access |
|---|---|
| founder, team_member | every route below |
| mentor, accountant, legal_advisor, business_consultant, investor | 403 `FORBIDDEN` on every route (captured: `mentor_403`) |

Behind the `funding` plan module: a workspace without it gets 402 `PLAN_REQUIRED` (not exercised live,
the e2e server runs with enforcement off; the shape is the same as the other Funding routes, covered by a
unit test). Role is checked first, then the plan.

All bodies use the envelope `{"data": ..., "meta": null}`. Errors use `{"error": {"code", "message", "field_errors"}}`.
Money is `{"minor": <integer>, "currency": "<ISO>"}` in minor units (200,000 USD is `20000000`).

## 3. The catalogue: `GET /funding/grants`

Query: `q` (text, max 100), `only_open=true`, `kind=cash|in_kind`. Anything else for `kind` is 422.

```json
{
  "data": {
    "items": [
      {
        "id": "63183886-e215-4d02-8122-c66f63a8bd80",
        "slug": "aws-activate",
        "name": "AWS Activate",
        "funder": "Amazon Web Services",
        "kind": "credits (in-kind)",
        "in_kind": true,
        "amount_min": null,
        "amount_max": {
          "minor": 20000000,
          "currency": "USD"
        },
        "amount_note": "Up to USD 200,000 in AWS Activate credits, with additional credits for AI startups (page).",
        "deadline_text": "rolling",
        "status_text": "open",
        "closed": false,
        "regions": "not restricted on the page fetched",
        "sectors": "any",
        "stages": "startups (see page)",
        "eligibility": "Apply through the page.",
        "source_url": "https://aws.amazon.com/startups/",
        "verified_on": "2026-11-12",
        "verification": "verified",
        "stale": false,
        "notes": "Credits, not cash."
      }
    ],
    "disclaimer": "Grant details change. Check the funder's own page before you apply: we show when each row was last checked and link to its source."
  },
  "meta": null
}
```

(trimmed to one of 15 rows; `catalogue_filtered` is the same shape.)

Field notes the FE must respect:

- `source_url` and `verified_on` are on **every** row. Show "Checked 12 Nov 2026" and link the source.
- `verification` is `verified` or `partial`. Show a "details not fully confirmed" tag on `partial`.
- `stale: true` means the row has not been re-checked for 180 days: show "may be out of date".
- `closed: true` means the call was closed when the row was checked. Still listed, label it.
- `in_kind: true` is credits or a programme, not cash. Do not add these amounts to a cash total.
- `deadline_text` and `status_text` are **free text** from the funder ("annual: portal opens 1 January"),
  not dates. Never parse them. `amount_min`, `amount_max` and `currency` may each be `null`.
- `disclaimer` is wording to show on the page.

Bad filter:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "query.kind",
        "message": "String should match pattern '^(cash|in_kind)$'"
      }
    ]
  }
}
```

## 4. Matches

### 4.1 Read: `GET /funding/grants/matches?state=suggested|dismissed|not_relevant|applying`

Default is `suggested`, best first. Before any run:

```json
{
  "data": {
    "items": [],
    "refresh": {
      "status": "never",
      "last_run_at": null
    },
    "disclaimer": "Grant details change. Check the funder's own page before you apply: we show when each row was last checked and link to its source."
  },
  "meta": null
}
```

After a run (one of ten items shown):

```json
{
  "data": {
    "items": [
      {
        "id": "191b5100-eb31-4a0c-8a9a-dfc14e77bdfc",
        "score": 95,
        "why": "Fits your clinic startup, aws-activate.",
        "ai_scored": true,
        "state": "suggested",
        "matched_at": "2026-11-13T14:22:19.446083+00:00",
        "grant": {
          "id": "63183886-e215-4d02-8122-c66f63a8bd80",
          "slug": "aws-activate",
          "name": "AWS Activate",
          "funder": "Amazon Web Services",
          "kind": "credits (in-kind)",
          "in_kind": true,
          "amount_min": null,
          "amount_max": {
            "minor": 20000000,
            "currency": "USD"
          },
          "amount_note": "Up to USD 200,000 in AWS Activate credits, with additional credits for AI startups (page).",
          "deadline_text": "rolling",
          "status_text": "open",
          "closed": false,
          "regions": "not restricted on the page fetched",
          "sectors": "any",
          "stages": "startups (see page)",
          "eligibility": "Apply through the page.",
          "source_url": "https://aws.amazon.com/startups/",
          "verified_on": "2026-11-12",
          "verification": "verified",
          "stale": false,
          "notes": "Credits, not cash."
        }
      }
    ],
    "refresh": {
      "status": "idle",
      "last_run_at": "2026-11-13T14:22:19.446083+00:00"
    },
    "disclaimer": "Grant details change. Check the funder's own page before you apply: we show when each row was last checked and link to its source."
  },
  "meta": null
}
```

`refresh.status` is `never` | `queued` | `running` | `idle`. `last_run_at` is `null` while queued or running.
`ai_scored: false` means the model was unavailable or the daily AI allowance was spent, so `score` and
`why` came from plain rules (the page can stay the same; optionally show a small "quick match" tag).

### 4.2 Run a fresh match: `POST /funding/grants/matches/refresh`

202 when queued:

```json
{
  "data": {
    "deduplicated": false,
    "refresh": {
      "status": "queued",
      "last_run_at": null
    }
  },
  "meta": null
}
```

A run already queued or running returns **200** with `deduplicated: true` and queues nothing:

```json
{
  "data": {
    "deduplicated": true,
    "refresh": {
      "status": "queued",
      "last_run_at": null
    }
  },
  "meta": null
}
```

Poll `GET /funding/grants/matches` every 3 seconds while `refresh.status` is `queued` or `running`, and
stop at `idle`. While queued the list is whatever it was before (`matches_queued`: empty on a first run).
The same run also happens by itself once a week; a founder who is on the page is not told in real time.

### 4.3 Dismiss, not relevant, restore

`POST /funding/grants/matches/{id}/dismiss`, `/not-relevant`, `/restore`. No body.

```json
{
  "data": {
    "id": "191b5100-eb31-4a0c-8a9a-dfc14e77bdfc",
    "state": "dismissed"
  },
  "meta": null
}
```

`match_not_relevant` and `match_restored` have the same shape with `state` `not_relevant` and `suggested`.
Dismissed and not-relevant grants are **never suggested again** by later runs, until restored. Show them
under a "Hidden" filter via `?state=dismissed` / `?state=not_relevant` (`matches_dismissed_list`).

A suggestion that stops ranking in the top ten simply disappears on the next run (it is not an error).
Dismiss while an application exists is refused:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "You are already applying to this grant. Remove the application first.",
    "field_errors": [
      {
        "field": "state",
        "message": "You are already applying to this grant. Remove the application first."
      }
    ]
  }
}
```

## 5. Start an application: `POST /funding/grants/{grant_id}/apply`

`grant_id` is the **catalogue grant's** id (`match.grant.id`), not the match id. 201 the first time, with
the application, its four starter questions and the catalogue row:

```json
{
  "data": {
    "id": "347e5951-0622-4973-979b-609deaac17e2",
    "grant_id": "63183886-e215-4d02-8122-c66f63a8bd80",
    "name": "AWS Activate",
    "funder": "Amazon Web Services",
    "status": "planning",
    "deadline": null,
    "expired": false,
    "amount": {
      "minor": 20000000,
      "currency": "USD"
    },
    "notes": null,
    "submitted_at": null,
    "created_at": "2026-11-13T14:22:19.703487+00:00",
    "source_url": "https://aws.amazon.com/startups/",
    "catalogue_stale": false,
    "catalogue_deadline_text": "rolling",
    "answers": [
      {
        "id": "ebacb1f2-c3f7-4c19-8951-18236ad3b7b7",
        "position": 0,
        "question": "What problem are you solving, and for whom?",
        "answer": "",
        "ai_drafted": false,
        "draft_status": null
      }
    ],
    "grant": {
      "id": "63183886-e215-4d02-8122-c66f63a8bd80",
      "slug": "aws-activate",
      "name": "AWS Activate",
      "funder": "Amazon Web Services",
      "kind": "credits (in-kind)",
      "in_kind": true,
      "amount_min": null,
      "amount_max": {
        "minor": 20000000,
        "currency": "USD"
      },
      "amount_note": "Up to USD 200,000 in AWS Activate credits, with additional credits for AI startups (page).",
      "deadline_text": "rolling",
      "status_text": "open",
      "closed": false,
      "regions": "not restricted on the page fetched",
      "sectors": "any",
      "stages": "startups (see page)",
      "eligibility": "Apply through the page.",
      "source_url": "https://aws.amazon.com/startups/",
      "verified_on": "2026-11-12",
      "verification": "verified",
      "stale": false,
      "notes": "Credits, not cash."
    }
  },
  "meta": null
}
```

(Trimmed to the first of four starter answers.) Calling it again for the same grant returns the same
application with **200** (`apply_existing`). The match moves to `state: "applying"`
(`GET /funding/grants/matches?state=applying`). An unknown or retired grant is 404.

## 6. The tracker

### 6.1 List: `GET /funding/grant-applications`

```json
{
  "data": {
    "items": [
      {
        "id": "07c1096f-2065-4603-b616-7ae18fcfcbe2",
        "grant_id": null,
        "name": "Missed call",
        "funder": "",
        "status": "planning",
        "deadline": "2026-11-10",
        "expired": true,
        "amount": null,
        "notes": null,
        "submitted_at": null,
        "created_at": "2026-11-13T14:22:20.210580+00:00",
        "source_url": null,
        "catalogue_stale": null,
        "catalogue_deadline_text": null
      },
      {
        "id": "7ad36588-dc7b-4556-931c-8bea9b9943b5",
        "grant_id": null,
        "name": "State innovation fund",
        "funder": "Lagos State",
        "status": "planning",
        "deadline": "2026-12-04",
        "expired": false,
        "amount": {
          "minor": 500000000,
          "currency": "NGN"
        },
        "notes": null,
        "submitted_at": null,
        "created_at": "2026-11-13T14:22:20.191018+00:00",
        "source_url": null,
        "catalogue_stale": null,
        "catalogue_deadline_text": null
      }
    ],
    "counts": {
      "planning": 2,
      "drafting": 0,
      "submitted": 1,
      "awarded": 0,
      "rejected": 0,
      "withdrawn": 0
    },
    "expired": 1
  },
  "meta": null
}
```

(Trimmed to two items.) Order: soonest deadline first, no-deadline last. `counts` is per status for the
tab badges. `expired: true` on an item means the deadline passed and it is still `planning` or `drafting`:
show it in a warning colour with "Deadline passed". `expired` at the top level is that count.

In list items the `answers` and `grant` objects are **absent**; they appear only on the detail routes
(trap: do not read `answers` from the list).

### 6.2 Add by hand: `POST /funding/grant-applications`

Body: `name` (required), `funder`, `deadline` (`YYYY-MM-DD`), `amount_minor` + `currency` (a currency is
required with an amount), `notes`. 201 with the same detail shape as 5 (with `grant_id`, `source_url`,
`grant` all `null`):

```json
{
  "data": {
    "id": "7ad36588-dc7b-4556-931c-8bea9b9943b5",
    "grant_id": null,
    "name": "State innovation fund",
    "funder": "Lagos State",
    "status": "planning",
    "deadline": "2026-12-04",
    "expired": false,
    "amount": {
      "minor": 500000000,
      "currency": "NGN"
    },
    "notes": null,
    "submitted_at": null,
    "created_at": "2026-11-13T14:22:20.191018+00:00",
    "source_url": null,
    "catalogue_stale": null,
    "catalogue_deadline_text": null,
    "answers": [
      {
        "id": "1ffb38d7-5288-4a6f-ac50-5b053815b685",
        "position": 0,
        "question": "What problem are you solving, and for whom?",
        "answer": "",
        "ai_drafted": false,
        "draft_status": null
      },
      {
        "id": "eae2c850-45b5-4aca-8d7b-fdadfda9efa6",
        "position": 1,
        "question": "What traction do you have so far?",
        "answer": "",
        "ai_drafted": false,
        "draft_status": null
      },
      {
        "id": "27c87269-2d56-4405-b40b-0658f030a1b3",
        "position": 2,
        "question": "How would you use the funding?",
        "answer": "",
        "ai_drafted": false,
        "draft_status": null
      },
      {
        "id": "a121abc4-66d7-4fbd-ab18-ae8126f7c3cc",
        "position": 3,
        "question": "Who is on the team, and why are you the right people?",
        "answer": "",
        "ai_drafted": false,
        "draft_status": null
      }
    ],
    "grant": null
  },
  "meta": null
}
```

Amount without a currency:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "An amount needs a currency.",
    "field_errors": [
      {
        "field": "currency",
        "message": "An amount needs a currency."
      }
    ]
  }
}
```

### 6.3 Detail, edit, delete

- `GET /funding/grant-applications/{id}`: the detail shape of 5.
- `PATCH /funding/grant-applications/{id}`: any of `name`, `funder`, `status`, `deadline`, `amount_minor`,
  `currency`, `notes`. Statuses: `planning`, `drafting`, `submitted`, `awarded`, `rejected`, `withdrawn`.
  Moving to `submitted` stamps `submitted_at` once (later moves do not change it).

```json
{
  "data": {
    "id": "347e5951-0622-4973-979b-609deaac17e2",
    "grant_id": "63183886-e215-4d02-8122-c66f63a8bd80",
    "name": "AWS Activate",
    "funder": "Amazon Web Services",
    "status": "submitted",
    "deadline": null,
    "expired": false,
    "amount": {
      "minor": 20000000,
      "currency": "USD"
    },
    "notes": null,
    "submitted_at": "2026-11-13T14:22:20.230729+00:00",
    "created_at": "2026-11-13T14:22:19.703487+00:00",
    "source_url": "https://aws.amazon.com/startups/",
    "catalogue_stale": false,
    "catalogue_deadline_text": "rolling",
    "answers": [
      {
        "id": "ebacb1f2-c3f7-4c19-8951-18236ad3b7b7",
        "position": 0,
        "question": "What problem are you solving, and for whom?",
        "answer": "We help clinics book visits. [add your traction]",
        "ai_drafted": true,
        "draft_status": null
      }
    ],
    "grant": {
      "id": "63183886-e215-4d02-8122-c66f63a8bd80",
      "slug": "aws-activate",
      "name": "AWS Activate",
      "funder": "Amazon Web Services",
      "kind": "credits (in-kind)",
      "in_kind": true,
      "amount_min": null,
      "amount_max": {
        "minor": 20000000,
        "currency": "USD"
      },
      "amount_note": "Up to USD 200,000 in AWS Activate credits, with additional credits for AI startups (page).",
      "deadline_text": "rolling",
      "status_text": "open",
      "closed": false,
      "regions": "not restricted on the page fetched",
      "sectors": "any",
      "stages": "startups (see page)",
      "eligibility": "Apply through the page.",
      "source_url": "https://aws.amazon.com/startups/",
      "verified_on": "2026-11-12",
      "verification": "verified",
      "stale": false,
      "notes": "Credits, not cash."
    }
  },
  "meta": null
}
```

- Bad status:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "status",
        "message": "Input should be 'planning', 'drafting', 'submitted', 'awarded', 'rejected' or 'withdrawn'"
      }
    ]
  }
}
```

- `DELETE /funding/grant-applications/{id}`: returns 200, and the grant **comes back as a suggestion**
  (if it was matched). The answers are deleted with it. Confirm before deleting.

```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```

- Another workspace's id, or one that does not exist:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

## 7. Answers and "Draft with AI"

- `POST /funding/grant-applications/{id}/answers` body `{"question": "...", "answer": ""}` (max 20 per
  application). 201: `answer_added`. `PATCH .../answers/{answer_id}` with `question` and/or `answer`
  (`answer_edited`); typing an answer clears `ai_drafted` (it is the founder's own words now).
  `DELETE` returns `{"deleted": true}` (`answer_deleted`).

```json
{
  "data": {
    "id": "f16bf9bf-998c-4e38-884f-6e2fdd81c6b1",
    "position": 1,
    "question": "What traction do you have so far?",
    "answer": "We serve 40 clinics in Lagos.",
    "ai_drafted": false,
    "draft_status": null
  },
  "meta": null
}
```

- `POST .../answers/{answer_id}/draft` body `{}` or `{"overwrite": true}`. **202** with the answer in
  `draft_status: "queued"`:

```json
{
  "data": {
    "id": "ebacb1f2-c3f7-4c19-8951-18236ad3b7b7",
    "position": 0,
    "question": "What problem are you solving, and for whom?",
    "answer": "",
    "ai_drafted": false,
    "draft_status": "queued"
  },
  "meta": null
}
```

Poll `GET /funding/grant-applications/{id}` every 3 seconds until that answer's `draft_status` is `null`
(drafted: `answer` has the text and `ai_drafted: true`) or `"failed"` (show "Could not draft, try again";
the button works again). After a draft:

```json
{
  "data": {
    "id": "347e5951-0622-4973-979b-609deaac17e2",
    "grant_id": "63183886-e215-4d02-8122-c66f63a8bd80",
    "name": "AWS Activate",
    "funder": "Amazon Web Services",
    "status": "planning",
    "deadline": null,
    "expired": false,
    "amount": {
      "minor": 20000000,
      "currency": "USD"
    },
    "notes": null,
    "submitted_at": null,
    "created_at": "2026-11-13T14:22:19.703487+00:00",
    "source_url": "https://aws.amazon.com/startups/",
    "catalogue_stale": false,
    "catalogue_deadline_text": "rolling",
    "answers": [
      {
        "id": "ebacb1f2-c3f7-4c19-8951-18236ad3b7b7",
        "position": 0,
        "question": "What problem are you solving, and for whom?",
        "answer": "We help clinics book visits. [add your traction]",
        "ai_drafted": true,
        "draft_status": null
      }
    ],
    "grant": {
      "id": "63183886-e215-4d02-8122-c66f63a8bd80",
      "slug": "aws-activate",
      "name": "AWS Activate",
      "funder": "Amazon Web Services",
      "kind": "credits (in-kind)",
      "in_kind": true,
      "amount_min": null,
      "amount_max": {
        "minor": 20000000,
        "currency": "USD"
      },
      "amount_note": "Up to USD 200,000 in AWS Activate credits, with additional credits for AI startups (page).",
      "deadline_text": "rolling",
      "status_text": "open",
      "closed": false,
      "regions": "not restricted on the page fetched",
      "sectors": "any",
      "stages": "startups (see page)",
      "eligibility": "Apply through the page.",
      "source_url": "https://aws.amazon.com/startups/",
      "verified_on": "2026-11-12",
      "verification": "verified",
      "stale": false,
      "notes": "Credits, not cash."
    }
  },
  "meta": null
}
```

An answer that already has text needs `overwrite: true`, so ask "Replace what you wrote?" first:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This answer already has text. Confirm to replace it.",
    "field_errors": [
      {
        "field": "answer",
        "message": "This answer already has text. Confirm to replace it."
      }
    ]
  }
}
```

Text the founder types while a draft is running is **never overwritten** (the draft is dropped, the
status clears). If the daily AI allowance is spent the route returns 429 `AI_BUDGET_EXHAUSTED` (unit test,
not live). Drafts contain bracketed placeholders like `[add your monthly revenue]` where the profile lacks
a fact: highlight them.

## 8. Notification

`funding.grant.matched` goes to founders and team members when a run finds suggestions not seen before:
"N new grant matches for you, starting with <name>", opens `/funding/grants`. Not exercised live here
(checked in the unit tests); the live run emitted the event, visible in the server log.

**Off by default (2026-11-13 fix):** the module is hidden, so the weekly run and this notification are
behind the server setting `FUNDING_GRANT_MATCHING_ENABLED` (default `false`). While it is off, no weekly run is
scheduled and no `funding.grant.matched` notification is sent, even for a manual refresh. `POST
/grants/matches/refresh` itself is unchanged (same 202 body and same matches); no response shape changed.

## 9. Traps and UX the FE must surface

1. **List vs detail**: tracker items have no `answers` or `grant`; detail has both.
2. **Match id vs grant id**: dismiss/restore take the match id; apply takes the grant id.
3. **Free text deadlines**: never parse `deadline_text`. Only application `deadline` is a real date.
4. **Always show source and checked date**, and the `disclaimer`. A grant may have changed since.
5. **Credits are not cash** (`in_kind`): keep them out of any money total.
6. **Hidden suggestions are permanent** until restored: say so on the Dismiss and Not relevant buttons.
7. **Deleting an application deletes its answers**: confirm.
8. **AI drafts are drafts**: label them, keep the placeholders visible, never submit them for the founder.
9. Hidden module: no sidebar entry.

## 10. Verification table

| Behaviour | Verified |
|---|---|
| Catalogue list, filters, bad filter 422 | live (`e2e/test_funding_grants.py`) |
| Refresh 202, dedupe 200, queued and idle states, ranked matches | live (**canned model**) |
| Dismiss, not relevant, restore, hidden lists, persistence across a second run | live |
| Start application 201/200, applying list, dismiss-while-applying 422 | live |
| Draft 202, poll, overwrite guard 422, edit, add, delete answer | live (canned model) |
| Manual add, status move, `submitted_at`, expired, counts, delete, 404 | live |
| Team member 200, mentor 403 | live (real invitations) |
| Country filter (US excluded from Africa-only grants), plain fallback when the model fails or the allowance is spent, hostile model output clamped | unit tests |
| Weekly schedule (one run per workspace per ISO week), plan check inside the job | unit tests |
| Notification to founders and team members only, none when nothing is new | unit tests |
| Weekly run and notification are off unless `FUNDING_GRANT_MATCHING_ENABLED`; manual refresh never notifies while off | unit tests |
| Draft never overwrites text typed meanwhile, dead job reconcile, 429 on spent allowance | unit tests |
| 402 `PLAN_REQUIRED` gate | unit tests (not live: e2e runs with enforcement off) |
| A real model's scoring and drafting quality | **not exercised** |
