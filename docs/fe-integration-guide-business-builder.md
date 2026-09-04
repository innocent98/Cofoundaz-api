# FE Integration Guide — Business Builder Canvas Core (Module 08, Slice 1)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_business_builder.py::test_business_builder_journey` running against a real server
(`make e2e`) — see `e2e/_captures/business/*.json`. Nothing here is retyped from memory or
invented. IDs are real values from that ephemeral test run (they differ on every real request,
but the shapes are exact). Error-body shapes (§5) were **not** exercised by the live journey
(it's a happy-path walk) — they're derived from source and unit tests, and are called out inline
and in the verification table.

Base path: `/api/v1/business-builder`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header identifying the active workspace
(`GET /auth/me` → `data.active_workspace_id`), same as every other tenant-scoped endpoint in this
API. **Reads (`GET /overview`, `GET /canvases/{type}`) are open to any active member** —
founder, team_member, and mentor. **Writes (`PUT /canvases/{type}`, `POST
/canvases/{type}/ai-fill`) require founder or team_member — a mentor gets 403
`FORBIDDEN`.**

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see §5).

**Canvas types** (path segment `{type}`, exactly these 5 values — an unrecognized value 404s):
`business_model`, `lean`, `value_prop`, `mission_vision`, `swot`.

---

## ⚠️ Read this before wiring up saving — `PUT` is full-replace, NOT a partial merge

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

One call returns all 5 canvas types with their completion state — this is what a "Business
Builder" home screen/menu is built on. Read-only: calling this creates no rows, even for canvas
types the founder has never opened.

`e2e/_captures/business/overview_empty.json` — captured for a freshly-onboarded founder, before
any canvas was touched (status `200`):

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
    }
  ],
  "meta": null
}
```

`e2e/_captures/business/overview_after.json` — captured after `business_model` had one block
saved (status `200`), showing the row flip:

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
    }
  ],
  "meta": null
}
```

### Row field reference

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
    "job_id": "d40c94f1-3b1a-488c-9d47-0deb74da6319",
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
    "id": "d40c94f1-3b1a-488c-9d47-0deb74da6319",
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

## 5. Errors

Every error is `{"error": {"code": …, "message": …, "field_errors": [...]}}` (no `data`/`meta`)
— the same shared shape used throughout this API. **None of the error paths below were exercised
by the live E2E journey** (it's a single happy-path walk) — every shape here is derived from
source (`app/core/errors.py`) and confirmed by the named unit test.

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

## Verification table

Rows marked ✅ were exercised **live**, over real HTTP, against a real Postgres-backed server
(`make e2e`, `e2e/test_business_builder.py::test_business_builder_journey`) — not just
unit-tested in-process — and the response body is captured verbatim in the named file. Rows
marked ⬜ are covered by the unit suite (real Postgres, per-test rollback — never mocked) but
were **not** re-asserted over live HTTP; the shape source is named.

| Endpoint / behavior | Verified live? | Source |
|---|---|---|
| `GET /overview` — all 5 types, `status == "start"` when untouched | ✅ | `overview_empty.json` |
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

Every ⬜ row is safe to build against — it's covered by the real-Postgres unit suite
(`tests/api/test_business_canvases.py`, `tests/services/business/`) and the shapes match this
API's established envelope/error conventions — they just weren't double-verified end-to-end over
live HTTP in this module's single journey.
