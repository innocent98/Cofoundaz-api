# FE Integration Guide — Marketing Hub: SEO Tools (Module 10, Slice 4)

> **Provenance.** `e2e/test_marketing.py::test_marketing_seo_journey` was run
> (`bash scripts/e2e_run.sh`, 57/57 e2e passed) and every **success** body in §1–§4 below is pasted
> **verbatim** from the 8 captures it wrote to `e2e/_captures/marketing/`:
> `keyword_created.json`, `keywords_list.json`, `keyword_updated.json`,
> `tracked_page_created.json`, `tracked_page_checklist.json`, `positioning_upserted.json`,
> `content_gap_accepted.json`, `content_gap_ready.json`. Nothing was written from the schema or
> from memory. Captures were taken with `LLM_PROVIDER=stub` (this repo's e2e default — no real LLM
> call). Two things are **not** e2e-captured and are labelled ⚠️ inline: error responses (422/403/
> 404) and the DELETE bodies — those are unit-tested (`tests/api/test_marketing_seo.py`) or read
> from source, and the verification table at the end says which.

> ### ⚠️ Read this first — what is FE-aligned and what is build-ahead
>
> | Section | Status |
> |---|---|
> | §1 **Keyword tracker** | **FE-aligned** to the current UI (`SEOKeyword` type). Safe to build against. |
> | §3 **Brand positioning** | **FE-aligned** to the current UI (positioning statement builder). Safe to build against. |
> | §2 **On-page checklist** | **BUILD-AHEAD — no FE screen consumes this yet.** Shapes are **provisional** and may change when the FE is built. |
> | §4 **AI content-gap** | **BUILD-AHEAD — no FE screen consumes this yet.** Shapes are **provisional** and may change when the FE is built. |
>
> Do not treat §2/§4 as a contract until an FE screen exists and has been reconciled against them.

This extends the earlier Marketing Hub guides — **`docs/fe-integration-guide-marketing-calendar.md`**
(Slice 1), **`-campaigns.md`** (Slice 2), **`-copy.md`** (Slice 3a) and **`-channel-ai.md`**
(Slice 3b). Same base path, same auth, same envelope, same unified `marketing_ai_generations`
table (now with a fifth `kind`, `content_gap`). It cross-references
**`docs/fe-integration-guide-ai-status.md`** for the shared generating/ready/failed +
`over_budget` async pattern.

Base path: `/api/v1/marketing`. Every route requires `Authorization: Bearer <token>` and an
`X-Workspace-Id` header. **Access: founder or team_member only** — the same
`require_role(founder, team_member)` dependency as every `/marketing` route; any other membership
role (e.g. `mentor`, `investor`) gets **403 `FORBIDDEN`** on every route below. ⚠️ Not e2e-captured;
unit-verified by `tests/api/test_marketing_seo.py::test_keyword_rbac_forbidden` and
`::test_content_gap_rbac_forbidden` (parametrized over `mentor`/`investor`).

Every success response is the standard envelope `{"data": …, "meta": null}`.

---

## 1. Keyword tracker — `FE-aligned`

Routes:

```
GET    /marketing/keywords          -> 200 {data: {keywords: [...]}}
POST   /marketing/keywords          -> 200 {data: <keyword>}
PATCH  /marketing/keywords/{id}     -> 200 {data: <keyword>}
DELETE /marketing/keywords/{id}     -> 200 {data: {deleted: true}}
```

Metrics are **entered manually** by the founder (no external SEO-provider sync yet — see
Follow-ups). The backend stores whatever the founder types.

### Field mapping — map snake_case to your camelCase

The API returns **snake_case**. Your `SEOKeyword` type is camelCase — map at the API boundary:

| API field | FE `SEOKeyword` field | Type | Notes |
|---|---|---|---|
| `keyword` | `keyword` | string | Required on create, 1–200 chars |
| `volume` | `volume` | **string** | e.g. `"2.4K"` — a **string**, not a number. Max 20 chars. Matches `SEOKeyword.volume`. Do not `parseInt` it. |
| `difficulty` | `difficulty` | integer 0–100 \| null | Out-of-range → **422** |
| `current_rank` | **`currentRank`** | integer ≥ 0 \| null | snake_case → camelCase |
| `target_page` | **`targetPage`** | string \| null | snake_case → camelCase; max 500 chars |
| `id`, `created_at`, `updated_at` | — | uuid / ISO-8601 UTC | Server-set |

Requests are also **snake_case** (`current_rank`, `target_page`) — map camelCase → snake_case on the
way out too.

### 1a. `POST /marketing/keywords`

Request (the exact body the e2e journey sent):

```json
{
  "keyword": "automated daily savings",
  "volume": "2.4K",
  "difficulty": 45,
  "current_rank": 12,
  "target_page": "/x"
}
```

Response **200** (verbatim, `keyword_created.json`):

```json
{
  "data": {
    "id": "720f7d8f-2045-401f-92f5-ffb8499e9c62",
    "keyword": "automated daily savings",
    "volume": "2.4K",
    "difficulty": 45,
    "current_rank": 12,
    "target_page": "/x",
    "created_at": "2026-09-29T08:52:12.508352Z",
    "updated_at": "2026-09-29T08:52:12.508352Z"
  },
  "meta": null
}
```

Only `keyword` is required; `volume`, `difficulty`, `current_rank`, `target_page` are optional and
come back as `null` when omitted. ⚠️ The all-optional-omitted response is not e2e-captured (the
journey sent every field); `null` per the response schema and `tests/api/test_marketing_seo.py::
test_keyword_patch_explicit_null_422_and_partial_update_ok` (creates with only `keyword`).

### 1b. `GET /marketing/keywords`

Response **200** (verbatim, `keywords_list.json`):

```json
{
  "data": {
    "keywords": [
      {
        "id": "720f7d8f-2045-401f-92f5-ffb8499e9c62",
        "keyword": "automated daily savings",
        "volume": "2.4K",
        "difficulty": 45,
        "current_rank": 12,
        "target_page": "/x",
        "created_at": "2026-09-29T08:52:12.508352Z",
        "updated_at": "2026-09-29T08:52:12.508352Z"
      }
    ]
  },
  "meta": null
}
```

**Field-nesting trap:** the list is at `data.keywords`, not `data` (an array) — same convention as
`data.campaigns`, `data.segments`, etc. Newest first (`created_at` descending, per the service
query; the capture only has one row so ordering is not demonstrated live). No pagination.

### 1c. `PATCH /marketing/keywords/{id}`

Partial update — send only the fields that changed. Request (from the e2e journey):

```json
{ "current_rank": 8 }
```

Response **200** (verbatim, `keyword_updated.json`) — note `current_rank` 12 → 8 and a later
`updated_at`, every other field untouched:

```json
{
  "data": {
    "id": "720f7d8f-2045-401f-92f5-ffb8499e9c62",
    "keyword": "automated daily savings",
    "volume": "2.4K",
    "difficulty": 45,
    "current_rank": 8,
    "target_page": "/x",
    "created_at": "2026-09-29T08:52:12.508352Z",
    "updated_at": "2026-09-29T08:52:12.526319Z"
  },
  "meta": null
}
```

**Explicit `null` on `keyword` is rejected:** `PATCH {"keyword": null}` → **422**. (Only `keyword` is non-nullable; clearing an optional metric with `null` follows from the
schema/service but is ⚠️ untested.) ⚠️ 422 not
e2e-captured; unit-verified by `test_keyword_patch_explicit_null_422_and_partial_update_ok`.

### 1d. `DELETE /marketing/keywords/{id}`

Returns **200** with body `{"data": {"deleted": true}, "meta": null}`. ⚠️ **Not e2e-captured** —
shape read from `app/api/v1/endpoints/marketing.py::delete_keyword` and status-checked by
`test_keyword_crud_roundtrip` (asserts 200 only). A missing/cross-tenant id → **404 `NOT_FOUND`**
(⚠️ not captured; `get_keyword` raises `NotFound`).

### 1e. Validation

`difficulty` outside 0–100 → **422** `VALIDATION_ERROR` (e.g. `difficulty: 150`). ⚠️ Not e2e-captured;
unit-verified by `test_keyword_difficulty_out_of_range_422`. `current_rank` < 0, `keyword` empty or
> 200 chars, `volume` > 20 chars, `target_page` > 500 chars are also rejected by the request schema
(⚠️ schema-derived, no dedicated test per bound). Error envelope shape: see [Errors](#errors).

---

## 2. On-page checklist — `BUILD-AHEAD, provisional`

> **⚠️ BUILD-AHEAD — no FE screen consumes this yet. Shapes are provisional and may change when the
> FE is built.** Everything below was captured live and is accurate *today*, but it is not aligned
> to any existing UI, so do not lock it in as a contract.

A **tracked page** is a URL the founder wants to audit, carrying an on-page SEO checklist.

Routes:

```
GET    /marketing/seo/pages          -> 200 {data: {pages: [...]}}
POST   /marketing/seo/pages          -> 200 {data: <page>}
PATCH  /marketing/seo/pages/{id}     -> 200 {data: <page>}
DELETE /marketing/seo/pages/{id}     -> 200 (⚠️ body not captured)
```

### The 8 fixed checklist items

The checklist is a fixed set of 8 boolean items — the keys are always exactly these, in this order
(they appear in this order in both captures below):

`title_tag`, `meta_description`, `h1`, `keyword_in_intro`, `image_alt`, `internal_links`,
`url_slug`, `mobile_friendly`

### 2a. `POST /marketing/seo/pages`

Request: `{"url": "/pricing"}` (`url` 1–500 chars).

Response **200** (verbatim, `tracked_page_created.json`) — a new page is **seeded all-false**:

```json
{
  "data": {
    "id": "df203323-b495-4bc1-a348-d60c97a97984",
    "url": "/pricing",
    "checklist": {
      "title_tag": false,
      "meta_description": false,
      "h1": false,
      "keyword_in_intro": false,
      "image_alt": false,
      "internal_links": false,
      "url_slug": false,
      "mobile_friendly": false
    },
    "completed": 0,
    "total": 8,
    "created_at": "2026-09-29T08:52:12.538609Z",
    "updated_at": "2026-09-29T08:52:12.538609Z"
  },
  "meta": null
}
```

`completed` (count of `true` items) and `total` (always 8) are **server-computed** — render a
"1/8" progress bar from them; do not recount client-side.

**Duplicate url per workspace → 422.** Creating a second page with the same `url` in the same
workspace is rejected with **422 `VALIDATION_ERROR`** on field `url`. Per source the message is
`"A tracked page with this url already exists."`. ⚠️ Not e2e-captured; status verified by
`test_tracked_page_duplicate_url_422` (which also proves the session stays usable and only the
first page persists). The message string is read from `app/services/marketing/seo.py`, not from a
captured response.

### 2b. `PATCH /marketing/seo/pages/{id}` — merge-toggle

The body is a **partial** map of `{item: bool}` under `checklist`. Only the keys you send change;
every other item keeps its value (merge, not replace). Request (from the e2e journey):

```json
{ "checklist": { "h1": true } }
```

Response **200** (verbatim, `tracked_page_checklist.json`) — `h1` flipped, the other 7 preserved,
`completed` 0 → 1:

```json
{
  "data": {
    "id": "df203323-b495-4bc1-a348-d60c97a97984",
    "url": "/pricing",
    "checklist": {
      "title_tag": false,
      "meta_description": false,
      "h1": true,
      "keyword_in_intro": false,
      "image_alt": false,
      "internal_links": false,
      "url_slug": false,
      "mobile_friendly": false
    },
    "completed": 1,
    "total": 8,
    "created_at": "2026-09-29T08:52:12.538609Z",
    "updated_at": "2026-09-29T08:52:12.548611Z"
  },
  "meta": null
}
```

**Unknown checklist key → 422** (`{"checklist": {"not_a_real_item": true}}`). Per source the
message is `"Unknown checklist item(s): not_a_real_item."`. ⚠️ Not e2e-captured; status verified by
`test_tracked_page_unknown_checklist_key_422`. The PATCH body carries **only** `checklist` — you
cannot rename a page's `url`.

### 2c. `GET /marketing/seo/pages` and `DELETE`

`GET` returns `{"data": {"pages": [ <page>, … ]}, "meta": null}` with each element shaped exactly
like the `POST` response above, newest first. ⚠️ **Not e2e-captured** (shape derived from
`serialize_page` + `tests/api/test_marketing_seo.py::test_tracked_page_duplicate_url_422`, which
reads `data.pages`). `DELETE /marketing/seo/pages/{id}` — ⚠️ not captured, not covered by a
dedicated test; by symmetry with keyword delete it is expected to return `{"deleted": true}`
(source: `app/api/v1/endpoints/marketing.py`). Treat as unverified.

---

## 3. Brand positioning — `FE-aligned`

One positioning record **per workspace** (not a list). Routes:

```
GET /marketing/positioning   -> 200 {data: {audience, need, product, category, differentiator, statement}}
PUT /marketing/positioning   -> 200 {data: {...same shape...}}
```

### Fields

| Field | Type | Notes |
|---|---|---|
| `audience` | string \| null | Max 300 chars |
| `need` | string \| null | Max 300 chars |
| `product` | string \| null | Max 300 chars |
| `category` | string \| null | Max 300 chars |
| `differentiator` | string \| null | Max 300 chars |
| `statement` | string \| null | **Server-composed, read-only.** Never send it. |

The `statement` is composed by the server from the five parts in the template
`For {audience} who {need}, {product} is the {category} that {differentiator}.`

### 3a. `PUT /marketing/positioning`

**PUT is full-replace, not merge.** Every one of the 5 fields is overwritten by what you send; a
field you omit is stored as `null`, not left unchanged. Always send all five (the FE form does).
Request (from the e2e journey):

```json
{
  "audience": "freelancers in Lagos",
  "need": "save money without thinking about it",
  "product": "Cofoundaz Save",
  "category": "savings app",
  "differentiator": "rounds up every payment automatically"
}
```

Response **200** (verbatim, `positioning_upserted.json`):

```json
{
  "data": {
    "audience": "freelancers in Lagos",
    "need": "save money without thinking about it",
    "product": "Cofoundaz Save",
    "category": "savings app",
    "differentiator": "rounds up every payment automatically",
    "statement": "For freelancers in Lagos who save money without thinking about it, Cofoundaz Save is the savings app that rounds up every payment automatically."
  },
  "meta": null
}
```

**Field-nesting trap:** unlike keywords and pages, positioning has **no `id`, no timestamps, and no
wrapper array** — the five fields plus `statement` sit directly under `data`.

### 3b. `GET /marketing/positioning` before any PUT — never 404

Before the founder has ever saved, `GET` returns **200 with every field `null`, including
`statement: null`** — it is never a 404, so the FE does not need a "not found → empty form" branch.

⚠️ **Not e2e-captured** (the journey does PUT first). Shape verified by
`tests/api/test_marketing_seo.py::test_positioning_get_before_put_is_empty` (asserts 200,
`statement is None`, `audience is None`) and from `serialize_positioning` in
`app/services/marketing/seo.py`, which returns all six keys as `null`:

```json
{ "data": { "audience": null, "need": null, "product": null, "category": null, "differentiator": null, "statement": null }, "meta": null }
```

(The body above is assembled from the source, not a captured response.)

### 3c. All-blank PUT keeps `statement: null`

`PUT {}` (or all five fields blank/whitespace) stores the parts but leaves **`statement: null`** —
the server never produces a dangling `"For  who ,  is the  that ."` skeleton, so the empty state is
identical to GET-before-PUT. Render `statement` only when it is non-null. ⚠️ Not e2e-captured;
verified by `test_positioning_empty_put_yields_null_statement`.

### 3d. Partially-filled PUT

If only *some* parts are non-blank, the server still composes the template with the blank parts
empty (e.g. `"For gig workers who , Kolo is the  that ."`) — `statement` is `null` **only** when all
five are blank. ⚠️ **Not verified by any test or capture** — read from `compose_statement` in
`app/services/marketing/seo.py`. The FE should validate that all five parts are filled before
showing the composed statement as final.

---

## 4. AI content-gap suggestions — `BUILD-AHEAD, provisional`

> **⚠️ BUILD-AHEAD — no FE screen consumes this yet. Shapes are provisional and may change when the
> FE is built.**

Same async `POST -> 202 -> poll` pattern as every Marketing AI generation (copy, plan-week,
channel-plan, fit-notes). It asks the LLM for up to 7 content ideas the startup has **not** covered
yet, grounded in the founder's **10 most recent tracked keywords** (§1) plus stage/industry read
server-side.

Routes:

```
POST /marketing/seo/content-gaps/generate       -> 202 {data: {id, status: "generating"}}   (no request body)
GET  /marketing/seo/content-gaps/{id}           -> 200 {data: <generation>}                 (poll)
GET  /marketing/seo/content-gaps                -> 200 {data: {generations: [...]}}         (history)
```

### 4a. `POST /marketing/seo/content-gaps/generate`

No request body. Response **202** (verbatim, `content_gap_accepted.json`):

```json
{
  "data": {
    "id": "2cc6558c-401d-406b-8aaa-cb37096d23be",
    "status": "generating"
  },
  "meta": null
}
```

### 4b. `GET /marketing/seo/content-gaps/{id}` — poll

Response **200** when done (verbatim, `content_gap_ready.json`):

```json
{
  "data": {
    "id": "2cc6558c-401d-406b-8aaa-cb37096d23be",
    "startup_id": "b59654ba-d369-4508-9efe-efbcd937c8bd",
    "kind": "content_gap",
    "status": "ready",
    "inputs": {},
    "output": {
      "gaps": [
        {
          "angle": "[stub-llm] angle",
          "title": "[stub-llm] title",
          "target_keyword": "[stub-llm] target_keyword"
        }
      ]
    },
    "error": null,
    "created_at": "2026-09-29T08:52:12.571265Z",
    "updated_at": "2026-09-29T08:52:12.591703Z"
  },
  "meta": null
}
```

> **⚠️ Stub-provider values.** The `"[stub-llm] …"` strings and the single-element `gaps` array are
> **what the stub LLM emits** (this repo's e2e/staging default), not real content. A real provider
> returns up to **7** entries of real prose. The **shape** — `output.gaps[]` with exactly
> `title`, `target_keyword`, `angle` — is what to code against. Never hard-code or display
> `[stub-llm]` text as if it were a real suggestion.

Field notes:

| Field | Notes |
|---|---|
| `status` | `generating` \| `ready` \| `failed` |
| `output.gaps[]` | `{title, target_keyword, angle}` — all strings. Max 7 entries. |
| `output` while `generating` | Empty object `{}` ⚠️ (not captured for this kind; same lifecycle as the other generations — see `docs/fe-integration-guide-ai-status.md`) |
| `inputs` | Always `{}` for this kind (no request body) |
| `error` | `null` unless `status: "failed"` |
| `startup_id` | Present on the poll body (the generation row's full shape); **absent** from the 202 body |

**Polling cadence:** a few seconds between polls, backing off, stopping on `ready`/`failed`, capped at
a reasonable timeout (same guidance as Slices 3a/3b; not tuned for this kind).

### 4c. `over_budget` failure

If the workspace's LLM budget is exhausted the poll returns **200** (not an HTTP error) with
`status: "failed"`, `error: "over_budget"`, and `output: {}`. Treat it as a quiet, expected degrade
("AI is temporarily paused"), not an alarm — see `docs/fe-integration-guide-ai-status.md` for the
`resets_at` handling and the shared budget semantics. ⚠️ **Not e2e-captured** (forcing it live would
require seeding the `llm_usage_daily` ledger directly); the shared worker path
(`_fail_over_budget`) is unit-verified for the other kinds in
`tests/worker/test_marketing_ai_handlers.py`, and `handle_marketing_content_gap` calls the same
helper. There is no automatic retry server-side.

### 4d. `GET /marketing/seo/content-gaps` — history

Returns `{"data": {"generations": [ <generation>, … ]}, "meta": null}`, each element shaped exactly
like the poll body above, newest first. Unlike plan-week/channel-plan/fit-notes, content-gap **has**
a history list. ⚠️ Not e2e-captured; verified by
`test_content_gap_generate_202_and_pollable` (asserts the new id appears in `data.generations`).

### 4e. "Write it" — a pure FE composition (no endpoint)

A "Write it" button on a gap is **not** a backend feature: the FE composes it from an existing
endpoint. Take a gap and call `POST /marketing/copy/generate` (Slice 3a; see
`docs/fe-integration-guide-marketing-copy.md`), passing the gap's `title` / `angle` /
`target_keyword` as the copy request's `key_message` (choose `asset_type` and `tone` in the FE).
Then poll the copy generation as usual. The backend keeps **no link** between a content-gap idea
and the copy it inspires. ⚠️ This composition is not e2e-exercised; both halves are individually
verified.

### 4f. Kind-mismatch is a 404

A `copy` / `plan_week` / `channel_plan` / `channel_fit` generation id polled on
`GET /seo/content-gaps/{id}` returns **404 `NOT_FOUND`** — and a content-gap id polled on the other
kinds' routes 404s too. Indistinguishable from "does not exist" or "belongs to another workspace".
⚠️ Not e2e-captured; verified by `test_content_gap_kind_mismatch_404`.

---

## Errors

Standard envelope, same shape as Slices 1–3b (shape copied from
`docs/fe-integration-guide-marketing-channel-ai.md`, which captured it live earlier; **not**
re-captured for the SEO routes):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "...",
    "field_errors": [ { "field": "objective", "message": "..." } ]
  }
}
```

| Status | Code | When (SEO routes) |
|---|---|---|
| 401 | — | Missing or invalid access token |
| 403 | `EMAIL_NOT_VERIFIED` | Signed in, but email not verified |
| 403 | `FORBIDDEN` | Not a founder/team_member of this workspace (enforced before any row lookup) |
| 404 | `NOT_FOUND` | Keyword/page/generation id missing, cross-tenant, or (content-gap) the wrong `kind` |
| 422 | `VALIDATION_ERROR` | `difficulty` out of 0–100; `keyword` null/empty; duplicate tracked-page `url`; unknown checklist key; over-length string fields |

A content-gap generation that reaches `status: "failed"` (`error: "over_budget"`) is **not** an
HTTP error — `GET …/{id}` still returns 200 (§4c).

---

## UX consequences the FE must surface

- **`volume` is a string.** Display it as-is (`"2.4K"`); never format it as a number or sort it
  numerically without parsing your own convention. Metrics are founder-entered, so they can be stale
  or blank — render `null` as an empty/`—` cell, not `0`.
- **Positioning `statement` is read-only and can be `null`.** Show it live from the last `PUT`
  response; hide the "statement" card when it is `null`. Don't try to compose it client-side — the
  server's template is the source of truth.
- **PUT positioning overwrites everything.** Send all five fields every time or you will silently
  blank the ones you omitted.
- **Checklist progress comes from `completed`/`total`.** Don't recount; and because PATCH merges,
  send only the toggled item.
- **Content-gap output is AI-generated and asynchronous.** Show a pending state while `generating`,
  and never present `[stub-llm]` text to a real user.

---

## Verification table

| Behaviour | Verified? | Source |
|---|---|---|
| `POST /keywords` → 200, snake_case body, `volume` is a string (`"2.4K"`) | ✅ live | `keyword_created.json` |
| `GET /keywords` → 200, list at `data.keywords` | ✅ live | `keywords_list.json` |
| `PATCH /keywords/{id}` partial update (`current_rank` 12→8), other fields preserved | ✅ live | `keyword_updated.json` |
| `difficulty` outside 0–100 → 422 | ⚠️ unit only | `tests/api/test_marketing_seo.py::test_keyword_difficulty_out_of_range_422` |
| `PATCH {"keyword": null}` → 422; partial PATCH OK | ⚠️ unit only | `::test_keyword_patch_explicit_null_422_and_partial_update_ok` |
| `DELETE /keywords/{id}` → 200 `{"deleted": true}` | ⚠️ status unit-tested; **body read from source, not captured** | `::test_keyword_crud_roundtrip`; `app/api/v1/endpoints/marketing.py` |
| Keyword list ordering newest-first | ⚠️ source only (capture has 1 row) | `app/services/marketing/seo.py::list_keywords` |
| Keyword RBAC 403 for mentor/investor | ⚠️ unit only | `::test_keyword_rbac_forbidden` |
| `POST /seo/pages` seeds all 8 items false, `completed: 0`, `total: 8` | ✅ live (**provisional — build-ahead**) | `tracked_page_created.json` |
| `PATCH /seo/pages/{id}` merges a partial `{item: bool}`, `completed` recomputed | ✅ live (**provisional — build-ahead**) | `tracked_page_checklist.json` |
| Duplicate page `url` per workspace → 422 (session stays usable) | ⚠️ unit only (status); message string from source | `::test_tracked_page_duplicate_url_422`; `seo.py` |
| Unknown checklist key → 422 | ⚠️ unit only (status); message string from source | `::test_tracked_page_unknown_checklist_key_422`; `seo.py` |
| `GET /seo/pages` → `data.pages`; `DELETE /seo/pages/{id}` body | ⚠️ not captured; DELETE body/status unverified | `::test_tracked_page_duplicate_url_422` (reads `data.pages`); source |
| `PUT /positioning` → 200, 5 fields + composed `statement` | ✅ live | `positioning_upserted.json` |
| `GET /positioning` before any PUT → 200 all-null incl. `statement: null` (never 404) | ⚠️ unit only (body assembled from source) | `::test_positioning_get_before_put_is_empty`; `seo.py::serialize_positioning` |
| All-blank PUT → `statement: null` | ⚠️ unit only | `::test_positioning_empty_put_yields_null_statement` |
| PUT is full-replace; single row per workspace | ⚠️ unit only | `::test_positioning_upsert_composes_statement_and_is_single_row` |
| Partially-filled PUT composes the template with blanks | ⚠️ **source only, untested** | `seo.py::compose_statement` |
| `POST /seo/content-gaps/generate` → 202 `{id, status: "generating"}` (**provisional — build-ahead**) | ✅ live | `content_gap_accepted.json` |
| `GET /seo/content-gaps/{id}` → ready, `output.gaps[{title,target_keyword,angle}]` (**provisional — build-ahead**) | ✅ live (stub-LLM values) | `content_gap_ready.json` |
| Real-provider output (up to 7 gaps of real prose) | ⚠️ not live — stub only; shape from `content_gap_schema()` | `app/services/marketing/ai_prompts.py` |
| `GET /seo/content-gaps` history → `data.generations` | ⚠️ unit only | `::test_content_gap_generate_202_and_pollable` |
| Content-gap kind-mismatch → 404 | ⚠️ unit only | `::test_content_gap_kind_mismatch_404` |
| Content-gap RBAC 403 for mentor/investor (incl. poll route) | ⚠️ unit only | `::test_content_gap_rbac_forbidden` |
| `over_budget` failure (`status: "failed"`, `error: "over_budget"`) | ⚠️ not live for this kind — shared helper unit-tested for other kinds | `tests/worker/test_marketing_ai_handlers.py` |
| "Write it" via `POST /marketing/copy/generate` | ⚠️ FE composition, not exercised end-to-end | `docs/fe-integration-guide-marketing-copy.md` |
| Migration `0037_seo_tools` applies; single alembic head | ✅ live (applied in the e2e run) | `alembic/versions/0037_seo_tools.py` |
