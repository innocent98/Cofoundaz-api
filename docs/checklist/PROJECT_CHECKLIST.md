# Cofoundaz API — Master Build Checklist

> **Purpose:** the single, always-current bird's-eye map of the whole backend build — what's
> shipped, what's in progress, what's ahead. Kept as task lists so progress is visible at a
> glance. Complements per-change **specs** (`docs/superpowers/specs/`), **plans**
> (`docs/superpowers/plans/`), and **SOPs** (`docs/sop/`); this file is the index that spans
> them all and never gets archived.
>
> **Source of truth for scope:** `../Cofoundaz_Technical_PRD.md` + the UI handoff in `../cofoundaz/`.
> **Convention:** update on new scope (map it in *before* building), on planning (mirror task
> breakdown), and on shipping (check off + note PR/commit). An item is checked **only when done
> and verified**.
> **Branching (as of 2026-09-03):** new work lands `feature branch → develop → fast-forward
> main`, not `feature branch → main` directly — see `docs/deployment/BRANCHING.md`. Entries
> below dated before this that say "merged to `main`" are historically accurate under the old,
> now-retired model; leave them as written. Only entries from here on should describe the
> `develop → main` path.

_Last reconciled: 2026-09-24 · **Module 10 (Marketing Hub) Slice 2 of 5 shipped, on branch
`feat/module-10-marketing-slice2` (off Slice 1; not yet merged, no PR opened yet):** Campaigns +
Audience Segments — `campaigns`/`audience_segments`/`campaign_segments` tables (migration
`0034_campaigns_segments`), 11 new `/marketing` endpoints (6 segment + 5 campaign routes) behind
the same `require_role(founder, team_member)` dependency Slice 1 built, a guarded
`draft→active→paused/completed` campaign lifecycle (`_TRANSITIONS` table, illegal moves 422,
re-issuing the current status a silent no-op, the whole `PATCH` atomic — a reviewer-caught
partial-write gap was fixed in `79aae4d` during the build), `channel_mix` as `{ChannelKey: percent}`
against a cents `budget` (FE derives spend), and `marketing.campaign.launched`/`.completed`
events wired to in-app notifications via the existing Module 20 registry pattern (no email,
matching Slice 1's precedent). `metrics` stays `{}` until Slice 5; AI content generation, SEO
tools, and performance analytics remain deferred to Slices 3–5. Unit **1472 passed**; live e2e
**54/54 passed** (`e2e/test_marketing.py::test_marketing_campaigns_journey`, 7 new captures
alongside Slice 1's 7). SOP: `docs/sop/2026-09-24-marketing-slice2.md`. FE guide:
`docs/fe-integration-guide-marketing-campaigns.md` (new, cross-references Slice 1's calendar
guide). **Module 10 remains open (Slice 2 of 5 done): 12 modules fully complete, 1 open, 13 not
started — unchanged counts, Module 10 was already counted open since Slice 1.**_

_Previously: 2026-09-24 · **Module 10 (Marketing Hub) Slice 1 of 5 shipped, on branch
`feat/module-10-marketing-slice1` (not yet merged, no PR opened yet):** the Content Calendar +
Channels + Overview CRUD spine — `content_calendar`/`marketing_channels` tables (migration
`0033_marketing_calendar_channels`), 8 `/marketing` endpoints behind
`require_role(founder, team_member)`, a fixed 8-key lazy-seeded Channels board, and a
`marketing.post.published` event wired to an in-app notification via the existing Module 20
registry pattern. AI content generation, campaigns, SEO tools, and performance analytics are
explicitly deferred to Slices 2–5 (Overview's `active_campaigns`/`top_channel_by_conversions`/
`ai_content_ideas` stay `null` until each lands). Unit **1446 passed**; live e2e **53/53 passed**
(`e2e/test_marketing.py`, 7 captures). SOP: `docs/sop/2026-09-24-marketing-slice1.md`. FE guide:
`docs/fe-integration-guide-marketing-calendar.md` (new). **Module 10 moves from not-started to
open (in progress): 12 modules fully complete, 1 open, 13 not started.**_

_Previously: 2026-09-23 · **Two more Module-03-deferred AI consumers shipped, on branch
`feat/module-03-deferred-ai-upgrades` (not yet merged, no PR opened yet):** the Learning
Academy's (Module 17) shelf-level `recommendation_reason` on `GET /learning/recommendations`
(migration `0031_learning_recommendations`, `ai.learning.recommendations` worker) and the
Founder Journal's (Module 21) daily `prompt` on `GET /journal/prompts/today` (migration
`0032_journal_prompts`, `ai.journal.prompt` worker, grounded only in operational signals —
never journal content or mood). Both follow the same lazy-generate-then-upgrade pattern as the
dashboard briefing/mission-reason/health-recommendation/roadmap-rationale/onboarding-panel
upgrades already shipped under Module 03. **Module 03 itself has no remaining open follow-ups**
(unchanged by this pass — these two consumers live in Modules 17/21, not Module 03's own
section); this pass also fixed stale drift on the Module 17/21 checkboxes below (Module 21 had
never been flipped to `[x]` despite being merged and already counted complete in the Snapshot).
**Module 09 (Validation Hub) remains unstarted, still with the junior** — untouched by this
pass. SOP: `docs/sop/2026-09-23-deferred-ai-upgrades.md`. FE guides:
`docs/fe-integration-guide-learning-recommendations.md` (new),
`docs/fe-integration-guide-journal.md` (§1 extended). **Counts unchanged: 12 modules fully
complete, 0 open, 14 not started.**_

_Previously: 2026-09-21 · **Module 03 (AI Co-Founder)'s deferred infra follow-ups reconciled —
2 of 3 shipped, 1 dropped won't-do.** Merged (**PR #95**, commits `40996dd` →
`fa6f0cc`) — a per-workspace daily LLM token budget
(`llm_usage_daily` ledger + migration `0030`, `LLM_DAILY_TOKEN_BUDGET` config — default `15000`,
`≤0` = unlimited kill-switch — enforced via `metered_complete`/`metered_complete_json` across all 8
`ai.py` AI handlers + the business-plan generator, skip-and-keep-templated on over-budget) and a
new workspace-scoped `GET /ai/status` endpoint (today's token usage vs. budget, `over_budget`,
`resets_at`, recent failed AI-enrichment jobs). Of Slice 1's 3 named infra items: **✅ per-workspace
LLM budget/rate-limiting** is done (token-volume budgeting; call-*rate* limiting specifically was
not built — see the SOP Follow-ups) and **✅ the enrichment-status signal** is done (workspace-level
via `/ai/status`; not a per-field/per-record marker — see the SOP Follow-ups). The third,
a non-OpenAI/Anthropic `LLMClient` provider implementation, is **dropped won't-do (OpenAI-only,
2026-09-21)** — a scope decision, not a build. **Module 03 now has no remaining open follow-ups.**
SOP: `docs/sop/2026-09-21-llm-budget-ai-status.md`. FE guide:
`docs/fe-integration-guide-ai-status.md`. **Counts unchanged (Module 03 was already complete): 12
modules fully complete, 0 open, 14 not started.**_

_Previously: 2026-09-21 · **Module 03 (AI Co-Founder) marked COMPLETE** — owner decision. All
seven build slices (PR #72, #77, #81, #83, #87, #90, #92) and all six AI consumers (assessment
narrative, canvas/records `ai_fill`, mission reason + health recommendations, dashboard briefing,
roadmap rationale, onboarding panel), plus the LLM seam itself, are shipped. The three items Slice
1's Deferred list still carries **unbuilt** — a non-OpenAI/Anthropic `LLMClient` provider
implementation, per-workspace LLM budget/rate limiting, and a structured "AI enrichment failed /
still templated" signal for FE/operator — are, by owner decision, **scoped out of Module 03's
completion** and reclassified as non-blocking **deferred follow-ups**: still `- [ ]`, still listed
in the Module 03 section (Slice 1's Deferred bullet) and in the Snapshot below — not built, not
deleted, not checked off. **Counts: 12 modules fully complete, 0 open, 14 not started.**
Checklist-only reconcile — no code, migration, or test changed in this pass._

_Previously: 2026-09-21 · **Module 03 (AI Co-Founder) Onboarding AI Panel** shipped on branch
`feat/onboarding-ai-panel` (7 tasks, migration `0029_startup_profile_ai_panel`), PR to `develop` to
follow — the sixth and last of six Module-03-deferred AI consumers now built, across the seventh
and final Module-03 build slice: completing the
founder's onboarding signals (industry + stage + goals, the last of which to land via `PATCH
/onboarding/state`) now writes a templated `StartupProfile.ai_panel` (nullable Text) **synchronously
in that same request**, then enqueues a new `ai.onboarding.panel` worker job that overwrites it with
LLM prose within seconds; `GET`/`PATCH /onboarding/state` both expose the current value flat under
`data.ai_panel` (no field-nesting asymmetry, unlike the assessment narrative). This is a **one-shot,
generate-once** trigger — once `ai_panel` is non-null it is never regenerated, even if the founder
later edits industry/stage/goals (a real, separate follow-up, not started here). As a same-branch
cleanup, the dead, never-consumed `app/platform/ai.py` (`AIPanel`/`StubAIPanel`) platform seam was
retired — zero consumers existed anywhere in the repo; its wording lives on as the new
`_templated_panel()` helper. Proven live end to end (`e2e/test_onboarding_ai_panel.py`, 2 captures):
sign up → complete the three signals (templated `ai_panel` asserted, NOT `[stub-llm]`) →
in-process worker drain → `GET /onboarding/state` (AI-authored `[stub-llm]` `ai_panel` asserted),
over real HTTP (51 e2e passed total, no regression). **This closes the Module-03 AI-consumer set —
all six named consumers (assessment narrative, canvas/records `ai_fill`, mission reason + health
recommendations, dashboard briefing, roadmap rationale, onboarding panel) are now shipped, across
seven build slices.** At the time of this shipment, Module 03 itself remained open solely for
Slice 1's infra Deferred items. **Reconciled 2026-09-21 (see the banner above): the owner has since
scoped those 3 infra items (non-OpenAI/Anthropic provider implementation, per-workspace LLM
budget/rate limiting, a structured "AI enrichment failed / still templated" signal) out of Module
03's completion as non-blocking deferred follow-ups, and marked Module 03 COMPLETE. Current counts:
12 modules fully complete, 0 open, 14 not started — see Snapshot.** SOP:
`docs/sop/2026-09-21-onboarding-ai-panel.md`; FE guide (new, focused):
`docs/fe-integration-guide-onboarding-ai-panel.md`._

_Previously: 2026-09-21 · **Module 03 (AI Co-Founder) Roadmap Re-plan Rationale** shipped on
branch `feat/roadmap-replan-rationale` (7 tasks, migration `0028_roadmap_replan_rationale`), merged
(PR #90) — the fifth of six Module-03-deferred AI consumers now built: applying a
roadmap re-plan (`POST /replan/apply`) now writes a holistic, AI-authored `rationale` onto the new
`RoadmapReplan.rationale` column (nullable Text) — a **templated fallback written synchronously**
(so `apply`'s response never has to wait on the LLM) that a new `ai.roadmap.rationale` worker job
then overwrites with LLM prose within seconds; `GET /replan/history` exposes the current value on
every row (`null` for any pre-existing row, no backfill). **`apply`'s `rationale` is always the
templated value, never AI-authored** — the FE must re-fetch `GET /replan/history` to see the
upgrade; this nuance is called out explicitly in the FE guide. Uses the free-text `complete()`
seam (Slice 1), not the structured `complete_json` mode (Slice 2) — a holistic rationale is prose,
not typed fields, same choice §08.11's plan generator made. As a same-branch cleanup, the dead
`roadmap.replan` job enqueued at assessment-complete (no handler was ever registered for it) was
also removed; assessment completion still enqueues `ai.assessment.narrative` and
`ai.health.recommendations`. Proven live end to end (`e2e/test_roadmap_replan_rationale.py`, 2
captures): force a slip → preview → apply (templated `rationale` asserted) → in-process worker
drain → `GET /replan/history` (AI-authored `[stub-llm]` `rationale` asserted), over real HTTP (50
e2e passed total, no regression; 1,311 unit passed; single alembic head, no migration drift).
**This does NOT complete Module 03**: only one Module-03-deferred AI consumer remains unbuilt —
**the onboarding AI panel**. **Counts unchanged: still 11 modules fully complete, 1 open (03), 14
not started.** SOP: `docs/sop/2026-09-21-roadmap-replan-rationale.md`; FE guide (extended):
`docs/fe-integration-guide-roadmap.md` (§9)._

_Previously: 2026-09-19 · **Module 03 (AI Co-Founder) Dashboard AI Briefing** shipped on
branch `feat/dashboard-ai-briefing` (5 tasks, migration `0027_daily_briefings`), PR to `develop`
to follow — the third of five Module-03-deferred AI consumers now built: the dashboard's
`briefing`/`risks`/`opportunities` panel (previously permanently static — see Module 02's own
SOP) is now backed by a new `daily_briefings` table, lazily generated on the first
`GET /dashboard/summary` read of the day for a founder who has completed the kickoff assessment
— `status: "generating"` immediately (a placeholder row + enqueued job), `status: "ready"` once
the new `ai.dashboard.briefing` worker job writes AI-authored text via the existing
`complete_json` structured-output method. A founder who hasn't completed the kickoff assessment
still sees the original static `"empty"` shape, byte-for-byte unchanged. Proven live end to end
(`e2e/test_dashboard_ai_briefing.py`, 2 captures): enqueue → in-process worker drain → structured
LLM (stub) call → persisted `ready` row, over real HTTP (49 e2e passed total, no regression) —
this run also fixed a stale assertion in the pre-existing `e2e/test_dashboard.py` journey (a
just-assessed founder's first summary read now shows `briefing.status: "generating"`, not
`"empty"`, immediately). **This does NOT complete Module 03**: only two Module-03-deferred AI
consumers remain unbuilt — **the onboarding AI panel and the roadmap replan rationale**. **Counts
unchanged: still 11 modules fully complete, 1 open (03), 14 not started.** SOP:
`docs/sop/2026-09-19-dashboard-ai-briefing.md`; FE guide (extended):
`docs/fe-integration-guide-dashboard.md`._

_Previously: 2026-09-19 · **Module 03 (AI Co-Founder) Slice 4 (Mission Reason + Health
Recommendation AI Upgrade)** (7 tasks, no migration, merged — PR #83) — two more
Module-03-deferred AI consumers upgraded from templated/catalog text to LLM-authored text, on the
same async-upgrade pattern Slices 1–3 established: **today's mission's per-task `reason` line**
(Module 04) now gets rewritten by a new `ai.mission.reason` job enqueued whenever
`get_or_generate_today` generates a non-empty mission (gated on task count via a small
`_enqueue_mission_reason` helper, so the weekends-off empty mission enqueues nothing), and **each
health-score recommendation's `body`** (Module 06) now gets rewritten by a new
`ai.health.recommendations` job enqueued at the end of every `recompute_health_score` call — made
idempotent (and free of any LLM call at all once nothing is left to do) by a guard that only
touches `pending` rows whose `body` still equals the catalog default. Both jobs re-use Slice 2's
`complete_json` structured-output method; no new LLM capability, no migration (both fields are
pre-existing columns). Proven live end to end (`e2e/test_mission_reason.py`,
`e2e/test_health_recommendations_ai.py`): enqueue → in-process worker drain → structured LLM
(stub) call → persisted overwrite, over real HTTP (48 e2e passed total, no regression). Along the
way, live testing corrected two things the design/plan text had wrong: the mission endpoint is
`/api/v1/missions/today` (plural), and health recommendations actually materialize via
`GET /health-score`'s lazy-on-read fallback, not (reliably) the assessment-complete request itself
— a pre-existing autoflush-timing quirk in `complete_assessment`, now documented inline in both FE
guides and flagged as a follow-up, not fixed by this slice. **This does NOT complete Module 03**:
the dashboard AI briefing, onboarding AI panel, and roadmap replan rationale (the other three
Module-03-deferred AI consumers) remain unbuilt. **Counts unchanged: still 11 modules fully
complete, 1 open (03), 14 not started.** SOP: `docs/sop/2026-09-19-mission-health-ai.md`; FE
guides (extended): `docs/fe-integration-guide-mission.md`,
`docs/fe-integration-guide-health-score.md`._

_Previously: 2026-09-19 · **Module 03 (AI Co-Founder) Slice 3 (Typed Records AI Fill) MERGED
(PR #81)** (3 tasks, no migration) — a real
worker for `business.{kind}.ai_fill` (persona/revenue_stream/competitor/pricing), the job Module 08
Slice 2 has enqueued since it shipped, with no worker ever claiming it — **the last ai_fill job type
left unconsumed** now drafts up to 3 records for a kind that's completely EMPTY, via one
`complete_json` call constrained to a strict per-kind JSON Schema derived from the existing
`RECORD_SCHEMAS` Pydantic registry (`map_x`/`map_y` intentionally omitted from `competitor`), each
drafted record validated (and skipped, not failed, on a bad one) through the same `create_record`
path a manual `POST /{kind}` already uses. Required making `StubLLMClient._stub_value` genuinely
recursive (Slice 2's version only handled one level, enough for canvases' flat shape but not
records' nested `{records: [{...}]}` shape) — existing flat canvas stub behavior is unchanged and
pinned by a new regression test. Proven live end to end (`e2e/test_records_ai_fill.py`): enqueue →
in-process worker drain → structured LLM (stub) call → validated `create_record` write → `GET
/personas` shows a stub-drafted record at `data.records[0].data.name`. This does NOT complete
Module 03 or Module 08 — it closes the last unconsumed ai-fill job, but the mission reason line,
health-score recommendations, dashboard AI briefing, onboarding AI panel, and roadmap replan
rationale (all Module 03 consumers) remain unbuilt, and there's no top-up/regenerate mode for a kind
that already has some records. **Counts unchanged: still 11 modules fully complete, 1 open (03), 14
not started.** SOP: `docs/sop/2026-09-19-records-ai-fill.md`; FE guide (extended):
`docs/fe-integration-guide-ai-canvas-fill.md` (§5)._

_Previously: 2026-09-19 · **§08.11 AI Business Plan Generator MERGED (PR #79) — MODULE 08
(BUSINESS BUILDER) IS NOW FULLY COMPLETE, all 4 slices** (
5 tasks, migration `0026_business_plans`) — the multi-section generator Module 08 Slice 3 flagged as
"the ONLY Module 08 PRD sub-screen not yet shippable" is now real: a `business_plans` entity
(`generating`/`complete`/`failed`) + `POST /business-builder/plan/generate` (editor, 202, enqueues
`business.plan.generate`) + `GET /business-builder/plan` (member, latest plan status +
`document_id`). The worker handler calls Module 03 Slice 1's free-text LLM seam directly
(`get_llm_client().complete(...)`, NOT Slice 2's `complete_json` — a plan section is prose, not a
fixed schema), one call per one of 10 fixed `PLAN_SECTIONS`, each built
from a PII-free context gatherer over the startup's profile/canvases/records/assessment/roadmap),
then stores the assembled `{heading, body}` sections as a Module 18 `Document`
(`kind=business_plan`, `ai_generated=True`) and links `business_plans.document_id`, publishing
`business.plan.generated` (maps to the existing `business` notification category — no new category
needed). Proven live end to end (`e2e/test_business_plan.py`): enqueue → in-process worker drain →
10 sequential LLM (stub) calls → `create_document` → `GET /plan` shows `status: "complete"` +
`document_id` → `GET /documents/{id}` returns all 10 sections with stub bodies. This was the last
open item in Module 08 and the last PRD sub-screen dependency-blocked on Module 03 — **11 modules
now FULLY complete on `develop`-equivalent scope; only 1 remains open (03, itself now only blocked
on the still-unbuilt typed-record `ai_fill` worker and the other pre-Module-03 AI consumers, not on
any Business Builder scope)**. SOP: `docs/sop/2026-09-19-ai-business-plan-generator.md`; FE guide:
`docs/fe-integration-guide-ai-business-plan.md`. Merged to `develop` via PR #79._

_Previously: 2026-09-19 · **Module 03 (AI Co-Founder) Slice 2 (Structured Output + Canvas AI
Fill) is now MERGED** (PR #77, no migration) — a structured
(JSON-Schema-constrained) output mode on the LLM seam (`complete_json`, alongside Slice 1's
free-text `complete`) plus its first real consumer: `business.canvas.ai_fill` — the job Module 08
Slice 1 has enqueued since it shipped, with no worker ever claiming it — now drafts a canvas's
EMPTY blocks via one schema-constrained LLM call (schema derived from the existing `CANVAS_BLOCKS`
registry), never overwriting a block the founder already filled. Proven live end to end
(`e2e/test_canvas_ai_fill.py`): enqueue → in-process worker drain → structured LLM (stub) call →
merged write → `GET /canvases/{type}` shows every previously-empty block filled, `version`
incremented, `completion.status: "complete"`. This keeps Module 03 **open** (the canvas-record
`ai_fill` jobs and Module 08's §08.11 generator itself both remain unbuilt) but **unblocks §08.11 on
the capability** — the structured-output mechanism it needs now exists and is proven against a real
consumer, not just designed. SOP: `docs/sop/2026-09-19-llm-structured-output-canvas-fill.md`; new FE
guide: `docs/fe-integration-guide-ai-canvas-fill.md`.

_Previously: 2026-09-19 · **Module 20 (Notifications) Slice 4 (Real-Time SSE) is now built —
MODULE 20 IS NOW FULLY COMPLETE, all 4 slices — MERGED** (Slice 4 Real-Time SSE = PR #74, no
migration) — a Redis pub/sub backplane (a SQLAlchemy `after_commit` listener publishes every
committed notification, covering every creating call path with zero other call-site changes) plus a
one-time-ticket-authed `GET /notifications/stream` SSE endpoint now deliver notification rows to an
open app the instant they're created, closing the "the FE must poll, there is no push" gap every
prior slice's FE guide called out. Proven live across two real OS processes, not just an in-process
fake (`e2e/test_notifications_realtime.py`): the TEST process commits a notification, which publishes
through Redis to the separate SERVER process's already-open stream. This moved Module 20 from "open,
one slice left" to **fully complete — 10 modules now FULLY complete on `develop`; only 2 remain open
(03, 08)**. SOP: `docs/sop/2026-09-19-notifications-realtime-sse.md`; FE guide:
`docs/fe-integration-guide-notifications-realtime.md` (cross-linked from the Slices 1–3 guide)._

---

## Snapshot

**PRD module tally: 26 total** — **12 modules FULLY complete** (10 on `develop`: 01 Auth+Onboarding · 02 Dashboard · 04 Today's Mission · 05 Roadmap, all 3 slices · 06 Health Score · 07 Assessment · 17 Learning Academy (PR #59) · 18 Documents & Templates, all 4 slices · **20 Notifications, all 4 slices** · 21 Founder Journal; plus **08 Business Builder, all 4 slices — §08.11 AI Business Plan Generator now built on `feat/ai-business-plan-generator`, merged (PR #79)**; plus **03 AI Co-Founder — all 7 slices, 6 AI consumers + the LLM seam; PRs #72/#77/#81/#83/#87/#90/#92 — marked COMPLETE 2026-09-21 by owner decision (assessment narrative, canvas/records `ai_fill`, mission reason + health recommendations, dashboard briefing, roadmap rationale, onboarding panel); of the 3 infra items named since Slice 1, 2 shipped 2026-09-21, merged (PR #95) (per-workspace LLM token budget, workspace-level `GET /ai/status` enrichment-status endpoint) and the third (non-OpenAI/Anthropic `LLMClient` provider implementation) is dropped won't-do (OpenAI-only, 2026-09-21) — Module 03 has no remaining open follow-ups; see the Module 03 section below**) + the Foundation/Tenancy spine + the Resend email backend. **1 open** — **10 Marketing Hub**, Slice 1 (Content Calendar + Channels + Overview CRUD spine, `feat/module-10-marketing-slice1`) + Slice 2 (Campaigns + Audience Segments, `feat/module-10-marketing-slice2`) of 5 shipped 2026-09-24 (neither yet merged); Slices 3–5 (AI Content Assistant, SEO Tools, Performance Analytics) planned but not started — see the Module 10 section below. **13 not started** (09 · 11–16 · 19 · 22–26) — of these, 09 Validation Hub is assigned to the junior (handoff + issue #62) but has no code yet.

| State | Count | Modules |
|---|---|---|
| ✅ Fully complete | 12 modules (+spine) | Foundation/Tenancy spine · Auth+Onboarding (01) · Founder Dashboard (02) · Today's Mission (04) · Roadmap (05, all 3 slices) · Health Score (06) · Assessment (07) · **Business Builder (08, all 4 slices — §08.11 AI Business Plan Generator; `feat/ai-business-plan-generator`, merged (PR #79))** · **AI Co-Founder (03)** — Slice 1 (LLM seam + assessment narrative, PR #72) + Slice 2 (structured output + `business.canvas.ai_fill` worker, PR #77) merged; Slice 3 (`business.{kind}.ai_fill` typed-records worker) merged (PR #81); Slice 4 (`ai.mission.reason` + `ai.health.recommendations` workers) merged (PR #83); Slice 5 (`ai.dashboard.briefing` worker + `daily_briefings` table, migration `0027`) merged (PR #87); Slice 6 (`ai.roadmap.rationale` worker + `roadmap_replans.rationale`, migration `0028`) merged (PR #90); Slice 7 (`ai.onboarding.panel` worker + `startup_profiles.ai_panel`, migration `0029`) merged (PR #92) — SOP `docs/sop/2026-09-21-onboarding-ai-panel.md` (Slice 7), `docs/sop/2026-09-21-roadmap-replan-rationale.md` (Slice 6), `docs/sop/2026-09-19-dashboard-ai-briefing.md` (Slice 5), `docs/sop/2026-09-19-mission-health-ai.md` (Slice 4), `docs/sop/2026-09-19-records-ai-fill.md` (Slice 3), `docs/sop/2026-09-19-llm-structured-output-canvas-fill.md` (Slice 2), `docs/sop/2026-09-19-llm-seam-assessment-narrative.md` (Slice 1). All six named Module-03 AI consumers are shipped, across seven build slices (§08.11 shipped directly on Slice 1's free-text seam) — **complete; of the 3 named infra items, 2 (per-workspace LLM budget + workspace-level `GET /ai/status`) shipped 2026-09-21, merged (PR #95) and the third (non-OpenAI provider impl) is dropped won't-do (OpenAI-only, 2026-09-21) — no remaining open follow-ups in the Module 03 section** · **Learning Academy (17; PR #59)** · **Documents & Templates (18, all 4 slices; PRs #48/#50/#53/#55)** · **Notifications (20, all 4 slices; PRs #58/#60/#71/#74)** · Founder Journal (21; PR #37). Also merged: Resend email backend (PR #54; live+verified on staging). Core spine + Dashboard also on `main` (PR #38). |
| 🟡 Open (started, not finished) | 1 module | **Marketing Hub (10)** — Slice 1 (Content Calendar + Channels + Overview CRUD spine, migration `0033_marketing_calendar_channels`, `feat/module-10-marketing-slice1`) + Slice 2 (Campaigns + Audience Segments, migration `0034_campaigns_segments`, `feat/module-10-marketing-slice2`) of 5 shipped 2026-09-24 (neither yet merged, no PR opened yet); Slices 3–5 (AI Content Assistant, SEO Tools, Performance Analytics) planned, not started. SOP `docs/sop/2026-09-24-marketing-slice1.md` + `docs/sop/2026-09-24-marketing-slice2.md`. FE guide `docs/fe-integration-guide-marketing-calendar.md` + `docs/fe-integration-guide-marketing-campaigns.md`. See the Module 10 section below. |
| ⬜ Not started | 13 modules | Validation Hub (09) · Sales Hub (11) · Finance Hub (12) · Legal & Compliance (13) · Funding Hub (14) · Investor Readiness (15) · Marketplace (16) · Calendar & Milestones (19) · Analytics & Reports (22) · Team Collaboration (23) · Subscription & Billing (24, payment-provider-gated) · Admin Portal (25) · Super Admin Portal (26) |

**Health at a glance:** **132 endpoints** (directly counted from the OpenAPI schema's
path×method operations, `app.openapi()["paths"]` — 110 paths, 132 operations, **up from 108
paths/130 ops** — the two new §08.11 routes, `POST /business-builder/plan/generate` and
`GET /business-builder/plan`) · **1257 unit tests** (real Postgres, up from 1245) + **45 live E2E**
(up from 44) · **97.45% coverage** (floor 95; down fractionally from 97.46% — new lines added
without a 1:1 test-line ratio, still comfortably above floor) · black 26.5.1 / isort 6.1.0 / ruff
0.16.5 / mypy clean (directly re-run this pass — 3 pre-existing unformatted files fixed in this
pass, `app/services/business/plan_context.py` / `tests/services/business/test_plan_generation.py` /
`tests/worker/test_plan_handler.py`, all left unformatted by this slice's own Tasks 1–3, which only
ran `black`/`ruff` against their own touched files; `tests/worker/test_plan_handler.py` also had an
import-order violation fixed by `isort`) · pylint **9.89/10** (directly re-run this pass,
unchanged — no new pylint findings) · bandit clean, 0 findings (directly re-run this pass) · alembic
single head `0026_business_plans` (directly re-run this pass) · radon average complexity
**A (2.36)**, every module MI **A** · hadolint / actionlint / `trivy config` / checkov all exit 0 ·
`pip-audit` clean (1 documented ignore) · zero AI-attribution trailers.

_Note: the radon/hadolint/actionlint/`trivy config`/checkov/pip-audit figures above are carried
forward unchanged from the last full lint/security sweep (not re-run in this pass — this pass's own
CI reproduction covered black/isort/ruff/mypy/pylint/bandit/pytest+coverage/alembic heads/e2e, all
directly re-run and recorded above, per `.superpowers/sdd/2026-09-19-ai-business-plan-generator/
task-5-report.md`). `docker-compose.yml`/`docker-compose.prod.yml` gained no new service in this
pass — `business.plan.generate` runs inside the existing `worker` process (see this slice's SOP's
"Operate" section), so no new `trivy config`/checkov surface was added. Endpoint count and unit/e2e
test counts are freshly re-counted this pass (2026-09-19), not carried over._

---

## ✅ Foundation & Tenancy Spine — *shipped (PR #1)*

- [x] Error framework + exception handlers + standard response envelope
- [x] Typed SQLAlchemy `Base` + model mixins (UUID, timestamps)
- [x] Real Postgres test harness (per-test transaction rollback) + coverage gate
- [x] Tenancy spine models — `users`, `startups`, `startup_profiles`, `memberships`
- [x] RBAC + tenancy resolution — `require_role`, `require_workspace`, `X-Workspace-Id`
- [x] Real `get_current_user` dependency
- [x] Jobs table + `JobDispatcher` seam + `GET /jobs/{id}` endpoint
- [x] Rate limiting
- [x] Audit log + `write_audit` helper
- [x] Initial Alembic migration `0001_initial_schema`
- [x] Platform seams: `event_bus`, `job_dispatcher`, `EmailSender`, `Storage`, `AIPanel`

## ✅ Module 01 — Auth — *shipped (PR #2)*

- [x] Signup / verify email / resend verification
- [x] Login with lockout + MFA gate
- [x] Refresh-token session service (rotation + reuse-detection → family revocation)
- [x] Refresh / logout endpoints (dual-transport JWT: httpOnly cookie + Bearer)
- [x] Forgot / reset password
- [x] `GET /auth/me` identity endpoint
- [x] MFA service + TOTP setup/challenge endpoints
- [x] OAuth (Google/Apple) + SMS MFA — `501 FEATURE_NOT_ENABLED` seams
- [x] Auth tables migration `0002_auth_tables`
- [x] Live E2E auth verification
- [ ] _Deferred:_ OAuth real providers · SMS MFA real delivery (seams in place)

## ✅ Module 01.6 — Onboarding — *shipped (PR #3)*

- [x] `InvitationStatus` enum + `invitations` model + `assessment_pending` column
- [x] Migration `0003_onboarding`
- [x] Email-verified dependency + draft-workspace resolver (lazy-create)
- [x] `GET /onboarding/state`
- [x] `PATCH /onboarding/state` (per-step autosave + validation, steps 1–4)
- [x] `POST /onboarding/logo` (Storage seam, ≤2 MB, type-checked)
- [x] `POST /onboarding/invites` (founder-only, dedupe, invite email)
- [x] `POST /onboarding/complete` (gate + `roadmap.generate`/`healthscore.initialize` jobs + idempotent)
- [x] `GET /invitations/{token}` (public preview)
- [x] `POST /invitations/accept` (email-bound)
- [x] Live E2E onboarding journey + SOP
- [ ] _Deferred:_ ~~onboarding AI panel (Module 03)~~ now shipped via **Module 03 Slice 7**
      (`ai.onboarding.panel`, 2026-09-21 — templated instant value + AI-upgrade-second, one-shot on
      signals-complete — see that module's own section) · real notification delivery (Module 20)

## ✅ Module 07 — Startup Assessment — *shipped (PR #4)*

- [x] Question bank v1 (versioned, 11 questions × 5 dimensions)
- [x] Adaptive engine — `show_if` skip-logic grammar, server-driven next-question, per-type validation
- [x] Deterministic per-dimension 0–100 scoring (clamp + neutral-50) + templated narrative
- [x] Data model — `assessments` / `assessment_answers` / `assessment_results` + migration `0004`
- [x] `POST /assessments` (start/resume, savepoint+IntegrityError→resume)
- [x] `GET /assessments/{id}/next-question`
- [x] `POST /assessments/{id}/answers` (autosave, `ON CONFLICT` upsert)
- [x] `POST /assessments/{id}/complete` (atomic claim → score → side-effects)
- [x] `GET /assessments` · `GET /assessments/{id}` · `GET /assessments/compare`
- [x] Race-safety on all 3 write paths + concurrency tests
- [x] Live E2E adaptive journey + SOP
- [ ] _Deferred:_ AI-generated narrative (Modules 03/06) · ~~quarterly re-assessment cron~~ now
      fires via **Module 20 Slice 3** (`assessment.quarterly.due`, ≥ `QUARTERLY_REASSESS_DAYS`
      since the last completed assessment — see that module's own section)

## ✅ E2E Full-Coverage Pass — *shipped (PR #5)*

- [x] Close E2E gaps: logo upload · invite preview · assessment compare
- [x] Smoke openapi assertion covers all 34 endpoints

---

## ✅ Module 06 — Health Score — *shipped (PR #6, merged `0f8523c`)*

_Explainable 0–100 score · 5 dimension sub-scores · trend history · benchmarks · ranked recommendations._

**Design (brainstorming):**
- [x] Resolve computation/recompute model → **A: inline recompute + lazy-on-read, no worker**
- [x] Reconcile dimension naming → **keep internal keys `product/market/money/legal/team` (reuse `Dimension` enum); "Financial" is `money`'s display label only**
- [x] Signal-sourcing scope → **C: build `health_signals` now (assessment-derived), weights as static versioned config, defer `health_dimensions` table**
- [x] Pre-assessment state → **A: honest pending empty-state (no row until first assessment; `healthscore.initialize` still enqueued at onboarding-complete but stays unconsumed by design)**
- [x] Recommendations engine scope → **A: rule-based + persisted (`health_recommendations`, dedupe, never resurrect dismissed)**
- [x] Benchmarks scope → **A: honest empty-state with cohort-size gate (no aggregation pipeline built now)**
- [x] Band thresholds + weekly-delta / drop / record semantics — implemented in `app/services/health_score/config.py` + `scoring.py`
- [x] `healthscore.recalculate` stub replaced by the inline recompute at assessment-complete; `healthscore.initialize` (onboarding) intentionally left as an unconsumed stub — see `docs/sop/2026-08-19-health-score.md`
- [x] Spec written → self-review → user review gate → writing-plans (`.superpowers/sdd/2026-08-19-health-score/`)
- [x] Endpoints: `GET /health-score` · `/dimensions/{dim}` · `/history?range` · `/benchmarks` · `/recommendations` · `POST /recommendations/{id}/{accept,dismiss}`
- [x] Data model: `health_scores` · `health_score_history` · `health_signals` + `health_recommendations` + migration `0005`
- [x] Events: `healthscore.updated` · `healthscore.dropped` · `healthscore.record`
- [x] Live E2E + SOP + **FE integration guide (captured live)** — `e2e/test_health_score.py`, `docs/sop/2026-08-19-health-score.md`, `docs/fe-integration-guide-health-score.md`
- [ ] _Deferred:_ real benchmark cohort aggregation · async worker for the unconsumed stub jobs (Module 05) · ~~AI-generated recommendation bodies~~ now shipped via **Module 03 Slice 4**
      (`ai.health.recommendations`, 2026-09-19 — see that module's own section) — see SOP Follow-ups

---

## ✅ Module 05 — Roadmap — *complete, all 3 slices merged to `main` (Slice 3 via PR #16)*

_Decomposed in brainstorming: each slice = its own spec → plan → build → PR._

**Slice 1 — Core** — *✅ merged (PR #7, `5fef521`)*
- [x] Scope split + locked decisions (stage-only catalog · role-based access · generate honors job contract inline · derived progress/explicit status · inline+lazy)
- [x] Enums (`RoadmapStatus`, `TaskEffort`) + 5 models (`roadmaps`/`roadmap_phases`/`roadmap_milestones`/`roadmap_tasks`/`roadmap_task_dependencies`) + factories
- [x] Migration `0006_roadmap`
- [x] `require_roles(founder, team_member)` editor dep + tenancy resolvers
- [x] Template catalog + `generate_roadmap` (create-once `ON CONFLICT` + race test) + `roadmap.generated`
- [x] `recompute_milestone_progress` + `GET /roadmap` (tree serialize + lazy generate)
- [x] `POST /roadmap/generate` (202 job) + wire inline into `complete_onboarding` (retire stub)
- [x] Phases CRUD · Milestones CRUD (+ mark-complete transition event) · Tasks CRUD (+ progress recompute)
- [x] Live E2E + SOP + FE integration guide (captured live) — `e2e/test_roadmap.py`, `docs/sop/2026-08-21-roadmap-core.md`, `docs/fe-integration-guide-roadmap.md`
- [ ] _Deferred:_ ~~`roadmap.milestone.overdue` event + notifications~~ now fires via **Module 20
      Slice 3** (once per milestone, ever — no recurring re-nudge; see that module's own section) ·
      workspace-tz base date still deferred (Slice 3's scheduler uses one global `SCHEDULER_TIMEZONE`, not per-workspace)

**Slice 2 — Dependencies + Templates** — *✅ done, merged to `main`*
- [x] Scope + locked decisions (separate gallery catalog · apply = append + dedup by template id · write-time DFS cycle detection · duplicate-edge idempotent 200 · dedicated graph endpoint)
- [x] `GALLERY_TEMPLATES` static catalog (named, industry-tagged packs) + counts helper
- [x] Migration `0007_roadmap_applied_templates` (`applied_template_keys` JSONB on `roadmaps`)
- [x] `dependencies.py` — `would_create_cycle` (DFS reachability) + `add_dependency`
- [x] `POST`/`DELETE /roadmap/tasks/{id}/dependencies` (cycle → 409 `DEPENDENCY_CYCLE`, dup → idempotent 200)
- [x] `GET /roadmap/dependencies` graph + populate tree `depends_on`/`dependency_count`
- [x] `GET /roadmap/templates` gallery + `GET /roadmap/templates/{id}` preview
- [x] `POST /roadmap/templates/{id}/apply` (append + dedup + `roadmap.template.applied`)
- [x] Live E2E extension + SOP + FE integration guide update — `e2e/test_roadmap.py` (extended), `docs/sop/2026-08-22-roadmap-deps-templates.md`, `docs/fe-integration-guide-roadmap.md` (updated)
- [ ] _Deferred:_ `add_dependency`/`apply_template` no-row-lock race under concurrent double-calls (benign, same class as Slice 1's `_next_order`) · `roadmap.template.applied` has no consumer until Module 20

**Slice 3 — AI Re-plan** — *✅ done, merged to `main` (PR #16, Tasks 1–7)* — 2026-08-27
- [x] Scope + locked decisions (slip-and-cascade along dependency DAG · stateless preview + deterministic apply · `roadmap_replans` history table + milestone marker columns · templated reason v1 · `roadmap.replan` job stays an unconsumed stub)
- [x] Migration `0008_roadmap_replan` (`roadmap_replans` table + `last_replanned_at`/`last_replan_reason` on `roadmap_milestones`) + `RoadmapReplan` model
- [x] `detect_drift` + `compute_replan` cascade engine (`REPLAN_BUFFER_DAYS = 7`, milestone-precedence DAG, max-not-sum shift propagation, templated reason)
- [x] `apply_replan` (recompute-on-apply, stale `change_id` → skipped, markers + one history row + `roadmap.replanned` event)
- [x] `POST /roadmap/replan/preview` (member) + `POST /roadmap/replan/apply {change_ids[]}` (editor)
- [x] `GET /roadmap/replan/history` + tree `roadmap.drift.slipped_count` + per-milestone `replanned` marker
- [x] Live E2E (26 passed) + smoke surface — `e2e/test_roadmap_replan.py`
- [x] SOP + FE integration guide update + checklist reconcile — `docs/sop/2026-08-26-roadmap-replan.md`, `docs/fe-integration-guide-roadmap.md` (updated)
- [ ] _Deferred:_ ~~AI-authored rationale (Module 03)~~ now shipped via **Module 03 Slice 6**
      (`ai.roadmap.rationale`, 2026-09-21 — a holistic `rationale` field, not an upgrade of the
      per-change `reason` strings below, which remain templated — see that module's own section) ·
      notification on `roadmap.replanned` (Module 20) · `roadmap.replan` job's enqueue **removed**
      2026-09-21 (dead code — no handler was ever registered for it; see Module 03 Slice 6's SOP)
      · phase/task dates not shifted in v1 · `_milestone_precedence` unscoped query · `change_ids`
      not deduped on apply — see SOP Follow-ups

---

## ✅ Module 04 — Today's Mission — *shipped, merged to `main` (PR #17)*

_A daily 1–3 task mission generated lazily-on-read from the founder's roadmap · complete/snooze/reorder/reject · custom tasks · derived streak · history + weekly % · settings. Read-only against the roadmap; migration `0009`._

**Design (brainstorming) — locked decisions:**
- [x] Generation → **inline + lazy-on-read** (`GET /missions/today` generates today's mission if none exists; no cron/worker — 06:00 cron + push deferred to Module 20)
- [x] Roadmap link → **soft, unconstrained** `mission_tasks.roadmap_task_id` (nullable UUID, **no FK**) — mission is a snapshot, decoupled from roadmap tables
- [x] Streak → **derived, not stored** (consecutive completed days ending today/yesterday)
- [x] Reason line → **templated** v1 (`"From your '{milestone}' milestone."`); AI-authored
      rewrite now shipped via **Module 03 Slice 4** (`ai.mission.reason`, 2026-09-19 — templated
      text stays the permanent fallback — see that module's own section)
- [x] Spec → self-review → plan (7 TDD tasks) — `docs/superpowers/specs/2026-08-26-todays-mission-design.md`, `docs/superpowers/plans/2026-08-26-todays-mission.md`

**Build (subagent-driven, Tasks 1–7):**
- [x] Enums (`MissionStatus`, `MissionTaskStatus`) + 3 models (`missions`/`mission_tasks`/`mission_settings`) + factories
- [x] Migration `0009_mission` (sibling of `0008_roadmap_replan` off `0007`; merge revision reconciles later)
- [x] Generation service — `get_or_generate_today` (read-only roadmap selection + carry-forward snoozed + templated reasons) + derived `streak`
- [x] `GET /missions/today` (lazy-gen + `no_roadmap` empty-state + weekends-off empty mission + streak)
- [x] `GET`/`PATCH /missions/settings` (defaults lazily created · `mission_size` clamp 1–3 → 422)
- [x] `POST /missions/tasks` (custom task, appended) + `PATCH /missions/tasks/{id}` (complete/snooze/reorder/reject)
- [x] Events: `mission.task.completed` · `mission.completed` · `mission.streak.milestone` (7/30/100)
- [x] `GET /missions/history` (per-day completed/total + rolling weekly completion %)
- [x] Access: reads = any member (mentor incl.) · writes = founder/team_member (mentor → 403) · cross-workspace → uniform 404
- [x] Live E2E (`e2e/test_mission.py`, 6 captures) + smoke openapi surface (5 mission paths)
- [x] SOP + FE integration guide (captured live) + this checklist reconcile — `docs/sop/2026-08-26-todays-mission.md`, `docs/fe-integration-guide-mission.md`
- [ ] _Deferred:_ ~~06:00 cron generation~~ now fires via **Module 20 Slice 3** (`mission.ready`,
      pre-generates + notifies past `MISSION_GEN_HOUR` local — see that module's own section); push
      notification still deferred to Module 20 Slice 4 · ~~AI-authored reason line~~ now shipped
      via **Module 03 Slice 4** (`ai.mission.reason`, 2026-09-19 — see that module's own section) ·
      real `mission.*` event delivery (Module 20) · workspace-timezone base date

## ✅ Module 02 — Founder Dashboard — *merged to `main` (PR #38) + `develop`*

_The founder's home screen: `GET /dashboard/summary` (9-section aggregation of Modules 04/05/06/07)
and `GET /dashboard/activity` (keyset-paginated team feed). In-process aggregation BFF, no new
domain logic — plus one new durable primitive, `activity_log` + `write_activity()` (mirrors
`write_audit()`), wired at 8 existing action sites. Migration `0010_dashboard`. SOP:
`docs/sop/2026-08-31-dashboard.md`._

**Build (subagent-driven, Tasks 1–7):**
- [x] `ActivityLog` model + migration `0010_dashboard` (chains off `0009_mission`, sole alembic
      head) + `write_activity()` helper (`app/platform/activity.py`) + `create_activity` factory
- [x] 8 `write_activity` call sites wired into existing endpoints — `mission.task.added` ·
      `mission.task.completed` (idempotency-guarded) · `mission.task.snoozed` ·
      `mission.task.rejected` · `roadmap.milestone.completed` · `roadmap.replanned` ·
      `member.joined` · `assessment.completed` (gated on `complete_assessment`'s new `claimed`
      return value, not on the call merely succeeding, so a retried completion can't duplicate
      the feed row)
- [x] Aggregation service — `get_summary(db, startup, user)` composing Health Score / Mission /
      Roadmap / Assessment reads, with **per-section resilience**
      (`{"error": true}` marker instead of a 500 if one section's read throws)
- [x] `GET /dashboard/summary` — 9 sections: `greeting`, `health`, `mission`, `upcoming`
      (7-day roadmap-milestone window, `UPCOMING_WINDOW_DAYS`), `kpis`
      (`tasks_done_this_week` live; `revenue`/`runway`/`pipeline_value`/`campaign_performance`
      honestly `null`), `calibration`, `briefing`/`risks`/`opportunities` (honest static
      empty-states at launch — **upgraded to a live AI-generated `generating`/`ready` state
      2026-09-19, see below**)
- [x] `GET /dashboard/activity` — keyset pagination on `(created_at, id)`, opaque base64 cursor,
      `limit` clamped 1–50, `actor: {id, name} | null` (outer-joined, no N+1), malformed cursor →
      `422 VALIDATION_ERROR`
- [x] Access: both routes = any active member (founder/team_member/mentor); no writes in this
      module
- [x] Live E2E journey (`e2e/test_dashboard.py`, 2 captures) + smoke openapi surface
      (`/dashboard/summary`, `/dashboard/activity`)
- [x] SOP + FE integration guide (captured live) + this checklist reconcile —
      `docs/sop/2026-08-31-dashboard.md`, `docs/fe-integration-guide-dashboard.md`
- [x] AI briefing/risks/opportunities → **shipped 2026-09-19 as Module 03's dashboard AI
      briefing** (new `daily_briefings` table, lazy-on-read generation gated on assessment-
      complete, `ai.dashboard.briefing` worker) — see Module 03, below, and
      `docs/sop/2026-09-19-dashboard-ai-briefing.md`
- [ ] _Deferred:_ financial KPIs (`revenue`/`runway`/`pipeline_value`/`campaign_performance`) →
      Modules 09–11 · realtime activity delivery (websocket/push) → Module 20 · widget-level
      role/grant filtering · `kpi_snapshots` table deliberately not built (nothing to persist
      yet — `daily_briefings` now exists, see above) · `_section`'s swallowed exceptions have no
      Sentry capture · no dedicated `app/schemas/dashboard.py` (plain-dict responses) ·
      `write_activity` call sites are manual, not event-bus-driven · workspace-timezone base
      date — see SOP Follow-ups

## ✅ Module 03 — AI Co-Founder — *Slice 1 (LLM seam + assessment narrative, PR #72) + Slice 2
(structured output + canvas ai-fill worker, PR #77) MERGED to `develop`; Slice 3 (typed records
ai-fill worker) merged (PR #81); Slice 4 (mission reason + health recommendation AI upgrade)
merged (PR #83); Slice 5 (dashboard AI briefing) merged (PR #87); Slice 6 (roadmap re-plan
rationale) merged (PR #90); Slice 7 (onboarding AI panel) merged (PR #92) — no migration on
Slices 2–4, migration `0027_daily_briefings` on Slice 5, migration
`0028_roadmap_replan_rationale` on Slice 6, migration `0029_startup_profile_ai_panel` on Slice 7.
The last unconsumed `ai_fill` job type is closed (Slice 3); the mission reason line and
health-score recommendations are now AI-upgraded (Slice 4, 2026-09-19); the dashboard AI briefing
is now live (Slice 5, 2026-09-19); the roadmap re-plan rationale is now live (Slice 6,
2026-09-21); the **onboarding AI panel is now live (Slice 7, 2026-09-21) — all six named
Module-03 AI consumers are shipped, across seven build slices.** **MODULE 03 COMPLETE** (owner
decision, 2026-09-21) — not because every item underneath it is built, but because the 3 infra
items Slice 1's Deferred list still carries (non-OpenAI/Anthropic `LLMClient` provider
implementation, per-workspace LLM budget/rate limiting, and a structured "AI enrichment failed /
still templated" signal) are scoped out of Module 03's completion as non-blocking deferred
follow-ups. **Update 2026-09-21 (`feat/llm-budget-ai-status`):** 2 of these 3 are now shipped — a
per-workspace daily LLM token budget and a workspace-level `GET /ai/status` endpoint — and the
third (non-OpenAI provider) is dropped won't-do (OpenAI-only). Module 03 has no remaining open
follow-ups; see Slice 1's Deferred bullet, updated below, and SOP
`docs/sop/2026-09-21-llm-budget-ai-status.md`*

_Slice 1: a provider-agnostic LLM seam (`app/platform/llm.py` — `LLMClient` Protocol,
`OpenAILLMClient` fail-loud, `StubLLMClient` for tests/e2e, `get_llm_client()` factory switched on
`LLM_PROVIDER`, mirroring the existing email seam's shape) plus ONE real consumer wired onto an
existing flow: assessment completion (Module 07) now enqueues an `ai.assessment.narrative` job that
asynchronously upgrades `AssessmentResult.narrative` from a templated string to an AI-generated one,
with the templated version as the permanent fallback if the AI job ever fails. No new API surface,
no new route, no migration. SOP: `docs/sop/2026-09-19-llm-seam-assessment-narrative.md`. FE guide:
`docs/fe-integration-guide-ai-assessment-narrative.md`._

**Slice 1 — LLM Seam + Assessment Narrative** — *✅ built (4 tasks, this branch)*
- [x] Design + implementation plan (`.superpowers/sdd/2026-09-19-llm-seam-assessment-narrative/`) —
      seam mirrors the email seam's shape; async job not an inline call; templated-first,
      AI-upgrade-second, templated-forever-on-failure; fail-loud client so the job's own
      retry/backoff can act on real signal
- [x] `LLM_PROVIDER`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_BASE_URL`/`LLM_TIMEOUT`/`LLM_MAX_TOKENS`
      config settings (`app/core/config.py`), `gpt-5.6-luna` default, `.env.example` documented
      (provider-agnostic scheme + `LLM_BASE_URL` = full base incl. `/v1` convention)
- [x] `app/platform/llm.py` — `LLMMessage`/`LLMClient` Protocol/`StubLLMClient`/`OpenAILLMClient`/
      `get_llm_client()`; live-verified against real `gpt-5.6-luna`: sends
      `max_completion_tokens` not `max_tokens`, does NOT forward `temperature` (model 400s on any
      explicit value); `LLM_BASE_URL` full-base-incl-`/v1` convention (fixed after review caught a
      double-`/v1` bug)
- [x] `app/services/assessment/narrative.py::build_narrative_messages` — data-minimized prompt
      (dimension scores, overall score, industry, stage only — no PII) · `app/worker/handlers/
      ai.py::handle_assessment_narrative` — re-fetches result, calls the LLM, overwrites
      `narrative`, benign no-op if the result is missing · `complete_assessment` enqueues
      `ai.assessment.narrative` alongside the pre-existing `roadmap.replan`
- [x] Live E2E journey (`e2e/test_ai_assessment_narrative.py`, 2 captures) proving enqueue → job
      claim → LLM (stub) call → persisted overwrite end to end, over real HTTP with a real
      Postgres-backed in-process worker drain, zero network calls (`LLM_PROVIDER=stub` pinned in
      `scripts/e2e_run.sh`) + full existing e2e suite re-run green (42 e2e, 1216 unit)
- [x] SOP + FE integration guide (captured live, incl. a called-out field-nesting trap: `complete`
      returns `data.narrative` flat, `GET` returns `data.result.narrative` nested) + this checklist
      reconcile — `docs/sop/2026-09-19-llm-seam-assessment-narrative.md`,
      `docs/fe-integration-guide-ai-assessment-narrative.md`
- [x] structured (JSON-shaped) LLM output → **built in Slice 2** (`complete_json`, below) · every
      other deferred-to-Module-03 AI consumer (onboarding AI panel, Mission reason line, Roadmap
      replan rationale, Health Score recommendation reasons, Learning recommendations, Validation
      Hub insight synthesizer, Business Builder's typed-record `ai-fill` jobs) still unbuilt, now
      unblocked on infrastructure only
- [x] **Deferred follow-ups (scoped out of Module 03 completion, 2026-09-21) — resolved
      2026-09-21, merged (PR #95):** ✅
      **per-workspace LLM daily token budget** — `llm_usage_daily` ledger + migration `0030`,
      `LLM_DAILY_TOKEN_BUDGET` config (default `15000`, `≤0`=unlimited), `metered_complete`/
      `metered_complete_json` enforced across all 8 `ai.py` handlers + the business-plan
      generator (skip-and-keep-templated on over-budget); call-*rate* limiting (request
      frequency, as opposed to token volume) was **not** built — still a genuine gap, tracked as
      a follow-up in the SOP · ✅ **enrichment-status signal** — `GET /ai/status` (workspace-scoped:
      token usage vs. budget, `over_budget`, `resets_at`, recent failed AI-enrichment jobs); this
      is a workspace-level aggregate, not a per-field/per-record "still templated" marker on any
      individual `MissionTask`/`HealthRecommendation`/etc. — that finer-grained signal is still
      unbuilt, tracked as a follow-up in the SOP · **won't-do (2026-09-21, OpenAI-only):** a
      non-Anthropic/non-OpenAI-compatible `LLMClient` provider implementation — a scope decision,
      not a build; the existing `LLMClient` Protocol still supports adding one later if ever
      needed. **No remaining open Module-03 follow-ups.** SOP:
      `docs/sop/2026-09-21-llm-budget-ai-status.md`. FE guide:
      `docs/fe-integration-guide-ai-status.md`.

**Slice 2 — Structured Output + Canvas AI Fill** — *✅ MERGED to `develop` (PR #77;
3 tasks, no migration)*
- [x] Design + implementation plan (`.superpowers/sdd/2026-09-19-llm-structured-output-canvas-fill/`)
      — `complete_json` as a second method on the SAME `LLMClient` Protocol, not a new seam ·
      schema derived from the existing `CANVAS_BLOCKS` registry, not hand-maintained · fill-empties-
      only re-read at run time, never overwrite a user-filled block · fail-loud + job retry, no
      partial write on an LLM error
- [x] `LLMClient.complete_json(messages, *, schema: dict, max_tokens: int) -> dict`
      (`app/platform/llm.py`) — `StubLLMClient` deterministic per-property stub;
      `OpenAILLMClient` strict `response_format: {type: json_schema, strict: true}` mode,
      live-verified against real `gpt-5.6-luna` (Chat Completions supports it as-is, no Responses-
      API migration needed); `_post_chat`/`_content` DRY'd out of `complete()`, all 8 pre-existing
      `complete()` tests pass unchanged after the extraction
- [x] `canvas_json_schema(canvas_type)` (`app/services/business/canvas_defs.py`) — strict schema
      built from `CANVAS_BLOCKS` (list block → array-of-string, text block → string, every key
      required, `additionalProperties: false`) · `build_canvas_fill_messages`
      (`app/services/business/ai_fill.py`, new) — data-minimized prompt (startup name/industry/
      stage + block key/label/kind lines only, no PII)
- [x] `handle_canvas_ai_fill` (`app/worker/handlers/ai.py`), registered as
      `"business.canvas.ai_fill"` — the exact job type `POST /canvases/{type}/ai-fill` has enqueued
      since Module 08 Slice 1; re-reads the canvas's current blocks, drafts only the empty ones via
      `complete_json`, merges back only previously-empty keys present in the LLM response,
      `validate_blocks`, `version += 1`
- [x] Live E2E journey (`e2e/test_canvas_ai_fill.py`, 2 captures) proving enqueue → job claim →
      structured LLM (stub) call → schema-constrained merge → persisted write end to end, over real
      HTTP with a real Postgres-backed in-process worker drain, zero network calls
      (`LLM_PROVIDER=stub`, already exported by `scripts/e2e_run.sh` since Slice 1) + full existing
      e2e suite re-run green (44 e2e, 1245 unit)
- [x] SOP + FE integration guide (captured live, incl. the augment-never-overwrite contract and the
      two job-completion polling options) + this checklist reconcile —
      `docs/sop/2026-09-19-llm-structured-output-canvas-fill.md`,
      `docs/fe-integration-guide-ai-canvas-fill.md`
- [x] `business.{kind}.ai_fill` worker for Module 08's typed records (personas/revenue
      streams/competitors/pricing) → **shipped in Slice 3, below** (needed a Pydantic-model-driven
      schema variant, not a copy-paste of the canvas one, as flagged here) — §08.11 AI Business Plan
      Generator shipped (see Module 08, below) on this seam's free-text `complete()` method (one
      call per fixed section), not `complete_json` — the plan generator turned out not to need
      structured output after all, so it did not end up depending on this item
- [ ] _Deferred:_ no `pydantic.TypeAdapter(...).json_schema()`-based helper for a future
      Pydantic-shaped consumer (Slice 3 hand-built its schema the same way this slice hand-built
      `canvas_json_schema`) · no Anthropic `complete_json` implementation (the wire shape is
      itself an OpenAI-specific convention) · no overwrite/"regenerate everything" ai-fill mode ·
      no structured "ai-fill failed / still empty" signal beyond the existing job status — see SOP
      Follow-ups

**Slice 3 — Typed Records AI Fill** — *✅ MERGED to `develop` (PR #81; 3 tasks, no migration)*
- [x] Design + implementation plan (`.superpowers/sdd/2026-09-19-records-ai-fill/`) — records need a
      Pydantic-model-driven schema variant (`RECORD_SCHEMAS`), not a copy of Slice 2's
      dataclass-driven `canvas_json_schema` · fill-empties is a whole-kind gate (any existing record
      → no-op), not a per-block augment · validate-and-skip (not validate-and-fail) via the existing
      `create_record` path
- [x] `StubLLMClient._stub_value` made genuinely recursive (`app/platform/llm.py`) — object nodes
      recurse per property key, array nodes recurse into `items` and wrap, enum nodes short-circuit
      to the first member; flat canvas-shape behavior unchanged and pinned by a new regression test
      (`test_stub_complete_json_still_flat_for_canvas_shape`)
- [x] `record_json_schema(kind)` / `_record_item_schema(kind)` (`app/services/business/
      record_defs.py`) — strict per kind, built from `RECORD_SCHEMAS`; `competitor.map_x`/`map_y`
      intentionally omitted (AI shouldn't set UI positioning coords); enum fields constrained to
      valid members · `build_record_fill_messages(kind, *, name, industry, stage)`
      (`app/services/business/ai_fill.py`) — PII-free prompt
- [x] `handle_record_ai_fill` (`app/worker/handlers/ai.py`), registered for all 4
      `business.{kind}.ai_fill` job types — the exact job types `POST /{kind}/ai-fill` has enqueued
      since Module 08 Slice 2; whole-kind empty-gate, up to 3 records via `complete_json`, each
      validated (and skipped on failure, not aborted) through `create_record`, `db.flush()` only
- [x] Live E2E journey (`e2e/test_records_ai_fill.py`, 2 captures) proving enqueue → job claim →
      structured LLM (stub) call → validated `create_record` write end to end, over real HTTP with a
      real Postgres-backed in-process worker drain, zero network calls (`LLM_PROVIDER=stub`, already
      exported by `scripts/e2e_run.sh` since Slice 1) + full existing e2e suite re-run green
      (46 e2e, 1266 unit)
- [x] SOP + FE integration guide (extended, captured live, incl. the `data.records` field-nesting
      trap vs. canvas's flat `data.blocks`) + this checklist reconcile —
      `docs/sop/2026-09-19-records-ai-fill.md`,
      `docs/fe-integration-guide-ai-canvas-fill.md` (§5)
- [ ] _Deferred:_ no top-up/"regenerate" mode for a kind that already has some records (whole-kind
      gate only) · mission reason line and health recommendations → **shipped in Slice 4, below**
      (2026-09-19) · dashboard briefing → **shipped in Slice 5, below** (2026-09-19) · roadmap
      re-plan rationale → **shipped in Slice 6, below** (2026-09-21) · onboarding panel →
      **shipped in Slice 7, below** (2026-09-21 — the last named Module-03 AI consumer) · no
      structured "ai-fill failed /
      still empty" signal beyond the existing job status · `create_record`'s position assignment
      still not race-safe (pre-existing gap, unchanged) — see SOP Follow-ups

**Slice 4 — Mission Reason + Health Recommendation AI Upgrade** — *✅ merged (PR #83)
(7 tasks, no migration) — 2026-09-19*
- [x] Design + implementation plan
      (`.superpowers/sdd/2026-09-19-module-03-mission-health-ai/`) — same async-upgrade pattern as
      Slices 1–3, reused twice, not redesigned · mission enqueue gated on task count (skip the
      empty weekends-off mission) · health idempotency via a `body == catalog default` guard on
      `pending` rows only, with a genuine no-LLM-call fast path once nothing is left to do
- [x] `app/services/mission/ai_reason.py` (new) — `mission_reason_schema(n)` (strict,
      `reasons: [{order, reason}]`) · `build_mission_reason_messages` (PII-free) ·
      `handle_mission_reason` (`app/worker/handlers/ai.py`), registered `"ai.mission.reason"` —
      rewrites only the tasks the model returned a reason for, `[:300]` truncation, templated
      reason is the permanent fallback for any task the model skips
- [x] `_enqueue_mission_reason` helper + call site in `get_or_generate_today`
      (`app/services/mission/service.py`) — gated on `order` so the empty weekends-off mission
      enqueues nothing (keeps the function under ruff's C901 ceiling)
- [x] `app/services/health_score/ai_recommendations.py` (new) — `catalog_bodies()` ·
      `health_recommendation_schema(keys)` (strict, `key` enum-constrained to the given pending
      keys) · `build_health_recommendation_messages` (PII-free) · `handle_health_recommendations`
      (`app/worker/handlers/ai.py`), registered `"ai.health.recommendations"` — idempotent,
      `accepted`/`dismissed` rows never touched, `title` left as the catalog headline (only `body`
      personalized)
- [x] One new `job_dispatcher.enqueue(..., "ai.health.recommendations", ...)` call site at the end
      of `recompute_health_score` (`app/services/health_score/service.py`)
- [x] Live E2E (`e2e/test_mission_reason.py`, `e2e/test_health_recommendations_ai.py`, 4 captures)
      proving enqueue → in-process worker drain → structured LLM (stub) call → persisted overwrite
      end to end, over real HTTP, zero network calls (`LLM_PROVIDER=stub`) + full e2e suite
      re-run green (48 e2e, no regression)
- [x] Two live-verified corrections to the design/plan text, carried into both FE guides: the
      mission endpoint is `/api/v1/missions/today` (**plural** "missions") · health recommendations
      actually materialize via `GET /health-score`'s lazy-on-read fallback, not (reliably) the
      assessment-complete request itself — a pre-existing `complete_assessment` autoflush-timing
      quirk, documented inline (**now fixed 2026-09-19** — `db.flush()` before the inline recompute;
      `docs/sop/2026-09-19-complete-assessment-recompute-flush.md`)
- [x] SOP + FE integration guide extensions (both payloads pasted verbatim from live captures) +
      this checklist reconcile — `docs/sop/2026-09-19-mission-health-ai.md`,
      `docs/fe-integration-guide-mission.md`, `docs/fe-integration-guide-health-score.md`
- [ ] _Deferred:_ dashboard AI briefing → **shipped in Slice 5, below** (2026-09-19) · roadmap
      re-plan rationale → **shipped in Slice 6, below** (2026-09-21) · onboarding AI panel →
      **shipped in Slice 7, below** (2026-09-21 — the last named Module-03 AI consumer) · ~~the pre-existing
      `complete_assessment` same-transaction recompute no-op (autoflush timing)~~ **fixed
      2026-09-19** (`docs/sop/2026-09-19-complete-assessment-recompute-flush.md`) · no structured
      "AI upgrade pending / still templated" signal on either `MissionTask` or
      `HealthRecommendation` · the live e2e only proves the single-item-rewrite path
      (deterministic stub); the multi-item path is unit-tested only — see SOP Follow-ups

**Slice 5 — Dashboard AI Briefing** — *✅ merged (PR #87)
(5 tasks, migration `0027_daily_briefings`) — 2026-09-19*
- [x] Design + implementation plan
      (`docs/superpowers/specs/2026-09-19-dashboard-ai-briefing-design.md`,
      `docs/superpowers/plans/2026-09-19-dashboard-ai-briefing.md`) — same async-upgrade pattern
      as Slices 2–4, applied to a new table rather than an existing column, since Module 02 never
      persisted `briefing`/`risks`/`opportunities` · lazy-on-read generation gated on
      assessment-complete, mirroring Mission's own lazy-generation shape and race guard
- [x] `daily_briefings` table + migration `0027_daily_briefings` (chains off
      `0026_business_plans`, sole alembic head) + `BriefingStatus` enum
      (`generating`/`ready`/`failed`) + `DailyBriefing` model (`app/db/models/dashboard.py`),
      unique on `(startup_id, briefing_date)`
- [x] `app/services/dashboard/ai_briefing.py` (new) — `dashboard_briefing_schema()` (strict, 3
      required string fields) · `build_dashboard_briefing_messages` (PII-free: startup
      name/industry/stage + health score/band + mission/upcoming/weekly-task counts)
- [x] `gather_briefing_context` + `get_or_generate_briefing` + `_briefing_blocks`
      (`app/services/dashboard/service.py`) — assessment-complete gate (no assessment → original
      static `"empty"` shape, unchanged), lazy create + enqueue-once,
      `IntegrityError`-race-guarded (mirrors `_get_or_generate_today_race_safe`),
      `_section_isolated`-wrapped so a briefing failure can't 500 the dashboard; `get_summary`
      wired to the new `_briefing_blocks(...)` output
- [x] `handle_dashboard_briefing` (`app/worker/handlers/ai.py`), registered
      `"ai.dashboard.briefing"` — idempotent on `status == generating`, `complete_json` call,
      overwrites all 3 fields + flips `status = ready`, `db.flush()` only
- [x] Live E2E journey (`e2e/test_dashboard_ai_briefing.py`, 2 captures) proving enqueue →
      in-process worker drain → structured LLM (stub) call → persisted `ready` row end to end,
      over real HTTP, zero network calls (`LLM_PROVIDER=stub`) + full e2e suite re-run green
      (49 e2e, no regression) — also fixed a stale `"empty"` assertion in the pre-existing
      `e2e/test_dashboard.py` journey (now `"generating"` immediately post-assessment) and
      regenerated its capture
- [x] SOP + FE integration guide extension (both `"generating"`/`"ready"` payloads pasted
      verbatim from live captures, plus a callout distinguishing this feature's `[stub-llm]`
      markers from Slice 4's unrelated ones in the same capture) + this checklist reconcile —
      `docs/sop/2026-09-19-dashboard-ai-briefing.md`, `docs/fe-integration-guide-dashboard.md`
- [ ] _Deferred:_ roadmap re-plan rationale → **shipped in Slice 6, below** (2026-09-21) ·
      onboarding AI panel → **shipped in Slice 7, below** (2026-09-21 — the last named Module-03
      AI consumer) ·
      `briefing`/`risks`/`opportunities` remain single prose strings, not structured lists · no
      06:00 prewarm / scheduled regeneration (purely lazy-on-read) · no intra-day regeneration
      once a day's row exists · `BriefingStatus.failed` defined but never written (no structured
      "generation failed" signal to the FE) · the race-guard path is unit-covered only, not proven
      under real concurrency — see SOP Follow-ups

**Slice 6 — Roadmap Re-plan Rationale** — *✅ merged (PR #90)
(7 tasks, migration `0028_roadmap_replan_rationale`) — 2026-09-21*
- [x] Design + implementation plan
      (`docs/superpowers/specs/2026-09-21-roadmap-replan-rationale-design.md`,
      `docs/superpowers/plans/2026-09-21-roadmap-replan-rationale.md`) — same async-upgrade
      pattern as Slices 1–5, applied to a new column on the existing `RoadmapReplan` row rather
      than a new table · free-text `complete()` chosen over `complete_json` (a holistic rationale
      is prose, not typed fields, same choice §08.11's plan generator made) · opportunistic
      same-branch removal of the dead `roadmap.replan` enqueue (no handler ever existed for it)
- [x] `roadmap_replans.rationale` (nullable `Text`) + migration `0028_roadmap_replan_rationale`
      (chains off `0027_daily_briefings`, sole alembic head)
- [x] `app/services/roadmap/ai_rationale.py` (new) — `build_roadmap_rationale_messages(*, stage,
      name, industry, changes)` (PII-free: startup name/industry/stage + shifted milestones'
      titles/dates/reasons only)
- [x] `_templated_rationale(summary, snapshot)` + `apply_replan` wiring
      (`app/services/roadmap/replan.py`) — writes the templated instant-fallback `rationale` into
      the new `RoadmapReplan` row synchronously, enqueues `ai.roadmap.rationale` once per applied
      re-plan, returns `rationale` in the apply result dict
- [x] `handle_roadmap_rationale` (`app/worker/handlers/ai.py`), registered
      `"ai.roadmap.rationale"` — re-fetches the replan row, one `complete()` call, overwrites
      `rationale`, `db.flush()` only; benign no-op if the row is missing
- [x] `GET /replan/history` (`app/api/v1/endpoints/roadmap.py`) — each row gains `"rationale":
      r.rationale`; no new route, no status-code change
- [x] Removed the dead `job_dispatcher.enqueue(db, "roadmap.replan", ...)` at
      `app/services/assessment/service.py` (no handler was ever registered for it) — assessment
      completion still enqueues `ai.assessment.narrative` + `ai.health.recommendations`;
      `tests/api/assessment/test_complete.py` and
      `tests/services/assessment/test_complete_concurrency.py` (incl. its docstring) updated to
      assert the new completion-job set and that `roadmap.replan` is no longer enqueued
- [x] Live E2E journey (`e2e/test_roadmap_replan_rationale.py`, 2 captures) proving force-a-slip →
      preview → apply (templated `rationale` asserted) → in-process worker drain → `GET
      /replan/history` (AI-authored `[stub-llm]` `rationale` asserted) end to end, over real HTTP,
      zero network calls (`LLM_PROVIDER=stub`) + full e2e suite re-run green (50 e2e, no
      regression); full non-e2e suite re-run — 1,311 passed; single alembic head, no drift
- [x] SOP + FE integration guide extension (both `apply`/`history` payloads pasted verbatim from
      live captures, incl. the apply-is-templated / history-is-AI-authored nuance called out
      explicitly) + this checklist reconcile — `docs/sop/2026-09-21-roadmap-replan-rationale.md`,
      `docs/fe-integration-guide-roadmap.md` (§9)
- [ ] _Deferred:_ onboarding AI panel → **shipped in Slice 7, below** (2026-09-21 — the last named
      Module-03 AI consumer) · per-change `reason` strings inside `changes[]` remain templated, not
      AI-upgraded (only the holistic record-level `rationale` was added) · the `roadmap.replan`
      job type is gone, not repurposed (Module 05 Slice 3's SOP had already ruled out ever
      auto-draining it) · no structured "AI enrichment failed / still templated" signal · the
      live e2e only proves the single-replan path; a re-plan with more than 3 shifted milestones
      (the `_templated_rationale` "and N more" branch) is unit-tested only — see SOP Follow-ups

**Slice 7 — Onboarding AI Panel** — *✅ merged (PR #92)
(7 tasks, migration `0029_startup_profile_ai_panel`) — 2026-09-21*
- [x] Design + implementation plan
      (`.superpowers/sdd/2026-09-21-onboarding-ai-panel/`) — no PRD text for this slice (scope
      came from the onboarding UI comp's existing calibration-panel concept) · one-shot
      generate-once trigger lives in `apply_step`, gated on "`ai_panel` was null," since (unlike
      every prior slice) there was no existing "apply"-style write path to piggyback on · dead
      `app/platform/ai.py` (`AIPanel`/`StubAIPanel`) seam retired in the same branch, its wording
      preserved as the new templated fallback
- [x] `startup_profiles.ai_panel` (nullable `Text`) + migration
      `0029_startup_profile_ai_panel` (chains off `0028_roadmap_replan_rationale`, sole alembic
      head)
- [x] `app/services/onboarding/ai_panel.py` (new) — `_templated_panel(industry, stage)` (instant
      deterministic fallback, reuses the retired seam's wording) · `build_onboarding_panel_messages(*,
      industry, stage, goals)` (PII-free — no founder name/role/country/phone)
- [x] `_maybe_generate_ai_panel` (`app/services/onboarding/steps.py`), called at the end of every
      `apply_step` — one-shot: no-ops once `ai_panel` is non-null, and until industry + stage +
      goals are all set; writes the templated value + enqueues `ai.onboarding.panel` exactly once
      · `serialize_state` (`app/services/onboarding/workspace.py`) gains `"ai_panel"`, shared
      flat by both `GET`/`PATCH /onboarding/state` (no field-nesting asymmetry)
- [x] `handle_onboarding_panel` (`app/worker/handlers/ai.py`), registered
      `"ai.onboarding.panel"` — re-fetches the startup, one `complete()` call, overwrites
      `ai_panel`, `db.flush()` only; benign no-op if the startup/profile is missing
- [x] Retired the dead, never-consumed `app/platform/ai.py` (`AIPanel`/`StubAIPanel`) +
      `tests/platform/test_ai.py` — confirmed zero consumers repo-wide before deletion
- [x] Live E2E journey (`e2e/test_onboarding_ai_panel.py`, 2 captures) proving sign-up → complete
      the three signals (templated `ai_panel` asserted) → in-process worker drain → `GET
      /onboarding/state` (AI-authored `[stub-llm]` `ai_panel` asserted) end to end, over real
      HTTP, zero network calls (`LLM_PROVIDER=stub`) + full e2e suite re-run green (51 e2e, no
      regression)
- [x] SOP + FE integration guide (new, focused; both `state_templated`/`state_after_drain`
      payloads pasted verbatim from live captures) + this checklist reconcile —
      `docs/sop/2026-09-21-onboarding-ai-panel.md`,
      `docs/fe-integration-guide-onboarding-ai-panel.md`
- [ ] _Deferred:_ **this closes the Module-03 AI-consumer set — all six named consumers are now
      shipped, across seven build slices.** Module 03 itself is now marked **COMPLETE** (owner
      decision, 2026-09-21) — the non-OpenAI/Anthropic provider implementation and per-workspace
      LLM budget/rate limiting (named since Slice 1) were, at the time of this slice, still
      unbuilt; **reconciled 2026-09-21 on `feat/llm-budget-ai-status`:** the per-workspace LLM
      token budget and a workspace-level `GET /ai/status` enrichment-status endpoint are now
      shipped, and the non-OpenAI provider item is dropped won't-do (OpenAI-only) — no remaining
      open Module-03 follow-ups; see this module's own Slice 1 Deferred bullet and the Snapshot
      above · no regenerate-on-edit (the one-shot guard is permanent; changing
      industry/stage/goals after the panel exists does not re-trigger it) · no interactive chat
      panel (a single one-time greeting, not a two-way conversation) · no *per-field* "AI
      enrichment failed / still templated" signal specific to `ai_panel` (the new `/ai/status`
      endpoint is a workspace-level aggregate, not a per-record marker) · the live e2e only
      proves the single-panel happy path; the no-op paths (missing startup, LLM failure) are
      unit-tested only — see SOP Follow-ups

## ✅ Module 08 — Business Builder — *all 4 slices — MODULE 08 COMPLETE (PR #39, PR #46, PR #47,
`feat/ai-business-plan-generator` — Slice 4/§08.11 merged (PR #79)). Slice 4, the AI Business Plan
Generator (§08.11), was the last open PRD sub-screen, blocked on Module 03 (AI Co-Founder) end to
end — it now ships using Module 03 Slice 1's free-text LLM seam directly (`complete()`, one call
per fixed section), not the structured-output mode Slice 2 built for canvas ai-fill; see Slice 4
below for why. The `business.canvas.ai_fill` job Slice 1 enqueues has a real worker (Module 03
Slice 2, above), and the typed-record `business.{kind}.ai_fill` job (Slice 2, below) now does too
(Module 03 Slice 3, merged (PR #81)) — every ai-fill job Module 08
enqueues now has a real handler.*

_Slice 1: five structured strategy canvases (`business_model`/`lean`/`value_prop`/`mission_vision`/
`swot`), each a generic `business_canvases` row + an in-code block registry. Optimistic-concurrency
versioned full-replace saves, derived completion, and an AI-fill job seam deliberately left
unconsumed until Module 03. Migration `0011_business_canvases`. SOP:
`docs/sop/2026-09-01-business-builder-canvas.md`.
Slice 2: four typed-artifact record kinds (`persona`/`revenue_stream`/`competitor`/`pricing`), each
a generic `business_records` row + an in-code Pydantic schema registry, under a uniform `{kind}`
CRUD surface. Same full-replace-PUT and deferred-ai-fill-job conventions as Slice 1; `GET
/overview` now returns 9 rows (5 canvas + 4 record). Migration `0012_business_records`. SOP:
`docs/sop/2026-09-04-business-builder-records.md`.
Slice 3: a non-editor member (PRD's `business_consultant` "suggest mode") can propose a
`canvas_update`/`record_create`/`record_update`/`record_delete` via `POST /suggestions`; a founder
or team_member reviews (`GET /suggestions?status=pending`) and resolves
(`POST /suggestions/{id}/approve|reject`) — applied through the SAME Slice 1/2 write functions, so
the existing full-replace and optimistic-concurrency contracts apply to a suggestion's approval for
free. Plus the competitor positioning map: editable 2×2 axes (`business_positioning_maps`, a new
singleton-per-startup table) and `map_x`/`map_y` on the existing `CompetitorData` record (no new
table for coordinates). Migrations `0013_business_suggestions`, `0014_business_positioning_maps`.
SOP: `docs/sop/2026-09-08-business-builder-slice3.md`._

**Slice 1 — Canvas Core** — *✅ merged to `develop` (PR #39, Tasks 1–6)*
- [x] Scope + locked decisions (generic table + `CANVAS_BLOCKS` code registry, not 5 tables ·
      optimistic-concurrency version counter, not a row lock · PUT is full-replace, not a merge ·
      completion derived on read, not cached · ai-fill enqueue-only, real worker deferred to
      Module 03) — `.superpowers/sdd/2026-09-01-business-builder-canvas/`
- [x] `CanvasType` enum + `BusinessCanvas` model + migration `0011_business_canvases` (chains off
      `0010_dashboard`, sole alembic head) + standalone `startup_id` index
- [x] `CANVAS_BLOCKS` block registry (`app/services/business/canvas_defs.py`) — 5 canvas types,
      `BlockDef{key,label,kind}`, `empty_blocks()` scaffold
- [x] Canvas service — `get_or_create_canvas` (lazy-get/create) · `validate_blocks` (per-block-kind
      422) · `save_canvas` (version check → full-replace merge, pinned by a dedicated test → 
      `business.artifact.completed` on the not-complete→complete transition only) · `completion`
      (derived `filled_blocks`/`total_blocks`/`completion_pct`/`status`) · `overview`
- [x] `GET /business-builder/overview` (member, read-only, creates no rows) · `GET
      /business-builder/canvases/{type}` (member, lazy-creates on first read; unknown type → 404)
- [x] `PUT /business-builder/canvases/{type}` (editor; stale `version` → 409
      `CANVAS_VERSION_CONFLICT`; bad block → 422; **full-replace**, not a partial merge — an
      omitted block key resets to empty)
- [x] `POST /business-builder/canvases/{type}/ai-fill` (editor; 202, enqueues
      `business.canvas.ai_fill` job, writes no canvas row; job stays `queued` — no worker yet)
- [x] Access: reads = any active member (mentor incl.) · writes = founder/team_member (mentor →
      403 `FORBIDDEN`)
- [x] Live E2E journey (`e2e/test_business_builder.py`, 6 captures) + smoke openapi surface (3
      business-builder paths)
- [x] SOP + FE integration guide (captured live) + this checklist reconcile —
      `docs/sop/2026-09-01-business-builder-canvas.md`,
      `docs/fe-integration-guide-business-builder.md`
- [x] real `business.canvas.ai_fill` worker → **shipped, Module 03 Slice 2** (see Module 03 above,
      `docs/sop/2026-09-19-llm-structured-output-canvas-fill.md`)
- [x] AI Business Plan Generator (§08.11) → **shipped, Slice 4 below**
      (`feat/ai-business-plan-generator`, merged (PR #79))
- [ ] _Deferred:_ canvas version history (no row-level history table) · no
      `business.artifact.completed` consumer yet · no `write_activity` call site for canvas saves
      (doesn't show up in the dashboard activity feed) · JSONB doesn't preserve `blocks` key order
      (documented in the FE guide, not a bug) — see SOP Follow-ups

**Slice 2 — Typed Artifacts** — *✅ merged to `develop` (PR #46, Tasks 1–6)*
- [x] Scope + locked decisions (generic `business_records` table + `RECORD_SCHEMAS` Pydantic
      registry, not 4 tables · real Pydantic model validation per kind, not a hand-rolled checker ·
      PUT is full-replace, not a merge, same as Slice 1 · record-kind overview rows are binary
      (`count >= 1` → complete), no partial "continue" state · ai-fill enqueue-only, real worker
      deferred to Module 03) — `.superpowers/sdd/2026-09-04-business-builder-records/`
- [x] `RecordKind`/`ThreatLevel`/`PricingModelType` enums + `BusinessRecord` model + migration
      `0012_business_records` (chains off `0011_business_canvases`, sole alembic head) +
      standalone `startup_id` index + composite `(startup_id, kind, position)` index
- [x] `RECORD_SCHEMAS` registry (`app/services/business/record_defs.py`) — 4 Pydantic v2 models
      (`PersonaData`/`RevenueStreamData`/`CompetitorData`/`PricingData` + nested `PricingTier`),
      each `extra="forbid"`; `fields(kind)` descriptor incl. enum `choices` (FE dropdown source)
- [x] Records service — `validate` (Pydantic → 422 `field_errors`) · `create_record`
      (position-by-count append, `business.artifact.completed` on a kind's first record only) ·
      `update_record` (full-replace, pinned by a dedicated test) · `delete_record` ·
      `list_records` (ordered) · `overview()` extended with the 4 record-kind rows
- [x] `GET /business-builder/{kind}` (member; `{records, fields}`; unknown kind → 404) · `POST
      /business-builder/{kind}` (editor; 201; bad `data` → 422 `VALIDATION_ERROR`)
- [x] `PUT /business-builder/{kind}/{record_id}` (editor; **full-replace**, not a partial merge —
      an omitted field resets to its schema default; unknown/cross-tenant id → 404) · `DELETE
      /business-builder/{kind}/{record_id}` (editor; `{deleted: true}`)
- [x] `POST /business-builder/{kind}/ai-fill` (editor; 202, enqueues `business.{kind}.ai_fill`
      job, writes no record synchronously — the job now has a real worker, see below)
- [x] `GET /business-builder/overview` extended — 9 rows total (5 canvas + 4 record kinds);
      record rows: `{type, label, status, completion_pct, count}`, `status`/`completion_pct`
      derived from `count >= 1`
- [x] Access: reads = any active member (mentor incl.) · writes = founder/team_member (mentor →
      403 `FORBIDDEN`) — same as Slice 1
- [x] Live E2E journey (`e2e/test_business_builder.py::test_business_builder_records_journey`,
      10 new captures) + the pre-existing Slice 1 journey re-verified (its `/overview` assertions
      widened for the 4 new record rows) + smoke openapi surface (3 new `{kind}` paths)
- [x] SOP + FE integration guide (captured live) + this checklist reconcile —
      `docs/sop/2026-09-04-business-builder-records.md`,
      `docs/fe-integration-guide-business-builder.md` (§6–§11)
- [x] AI Business Plan Generator (§08.11) → **shipped, Slice 4 below**
      (`feat/ai-business-plan-generator`, merged (PR #79)) — did not end up needing this slice's
      typed-record `ai_fill` capability
- [x] real `business.{kind}.ai_fill` worker → **shipped, Module 03 Slice 3** (see Module 03 above,
      `docs/sop/2026-09-19-records-ai-fill.md`; merged (PR #81)) —
      needed a Pydantic-model-driven schema variant, not a copy-paste of the canvas one, exactly as
      flagged here
- [ ] _Deferred:_ Module 12 (Revenue) sync for
      `revenue_stream` records · no reorder/`PATCH .../{id}/reorder` endpoint (`position` is
      append-only) · `create_record`'s position assignment is not race-safe (no unique constraint
      on `(startup_id, kind, position)`, unlike Slice 1's race-safe `get_or_create_canvas`) ·
      `test_put_cross_tenant_404` is not yet a genuine cross-tenant test (asserts against an
      unknown id, not a real second tenant's record; same gap for `DELETE`) · JSONB doesn't
      preserve `data` key order (documented in the FE guide, not a bug) — see SOP Follow-ups

**Slice 3 — Suggestions + Positioning Map** — *✅ merged to `develop` (PR #47, Tasks 1–6)*
- [x] Scope + locked decisions (suggestions apply through the EXISTING Slice 1/2 write functions,
      not a parallel apply path · `base_version` pinned at create time vs. `current` recomputed
      live at every read — two different mechanisms, not the same snapshot · positioning
      coordinates live on the competitor RECORD (`map_x`/`map_y`), not a separate points table ·
      `PositioningMapSave` carries `axes` only — no way to set coordinates through the map
      endpoint, by design · any active member can suggest, only `_editor` can approve/reject, same
      role split as every other Business Builder write) —
      `.superpowers/sdd/2026-09-08-business-builder-suggestions/`
- [x] `SuggestionOp`/`SuggestionStatus` enums + `BusinessSuggestion`/`BusinessPositioningMap`
      models + migrations `0013_business_suggestions`, `0014_business_positioning_maps` (chain off
      `0012_business_records`, sole alembic head) + `CompetitorData.map_x`/`map_y` (no migration —
      new JSONB keys on the existing `data` column)
- [x] Suggestions service (`app/services/business/suggestions.py`) — `create_suggestion` (per-op
      target/payload validation, `base_version` capture for `canvas_update`) · `list_suggestions`
      (status filter) · `_current` (live diff-view read) · `serialize_suggestion` · `_apply`
      (dispatches to `save_canvas`/`create_record`/`update_record`/`delete_record`) ·
      `approve_suggestion`/`reject_suggestion` (pending → approved/rejected state machine)
- [x] Positioning service (`app/services/business/positioning.py`) — `get_or_create_map`
      (race-safe lazy-create, mirrors `get_or_create_canvas`) · `validate_axes` · `update_axes` ·
      `assemble_map` (joins map axes with every competitor's coordinates)
- [x] `POST /business-builder/suggestions` (any member; per-op 404/422) · `GET
      /business-builder/suggestions` (any member; `?status=` filter, unknown status → 404)
- [x] `POST /business-builder/suggestions/{id}/approve` (editor; applies via `_apply()`; 409
      `SUGGESTION_NOT_PENDING` / 409 `CANVAS_VERSION_CONFLICT` / 404 target-gone) · `POST
      /business-builder/suggestions/{id}/reject` (editor; 409 `SUGGESTION_NOT_PENDING`)
- [x] `GET /business-builder/positioning-map` (any member; lazy-creates axes row) · `PUT
      /business-builder/positioning-map` (editor; axes only — coordinates via the EXISTING
      `POST`/`PUT /competitors`, not a new route)
- [x] Access: suggest = any active member · approve/reject/PUT-axes = founder/team_member
      (`business_consultant`/mentor/accountant/legal_advisor/investor → 403 `FORBIDDEN` on
      resolve, same as every other Business Builder write)
- [x] Live E2E journeys (`e2e/test_business_builder.py::test_business_suggestions_journey`,
      `::test_business_positioning_map_journey`, 25 new captures — both 409s and the 403
      exercised live, not just derived from source) + full existing suite re-run green (32 e2e, 824
      unit) + smoke openapi surface
- [x] SOP + FE integration guide (every body captured live, zero source-derived error rows) + this
      checklist reconcile — `docs/sop/2026-09-08-business-builder-slice3.md`,
      `docs/fe-integration-guide-business-builder-suggestions.md`
- [x] **AI Business Plan Generator (PRD §08.11)** → **shipped, Slice 4 below**
      (`feat/ai-business-plan-generator`, merged (PR #79)) — `POST /business-builder/plan/generate`,
      `business_plans` entity, `business.plan.generated` event. Was dependency-blocked on Module 03
      (AI Co-Founder) entirely; Module 03 Slice 1
      (`docs/sop/2026-09-19-llm-seam-assessment-narrative.md`) built the LLM seam this generator
      calls directly (`complete()`, free-text — see Slice 4 for why the Slice 2 structured-output
      mode wasn't needed here). The document-store half of the dependency landed on
      `feat/documents-templates` (Module 18 Slice 1 — see below), whose
      `create_document(..., ai_generated=True, kind=business_plan)` seam this generator now calls.
      This was the last Module 08 PRD sub-screen not yet shippable — **Module 08 is now fully
      complete, all 4 slices.**
- [ ] _Deferred:_ reject-reason field
      on `POST .../reject` (no structured "why" today) · no server-computed suggestion diff
      summary beyond raw `current`/`payload` · no notification wired to
      `business.suggestion.created`/`approved`/`rejected` (events fire, no consumer yet) — see SOP
      Follow-ups

**Slice 4 — AI Business Plan Generator (§08.11)** — *✅ MERGED to `develop` (PR #79;
5 tasks, migration `0026_business_plans`)*
- [x] Design + implementation plan
      (`.superpowers/sdd/2026-09-19-ai-business-plan-generator/`) — fixed 10-section outline
      (`PLAN_SECTIONS`), not an LLM-decided structure · section-by-section free-text `complete()`
      calls, not one giant prompt or `complete_json` (a plan section is prose, not a fixed schema
      — Slice 2's structured mode doesn't fit this shape) · plan stored as a Module 18 `Document`
      (`kind=business_plan`), not a new bespoke table for content · async worker job, same
      enqueue-then-drain pattern as every other AI consumer on this seam
- [x] `BusinessPlanStatus` enum (`generating`/`complete`/`failed`) + `BusinessPlan` model
      (`startup_id`, `status`, `document_id`, `created_by_id`) + migration `0026_business_plans`
      (chains off `0025_roadmap_milestone_due_idx`, sole alembic head)
- [x] `PLAN_SECTIONS` — 10 fixed sections (`app/services/business/plan_defs.py`: Executive
      Summary, Problem & Opportunity, Solution & Product, Market & Customers, Business Model,
      Go-to-Market, Competition, Team, Financials & Projections, Roadmap & Milestones), each a
      `{key, heading, guidance}` · `build_plan_context` (`app/services/business/plan_context.py`)
      — PII-free context gatherer over the startup's profile/canvases/records/latest completed
      assessment/roadmap phases+milestones · `build_section_messages` — per-section prompt builder
- [x] `handle_plan_generate` (`app/worker/handlers/plan.py`), registered as
      `"business.plan.generate"` — benign no-op if the plan row is missing or already resolved;
      one `client.complete(...)` call per `PLAN_SECTIONS` entry; assembles `[{heading, body}]`,
      calls `create_document(..., kind=business_plan, ai_generated=True)`, links
      `plan.document_id`, flips `status` to `complete`, publishes `business.plan.generated`
- [x] `POST /business-builder/plan/generate` (editor; 202, `{plan_id, status: "generating"}`,
      enqueues `business.plan.generate`) · `GET /business-builder/plan` (any active member; latest
      plan `{id, status, document_id, created_at}`, 404 if none yet)
- [x] `business.plan.generated` event maps to the existing `business` notification category — no
      new category needed (`tests/services/notifications/test_plan_notification.py`)
- [x] Live E2E journey (`e2e/test_business_plan.py`, 3 captures) proving enqueue → in-process
      worker drain → 10 sequential LLM (stub) calls → `create_document` → `GET /plan` shows
      `status: "complete"` + `document_id` → `GET /documents/{id}` returns all 10 sections with
      stub bodies, over real HTTP with a real Postgres-backed worker drain, zero network calls
      (`LLM_PROVIDER=stub`, already exported by `scripts/e2e_run.sh` since Module 03 Slice 1) +
      full existing e2e suite re-run green (45 e2e, 1257 unit)
- [x] SOP + FE integration guide (captured live) + this checklist reconcile —
      `docs/sop/2026-09-19-ai-business-plan-generator.md`,
      `docs/fe-integration-guide-ai-business-plan.md`
- [ ] _Deferred:_ no plan history/list endpoint (only the latest plan is retrievable — regenerating
      creates a new row but the old one and its document are not surfaced) · a plan stuck
      `generating` after the worker's retries are exhausted never flips to `failed` in v1 (no
      terminal-failure transition wired yet, despite the enum having the value) · no incremental
      per-section progress signal (the FE only sees `generating` → `complete`, not "3 of 10
      sections done") · sections are free text, not structured financials (no numeric revenue/cost
      fields a dashboard could chart) · no PDF/export format, Document markdown only ·
      regenerating a plan is just a fresh `POST` (no per-section regenerate, no diff against the
      previous version) · remaining Module 03 AI consumers (typed-record `ai_fill` → **shipped,
      Module 03 Slice 3**, see above; mission reason + health-score recommendations → **shipped,
      Module 03 Slice 4**; dashboard briefing → **shipped, Module 03 Slice 5**; roadmap re-plan
      rationale → **shipped, Module 03 Slice 6** (2026-09-21); onboarding AI panel → **shipped,
      Module 03 Slice 7** (2026-09-21) — **all six named Module-03 AI consumers, across all seven
      build slices, are now shipped**; Learning recommendations and
      Validation Hub's insight synthesizer remain separately deferred, unblocked on
      infrastructure only, not yet started — see `docs/sop/2026-09-19-llm-seam-assessment-narrative.md`
      and `docs/sop/2026-09-19-ai-business-plan-generator.md`) — see SOP Follow-ups

## ✅ Module 18 — Documents & Templates — *all 4 slices MERGED — MODULE 18 COMPLETE: Slice 1
(Library Core) PR #48 · Slice 2 (Upload & Files) PR #50 · Slice 3 (Sharing) PR #53 · Slice 4
(E-signature) PR #55*

_Module 18 has no detailed textual PRD entry — scope recovered from the UI comp
(`Documents & Templates.dc.html`), decomposing into four slices: Library Core (a document store +
in-code template registry), Upload & Files (Cloudinary-backed binary storage), Sharing (external
tokenized read links), and E-signature (this slice, tokenized signing links) — **all four now
built**, closing out Module 18. Slice 1 is also the `create_document` seam Module 08's AI Business
Plan Generator (§08.11, now shipped) calls — see that module's entry above. SOPs:
`docs/sop/2026-09-09-documents-templates-slice1.md`,
`docs/sop/2026-09-10-documents-files-slice2.md`,
`docs/sop/2026-09-12-documents-sharing-slice3.md`,
`docs/sop/2026-09-14-documents-esignature-slice4.md`._

**Slice 1 — Document Library Core** — *✅ merged to `develop` (PR #48, Tasks 1–5)*
- [x] Scope + locked decisions (generic `documents` table + JSONB `sections` array, canvas
      pattern, not a normalized child table · optimistic-concurrency `version` counter, full-
      replace `PUT`, same as Business Builder · in-code `DOCUMENT_TEMPLATES` registry, not a DB
      table, same as `CANVAS_BLOCKS` · `folder` is a freeform string column, not a `folders`
      table · no export/uploads/sharing/e-sign in this slice) —
      `.superpowers/sdd/2026-09-09-documents-templates-slice1/`
- [x] `DocumentKind`/`DocumentStatus` enums + `Document` model + migration `0016_documents`
      (chains off `0015_business_positioning_maps`, sole alembic head) + standalone
      `startup_id`/`created_by_id` indexes + composite `(startup_id, kind)` index
- [x] `DOCUMENT_TEMPLATES` registry (`app/services/documents/template_defs.py`) — 5 templates
      (Business Plan/Pitch Deck/Financial Model/Meeting Notes/One-Pager), `instantiate()` (assigns
      section ids, empty bodies), `catalog()`/`template_view()`
- [x] Document service (`app/services/documents/service.py`) — `validate_sections` (shape check +
      id assignment, 422 on bad shape) · `create_document` (the Module 08 AI-generator seam,
      publishes `document.created`) · `list_documents` (kind/folder/status filters) ·
      `get_document` (tenant-scoped 404) · `update_document` (version check → 409
      `DOCUMENT_VERSION_CONFLICT`, else full-replace + version bump) · `delete_document` ·
      `serialize_summary`/`serialize_document` (the summary-vs-full split)
- [x] `GET /document-templates` · `GET /document-templates/{key}` (member; unknown key → 404) ·
      `GET /documents?kind=&folder=&status=` (member; **summaries only, no `sections`**; unknown
      filter value → 404)
- [x] `POST /documents` (editor; 201; `template_key` seeds `kind`/`title`/`sections`, else
      explicit/blank) · `GET /documents/{id}` (member; full document **with `sections`**) · `PUT
      /documents/{id}` (editor; full-replace; stale `version` → 409
      `DOCUMENT_VERSION_CONFLICT`) · `DELETE /documents/{id}` (editor; `{deleted: true}`)
- [x] Access: reads = any active member · writes = founder/team_member (mentor → 403
      `FORBIDDEN`) — same `require_workspace`/`_editor` split as Business Builder
- [x] Live E2E journey (`e2e/test_documents.py::test_documents_journey`, 9 captures: templates
      catalog + detail → create-from-template → get → full-replace edit (version bump) → stale-
      version 409 → folder-filtered list (summary shape confirmed) → delete → 404) + full existing
      suite re-run green (34 e2e, 961 unit) + smoke openapi surface
- [x] SOP + FE integration guide (every payload/status/error captured live except the 403 write-
      role row, cited from a passing unit test) + this checklist reconcile —
      `docs/sop/2026-09-09-documents-templates-slice1.md`,
      `docs/fe-integration-guide-documents-templates.md`
- [x] Slice 2 (Upload & Files) landed — Cloudinary-backed storage is now available; merged (PR #50).
- [x] Slice 3 (Sharing) landed — external tokenized share links are now available; merged (PR #53).
- [x] Slice 4 (E-signature) landed — tokenized-link signing is now available, completing Module 18;
      see below.
- [x] `business_plans.document_id` FK wired → **shipped, Module 08 §08.11 (AI Business Plan
      Generator)** — see that module's entry above
- [ ] _Deferred:_ no per-section endpoints (always full-replace `PUT`) · no `folders`
      table/folder management UI · no user-authored templates (registry is read-only, in-code) —
      see SOP Follow-ups

**Slice 2 — Upload & Files** — *✅ merged to `develop` (PR #50, Tasks 1–5)*
- [x] Scope + locked decisions (separate `document_files` table, not columns bolted onto
      `documents` · `Storage` protocol extended with `delete` · Cloudinary behind that protocol,
      `resource_type="raw"` for deterministic delete · allowlist-by-content-type + 15 MB cap,
      enforced server-side against actual streamed bytes, not a trusted `Content-Length` · no
      attachments-to-document FK in this slice) —
      `.superpowers/sdd/2026-09-10-documents-files-slice2/`
- [x] `Storage.delete` added to the protocol · `CloudinaryStorage` (`save`/`delete`, both
      `resource_type="raw"`) alongside the existing `LocalStorage` · `get_storage()` switches on
      `settings.STORAGE_BACKEND` (`app/platform/storage.py`)
- [x] `DocumentFile` model + migration `0017_document_files` (chains off `0016_documents`, sole
      alembic head) + standalone `startup_id`/`uploaded_by_id` indexes + composite
      `(startup_id, folder)` index
- [x] File service (`app/services/documents/files.py`) — `EXT_BY_CONTENT_TYPE` allowlist ·
      `upload_file` (storage key + save + row + `document.file.uploaded` event) · `list_files`
      (folder filter, newest-first) · `get_file` (tenant-scoped 404) · `delete_file` (storage
      delete + row delete + `document.file.deleted` event) · `serialize_file` (one shape, no
      summary/full split)
- [x] `POST /documents/files` (editor; `multipart/form-data`, field `file` + form field `folder`;
      201; allowlist/15 MB-cap violations → 422 `VALIDATION_ERROR`) · `GET /documents/files?folder=`
      (member; summaries) · `GET /documents/files/{id}` (member) · `DELETE /documents/files/{id}`
      (editor; `{deleted: true}`) — registered ahead of `/documents/{document_id}` so the literal
      `files` segment isn't shadowed (regression-tested)
- [x] Access: reads = any active member · writes = founder/team_member (mentor → 403 `FORBIDDEN`)
      — same `require_workspace`/`_editor` split as Slice 1
- [x] Live E2E journey (`e2e/test_documents.py::test_documents_files_journey`, 6 captures: upload
      (multipart PDF + folder) → folder-filtered list → get → disallowed-type 422 → delete → 404)
      + full existing suite re-run green (35 e2e, 978 unit) — both upload and delete confirmed to
      persist (`db.commit()` verified via a follow-up `GET` after each write)
- [x] SOP + FE integration guide (every payload/status/error captured live except the 403 write-
      role row and the 15 MB-cap row, both cited from passing unit tests) + this checklist
      reconcile — `docs/sop/2026-09-10-documents-files-slice2.md`,
      `docs/fe-integration-guide-documents-files.md`
- [ ] _Deferred:_ **DEPLOY FOLLOW-UP — set `STORAGE_BACKEND=cloudinary` +
      `CLOUDINARY_CLOUD_NAME`/`CLOUDINARY_API_KEY`/`CLOUDINARY_API_SECRET` in
      `.env.staging.enc`/`.env.production.enc` before this slice reaches either environment** —
      without it, uploads silently fall back to `LocalStorage` (ephemeral, not shared across
      replicas) with no startup-time warning · attachments-to-document FK (no `document_id` link
      from a file to a specific document yet) · no content/malware scanning · no file versioning
      (re-upload creates a new row, not an in-place update) · no per-file access level beyond the
      tenant's member/editor split — Slice 3 (Sharing) shipped external share links for
      structured `documents` only, not `document_files`; file sharing remains a follow-up (see
      Slice 3's own SOP Follow-ups) — see SOP Follow-ups

**Slice 3 — Sharing** — *✅ merged to `develop` (PR #53, Tasks 1–4)*
- [x] Scope + locked decisions (external expiring-link/token model, View/Comment only, mirroring
      the existing invitation-token pattern (`token_urlsafe(32)` + `hash_token` sha256 + uniform
      404) · `comment` tier stored but functionally `view` until a comment entity exists · `edit`
      not offered — anonymous edits can't be attributed in version history · shares target
      structured `documents` only, not `document_files` · email via the existing `EmailSender`
      seam, not blocked on Module 20) —
      `.superpowers/sdd/2026-09-12-documents-sharing-slice3/`
- [x] `ShareAccess` enum (`view`/`comment`) + `DocumentShare` model + migration
      `0018_document_shares` (chains off `0017_document_files`, sole alembic head) + standalone
      `startup_id`/`document_id`/`shared_by_id` indexes + unique `token_hash` + composite
      `(startup_id, created_at)` index
- [x] Sharing service (`app/services/documents/shares.py`) — `create_share` (token gen + hash +
      row + `document.shared` event, returns the raw token) · `list_shares` (per-document) ·
      `list_workspace_shares` (per-startup overview) · `revoke_share` (`document.share.revoked`
      event) · `open_shared` (hash lookup, uniform 404 for unknown/expired/revoked, sets
      `last_viewed_at`) · `serialize_share` (derives `status`, omits `token_hash`)
- [x] `POST /documents/{id}/shares` (editor; 201; JSON body `{email, access_level?,
      expires_in_days?}`; builds the link, emails it, response returns `link` **once**) · `GET
      /documents/{id}/shares` (member; per-document "Shared with" list, no `link`) · `DELETE
      /documents/{id}/shares/{share_id}` (editor; `{revoked: true}`) · `GET /documents/shares`
      (member; workspace "Shared with others" overview, `document_id` on each row; registered
      ahead of `/documents/{document_id}` so the literal `shares` segment isn't shadowed) · `GET
      /shared/{token}` (**public, no auth at all**; returns `{document, access_level,
      expires_at}`; uniform 404 unknown/expired/revoked; persists `last_viewed_at`)
- [x] Access: authenticated reads = any active member · authenticated writes = founder/team_member
      (mentor → 403 `FORBIDDEN`) — same `require_workspace`/`_editor` split as Slices 1–2; the
      public open route has no auth dependency at all, by design
- [x] Live E2E journey (`e2e/test_documents.py::test_documents_sharing_journey`, 6 captures:
      create (returns `link`, cross-checked against the raw link parsed out of the captured share
      email in the file mail dir) → public open with zero auth headers → per-document list
      (`last_viewed_at` now set) → workspace overview (`document_id` present) → revoke → public
      open → 404) + full existing suite re-run green (36 e2e, 992 unit)
- [x] SOP + FE integration guide (every payload/status/error captured live except the rows listed
      unit-only in that guide's verification table — non-editor 403, cross-tenant revoke,
      unknown/expired-token 404, and the `expires_in_days: 0`/`null` "never expires" case) + this
      checklist reconcile — `docs/sop/2026-09-12-documents-sharing-slice3.md`,
      `docs/fe-integration-guide-documents-sharing.md`
- [x] **FE-origin link fix (2026-09-19, PR #73)** — the emailed
      share link (and Slice 4's signing links) now build off `APP_BASE_URL` (the FE origin), falling
      back to `SERVER_HOST` only when unset, mirroring `auth/emails.py`. So `/shared/:token` and
      `/sign/:token` are the **FE routes** the app must serve; the emailed link opens them, and the
      FE page then calls `GET /api/v1/shared/{token}` / `GET`+`POST /api/v1/sign/{token}`. Verified
      live in the e2e (captured `share_email.json`/`signature_email.json` show the FE origin) + 4 new
      unit tests. `APP_BASE_URL` already set to the FE origin on staging/prod. —
      `docs/sop/2026-09-19-document-emails-fe-links.md`, both FE guides updated (§6/routes)
- [ ] _Deferred:_ Edit access tier + member-scoped ACL editing · Comment feature (tier stored, not
      yet functional) · sharing uploaded files (`document_files`, Slice 2) · wrap the share-create
      email send in `try`/`except` so a transient SMTP failure can't 500 an otherwise-valid create
      (Slice 4's signature-request email send was built correctly wrapped from the start — see its
      SOP "How") — see SOP Follow-ups

**Slice 4 — E-signature** — *✅ MERGED to `develop` (PR #55; Tasks 1–4) — completes Module 18*
- [x] Scope + locked decisions (build-your-own tokenized-link signing, not a third-party e-sign
      provider · sign uploaded files (`document_files`), not structured `documents` · typed-name
      simple signature + audit trail (name/timestamp/IP/user-agent), no drawn-signature image · no
      draft state — create sends immediately · default 14-day expiry · any-order signing, `position`
      display-only · migration `0020_signatures`, chaining off `0018_document_shares` with `0019`
      intentionally skipped, reserved for another engineer's Module 17 work) —
      `.superpowers/sdd/2026-09-14-documents-esignature-slice4/`
- [x] `SignatureRequestStatus` enum (`awaiting`/`complete`/`cancelled`, `expired` derived not
      stored) + `SignatureRequest`/`SignatureSigner` models + migration `0020_signatures` (chains
      off `0018_document_shares`, sole alembic head) + standalone `startup_id`/`file_id`/
      `created_by_id`/`request_id` indexes + unique `token_hash` + composite
      `(startup_id, created_at)` index
- [x] E-signature service (`app/services/documents/signatures.py`) — `create_request` (≥1-signer
      required else 422; fresh token per signer; `document.signature.requested` event) ·
      `list_requests`/`get_request` (tenant-scoped) · `cancel_request` (409 `SIGNATURE_NOT_ACTIVE`
      guard) · `unsigned_signers`/`reissue_unsigned` (remind rotates unsigned signers' tokens —
      the OLD link stops working) · `open_for_signing` (uniform 404
      unknown/expired/cancelled/complete/already-signed) · `record_signature` (audit fields;
      flips to `complete` + `completed_at` the instant every signer has signed) ·
      `request_status` (derives `expired`) · `serialize_request` (never emits `token_hash`,
      never emits `signed_ip`/`signed_user_agent` either — audit trail is captured, not exposed)
- [x] `POST /documents/files/{id}/signature-requests` (editor; 201; body `{signers, title?,
      expires_in_days?}`; response returns `signer_links` **once**) · `GET
      /documents/signature-requests` (member; full `signers` array per row, not summary-shaped) ·
      `GET /documents/signature-requests/{id}` (member) · `POST
      /documents/signature-requests/{id}/remind` (editor; rotates unsigned tokens, re-emails) ·
      `POST /documents/signature-requests/{id}/cancel` (editor; 409 if not active) · `GET`/`POST
      /sign/{token}` (**public, no auth at all**; view returns file + request + signer identity;
      sign takes `{typed_name}`, records IP/user-agent, returns the full updated request) —
      registered ahead of `/documents/{document_id}` so the literal `signature-requests` segment
      isn't shadowed
- [x] Access: authenticated reads = any active member · authenticated writes = founder/team_member
      (mentor → 403 `FORBIDDEN`) — same `require_workspace`/`_editor` split as Slices 1–3; both
      public signing routes have no auth dependency at all, by design
- [x] Live E2E journey (`e2e/test_documents.py::test_documents_esignature_journey`, 11 captures:
      upload → create 2-signer request (`signer_links` returned once, cross-checked against the
      captured signature emails) → view + sign each signer publicly (first stays `awaiting`,
      second flips to `complete`) → re-opening a signed token 404s → get + list both show `2 of 2`
      + `complete` → a second request created then cancelled, its signer's link 404s afterward) +
      full existing suite re-run green (37 e2e, 1016 unit)
- [x] SOP + FE integration guide (every payload/status/error captured live except the rows listed
      unit-only in that guide's verification table — non-editor 403, cross-tenant cancel, unknown-
      token 404, expired-clock derivation, remind's token rotation, and the already-complete 409
      guard) + this checklist reconcile — `docs/sop/2026-09-14-documents-esignature-slice4.md`,
      `docs/fe-integration-guide-documents-esignature.md`
- [ ] _Deferred:_ third-party provider for certificate-based signing (v1 is a simple electronic
      signature — typed name + audit trail, not notarized/certificate-based) · drawn-signature
      image · signing structured `documents` (needs a freeze-to-file step first) · ordered/
      sequential signing enforcement (`position` is display-only today) · decline-to-sign ·
      owner in-app notification on completion (events publish, unconsumed until Module 20) ·
      ~~same `SERVER_HOST`/FE-link gap as Slice 3~~ **FIXED 2026-09-19** — signing links now use
      `APP_BASE_URL` (FE origin); see the Slice 3 fix line above and
      `docs/sop/2026-09-19-document-emails-fe-links.md` ·
      the audit trail (`signed_ip`/`signed_user_agent`) is captured but never exposed via any API
      response · no generated "signed certificate" PDF for the comp's Download CTA — see SOP
      Follow-ups

## ✅ Module 20 — Notifications — *ALL 4 SLICES SHIPPED — MODULE 20 COMPLETE (In-App Feed PR #58;
Email + Preferences + Worker PR #60; Scheduler/Cron PR #71 (migrations `0024_scheduled_runs` →
`0025_roadmap_milestone_due_idx`); Real-Time SSE PR #74, no migration)* — 2026-09-19

_Module 20 decomposed into 4 slices (agreed 2026-09-14, `docs/superpowers/specs/
2026-09-14-notifications-feed-design.md`): **1 In-app feed + fan-out** (the platform event bus
becomes a real same-transaction dispatcher and ~15 domain events fan out to per-user rows — ✅
merged), **2 Email delivery + per-user preferences** (Resend backend already existed; this slice
adds the preferences model/endpoints, the in-transaction enqueue, and a new background `worker`
process — ✅ merged), **3 Scheduler/cron** (mission 06:00, roadmap-overdue, quarterly
re-assessment — enqueues into Slice 2's SAME `jobs` table/worker, no new infrastructure — ✅ built),
**4 Real-time (SSE) delivery** (a Redis pub/sub backplane + `after_commit` publish + a
`GET /notifications/stream` SSE endpoint, one-time ticket auth, no new infrastructure — ✅ built,
this pass — MODULE 20 NOW COMPLETE). Nearly every already-shipped module (Dashboard, Roadmap,
Mission, Health Score, Documents, Business Builder, Assessment, onboarding) had a "real notification
delivery — Module 20" deferred line in its own SOP; Slice 1 retired the in-app half, Slice 2 added
the email half, Slice 3 retired the "06:00 cron / overdue / quarterly re-assess" lines, and Slice 4
retires the remaining "no real-time delivery, the FE must poll" lines (device/closed-app push
remains a genuinely separate, still-unbuilt follow-up — see Slice 4's SOP Follow-ups). SOPs:
`docs/sop/2026-09-14-notifications-feed-slice1.md`,
`docs/sop/2026-09-15-notifications-email-delivery.md`,
`docs/sop/2026-09-18-notifications-scheduler.md`,
`docs/sop/2026-09-19-notifications-realtime-sse.md`._

**Slice 1 — In-App Feed + Fan-Out** — *🟢 MERGED to `develop` (PR #58, Tasks 1–6 +
final-review fix wave)*
- [x] Scope + locked decisions (real synchronous same-transaction event bus, not a queue — a
      notification exists iff the triggering action committed · per-handler `db.begin_nested()`
      savepoint + try/except so a notification bug never breaks the triggering action · data-driven
      registry, one file, ~15 rows · recipient default = active members minus actor, overridable per
      event · generic per-type copy, not per-instance rendering · in-app delivery only this slice ·
      migration `0021_notifications`, `0019` reserved/skipped for a concurrent Module 17 branch) —
      `.superpowers/sdd/2026-09-14-notifications-feed-slice1/`
- [x] `app/platform/events.py` — `EventBus.publish` gains a `db: Session` parameter and now
      dispatches to subscribed handlers inside a per-handler savepoint; `subscribe(event, handler)`
      added — 32 `event_bus.publish(...)` call sites (28 in `app/services/**`, 4 in
      `app/api/v1/endpoints/**`) mechanically updated to the new signature, behavior-preserving for
      every caller with no registered handlers
- [x] `Notification` model + migration `0021_notifications` (chains off `0020_signatures`, sole
      alembic head) + standalone `user_id`/`startup_id` indexes + composite
      `(user_id, startup_id, created_at)` index for the feed query
- [x] Notifications service (`app/services/notifications/service.py`) — `create_notifications`
      (bulk-insert per recipient) · `list_notifications` (keyset pagination on
      `(created_at, id) desc`, `limit` clamped `[1, 50]`) · `unread_count` · `mark_read` (404
      cross-user, idempotent) · `mark_all_read` (bulk update, returns count) ·
      `serialize_notification`
- [x] Registry (`app/services/notifications/registry.py`) — `SPECS` dict, 15 v1 handled events
      (`document.shared`, `document.signature.{requested,signed,completed}`,
      `business.suggestion.{created,approved,rejected}`, `business.artifact.completed`,
      `roadmap.replanned`, `roadmap.milestone.completed`, `mission.completed`,
      `mission.streak.milestone`, `healthscore.dropped`, `assessment.completed`,
      `workspace.member.joined`) · `register()` subscribes all 15 at import time, called from
      `app/api/v1/api.py`
- [x] `GET /notifications?unread=&limit=&cursor=` (feed, keyset pagination) · `GET
      /notifications/unread-count` (bell badge) · `POST /notifications/{id}/read` (404 if not the
      caller's row) · `POST /notifications/read-all` (`{marked: N}`) — all four scoped strictly to
      `(membership.user_id, membership.startup_id)`, verified user + `require_workspace`
- [x] Live E2E journey (`e2e/test_notifications.py::test_notifications_journey`, 19 captures):
      founder A invites teammate B (a REAL second active member) → B accepts → A shares a document
      twice → B's feed shows exactly 2 unread `document.shared` rows (`data.shared_by_id` = A) → A's
      own feed has **zero** `document.shared` rows (actor exclusion, fixed in `42e00ef` — see below)
      → keyset pagination (`limit=1` → non-null `next_cursor` → the other row + `next_cursor: null`)
      → mark-read (idempotent) → unknown-id 404 → **cross-user 404** (A's own `workspace.member.
      joined` row 404s for B) → `read-all` → unread-count → 0 → full feed still shows both rows, now
      `read: true` + full existing suite re-run green (38 e2e, 1036 unit)
- [x] SOP + FE integration guide (every payload/status/error captured live except the 13 event
      types' `data` shape and a handful of shared-dependency rows, all cited in that guide's
      verification table) + this checklist reconcile —
      `docs/sop/2026-09-14-notifications-feed-slice1.md`,
      `docs/fe-integration-guide-notifications.md`
- [x] **Final-review fix wave (`42e00ef`)** — fixed the actor-exclusion known gap above: added an
      actor-identifying payload key (`shared_by_id`/`created_by`/`actor_id`) at the 6 publish sites
      for events with a genuine member actor (`document.shared`, `document.signature.requested`,
      the 3 `business.suggestion.*` events, `roadmap.milestone.completed`) and recognized
      `roadmap.replanned`'s existing `applied_by` key in `_actor()` — 7 events now genuinely exclude
      the actor; confirmed live (`e2e/_captures/notifications/actor_excluded_from_own_action.json`,
      superseding the deleted `actor_not_excluded_known_gap.json`) and by a new unit test using the
      real `document.shared` payload shape. Passive/system events (missions, health score, signature
      completion, etc.) intentionally left notify-all — no member actor exists to exclude. Also
      hardened `create_notifications` to give each fanned-out row its own `dict(data)` copy instead
      of sharing one dict object. 1036 unit / 38 e2e green, `ruff`/`black`/`mypy` clean. SOP + FE
      guide updated in the same pass.
- [ ] _Deferred:_ richer per-type/per-instance titles (today: one fixed string per event type) ·
      notification grouping/digest · ~~Slices 3–4 (scheduler/cron, real-time/push)~~ both since
      shipped (Slice 3 above; Slice 4, `docs/sop/2026-09-19-notifications-realtime-sse.md`) — see SOP
      Follow-ups

**Slice 2 — Email Delivery + Preferences + Worker** — *✅ MERGED to `develop` (PR #60; migration
`0022_notifications_email`; the minimal job worker other slices/modules now build on)*
- [x] Scope + locked decisions (in-transaction enqueue at the same fan-out point Slice 1 already
      writes in-app rows from — a rolled-back triggering action enqueues no email, same guarantee
      as the in-app row · a separate background `worker` process claims + sends, never inline in
      the request · opt-out preferences model, `master_email` + 5 categories, all default ON ·
      at-least-once email delivery accepted as a waiver, not built around · migration
      `0022_notifications_email`, chains off `0021_notifications`, sole head) —
      `.superpowers/sdd/2026-09-15-notifications-email-delivery/`
- [x] `notification_preferences` table (`user_id`/`startup_id` FKs, unique per pair,
      `master_email` bool default true, `categories` JSONB default `{}`) + `jobs.attempts`/
      `jobs.run_after` columns, migration `0022_notifications_email`
- [x] Category catalog (`app/services/notifications/categories.py`) — 5 categories (`documents`,
      `business`, `roadmap_missions`, `health_assessment`, `team`) covering all 15 v1 event types,
      same map powers both the preferences gate and Slice 1's email deep-link path
- [x] Preferences service (`app/services/notifications/preferences.py`) — `effective_preferences`
      (defaults-merged, always all 5 keys present) · `set_preferences` (genuine partial merge) ·
      `email_enabled` (the enqueue-time gate: `master_email AND categories[category]`)
- [x] `GET`/`PUT /api/v1/notifications/preferences` — verified user + `require_workspace`, same
      auth convention as Slice 1's 4 routes; unknown category key → `422 VALIDATION_ERROR`
      (request-level `field_validator`, nothing partially applied)
- [x] Registry enqueue (`app/services/notifications/registry.py::_handle`) — captures
      `create_notifications`'s return value, enqueues one `email.notification` job per opted-in
      recipient, in the SAME transaction/savepoint as the in-app rows
- [x] Worker (`app/worker/`) — `runner.py` (claim via `SELECT ... FOR UPDATE SKIP LOCKED`,
      per-job `db.begin_nested()` isolation, exponential backoff capped at 1h,
      `WORKER_MAX_ATTEMPTS`=5 terminal failure, stale-`RUNNING` reaper) · `handlers/email.py`
      (re-fetches the notification + recipient, HTML-escapes title/body, validates the deep-link
      URL scheme) · `__main__.py` (poll loop, graceful `SIGTERM`/`SIGINT` shutdown) · new `worker`
      service in `docker-compose.yml`/`docker-compose.prod.yml` (dev: `build:`; prod: `image:`,
      0.5 CPU/512M limit, no published ports) · `DEPLOYMENT_GUIDE.md` resource/connection tables
      updated
- [x] Live E2E journey (`e2e/test_notifications_email.py::test_email_delivery_and_preferences`, 4
      captures): mirrors Slice 1's A/B setup verbatim → B's preferences default all-ON → A shares a
      document → queue drained in-process (looped `run_once` — the shared e2e `jobs` table has a
      backlog from every earlier test, so a single batch under-drains) → B's mailbox has the new
      email, subject verified, captured (`delivered_email.json`) → B turns `documents` email OFF
      (`preferences_documents_off.json`) → A shares again → drain again → B's mailbox count
      UNCHANGED (no new job enqueued) → `GET /preferences` reflects the toggle
      (`preferences_get.json`) → unknown category `PUT` → `422`, captured
      (`preferences_put_unknown_category_422.json`) · full suite re-run green (39 e2e, up from 38)
- [x] SOP + FE integration guide extension (every payload/status/error captured live) + this
      checklist reconcile — `docs/sop/2026-09-15-notifications-email-delivery.md`,
      `docs/fe-integration-guide-notifications.md` §9 "Preferences & email (Slice 2)"
- [x] Full local CI reproduction green before commit: `black`/`isort`/`ruff` (12 pre-existing
      unformatted files from Tasks 1–7 fixed in this pass, plus one `ruff` `C420` finding in
      `categories.py` and one `mypy` missing-annotation finding in `worker/__main__.py`'s SIGTERM
      handler) · `mypy` clean · `pylint` 9.89/10 (≥ 9.5 floor) · `bandit` clean · 1057 unit passed,
      97.66% coverage (≥ 95% floor) · exactly one alembic head · 39 e2e passed — see
      `.superpowers/sdd/2026-09-15-notifications-email-delivery/task-8-report.md` for the full
      per-gate breakdown
- [ ] _Deferred:_ at-least-once email delivery (a worker crash between a successful Resend send and
      its `_finalize_success` commit can re-send — accepted waiver, mitigated later by an
      idempotency key if it becomes a real problem) · preferences are not retroactive (no "cancel a
      pending email" path) · no per-notification email-delivery status exposed via the API · same
      generic per-type (not per-instance) copy limitation as Slice 1 — see SOP Follow-ups

**Slice 3 — Scheduler / Cron** — *✅ MERGED to `develop` (PR #71;
migrations `0024_scheduled_runs` → `0025_roadmap_milestone_due_idx`, chain off
`0023_learning`; `0025` is the sole head)* — 2026-09-18
- [x] Design + decisions (`.superpowers/sdd/2026-09-18-notifications-scheduler/`, design doc
      `docs/superpowers/specs/2026-09-18-notifications-scheduler-design.md`) — scheduler = a
      throttled tick inside the existing `worker` loop, no new container · a DB claim ledger
      (`scheduled_runs`, unique `(task_key, scope_key, period_key)`) makes firing once-per-period
      safe, leader-free · tick claims + enqueues into Slice 2's SAME `jobs` table; handlers do the
      work + publish · one config timezone (`SCHEDULER_TIMEZONE`) for the 06:00 check, per-workspace
      tz deferred · overdue fires once per milestone, ever (no daily/weekly re-nudge) · quarterly
      only for workspaces with a prior completed assessment
- [x] `scheduled_runs` ledger table + migration `0024_scheduled_runs` (`app/db/models/
      scheduled_run.py`)
- [x] `app/worker/scheduler.py` — `_claim` (once-per-period, savepoint insert), three detectors
      (`_due_missions` past `MISSION_GEN_HOUR`; `_due_overdue_milestones` past `due_on` + not done;
      `_due_quarterly` last completed assessment ≥ `QUARTERLY_REASSESS_DAYS` old, excluding
      in-progress and soft-deleted startups), `scheduler_tick(db, *, now)` — per-item isolated,
      commits once, returns count enqueued
- [x] `app/worker/handlers/scheduled.py` — three job handlers (`handle_mission_generate`,
      `handle_roadmap_overdue`, `handle_assessment_quarterly`), each re-checking before publishing
      (overdue re-fetches the milestone — no stale-notification publish if it was completed/deleted
      between enqueue and run) — publish `mission.ready`, `roadmap.milestone.overdue`,
      `assessment.quarterly.due`
- [x] `app/worker/__main__.py::main_loop` — throttled `scheduler_tick` call (`SCHEDULER_INTERVAL`,
      default 60s, monotonic-clock gated) alongside the existing `run_once` poll
- [x] Registry (`app/services/notifications/registry.py`) — 3 new `SPECS` rows, all-active-members
      recipients (no actor — these are scheduled/system events); categories
      (`app/services/notifications/categories.py`) — `mission.ready`/`roadmap.milestone.overdue` →
      `roadmap_missions`, `assessment.quarterly.due` → `health_assessment` (both pre-existing
      categories, no 6th category added)
- [x] Config (`app/core/config.py`) — `SCHEDULER_TIMEZONE` (default `UTC`), `MISSION_GEN_HOUR`
      (default `6`), `SCHEDULER_INTERVAL` (default `60`), `QUARTERLY_REASSESS_DAYS` (default `90`)
- [x] Whole-branch-review perf hardening (F1/F2) — detectors pre-filter already-claimed scopes so a
      steady-state tick issues no doomed re-`INSERT`s (overdue de-duped in SQL via `NOT EXISTS`;
      missions/quarterly via a bounded in-memory set; `_claim` retained as the concurrency backstop)
      + migration `0025_roadmap_milestone_due_idx` indexes `roadmap_milestones(due_on)` for the
      overdue detector's per-tick range filter — now the sole alembic head
- [x] Live E2E journey (`e2e/test_notifications_scheduler.py::
      test_scheduled_mission_ready_notification`, 1 capture): founder onboards to a generated
      roadmap → scheduler tick at 07:00 UTC (past `MISSION_GEN_HOUR`) → worker queue drained
      in-process → `mission.ready` notification in the founder's feed, `data == {startup_id,
      mission_id}`, captured (`mission_ready_feed.json`) → a same-day re-tick does NOT duplicate it
      (proves the claim ledger). `roadmap.milestone.overdue`/`assessment.quarterly.due` are NOT
      reachable from a fresh e2e signup (need a backdated milestone / an assessment >90 days old) —
      honestly labelled unit-only in the FE guide + SOP rather than faked, backed by
      `tests/worker/test_scheduled_handlers.py`'s exact-payload assertions
- [x] SOP + FE integration guide extension (new §10 "Scheduled / time-based notifications") + this
      checklist reconcile — `docs/sop/2026-09-18-notifications-scheduler.md`,
      `docs/fe-integration-guide-notifications.md` §10
- [x] Full local CI reproduction green before commit: `black`/`isort`/`ruff` (5 pre-existing
      unformatted files from Tasks 1–6 fixed in this pass, plus one `ruff` `F401` finding and 4
      `mypy` missing-annotation findings in `app/worker/scheduler.py`'s detectors) · `mypy` clean ·
      `pylint` 9.89/10 (≥ 9.5 floor, unchanged — no new findings in scheduler code) · `bandit` clean
      · 1199 unit passed, 97.76% coverage (≥ 95% floor) · exactly one alembic head
      (`0024_scheduled_runs`) · 41 e2e passed — see `.superpowers/sdd/
      2026-09-18-notifications-scheduler/task-7-report.md` for the full per-gate breakdown
- [ ] _Deferred:_ single global `SCHEDULER_TIMEZONE`, not per-workspace (D3 — no tz field on
      `Startup` today) · overdue fires once per milestone ever, no recurring re-nudge (D5) ·
      quarterly only for workspaces with a prior completed assessment, never for a
      never-assessed workspace (D6) · roadmap TASK overdue out of scope, milestones only · the
      claim/enqueue non-atomicity gap (safe for mission generation via its lazy fallback, not
      mitigated for overdue/quarterly — see SOP "How") · an overdue/quarterly job that exhausts
      `WORKER_MAX_ATTEMPTS` leaves its `"once"` claim in place, so that occurrence never re-fires
      (F3 — within the D5 once-ever contract; a future worker-DLQ/alerting slice should surface a
      terminal-failed `scheduled.*` job rather than this loop retrying forever) — see SOP Follow-ups

**Slice 4 — Real-Time SSE Delivery** — *✅ MERGED to `develop` (PR #74;
no migration — sole alembic head unchanged, `0025_roadmap_milestone_due_idx`)* —
2026-09-19, **MODULE 20 NOW FULLY COMPLETE (all 4 slices)**
- [x] Design + decisions (`.superpowers/sdd/2026-09-19-notifications-realtime-sse/`, design doc
      `docs/superpowers/specs/2026-09-19-notifications-realtime-sse-design.md`) — Server-Sent Events
      over a Redis pub/sub backplane, not websocket (one-way server→client, `EventSource` gives
      auto-reconnect for free) · publish happens in a SQLAlchemy `after_commit` listener so an event
      fires only for an actually-committed row, across every caller (request/worker/scheduler) ·
      one-time ticket auth (native `EventSource` can't set `Authorization`; a token in the URL would
      leak into logs) · reconcile-not-replay (no `Last-Event-ID`/server event log — FE re-fetches the
      feed on reconnect) · fail-soft publish (SSE is a live optimization, the DB row + email job stay
      the durable path) · device/closed-app push explicitly out of scope, a separate later slice
- [x] Realtime seam (`app/platform/realtime.py`, new) — `channel_for(startup_id, user_id)` ·
      fail-soft `publish_notification` (sync, via the existing `get_redis()` client) ·
      `mint_stream_ticket`/`consume_stream_ticket` (one-time `SET NX EX` / `GETDEL` ticket pair,
      `SSE_TICKET_TTL`) · async `subscription(channel)` context manager over a lazily-imported
      `redis.asyncio` pubsub (no new dependency — already ships with `redis`)
- [x] Publish-on-commit (`app/services/notifications/service.py` + `app/db/session.py`) —
      `create_notifications` stashes one `(channel, payload)` per created row in
      `db.info["pending_realtime"]` after flush; new `after_commit`/`after_rollback` listeners on
      `SessionLocal` publish the stash (or drop it, no phantom events, on rollback) — automatically
      covers every notification-creating call path with zero other call-site changes
- [x] `POST /notifications/stream-ticket` (mints a ticket for the caller's own
      `(user_id, startup_id)`) · `GET /notifications/stream?ticket=...` (consumes the ticket, 401
      `INVALID_TICKET` if bad/expired/reused; re-checks active membership, 403 `FORBIDDEN`; then an
      SSE `StreamingResponse` — initial authoritative `event: unread`, live `event:
      notification.created` frames identical in shape to a `GET /notifications` row via the existing
      `serialize_notification`, `: heartbeat` comments every `SSE_HEARTBEAT_INTERVAL`,
      `X-Accel-Buffering: no`, no request-scoped DB session held open across the stream)
- [x] Config (`app/core/config.py`) — `SSE_TICKET_TTL` (default 30s), `SSE_HEARTBEAT_INTERVAL`
      (default 20s)
- [x] Live E2E journey (`e2e/test_notifications_realtime.py::
      test_realtime_notification_delivery`, 2 captures) — the one path a fake-pubsub unit test
      cannot prove: a founder opens the SSE stream against the SERVER process; the TEST process then
      creates + commits a notification, which fires `after_commit` IN THE TEST PROCESS → Redis
      `PUBLISH` → the server process's already-subscribed stream forwards it down the open
      connection — a genuine two-OS-process round trip through Redis, not two objects sharing a fake.
      Hard `t.join(timeout=8.0)` + `assert not t.is_alive()` so a stalled stream fails loudly rather
      than hangs the suite. Captured `stream_ticket.json` (ticket mint) + `stream_frame.json` (the
      received `notification.created` frame)
- [x] SOP + new FE integration guide + this checklist reconcile —
      `docs/sop/2026-09-19-notifications-realtime-sse.md`,
      `docs/fe-integration-guide-notifications-realtime.md` (new, cross-linked from
      `docs/fe-integration-guide-notifications.md`'s §0/§7, whose stale "no real-time delivery"
      claims are corrected in place rather than left to silently rot)
- [x] Full local CI reproduction green before commit: `black`/`isort`/`ruff` (2 pre-existing
      unformatted files fixed in this pass — `tests/api/notifications/test_stream.py`, left
      unformatted by Task 3's narrower per-file check, and the new e2e test file) · `mypy app` clean
      (156 files) · `pylint` 9.89/10 (≥ 9.5 floor, unchanged — this task added no `app/` code) ·
      `bandit` clean, 0 findings · **1227 unit tests passed, 97.36% coverage** (≥ 95% floor) ·
      exactly one alembic head (`0025_roadmap_milestone_due_idx`, unchanged from `develop` — no
      migration in this slice) · **43 e2e passed** (up from 42) — see `.superpowers/sdd/
      2026-09-19-notifications-realtime-sse/task-4-report.md` for the full per-gate breakdown
- [ ] _Deferred:_ device/web push (VAPID/FCM/APNs) for a CLOSED app — the other half of "push",
      genuinely unbuilt, its own later slice · no `Last-Event-ID` replay / server-side event log
      (reconcile-via-refetch only) · one Redis pubsub connection per open SSE stream, not a shared
      per-node subscriber (fine at current scale, a scaling optimization if connection counts grow) ·
      no read-state fan-out across a user's own open tabs — see SOP Follow-ups

## ✅ Module 17 — Learning Academy — *MERGED to `develop` (PR #59; migration `0023_learning`)*

_A learning hub inside the workspace: a read-only, versioned **in-code catalog** of courses
(ordered lessons), learning paths and articles — **labelled placeholder content; real content is
required before go-live** — plus per-person, per-workspace enrolments, lesson progress and
certificates. Eight routes under `/learning`, founders and team members only (reads included).
Deterministic recommendations with continue watching, race-safe automatic enrolment, derived course
and path progress. Migration `0022_learning`. Spec:
`docs/superpowers/specs/2026-09-11-learning-academy-design.md` (decisions D1–D10 agreed with the
lead). SOP: `docs/sop/2026-09-14-learning-academy.md`._

**Build (Tasks 1–6):**
- [x] Scope + locked decisions D1–D10, agreed with the lead on GitHub (in-code catalog, moving to
      the DB with Module 25.4 · deterministic recommendations, Health Score key deferred ·
      certificate record + credential code + job stub, no PDF · lesson notes deferred · continue
      watching inside recommendations · founders + team members only · enrolment unique per
      workspace · labelled placeholder catalog · auto-enrol on lesson completion · course % and
      path % formula) — spec + plan under `docs/superpowers/`
- [x] `CourseLevel` enum + `Enrollment`/`LessonProgress`/`Certificate` models + migration
      `0022_learning` (chains off `0021_notifications`, so the chain runs `0018` → `0020` → `0021`
      → `0022`; sole alembic head, `alembic check` clean) — unique `(startup_id, user_id,
      course_id)` on enrolments and certificates, unique `(startup_id, user_id, lesson_id)` on
      lesson progress, unique `credential_code`, `ck_enrollments_progress_range` (0–100), `CASCADE`
      FKs + `startup_id`/`user_id` indexes
- [x] In-code catalog (`app/services/learning/catalog.py`, `LEARNING_CATALOG_VERSION = 1`) — 6
      stage-tagged courses (one per stage, three levels), 2 paths, 2 articles, every title prefixed
      `[Placeholder] ` · stable ids · lesson ids unique catalog-wide · durations derived
- [x] Service write path (`app/services/learning/service.py`) — `get_or_create_enrollment`
      (SAVEPOINT + re-select on `IntegrityError`, as `get_or_create_canvas`) · `complete_lesson`
      (auto-enrol + enrolment row lock `FOR UPDATE`, so concurrent completions can't store a stale
      %) · course % = `round(100 × completed ÷ total)` · one certificate at 100%
      (`secrets.token_urlsafe(16)`) + `learning.course.completed` event +
      `learning.certificate.generate` job
- [x] Service read path — `recommended_courses` (stage filter, completed excluded,
      `RECOMMENDATION_SORT_KEYS` level → catalog order; no stage → beginner courses) ·
      `continue_watching` · path % = `round(mean of course %)`, unenrolled = 0, halves to even ·
      course / path / article views
- [x] 8 routes — `GET /learning/recommendations` · `GET /learning/courses` ·
      `GET /learning/courses/{course_id}` · `GET /learning/paths` · `GET /learning/articles` ·
      `POST /learning/enrollments` (201 first time, 200 on a repeat) ·
      `PATCH /learning/lessons/{lesson_id}/progress` (`completed: true` only, `false` → 422;
      `certificate` key always present) · `GET /learning/certificates` — both writes `db.commit()`
- [x] Access: every route = founder/team_member (mentor, accountant, legal_advisor,
      business_consultant, investor → 403) + verified email; progress is personal and per
      workspace — access matrix and isolation unit-tested
- [x] Tests — 124 learning unit/API tests (models 7 · migration 2 · catalog 6 · service 13 ·
      concurrency 3 · browse 11 · API 82) · full project suite on the latest `develop`: 1,160
      passed, coverage 97.97% (floor 95) · ruff / black / mypy clean
- [x] Smoke openapi surface — the 8 learning routes added to `e2e/test_smoke.py`
- [ ] Live E2E journey (`e2e/test_learning.py`, written: onboard → recommendations → catalog +
      course → enrol 201/200 → continue watching → lesson 1 = 50% → lesson 2 = 100% + certificate
      → shelf and continue watching cleared → paths / articles / certificates; 11 captures) —
      **not yet run**; how to run the e2e suite on Windows is with the lead
- [x] SOP — `docs/sop/2026-09-14-learning-academy.md`
- [ ] FE integration guide (`docs/fe-integration-guide-learning.md`) — waits on the live e2e
      captures
- [ ] _Deferred:_ **Replace the placeholder catalog with real content — REQUIRED before go-live** ·
      move the catalog into the DB when Module 25.4 lands · Health Score signal as a
      recommendation sort key · PDF rendering, sharing and a public certificate verification
      endpoint (jobs are enqueued, nothing renders them) · private lesson notes ·
      ~~AI recommendations + reason line (Module 03)~~ · notifications (Module 20) · video hosting
      (`video_ref` only) · un-completing a lesson — see SOP Follow-ups
- [x] **AI shelf-level `recommendation_reason` (Module 03 deferred AI upgrade)** — shipped
      2026-09-23 on `feat/module-03-deferred-ai-upgrades` (migration `0031_learning_recommendations`,
      `ai.learning.recommendations` worker). Struck through above; see its own reconcile entry
      under "Upcoming" and SOP `docs/sop/2026-09-23-deferred-ai-upgrades.md`. Per-course reasons
      and a Health-Score-weighted recommendation sort key remain unbuilt follow-ups.

## 🟢 Module 09 — Validation Hub — *shipped on branch `feat/validation-hub`, not yet merged*

_The "prove it before you build it" workspace: assumptions on a four-column board (untested →
testing → validated/invalidated), experiments and smoke tests with their metrics, interview notes,
and surveys answered **by the public**. Sixteen member routes under `/api/v1/validation` for
founders and team members only, plus **two unauthenticated routes** — read a survey form by token,
and submit an answer set. Migration `0035_validation` (renumbered twice as 03/08/10 merged ahead).
Spec `docs/superpowers/specs/2026-09-20-validation-hub-design.md` (D1–D10 agreed with the lead),
plan `docs/superpowers/plans/2026-09-20-validation-hub.md`, SOP
`docs/sop/2026-09-24-validation-hub.md`._

**Build (Tasks 1–7):**
- [x] Scope + locked decisions D1–D10 agreed with the lead (token-addressed public endpoint with a
      public read alongside it · 20/minute per-route rate limit · anonymous responses, repeats
      allowed · MVP feedback cut from v1 · AI synthesizer and script generation stubbed as enqueued
      jobs · JSONB question schema with caps · free assumption transitions, events only on a real
      change · derived evidence counts · JSONB assumption links)
- [x] Six enums + five models (`assumptions`, `experiments`, `interviews`, `surveys`,
      `survey_responses`) + migration `0035_validation` (chains off `0034_campaigns_segments`, sole
      alembic head, `alembic check` clean) — per-table `startup_id` indexes, unique
      `surveys.token_hash`, CASCADE FKs, server-side defaults, **no `user_id` on responses**
- [x] Question/answer validation (`app/services/validation/questions.py`) — types
      `choice|scale|nps|open`, server-assigned stable ids kept across edits, caps of 50 questions,
      20 options and 4 000 characters per open answer; every message safe to show the public
- [x] Assumptions + experiments service — free status transitions publishing
      `validation.assumption.validated|invalidated` only on an actual change into those states ·
      `evidence_counts` derived on read from linked experiments and interviews · `link_ids`
      rejecting assumptions outside the workspace (422) · smoke-test conversion returning 0.0
      rather than dividing by zero
- [x] Interviews + surveys service — filters by segment, verdict and assumption; survey token
      minted on first open, only its SHA-256 hash stored, raw value returned **once**; analytics
      with per-option counts (including unpicked options), scale/NPS counts + averages, answered
      counts for open questions, and the completion rate
- [x] Public surface (`app/services/validation/public.py`) copying Module 18's share/sign pattern —
      uniform 404 for unknown/draft/closed, acknowledgement-only reply, nothing about the
      respondent stored beyond `submitted_at`
- [x] **`Limiter` moved from `app/main.py` to `app/core/rate_limit.py`** (behaviour unchanged) so
      endpoint modules can carry per-route limits without a circular import; the public submit
      route takes the project's first one at `20/minute`. `tests/api/test_rate_limit_key.py` had
      its import updated to the new home
- [x] 16 member routes + 2 public routes + schemas + router registration at `prefix="/validation"`;
      write handlers commit
- [x] Tests — 144 validation tests (models 6 · migration 2 · questions 8 · assumptions 8 ·
      experiments 8 · interviews 6 · surveys 7 · public 7 · API 92) · full project suite **1,616
      passed**, coverage **97.20%** (floor 95) · ruff / black / mypy clean
- [x] Smoke openapi surface — the fourteen validation route shapes added to `e2e/test_smoke.py`
- [ ] Live E2E journey (`e2e/test_validation.py`, written: onboard → assumption → survey built and
      opened → **public form read and answered with no authentication** → wrong token 404 →
      analytics → experiment + interview linked → assumption validated with evidence count 2; 12
      captures) — **not yet run**; the e2e runner needs Poetry on the host, so CI runs it first
- [x] SOP — `docs/sop/2026-09-24-validation-hub.md`
- [ ] FE integration guide (`docs/fe-integration-guide-validation.md`) — written from the response
      builders with a provenance note; **regenerate from the real captures after the first e2e run**
- [ ] _Deferred:_ AI Insight Synthesizer + interview-script generation (stubs enqueue
      `validation.synthesize` / `validation.scripts.generate`, nothing drains them until Module 03)
      · hosted smoke-test pages · MVP feedback + theme clustering (PRD 09.7) · a members-only route
      to read raw open answers · delete routes · survey token rotation/expiry and response
      de-duplication · a join table for assumption links · notifications (Module 20) subscribing to
      the validation events — see SOP Follow-ups

## ✅ Deployment & Infrastructure — *on `chore/production-deployment-hardening` (PR #18, open)*

_Production docker/compose hardening, CI/CD pipeline rework, and a real readiness endpoint —
verified locally, and the CI workflows have now had a first real run on GitHub
(which found four defects, since fixed). Nothing has touched a real VPS. See
`docs/sop/2026-08-27-production-deployment-hardening.md`._

- [x] `docker-compose.prod.yml` — no host-published Postgres/Redis, `migrate` one-shot gated by
      `service_completed_successfully`, resource limits, json-file log rotation, own compose
      project name so `down -v` can't touch the dev stack's volumes. **Superseded 2026-08-29:**
      the project name is now `${COMPOSE_PROJECT_NAME:-cofoundaz-api-prod}` and the fixed
      `container_name:` keys are gone, so one file serves both stacks — see the nginx section
- [x] `docker-compose.yml` made honestly dev-only
- [x] Production `Dockerfile` rewritten — Poetry 2.2.1 (was 1.8.4, couldn't read the
      lock-version 2.1 lockfile), tini + gunicorn/uvicorn workers, `libpq-dev`/pip/setuptools/wheel
      removed from the runtime image (396 MB → 376 MB)
- [x] `poetry.lock` un-gitignored and git-staged for reproducible builds
- [x] `.dockerignore` inverted to deny-by-default; `.env.production.example` added; real-looking
      `SECRET_KEY` in `.env.example` replaced with a placeholder
- [x] CI (`ci.yml`): 1 job → **10**. First pass took it to 6 — `lint` (now incl. mypy), `test` on Postgres 17 with a 95%
      coverage gate, `migrations` (single-head + fresh upgrade + `alembic check` + downgrade
      round-trip), `e2e`, `security` (gitleaks + Semgrep + pip-audit), `build` (image + smoke +
      Trivy); plus a shared `setup-python-poetry` composite action, SHA-pinned actions,
      least-privilege `permissions:` and concurrency groups. The scanning wave added 4 more —
      `quality`, `trivy-repo`, `dependency-review`, `sonarcloud` — leaving 8 jobs fully parallel,
      `sonarcloud` on `needs: [test]`, and `build` on
      `needs: [lint, quality, test, migrations, e2e, security, trivy-repo]`
- [x] **CI restructured to be event-dependent** (2026-09-03) — `sonarcloud` and `trivy-repo`
      removed outright (see the cost-model comment atop `ci.yml`: `sonarcloud` was inert with no
      `SONAR_TOKEN` and had never produced a finding; `trivy-repo` duplicated `pip-audit` on CVEs
      and duplicated the image scan already inside `build`), leaving **8 jobs** split by what
      each *event* can actually prove rather than all 8 firing on both `push` and `pull_request`.
      `lint` / `test` / `security` run on every push to `develop` **and** on a non-draft PR into
      `develop` (~5 min) — the safety net for a direct push that bypasses PR review entirely.
      `migrations` / `e2e` / `quality` / `build` / `dependency-review` run **PR-only** (~+6 min);
      re-running them on the merge commit would re-test a tree that was green minutes earlier.
      `build` now needs `[lint, quality, test, migrations, e2e, security]` (`trivy-repo` dropped
      from the list along with the job). Draft PRs cost nothing — every job carries an explicit
      `github.event.pull_request.draft == false` guard, and GitHub does not skip drafts on its
      own. `main` is absent from every trigger in this file: a PR from `develop` to `main` runs
      nothing, and neither does a push to `main` (that fires `cd-production.yml` instead) —
      because `main` is required to be a fast-forward of `develop`, the tree arriving there
      already passed the full suite once, and re-running it would bill for testing the same tree
      a third time. Previously ~100 runs across 3 days exhausted the private repo's 2,000
      min/month Free-tier allowance and every job started failing in 2 seconds with zero steps —
      indistinguishable from a real failure unless you know to check the billing page. See
      `docs/deployment/BRANCHING.md`
- [x] `GET /api/v1/health/ready` — checks Postgres + Redis, `503` naming the failed dependency,
      no DSN leak (7 tests); `/health` kept as a cheap liveness check
- [x] Security-exception scaffolding — `.trivyignore` / `.github/security/pip-audit-ignores.txt`
      created, currently suppress nothing
- [x] Semgrep (CI ruleset, severity ERROR) verified clean locally — exit 0, no findings
- [ ] **Code scanning** — `codeql.yml` authored, `actionlint` clean, **NEVER EXECUTED**
      (CodeQL cannot run locally). Own workflow: needs a weekly cron, which in
      `ci.yml` would run the whole pipeline weekly). Python `security-and-quality`
      suite, report-only. Complements Semgrep: CodeQL does interprocedural
      dataflow/taint tracking, Semgrep is single-window pattern matching
- [x] **Quality scanning** — `quality` job: pylint wired up at last (it was a
      declared dev dep CI never ran) with a scoped `[tool.pylint]` config,
      **9.94/10** measured, gate `--fail-under=9.5`; radon complexity/MI report
      (301 blocks, average **A (2.30)**, every module MI **A**); hadolint on the
      Dockerfile at the strictest threshold
- [x] Complexity gate via ruff `C901` (`max-complexity = 12`; measured worst is
      `validate_answer` at 11) — one enforcer, radon reports only
- [x] **bandit** Python SAST in the `security` job — 3 findings, all false
      positives, fixed with inline `# nosec` + written reasons; now exits 0
- [x] **hadolint** — DL3066 FIXED (`USER 1000:1000`, not ignored); only DL3008
      ignored, with justification in `.hadolint.yaml`. Exits 0
- [x] **Trivy beyond the image** — `trivy fs` (report-only; finds the locked graph
      incl. unfixable `ecdsa CVE-2024-23342`, plus 0 secrets) and `trivy config`
      (BLOCKS; **0 misconfigurations** on the hardened Dockerfile)
- [x] **SBOM** — CycloneDX 1.7 from the built image, **174 components** (173 library +
      1 operating-system), ~301 KB — generated and counted locally
- [ ] SBOM as a *workflow artifact* (`sbom-cyclonedx-<sha>`, 90-day retention) —
      **NOT VERIFIED**, never uploaded; likewise the SLSA provenance + SBOM
      attestations CD attaches to the GHCR manifest, since no GHCR push has happened.
      cosign signing was deliberately declined — CD deploys the digest it just built,
      and without a `cosign verify` gate that can refuse a deploy a signature is ceremony
- [ ] **`dependency-review`** job on pull requests — authored (blocks a PR that
      *introduces* a high-severity or copyleft-licensed dependency); **NEVER EXECUTED**,
      it needs a real PR to diff a manifest against a base commit
- [ ] **`.github/dependabot.yml`** — pip / github-actions / docker, authored; what keeps
      the SHA and digest pins moving rather than rotting (pinning *without* an update
      mechanism is strictly worse than not pinning). **NEVER RUN** — needs enabling, and
      the Docker digest PRs specifically need confirming (see follow-ups)
- [x] Distinct SARIF `category:` per scan mode (semgrep / codeql / trivy-fs /
      trivy-config / trivy-image) so Security-tab results don't overwrite
- [x] All **18** Action SHA pins verified against the live GitHub API — zero
      mismatches; every action input validated against its pinned `action.yml`
- [x] `make quality` / `make scan` / `make sbom` reproduce the CI scanning locally —
      `make quality` exit 0; `make scan` runs 6 stages (bandit PASS, Semgrep PASS,
      gitleaks "no leaks found", `trivy config` PASS, `trivy fs` 7 HIGH and continues
      as report-only, `trivy image` 6 HIGH → exit 1, blocking as designed)
- [x] `bandit ^1.9.4` + `radon ^6.0.1` added as dev deps — resolution clean, **8 installs,
      0 updates** to existing packages; `pylint` was already declared but CI never ran it
- [x] **`python-multipart` 0.0.20 → 0.0.32** (approved) — only that package moved
      (122 packages before and after). `trivy image` **6 → 3**, `pip-audit`
      **16 → 10**, `trivy fs` **7 → 4**. Live consumer is the `/onboarding/logo`
      multipart upload (not login, which is a JSON body); re-verified via 4 logo
      unit tests, 11 auth e2e journeys and the onboarding upload journey
- [x] **Checkov** wired for `github_actions` — **47 passed / 0 failed / 1
      documented skip**; caught `CKV_GHA_7` on `cd.yml` first run. NOT used for
      compose: Checkov 3.3.15 has no `docker_compose` framework (verified — zero
      results), so a compose job there could never fail. **`cd.yml` was since
      deleted and split** (2026-09-03) — the same documented
      `checkov:skip=CKV_GHA_7` comment, with reasoning updated for each
      workflow's actual shape, now lives in both `cd-staging.yml`'s and
      `cd-production.yml`'s `workflow_dispatch` blocks
- [x] **VPS target spec CONFIRMED at 4 vCPU / 8 GB / 80 GB SSD** — restated in
      `docker-compose.prod.yml` and `docs/deployment/` as a confirmed spec rather
      than an assumption. Nominal CPU sums to 4.5 but `api` and `migrate` are
      mutually exclusive (`service_completed_successfully`), so real peaks are
      2.5 (migrating) and **exactly 4.0** (steady state); memory 5632M of 8192M
      leaves 2560M for host + page cache. Connections 42/100 (42%)
- [x] ~~**Staging → production gated pipeline** (2026-08-28) — CD restructured to
      `build-and-push` → `staging-deploy` → `staging-e2e` → `production-deploy`;
      production requires an explicitly-green staging gate and deploys the SAME
      digest staging proved, not a rebuild. Both deploys share one composite
      action (`.github/actions/deploy-stack`) so the logic cannot drift~~ —
      **superseded 2026-09-03.** Promotion needed to become a deliberate act
      (a push to `develop` and a push to `main` are two different events, not
      one event driving both stacks), and that broke the single-workflow
      `needs:` chain this item describes. See **CI/CD — branching restructure
      (staging/production split)** below for the replacement design: two
      workflows plus a registry-carried proof instead of a job dependency.
- [x] **Encrypted environment files** — `scripts/env.sh` (6 subcommands, 3-tier key
      discovery, AES-256-CBC + PBKDF2 100k), `make env-*` wrappers, `.gitignore`
      re-allows `*.enc` while still denying `.env` / `.env.staging` /
      `.env.production` / `.env.key` (proved with `git add --dry-run`)
- [x] **Two corrections to earlier assumptions** — `DEPLOY_PATH` is now a
      per-GitHub-Environment secret so no server path lives in the repo (the
      documented `/opt/cofoundaz-api` never existed); and the deployed env file
      is `.env`, not `.env.production`, throughout compose/Makefile/docs
- [x] **Live staging E2E gate** — `E2E_REMOTE=1` auto-deselects mailbox-dependent
      tests by fixture closure, prints every deselection, and errors if that would
      leave zero tests. Verified `13 passed, 14 deselected` against a live server
- [x] `docs/deployment/ENV_ENCRYPTION.md` + DEPLOYMENT_GUIDE / GITHUB_ACTIONS_SETUP /
      ROLLBACK updated; SOP `docs/sop/2026-08-28-staging-pipeline-env-encryption.md`.
      **2026-09-03 branching restructure** further updated `ROLLBACK.md` and added
      `docs/deployment/BRANCHING.md` (new); SOP
      `docs/sop/2026-09-03-cicd-branching-restructure.md`
- [ ] **SonarCloud** — `sonar-project.properties` committed but INERT; the job
      skips cleanly until a `SONAR_TOKEN` secret exists. Needs a SonarCloud
      account (could not be created here)
- [x] `.gitleaksignore` baselines the one historical `SECRET_KEY` committed to `.env.example` in
      `36ee5d51`; gitleaks over all 158 commits then passes clean
- [x] Full production stack verified locally end-to-end — migration gate, resource limits
      (`docker inspect`), read-only rootfs, graceful shutdown (1s, exit 0), readiness
      `200`→`503` on Redis outage
- [ ] `cd.yml` — **deleted 2026-09-03**, split into the workflows below; superseded, not
      renamed. See **CI/CD — branching restructure (staging/production split)** below
- [ ] `cd-staging.yml` (build/push GHCR + SSH deploy to staging + live E2E gate + mint
      `staging-verified-*` / `verified-sha256-*` proof tags) — authored, **never executed on
      GitHub**
- [ ] `cd-production.yml` (resolve a staging-verified digest + SSH deploy to production; no
      build step at all) — authored, **never executed on GitHub**
- [ ] `live-e2e.yml` (reusable live E2E; called by `cd-staging.yml`, also runnable standalone via
      `workflow_dispatch` against either environment) — authored, **never executed on GitHub**
- [ ] `scripts/ghcr_digest.sh` (resolves a GHCR tag/digest to its manifest digest via the
      registry v2 API; exit 0/2/1 = resolved/missing/error) — authored, **never executed against
      the real registry**
- [ ] _Deferred:_ Trivy/pip-audit CVE debt (`python-multipart`, `starlette`, `ecdsa` via
      `python-jose`) — CI expected red until triaged · real VPS deploy (SSH, nginx, UFW, GHCR
      push/pull) never attempted · CPU reservations are no-ops outside Swarm (memory
      reservations do apply) · `UvicornWorker` deprecated upstream (works on uvicorn 0.32.1)

## ✅ CI hardening & dependency debt — *shipped on `ci/exercise-image-cmd-and-dep-fixes` (2026-08-29)*

_The image's own `CMD` had never been executed by anything. SOP:
`docs/sop/2026-08-29-image-cmd-gate-and-dependency-fixes.md`._

- [x] **Image-CMD gate** (`scripts/image_cmd_check.sh`, BLOCKS in `ci.yml`'s `build` job) —
      runs the image with NO command override against throwaway Postgres/Redis on a
      user-defined network. Asserts the gunicorn `CMD` is still declared, the uvicorn
      worker class loads, `WEB_CONCURRENCY` really drives the worker count (set to 3, not
      the baked default 4, so a hardcoded `--workers` cannot pass), the container reaches
      Docker `healthy` on the image's own HEALTHCHECK timings, `/health` 200,
      `/api/v1/health/ready` 200 with both dependencies ok (JSON-parsed, not grepped), no
      worker churn, and **SIGTERM drains to exit 0, not 137**
- [x] Gate proven to bite before being trusted — an image hardcoding `--workers 2` fails on
      worker count; an image whose PID 1 swallows SIGTERM fails on exit 137, **having passed
      every other assertion**
- [x] Also runnable locally as `make image-check`; `make ci-local` grows a 7th stage
- [x] **PR #22** — `emails` 0.6 → 1.1.2 landed with a real fix: 1.x makes the `mail_from`
      ADDRESS non-optional, and `(None, None)` builds **no `From` header at all**, which
      every MTA rejects while `send()`'s discarded result hides it. Unset
      `EMAILS_FROM_EMAIL` now raises instead of being cast away. `app/platform/email.py`
      → 100% covered (it had **no** SMTP tests before)
- [x] **PR #19** — dev-tooling group landed (mypy 2.3.1, black 26.5.1, pylint 4.0.7,
      ruff 0.16.5, isort 6.1.0, pytest-asyncio 1.4.0, pytest-cov 7.1.0, ipython 9.17.0).
      Its "19 errors" were **ruff `UP042`, not mypy** — ruff runs first, so mypy never
      executed on that PR. Fixed by migrating 19 enums to `enum.StrEnum` after proving it
      safe: `name == value` everywhere, SQLAlchemy persists by name, `alembic check` shows
      no drift, no `str()`/f-string of any member in `app/`, and all 25 live API captures
      unchanged
- [x] **Weekend CI bug fixed** — six `test_mission_generate.py` tests asserted on generated
      mission tasks without pinning the date, so they passed Mon–Fri and failed **every
      Saturday and Sunday** on unmodified `main` (the service correctly returns an empty
      "weekends off" mission). A `weekday` fixture shifts the service's today forward to
      Monday on weekends and is a no-op on a weekday
- [ ] **PR #24 (`gunicorn` 26.2.0) — verified but NOT merged.** Workers, readiness,
      `--graceful-timeout` and graceful-stop exit 0 all hold, but gunicorn ≥ 25.1.0 starts a
      control socket by default under `$HOME`, and our `read_only: true` production container
      logs `[ERROR] Control server error: ... Read-only file system` on **every boot**.
      Needs `--no-control-socket` added to the Dockerfile `CMD` **in the same commit** — the
      flag does not exist in 23.0.0, so landing it first breaks startup outright
- [x] **PR #20 (`production-minor`) unblocked** (2026-08-29) — fastapi 0.136.3 → **0.141.1**,
      uvicorn 0.32.1 → **0.52.4**, httpx 0.27.2 → **0.28.1**, python-dotenv → 1.2.3. The
      blocker was slowapi losing every `include_router` route to fastapi 0.137's
      `_IncludedRouter` (upstream `laurentS/slowapi#281` is open with three unmerged PRs;
      0.1.10 is still broken), fixed in-tree by `app/core/rate_limit.py` with a boot-time
      self-check that refuses to start if resolution breaks. Ceiling `<0.137.0` → `<0.142.0`,
      still bounded on purpose. Measured: first 429 at request **121** under uvicorn for both
      the authenticated-user and anonymous-IP key; **480 × 200** under gunicorn with 4 workers.
      See SOP `2026-08-29-fastapi-ceiling-slowapi-included-router.md`
- [ ] _Deferred:_ migrate off the deprecated `uvicorn.workers` module to `uvicorn-worker`
      — **now unblocked**: `0.4.0` needs `uvicorn>=0.36.0` and the pin is `^0.52.4` as of
      2026-08-29. `uvicorn.workers` still ships in 0.52.4 and `make image-check` passes on
      it, so this is no longer urgent, but the module is on borrowed time ·
      widen the image-CMD gate to also run `alembic upgrade head` through the image ·
      `SC2329` false positive on `cleanup()` in `scripts/e2e_run.sh`

## ✅ passlib → direct bcrypt (unblocks bcrypt 5.0.0) — *shipped on `chore/bcrypt-5-passlib-migration` (2026-08-29)*

_PR #23 (`bcrypt` 4.3.0 → 5.0.0) killed every login test. SOP:
`docs/sop/2026-08-29-passlib-to-bcrypt5-migration.md`._

- [x] **Diagnosed, not assumed** — the `ValueError: password cannot be longer than 72 bytes`
      is raised while hashing a **9-character** password. It comes from passlib's own
      bcrypt backend self-test (`detect_wrap_bug`, a hardcoded 255-byte secret), so the
      backend never initialises and **every** call fails regardless of password length
- [x] Confirmed terminal, not a wait: **passlib's latest release is 1.7.4, 2020-10-08**
      (PyPI JSON API — no 1.7.5, no 2.x). Alternative was pinning `bcrypt<5` forever
- [x] **`pwdlib` evaluated and rejected on evidence** — its `BcryptHasher.verify` is a bare
      `bcrypt.checkpw` passthrough with no truncation and no option to enable it, so **63 of
      126** golden passlib hashes raise under it. It would still need our wrapper
- [x] **`app/core/security.py` calls `bcrypt` directly**; `passlib` removed from
      `pyproject.toml`/`poetry.lock`. Cost 12 / `$2b$` pinned explicitly so a future bcrypt
      release cannot silently move the cost factor. Public signatures unchanged
- [x] **Backward compatibility proven empirically, not asserted** — 126 hashes generated by
      the REAL pre-migration passlib 1.7.4 + bcrypt 4.3.0 (14 passwords × costs 4/8/10/12/13 ×
      idents `$2a$`/`$2b$`/`$2y$`), committed as `tests/fixtures/passlib_golden_hashes.json`
      with `scripts/gen_passlib_golden_hashes.py` as provenance. **126 verified, 0 mismatched,
      0 raised.** A provenance test stops the fixture being regenerated to hide a failure
- [x] **72-byte limit handled deliberately** — truncate on verify (mandatory: passlib
      truncated silently, so over-limit users' stored hashes cover only 72 bytes), reject on
      set. Truncation is on **UTF-8 bytes, not characters** — `"密" * 30` is 30 chars but 90
      bytes, and naive character-slicing still raised on 36 of the 126 hashes
- [x] `validate_password_strength` gains a byte-based upper bound on signup/reset **only**,
      never login (a login cap would lock out existing over-limit users). New additive error
      `PASSWORD_TOO_LONG` (422) — **FE/mobile must handle it**
- [x] `verify_password` returns `False` on a malformed stored hash instead of raising, so one
      corrupt row cannot 500 the login path; logged for ops without hash or password
- [x] Verified: **713 unit tests** (410 baseline all still green + 303 new), coverage 98.39%
- [x] Verified: **27 e2e**, covering the real login, MFA, lockout and reset journeys
- [x] Verified: black, isort, ruff and mypy clean; pylint 9.94; bandit and semgrep clean;
      `poetry check --lock` ok
- [x] No security regression: `pip-audit` identical before/after · `trivy config` 0 ·
      `trivy fs` unchanged (1 HIGH = the accepted `ecdsa` advisory) · `trivy image` exit 0 ·
      production image smoke-tested (bcrypt 5.0.0, passlib absent, `$2b$12$`)
- [ ] _Deferred:_ argon2 is now a clean additive next step (hashes are self-identifying by
      prefix, so prefix-dispatch + rehash-on-login needs **no** data migration) ·
      `scripts/gen_passlib_golden_hashes.py` cannot run on current deps (passlib gone) —
      throwaway-venv recipe is in its docstring

---

## ✅ Edge — nginx, TLS & two stacks on one VPS — *on `feat/nginx-edge-tls`*

_The front door, plus the compose blocker that made a second stack impossible. Everything
below is verified LOCALLY — a real nginx parsing and serving the real configuration against
the real application. **No VPS, no DNS, no certificate, no real handshake.** See
`docs/sop/2026-08-29-nginx-edge-two-stack-compose.md` and
`docs/deployment/NGINX_TLS.md`._

- [x] **Two stacks on one host** — `docker-compose.prod.yml` parameterised: project name from
      `COMPOSE_PROJECT_NAME` (default unchanged), all four `container_name:` keys removed
      (a fixed container name is global to the daemon, so it collides regardless of project),
      `API_PORT` per environment — production 8000, staging 8001
- [x] **Volume separation proven, not assumed** — both stacks up simultaneously; distinct
      containers, networks, ports and volumes; a marker row written into each Postgres and read
      back from the correct one; `down -v` on staging destroyed only staging's three volumes and
      production's marker row survived
- [x] Every reference to the old container names audited — `deploy-stack` already resolved via
      `docker compose … ps -q api`; `Makefile` goes through `$(PROD_COMPOSE)`; only prose in
      three docs named them
- [x] **Bug found and fixed:** `API_PORT` was read as `grep … | cut … || echo 8000` in three
      places. `||` tests the last command and `cut` exits 0 on empty input, so a missing
      `API_PORT` gave `http://127.0.0.1:/…` and rolled the deploy back for the wrong reason.
      Fixed in `deploy-stack/action.yml`, `cd.yml` and the `Makefile`. (`cd.yml` was since
      deleted and split into `cd-staging.yml` / `cd-production.yml` / `live-e2e.yml` — the same
      `sed -n 's/^API_PORT=//p' .env | tail -n 1` fix now lives in `live-e2e.yml`'s rate-limiter
      reset step; the other two never re-derive `API_PORT` themselves)
- [x] **nginx configuration version-controlled** in `deploy/nginx/` — http-level `conf.d/`
      (TLS, hardening, upstreams, rate-limit zones), shared `snippets/`, thin per-host vhosts,
      an ACME bootstrap vhost, and a `default_server` catch-all using `ssl_reject_handshake`
- [x] TLS 1.2 floor, ECDHE-only ciphers, session cache with tickets off, HTTP/2, HTTP→HTTPS
      301, HSTS **without `preload`** on either host (an apex-wide, irreversible decision that
      is not a subdomain's to make)
- [x] Security headers on every response including 4xx/5xx (`always`), strict `default-src
      'none'` CSP for the API, a separate relaxed CSP for the docs pages
- [x] `client_max_body_size` — `1m` globally, `3m` on `= /api/v1/onboarding/logo`, sized so a
      2–3 MB upload reaches the app and gets its actionable 422 instead of nginx's HTML 413
- [x] Edge rate limiting as a coarse backstop ~15× above the app's per-user budget, in separate
      zones per environment; staging's auth zone deliberately looser so the CD gate cannot 429
      itself
- [ ] **`live-e2e.yml` can now be dispatched against `environment: production`, whose auth zone
      is NOT loosened.** An on-demand production run can therefore 429 itself in a way the
      staging-only gate cannot — the workflow's own "Warn about writing to production" step says
      so, but the edge config does nothing to prevent it. Not a blocker (it is an opt-in
      diagnostic path, not the promotion gate), but a 429 from a production `live-e2e.yml` run
      should be read as "the zone is doing its job", not "the suite is broken"
- [x] JSON error pages for 413/429/502/503/504 in the app's own `{"error":{"code",…}}` envelope,
      with `proxy_intercept_errors off` so the application's own bodies pass through untouched
- [x] gzip on; brotli and HTTP/3 shipped **commented** with the exact enablement steps — both
      fail to parse on an nginx without the module/build, so neither may be enabled blind
- [x] `deploy/nginx/test/verify-local.sh` — real nginx, real backends, self-signed certs at the
      real cert paths: **`nginx -t` clean, 35 assertions, 0 failures**
- [x] `docs/deployment/NGINX_TLS.md` — DNS, first-time setup, certbot `--webroot`, renewal and
      how to prove it before it matters, the port map, and 502/504/handshake troubleshooting
- [x] `DEPLOYMENT_GUIDE.md` / `GITHUB_ACTIONS_SETUP.md` corrected — **nginx is a hard
      prerequisite for the staging E2E gate**, and therefore for reaching production at all.
      Neither document said so
- [ ] **`X-Forwarded-For` defect — diagnosed and proven, deliberately NOT fixed here.** Behind
      nginx every unauthenticated request keys the app's limiter on the Docker bridge gateway,
      so login/signup/forgot-password share one 120/min bucket for the whole internet. The fix
      is one env var, `FORWARDED_ALLOW_IPS=*`, in each encrypted `.env` — Adebayo's action.
      (A CIDR does **not** work: gunicorn rejects it and the container crash-loops.) Documented
      in `.env.production.example` and NGINX_TLS.md §11
- [ ] **Staging posture decided, not enforced** — recommendation is no auth: `noindex`, full
      TLS/header parity, rate limits, docs served (the gate needs `openapi.json`; production
      404s all three). Basic auth is documented and ready to uncomment, with the CD credential
      path verified against the pinned httpx — but it is **off**
- [ ] _Deferred:_ nothing has touched a real VPS — no certificate, no handshake, no SSL Labs
      grade, no certbot renewal, HTTP/3 and brotli never parsed by a capable nginx · the nginx
      upstreams hardcode 8000/8001 while the stacks read `API_PORT` from `.env`, with nothing
      enforcing agreement · no off-host certificate-expiry monitoring · `deploy/nginx/` is not
      linted by CI

---

## 🟢 CI/CD — branching restructure (staging/production split) — *merged to `develop` as PR #42 (`f96172c`); `main` untouched at `0de05f4`*

_The single `cd.yml` (build → staging → staging-e2e → production, gated by a same-workflow
`needs:` chain) is deleted. Staging and production now promote on separate events —
`push: [develop]` and `push: [main]` — so the gate that used to be a job dependency had to move
into something both runs can see: a proof carried on the GHCR manifest itself. Nothing below has
touched a real VPS, a real GHCR push, or a real GitHub Actions run — see the unchecked items at
the end. SOP: `docs/sop/2026-09-03-cicd-branching-restructure.md`._

- [x] `ci.yml` retargeted to `[develop]` only (`push` + `pull_request`), restructured to be
      event-dependent rather than firing the same 8 jobs on both events — see the "CI restructured
      to be event-dependent" item under **Deployment & Infrastructure** above for the full job
      split (`lint`/`test`/`security` always, `migrations`/`e2e`/`quality`/`build`/
      `dependency-review` PR-only, nothing on `main`)
- [x] `codeql.yml` retargeted to `[develop]` (`push` + `pull_request`, matching `ci.yml`'s
      reasoning: `main` is a fast-forward of an already-analysed `develop` commit); weekly cron
      (Mondays 04:17 UTC) unchanged
- [x] **`cd-staging.yml`** (new) — `push: [develop]` + `workflow_dispatch` (`image_tag` rollback
      input). `build-and-push` → `staging-deploy` → `staging-e2e` (calls `live-e2e.yml`) →
      `mark-staging-verified`. The only workflow in the repo that builds and pushes an
      application image
- [x] **`cd-production.yml`** (new) — `push: [main]` + `workflow_dispatch` (`image_tag` +
      `bypass_staging_proof` rollback inputs). `resolve-artifact` → `production-deploy`. **Has no
      build step at all** — it can only ever redeploy bytes `cd-staging.yml` already built and
      already proved. The `v*.*.*` tag trigger from the old `cd.yml` is removed entirely; a
      version tag no longer builds or deploys anything — tagging a release is now a labelling act
      on a commit already running in production
- [x] **`live-e2e.yml`** (new) — the old `staging-e2e` job body, extracted into a reusable
      workflow (`workflow_call`, input `environment`) also directly `workflow_dispatch`-able
      against `staging` or `production`. The rate-limiter reset (restarts the `api` service to
      clear slowapi's per-worker `MemoryStorage`) is staging-only; a production run instead prints
      an explicit warning that it creates real, non-cleaned-up user/workspace rows and does not
      reset the limiter — see the new item under **Edge** above about it being able to 429 itself
- [x] **Registry-carried staging-verified proof** — `mark-staging-verified` mints two tags on the
      SAME tested manifest via `docker buildx imagetools create`, then asserts (does not assume)
      both resolve back to the tested digest: `staging-verified-<full 40-char commit sha>`
      ("was this commit verified?", used by the automatic push-to-`main` path) and
      `verified-sha256-<64-hex digest>` ("were these bytes verified?", used by a manual rollback,
      which supplies a short tag like `sha-a1b2c3d` that the full commit SHA cannot be recovered
      from). Nothing else in the repository can mint either tag
- [x] `cd-production.yml`'s `resolve-artifact` — on a push to `main`, resolves `sha-<short7>` and
      `staging-verified-<full sha>`, fails loudly if either is absent (naming "not a fast-forward"
      as the likely cause) or if the two digests disagree, then deploys by digest. **No bypass
      exists on this path.** On `workflow_dispatch`, resolves the given tag, looks for
      `verified-sha256-<its digest>`, and refuses unless `bypass_staging_proof` is also ticked —
      which emits a `::warning` and records the bypass in the deployment summary
- [x] **`scripts/ghcr_digest.sh`** (new) — HEADs the GHCR v2 manifest API for a tag or digest;
      exit 0 = resolved (digest on stdout), 2 = does not exist, 1 = anything else, so a
      credentials outage is never misreported as a missing/un-promotable image
- [x] Docs updated to match: `docs/deployment/ROLLBACK.md` (§2 rewritten — a manual rollback no
      longer redeploys through staging first; it trusts the registry-carried proof instead, with
      a `bypass_staging_proof` break-glass path for pre-mechanism images, and an explicit
      gains/losses note on what re-proving-live-at-rollback-time was traded for), plus a new
      `docs/deployment/BRANCHING.md` describing the full promotion model, and this checklist entry
- [ ] **Delete the stale `GHCR_PULL_TOKEN` repository secret** — referenced by nothing after the
      fix above, and a long-lived `read:packages` credential. Settings → Secrets and variables →
      Actions. **NOT DONE** — Adebayo's action
- [ ] **Required reviewers on the `production` Environment** — `protection_rules: []` today, yet
      `cd-production.yml:58-59`, `cd-production.yml:291-292` and `live-e2e.yml:59-62` all treat it
      as *the* production gate. Until it exists, `bypass_staging_proof` can deploy an unverified
      image unattended and a Live E2E dispatch against production signs up real users with no
      prompt. **NOT DONE** — Settings → Environments → `production`, Adebayo's action
- [ ] **Branch protection on `main` requiring LINEAR HISTORY** — required for the fast-forward
      promotion model to hold (a squash merge or merge commit mints a new SHA that was never
      built, so `resolve-artifact` fails loudly rather than silently deploying stale bytes).
      **NOT DONE** — Settings → Branches → `main`, Adebayo's action
- [x] **Create the `develop` branch** — created; PR #42 merged into it 2026-09-03 (`f96172c`)
- [x] **First push to `develop`** — RAN (run `33776562659`). `ci.yml`'s event-dependent triggers
      and `cd-staging.yml`'s `build-and-push` both went green; **`staging-deploy` FAILED** at
      `docker login` with `username is empty`, and `staging-e2e` + `mark-staging-verified` were
      skipped. Cause: the split reverted both `deploy-stack` call sites to the removed
      `GHCR_PULL_*` PAT pair (`GHCR_PULL_USERNAME` has never existed → `""`) **and** dropped
      `permissions: packages: read` from both deploy jobs. See the item below
- [x] **GHCR-auth regression fixed** — `ghcr_username`/`ghcr_token` restored to
      `github.actor` / `secrets.GITHUB_TOKEN`, `packages: read` restored on `staging-deploy` and
      `production-deploy`, a fail-fast preflight added to `.github/actions/deploy-stack` (a
      composite action does not enforce `required: true` — an unset secret silently becomes `""`,
      which is why this only surfaced *after* the scp had landed on the VPS), and the invalid
      `script_stop:` input dropped. `actionlint` + `shellcheck` clean; preflight proven to fail on
      the real condition and pass on the fixed wiring. SOP:
      `docs/sop/2026-09-03-cd-ghcr-auth-regression.md`. **NOT VERIFIED in CI** — needs a push to
      `develop`; the identical bug was also live on the untriggered production path
- [ ] **Re-run `cd-staging.yml` after the fix** — still the first real GHCR pull from a VPS.
      **NEVER SUCCEEDED**
- [ ] **First fast-forward promotion, `develop` → `main`** — would be the first real execution of
      `cd-production.yml`'s `resolve-artifact` gate. **NEVER RUN**, and cannot happen until the
      item above has produced a `staging-verified` tag for it to resolve
- [ ] _Deferred:_ every existing "never touched a real VPS" item under **Deployment &
      Infrastructure** and **Edge** above applies identically here — this restructure changes
      *how* the gate is enforced, not whether the underlying deploy has ever run for real

---

## 🟡 Module 10 — Marketing Hub — *Slices 1–2 of 5 shipped (neither yet merged, no PR opened)*

Content Calendar + Channels + Overview CRUD spine (Slice 1), then Campaigns + Audience Segments
(Slice 2). Follows the same slice discipline as Modules 03/08/18/20: CRUD spine first, AI layer
seamed-and-deferred, analytics last. Migrations `0033_marketing_calendar_channels` →
`0034_campaigns_segments`. SOPs: `docs/sop/2026-09-24-marketing-slice1.md`,
`docs/sop/2026-09-24-marketing-slice2.md`. FE guides:
`docs/fe-integration-guide-marketing-calendar.md`,
`docs/fe-integration-guide-marketing-campaigns.md` (cross-referenced). Design specs:
`docs/superpowers/specs/2026-09-24-module-10-marketing-slice1-design.md`,
`docs/superpowers/specs/2026-09-24-module-10-marketing-slice2-design.md`.

- [x] **Slice 1 — Content Calendar + Channels core** — *shipped on `feat/module-10-marketing-slice1`
      (commits `545f7f2`→`eef03d8`), unit **1446 passed**, e2e **53/53 passed***
  - [x] Enums (`ContentStatus`, `ChannelStatus`, 8-value `ChannelKey`) + `ContentCalendarEntry`/
        `MarketingChannel` models, migration `0033` (`content_calendar`, `marketing_channels`
        tables — the PRD's `channels` name was reserved to avoid a future collision)
  - [x] Calendar service: create/list (date-range + channel + status filters)/get/update/delete;
        `status=scheduled` requires `scheduled_at` (422 otherwise, enforced identically on create
        and update); publish transition (`PATCH status:"published"`) sets `published_at` once and
        is idempotent on repeat (no re-fire, no duplicate notification)
  - [x] Channels: fixed 8-key board, lazy-seeded on first `GET /marketing/channels`
        (SAVEPOINT + re-select race guard, mirrors `get_or_create_enrollment`); `PATCH
        /channels/{key}` updates status/notes but does **not** itself seed — 404s if called before
        the workspace's first `GET /channels` (documented sharp edge, not a defect — see the SOP's
        Follow-ups)
  - [x] Overview stat strip: `scheduled_this_week` + `active_channels` computed live;
        `active_campaigns`/`top_channel_by_conversions`/`ai_content_ideas` explicit `null`
        constants naming which later slice fills each (S2/S5/S3)
  - [x] `/marketing` endpoints (8 routes) behind `require_role(founder, team_member)`; every other
        role gets 403 — no granular per-module "Marketing grant" yet (deferred, no per-module
        grant infra exists anywhere in this codebase)
  - [x] `marketing.post.published` domain event → in-app notification (`_members_minus_actor`,
        "Scheduled post published: {title}") via the existing Module 20 event/registry pattern; no
        email channel wired for this event in Slice 1
  - [x] Tests: `tests/db/test_marketing_models.py` (2) + `tests/test_marketing_migration.py` (2) +
        `tests/services/marketing/test_calendar_service.py` (8) +
        `tests/services/marketing/test_channel_service.py` (4) + `tests/api/test_marketing.py`
        (49 parametrized RBAC/CRUD cases) +
        `tests/services/notifications/test_marketing_notification.py` (2) +
        `e2e/test_marketing.py::test_marketing_journey` (7 live captures under
        `e2e/_captures/marketing/`)
- [x] **Slice 2 — Campaigns + Audience Segments** — *shipped on `feat/module-10-marketing-slice2`
      (commits `9ff939c`→`76657e3`), unit **1472 passed**, e2e **54/54 passed***
  - [x] Enums (`CampaignObjective` — 4 values, `CampaignStatus` — 4 values) + `AudienceSegment`/
        `Campaign`/`CampaignSegment` models, migration `0034_campaigns_segments` (`audience_segments`,
        `campaigns`, `campaign_segments` tables — id shortened from the brief's
        `0034_marketing_campaigns_segments`, which exceeded Alembic's 32-char
        `version_num` column limit)
  - [x] Segments service: create/get/list/update/delete + used-by (`GET
        /segments/{id}/campaigns`); `persona_id` validated as a `persona`-kind Business Builder
        record (Module 08) in this workspace, one query covering both "wrong kind" and
        "cross-tenant" as the same 422
  - [x] Campaigns service: create/get/list/update/delete; `channel_mix` is `{ChannelKey: percent}`
        (0–100, not required to sum to 100) against a cents `budget` — FE derives per-channel
        spend; `segment_ids` validated per-workspace, join rows fully replaced (dedupe +
        order-preserved) on every write that touches them
  - [x] Guarded lifecycle: `_TRANSITIONS` table — `draft→active` (launches, sets `launched_at`)
        and `active→completed` (sets `completed_at`) each fire a `marketing.campaign.*` event;
        `active↔paused` silent; anything else → 422; re-issuing the current status is a silent
        no-op; a reviewer-caught partial-write gap (fields from an illegal `PATCH` still landing
        before the status check failed) was fixed in `79aae4d` — the whole `PATCH` is now atomic
  - [x] `/marketing` endpoints (11 new routes: 6 segment + 5 campaign) behind the same
        `require_role(founder, team_member)` dependency Slice 1 already built — no new RBAC wiring
  - [x] `marketing.campaign.launched`/`marketing.campaign.completed` domain events → in-app
        notifications (`_members_minus_actor`, `"Campaign launched: {name}"` /
        `"Campaign completed: {name}"`) via the existing Module 20 event/registry pattern; no
        email channel wired for either event, matching Slice 1's precedent
  - [x] Tests: `tests/services/marketing/test_segments_service.py` (7) +
        `tests/services/marketing/test_campaigns_service.py` (9) +
        `tests/api/test_marketing_campaigns.py` (5) +
        `tests/services/notifications/test_marketing_campaign_notification.py` (2) +
        `e2e/test_marketing.py::test_marketing_campaigns_journey` (7 live captures under
        `e2e/_captures/marketing/`, alongside Slice 1's 7)
  - [x] `metrics` stays an explicit `{}` on every campaign — Slice 5 is what fills it
- [ ] **Slice 3 — AI Content Assistant** (`Write with AI` / `Plan my week` / copy generation,
      **plus an AI channel-plan recommender for `channel_mix`**) — *planned, not started.* Fills
      `ai_content_ideas`; Slice 1's calendar CRUD is exactly what AI-generated entries will be
      created through — nothing to rework.
- [ ] **Slice 4 — SEO Tools** — *planned, not started.*
- [ ] **Slice 5 — Performance Analytics** (+ Module 22 export) — *planned, not started.* Fills
      `top_channel_by_conversions` and campaign `metrics` (currently always `{}`).

**Deferred within the Slice 1 CRUD spine itself** (tracked in the Deferred follow-ups section
below): auto-publish at `scheduled_at` (manual `PATCH` only today); multi-channel entries (single
`ChannelKey` per entry today); media upload integration with Module 18 (`media_ref` is an
untouched opaque string slot); granular per-module Marketing grant.

**Deferred within the Slice 2 CRUD spine itself** (tracked in the Deferred follow-ups section
below): segment rule execution against real data (`definition` is an opaque, unevaluated JSON
blob; `est_size` is manually entered, not computed); no campaign Content step (attach/generate
assets, link calendar entries); no `cancelled` terminal status (only `draft`/`active`/`paused`/
`completed` exist, `completed` is the only terminal state).

---

## ⬜ Upcoming (from PRD — mapped as we reach each)

- [x] **Module 03 — AI Co-Founder** — *✅ MODULE 03 COMPLETE (owner decision, 2026-09-21)*: Slice 1
      (LLM seam + assessment narrative, PR #72) + Slice 2
      (structured output + canvas ai-fill worker, PR #77) merged; Slice 3 (typed records ai-fill
      worker, closing the last unconsumed ai-fill job type) merged (PR #81); Slice 4 (mission
      reason + health recommendation AI upgrade) merged (PR #83); Slice 5 (dashboard AI briefing)
      merged (PR #87); Slice 6 (roadmap re-plan rationale) merged (PR #90); Slice 7 (onboarding
      AI panel) merged (PR #92); see its own section above.
      All six named Module-03 AI consumers and the LLM seam are shipped, across seven build
      slices. Of the 3 infra items named since Slice 1: per-workspace LLM token budget and a
      workspace-level enrichment-status endpoint (`GET /ai/status`) shipped 2026-09-21 on
      `feat/llm-budget-ai-status` (SOP `docs/sop/2026-09-21-llm-budget-ai-status.md`); the
      non-OpenAI/Anthropic provider implementation is dropped won't-do (OpenAI-only,
      2026-09-21) — no remaining open Module-03 follow-ups
- [x] **Module 20 — Notifications** — *✅ ALL 4 SLICES BUILT — MODULE 20 COMPLETE*: Slice 1
      (In-App Feed + Fan-Out) merged (PR #58); Slice 2 (Email Delivery + Preferences + Worker)
      merged (PR #60); Slice 3 (Scheduler/Cron) merged (PR #71, migrations `0024_scheduled_runs` →
      `0025_roadmap_milestone_due_idx`); Slice 4 (Real-Time SSE) merged (PR #74, no migration);
      see its own section above.
      Device/closed-app push remains a genuinely separate, still-unbuilt follow-up (Slice 4 SOP)
- [x] **Module 17 — Learning Academy** — *✅ MERGED to `develop` (PR #59); see its own section above.* **2026-09-23:** its Module-03-deferred AI upgrade (shelf-level `recommendation_reason`, templated→AI on `GET /learning/recommendations`) shipped on `feat/module-03-deferred-ai-upgrades` (migration `0031_learning_recommendations`, commits `5035f56`→`2fb6501`) — SOP `docs/sop/2026-09-23-deferred-ai-upgrades.md`, FE guide `docs/fe-integration-guide-learning-recommendations.md` · brief `docs/handoff/module-17-learning-academy.md` · planned blueprint `docs/architecture/planned/modules-17-21-junior-handoff.md`
- [x] **Module 21 — Founder Journal** — *✅ MERGED (PR #37); this line was stale — the Snapshot above (and the module tally) already counted it complete, this checkbox had just never been flipped.* **2026-09-23:** its Module-03-deferred AI upgrade (daily `prompt`, static→AI on `GET /journal/prompts/today`, grounded only in operational signals — never journal content or mood) shipped on `feat/module-03-deferred-ai-upgrades` (migration `0032_journal_prompts`, commits `f9f9b01`→`1797088`) — SOP `docs/sop/2026-09-23-deferred-ai-upgrades.md`, FE guide `docs/fe-integration-guide-journal.md` §1 · brief `docs/handoff/module-21-founder-journal.md` · planned blueprint `docs/architecture/planned/modules-17-21-junior-handoff.md`
- [ ] **Module 10 — Marketing Hub** — *🟡 IN PROGRESS: Slices 1–2 of 5 shipped 2026-09-24* —
      Slice 1 on `feat/module-10-marketing-slice1` (Content Calendar + Channels + Overview CRUD
      spine, migration `0033_marketing_calendar_channels`) + Slice 2 on
      `feat/module-10-marketing-slice2` (Campaigns + Audience Segments, migration
      `0034_campaigns_segments`); neither yet merged, no PR opened yet — see its own section
      above. Slices 3–5 (AI Content Assistant, SEO Tools, Performance Analytics) planned, not
      started. SOPs `docs/sop/2026-09-24-marketing-slice1.md` +
      `docs/sop/2026-09-24-marketing-slice2.md`, FE guides
      `docs/fe-integration-guide-marketing-calendar.md` +
      `docs/fe-integration-guide-marketing-campaigns.md`.
- [ ] **Module 09 — Validation Hub** — *🟢 shipped on branch `feat/validation-hub`, not yet merged; see its own section above* · brief `docs/handoff/module-09-validation-hub.md`
- [ ] Remaining PRD modules — to be mapped into their own sections as scope firms up

**Reference docs:** system architecture blueprint `docs/architecture/system-architecture.md` (sync/verify after each module); planned-module blueprints under `docs/architecture/planned/`.

---

## Deferred follow-ups (tracked, non-blocking)

### Deferred AI upgrades (unblocked by Module 03)

Each is a small "seam it, defer it" AI slice inside an otherwise-complete (or in-progress)
module. Module 03's LLM seam, structured output, async-upgrade worker pattern, and the
per-workspace token budget all exist — so none of these are *blocked*. They
follow the established async-upgrade pattern (templated fallback written synchronously, an
`ai.*` worker job overwrites it via `complete*()`), and each is metered through
`app/platform/llm_budget.py`. **Modules 17 and 21 shipped 2026-09-23** on
`feat/module-03-deferred-ai-upgrades`; Module 09 remains with the junior.

- [x] **Module 17 — AI-picked learning recommendations** — **shipped 2026-09-23** as the
      shelf-level `recommendation_reason` (templated→AI on `GET /learning/recommendations`;
      migration `0031_learning_recommendations`, `ai.learning.recommendations` worker). Was v1
      deterministic stage-tag matching; the AI reason-line is now built. SOP
      `docs/sop/2026-09-23-deferred-ai-upgrades.md`, FE guide
      `docs/fe-integration-guide-learning-recommendations.md`. Per-course reasons +
      Health-Score-weighted sort key remain unbuilt follow-ups.
- [x] **Module 21 — AI context-aware journal prompts** — **shipped 2026-09-23** as the daily
      `prompt` upgrade (static→AI on `GET /journal/prompts/today`, grounded only in operational
      signals — never journal content or mood; migration `0032_journal_prompts`,
      `ai.journal.prompt` worker). SOP `docs/sop/2026-09-23-deferred-ai-upgrades.md`, FE guide
      `docs/fe-integration-guide-journal.md` §1. Mood/journal-aware prompts (behind consent)
      remain an unbuilt follow-up.
- [ ] **Module 09 — Validation Hub AI insight synthesizer** (`POST /validation/synthesize`) +
      interview-script generation (`POST /validation/scripts/generate`). Both are enqueue-a-job
      seams; v1 enqueues / ships plain CRUD. **In progress with the junior** (handoff + issue
      #62). Brief: `docs/handoff/module-09-validation-hub.md`.

### Module 10 (Marketing Hub) Slice 1 follow-ups

Deferred by the Slice 1 design spec, not gaps introduced by accident — tracked here so Slices
2–5 (and any interim hardening) can pick them up deliberately. SOP:
`docs/sop/2026-09-24-marketing-slice1.md`.

- [ ] Auto-publish at `scheduled_at` — today's status transitions are manual `PATCH` only; would
      hook Module 20's cron/scheduler infra.
- [ ] Multi-channel calendar entries — `ContentCalendarEntry.channel` is a single `ChannelKey`
      today; broadening to multiple channels per entry needs a model change, not a mechanical
      extension.
- [ ] Media upload integration with Module 18 — `media_ref` is an untouched opaque string slot,
      not fetched/validated server-side yet.
- [ ] Granular per-module "Marketing grant" — access is workspace-wide founder/team_member today;
      no per-entry ownership or narrower grant (no per-module grant infra exists anywhere in this
      codebase yet). **Still open after Slice 2** — segments/campaigns hang off the same
      workspace-wide dependency, no narrower grant added.
- [ ] `PATCH /marketing/channels/{key}` 404s if called before that workspace's first
      `GET /marketing/channels` (which lazy-seeds the 8 rows) — documented as a deliberate
      write-path-simplicity tradeoff in the FE guide, not fixed here; a future pass could make
      `PATCH` auto-seed instead.

### Module 10 (Marketing Hub) Slice 2 follow-ups

Deferred by the Slice 2 design spec, not gaps introduced by accident — tracked here so Slices
3–5 (and any interim hardening) can pick them up deliberately. SOP:
`docs/sop/2026-09-24-marketing-slice2.md`.

- [ ] AI channel-plan recommender for `channel_mix` — Slice 3's job; today `channel_mix` is
      100% manually entered by the founder.
- [ ] Campaign `metrics` stays `{}` and `top_channel_by_conversions` stays `null` until Slice 5
      (Performance Analytics) — same "ship the shape now, fill it in later" pattern as every
      other deferred Overview field in this API.
- [ ] Segment rule execution — `AudienceSegment.definition` is stored/returned as an opaque JSON
      blob with no engine evaluating it against real user/customer data; `est_size` is a
      manually-entered number, not computed from the definition.
- [ ] Campaign Content step — no attach/generate-assets flow and no link from a campaign to
      specific Slice 1 calendar entries yet; the PRD's "Content" step of campaign planning is
      unbuilt.
- [ ] No `cancelled` terminal status — only `draft`/`active`/`paused`/`completed` exist;
      `completed` is the only terminal state, with no server-side "abandon this campaign"
      transition distinct from delete.

- [x] **Resend email backend** — shipped (PR #54, `cddafdc`). `ResendEmailSender` behind `EmailSender`, `EMAIL_BACKEND=resend`, httpx (no new dep), fail-loud; `RESEND_API_KEY` now a real Settings field; share-create notification made best-effort. SOP `docs/sop/2026-09-12-resend-email-backend.md`. **Deploy:** set `EMAIL_BACKEND=resend` + `RESEND_API_KEY` + a Resend-verified `EMAILS_FROM_EMAIL` in `.env.staging.enc`/`.env.production.enc`; verify a real send in staging (not exercised live — mocked in tests).
- [ ] `complete_assessment` should return `job_ids` (parity with `complete_onboarding`)
- [ ] Index `assessments.created_by` FK
- [ ] `compare` error distinction (in-progress vs not-found) if FE needs it
- [ ] `team_size` scoring tie edge case
- [ ] `MFA_ENCRYPTION_KEY` must be set per environment (empty → 500)
- [x] Commit `poetry.lock` (currently gitignored) before reproducible deploy — *un-gitignored and
      git-staged 2026-08-27; still needs an actual commit, nothing is committed yet*
- [ ] `LocalStorage` returns a filesystem path, not an HTTP URL — `logo_url` not FE-renderable until a URL-returning storage backend lands
- [ ] No async worker draining `jobs` yet (Modules 05/06 decide)
- [ ] Trivy: **3 HIGH** remaining (all `starlette` 0.46.2; was 6 before the
      `python-multipart` bump). 0 CRITICAL, zero OS-package findings. Nothing
      suppressed in `.trivyignore`; CI's `build` job is expected red until triaged
- [ ] `pip-audit`: **10 advisories** remaining — `starlette` (9) + `ecdsa` 0.19.2
      (`PYSEC-2026-1325`, no fix, via `python-jose`); was 16. Nothing suppressed
      in `.github/security/pip-audit-ignores.txt`
- [ ] Migrate `python-jose` → `PyJWT` to drop the unfixable `ecdsa` advisory
      (`CVE-2024-23342` / `PYSEC-2026-1325`, the Minerva attack — surfaced by
      `trivy fs`, hidden from the image scan by `--ignore-unfixed`)
- [ ] Refactor `validate_answer` (`app/services/assessment/engine.py`) so ruff's
      `max-complexity` can drop 12 → the common default of 10. It is the only
      function above 10
- [ ] Enable SonarCloud (create project, replace the CHANGE_ME `projectKey` /
      `organization`, add `SONAR_TOKEN`) — see `docs/deployment/GITHUB_ACTIONS_SETUP.md`
- [ ] Triage the first CodeQL baseline, then decide whether to promote it from
      report-only to blocking via branch-protection code-scanning requirements
- [ ] Confirm Dependabot actually opens Docker **digest** PRs — the digest lives in
      an `ARG PYTHON_IMAGE=...` default rather than a bare `FROM` literal, which its
      Docker parser is not guaranteed to follow. If nothing appears within two
      weeks of enabling, refresh by hand and treat as unverified
- [ ] **Both compose files remain unscanned by any IaC tool.** `trivy config`
      targets Dockerfile/K8s/Terraform/CloudFormation/Helm; Checkov 3.3.15 has no
      `docker_compose` framework at all. Closing this needs a compose-specific
      linter, not another general IaC scanner
- [x] ~~`starlette` 0.46.2 — **3 HIGH** blocking `trivy image`~~ — **resolved.** The tree is
      on starlette **1.6.0** (pin `>=1.3.1,<2.0.0`), and `make scan` is clean: the only HIGH
      left in `trivy fs` is `ecdsa` CVE-2024-23342 (transitive via `python-jose`, no fixed
      version published, report-only). Verified 2026-08-29 alongside the fastapi bump
- [ ] **Rate limits are per-worker, not global.** slowapi uses in-memory `MemoryStorage`, so
      the real ceiling in production is `WEB_CONCURRENCY × RATE_LIMIT_PER_MINUTE`
      (measured: 4 workers × 120 = **480**, not 120). Pre-existing, not a regression, but now
      quantified. Redis is already a dependency — pointing slowapi's storage at it would make
      the limit global and exact. Do this before the limit is treated as a security control
      rather than an abuse damper
- [ ] **Only 13 of 27 e2e tests gate production.** The other 14 need
      `EMAIL_FILE_DIR` on the same machine as the test run (they reach the
      `mailbox` fixture, mostly via `make_verified_user`), which a remote runner
      does not have. The full local suite still runs against a local server — but only in
      `ci.yml`'s `e2e` job, which is **PR-only** as of the 2026-09-03 event split (never on a
      push to `develop` — see the cost-model comment atop `ci.yml`), so it is not a safety net
      for a direct push the way `lint`/`test`/`security` are. Widening the live gate needs
      mail-dir transport over SSH or an on-VPS runner
- [ ] **No secret has been created yet.** `.env.staging.enc` / `.env.production.enc`
      do not exist and `ENV_ENCRYPTION_KEY` is not in GitHub — the one-time setup
      in `docs/deployment/ENV_ENCRYPTION.md` §4 is Adebayo's to run
- [ ] `APP_URL` must be set as a **staging** environment variable, not only
      production — the staging gate (`live-e2e.yml`, called from `cd-staging.yml`) uses it as
      `E2E_BASE_URL`. Production's `APP_URL` now also feeds `live-e2e.yml` when it is dispatched
      on demand with `environment: production`
- [ ] The staging/production deploy path has **never run against a real VPS** — no
      SSH deploy, no scp, no GHCR pull from a server; neither `cd-staging.yml` (4 jobs:
      `build-and-push` → `staging-deploy` → `staging-e2e` → `mark-staging-verified`) nor
      `cd-production.yml` (2 jobs: `resolve-artifact` → `production-deploy`) has ever executed
- [ ] CodeQL, `dependency-review` and SonarCloud have **never executed** — none can
      run locally; first signal comes from the first GitHub Actions run/PR
- [ ] The `SECRET_KEY` committed to `.env.example` in `36ee5d51` is public in git history and must
      be treated as compromised — confirm no deployed environment ever used it; clearing it from
      history needs `git-filter-repo` + force-push + everyone re-cloning
- [ ] Production deploy path (`docker-compose.prod.yml`, hardened `Dockerfile`,
      `cd-production.yml`) has never been exercised against a real VPS — no SSH deploy, no
      nginx, no UFW, no GHCR push/pull; the workflows have never executed on GitHub
- [ ] `deploy.resources.reservations.cpus` is declarative-only outside Swarm (verified
      `CpuShares`/`CpuQuota`/`CpuPeriod` all `0` on Compose v2.40.3) — memory reservations do apply
- [ ] `uvicorn.workers.UvicornWorker` is deprecated upstream in favour of the `uvicorn-worker`
      package — works today on uvicorn 0.32.1
- [ ] Local venv runs Python 3.14 while the project targets 3.11 (CI uses 3.11) — pre-existing,
      not introduced by the deployment hardening pass
- [ ] **`FORWARDED_ALLOW_IPS=*` is not set in any real environment.** Until it is added to
      `.env.staging` / `.env.production` and re-encrypted, the app's anonymous rate limit is a
      single global bucket and `X-Forwarded-Proto` is not honoured behind nginx
- [ ] nginx upstream ports (8000/8001 in `deploy/nginx/conf.d/20-upstreams.conf`) and each
      stack's `API_PORT` must agree; nothing enforces it. Disagreement shows up as a 502 on one
      vhost only. A deploy-time assertion would close it
- [ ] No certificate-expiry monitoring from off-host — certbot can fail silently on a rate limit
      or a DNS change
- [ ] `deploy/nginx/` has no CI coverage. A job running just `nginx -t` inside the nginx
      container would be cheap and is not done
