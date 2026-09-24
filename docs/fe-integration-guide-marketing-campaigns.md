# FE Integration Guide — Marketing Hub: Campaigns + Audience Segments (Module 10, Slice 2)

> **Provenance.** `e2e/test_marketing.py::test_marketing_campaigns_journey` has been run
> (`bash scripts/e2e_run.sh`, 54/54 e2e passed) and the response bodies in §1–§2, §7–§10 below
> are pasted **verbatim** from the captures it wrote to `e2e/_captures/marketing/`
> (`segment_created.json`, `campaign_created.json`, `campaigns_list.json`,
> `campaign_launched.json`, `campaign_paused.json`, `campaign_completed.json`,
> `segment_used_by.json`). Nothing in those sections is hand-written or "tidied."
>
> The live journey does **not** exercise every route this guide documents — segment list/get/
> update/delete, campaign get/delete, and every `422` shape are **not** e2e-captured. Those are
> covered by `tests/api/test_marketing_campaigns.py` (integration tests against a real DB) and
> `tests/services/marketing/test_campaigns_service.py` /
> `tests/services/marketing/test_segments_service.py` (unit) — flagged inline with ⚠️ and again
> in the verification table at the end. An honestly-labelled gap, not a silent guess.

This extends **`docs/fe-integration-guide-marketing-calendar.md`** (Module 10 Slice 1 — Content
Calendar + Channels + Overview). Same base path, same auth model, same envelope. Read that guide
first if you haven't integrated the Marketing Hub yet; this one only covers what Slice 2 adds.

Base path: `/api/v1/marketing`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header, same as every other
tenant-scoped endpoint in this API.

**Access: founder or team_member only.** Every route in this guide is gated by the same
`require_role(MembershipRole.founder, MembershipRole.team_member)` dependency as the Slice 1
calendar/channels routes — one shared dependency instance covers the whole `/marketing` router,
segments and campaigns included. A mentor, accountant, legal advisor, business consultant, or
investor membership gets **403 `FORBIDDEN`** on every route below. There is still no per-module
"Marketing grant" — every founder and team member has full read/write access to every campaign
and segment in the workspace, not just their own.

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see the Errors section).

**Tables:** segments live in `audience_segments`; campaigns live in `campaigns`; the
campaign↔segment many-to-many join lives in `campaign_segments` (unique on
`(campaign_id, segment_id)`).

---

## Enums

```
CampaignObjective = awareness | leads | sales | launch
CampaignStatus    = draft | active | paused | completed
```

`ChannelKey` (used inside `channel_mix`, below) is the same fixed 8-value taxonomy from Slice 1:
`organic_social | paid_social | search | email | content_seo | partnerships | events | referral`.

---

## The `channel_mix` shape — percent, not spend; budget is in cents

```json
{ "email": 60, "search": 40 }
```

`channel_mix` is `{ChannelKey: percent}` — each value is a **0–100 percent**, not a currency
amount, and the percentages are **not required to sum to 100** (server does not enforce that).
`budget` is an integer in **minor units (cents)** — money-safe, the same convention every other
money field in this API uses. **The FE derives per-channel spend**: `spend[channel] = budget *
(pct / 100)`. There is no server-computed spend-per-channel field.

### Field-nesting/type trap: `channel_mix` values round-trip as floats

Send integers (`{"email": 60, "search": 40}`) and every captured response below comes back with
floats (`{"email": 60.0, "search": 40.0}`) — confirmed in every one of `campaign_created.json`,
`campaigns_list.json`, `campaign_launched.json`, `campaign_paused.json`, `campaign_completed.json`
below. This is `CampaignResponse.channel_mix: dict[str, float]` doing its job, not a bug — don't
assume the response type matches whatever numeric type you sent.

---

## 1. `POST /api/v1/marketing/segments` — create a segment

**Request** (from the e2e journey — this exact body produced `segment_created.json` below):

```json
{ "name": "SMB founders", "est_size": 1200, "definition": { "rules": [] } }
```

| Field | Type | Rules |
|---|---|---|
| `name` | string, 1–200 chars | Required. Blank/whitespace-only → 422 |
| `definition` | object | Optional, defaults to `{}`. Opaque rule-set container — **rule execution against real user/customer data is not built in this slice** (see Follow-ups); today it's just stored and returned as-is |
| `est_size` | integer ≥ 0 \| null | Optional. A manually-entered estimate, not computed |
| `persona_id` | UUID \| null | Optional. Must reference a `persona`-kind Business Builder record (Module 08) in **this** workspace — see the 422 below |

**`e2e/_captures/marketing/segment_created.json` — 200:**

```json
{
  "data": {
    "id": "3e13fc2c-7333-46e6-8fb2-6f357a12fe0c",
    "startup_id": "9a9862f5-7446-4761-8a3b-04f5294d245e",
    "name": "SMB founders",
    "definition": { "rules": [] },
    "est_size": 1200,
    "persona_id": null,
    "created_at": "2026-09-24T18:15:44.253023Z",
    "updated_at": "2026-09-24T18:15:44.253023Z"
  },
  "meta": null
}
```

### The `422` you must handle: `persona_id` must be a persona in this workspace

Sending a `persona_id` that either (a) doesn't exist, (b) belongs to another workspace, or
(c) exists but isn't a `persona`-kind `BusinessRecord` (e.g. a `competitor` or `pricing` record)
all produce the **same** rejection — **422 `VALIDATION_ERROR`**,
`field_errors: [{"field": "persona_id", "message": "persona_id must reference a persona in this
workspace."}]`. The service does one tenancy-and-kind-scoped query and treats "wrong kind" and
"not found/not yours" identically — don't build separate copy for each case.
⚠️ Not e2e-captured — verified by
`tests/services/marketing/test_segments_service.py::test_persona_must_be_persona_kind` and
`::test_persona_other_tenant_rejected`.

---

## 2. `GET /api/v1/marketing/segments` — list segments

**`data.segments`**, ordered `created_at` descending (newest first), no pagination — same
unpaginated-list convention as Slice 1's calendar entries. ⚠️ Not e2e-captured (the journey never
calls list-segments); shape is `{"data": {"segments": [<SegmentResponse>, ...]}, "meta": null}`,
each row identical in shape to §1's response. Verified by
`tests/api/test_marketing_campaigns.py::test_segment_and_campaign_crud`.

---

## 3. `GET /api/v1/marketing/segments/{segment_id}` — one segment

Same shape as §1's response. **404 `NOT_FOUND`** if the id doesn't exist or belongs to another
workspace — uniform cross-tenant 404, same convention as Slice 1 (see Errors). ⚠️ Not
e2e-captured; unit-tested via
`tests/services/marketing/test_segments_service.py::test_get_other_tenant_not_found`.

---

## 4. `PATCH /api/v1/marketing/segments/{segment_id}` — edit a segment

Partial update — send only the fields you're changing. Same field set as §1
(`name`/`definition`/`est_size`/`persona_id`), same `persona_id` 422 rule applied to the merged
value. ⚠️ Not e2e-captured; integration-tested via
`tests/api/test_marketing_campaigns.py::test_segment_and_campaign_crud` (which `PATCH`es
`est_size` and asserts the new value comes back).

---

## 5. `DELETE /api/v1/marketing/segments/{segment_id}` — delete a segment

```json
{ "data": { "deleted": true }, "meta": null }
```

**200, not 204** — same convention as every other delete in this API. Deletion is permanent;
confirm destructively in the UI. **Deleting a segment that's still referenced by a campaign does
not fail** — the `campaign_segments` join row is removed by the FK's `ondelete="CASCADE"`, so the
campaign's `segment_ids` silently shrinks by one. There is no "segment is in use, are you sure?"
guard server-side; if you want to warn the founder before deleting, call §6 (used-by) first and
build that confirmation client-side. ⚠️ Not e2e-captured; integration-tested.

---

## 6. `GET /api/v1/marketing/segments/{segment_id}/campaigns` — used-by

Every campaign in the workspace that currently targets this segment (via `campaign_segments`),
ordered `created_at` descending. 404s if the segment itself doesn't exist / isn't yours — same
tenancy check as every other segment route.

`e2e/_captures/marketing/segment_used_by.json` — captured **after** the journey's campaign had
been launched, paused, resumed, and completed (§10), so `status` here is `"completed"`:

```json
{
  "data": {
    "campaigns": [
      {
        "id": "69121419-1a12-44c6-8d7c-9d2c72cd95db",
        "startup_id": "9a9862f5-7446-4761-8a3b-04f5294d245e",
        "name": "Q4 launch push",
        "objective": "launch",
        "budget": 50000,
        "channel_mix": { "email": 60.0, "search": 40.0 },
        "status": "completed",
        "metrics": {},
        "segment_ids": ["3e13fc2c-7333-46e6-8fb2-6f357a12fe0c"],
        "period_start": null,
        "period_end": null,
        "launched_at": "2026-09-24T18:15:44.295871Z",
        "completed_at": "2026-09-24T18:15:44.329776Z",
        "created_at": "2026-09-24T18:15:44.264966Z",
        "updated_at": "2026-09-24T18:15:44.325218Z"
      }
    ]
  },
  "meta": null
}
```

Use this to power a segment's "used in N campaigns" chip and the pre-delete warning mentioned in
§5. Each row is a full `CampaignResponse` — identical shape to §8–§10 below.

---

## 7. `POST /api/v1/marketing/campaigns` — create a campaign

**Request** (from the e2e journey — this exact body produced `campaign_created.json` below):

```json
{
  "name": "Q4 launch push",
  "objective": "launch",
  "budget": 50000,
  "channel_mix": { "email": 60, "search": 40 },
  "segment_ids": ["3e13fc2c-7333-46e6-8fb2-6f357a12fe0c"]
}
```

| Field | Type | Rules |
|---|---|---|
| `name` | string, 1–200 chars | Required. Blank/whitespace-only → 422 |
| `objective` | `CampaignObjective` | Required |
| `budget` | integer ≥ 0 (cents) | Optional, defaults to `0` |
| `channel_mix` | `{ChannelKey: percent}` | Optional, defaults to `{}`. See the shape note above and the 422s below |
| `segment_ids` | `UUID[]` | Optional, defaults to `[]`. Every id must belong to this workspace — see the 422 below |
| `period_start` / `period_end` | ISO date (`YYYY-MM-DD`) \| null | Optional. Not validated against each other (no "end after start" check in this slice) |

Every new campaign starts at `status: "draft"` — there is no way to create a campaign directly
into `active`/`paused`/`completed`; you must `PATCH` the transition afterward (§10).

**`e2e/_captures/marketing/campaign_created.json` — 200:**

```json
{
  "data": {
    "id": "69121419-1a12-44c6-8d7c-9d2c72cd95db",
    "startup_id": "9a9862f5-7446-4761-8a3b-04f5294d245e",
    "name": "Q4 launch push",
    "objective": "launch",
    "budget": 50000,
    "channel_mix": { "email": 60.0, "search": 40.0 },
    "status": "draft",
    "metrics": {},
    "segment_ids": ["3e13fc2c-7333-46e6-8fb2-6f357a12fe0c"],
    "period_start": null,
    "period_end": null,
    "launched_at": null,
    "completed_at": null,
    "created_at": "2026-09-24T18:15:44.264966Z",
    "updated_at": "2026-09-24T18:15:44.264966Z"
  },
  "meta": null
}
```

**`metrics` is always `{}` in this slice — render it as "coming soon," not "no data yet."**
Slice 5 (Performance Analytics) is what starts writing real values into this field; same
shape, same field, no endpoint change when it lands.

`segment_ids` in the response is ordered by the join rows' own creation order (i.e. the order you
sent them in, deduplicated) — not alphabetical, not by segment name.

### The two `422`s on `channel_mix` — unknown channel key / out-of-range percent

Both are **Pydantic-boundary** validation (raised inside a `field_validator`, not the service
layer) — same envelope, but the top-level `message` is the generic
`"Please check the highlighted fields."` rather than a specific sentence:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      { "field": "channel_mix", "message": "Unknown channel: not_a_channel" }
    ]
  }
}
```

- **Unknown channel key** — any key not in the 8-value `ChannelKey` enum →
  `"Unknown channel: {key}"`.
- **Out-of-range percent** — any value outside `0–100` →
  `"channel_mix[{key}] must be between 0 and 100."`.

⚠️ Not e2e-captured (the journey never sends a bad payload); verified by
`tests/api/test_marketing_campaigns.py::test_bad_channel_mix_422` (asserts the 422 over HTTP) and
`tests/services/marketing/test_campaigns_service.py::test_bad_channel_mix_rejected` (asserts the
underlying `pydantic.ValidationError` at the schema level).

### The `422` on `segment_ids`: foreign or unknown segment

Any id in `segment_ids` that doesn't exist, or belongs to another workspace, is rejected —
**422 `VALIDATION_ERROR`**, and here the message **is** specific (this one is a service-layer
check, not a Pydantic one):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Unknown segment(s) for this workspace: ['<uuid>']",
    "field_errors": [
      { "field": "segment_ids", "message": "Unknown segment(s) for this workspace: ['<uuid>']" }
    ]
  }
}
```

Every bad id in the payload is listed in one error, not one-error-per-id. ⚠️ Not e2e-captured;
verified by
`tests/services/marketing/test_campaigns_service.py::test_foreign_segment_id_rejected`.

---

## 8. `GET /api/v1/marketing/campaigns` — list campaigns

**`e2e/_captures/marketing/campaigns_list.json`** — captured right after §7's create, before any
transition, so `status` is still `"draft"`:

```json
{
  "data": {
    "campaigns": [
      {
        "id": "69121419-1a12-44c6-8d7c-9d2c72cd95db",
        "startup_id": "9a9862f5-7446-4761-8a3b-04f5294d245e",
        "name": "Q4 launch push",
        "objective": "launch",
        "budget": 50000,
        "channel_mix": { "email": 60.0, "search": 40.0 },
        "status": "draft",
        "metrics": {},
        "segment_ids": ["3e13fc2c-7333-46e6-8fb2-6f357a12fe0c"],
        "period_start": null,
        "period_end": null,
        "launched_at": null,
        "completed_at": null,
        "created_at": "2026-09-24T18:15:44.264966Z",
        "updated_at": "2026-09-24T18:15:44.264966Z"
      }
    ]
  },
  "meta": null
}
```

Ordered `created_at` descending, no pagination — same convention as §2 and Slice 1's calendar
list.

---

## 9. `GET /api/v1/marketing/campaigns/{campaign_id}` — one campaign

Same shape as §7/§8's rows. **404 `NOT_FOUND`** on missing or cross-tenant id, uniform with every
other resource in this API. ⚠️ Not e2e-captured; integration-tested.

---

## 10. `PATCH /api/v1/marketing/campaigns/{campaign_id}` — edit, or transition status

Partial update — send only what's changing. Any of the create-time fields (`name`, `objective`,
`budget`, `channel_mix`, `segment_ids`, `period_start`, `period_end`) can be edited at any time,
**plus** an optional `status` to drive the lifecycle. `channel_mix`/`segment_ids` validation is
identical to §7's 422s, applied to whatever you send in the `PATCH`.

### The guarded transition table

```
draft    → active     legal — sets launched_at (first time only), fires marketing.campaign.launched
active   → completed  legal — sets completed_at, fires marketing.campaign.completed
active  ↔ paused      legal both directions — no event, no timestamp change
anything else         422 VALIDATION_ERROR, field "status"
```

That's the **entire** legal-transition set — e.g. `draft → paused`, `draft → completed`,
`paused → completed`, and `active → draft` are all illegal. Re-sending the campaign's **current**
status (e.g. `PATCH {"status": "draft"}` on an already-draft campaign) is a **silent no-op** —
200, no error, no event, `launched_at`/`completed_at` untouched — distinct from an illegal
transition to a *different* status, which 422s.

**Sending `status` alongside other field changes in the same `PATCH` is atomic.** If the status
transition is illegal, the **entire request is rejected and nothing is written** — not the status,
not `name`, not `budget`, nothing — so a rejected `PATCH` is always safe to retry with a
corrected body, never a partial write. Verified by
`tests/services/marketing/test_campaigns_service.py::test_illegal_transition_emits_no_event_and_leaves_timestamps`.

**`e2e/_captures/marketing/campaign_launched.json`** — request was `{"status": "active"}`:

```json
{
  "data": {
    "id": "69121419-1a12-44c6-8d7c-9d2c72cd95db",
    "startup_id": "9a9862f5-7446-4761-8a3b-04f5294d245e",
    "name": "Q4 launch push",
    "objective": "launch",
    "budget": 50000,
    "channel_mix": { "email": 60.0, "search": 40.0 },
    "status": "active",
    "metrics": {},
    "segment_ids": ["3e13fc2c-7333-46e6-8fb2-6f357a12fe0c"],
    "period_start": null,
    "period_end": null,
    "launched_at": "2026-09-24T18:15:44.295871Z",
    "completed_at": null,
    "created_at": "2026-09-24T18:15:44.264966Z",
    "updated_at": "2026-09-24T18:15:44.290697Z"
  },
  "meta": null
}
```

**`e2e/_captures/marketing/campaign_paused.json`** — request was `{"status": "paused"}` against
the now-active campaign above (`launched_at` unchanged, no new event):

```json
{
  "data": {
    "id": "69121419-1a12-44c6-8d7c-9d2c72cd95db",
    "startup_id": "9a9862f5-7446-4761-8a3b-04f5294d245e",
    "name": "Q4 launch push",
    "objective": "launch",
    "budget": 50000,
    "channel_mix": { "email": 60.0, "search": 40.0 },
    "status": "paused",
    "metrics": {},
    "segment_ids": ["3e13fc2c-7333-46e6-8fb2-6f357a12fe0c"],
    "period_start": null,
    "period_end": null,
    "launched_at": "2026-09-24T18:15:44.295871Z",
    "completed_at": null,
    "created_at": "2026-09-24T18:15:44.264966Z",
    "updated_at": "2026-09-24T18:15:44.305105Z"
  },
  "meta": null
}
```

The journey then resumes (`PATCH {"status": "active"}`, `paused → active`, legal, no event —
asserted in the test but not separately captured, since only 7 named bodies were captured for
this slice) before completing:

**`e2e/_captures/marketing/campaign_completed.json`** — request was `{"status": "completed"}`:

```json
{
  "data": {
    "id": "69121419-1a12-44c6-8d7c-9d2c72cd95db",
    "startup_id": "9a9862f5-7446-4761-8a3b-04f5294d245e",
    "name": "Q4 launch push",
    "objective": "launch",
    "budget": 50000,
    "channel_mix": { "email": 60.0, "search": 40.0 },
    "status": "completed",
    "metrics": {},
    "segment_ids": ["3e13fc2c-7333-46e6-8fb2-6f357a12fe0c"],
    "period_start": null,
    "period_end": null,
    "launched_at": "2026-09-24T18:15:44.295871Z",
    "completed_at": "2026-09-24T18:15:44.329776Z",
    "created_at": "2026-09-24T18:15:44.264966Z",
    "updated_at": "2026-09-24T18:15:44.325218Z"
  },
  "meta": null
}
```

`completed` is **terminal** — nothing in the transition table accepts `completed` as the "from"
state, so a completed campaign can never be reopened via `PATCH status`. If the founder needs to
resume work, the UI's only honest option today is "duplicate as a new draft" (not a built
endpoint — a manual copy client-side), not "reactivate."

### Launch and completion notifications

Both transitions fire an in-app notification via the existing Module 20 registry pattern, same
`_members_minus_actor` recipient rule as Slice 1's publish notification (every founder/team member
in the workspace **except** whoever made the `PATCH` call):

| Event | Title (exact string) |
|---|---|
| `marketing.campaign.launched` (draft→active) | `"Campaign launched: {name}"` |
| `marketing.campaign.completed` (active→completed) | `"Campaign completed: {name}"` |

No email channel for either event in this slice — in-app only, same as `marketing.post.published`.
Poll/subscribe via the existing notification feed
(`docs/fe-integration-guide-notifications.md`,
`docs/fe-integration-guide-notifications-realtime.md`); filter on
`type: "marketing.campaign.launched"` / `"marketing.campaign.completed"`. ⚠️ Not e2e-captured
against the live notification feed — verified by
`tests/services/notifications/test_marketing_campaign_notification.py`.

---

## 11. `DELETE /api/v1/marketing/campaigns/{campaign_id}` — delete a campaign

```json
{ "data": { "deleted": true }, "meta": null }
```

**200, not 204.** Deletion is permanent and cascades: the campaign's `campaign_segments` join rows
are removed (`ondelete="CASCADE"`), so it silently disappears from every segment's used-by list
(§6). No status restriction — a `draft`, `active`, `paused`, or `completed` campaign can all be
deleted directly; there is no "must be completed/draft to delete" guard. ⚠️ Not e2e-captured;
integration-tested via `tests/api/test_marketing_campaigns.py::test_segment_and_campaign_crud`.

---

## Errors

Standard envelope, same shape as Slice 1:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [ { "field": "channel_mix", "message": "..." } ]
  }
}
```

| Status | Code | When |
|---|---|---|
| 401 | — | Missing or invalid access token |
| 403 | `EMAIL_NOT_VERIFIED` | Signed in, but email not verified |
| 403 | `FORBIDDEN` | Not a founder/team_member of this workspace |
| 404 | `NOT_FOUND` | Segment or campaign does not exist for this workspace (uniform cross-tenant 404, same as Slice 1) |
| 422 | `VALIDATION_ERROR` | Blank `name`; unknown `channel_mix` key; out-of-range `channel_mix` percent; foreign/unknown `segment_ids`; `persona_id` not a persona-kind record in this workspace; illegal campaign status transition |

**Two different 422 shapes share the same code.** A Pydantic-boundary failure (bad `channel_mix`)
carries the generic top-level `message` shown above; a service-layer failure (bad `segment_ids`,
bad `persona_id`, illegal transition) carries a specific top-level `message` that matches its
single `field_errors[0].message`. Don't hardcode UI copy against the top-level `message` for
`channel_mix` errors — read `field_errors` instead, same advice that applies everywhere else in
this API.

---

## Verification table

| Claim | Source |
|---|---|
| Field names, types, enum values | `app/schemas/marketing.py`, `app/db/models/enums.py` |
| Status codes, routes, auth dependency | `app/api/v1/endpoints/marketing.py` (segments/campaigns routes share the Slice 1 `_marketing` dependency) |
| Segment create + used-by response bodies | ✅ live | `segment_created.json`, `segment_used_by.json` |
| Campaign create/list/launch/pause/complete response bodies | ✅ live | `campaign_created.json`, `campaigns_list.json`, `campaign_launched.json`, `campaign_paused.json`, `campaign_completed.json` |
| `channel_mix` ints round-trip as floats | ✅ live — visible in every capture above | `app/schemas/marketing.py::CampaignResponse.channel_mix: dict[str, float]` |
| Segment list/get/update/delete | ⚠️ integration-tested, not e2e-captured | `tests/api/test_marketing_campaigns.py::test_segment_and_campaign_crud` |
| `persona_id` 422 (wrong kind / cross-tenant) | ⚠️ unit only | `tests/services/marketing/test_segments_service.py::test_persona_must_be_persona_kind`, `::test_persona_other_tenant_rejected` |
| Campaign get/delete | ⚠️ integration-tested, not e2e-captured | `tests/api/test_marketing_campaigns.py::test_segment_and_campaign_crud` |
| `channel_mix` 422s (unknown key / out-of-range) | ⚠️ integration + unit, not e2e-captured | `tests/api/test_marketing_campaigns.py::test_bad_channel_mix_422`, `tests/services/marketing/test_campaigns_service.py::test_bad_channel_mix_rejected` |
| `segment_ids` foreign-id 422 | ⚠️ unit only | `tests/services/marketing/test_campaigns_service.py::test_foreign_segment_id_rejected` |
| Guarded transition table (legal/illegal moves, atomicity, no-op re-issue) | ✅ live for the 4 legal transitions exercised in the journey (draft→active→paused→active→completed); illegal-transition 422 + atomicity + no-op re-issue are unit/integration only | `app/services/marketing/campaigns.py::_TRANSITIONS`, `update_campaign`; `tests/services/marketing/test_campaigns_service.py::test_illegal_transition_rejected`, `::test_illegal_transition_emits_no_event_and_leaves_timestamps`, `::test_reissue_current_status_emits_no_event`; `tests/api/test_marketing_campaigns.py::test_illegal_transition_422` |
| Launch/completion notification recipients + exact title strings | ⚠️ unit only | `tests/services/notifications/test_marketing_campaign_notification.py` |
| `metrics` stays `{}` until Slice 5 | ✅ live — every capture above shows `"metrics": {}"` | `app/services/marketing/campaigns.py::serialize_campaign` |
| Access matrix (403 for non-marketing roles, 200 for founder/team_member) | ⚠️ integration only | `tests/api/test_marketing_campaigns.py::test_rbac_non_marketing_role_forbidden` |
| Cross-tenant 404 uniformity | ⚠️ unit only, same pattern as Slice 1 | `tests/services/marketing/test_campaigns_service.py::test_get_other_tenant_not_found`, `tests/services/marketing/test_segments_service.py::test_get_other_tenant_not_found` |
| Table names (`audience_segments`, `campaigns`, `campaign_segments`) | `alembic/versions/0034_campaigns_segments.py` |
