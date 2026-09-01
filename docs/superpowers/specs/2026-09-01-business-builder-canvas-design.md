# Design — Module 08 Business Builder · Slice 1 (Canvas Core)

> **Status:** Approved (brainstorm) · **Date:** 2026-09-01 · **Repo:** `cofoundaz-api`
> **Sources of truth:** `../Cofoundaz_Technical_PRD.md` (Module 08 — Business Builder), the UI
> handoff `../cofoundaz/app/(dashboard)/business-builder/*`, and the existing platform seams
> (`app/platform/jobs.py`, `app/platform/ai.py`).
>
> First slice of Module 08. Built in the `feat/business-builder-canvas` branch, **migration `0011`**
> (head is `0010_dashboard`). The AI features of Module 08 (and Module 03 itself) are deferred until
> an LLM provider is configured; this slice builds everything that does not need one, and puts the
> "Fill with AI" surface behind the existing deferred-**job** seam so its API contract exists now.

---

## 1. Scope

Module 08 is large (9 artifact editors + a suggestion/approve workflow + an AI plan generator). It is
sliced; this is **Slice 1 — Canvas Core**: the five **block-based** artifacts, driven by one generic
table and one block-definition registry, with lazy-get / versioned-save / completion, plus the
deferred `ai-fill` job seam.

**In scope**

- One generic **`business_canvases`** table for the five block-based artifacts:
  Business Model Canvas, Lean Canvas, Value Proposition, Mission & Vision, SWOT.
- A per-type **block-definition registry** (static config) driving scaffolding, validation, and completion.
- `GET /business-builder/overview` (completion grid), `GET`/`PUT /business-builder/canvases/{type}`
  (lazy-get + versioned save with optimistic concurrency), `POST …/{type}/ai-fill` (deferred job).
- `business.artifact.completed` event on the completion transition.

**Deferred**

| Deferred | To |
|---|---|
| Real AI fill (drafting block content) | Module 03 / an AI worker — v1 **enqueues** a `business.canvas.ai_fill` job that sits queued (no consumer yet) |
| Full version **history** (viewable past snapshots) | later — v1 keeps an integer `version` counter + optimistic concurrency, which is what autosave actually needs |
| Personas · Pricing · Revenue · Competitors (typed-row artifacts) | **Slice 2** |
| BC suggest→approve workflow · AI Business Plan generator · export | **Slice 3** |
| Per-section comment threads | Module 23 |

## 2. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | Artifact storage | **One generic `business_canvases` table** (`type`, `blocks` JSONB, `version`) + a per-type block registry, NOT five bespoke tables. The five block-based artifacts are all "a typed set of named blocks," so one abstraction + one editor pattern covers five screens. |
| 2 | Versioning | **Integer `version` counter + optimistic concurrency (409 on stale save)** now; storing viewable snapshots is deferred. This is exactly what the autosave/two-tab case needs, without the storage cost. |
| 3 | AI fill | **Deferred behind the job seam.** `ai-fill` enqueues a `business.canvas.ai_fill` job via `job_dispatcher` and returns it (`202`); a worker/Module 03 drains it later. The API + FE contract exist now; nothing is dishonestly stubbed. |
| 4 | Lazy-create | `GET`/overview never 404 on "no canvas yet" — a canvas is lazily materialised as a full empty scaffold from the registry, so the FE always receives a well-formed shape. |

## 3. Data model — migration `0011_business_canvases`

New enum (`app/db/models/enums.py`): `CanvasType(business_model | lean | value_prop | mission_vision | swot)`.

**`business_canvases`** (UUIDMixin + TimestampMixin):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | `ondelete=CASCADE`, indexed |
| `type` | `CanvasType` enum | |
| `blocks` | JSONB | `{ block_key: value }` — `value` is `str` for `text` blocks, `list[str]` for `list` blocks (see registry) |
| `version` | Integer | `server_default="1"`, NOT NULL; increments by 1 on each successful save |
| (TimestampMixin) | | |
| unique | | `uq_business_canvas_startup_type (startup_id, type)` — one canvas per type per workspace |

No other tables in this slice.

## 4. Block-definition registry — `app/services/business/canvas_defs.py`

Static config (in the spirit of roadmap templates). Each `CanvasType` maps to an **ordered** list of
block definitions:

```python
@dataclass(frozen=True)
class BlockDef:
    key: str        # stable machine key, e.g. "key_partners"
    label: str      # human label, e.g. "Key Partners"
    kind: str       # "text" | "list"

CANVAS_BLOCKS: dict[CanvasType, tuple[BlockDef, ...]] = { ... }
```

- **business_model** — 9 `list` blocks (Key Partners, Key Activities, Key Resources, Value
  Propositions, Customer Relationships, Channels, Customer Segments, Cost Structure, Revenue Streams).
- **lean** — 9 `list` blocks (Problem, Solution, Key Metrics, Unique Value Proposition, Unfair
  Advantage, Channels, Customer Segments, Cost Structure, Revenue Streams).
- **value_prop** — 6 `list` blocks (Customer: Jobs, Pains, Gains · Value: Products & Services, Pain
  Relievers, Gain Creators).
- **swot** — 4 `list` blocks (Strengths, Weaknesses, Opportunities, Threats).
- **mission_vision** — 2 `text` blocks (Mission, Vision).

The registry is the single source of truth for: the empty scaffold (lazy-create), PUT validation
(allowed keys + per-kind type), and completion (which blocks exist / are non-empty). The block
definitions are returned to the FE on `GET /canvases/{type}` so the client need not hardcode them.

## 5. Service — `app/services/business/service.py`

- `get_or_create_canvas(db, startup, type) -> BusinessCanvas` — return the existing row for
  `(startup_id, type)`, or lazily create one whose `blocks` is the empty scaffold from the registry
  (`""` for `text` blocks, `[]` for `list` blocks), `version = 1`. Commits are owned by the endpoint.
- `validate_blocks(type, blocks) -> None` — every key ∈ the type's registry (unknown key → 422);
  every value matches its block's `kind` (a `text` block must be a `str`, a `list` block a
  `list[str]`; wrong kind → 422). Missing keys are allowed (treated as empty) — a partial save is fine.
- `save_canvas(db, canvas, blocks, expected_version) -> BusinessCanvas` — `validate_blocks` first;
  **optimistic concurrency**: if `expected_version != canvas.version` → raise `CanvasVersionConflict`
  (409); else normalise `blocks` to the full block set (fill missing keys with empties), assign,
  `version += 1`. If this save transitions the canvas from not-complete to **complete** (every block
  non-empty), emit `business.artifact.completed`.
- `completion(type, blocks) -> dict` — `{ filled_blocks, total_blocks, completion_pct,
  status: "start" | "continue" | "complete" }`. A block is *filled* when its value is a non-empty
  string / non-empty list. `start` = 0 filled, `complete` = all filled, else `continue`.
- `overview(db, startup) -> list[dict]` — for **each** `CanvasType`, a row
  `{ type, label, ...completion() }`, in registry order. **Read-only: it does NOT create rows** — a
  type with no canvas yet is reported from the empty scaffold (`start`/0%). Only `GET /canvases/{type}`
  (opening an editor) lazily materialises a row.

## 6. Endpoints (`/api/v1/business-builder`)

All verified. **Reads = any active member; writes = editor** (`founder`/`team_member`; mentor 403,
via `require_role`). Cross-tenant / no-workspace → uniform `403`/`404`.

| Route | Access | Behaviour |
|---|---|---|
| `GET /business-builder/overview` | member | The completion grid: `[{ type, label, filled_blocks, total_blocks, completion_pct, status }]` for all 5 types. |
| `GET /business-builder/canvases/{type}` | member | `{ type, version, blocks, block_defs: [{key,label,kind}], completion: {…} }` (lazy-created if absent). Unknown `{type}` → 404. |
| `PUT /business-builder/canvases/{type}` | editor | Body `{ blocks, version }`. Saves; stale `version` → **409 `CANVAS_VERSION_CONFLICT`**; invalid block key/kind → **422 `VALIDATION_ERROR`**. Returns the saved canvas (new `version`). |
| `POST /business-builder/canvases/{type}/ai-fill` | editor | Enqueue a `business.canvas.ai_fill` job via `job_dispatcher.enqueue(db, type="business.canvas.ai_fill", payload={startup_id, canvas_type, section?}, startup_id=…)`; commit; return the queued `Job` (`{ job_id, status: "queued" }`, HTTP `202`). Writes no canvas. FE polls `GET /jobs/{id}`. |

## 7. Errors, events, config

- **Errors (reuse `AppError` taxonomy):** `NOT_FOUND` (unknown `{type}` / cross-tenant),
  `VALIDATION_ERROR` (bad block key/kind, malformed body), `FORBIDDEN` (mentor write),
  `EMAIL_NOT_VERIFIED`. One **domain-specific 409** — `CanvasVersionConflict` (code
  `CANVAS_VERSION_CONFLICT`, http 409) — added as an `AppError` subclass, consistent with the
  codebase's existing per-domain 409s (`DEPENDENCY_CYCLE`, `RECOMMENDATION_RESOLVED`, `ALREADY_MEMBER`).
  The stale-version case is a distinct state the FE handles specially ("this was edited elsewhere —
  reload"), which is why it earns its own code rather than a generic 422.
- **Events (fire-and-forget, Module 20 consumer):** `business.artifact.completed` —
  `{ startup_id, canvas_type }` — emitted once on the transition to all-blocks-filled.
- **Config:** none new. The `ai-fill` job `type` string is `"business.canvas.ai_fill"`.

## 8. Testing

- **TDD**, real Postgres + per-test rollback; add a `create_business_canvas` factory.
- **Registry/scaffold:** every `CanvasType` lazy-creates a full empty scaffold (all keys present,
  correct empty per kind); `block_defs` returned match the registry.
- **Save:** valid partial save persists + bumps `version`; unknown key → 422; wrong kind (`str` for a
  `list` block or vice-versa) → 422; **stale `version` → 409 `CANVAS_VERSION_CONFLICT`**; a save that
  fills the last block flips `status` to `complete` and emits `business.artifact.completed` exactly
  once; a save on an already-complete canvas does **not** re-emit.
- **Completion:** empty → `start`/0%; partial → `continue`; all → `complete`/100%; math per type.
- **ai-fill:** enqueues exactly one `business.canvas.ai_fill` job with `{startup_id, canvas_type}` in
  the payload, returns the job id + `queued`, and writes/updates **no** canvas row.
- **Tenancy/access:** member reads; editor writes; mentor write → 403; cross-workspace → 404; verified
  gate; a canvas never leaks across `startup_id`.
- **Live E2E** (`e2e/test_business_builder.py`): onboard → `GET /overview` (all `start`) → `PUT` a
  business-model canvas → `GET` it back (version 2, blocks persisted) → `GET /overview` (that row now
  `continue`/`complete`) → `POST …/ai-fill` → `GET /jobs/{id}` shows the queued job. Capture bodies to
  `e2e/_captures/business/`.
- **FE integration guide** (`docs/fe-integration-guide-business-builder.md`) from live captures: the
  canvas shape + `block_defs` contract, the **optimistic-concurrency 409 trap** (send the `version`
  you fetched; on 409 reload), and the **ai-fill job/poll** contract (202 → poll `GET /jobs/{id}`;
  note the job stays `queued` until the AI worker ships).

## 9. Plan shape

One implementation plan (`writing-plans`), ~6 TDD tasks, subagent-driven in the
`feat/business-builder-canvas` branch:

1. `CanvasType` enum + `business_canvases` model + migration `0011` + `canvas_defs` registry + `CanvasVersionConflict` error + factory
2. Service: `get_or_create_canvas` + `validate_blocks` + `save_canvas` (versioning/409 + completion event) + `completion` + `overview`
3. `GET /business-builder/overview` + `GET /business-builder/canvases/{type}`
4. `PUT /business-builder/canvases/{type}` (validation + optimistic concurrency)
5. `POST /business-builder/canvases/{type}/ai-fill` (deferred `job_dispatcher` enqueue)
6. Live E2E + smoke surface + SOP + FE integration guide + checklist reconcile
