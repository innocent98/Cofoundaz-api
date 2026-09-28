# FE Integration Guide — Marketing Hub: Channel-Plan Recommender + Channel Fit Notes (Module 10, Slice 3b)

> **Provenance.** `e2e/test_marketing.py::test_marketing_channel_ai_journey` has been run
> (`bash scripts/e2e_run.sh`, 56/56 e2e passed) and every response body in §1–§5 below is pasted
> **verbatim** from the captures it wrote to `e2e/_captures/marketing/`
> (`channel_plan_accepted.json`, `channel_plan_ready.json`, `fit_notes_accepted.json`,
> `fit_notes_ready.json`, `channels_with_fit_notes.json`). Nothing here is hand-written or "tidied"
> from the schema or from memory. The captures were taken with `LLM_PROVIDER=stub` (this repo's
> e2e default — no real LLM call, no network) — every place that matters is flagged inline below,
> most importantly §2's **even-split `channel_mix`**, which is **real stub behavior**, not a
> mistake (see the boxed note in §2).
>
> Not e2e-captured: the `failed`/`over_budget` status (forcing a real over-budget failure would
> require seeding the `llm_usage_daily` ledger directly, breaking the shared e2e process's budget
> guarantee for every other test — see `docs/fe-integration-guide-ai-status.md`), and the RBAC 403
> matrix. Those are unit/integration-tested; flagged inline and again in the verification table.

This extends **`docs/fe-integration-guide-marketing-copy.md`** (Slice 3a — AI copy generation +
plan-week), itself extending **`docs/fe-integration-guide-marketing-calendar.md`** (Slice 1) and
**`docs/fe-integration-guide-marketing-campaigns.md`** (Slice 2). Same base path, same auth model,
same envelope, same unified `marketing_ai_generations` table (now with two more `kind` values).
Read those guides first if you haven't integrated the Marketing Hub yet; this one only covers what
Slice 3b adds: an **AI channel-mix recommender** for the campaign-create wizard, and **per-channel
AI fit notes** for the Channels page. It also cross-references
**`docs/fe-integration-guide-ai-status.md`** for the shared generating/ready/failed +
`over_budget` async pattern every AI-generation feature in this API uses.

Base path: `/api/v1/marketing`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header, same as every other tenant-scoped
endpoint in this API. **Access: founder or team_member only** — same `_marketing =
require_role(founder, team_member)` dependency as every other `/marketing` route; every other
membership role gets **403 `FORBIDDEN`** on all 4 routes below, including both `{id}` GET routes
(RBAC rejects before any row lookup). ⚠️ Not e2e-captured; verified by
`tests/api/test_marketing_ai.py::test_channel_ai_rbac_forbidden` (parametrized over
`mentor`/`investor`).

Every success response is the standard envelope `{"data": …, "meta": null}`.

---

## 0. Same shape as Slice 3a: two more `kind`s on one table

`MarketingGenerationKind` gains two members this slice: `channel_plan` and `channel_fit`. Both are
rows in the same `marketing_ai_generations` table Slice 3a's `copy`/`plan_week` already use, served
through **separate routes per kind**, same as before:

```
POST …/channel-plan/recommend       ->  202 {id, status: "generating"}
GET  …/channel-plan/recommendations/{id}  ->  200 {..., status, output: {channel_mix, rationale}, ...}

POST …/channels/fit-notes/generate  ->  202 {id, status: "generating"}
GET  …/channels/fit-notes/{id}      ->  200 {..., status, output: {notes}, ...}
```

**A `channel_plan` id is 404 on the `channel_fit` route and vice versa** — same kind-scoped
`get_generation` (`app/services/marketing/ai_content.py`) Slice 3a established; see §6.

Neither route has a history-list endpoint (unlike copy's `GET /copy/generations`) — this is YAGNI
by design (design spec D1/D2): the FE applies a channel-plan result to the campaign wizard's
sliders, or a fit-notes result inline onto the Channels page, and moves on. Poll cadence guidance
is unchanged from Slice 3a — a few seconds, backing off, capped at a reasonable timeout.

---

## 1. `POST /api/v1/marketing/channel-plan/recommend` — start a channel-mix recommendation

**Request** (from the e2e journey — this exact body produced `channel_plan_accepted.json` below):

```json
{ "objective": "leads" }
```

| Field | Type | Rules |
|---|---|---|
| `objective` | `CampaignObjective` (`awareness \| leads \| sales \| launch`) | Required |
| `budget` | integer, cents, ≥0 \| null | Optional — only flavors the rationale text, does not change validation |

**Not tied to a `campaign_id`** — this is a standalone recommendation (design decision D1): it
works mid-wizard, before the founder has saved a draft campaign. Stage and industry are read
server-side from the caller's `Startup` row, never from the request body.

**`e2e/_captures/marketing/channel_plan_accepted.json` — 202:**

```json
{
  "data": {
    "id": "0c60c699-bf25-4d85-89b6-7f7a94ed146e",
    "status": "generating"
  },
  "meta": null
}
```

Same thin `{id, status}` shape every other AI-generation 202 in this API uses — store `id`, poll §2.

---

## 2. `GET /api/v1/marketing/channel-plan/recommendations/{generation_id}` — poll a recommendation

`e2e/_captures/marketing/channel_plan_ready.json` — captured after the in-process worker drained
the job (`LLM_PROVIDER=stub`):

```json
{
  "data": {
    "id": "0c60c699-bf25-4d85-89b6-7f7a94ed146e",
    "startup_id": "fba8ff9f-6b69-46aa-941b-74342b68ef6a",
    "kind": "channel_plan",
    "status": "ready",
    "inputs": {
      "budget": null,
      "objective": "leads"
    },
    "output": {
      "rationale": "[stub-llm] rationale",
      "channel_mix": {
        "email": 13,
        "events": 12,
        "search": 13,
        "referral": 12,
        "content_seo": 12,
        "paid_social": 13,
        "partnerships": 12,
        "organic_social": 13
      }
    },
    "error": null,
    "created_at": "2026-09-28T18:10:33.923923Z",
    "updated_at": "2026-09-28T18:10:33.948056Z"
  },
  "meta": null
}
```

| Field | Meaning |
|---|---|
| `inputs` | The exact request body from §1, round-tripped as stored JSON (`budget` appears explicitly as `null` when omitted, not absent) |
| `output.channel_mix` | `ChannelKey` → **integer percent**, **always summing to exactly 100** — the worker normalizes server-side (see the boxed note below). Never render this as "may not sum to 100"; it always does. A schema-compliant provider returns all 8 keys (the response schema requires them); in the rare event a provider omits one, the server still normalizes whatever valid keys it returned to sum to 100, so treat a missing key as 0% rather than assuming all 8 are always present. |
| `output.rationale` | A short string a founder can read next to the mix |
| `error` | `null` while `generating`/`ready`; a string (`"over_budget"` today) when `status == "failed"` — see §7 |

**Render `output.channel_mix` as the campaign wizard's channel-split sliders' *starting* values**,
not a locked-in decision — the founder can still drag them. There is no server-side link recorded
between this generation and the campaign it informs; if the FE saves that provenance, it tracks it
client-side (same pattern as Slice 3a's Write-with-AI/Save-to-calendar compositions).

### ⚠️ The one thing the FE must know: under the stub provider, `channel_mix` is an even split — not a real recommendation

**This capture's `channel_mix` (13/12/13/12/12/13/12/13, summing to 100) is an even split across
the 8 channels, not a model-weighted recommendation.** This is real, reproducible stub behavior,
not a copy/paste mistake in this guide. Root cause: `channel_plan_schema()` declares every channel
as `{"type": "integer"}` (no `enum` constraint on the *value*, unlike the *keys*, which the schema
does enumerate) — `StubLLMClient._stub_value` returns `0` for every plain `integer`/`number` field,
so the stub always proposes `{channel: 0}` for all 8 channels. `normalize_channel_mix`
(`app/worker/handlers/marketing_ai.py`) treats an all-zero (`total <= 0`) input as "nothing valid,"
which is its documented fallback path: an **even split** using largest-remainder rounding
(`divmod(100, 8)` → 4 channels get 13, 4 get 12, rounding out to exactly 100). `rationale` is
likewise always the stub's generic placeholder string, `"[stub-llm] rationale"`.

**Under a real LLM provider**, the model returns real, differentiated integer percentages per
channel (e.g. weighted toward `search`/`paid_social` for a `leads` objective), `normalize_channel_mix`
still runs (rounding to exactly 100 via the same largest-remainder algorithm, dropping any
non-finite/negative/invalid-key value), and `rationale` is real, objective-grounded prose from the
model — not a placeholder. **Do not treat the even split or the `"[stub-llm]"` string as the
production behavior** when building FE mocks/tests/screenshots from this guide; use it only to
verify the *shape* (8 keys, integers summing to 100, a non-empty rationale string) and the
normalization guarantee, not the specific values.

---

## 3. `POST /api/v1/marketing/channels/fit-notes/generate` — start a fit-notes generation

**No request body** (the e2e journey sends `json={}`, same convention as Slice 3a's plan-week
route — the worker sources stage/industry from the `Startup` row and each channel's current
`status`, not from the request).

**`e2e/_captures/marketing/fit_notes_accepted.json` — 202:**

```json
{
  "data": {
    "id": "70dc29df-c857-4d08-b90c-a8f63cfe7e89",
    "status": "generating"
  },
  "meta": null
}
```

**One job produces all 8 channels' notes in a single LLM call** (design decision D2 — cheaper and
gives the model cross-channel coherence, vs. 8 separate calls). Store `id`, poll §4.

---

## 4. `GET /api/v1/marketing/channels/fit-notes/{generation_id}` — poll a fit-notes generation

`e2e/_captures/marketing/fit_notes_ready.json` — captured after the in-process worker drained the
job:

```json
{
  "data": {
    "id": "70dc29df-c857-4d08-b90c-a8f63cfe7e89",
    "startup_id": "fba8ff9f-6b69-46aa-941b-74342b68ef6a",
    "kind": "channel_fit",
    "status": "ready",
    "inputs": {},
    "output": {
      "notes": {
        "email": "[stub-llm] email",
        "events": "[stub-llm] events",
        "search": "[stub-llm] search",
        "referral": "[stub-llm] referral",
        "content_seo": "[stub-llm] content_seo",
        "paid_social": "[stub-llm] paid_social",
        "partnerships": "[stub-llm] partnerships",
        "organic_social": "[stub-llm] organic_social"
      }
    },
    "error": null,
    "created_at": "2026-09-28T18:10:33.965476Z",
    "updated_at": "2026-09-28T18:10:33.976849Z"
  },
  "meta": null
}
```

`inputs` is always `{}` (no request fields to echo). `output.notes` is **all 8 `ChannelKey`
values present** (the schema enumerates the keys, so unlike `channel_mix`'s values, the stub
always emits a syntactically valid string per key — here each is the generic placeholder
`"[stub-llm] <channel>"`). Under a real provider, each value is one or two sentences of real
fit-note prose grounded in the startup's stage/industry and that channel's current status (§5).

**This poll response is a secondary read path** — the FE does not need to poll this route to get
the notes onto the Channels page; §5 explains the primary, inline path.

---

## 5. The primary read path: `GET /api/v1/marketing/channels` now returns the notes inline

**This is the existing Slice 1 route, unchanged in shape except for two new fields per card** —
no new route to integrate for *reading* fit notes day-to-day; only §3's generate call is new.

`e2e/_captures/marketing/channels_with_fit_notes.json` — captured immediately after the fit-notes
generation in §3/§4 completed:

```json
{
  "data": [
    {
      "id": "529b4cf1-4cf5-42f8-9735-6b193f267eef",
      "key": "organic_social",
      "status": "not_started",
      "notes": null,
      "ai_fit_note": "[stub-llm] organic_social",
      "fit_note_generated_at": "2026-09-28T18:10:33.985980Z"
    },
    {
      "id": "ddeb5d34-ebd3-4450-962a-03cb8613b0af",
      "key": "paid_social",
      "status": "not_started",
      "notes": null,
      "ai_fit_note": "[stub-llm] paid_social",
      "fit_note_generated_at": "2026-09-28T18:10:33.985980Z"
    }
  ],
  "meta": null
}
```

(Trimmed to 2 of the 8 cards above — the full capture has all 8, one per `ChannelKey`, each with
its own `ai_fit_note`/`fit_note_generated_at`.)

| Field | Meaning |
|---|---|
| `notes` | **Unrelated founder-authored field, pre-existing since Slice 1** — the founder's own free-text notes on the channel (`PATCH /channels/{key}`). Do not confuse with `ai_fit_note`. |
| `ai_fit_note` | **New this slice.** `null` until a fit-notes generation has completed at least once for this workspace; then the note text for that channel from the most recent completed run. |
| `fit_note_generated_at` | **New this slice.** `null` alongside `ai_fit_note` until the first run; then an ISO 8601 UTC timestamp — **the same instant for all 8 channels from one run** (`app/worker/handlers/marketing_ai.py::handle_marketing_channel_fit` stamps `now()` once and applies it to every row the single job writes). Use it to render "AI fit note as of {time}" per card without expecting per-channel staggering. |

### Field-nesting / naming trap: `notes` vs. `ai_fit_note`

**`notes` (founder-authored, pre-existing) and `ai_fit_note` (AI-generated, new this slice) are
two separate, independently-null fields on the same card** — a channel can have one, both,
neither, or (after a founder edits their own note post-generation) a stale-looking pair where
`ai_fit_note` still reflects an older generation than the founder's latest edit to `notes`.
Rendering these as a single "note" field, or assuming updating one updates the other, will produce
a visibly wrong UI. Render them as two distinct pieces of copy — e.g. "Your notes" vs. "AI says"
— on the channel card.

### What re-running fit-notes generation does to existing notes

Re-`POST /channels/fit-notes/generate` overwrites **every** channel whose key the model returns a
note for, with a fresh `fit_note_generated_at`. **A `ChannelKey` the model's response omits (or
that fails validation) keeps its previous `ai_fit_note` untouched** — it is never wiped to `null`
on a partial response. ⚠️ Not e2e-captured (the journey only runs one generation); unit-verified by
`tests/worker/test_marketing_ai_handlers.py::test_channel_fit_keeps_prior_note_when_key_omitted`.

### Lazy-seeding still applies

Same lazy-seed-on-first-read mechanic as Slice 1: if fit-notes generation runs before the
workspace's channels have ever been seeded (i.e. before any `GET /channels` call), the worker seeds
all 8 rows itself (reusing `list_channels`'s seeding path) before writing notes — the FE never
needs to call `GET /channels` first to "warm up" the rows before generating fit notes. ⚠️ Not
e2e-captured (the journey's earlier steps already trigger seeding); unit-verified by
`tests/worker/test_marketing_ai_handlers.py::test_channel_fit_writes_notes_onto_rows_and_seeds`.

---

## 6. Kind-mismatch is a 404, same convention as Slice 3a

A `channel_fit` generation's `id` used against the **channel-plan** route (`GET
/channel-plan/recommendations/{channel_fit_id}`) returns **404 `NOT_FOUND`** — and symmetrically, a
`channel_plan` id against the fit-notes route 404s too. Same kind-scoped `get_generation` query
(`startup_id` + `kind` in one filter) Slice 3a's copy/plan-week routes already use — a kind
mismatch is indistinguishable, response-wise, from "doesn't exist" or "belongs to another
workspace." ✅ Live-equivalent behavior verified by
`tests/api/test_marketing_ai.py::test_channel_plan_kind_mismatch_404`. Never assume an id from one
flow is valid on the other's route, even though both rows live in the same table as `copy`/
`plan_week` generations.

---

## 7. The `over_budget` failure — cross-ref `docs/fe-integration-guide-ai-status.md`

Both `channel_plan` and `channel_fit` generations can land in `status: "failed"` with `error:
"over_budget"` — the identical workspace-wide LLM budget guard every AI feature in this API
shares, unchanged from Slice 3a's `_fail_over_budget` helper (`app/worker/handlers/marketing_ai.py`).
`output` stays `{}` on this path. For `channel_fit` specifically: **lazy-seeding of the 8 channel
rows still runs before the budget check** — an over-budget fit-notes run may have seeded channel
rows that didn't exist before, but writes no `ai_fit_note` values to them. ⚠️ Not e2e-captured
(forcing it live would require seeding the `llm_usage_daily` ledger directly); unit-verified by
`tests/worker/test_marketing_ai_handlers.py::test_channel_plan_over_budget_fails` and
`test_channel_fit_over_budget_fails`.

**FE handling:** identical to Slice 3a's §6 — treat `over_budget` as a quiet, expected degrade
("AI is temporarily paused, try again after `resets_at`"), not an error to alarm the founder with.
There is no automatic retry server-side.

---

## Errors

Standard envelope, same shape as Slices 1–3a:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "...",
    "field_errors": [ { "field": "objective", "message": "..." } ]
  }
}
```

| Status | Code | When |
|---|---|---|
| 401 | — | Missing or invalid access token |
| 403 | `EMAIL_NOT_VERIFIED` | Signed in, but email not verified |
| 403 | `FORBIDDEN` | Not a founder/team_member of this workspace — enforced before any row lookup, including on both `{id}` GET routes |
| 404 | `NOT_FOUND` | Generation id does not exist, belongs to another workspace, or is the **wrong `kind`** for the route called (§6) |
| 422 | `VALIDATION_ERROR` | Invalid/missing `objective` enum value; negative `budget` |

A generation that reaches `status: "failed"` (`error: "over_budget"`) is **not** an HTTP error —
`GET …/{id}` still returns 200 with the failed row (§7). Only malformed requests and access/lookup
failures use the `{"error": {...}}` envelope.

---

## Verification table

| Behaviour | Verified live? | Source |
|---|---|---|
| `POST /channel-plan/recommend` → 202 `{id, status: "generating"}` | ✅ | `channel_plan_accepted.json` |
| `GET /channel-plan/recommendations/{id}` → ready, full `GenerationResponse` shape | ✅ | `channel_plan_ready.json` |
| `output.channel_mix` has all 8 `ChannelKey`s, integers summing to exactly 100 | ✅ live (this capture sums to 100) | `channel_plan_ready.json` |
| Under the stub provider, `channel_mix` is specifically the even-split fallback (13/12 pattern) because the stub emits `0` for every integer field and `normalize_channel_mix` treats an all-zero input as "nothing valid" | ✅ live (the even split itself), root cause traced statically | `channel_plan_ready.json`; `app/services/marketing/ai_prompts.py::channel_plan_schema`, `app/worker/handlers/marketing_ai.py::normalize_channel_mix` |
| `rationale` under stub is the generic placeholder `"[stub-llm] rationale"`; real prose under a real provider is schema/prompt-verified, not live | ✅ live (stub) | `channel_plan_ready.json`, `app/services/marketing/ai_prompts.py::build_channel_plan_messages` |
| Normalization handles non-finite (NaN/Inf), negative, bool, and invalid-key values by dropping them, and still lands on exactly 100 via largest-remainder rounding | ⚠️ unit only | `tests/worker/test_marketing_ai_handlers.py::test_normalize_rejects_nan_and_inf`, `test_normalize_rejects_bool_str_and_negative_values`, `test_normalize_largest_remainder_sums_exactly_100` |
| `POST /channels/fit-notes/generate` → 202 `{id, status: "generating"}`, no request body | ✅ | `fit_notes_accepted.json` |
| `GET /channels/fit-notes/{id}` → ready, `output.notes` has all 8 `ChannelKey`s | ✅ | `fit_notes_ready.json` |
| `GET /channels` surfaces `ai_fit_note` + `fit_note_generated_at` inline per card after a fit-notes run completes | ✅ | `channels_with_fit_notes.json` |
| `fit_note_generated_at` is the same instant across all 8 channels from one run | ✅ (all 8 cards in the full capture share the timestamp) | `channels_with_fit_notes.json` |
| A `ChannelKey` the model's response omits keeps its prior `ai_fit_note` rather than being wiped | ⚠️ unit only | `tests/worker/test_marketing_ai_handlers.py::test_channel_fit_keeps_prior_note_when_key_omitted` |
| An invalid/unknown key in the model's `notes` response is dropped, never written to any channel row | ⚠️ unit only | `tests/worker/test_marketing_ai_handlers.py::test_channel_fit_drops_invalid_key_from_notes` |
| Fit-notes generation lazy-seeds all 8 channels first if none exist yet | ⚠️ unit only | `tests/worker/test_marketing_ai_handlers.py::test_channel_fit_writes_notes_onto_rows_and_seeds` |
| Kind-mismatch (`channel_plan` id via fit-notes route, and vice versa) → 404 | ✅ | `tests/api/test_marketing_ai.py::test_channel_plan_kind_mismatch_404` (unit; not separately e2e-captured) |
| RBAC: 403 for non-marketing roles on all 4 new routes, including both `{id}` GET routes | ⚠️ unit only | `tests/api/test_marketing_ai.py::test_channel_ai_rbac_forbidden` |
| `status: "failed"`, `error: "over_budget"`, `output: {}` on budget exhaustion for both kinds | ⚠️ unit only — not reachable from a normal live journey without seeding the ledger | `tests/worker/test_marketing_ai_handlers.py::test_channel_plan_over_budget_fails`, `test_channel_fit_over_budget_fails` |
| `channel_fit` lazy-seeding runs before the budget check (seeds rows, writes no notes, on an over-budget run) | ⚠️ unit only | `app/worker/handlers/marketing_ai.py::handle_marketing_channel_fit` (comment + design spec D2), covered indirectly by `test_channel_fit_over_budget_fails` |
| Migration `0036_channel_fit_notes` — two nullable columns on `marketing_channels`, single alembic head | ✅ live (migration applied in e2e) | `alembic/versions/0036_channel_fit_notes.py` |
