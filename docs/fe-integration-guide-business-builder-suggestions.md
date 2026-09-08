# FE Integration Guide — Business Builder Suggestions & Positioning Map (Module 08, Slice 3)

Covers the third Business Builder surface, layered on top of Slice 1 (Canvas Core) and Slice 2
(Typed Artifacts) — see `docs/fe-integration-guide-business-builder.md` for those. This guide
covers:
- **Suggestions** (§1–§6) — a non-editor teammate (e.g. `business_consultant`) proposes an edit
  to a canvas or a record; a founder/team_member reviews and approves/rejects it.
- **Positioning Map** (§7–§8) — the editable-axes 2×2 competitor map from PRD §08.9, including the
  coordinate-on-competitor trap.

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_business_builder.py` (`test_business_suggestions_journey` for §1–§6,
`test_business_positioning_map_journey` for §7–§8) running against a real server
(`scripts/e2e_run.sh`) — see `e2e/_captures/business/suggestions_*.json` and
`e2e/_captures/business/positioning_*.json`. Nothing here is retyped from the schema, the
service, or memory. IDs, timestamps, and emails are real values from that ephemeral test run
(they differ on every real request; the shapes are exact). **Every payload, status code, and
error body in this guide — including both 409s — was exercised live**, none are derived from
source alone (unlike Slice 1/2's guide, which had some unexercised error rows).

Base path: `/api/v1/business-builder`. Same auth convention as Slices 1–2: Bearer access token +
`X-Workspace-Id` header. **Role rule for suggestions: any active member can create a suggestion
(`POST /suggestions` uses `require_workspace`, not `require_role`) — but only a founder or
team_member can approve/reject (`_editor` = founder, team_member).** A `business_consultant`,
`mentor`, `accountant`, `legal_advisor`, or `investor` can suggest but never resolve their own or
anyone else's suggestion — confirmed live in §5 (`suggestions_approve_forbidden.json`).
Positioning-map reads are open to any active member; `PUT /positioning-map` and the competitor
`POST`/`PUT` used to set coordinates require the same `_editor` role.

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}`.

---

## 1. The suggestion object

Every suggestion endpoint below returns this same shape. Field reference:

| Field | Meaning |
|---|---|
| `id` | suggestion UUID — use in the `approve`/`reject` URLs. |
| `op` | one of `canvas_update`, `record_create`, `record_update`, `record_delete` — see §2 for the `target`/`payload` shape each op expects. |
| `target` | what the suggestion touches. Normalized server-side to `{"canvas_type": ...}` for `canvas_update`, or `{"kind": ..., "record_id": ...}` for `record_update`/`record_delete` (`record_id` filled in from the target you sent, even if you also sent other keys) — `{"kind": ...}` only (no `record_id`) for `record_create`, since there's no record yet. |
| `payload` | the proposed change, as you sent it — `{"blocks": {...}}` for `canvas_update`, `{"data": {...}}` for `record_create`/`record_update`. **Always `null` for `record_delete`** — a delete has nothing to propose beyond "remove this", so the server discards any `payload` you send. |
| `base_version` | the canvas's `version` at the moment the suggestion was created — **only set for `canvas_update`**, `null` for every record op. This is what `approve` checks against the canvas's *current* version to decide 200 vs `409 CANVAS_VERSION_CONFLICT` (see §5). |
| `current` | **the target's state, read LIVE at response time — not a snapshot frozen when the suggestion was created.** See the important caveat in §3: the same suggestion's `current` is different on its create-response vs. its approve-response, because the canvas/record moved in between. `null` for `record_create` (there is no prior state for a record that doesn't exist yet) and for `record_update`/`record_delete` whose target record has since been deleted. |
| `note` | free-text rationale the suggester typed; `null` if omitted. |
| `status` | `pending` → `approved` \| `rejected` (terminal). See §6 for the state machine. |
| `author` | `{id, name, email}` of whoever created the suggestion. `name` is `null` if that user has no profile `full_name` set (true of every e2e test user, which only sets a workspace-level display name, not a profile name — see the live captures). |
| `resolved_by` | `{id, name}` of whoever approved/rejected it, `null` while `pending`. |
| `resolved_at` | ISO-8601 timestamp, `null` while `pending`. |
| `created_at` | ISO-8601 timestamp. |

---

## 2. `POST /api/v1/business-builder/suggestions` — create a suggestion

Any active member (including `business_consultant`) may call this. Body: `{"op": ..., "target":
{...}, "payload": {...}, "note": "..."}`. `note` and `payload` are optional (`payload` is required
in practice for every op except `record_delete`). The `target`/`payload` shape depends on `op`:

| `op` | `target` (request) | `payload` (request) | Create-time validation |
|---|---|---|---|
| `canvas_update` | `{"canvas_type": "business_model"}` | `{"blocks": {...}}` — **same list-vs-text rule as the canvas `PUT`: every business_model/lean/value_prop/swot block is `list[str]`, only `mission_vision`'s two blocks are plain `str`.** | Unknown `canvas_type` → 404. Bad block shape/unknown block key → 422 `VALIDATION_ERROR` (same `validate_blocks` as the canvas `PUT`). |
| `record_create` | `{"kind": "persona"}` | `{"data": {...}}` — full record data, same schema as `POST /{kind}`. | Unknown `kind` → 404. Bad `data` against that kind's Pydantic schema → 422 `VALIDATION_ERROR`. |
| `record_update` | `{"kind": "persona", "record_id": "<uuid>"}` | `{"data": {...}}` — **full-replace, same as `PUT /{kind}/{id}`: omitted fields reset to schema default on approval, not preserved.** | Unknown `kind` → 404. Unknown/cross-tenant `record_id` → 404 (checked **at suggestion-create time**, using the suggester's own membership — so a consultant can't even suggest an edit to a record outside their tenant). Bad `data` → 422. |
| `record_delete` | `{"kind": "persona", "record_id": "<uuid>"}` | any `payload` you send is discarded — always serialized back as `null`. | Same `kind`/`record_id` checks as `record_update`. |

**`canvas_update` request/response** — request was `{"op": "canvas_update", "target":
{"canvas_type": "business_model"}, "payload": {"blocks": {"key_partners": ["Acme Corp"]}}, "note":
"Add Acme as a key partner"}`. Response (`e2e/_captures/business/suggestions_create_canvas_update.json`,
status `201`):

```json
{
  "data": {
    "id": "c5f41a1b-203e-45b2-af10-dfc8e8463047",
    "op": "canvas_update",
    "target": { "canvas_type": "business_model" },
    "payload": { "blocks": { "key_partners": ["Acme Corp"] } },
    "base_version": 1,
    "current": {
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
      "version": 1
    },
    "note": "Add Acme as a key partner",
    "status": "pending",
    "author": {
      "id": "d9277642-ed1c-421a-9dd8-d34249270e6f",
      "name": null,
      "email": "consultant-ef3da62188e1@example.com"
    },
    "resolved_by": null,
    "resolved_at": null,
    "created_at": "2026-09-08T14:03:13.753815+00:00"
  },
  "meta": null
}
```

**`record_create` request/response** — request was `{"op": "record_create", "target": {"kind":
"persona"}, "payload": {"data": {"name": "Suggested Persona"}}, "note": "Add a persona for our
target user"}`. Response (`suggestions_create_record_create.json`, status `201`) — **note
`current` is `null`, and `base_version` is `null`**:

```json
{
  "data": {
    "id": "0652405a-5983-46f8-8608-8760ceaf249c",
    "op": "record_create",
    "target": { "kind": "persona" },
    "payload": { "data": { "name": "Suggested Persona" } },
    "base_version": null,
    "current": null,
    "note": "Add a persona for our target user",
    "status": "pending",
    "author": {
      "id": "d9277642-ed1c-421a-9dd8-d34249270e6f",
      "name": null,
      "email": "consultant-ef3da62188e1@example.com"
    },
    "resolved_by": null,
    "resolved_at": null,
    "created_at": "2026-09-08T14:03:13.857587+00:00"
  },
  "meta": null
}
```

**`record_update` request/response** — request targeted the persona created above (`record_id`
filled in after §2's `record_create` was approved), body `{"op": "record_update", "target":
{"kind": "persona", "record_id": "5d5213c5-..."}, "payload": {"data": {"name": "Suggested Persona
(Updated)", "quote": "I just need this to work."}}, "note": "Flesh out the persona quote"}`.
Response (`suggestions_create_record_update.json`, status `201`) — **note `current` is NOT
`null` here, unlike `record_create`** — it's the record's existing `data`:

```json
{
  "data": {
    "id": "f69a26c8-4d94-4f8a-a192-409da4475c87",
    "op": "record_update",
    "target": { "kind": "persona", "record_id": "5d5213c5-ccfe-4dc4-a81d-b2f576b7b998" },
    "payload": {
      "data": { "name": "Suggested Persona (Updated)", "quote": "I just need this to work." }
    },
    "base_version": null,
    "current": {
      "data": {
        "name": "Suggested Persona",
        "goals": [],
        "quote": "",
        "demographics": "",
        "frustrations": [],
        "watering_holes": []
      }
    },
    "note": "Flesh out the persona quote",
    "status": "pending",
    "author": {
      "id": "d9277642-ed1c-421a-9dd8-d34249270e6f",
      "name": null,
      "email": "consultant-ef3da62188e1@example.com"
    },
    "resolved_by": null,
    "resolved_at": null,
    "created_at": "2026-09-08T14:03:13.889225+00:00"
  },
  "meta": null
}
```

**`record_delete` request/response** — body `{"op": "record_delete", "target": {"kind":
"persona", "record_id": "5d5213c5-..."}, "note": "This persona turned out to be a duplicate"}`
(no `payload` sent at all). Response (`suggestions_create_record_delete.json`, status `201`) —
**note `payload` comes back `null`**, and `current` still shows the record's live data (it hasn't
been deleted yet — only *proposed* for deletion):

```json
{
  "data": {
    "id": "50316459-2fd0-45fa-9d6f-10609a28c6ab",
    "op": "record_delete",
    "target": { "kind": "persona", "record_id": "5d5213c5-ccfe-4dc4-a81d-b2f576b7b998" },
    "payload": null,
    "base_version": null,
    "current": {
      "data": {
        "name": "Suggested Persona (Updated)",
        "goals": [],
        "quote": "I just need this to work.",
        "demographics": "",
        "frustrations": [],
        "watering_holes": []
      }
    },
    "note": "This persona turned out to be a duplicate",
    "status": "pending",
    "author": {
      "id": "d9277642-ed1c-421a-9dd8-d34249270e6f",
      "name": null,
      "email": "consultant-ef3da62188e1@example.com"
    },
    "resolved_by": null,
    "resolved_at": null,
    "created_at": "2026-09-08T14:03:13.923118+00:00"
  },
  "meta": null
}
```

---

## 3. ⚠️ `current` is LIVE, not a snapshot — the diff-contract trap

**Do not treat the `current` you got back from `POST /suggestions` as a frozen "before" state to
diff against forever.** `current` is recomputed from the database **every time the suggestion is
serialized** — on create, on every list/get, and on the approve/reject response itself. If the
canvas or record changes for any OTHER reason between suggestion-creation and your next read of
that suggestion, `current` reflects the NEW state, not the state at creation time.

**Live proof:** the exact same suggestion (`id: c5f41a1b-...`) from §2 shows two different
`current` values across this journey:
- At creation (`suggestions_create_canvas_update.json`): `current.blocks.key_partners == []`,
  `current.version == 1` (the canvas hadn't been touched yet).
- On its approve response (`suggestions_approve_canvas_update.json`, §5 below): `current.blocks.
  key_partners == ["Acme Corp"]`, `current.version == 2` — **because approving is what applied
  the change**, so by the time the response serializes, the canvas has already moved.

**What this means for your UI:** use `current` (from the suggestion you're currently viewing) as
"what does the target look like RIGHT NOW, for a diff view", not as "what did it look like when
this suggestion was filed." If you need the latter, capture the `current` from the very first
read of a `pending` suggestion yourself and hold it client-side — the server does not preserve
it for you.

---

## 4. `GET /api/v1/business-builder/suggestions` — list

Any active member. Optional `?status=pending|approved|rejected` filter; an unrecognized status
value → `404 NOT_FOUND` (same "no such view" convention as other list-filter endpoints in this
API — not a `422`). Ordered newest-first (`created_at desc`). Response is `{"suggestions": [...]}`
— each entry is the same shape as §1.

`e2e/_captures/business/suggestions_list_pending.json` (status `200`, `?status=pending`, one
suggestion present):

```json
{
  "data": {
    "suggestions": [
      {
        "id": "c5f41a1b-203e-45b2-af10-dfc8e8463047",
        "op": "canvas_update",
        "target": { "canvas_type": "business_model" },
        "payload": { "blocks": { "key_partners": ["Acme Corp"] } },
        "base_version": 1,
        "current": {
          "blocks": {
            "channels": [], "key_partners": [], "key_resources": [], "cost_structure": [],
            "key_activities": [], "revenue_streams": [], "customer_segments": [],
            "value_propositions": [], "customer_relationships": []
          },
          "version": 1
        },
        "note": "Add Acme as a key partner",
        "status": "pending",
        "author": {
          "id": "d9277642-ed1c-421a-9dd8-d34249270e6f",
          "name": null,
          "email": "consultant-ef3da62188e1@example.com"
        },
        "resolved_by": null,
        "resolved_at": null,
        "created_at": "2026-09-08T14:03:13.753815+00:00"
      }
    ]
  },
  "meta": null
}
```

---

## 5. `POST /api/v1/business-builder/suggestions/{id}/approve` — approve

**Requires founder or team_member — a `business_consultant` (or mentor/accountant/legal_advisor/
investor) gets `403 FORBIDDEN`.** Applies the suggestion through the SAME write path a direct
`PUT`/`POST`/`DELETE` would use (`save_canvas`, `create_record`, `update_record`, `delete_record`)
— there is no separate "suggestion apply" code path with its own bugs to diverge from the direct
endpoints.

**403 when a non-editor tries to approve** — `suggestions_approve_forbidden.json` (status `403`,
the `business_consultant` author trying to approve their own suggestion):

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

**Successful `canvas_update` approve** — `suggestions_approve_canvas_update.json` (status `200`):
notice `status` is now `"approved"`, `resolved_by`/`resolved_at` are filled in, and — per §3 —
`current` now reflects the POST-approval canvas (`key_partners: ["Acme Corp"]`, `version: 2`):

```json
{
  "data": {
    "id": "c5f41a1b-203e-45b2-af10-dfc8e8463047",
    "op": "canvas_update",
    "target": { "canvas_type": "business_model" },
    "payload": { "blocks": { "key_partners": ["Acme Corp"] } },
    "base_version": 1,
    "current": {
      "blocks": {
        "channels": [],
        "key_partners": ["Acme Corp"],
        "key_resources": [],
        "cost_structure": [],
        "key_activities": [],
        "revenue_streams": [],
        "customer_segments": [],
        "value_propositions": [],
        "customer_relationships": []
      },
      "version": 2
    },
    "note": "Add Acme as a key partner",
    "status": "approved",
    "author": {
      "id": "d9277642-ed1c-421a-9dd8-d34249270e6f",
      "name": null,
      "email": "consultant-ef3da62188e1@example.com"
    },
    "resolved_by": { "id": "b3a9fb75-ce6e-468f-b081-ff420eec3eeb", "name": "Ada Founder" },
    "resolved_at": "2026-09-08T14:03:13.787605+00:00",
    "created_at": "2026-09-08T14:03:13.753815+00:00"
  },
  "meta": null
}
```

**GET the canvas afterward** confirms the approval actually applied — `suggestions_canvas_after_approve.json`
(status `200`): `version: 2`, `key_partners: ["Acme Corp"]`. **Same full-replace warning as
Slice 1/2 applies: every OTHER block reset to empty**, because `save_canvas` rebuilds from
`empty_blocks()` and overlays only the suggested `blocks` — a canvas_update suggestion is not
exempt from that full-replace contract just because it came from a suggestion instead of a direct
`PUT`.

**409 `SUGGESTION_NOT_PENDING` — approving an already-resolved suggestion** —
`suggestions_not_pending.json` (status `409`, approving the same suggestion a second time):

```json
{
  "error": {
    "code": "SUGGESTION_NOT_PENDING",
    "message": "This suggestion has already been resolved.",
    "field_errors": []
  }
}
```

**409 `CANVAS_VERSION_CONFLICT` — the canvas moved since the suggestion was filed** —
`suggestions_version_conflict.json` (status `409`). Reproduced live by: a consultant suggests a
second `canvas_update` (`base_version: 2`); the founder then directly `PUT`s the canvas (bumping
it to `version: 3`) BEFORE resolving that suggestion; approving now fails because `expected_
version` (2, pinned when the suggestion was created) no longer matches the canvas's actual
version (3):

```json
{
  "error": {
    "code": "CANVAS_VERSION_CONFLICT",
    "message": "This canvas was changed elsewhere. Reload and reapply your edits.",
    "field_errors": []
  }
}
```

**On this 409: the suggestion stays `pending`** — the apply-then-persist transaction rolls back
entirely, so nothing partial is committed. Confirmed live: re-listing `?status=pending`
immediately after this 409 still includes the suggestion. **What your UI should do:** show the
founder the conflict, let them re-`GET` the canvas to see what changed, and offer to reject the
stale suggestion (asking the consultant to re-propose against the new state) rather than
retrying the same approve call — retrying will 409 again forever, exactly like the direct-`PUT`
version conflict documented in the Slice 1/2 guide.

**Successful `record_create` / `record_update` / `record_delete` approve** — same 200 shape,
`status: "approved"`; captured live at `suggestions_approve_record_create.json`,
`suggestions_approve_record_update.json`, `suggestions_approve_record_delete.json`. Confirmed
applied via `GET /personas` immediately after each: `suggestions_personas_after_create.json`
(the persona now exists), and `suggestions_personas_after_delete.json` (`"records": []` — the
persona is gone after the delete suggestion was approved).

---

## 6. `POST /api/v1/business-builder/suggestions/{id}/reject` — reject

Same role rule as approve (founder/team_member only). Does **not** touch the target — only flips
`status` to `"rejected"`. `suggestions_reject.json` (status `200`, rejecting the version-conflicted
suggestion from §5 instead of leaving it stuck pending):

```json
{
  "data": {
    "id": "a5db1568-ade5-4bf8-afac-db46861cd8d6",
    "op": "canvas_update",
    "target": { "canvas_type": "business_model" },
    "payload": { "blocks": { "key_activities": ["Customer support"] } },
    "base_version": 2,
    "current": {
      "blocks": {
        "channels": [],
        "key_partners": ["Acme Corp", "Umbrella Corp"],
        "key_resources": [],
        "cost_structure": [],
        "key_activities": [],
        "revenue_streams": [],
        "customer_segments": [],
        "value_propositions": [],
        "customer_relationships": []
      },
      "version": 3
    },
    "note": "Add customer support as a key activity",
    "status": "rejected",
    "author": {
      "id": "d9277642-ed1c-421a-9dd8-d34249270e6f",
      "name": null,
      "email": "consultant-ef3da62188e1@example.com"
    },
    "resolved_by": { "id": "b3a9fb75-ce6e-468f-b081-ff420eec3eeb", "name": "Ada Founder" },
    "resolved_at": "2026-09-08T14:03:13.849518+00:00",
    "created_at": "2026-09-08T14:03:13.812876+00:00"
  },
  "meta": null
}
```

Rejecting a non-pending suggestion also 409s `SUGGESTION_NOT_PENDING` (same as approve) — not
separately re-captured, same error shape as §5.

### State machine

```
        create (any member)              approve (editor)
  ─────────────────────────► pending ─────────────────────────► approved (terminal)
                                │
                                │ reject (editor)
                                ▼
                            rejected (terminal)
```

`approved` and `rejected` are both terminal — a second approve/reject call on either 409s
`SUGGESTION_NOT_PENDING`. There is no "re-open" or "un-approve" endpoint; a founder who wants to
undo an approved `canvas_update` edits the canvas directly (`PUT /canvases/{type}`).

---

## 7. `GET` / `PUT /api/v1/business-builder/positioning-map` — axes + competitor coordinates

`GET` lazily creates the singleton axes row for this startup (default axes, below) on first read
— same lazy-create pattern as Slice 1's canvas `GET`. Any active member can read; `PUT` (axes
only — see §8's trap) requires founder/team_member.

**Default state** — `positioning_get_default.json` (status `200`, freshly-onboarded founder, no
competitors yet):

```json
{
  "data": {
    "axes": {
      "x": { "label": "Price", "low": "Low", "high": "High" },
      "y": { "label": "Quality", "low": "Low", "high": "High" }
    },
    "competitors": []
  },
  "meta": null
}
```

**`PUT` new axes** — request `{"axes": {"x": {"label": "Growth Rate", "low": "Slow", "high":
"Fast"}, "y": {"label": "Retention", "low": "Poor", "high": "Great"}}}`. Response
(`positioning_put_axes.json`, status `200`) — response is `{axes}` only, no `competitors` key on
the `PUT` response (only on `GET`):

```json
{
  "data": {
    "axes": {
      "x": { "low": "Slow", "high": "Fast", "label": "Growth Rate" },
      "y": { "low": "Poor", "high": "Great", "label": "Retention" }
    }
  },
  "meta": null
}
```

Each axis object requires all three of `label`/`low`/`high` as strings — anything else (missing
key, non-string value) → `422 VALIDATION_ERROR` (not exercised live in this journey; derived
from `validate_axes`, `app/services/business/positioning.py:35`).

**`GET` after a competitor has coordinates** — `positioning_get_with_competitor.json` (status
`200`, after §8's competitor create):

```json
{
  "data": {
    "axes": {
      "x": { "low": "Slow", "high": "Fast", "label": "Growth Rate" },
      "y": { "low": "Poor", "high": "Great", "label": "Retention" }
    },
    "competitors": [
      { "id": "a2f84723-7941-49d5-ba9d-d5b468e7b5b6", "name": "BigCo Rival", "x": 0.7, "y": 0.3, "threat_level": "high" }
    ]
  },
  "meta": null
}
```

Each `competitors[]` entry is `{id, name, x, y, threat_level}` — a projection of the underlying
competitor record's `map_x`/`map_y`/`threat_level` fields (renamed `x`/`y` on this endpoint only;
see §8). `x`/`y` are `null` if that competitor was created without coordinates.

---

## 8. ⚠️ The coordinate-on-competitor trap

**Competitor map coordinates (`map_x`/`map_y`) live on the competitor RECORD, not on the
positioning-map row.** `PUT /positioning-map`'s request schema (`PositioningMapSave`) has exactly
one field, `axes` — there is no way to set a competitor's coordinates through this endpoint, ever.
Coordinates are set the same way every other competitor field is: `POST /competitors` at creation,
or `PUT /competitors/{id}` (full-replace, same contract as Slice 2's record `PUT`) afterward.

**`POST /competitors` with coordinates** — request `{"data": {"name": "BigCo Rival",
"positioning": "Enterprise incumbent", "threat_level": "high", "map_x": 0.7, "map_y": 0.3}}`.
Response (`positioning_competitor_create.json`, status `201`) — `map_x`/`map_y` are plain fields
on `data`, same as every other `CompetitorData` field:

```json
{
  "data": {
    "id": "a2f84723-7941-49d5-ba9d-d5b468e7b5b6",
    "kind": "competitor",
    "data": {
      "name": "BigCo Rival",
      "map_x": 0.7,
      "map_y": 0.3,
      "price": "",
      "strengths": [],
      "weaknesses": [],
      "positioning": "Enterprise incumbent",
      "threat_level": "high"
    },
    "position": 0
  },
  "meta": null
}
```

`map_x`/`map_y` are each `0.0`–`1.0` (validated by `CompetitorData`'s `Field(ge=0, le=1)`) — out
of that range → `422 VALIDATION_ERROR` (not exercised live; derived from `record_defs.py:36-37`).
Both default to `null` if omitted — a competitor with no coordinates yet appears in `GET
/positioning-map`'s `competitors[]` with `x: null, y: null` (the FE should skip plotting it, or
plot it in an "unplaced" tray, rather than defaulting to `0, 0`).

**THE TRAP, live:** `PUT /positioning-map` with an extraneous `competitors` key in the body is
accepted (200) but silently ignored — pydantic drops any field `PositioningMapSave` doesn't
declare. Request: `{"axes": {...same axes...}, "competitors": [{"id": "a2f84723-...", "map_x":
0.99, "map_y": 0.99}]}`. Response (`positioning_map_ignores_coords.json`, status `200`) — no
error, no `competitors` echoed back, and critically **no coordinate change applied**:

```json
{
  "data": {
    "axes": {
      "x": { "low": "Slow", "high": "Fast", "label": "Growth Rate" },
      "y": { "low": "Poor", "high": "Great", "label": "Retention" }
    }
  },
  "meta": null
}
```

Confirmed by immediately re-`GET`ting the map — `positioning_map_unchanged_after_trap.json`: the
competitor's `x`/`y` are still `0.7`/`0.3`, NOT `0.99`/`0.99`. **If your FE lets a founder drag a
competitor dot on the map, the drag handler must call `PUT /competitors/{id}`, not `PUT
/positioning-map`** — sending the new coordinates through the map endpoint is a silent no-op, not
an error your FE will see.

**THE FIX, live:** `PUT /competitors/{id}` with the updated coordinates. Request `{"data":
{"name": "BigCo Rival", "positioning": "Enterprise incumbent", "threat_level": "high", "map_x":
0.85, "map_y": 0.15}}` — **note this is a full-replace `PUT`, same as any other record: you must
resend every field, not just `map_x`/`map_y`, or the omitted ones reset to their schema default**.
Response (`positioning_competitor_coords_update.json`, status `200`):

```json
{
  "data": {
    "id": "a2f84723-7941-49d5-ba9d-d5b468e7b5b6",
    "kind": "competitor",
    "data": {
      "name": "BigCo Rival",
      "map_x": 0.85,
      "map_y": 0.15,
      "price": "",
      "strengths": [],
      "weaknesses": [],
      "positioning": "Enterprise incumbent",
      "threat_level": "high"
    },
    "position": 0
  },
  "meta": null
}
```

`GET /positioning-map` afterward reflects the new coordinates —
`positioning_get_after_coord_fix.json` (status `200`, `competitors[0].x == 0.85, .y == 0.15`).

---

## 9. Errors — quick reference

| Code | HTTP | When | Live capture |
|---|---|---|---|
| `FORBIDDEN` | 403 | non-editor (e.g. `business_consultant`) calls approve/reject, or a non-editor calls `PUT /positioning-map`/`POST`\|`PUT /competitors` | `suggestions_approve_forbidden.json` |
| `SUGGESTION_NOT_PENDING` | 409 | approve/reject on a suggestion already `approved`/`rejected` | `suggestions_not_pending.json` |
| `CANVAS_VERSION_CONFLICT` | 409 | approving a `canvas_update` suggestion whose `base_version` no longer matches the canvas's current version | `suggestions_version_conflict.json` |
| `VALIDATION_ERROR` | 422 | bad `payload` shape for the op at suggestion-create time (bad block kind/unknown block, bad record `data`, invalid enum) | not captured live in this journey (happy-path payloads only) — same `validate_blocks`/`validate` as Slices 1–2 |
| `VALIDATION_ERROR` | 422 | bad axes shape on `PUT /positioning-map` (missing `label`/`low`/`high`, or non-string) | not captured live — derived from `validate_axes` |
| `NOT_FOUND` | 404 | unknown `canvas_type`/`kind` in `target`, unknown/cross-tenant `record_id` in `target` (checked at suggestion-create time), or unknown status filter on `GET /suggestions` | not captured live in this journey |

Same `X-Workspace-Id`/auth dependency chain as every other Business Builder route — see the
Slice 1/2 guide §5 for the shared 401/no-workspace behavior.

---

## Verification table

All rows below were exercised **live**, over real HTTP, against a real Postgres-backed server
(`scripts/e2e_run.sh`, `e2e/test_business_builder.py::test_business_suggestions_journey` for
suggestion rows, `::test_business_positioning_map_journey` for positioning-map rows) — not just
unit-tested in-process — and every response body is captured verbatim in the named file.

| Behaviour | Verified live? | Capture |
|---|---|---|
| `POST /suggestions` — `canvas_update`, `base_version` set from current canvas version | ✅ | `suggestions_create_canvas_update.json` |
| `POST /suggestions` — `record_create`, `current` is `null`, `base_version` is `null` | ✅ | `suggestions_create_record_create.json` |
| `POST /suggestions` — `record_update`, `current` is the record's existing `data` (non-null) | ✅ | `suggestions_create_record_update.json` |
| `POST /suggestions` — `record_delete`, `payload` forced to `null` regardless of what was sent | ✅ | `suggestions_create_record_delete.json` |
| Non-editor gets `403 FORBIDDEN` on approve | ✅ | `suggestions_approve_forbidden.json` |
| `GET /suggestions?status=pending` lists a pending suggestion | ✅ | `suggestions_list_pending.json` |
| Approve applies `canvas_update` through `save_canvas`, bumps version, `current` updates live (§3) | ✅ | `suggestions_approve_canvas_update.json`, `suggestions_canvas_after_approve.json` |
| Re-approving an already-resolved suggestion → `409 SUGGESTION_NOT_PENDING` | ✅ | `suggestions_not_pending.json` |
| Approving a `canvas_update` whose canvas moved since → `409 CANVAS_VERSION_CONFLICT`, suggestion stays `pending` | ✅ | `suggestions_version_conflict.json` |
| `POST /suggestions/{id}/reject` — flips to `rejected`, target untouched | ✅ | `suggestions_reject.json` |
| Approve `record_create` — creates the record for real | ✅ | `suggestions_approve_record_create.json`, `suggestions_personas_after_create.json` |
| Approve `record_update` — applies the update for real | ✅ | `suggestions_approve_record_update.json` |
| Approve `record_delete` — deletes the record for real | ✅ | `suggestions_approve_record_delete.json`, `suggestions_personas_after_delete.json` |
| `GET /positioning-map` — default axes, lazy-created, no competitors | ✅ | `positioning_get_default.json` |
| `PUT /positioning-map` — updates axes | ✅ | `positioning_put_axes.json` |
| `POST /competitors` with `map_x`/`map_y` — coordinates stored on the record | ✅ | `positioning_competitor_create.json` |
| `GET /positioning-map` — competitor appears with `x`/`y`/`threat_level` | ✅ | `positioning_get_with_competitor.json` |
| `PUT /positioning-map` with a `competitors` key — silently ignored, no coordinate change | ✅ | `positioning_map_ignores_coords.json`, `positioning_map_unchanged_after_trap.json` |
| `PUT /competitors/{id}` — the correct way to move a competitor's coordinates | ✅ | `positioning_competitor_coords_update.json`, `positioning_get_after_coord_fix.json` |

Every row in this guide is ✅ — this journey was written specifically to exercise both 409s and
the `current`-liveness behavior live, rather than leaving them as source-derived rows the way
Slice 1/2's guide had to for its unexercised error paths.
