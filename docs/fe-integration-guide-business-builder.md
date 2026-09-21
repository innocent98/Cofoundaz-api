# FE Integration Guide — Business Builder (Module 08)

Covers both shipped slices:
- **Slice 1 — Canvas Core** (§1–§4): the 5 structured strategy canvases.
- **Slice 2 — Typed Artifacts** (§6–§11): the 4 generic `{kind}` record collections (personas,
  revenue streams, competitors, pricing). Record rows in the overview grid are covered in §1
  alongside the canvas rows, since both come back from the one `GET /overview` call.

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_business_builder.py` (`test_business_builder_journey` for Slice 1,
`test_business_builder_records_journey` for Slice 2) running against a real server (`make e2e`)
— see `e2e/_captures/business/*.json`. Nothing here is retyped from memory or invented. IDs are
real values from that ephemeral test run (they differ on every real request, but the shapes are
exact). Error-body shapes (§5, §11) were **not** exercised by the live journeys (both are
happy-path walks) — they're derived from source and unit tests, and are called out inline and in
the verification table.

Base path: `/api/v1/business-builder`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header identifying the active workspace
(`GET /auth/me` → `data.active_workspace_id`), same as every other tenant-scoped endpoint in this
API. **Reads (`GET /overview`, `GET /canvases/{type}`, `GET /{kind}`) are open to any active
member** — founder, team_member, and mentor. **Writes (`PUT /canvases/{type}`, `POST
/canvases/{type}/ai-fill`, `POST /{kind}`, `PUT /{kind}/{record_id}`, `DELETE
/{kind}/{record_id}`, `POST /{kind}/ai-fill`) require founder or team_member — a mentor gets 403
`FORBIDDEN`.**

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see §5, §11).

**Canvas types** (path segment `{type}`, exactly these 5 values — an unrecognized value 404s):
`business_model`, `lean`, `value_prop`, `mission_vision`, `swot`.

**Record kinds** (path segment `{kind}`, exactly these 4 URL values — note the plural/hyphenated
URL segment differs from the singular/underscored `kind` value in response bodies; an
unrecognized `{kind}` segment 404s):

| URL segment `{kind}` | Response `kind` value |
|---|---|
| `personas` | `persona` |
| `revenue-streams` | `revenue_stream` |
| `competitors` | `competitor` |
| `pricing` | `pricing` |

**⚠️ `/overview` now returns 9 rows, not 5.** Since Slice 2, `GET /overview`'s response includes
the 5 canvas-type rows **plus 4 new record-kind rows** — see §1's "Record-kind rows" subsection.

---

## ⚠️ Read this before wiring up saving — `PUT` is full-replace, NOT a partial merge

**This applies to BOTH slices.** `PUT /canvases/{type}` (Slice 1, below) and `PUT
/{kind}/{record_id}` (Slice 2, §8) are both full-replace, not a merge — same contract, same trap,
same fix. Read this section once; it covers both.

**Every `PUT /canvases/{type}` call must send the COMPLETE `blocks` object — every block key for
that canvas type, including the ones you didn't just edit.** The server does not merge your
request into the existing canvas. It rebuilds from that canvas type's empty scaffold and applies
your `blocks` on top (`merged = empty_blocks(type); merged.update(request.blocks)`) — **any
block key you omit is reset to empty** (`[]` for a list block, `""` for a text block), even if
that block had real founder-entered content a moment ago.

**Concretely:** if `business_model` currently has `key_partners: ["Stripe"]` and
`value_propositions: ["Fast onboarding"]`, and your save only sends
`{"blocks": {"key_partners": ["Stripe", "AWS"]}, "version": 2}`, the response comes back with
`value_propositions: []` — silently wiped, not left alone. This is pinned by a dedicated backend
test (`tests/services/business/test_service.py`) — it is deliberate, documented behavior, not a
bug you can report.

**What this means for your save flow:** always keep the full, current `blocks` object in your
local canvas state (from the last `GET` or the last successful `PUT` response), apply only the
edited field(s) to your **local copy**, and send that entire local `blocks` object on every save
— never a diff, never "just the field the user touched."

---

## 1. `GET /api/v1/business-builder/overview` — the completion grid

One call returns **all 5 canvas types plus all 4 record kinds** (9 rows total, since Slice 2) with
their completion state — this is what a "Business Builder" home screen/menu is built on.
Read-only: calling this creates no canvas or record rows, even for a type/kind the founder has
never opened/added.

`e2e/_captures/business/overview_empty.json` — captured for a freshly-onboarded founder, before
any canvas or record was touched (status `200`):

```json
{
  "data": [
    {
      "type": "business_model",
      "label": "Business Model",
      "filled_blocks": 0,
      "total_blocks": 9,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "lean",
      "label": "Lean",
      "filled_blocks": 0,
      "total_blocks": 9,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "value_prop",
      "label": "Value Prop",
      "filled_blocks": 0,
      "total_blocks": 6,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "mission_vision",
      "label": "Mission Vision",
      "filled_blocks": 0,
      "total_blocks": 2,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "swot",
      "label": "Swot",
      "filled_blocks": 0,
      "total_blocks": 4,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "persona",
      "label": "Persona",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    },
    {
      "type": "revenue_stream",
      "label": "Revenue Stream",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    },
    {
      "type": "competitor",
      "label": "Competitor",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    },
    {
      "type": "pricing",
      "label": "Pricing",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    }
  ],
  "meta": null
}
```

`e2e/_captures/business/overview_after.json` — captured after `business_model` had one block
saved (status `200`), showing the row flip. **Note the record-kind rows are untouched here** —
this capture is from the canvas journey, which never creates any records:

```json
{
  "data": [
    {
      "type": "business_model",
      "label": "Business Model",
      "filled_blocks": 1,
      "total_blocks": 9,
      "completion_pct": 11,
      "status": "continue"
    },
    {
      "type": "lean",
      "label": "Lean",
      "filled_blocks": 0,
      "total_blocks": 9,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "value_prop",
      "label": "Value Prop",
      "filled_blocks": 0,
      "total_blocks": 6,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "mission_vision",
      "label": "Mission Vision",
      "filled_blocks": 0,
      "total_blocks": 2,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "swot",
      "label": "Swot",
      "filled_blocks": 0,
      "total_blocks": 4,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "persona",
      "label": "Persona",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    },
    {
      "type": "revenue_stream",
      "label": "Revenue Stream",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    },
    {
      "type": "competitor",
      "label": "Competitor",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    },
    {
      "type": "pricing",
      "label": "Pricing",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    }
  ],
  "meta": null
}
```

`e2e/_captures/business/overview_records.json` — captured **from the records journey**, after one
persona was created and updated (no competitor/pricing record yet at this point) — this is the
capture that proves the record-kind row flip (status `200`):

```json
{
  "data": [
    {
      "type": "business_model",
      "label": "Business Model",
      "filled_blocks": 0,
      "total_blocks": 9,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "lean",
      "label": "Lean",
      "filled_blocks": 0,
      "total_blocks": 9,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "value_prop",
      "label": "Value Prop",
      "filled_blocks": 0,
      "total_blocks": 6,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "mission_vision",
      "label": "Mission Vision",
      "filled_blocks": 0,
      "total_blocks": 2,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "swot",
      "label": "Swot",
      "filled_blocks": 0,
      "total_blocks": 4,
      "completion_pct": 0,
      "status": "start"
    },
    {
      "type": "persona",
      "label": "Persona",
      "status": "complete",
      "completion_pct": 100,
      "count": 1
    },
    {
      "type": "revenue_stream",
      "label": "Revenue Stream",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    },
    {
      "type": "competitor",
      "label": "Competitor",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    },
    {
      "type": "pricing",
      "label": "Pricing",
      "status": "start",
      "completion_pct": 0,
      "count": 0
    }
  ],
  "meta": null
}
```

### Row field reference — canvas rows (Slice 1)

| Field | Notes |
|---|---|
| `type` | one of the 5 canvas type strings — use as the key when routing to `GET /canvases/{type}`. |
| `label` | a display label, **title-cased from `type`** (`"swot"` → `"Swot"`, not `"SWOT"` — see the label-casing note below). Fine to render as-is, or override client-side per type if you want proper-noun casing (e.g. "SWOT"). |
| `filled_blocks` / `total_blocks` | integers; `total_blocks` differs by canvas type (9 for `business_model`/`lean`, 6 for `value_prop`, 4 for `swot`, 2 for `mission_vision`) — don't hardcode a denominator. |
| `completion_pct` | `round(filled / total * 100)`, integer, `0`–`100`. |
| `status` | `"start"` (0 filled), `"continue"` (1..total-1 filled), `"complete"` (all filled). These 3 values only — no `"empty"`/`"pending"` variants like other modules use. |

**⚠️ Label-casing note:** `label` is generated by `type.replace("_", " ").title()` server-side —
this produces `"Swot"`, not the proper-noun `"SWOT"`, and `"Value Prop"`, not `"Value
Proposition"`. If your design wants proper display names (e.g. "SWOT Analysis", "Value
Proposition Canvas"), maintain that mapping client-side rather than rendering `label` directly —
it wasn't designed as polished UI copy, just a readable fallback.

### Row field reference — record-kind rows (Slice 2)

**⚠️ Record-kind rows use a different field set than canvas rows on the same list** — there is no
`filled_blocks`/`total_blocks` for a record kind (see the note below on why), and there is a
`count` field canvas rows don't have.

| Field | Notes |
|---|---|
| `type` | one of the 4 record-kind strings (`persona`/`revenue_stream`/`competitor`/`pricing`) — map to the URL segment via the table in the intro (e.g. `persona` → `GET /personas`). |
| `label` | same title-cased-from-`type` generation as canvas rows (`"revenue_stream"` → `"Revenue Stream"`). |
| `status` | **only `"start"` or `"complete"` — no `"continue"`.** A record kind is binary: the founder has added at least one record (`"complete"`) or none (`"start"`). Unlike a canvas, there's no natural "how many personas is enough" denominator to compute a partial state from. |
| `completion_pct` | `0` or `100` only (matches `status`) — not a real percentage, just a boolean rendered the same way canvas rows render their percentage, for a uniform progress-bar UI across all 9 rows if you want one. |
| `count` | the actual number of records of this kind — **this is the field to render** ("3 personas"), not `completion_pct`. Canvas rows do not have this field. |

**Why no `filled_blocks`/`total_blocks` on a record-kind row:** those two fields describe a
canvas's block-scaffold shape, which doesn't apply to a record collection (there's no fixed
"total number of personas a founder should have"). If your overview UI iterates all 9 rows
generically, branch on whether `count` is present (record-kind row) vs `filled_blocks`/
`total_blocks` (canvas row) rather than assuming every row has the same shape.

---

## 2. `GET /api/v1/business-builder/canvases/{type}` — get (and lazy-create) one canvas

Returns one canvas's full state: its blocks, the block definitions to render a form from, and
its completion summary. **Side effect:** if this startup has never touched this canvas type
before, the row is lazily created here, at `version: 1`, with every block empty. Unknown `{type}`
→ `404 NOT_FOUND`.

`e2e/_captures/business/canvas_get.json` — captured on the very first `GET` for
`business_model` (lazy-create), before any save (status `200`):

```json
{
  "data": {
    "type": "business_model",
    "version": 1,
    "blocks": {
      "channels": [],
      "key_partners": [],
      "key_resources": [],
      "cost_structure": [],
      "key_activities": [],
      "revenue_streams": [],
      "customer_segments": [],
      "value_propositions": [],
      "customer_relationships": []
    },
    "block_defs": [
      { "key": "key_partners", "label": "Key Partners", "kind": "list" },
      { "key": "key_activities", "label": "Key Activities", "kind": "list" },
      { "key": "key_resources", "label": "Key Resources", "kind": "list" },
      { "key": "value_propositions", "label": "Value Propositions", "kind": "list" },
      { "key": "customer_relationships", "label": "Customer Relationships", "kind": "list" },
      { "key": "channels", "label": "Channels", "kind": "list" },
      { "key": "customer_segments", "label": "Customer Segments", "kind": "list" },
      { "key": "cost_structure", "label": "Cost Structure", "kind": "list" },
      { "key": "revenue_streams", "label": "Revenue Streams", "kind": "list" }
    ],
    "completion": {
      "filled_blocks": 0,
      "total_blocks": 9,
      "completion_pct": 0,
      "status": "start"
    }
  },
  "meta": null
}
```

### The `block_defs` contract — render from this, never hardcode a canvas's shape

**`block_defs` is served on every `GET` and every `PUT` response specifically so the FE never
needs to know in advance which blocks exist for a given canvas type, in what order, or what kind
each one is.** Build your canvas form by iterating `block_defs`, not by shipping a
`business_model` component that assumes 9 specific named fields. This is what lets the backend
add, rename, relabel, or reorder blocks for any canvas type without an FE deploy.

| `block_defs[i]` field | Meaning |
|---|---|
| `key` | the field name to read/write in `blocks` (e.g. `"key_partners"`). |
| `label` | display label for the form field (e.g. `"Key Partners"`) — this one IS meant as polished UI copy, unlike `overview`'s canvas-level `label`. |
| `kind` | `"text"` (render a single text input/textarea, value is a plain string) or `"list"` (render a repeatable list-of-strings input, value is `string[]`). Only these two kinds exist in Slice 1. |

**⚠️ Key-order trap:** `block_defs` is served in a stable, meaningful order (matches the
canonical framework layout — e.g. Business Model Canvas's 9 blocks in their standard
presentation order). **The `blocks` object's own key order does NOT match `block_defs`'s order**
(observed live in the capture above — `blocks` starts with `channels`, `block_defs` starts with
`key_partners`) because `blocks` round-trips through Postgres JSONB, which does not preserve
key-insertion order. **Always use `block_defs`'s order to decide layout; use `blocks` purely as a
`{key: value}` lookup, never for ordering.**

---

## 3. `PUT /api/v1/business-builder/canvases/{type}` — save a canvas

Saves the complete canvas state under optimistic-concurrency versioning. **Read the full-replace
warning at the top of this doc before wiring this up.**

Request body:

```json
{
  "blocks": { "...every block key for this canvas type...": "..." },
  "version": 1
}
```

- `version` — the `version` you last read from `GET`/a previous `PUT` response for this exact
  canvas. Send it back unchanged; the server increments it for you.
- `blocks` — the **complete** blocks object (see the warning above). Any block kind mismatch
  (e.g. a string where a list is expected) → `422 VALIDATION_ERROR`. Any key not in that canvas
  type's `block_defs` → `422 VALIDATION_ERROR`.

`e2e/_captures/business/canvas_put.json` — the request above was
`{"blocks": {"key_partners": ["Stripe", "AWS"]}, "version": 1}` (all other keys omitted
deliberately in this capture to exercise/prove the full-replace behavior — they come back
empty). Response (status `200`):

```json
{
  "data": {
    "type": "business_model",
    "version": 2,
    "blocks": {
      "channels": [],
      "key_partners": ["Stripe", "AWS"],
      "key_resources": [],
      "cost_structure": [],
      "key_activities": [],
      "revenue_streams": [],
      "customer_segments": [],
      "value_propositions": [],
      "customer_relationships": []
    },
    "block_defs": [
      { "key": "key_partners", "label": "Key Partners", "kind": "list" },
      { "key": "key_activities", "label": "Key Activities", "kind": "list" },
      { "key": "key_resources", "label": "Key Resources", "kind": "list" },
      { "key": "value_propositions", "label": "Value Propositions", "kind": "list" },
      { "key": "customer_relationships", "label": "Customer Relationships", "kind": "list" },
      { "key": "channels", "label": "Channels", "kind": "list" },
      { "key": "customer_segments", "label": "Customer Segments", "kind": "list" },
      { "key": "cost_structure", "label": "Cost Structure", "kind": "list" },
      { "key": "revenue_streams", "label": "Revenue Streams", "kind": "list" }
    ],
    "completion": {
      "filled_blocks": 1,
      "total_blocks": 9,
      "completion_pct": 11,
      "status": "continue"
    }
  },
  "meta": null
}
```

Notice `version` is now `2` and `key_partners` holds the saved value — **and every other block
key is back to `[]`**, exactly as the full-replace warning describes; this capture is the live
proof of that behavior, not a hypothetical.

### ⚠️ The optimistic-concurrency trap — handling `409 CANVAS_VERSION_CONFLICT`

**Always send the `version` you actually fetched, and always update your locally-held `version`
from the response of every successful save.** If two tabs/devices (or the same tab after a stale
reload) send a `PUT` with a `version` that no longer matches the row's current version, the
server responds `409` with this shape (derived from `CanvasVersionConflict` /
`tests/api/test_business_canvases.py::test_put_stale_version_409` — **not captured live**, the
happy-path journey never triggers a real conflict):

```json
{
  "error": {
    "code": "CANVAS_VERSION_CONFLICT",
    "message": "This canvas was changed elsewhere. Reload and reapply your edits.",
    "field_errors": []
  }
}
```

**On this response: do NOT retry the same request.** Re-`GET` the canvas (which returns the
current `version` and `blocks`), reapply the user's in-progress edits on top of the fresh
`blocks`, and let the user re-save with the new `version`. There is no server-side merge — a
naive retry with the same stale `version` will 409 again forever.

**What the version check does and does not catch.** This is app-level optimistic concurrency —
`save_canvas` compares `expected_version` against the row's version at the moment it runs. It
reliably catches a stale save coming from a re-read (edit tab A, edit tab B off an older `GET`,
save A, save B → B gets 409 as shown above). It does **not** catch two saves that are truly
simultaneous and both read the same `version` at the same instant — those resolve last-writer-wins
at the database level, with no 409 to either caller. This is expected given the "occasional-editor,
not high-contention" tradeoff described in the SOP; a DB-level compare-and-swap (`UPDATE ... WHERE
version = :expected`) is a possible future hardening if simultaneous-edit collisions turn out to
matter in practice.

---

## 4. `POST /api/v1/business-builder/canvases/{type}/ai-fill` — AI-fill (deferred)

Enqueues an AI-fill job for this canvas. **Writes no canvas data itself** — it only creates a job
row; the canvas is untouched until (in a future module) a worker consumes the job and calls the
save path itself.

`e2e/_captures/business/ai_fill.json` (status `202`):

```json
{
  "data": {
    "job_id": "2e802d13-9d86-44c8-bb82-79b3f446ec4b",
    "status": "queued"
  },
  "meta": null
}
```

### The ai-fill deferral — poll `GET /jobs/{id}`, and expect it to stay `queued`

Poll `GET /api/v1/jobs/{job_id}` (Bearer token only — **no `X-Workspace-Id` header needed**, the
job endpoint scopes by the job's own stored `startup_id`) to track status.
`e2e/_captures/business/ai_fill_job.json` (status `200`, fetched immediately after the `ai-fill`
call above):

```json
{
  "data": {
    "id": "2e802d13-9d86-44c8-bb82-79b3f446ec4b",
    "type": "business.canvas.ai_fill",
    "status": "queued",
    "result": null,
    "error": null
  },
  "meta": null
}
```

**⚠️ This job will stay `"queued"` indefinitely as of Slice 1 — that is expected, known-deferred
behavior, not a bug.** No worker exists yet to drain `business.canvas.ai_fill` jobs (the same
"enqueue now, consume later" seam used by `healthscore.initialize` and `roadmap.replan`
elsewhere in this API — see those modules' own FE guides/SOPs). **Do not build a UI flow that
waits for this job to reach `"succeeded"`** — there is currently no code path that ever
transitions it out of `"queued"`. If you want to ship the ai-fill button now, disable it after
showing "AI fill requested" (or similar), and treat a real completed/failed state as a
forward-looking contract to revisit once Module 03 (AI Co-Founder) ships the worker. `status`
values in general: `"queued"` (only one ever observed/reachable today), plus `"succeeded"` /
`"failed"` as forward-looking values the shared `Job` model supports but nothing currently
produces for this job type.

---

## 5. Errors (Slice 1 — canvases)

Every error is `{"error": {"code": …, "message": …, "field_errors": [...]}}` (no `data`/`meta`)
— the same shared shape used throughout this API. **None of the error paths below were exercised
by the live E2E journey** (it's a single happy-path walk) — every shape here is derived from
source (`app/core/errors.py`) and confirmed by the named unit test. Slice 2 (records) errors are
in §11.

| Code | HTTP | When | Source |
|---|---|---|---|
| `CANVAS_VERSION_CONFLICT` | 409 | `PUT` sent a stale `version` | `CanvasVersionConflict`, `tests/api/test_business_canvases.py::test_put_stale_version_409` — see §3 |
| `VALIDATION_ERROR` | 422 | `PUT` sent an unknown block key, or a value of the wrong kind (string for a `"list"` block, non-string for a `"text"` block) | `validate_blocks`, `tests/api/test_business_canvases.py::test_put_bad_block_422` |
| `NOT_FOUND` | 404 | `{type}` in the URL isn't one of the 5 valid canvas types | `_parse_type`, `tests/api/test_business_canvases.py::test_get_unknown_type_404` |
| `FORBIDDEN` | 403 | a mentor calls `PUT` or `POST .../ai-fill` (reads are open to mentors; writes are not) | `require_role`, `tests/api/test_business_canvases.py::test_put_mentor_forbidden_403` / `test_ai_fill_mentor_forbidden_403` |

A missing/invalid `X-Workspace-Id` or an unauthenticated request behaves exactly as documented
in `docs/fe-integration-guide-mission.md` §7 — all four routes share the same
`require_workspace`/`require_role`/`get_verified_user` dependency chain as every other
tenant-scoped route in this API.

---

# Slice 2 — Typed Artifacts (records)

The generic `{kind}` CRUD surface: `personas`, `revenue-streams`, `competitors`, `pricing` (see
the URL-segment table in the intro for the exact `{kind}` string vs. the response `kind` value).
**Read the full-replace warning near the top of this document before wiring up §8** — `PUT
/{kind}/{record_id}` is full-replace, exactly like Slice 1's canvas `PUT`.

## 6. `GET /api/v1/business-builder/{kind}` — list records + form fields

Returns every record of this kind for the startup (ordered by `position`, oldest-first) plus a
`fields` descriptor array the FE renders a create/edit form from. Read-only, creates nothing.
Unknown `{kind}` segment → `404 NOT_FOUND`.

`e2e/_captures/business/record_list.json` — captured after the one persona created in §7 below
(status `200`):

```json
{
  "data": {
    "records": [
      {
        "id": "e78881e7-f452-4783-a975-eb797434893e",
        "kind": "persona",
        "data": {
          "name": "Busy Founder Bea",
          "goals": ["Ship an MVP", "Get first 10 customers"],
          "quote": "I just need this to work.",
          "demographics": "28-40, solo/early-stage founder",
          "frustrations": ["No time to research tools"],
          "watering_holes": ["Indie Hackers", "Twitter/X"]
        },
        "position": 0
      }
    ],
    "fields": [
      { "key": "name", "required": true, "type": "<class 'str'>", "choices": null },
      { "key": "demographics", "required": false, "type": "<class 'str'>", "choices": null },
      { "key": "goals", "required": false, "type": "list[str]", "choices": null },
      { "key": "frustrations", "required": false, "type": "list[str]", "choices": null },
      { "key": "watering_holes", "required": false, "type": "list[str]", "choices": null },
      { "key": "quote", "required": false, "type": "<class 'str'>", "choices": null }
    ]
  },
  "meta": null
}
```

### The `fields` contract — render your form from this, including `choices` for enum fields

**`fields` is served on every `GET /{kind}` specifically so the FE never hardcodes which fields a
record kind has, whether they're required, or — for an enum-backed field — what its valid values
are.** Build your create/edit form by iterating `fields`, not by shipping a hardcoded persona/
competitor/pricing form component.

| `fields[i]` field | Meaning |
|---|---|
| `key` | the field name to read/write inside `data` (e.g. `"name"`, `"threat_level"`). |
| `required` | `true`/`false` — whether Pydantic requires this field (no default). Only `name` (persona/revenue_stream/competitor) and `model_type` (pricing) are `required: true` today; everything else has a schema default. |
| `type` | **a human-readable hint only — do not parse this as a stable contract.** It's a raw Python `str(annotation)` — confirmed live to leak Python-internal reprs: `"<class 'str'>"`, `"list[str]"`, `"<enum 'ThreatLevel'>"`, even `"list[app.services.business.record_defs.PricingTier]"` for pricing's nested `tiers` field (see `pricing_list.json` below). Use `choices` (not `type`) to detect "this is an enum, render a dropdown." |
| `choices` | **`null` for every non-enum field.** For an enum-backed field, the list of valid string values — e.g. `threat_level` → `["low", "medium", "high"]`, `model_type` → the 5 `PricingModelType` values. **Build your dropdown options from this, never from a hardcoded list** — if the backend adds a `ThreatLevel` member later, your dropdown picks it up with no FE deploy. |

**Live proof of `choices` on an enum field** — `e2e/_captures/business/competitor_list.json`
(status `200`, `fields` array only shown here; `records` is the one competitor from §7):

```json
{
  "key": "threat_level",
  "required": false,
  "type": "<enum 'ThreatLevel'>",
  "choices": ["low", "medium", "high"]
}
```

`e2e/_captures/business/pricing_list.json` (status `200`, `fields` array only shown here):

```json
{
  "key": "model_type",
  "required": true,
  "type": "<enum 'PricingModelType'>",
  "choices": ["subscription", "one_time", "usage", "freemium", "tiered"]
}
```

Note `model_type` is `required: true` (no default — every pricing record must declare a model
type) while `threat_level` is `required: false` (defaults to `"medium"` server-side if omitted).

---

## 7. `POST /api/v1/business-builder/{kind}` — create a record

Request body: `{"data": {...fields for this kind...}}`. Omitted fields take their schema default
(e.g. an omitted persona `quote` becomes `""`, an omitted competitor `threat_level` becomes
`"medium"`). An unknown field in `data`, a missing required field, or an invalid enum value →
`422 VALIDATION_ERROR`.

`e2e/_captures/business/record_create.json` — request was `{"data": {"name": "Busy Founder Bea",
"demographics": "28-40, solo/early-stage founder", "goals": ["Ship an MVP", "Get first 10
customers"], "frustrations": ["No time to research tools"], "watering_holes": ["Indie Hackers",
"Twitter/X"], "quote": "I just need this to work."}}`. Response (status `201`):

```json
{
  "data": {
    "id": "e78881e7-f452-4783-a975-eb797434893e",
    "kind": "persona",
    "data": {
      "name": "Busy Founder Bea",
      "goals": ["Ship an MVP", "Get first 10 customers"],
      "quote": "I just need this to work.",
      "demographics": "28-40, solo/early-stage founder",
      "frustrations": ["No time to research tools"],
      "watering_holes": ["Indie Hackers", "Twitter/X"]
    },
    "position": 0
  },
  "meta": null
}
```

**⚠️ Key-order trap, same as Slice 1's `blocks`:** notice the response `data`'s key order
(`name, goals, quote, demographics, frustrations, watering_holes`) does **not** match the request
order — this is Postgres JSONB round-tripping, same as Slice 1's canvas `blocks`. Never rely on
`data`'s key order; use `fields` (§6) for form layout order.

`e2e/_captures/business/competitor_create.json` — request included `"threat_level": "high"`.
Response (status `201`):

```json
{
  "data": {
    "id": "9a004ca2-a173-4c02-b4fb-b11829a4a17d",
    "kind": "competitor",
    "data": {
      "name": "BigCo Rival",
      "price": "$$$$",
      "strengths": ["Brand recognition"],
      "weaknesses": ["Slow to ship"],
      "positioning": "Enterprise incumbent",
      "threat_level": "high"
    },
    "position": 0
  },
  "meta": null
}
```

`e2e/_captures/business/pricing_create.json` — request was `{"data": {"model_type": "tiered",
"tiers": [{"name": "Starter", "price": "$19/mo", "features": ["1 seat"]}, {"name": "Pro", "price":
"$49/mo", "features": ["5 seats", "Priority support"]}]}}`. Response (status `201`) — note
`tiers` is a nested list of objects, not a flat list of strings like `strengths`/`weaknesses`:

```json
{
  "data": {
    "id": "5e898f6c-a9dc-4c9f-9171-200376693e45",
    "kind": "pricing",
    "data": {
      "tiers": [
        { "name": "Starter", "price": "$19/mo", "features": ["1 seat"] },
        { "name": "Pro", "price": "$49/mo", "features": ["5 seats", "Priority support"] }
      ],
      "model_type": "tiered"
    },
    "position": 0
  },
  "meta": null
}
```

---

## 8. `PUT /api/v1/business-builder/{kind}/{record_id}` — full-replace update

**Read the full-replace warning near the top of this document — it applies here exactly as it
does to Slice 1's canvas `PUT`.** Send the complete `data` object every time; any field you omit
resets to its schema default, it is not left alone.

`e2e/_captures/business/record_update.json` — request was `{"data": {"name": "Busy Founder Bea
(Updated)", "goals": ["Ship an MVP"]}}` (deliberately omitting `quote`, `demographics`,
`frustrations`, `watering_holes` to demonstrate the reset). Response (status `200`):

```json
{
  "data": {
    "id": "e78881e7-f452-4783-a975-eb797434893e",
    "kind": "persona",
    "data": {
      "name": "Busy Founder Bea (Updated)",
      "goals": ["Ship an MVP"],
      "quote": "",
      "demographics": "",
      "frustrations": [],
      "watering_holes": []
    },
    "position": 0
  },
  "meta": null
}
```

Notice `quote`, `demographics`, `frustrations`, and `watering_holes` all reset to their empty
default — none were sent, so none were preserved. **What this means for your edit flow:** keep the
full current `data` object in your local state (from the last `GET`/`POST`/`PUT` response), apply
only the edited field(s) to your local copy, and send the entire local `data` object on every
save.

An unknown or cross-tenant `{record_id}` → `404 NOT_FOUND` (not captured live in this task's
journey — a happy-path walk never exercises this; derived from source (`_record()`,
`app/services/business/records.py:74`) and confirmed by
`tests/api/test_business_records.py::test_put_cross_tenant_404` — see the SOP's Follow-ups for a
noted gap in that test's coverage: it currently proves "unknown id" 404s, not "a *real* other
tenant's id" 404s).

---

## 9. `DELETE /api/v1/business-builder/{kind}/{record_id}` — delete a record

Removes one record. **Not captured live in this task's e2e journey** (the recorded journey never
deletes) — this response shape is derived from source (`delete_kind`,
`app/api/v1/endpoints/business.py:187`, which returns `success_response({"deleted": True})`) and
confirmed by `tests/api/test_business_records.py::test_delete`:

```json
{
  "data": { "deleted": true },
  "meta": null
}
```

Status `200`. Same 404 behavior as `PUT` (§8) for an unknown/cross-tenant `{record_id}`.

---

## 10. `POST /api/v1/business-builder/{kind}/ai-fill` — AI-fill (deferred)

Enqueues an AI-fill job for this record kind. **Writes no record itself** — only a job row. Same
deferred-job seam as Slice 1's canvas ai-fill (§4): no worker consumes `business.{kind}.ai_fill`
jobs yet.

`e2e/_captures/business/record_ai_fill.json` (status `202`, called for `personas`):

```json
{
  "data": {
    "job_id": "517d60dd-594c-41e0-bb76-ff178990378c",
    "status": "queued"
  },
  "meta": null
}
```

Poll `GET /api/v1/jobs/{job_id}` exactly as in §4 (Bearer token only, no `X-Workspace-Id`
needed). `e2e/_captures/business/record_ai_fill_job.json` (status `200`):

```json
{
  "data": {
    "id": "517d60dd-594c-41e0-bb76-ff178990378c",
    "type": "business.persona.ai_fill",
    "status": "queued",
    "result": null,
    "error": null
  },
  "meta": null
}
```

**⚠️ Same deferral caveat as §4: this job stays `"queued"` indefinitely.** No worker exists for
any `business.{kind}.ai_fill` job type yet. `job.type` is `f"business.{kind}.ai_fill"` — e.g.
`"business.persona.ai_fill"`, `"business.competitor.ai_fill"` — parse it if your polling UI needs
to confirm which kind a job belongs to, though in practice you already know since you're the one
who called `POST /{kind}/ai-fill`.

---

## 11. Errors (Slice 2 — records)

Same shared error envelope as §5. **None of the rows below were exercised by the live E2E
journey** except `422 VALIDATION_ERROR`, which is derived from source + unit test the same as the
rest (the journey is a happy-path walk and never sends invalid `data`).

| Code | HTTP | When | Source |
|---|---|---|---|
| `VALIDATION_ERROR` | 422 | `POST`/`PUT` `data` fails the kind's Pydantic schema — missing required field (`name`, or pricing's `model_type`), unknown field (`extra="forbid"`), or an invalid enum value (e.g. `threat_level: "apocalyptic"`, `model_type: "per_seat"`) | `validate`, `app/services/business/records.py:15`; `tests/api/test_business_records.py::test_create_bad_data_422`, `tests/services/business/test_records.py::test_validate_rejects_bad_enum_choice` |
| `NOT_FOUND` | 404 | `{kind}` in the URL isn't one of the 4 valid record kinds | `_parse_kind`, `tests/api/test_business_records.py::test_unknown_kind_404` |
| `NOT_FOUND` | 404 | `{record_id}` doesn't exist, or belongs to a different startup | `_record()`, `tests/api/test_business_records.py::test_put_cross_tenant_404` — see §8's caveat on this test's actual coverage |
| `FORBIDDEN` | 403 | a mentor calls `POST`/`PUT`/`DELETE`/`ai-fill` (reads are open to mentors; writes are not) | `require_role`, `tests/api/test_business_records.py::test_create_mentor_forbidden_403` / `test_ai_fill_mentor_forbidden_403` |

Same `X-Workspace-Id`/auth behavior as §5 — all five record routes share the same
`require_workspace`/`require_role`/`get_verified_user` dependency chain.

---

## Verification table

Rows marked ✅ were exercised **live**, over real HTTP, against a real Postgres-backed server
(`make e2e`, `e2e/test_business_builder.py::test_business_builder_journey` for Slice 1 rows,
`e2e/test_business_builder.py::test_business_builder_records_journey` for Slice 2 rows) — not
just unit-tested in-process — and the response body is captured verbatim in the named file. Rows
marked ⬜ are covered by the unit suite (real Postgres, per-test rollback — never mocked) but
were **not** re-asserted over live HTTP; the shape source is named.

### Slice 1 — canvases

| Endpoint / behavior | Verified live? | Source |
|---|---|---|
| `GET /overview` — 5 canvas types + 4 record kinds, `status == "start"` when untouched | ✅ | `overview_empty.json` |
| `GET /canvases/{type}` — lazy-create at v1, block scaffold, `block_defs` matches `blocks` keys | ✅ | `canvas_get.json` |
| `PUT /canvases/{type}` — saves, bumps version, `completion` recomputes | ✅ | `canvas_put.json` |
| `PUT` — full-replace semantics (omitted keys reset to empty) | ✅ | `canvas_put.json` (`value_propositions` etc. come back `[]` despite existing pre-save) |
| `GET /overview` — a saved canvas's row flips `"start"` → `"continue"`, others unaffected | ✅ | `overview_after.json` |
| `POST .../ai-fill` — 202, `{job_id, status: "queued"}`, writes no canvas row | ✅ | `ai_fill.json`; no-canvas-write assertion is unit-tested (`test_ai_fill_enqueues_job_and_writes_no_canvas`), not separately re-checked live |
| `GET /jobs/{id}` — the enqueued job, `status: "queued"` | ✅ | `ai_fill_job.json` |
| `409 CANVAS_VERSION_CONFLICT` on a stale `PUT` | ⬜ | `tests/api/test_business_canvases.py::test_put_stale_version_409` |
| `422 VALIDATION_ERROR` on an unknown block key or wrong kind | ⬜ | `test_put_bad_block_422` |
| `404 NOT_FOUND` on an unrecognized `{type}` | ⬜ | `test_get_unknown_type_404` |
| `403 FORBIDDEN` — mentor blocked on `PUT`/`ai-fill`, allowed on reads | ⬜ | `test_put_mentor_forbidden_403`, `test_ai_fill_mentor_forbidden_403` |
| `403` — non-member (no workspace membership) blocked on `overview` | ⬜ | `test_overview_requires_membership_403` |
| `business.artifact.completed` event on the not-complete → complete transition | ⬜ | `tests/services/business/test_service.py`; the journey only fills 1 of 9 blocks, never reaches `"complete"` |
| JSONB key-order mismatch between `blocks` and `block_defs` | ✅ | observed directly in `canvas_get.json`/`canvas_put.json` (`blocks` starts with `channels`; `block_defs` starts with `key_partners`) |

### Slice 2 — records

| Endpoint / behavior | Verified live? | Source |
|---|---|---|
| `POST /{kind}` — 201, creates a persona at `position: 0` | ✅ | `record_create.json` |
| `GET /{kind}` — lists records ordered by `position`, `fields` includes `name` | ✅ | `record_list.json` |
| `PUT /{kind}/{id}` — full-replace update; omitted keys reset to schema default | ✅ | `record_update.json` (`quote`/`demographics`/`frustrations`/`watering_holes` all reset to empty) |
| `GET /overview` — a kind's row flips `"start"` → `"complete"`, `count` increments, other kinds unaffected | ✅ | `overview_records.json` |
| `POST /{kind}` — `threat_level` enum field accepted and round-trips | ✅ | `competitor_create.json` |
| `GET /{kind}` — `fields` exposes `threat_level`'s `choices` as `["low","medium","high"]` | ✅ | `competitor_list.json` |
| `POST /{kind}` — `model_type` enum + nested `tiers` list accepted and round-trip | ✅ | `pricing_create.json` |
| `GET /{kind}` — `fields` exposes `model_type`'s `choices` as all 5 `PricingModelType` values | ✅ | `pricing_list.json` |
| `POST /{kind}/ai-fill` — 202, `{job_id, status: "queued"}`, writes no record | ✅ | `record_ai_fill.json`; no-record-write assertion is unit-tested (`test_ai_fill_enqueues_job_writes_no_record`), not separately re-checked live |
| `GET /jobs/{id}` — the enqueued record job, `status: "queued"`, `type: "business.persona.ai_fill"` | ✅ | `record_ai_fill_job.json` |
| `DELETE /{kind}/{id}` — 200, `{deleted: true}` | ⬜ | `tests/api/test_business_records.py::test_delete` — not called in the live journey |
| `422 VALIDATION_ERROR` — missing required field, unknown field, bad enum value | ⬜ | `test_create_bad_data_422`, `tests/services/business/test_records.py::test_validate_rejects_bad_enum_choice` |
| `404 NOT_FOUND` on an unrecognized `{kind}` segment | ⬜ | `test_unknown_kind_404` |
| `404 NOT_FOUND` on an unknown `{record_id}` (see §8's caveat: not yet a genuine cross-tenant test) | ⬜ | `test_put_cross_tenant_404` |
| `403 FORBIDDEN` — mentor blocked on write routes, allowed on `GET /{kind}` | ⬜ | `test_create_mentor_forbidden_403`, `test_ai_fill_mentor_forbidden_403` |
| `business.artifact.completed` event fires once, on a kind's first record only | ⬜ | `tests/services/business/test_records.py::test_create_first_record_emits_completed_event_once` |
| JSONB key-order mismatch between request `data` and response `data` | ✅ | observed directly in `record_create.json` (response key order differs from the request's) |

Every ⬜ row is safe to build against — it's covered by the real-Postgres unit suite
(`tests/api/test_business_canvases.py`, `tests/api/test_business_records.py`,
`tests/services/business/`) and the shapes match this API's established envelope/error
conventions — they just weren't double-verified end-to-end over live HTTP in this module's
journeys.
