# Design — Module 05 Roadmap · Slice 3 (AI Re-plan)

> **Status:** Approved (brainstorm) · **Date:** 2026-08-26 · **Repo:** `cofoundaz-api`
> **Sources of truth:** `../Cofoundaz_Technical_PRD.md` (Module 05 §05.6 AI Re-plan), the UI
> handoff `../cofoundaz/app/(dashboard)/roadmap/page.tsx` (AI Re-plan view), and Slices 1–2
> (`docs/superpowers/specs/2026-08-21-roadmap-core-design.md`, `2026-08-22-roadmap-deps-templates-design.md`, merged PRs #7–#8).
>
> Final slice of Module 05. Slices 1 (Core) + 2 (Dependencies + Templates) are merged. This slice
> gives the `roadmap.replan` seam meaning and closes out the module.
>
> **Parallel build note:** built in the `feat/roadmap-replan` worktree, **migration `0008`**.
> Module 04 (Today's Mission) builds concurrently in `feat/todays-mission` (**migration `0009`**,
> read-only against roadmap files) — coordinate the migration numbers on merge.

---

## 1. Scope (Slice 3)

**In scope** — **drift detection** (slipped milestones); a **stateless, deterministic re-plan
proposal** (`preview`); **apply selected changes** with per-milestone markers, a history record,
and an event; a **re-plan history** read; a **drift summary + per-milestone re-plan marker** on the
roadmap tree. **The re-planner never auto-applies** (co-pilot principle) — it proposes; a human
commits.

**Deferred / not built:**

| Deferred | To |
|---|---|
| AI-authored re-plan rationale (natural-language "why") | Module 03 — v1 uses templated reasons |
| Push notification *"I've drafted a re-plan for your review"* | Module 20 (`roadmap.replanned` emitted now, no consumer) |
| Auto-triggered re-plan / a worker draining `roadmap.replan` | never — co-pilot rule; the job stays an unconsumed stub |
| "Ask AI to break this milestone down" (05.3) | Module 03 |

## 2. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | What shifts | **Slip-and-cascade along dependencies.** A slipped milestone moves; downstream milestones (that depend on it) shift with it. |
| 2 | Preview → apply state | **Stateless preview + deterministic apply.** The proposal is a pure function of current roadmap state; nothing is persisted between `preview` and `apply`. `change_id == milestone_id`. `apply` recomputes and commits only still-valid selected changes. |
| 3 | Persistence | **`roadmap_replans` table** (history) **+ two marker columns** on `roadmap_milestones` (chip). |
| 4 | Rationale | **Templated `reason`** v1; AI-authored rationale deferred to Module 03. |
| 5 | `roadmap.replan` job | **Unconsumed stub, untouched.** Re-plan never auto-applies, and the stable assessment-complete path is not modified. |

## 3. Data model (migration `0008_roadmap_replan`)

- **New table `roadmap_replans`** (UUIDMixin + TimestampMixin):
  | Column | Type | Notes |
  |---|---|---|
  | `id` | UUID PK | |
  | `roadmap_id` | UUID FK→roadmaps | `ondelete=CASCADE`, indexed |
  | `applied_by` | UUID FK→users | who committed the re-plan |
  | `change_count` | Integer | number of milestones shifted |
  | `changes` | JSONB | snapshot `[{milestone_id, title, old_due, new_due, reason}]` |
  | `summary` | String | e.g. `"Re-planned 2 milestones"` |
  | (TimestampMixin) | | `created_at` orders the history |
- **Alter `roadmap_milestones`** — add `last_replanned_at` (timestamptz, nullable) and
  `last_replan_reason` (String, nullable). Stamped on apply; power the tree's "↻ Re-planned" chip
  without a join. `null` when the milestone has never been re-planned.

No change to `roadmap_task_dependencies` — the cascade reads the existing dependency DAG.

## 4. The cascade engine — `app/services/roadmap/replan.py`

Pure and deterministic; only `date.today()` depends on the clock. `REPLAN_BUFFER_DAYS = 7` (named
constant in this module).

```python
detect_drift(db, roadmap) -> list[RoadmapMilestone]     # due_on < today AND status != done
compute_replan(db, roadmap) -> list[Change]             # the proposal (pure)
apply_replan(db, roadmap, actor, change_ids) -> dict    # {applied, skipped, replan}
```

`Change = {change_id: uuid, milestone_id: uuid, title: str, old_due: date, new_due: date, reason: str}`
where `change_id == milestone_id` (a milestone has at most one proposed shift).

### 4.1 `compute_replan` — dependency-DAG shift propagation

1. **Base shift** for each *slipped* milestone: target `new = today + REPLAN_BUFFER_DAYS`;
   `base_shift = (new − old_due).days` (only if positive). Non-slipped → `0`.
2. **Milestone precedence graph:** `A → B` when some task in `B` depends on a task in `A`
   (projected from `roadmap_task_dependencies`, milestone-level). Acyclic by construction; a rare
   cross-milestone cycle is detected and that edge skipped (defensive — never loops).
3. **Propagate** in topological order:
   `shift[M] = max(base_shift[M], max(shift[U]) for each upstream U with U → M)`.
   The **max** (not sum) is correct: a milestone waits on the *latest* of its blockers, so a
   diamond (D depends on B and C) shifts by `max(shift[B], shift[C])`, never their sum.
4. **Emit a `Change`** for every `M` with `shift[M] > 0`: `new_due = old_due + shift[M]`.
   - `reason` (templated): milestone is itself slipped → *"{n} days overdue and not yet done."* ·
     shifted only by cascade → *"Shifts {n} days with its dependency '{upstream title}'."*

Milestones with only date shifts are proposed; phases/tasks are not moved in v1 (milestone `due_on`
is the scheduling unit the FE diff shows).

### 4.2 `apply_replan(db, roadmap, actor, change_ids)`

- **Recompute** the proposal from *current* state (never trusts a client-supplied diff).
- Keep only `change_ids` still present in the fresh proposal; a roadmap edited since `preview`
  drops stale ids → they come back as `skipped`.
- For each kept change: `milestone.due_on = new_due`; stamp `last_replanned_at = now`,
  `last_replan_reason = reason`.
- Write **one** `roadmap_replans` row (snapshot `changes` + `summary`).
- Emit `roadmap.replanned`.
- Return `{ applied: [change_id…], skipped: [stale change_id…], replan_id, summary }`.
- Empty or all-stale `change_ids` → `{applied: [], skipped: […]}`, **no history row, no event.**
  Naturally idempotent: re-applying finds those milestones no longer shifting → all `skipped`.

## 5. Endpoints & tree additions

All under `/api/v1/roadmap`, verified. **Preview + history = any active member** (read-only);
**apply = editor** (founder/team_member); mentor may view the proposal, only an editor commits.

| Route | Access | Behaviour |
|---|---|---|
| `POST /roadmap/replan/preview` | member | Stateless compute → `{ drift_count, changes: [{change_id, milestone_id, title, old_due, new_due, reason}] }`. No drift → `changes: []`. |
| `POST /roadmap/replan/apply` `{change_ids:[…]}` | editor | Applies still-valid selected changes; markers + history + `roadmap.replanned`. → `{ applied, skipped, replan_id, summary }`. Empty/all-stale → `applied: []`, no row/event. |
| `GET /roadmap/replan/history` | member | `[{ id, change_count, summary, applied_by:{id,name}, created_at, changes:[…] }]`, newest first. |

**`GET /roadmap` (`serialize_tree`) additions:**
- `roadmap.drift` → `{ "slipped_count": <n> }` (powers the banner *"{n} tasks have slipped…"*).
- each milestone gains `"replanned": { "at": "…", "reason": "…" } | null` (from the marker columns).

`preview` is a `POST` that changes nothing — matches the PRD verb and keeps the co-pilot split
(members *see* the proposal; editors *apply* it), consistent with the rest of roadmap. Cross-tenant
/ unknown ids everywhere → uniform `404`.

## 6. Events, errors, config

### 6.1 Events (via `event_bus.publish`, fire-and-forget)

| Event | When | Payload |
|---|---|---|
| `roadmap.replanned` | an apply commits ≥1 change | `{startup_id, roadmap_id, replan_id, change_count, applied_by}` |

No consumer yet — Module 20. Retires the "deferred `roadmap.replanned`" line from the Slice 2 spec.

### 6.2 Errors (reuse `AppError` taxonomy — no new codes)

| Code | HTTP | When |
|---|---|---|
| `NOT_FOUND` | 404 | no roadmap; cross-tenant/unknown ids |
| `FORBIDDEN` | 403 | mentor (non-editor) hitting `apply` |
| `EMAIL_NOT_VERIFIED` | 403 | unverified user (shared guard) |

**No-drift and all-stale apply are `200` empty-states, not errors.**

### 6.3 Config
`REPLAN_BUFFER_DAYS = 7` — a slipped milestone lands `today + 7d`. One named constant; no migration
to tune it.

## 7. Testing

- **TDD**, real Postgres + per-test rollback; add `create_replan` factory, reuse roadmap factories.
- **Cascade:** single slipped → `today+7`; a downstream dependent shifts by its upstream's delta;
  **diamond shifts by `max`, not sum**; multiple independent slips; no drift → `[]`; non-slipped /
  non-downstream untouched; `reason` templating (slipped vs cascaded wording).
- **Apply:** applies exactly the passed still-valid `change_ids`; a **stale id is skipped**;
  `last_replanned_at`/`reason` stamped; one `roadmap_replans` row with the JSONB snapshot + summary;
  `roadmap.replanned` emitted once; empty/all-stale → no row/event; re-apply idempotent.
- **Tree:** `drift.slipped_count` correct; milestone `replanned` is `null` before, populated after.
- **Tenancy/access:** preview = member OK; apply = editor (mentor `403`); cross-workspace `404`;
  verified gate.
- **Live E2E** (`e2e/test_roadmap_replan.py`): onboard → roadmap generates → force a slip
  (`PATCH` a milestone `due_on` into the past) → `preview` returns `drift_count` + cascade changes
  → `apply` a subset → `GET /roadmap` shows new `due_on` + `replanned` marker + reduced `drift` →
  `history` lists the re-plan. Capture bodies to `e2e/_captures/roadmap/`.
- **FE integration guide** update (`docs/fe-integration-guide-roadmap.md`): preview / apply / history
  payloads + the tree's `drift` + `replanned` additions, from live captures; verification table.

## 8. Plan shape

One implementation plan (`writing-plans`), ~7 TDD tasks, subagent-driven (fresh implementer +
independent review + fix loop per task, then whole-branch review), in the `feat/roadmap-replan`
worktree:

1. Migration `0008_roadmap_replan` (`roadmap_replans` + milestone marker cols) + `RoadmapReplan` model + factory
2. `detect_drift` + `compute_replan` cascade (pure) + `REPLAN_BUFFER_DAYS` + cascade unit tests
3. `apply_replan` (markers + history row + `roadmap.replanned`)
4. `POST /roadmap/replan/preview` + `POST /roadmap/replan/apply`
5. `GET /roadmap/replan/history` + `serialize_tree` `drift` summary + milestone `replanned` marker
6. Live E2E + smoke surface
7. SOP + FE integration guide update + checklist reconcile
