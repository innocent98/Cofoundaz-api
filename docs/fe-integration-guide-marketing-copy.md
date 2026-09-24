# FE Integration Guide — Marketing Hub: AI Content Generation (Module 10, Slice 3a)

> **Provenance.** `e2e/test_marketing.py::test_marketing_ai_generation_journey` has been run
> (`bash scripts/e2e_run.sh`, 55/55 e2e passed) and every response body in §1–§5 below is pasted
> **verbatim** from the captures it wrote to `e2e/_captures/marketing/`
> (`copy_generate_accepted.json`, `copy_generation_ready.json`, `copy_generations_history.json`,
> `plan_week_accepted.json`, `plan_week_ready.json`). Nothing here is hand-written or "tidied"
> from the schema or from memory. The captures were taken with `LLM_PROVIDER=stub` (this repo's
> e2e default — no real LLM call, no network) — every place that matters is flagged inline below,
> most importantly §4's empty `entries` array, which is **real stub behavior**, not a mistake (see
> the boxed note in §4).
>
> Not e2e-captured: the `failed`/`over_budget` status (forcing a real over-budget failure would
> require seeding the `llm_usage_daily` ledger directly, breaking the shared e2e process's budget
> guarantee for every other test — see `docs/fe-integration-guide-ai-status.md`), the kind-mismatch
> 404, and the RBAC 403 matrix. Those are unit/integration-tested; flagged inline and again in the
> verification table.

This extends **`docs/fe-integration-guide-marketing-calendar.md`** (Slice 1 — Content Calendar +
Channels + Overview) and **`docs/fe-integration-guide-marketing-campaigns.md`** (Slice 2 —
Campaigns + Audience Segments). Same base path, same auth model, same envelope. Read those guides
first if you haven't integrated the Marketing Hub yet; this one only covers what Slice 3a adds:
**AI-generated ad/social/email copy** and an **AI-proposed 7-day content calendar**. It also
cross-references **`docs/fe-integration-guide-ai-status.md`** for the shared
generating/ready/failed + `over_budget` async pattern every AI-generation feature in this API uses.

Base path: `/api/v1/marketing`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header, same as every other tenant-scoped
endpoint in this API.

**Access: founder or team_member only.** Every route below hangs off the same
`require_role(MembershipRole.founder, MembershipRole.team_member)` dependency (`_marketing`) as
every other `/marketing` route. A mentor, accountant, legal advisor, business consultant, or
investor membership gets **403 `FORBIDDEN`** on all six routes in this guide — including the two
`{id}` GET routes, which reject on RBAC *before* looking up the row (so a forbidden role gets 403
even for an id that doesn't exist). ⚠️ Not e2e-captured; verified by
`tests/api/test_marketing_ai.py::test_rbac_forbidden` (parametrized over `mentor`/`investor`,
covers all 4 mutating/list routes plus both `{id}` GET routes).

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see Errors).

**Table:** both generation kinds live in one table, `marketing_ai_generations`, discriminated by a
`kind` column (`copy` | `plan_week`) — see the field-nesting note in §0.

---

## 0. The shape every consumer must build against: POST-then-poll, one table, two kinds

Both AI features use the **identical async pattern**:

```
POST …/generate (or /plan-week)  ->  202 {id, status: "generating"}
GET …/{id}                       ->  200 {..., status: "generating" | "ready" | "failed", ...}
```

Poll `GET …/{id}` until `status` is `ready` or `failed` — there is no webhook/SSE push for these
two generations (unlike the realtime notification feed used elsewhere in this API). A sensible
poll cadence is the same one used for every other lazy-generate-then-upgrade AI feature in this
API (assessment narrative, business-plan generator, onboarding panel): a few seconds, backing off,
capped at a reasonable timeout (these are LLM calls — seconds, not milliseconds, even under the
stub provider in this environment).

**Both `copy` and `plan_week` generations are rows in the same `marketing_ai_generations` table,
discriminated by `kind`.** This is why the two features share one response shape
(`GenerationResponse` — see §1/§4) and one failure mode (`over_budget`, §6), but are served through
**separate routes per kind** (`/copy/generate` + `/copy/generations/{id}` vs.
`/calendar/plan-week` + `/calendar/plan-week/{id}`) rather than one generic `/generations/{id}`.
**A `plan_week` id is 404 on the copy route and vice versa** — see §5's kind-mismatch note. Do not
build a single "poll any generation id" helper that ignores which feature created it; keep the
kind's route paired with its own id.

---

## Enums

```
MarketingGenerationKind   = copy | plan_week
MarketingGenerationStatus = generating | ready | failed
AssetType                 = ad | social_post | email | landing_headline | product_description
CopyTone                  = bold | friendly | expert | playful
```

`channel` (optional, on the copy request only) is the same 8-value `ChannelKey` enum from Slice 1:
`organic_social | paid_social | search | email | content_seo | partnerships | events | referral`.

---

## 1. `POST /api/v1/marketing/copy/generate` — start a copy generation

**Request** (from the e2e journey — this exact body produced `copy_generate_accepted.json` below):

```json
{
  "asset_type": "ad",
  "channel": "email",
  "tone": "bold",
  "key_message": "Launch week is here"
}
```

| Field | Type | Rules |
|---|---|---|
| `asset_type` | `AssetType` | Required |
| `tone` | `CopyTone` | Required |
| `key_message` | string, 1–2000 chars | Required |
| `channel` | `ChannelKey` \| null | Optional — omit for channel-agnostic copy |
| `audience_segment_id` | UUID \| null | Optional. Must reference a segment in **this** workspace (Slice 2) — see the 422 below |
| `cta` | string, ≤200 chars \| null | Optional |

**`e2e/_captures/marketing/copy_generate_accepted.json` — 202:**

```json
{
  "data": {
    "id": "72f64ccc-046a-4da3-8c23-099feac1f0a3",
    "status": "generating"
  },
  "meta": null
}
```

**This response is intentionally thin** — just `{id, status}`, not the full `GenerationResponse`
shape §2 returns. Store `id` and start polling §2; there is nothing else to read from the 202
body.

### The `422` you must handle: `audience_segment_id` must be a segment in this workspace

Sending an `audience_segment_id` that doesn't exist, or belongs to another workspace, is rejected —
**422 `VALIDATION_ERROR`**, `field_errors: [{"field": "audience_segment_id", "message": "Unknown
segment for this workspace."}]`. Same one-query tenancy-scoped rejection pattern Slice 2 already
established for `persona_id` on segments. ⚠️ Not e2e-captured (the journey omits
`audience_segment_id`); verified by `app/services/marketing/ai_content.py::create_copy_generation`
and its unit tests.

---

## 2. `GET /api/v1/marketing/copy/generations/{generation_id}` — poll a copy generation

`e2e/_captures/marketing/copy_generation_ready.json` — captured after the in-process worker
drained the job (`LLM_PROVIDER=stub`):

```json
{
  "data": {
    "id": "72f64ccc-046a-4da3-8c23-099feac1f0a3",
    "startup_id": "d46495a7-e6c4-4e6a-8e2c-e26e7946b093",
    "kind": "copy",
    "status": "ready",
    "inputs": {
      "cta": null,
      "tone": "bold",
      "channel": "email",
      "asset_type": "ad",
      "key_message": "Launch week is here",
      "audience_segment_id": null
    },
    "output": {
      "variants": [
        "[stub-llm] variants"
      ]
    },
    "error": null,
    "created_at": "2026-09-24T21:06:34.147530Z",
    "updated_at": "2026-09-24T21:06:34.183341Z"
  },
  "meta": null
}
```

| Field | Meaning |
|---|---|
| `inputs` | The exact request body from §1, round-tripped as stored JSON (note `cta`/`channel`/`audience_segment_id` appear explicitly as `null` when omitted — not absent) |
| `output.variants` | **Under the stub LLM provider (this capture), always exactly one string, `"[stub-llm] variants"`.** Under a real LLM provider this is **up to 3 distinct copy strings** — the worker truncates the model's response to 3 (`app/worker/handlers/marketing_ai.py:59`, `variants[:3]`) and the schema (`app/services/marketing/ai_prompts.py::copy_schema`) requests exactly 3. **Do not hardcode "always 3 strings" in the FE** — render however many `variants` actually contains (1 under stub/staging, up to 3 under a real provider), and don't assume a fixed array length. |
| `error` | `null` while `generating`/`ready`; a string (`"over_budget"` today) when `status == "failed"` — see §6 |

**Render `output.variants` as candidate copy the founder picks from and edits**, not as a finished,
un-editable value — see the Write-with-AI composition in §7.

---

## 3. `GET /api/v1/marketing/copy/generations` — history list

`e2e/_captures/marketing/copy_generations_history.json`:

```json
{
  "data": {
    "generations": [
      {
        "id": "72f64ccc-046a-4da3-8c23-099feac1f0a3",
        "startup_id": "d46495a7-e6c4-4e6a-8e2c-e26e7946b093",
        "kind": "copy",
        "status": "ready",
        "inputs": {
          "cta": null,
          "tone": "bold",
          "channel": "email",
          "asset_type": "ad",
          "key_message": "Launch week is here",
          "audience_segment_id": null
        },
        "output": {
          "variants": [
            "[stub-llm] variants"
          ]
        },
        "error": null,
        "created_at": "2026-09-24T21:06:34.147530Z",
        "updated_at": "2026-09-24T21:06:34.183341Z"
      }
    ]
  },
  "meta": null
}
```

**`copy`-kind only** — this route never returns `plan_week` rows (`list_copy_generations` filters
`kind == MarketingGenerationKind.copy` server-side, `app/services/marketing/ai_content.py:80-86`).
There is no equivalent history-list route for `plan_week` in this slice — a founder can poll a
plan-week id they already have, but cannot list past plan-week generations. Ordered `created_at`
descending, no pagination — same unpaginated-list convention as every other list in this module.
Every row is identical in shape to §2's response, including generations still `generating` or
`failed` — this is a full history, not a "ready only" filter; render each row's own `status`.

---

## 4. `POST /api/v1/marketing/calendar/plan-week` — start a plan-week generation

**No request body** (the e2e journey sends `json={}`, which the route ignores — there is no
`PlanWeekRequest` schema; the worker sources context from the workspace's own `Startup` row —
`stage`, `industry`, `name` — not from the request).

**`e2e/_captures/marketing/plan_week_accepted.json` — 202:**

```json
{
  "data": {
    "id": "1dd578f4-2e1c-4183-a416-0b887dbe4d97",
    "status": "generating"
  },
  "meta": null
}
```

Same thin `{id, status}` shape as §1's 202 — store `id`, poll §5.

---

## 5. `GET /api/v1/marketing/calendar/plan-week/{generation_id}` — poll a plan-week generation

`e2e/_captures/marketing/plan_week_ready.json` — captured after the in-process worker drained the
job (`LLM_PROVIDER=stub`):

```json
{
  "data": {
    "id": "1dd578f4-2e1c-4183-a416-0b887dbe4d97",
    "startup_id": "d46495a7-e6c4-4e6a-8e2c-e26e7946b093",
    "kind": "plan_week",
    "status": "ready",
    "inputs": {},
    "output": {
      "entries": []
    },
    "error": null,
    "created_at": "2026-09-24T21:06:34.206235Z",
    "updated_at": "2026-09-24T21:06:34.215145Z"
  },
  "meta": null
}
```

`inputs` is always `{}` for `plan_week` (no request fields to echo — the request has no body).

### ⚠️ The one thing the FE must know: `output.entries` is `[]` here, and that is expected under the stub provider — not a bug

The **intended, real-provider shape** of each entry, per
`app/services/marketing/ai_prompts.py::plan_week_schema()`, is:

```json
{ "title": "string", "channel": "string (a ChannelKey value)", "body": "string", "day_offset": 0 }
```

— up to **7** entries, `day_offset` is `0`–`6` (`0` = today, matching the founder's local "start of
week" the FE chooses to render from).

**Under the stub LLM provider (this capture, and every staging/e2e run using
`LLM_PROVIDER=stub`), `output.entries` legitimately comes back empty (`[]`).** This is real,
reproducible stub behavior, not a copy/paste mistake in this guide or a transient flake. Root
cause: `handle_marketing_plan_week`
(`app/worker/handlers/marketing_ai.py:90-95`) filters the model's proposed entries down to only
those whose `"channel"` value is a member of the `ChannelKey` enum. The stub LLM
(`StubLLMClient`) has no `enum` constraint to honor on the `channel` field (the JSON-schema
declares it as a plain `"type": "string"`), so it falls through to its generic placeholder value,
literally the string `"[stub-llm] channel"` — which is **not** a valid `ChannelKey` — and every
entry gets dropped by the filter. Under a real LLM provider, `channel` is a real enum string
(e.g. `"email"`) and entries survive the filter, so `output.entries` will actually contain up to 7
populated objects there.

**FE handling:** render an empty-`entries` "ready" plan-week generation as a legitimate (if
unhelpful) empty result under stub/staging — not as an error, and not as still-generating. Don't
assume `entries.length > 0` whenever `status == "ready"`; check the array's actual length before
rendering an "Add all to calendar" action (§7) — there is nothing to add when it's empty.

### Kind-mismatch is a 404, not a 400/422

A `plan_week` generation's `id` used against the **copy** route (`GET
/copy/generations/{plan_week_id}`) returns **404 `NOT_FOUND`** — and symmetrically, a `copy`
generation's id against the plan-week route 404s too. `get_generation`
(`app/services/marketing/ai_content.py:67-77`) filters by `kind` in the same query as
`startup_id`, so a kind mismatch is indistinguishable, response-wise, from "doesn't exist" or
"belongs to another workspace" — same uniform-404 convention every cross-tenant lookup in this API
follows (see Slice 1's Errors section). ✅ Live-equivalent behavior verified by
`tests/api/test_marketing_ai.py::test_kind_mismatch_poll_404` (a `plan_week` id polled via the copy
route). Never assume an id from one flow is valid on the other's route, even though both rows live
in the same table.

---

## 6. The `over_budget` failure — cross-ref `docs/fe-integration-guide-ai-status.md`

Both `copy` and `plan_week` generations can land in `status: "failed"` with `error:
"over_budget"` — the same workspace-wide LLM budget guard every AI feature in this API shares
(`app/platform/llm_budget.py::metered_complete_json`). When the daily token budget is exhausted,
the worker sets `status = "failed"`, `error = "over_budget"`, and leaves `output` as `{}` (the
schema-declared empty default — never partially filled). This is **not** e2e-captured (forcing it
live would require seeding the `llm_usage_daily` ledger directly, which no live journey in this
repo does); it is unit-verified —
`tests/worker/test_marketing_ai_handlers.py::test_copy_over_budget_fails` asserts exactly this
`{status: "failed", error: "over_budget", output: {}}` shape.

**FE handling, same as every other AI-generation surface:** treat `over_budget` as a quiet,
expected degrade, not an error to alarm the founder with — show "AI is temporarily paused, try
again after `resets_at`" copy (fetch `GET /ai/status` for `resets_at`, per
`docs/fe-integration-guide-ai-status.md`), and let the founder retry the same generate call once
the budget resets. There is no automatic retry server-side — a failed generation stays failed
forever; the founder must re-`POST` to try again.

---

## 7. FE compositions — how the calendar (Slice 1) consumes these generations

Neither AI route writes to the calendar directly. Both compositions are **client-side**, built out
of endpoints that already exist:

**Save-to-calendar / "Add all" (from a plan-week result):** for each entry in `output.entries` the
FE wants to keep, call **`POST /api/v1/marketing/calendar-entries`** (Slice 1, see
`docs/fe-integration-guide-marketing-calendar.md` §2) — map `entry.title` → `title`,
`entry.channel` → `channel`, `entry.body` → `body`, and derive `scheduled_at` client-side from
`entry.day_offset` (0–6) against whatever "start of week" the FE is rendering. There is no
bulk/batch calendar-create endpoint — "Add all" means one `POST` per kept entry.

**Write-with-AI (from a calendar entry the founder is editing):** call **`POST
/marketing/copy/generate`** (§1) with that entry's context (`asset_type`, `tone`, `channel`,
`key_message`, optional `cta`/`audience_segment_id`), poll to `ready` (§2), let the founder pick/
edit one of `output.variants`, then **`PATCH /marketing/calendar-entries/{id}`** (Slice 1, see
`docs/fe-integration-guide-marketing-calendar.md` §5) with `{"body": "<chosen variant>"}` to write
it onto the entry. There is no server-side link recorded between the generation row and the
calendar entry it produced — if the FE needs to show "written with AI" provenance on an entry, it
must track that association client-side; the API does not persist it.

---

## Errors

Standard envelope, same shape as Slice 1/2:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "...",
    "field_errors": [ { "field": "audience_segment_id", "message": "Unknown segment for this workspace." } ]
  }
}
```

| Status | Code | When |
|---|---|---|
| 401 | — | Missing or invalid access token |
| 403 | `EMAIL_NOT_VERIFIED` | Signed in, but email not verified |
| 403 | `FORBIDDEN` | Not a founder/team_member of this workspace — enforced before any row lookup, including on the `{id}` GET routes |
| 404 | `NOT_FOUND` | Generation id does not exist, belongs to another workspace, or is the **wrong `kind`** for the route called (§5) |
| 422 | `VALIDATION_ERROR` | Invalid `asset_type`/`tone`/`channel` enum value; blank/oversized `key_message`/`cta`; foreign/unknown `audience_segment_id` (§1) |

A generation that reaches `status: "failed"` (`error: "over_budget"`) is **not** an HTTP error —
`GET …/{id}` still returns 200 with the failed row (§6). Only malformed requests and access/lookup
failures use the `{"error": {...}}` envelope.

---

## Verification table

| Behaviour | Verified live? | Source |
|---|---|---|
| `POST /copy/generate` → 202 `{id, status: "generating"}` | ✅ | `copy_generate_accepted.json` |
| `GET /copy/generations/{id}` → ready, full `GenerationResponse` shape | ✅ | `copy_generation_ready.json` |
| `output.variants` under stub provider is `["[stub-llm] variants"]` (1 item, not 3); real-provider "up to 3" is schema/worker-verified, not live | ✅ live (stub) | `copy_generation_ready.json`, `app/services/marketing/ai_prompts.py::copy_schema`, `app/worker/handlers/marketing_ai.py:59` |
| `GET /copy/generations` history list includes the generation | ✅ | `copy_generations_history.json` |
| `POST /calendar/plan-week` → 202 `{id, status: "generating"}`, no request body | ✅ | `plan_week_accepted.json` |
| `GET /calendar/plan-week/{id}` → ready | ✅ | `plan_week_ready.json` |
| `output.entries` real shape `{title, channel, body, day_offset}`, ≤7 | ⚠️ shape verified from schema, not live (stub filters entries — see §5) | `app/services/marketing/ai_prompts.py::plan_week_schema`, `app/worker/handlers/marketing_ai.py:90-95` |
| `output.entries` is `[]` under the stub provider specifically because the stub's placeholder `channel` value fails the `ChannelKey` filter | ✅ live (the empty array itself), root cause traced statically | `plan_week_ready.json`; `app/worker/handlers/marketing_ai.py:90-95`, `app/platform/llm.py::StubLLMClient._stub_value` |
| `audience_segment_id` 422 (foreign/unknown segment) | ⚠️ unit only | `app/services/marketing/ai_content.py::create_copy_generation` |
| `status: "failed"`, `error: "over_budget"`, `output: {}` on budget exhaustion | ⚠️ unit only — not reachable from a normal live journey without seeding the ledger (see §6) | `tests/worker/test_marketing_ai_handlers.py::test_copy_over_budget_fails` |
| Kind-mismatch (`plan_week` id via copy route, and vice versa) → 404 | ⚠️ unit/integration only, not separately e2e-captured | `tests/api/test_marketing_ai.py::test_kind_mismatch_poll_404` |
| RBAC: 403 for non-marketing roles on all 5 routes, including both `{id}` GET routes | ⚠️ unit/integration only | `tests/api/test_marketing_ai.py::test_rbac_forbidden` |
| Copy history list is `copy`-kind only, never returns `plan_week` rows | ⚠️ unit only | `app/services/marketing/ai_content.py::list_copy_generations` |
| Table name (`marketing_ai_generations`), `kind`/`status` discriminator columns | ✅ live (migration applied in e2e) | `alembic/versions/0035_marketing_ai_generations.py` |
