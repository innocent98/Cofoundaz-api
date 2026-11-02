# Module 03 — Onboarding AI panel (design)

**Status:** approved-for-planning
**Date:** 2026-09-21
**Module:** 03 (AI Co-Founder) consumer — the onboarding AI panel (the last Module-03-deferred consumer).
**Depends on:** Module 03 Slice 1 (LLM seam, `complete`) — shipped; the job worker; Module 01 (Auth+Onboarding, incl. `apply_step` / `serialize_state`) — shipped.

## Goal

Give the founder a one-shot, AI-authored "calibration" message during onboarding — "here's what I
understand about your startup and how I'll help" — reflecting their industry, stage, and goals. It is
surfaced as an `ai_panel` field on the onboarding state, written templated the moment the calibration
signals are complete and AI-upgraded moments later (assessment-narrative async-upgrade pattern).

One sentence: *the assessment-narrative async-upgrade pattern, applied to a new `ai_panel` field on
`StartupProfile`, generated once when the founder's industry + stage + goals first become known.*

## Why

- The `AIPanel`/`StubAIPanel` seam (`app/platform/ai.py`, `reply(context) -> str`) was scaffolded but
  **wired to nothing** — no endpoint, no storage, no consumer (only a unit test touches the stub). This
  slice delivers the real panel and retires that dead single-shot seam.
- It is the last Module-03-deferred AI consumer; completing it closes the AI-consumer set (mission
  reason, health recs, dashboard briefing, roadmap rationale, onboarding panel).
- The seam's `reply(context) -> str` shape and every prior Module 03 consumer confirm the intent is a
  one-shot contextual message, not a multi-turn chat (a chat would be a separate subsystem — explicitly
  out of scope).

## Scope

### In scope
1. **New `ai_panel` Text column** (nullable) on `StartupProfile` (+ migration `0029`).
2. **Trigger** — in `apply_step`, once `industry` + `stage` + `goals` are all present and `ai_panel`
   is still null: write a templated fallback + enqueue `ai.onboarding.panel`. Fires exactly once.
3. **Worker `handle_onboarding_panel`** — PII-free context (industry, stage, goals) → one `complete()`
   call → overwrites `ai_panel`.
4. **Expose `ai_panel`** on `serialize_state` (the `GET/PATCH /onboarding/state` response).
5. **Retire `app/platform/ai.py`** (`AIPanel`/`StubAIPanel`) + its test; fold the templated fallback
   into the onboarding service.
6. Tests (unit + one live e2e against the stub), onboarding FE guide note, SOP, checklist (**closes the
   Module 03 consumer set** — note Module 03 status in the reconcile).

### Out of scope (deferred)
- Interactive multi-turn chat panel (a separate subsystem: messages table, send endpoint, history).
- Regenerating the panel when the founder later edits industry/stage/goals (v1 generates once).
- A panel on any surface other than onboarding state.

## Architecture

### 1. Data model — `app/db/models/startup.py` + migration

Add to `StartupProfile`:
```python
ai_panel: Mapped[str | None] = mapped_column(Text, nullable=True)
```
Nullable so pre-existing profiles need no backfill and the "generate once" gate can key on
`ai_panel is None`. Migration `0029_startup_profile_ai_panel` (`--autogenerate`, then renumber):
`op.add_column` / `op.drop_column` on `startup_profiles`. Single linear head off
`0028_roadmap_replan_rationale`.

### 2. Trigger — `app/services/onboarding/steps.py`

At the end of `apply_step` (after the field writes + `db.flush()`), call a helper:
```python
_maybe_generate_ai_panel(db, startup)
```
```python
def _maybe_generate_ai_panel(db: Session, startup: Startup) -> None:
    profile = startup.profile
    if profile.ai_panel is not None:
        return  # generate exactly once
    if not (startup.industry and startup.stage and profile.goals):
        return  # calibration signals not complete yet
    profile.ai_panel = _templated_panel(startup.industry, startup.stage.value)
    db.flush()
    job_dispatcher.enqueue(db, "ai.onboarding.panel", {"startup_id": str(startup.id)}, startup.id)
```
Writing the templated value **before** enqueuing makes `ai_panel` non-null immediately, so a
subsequent PATCH won't re-enqueue (identical guard to the roadmap-rationale slice). `job_dispatcher`
imported from `app.platform.jobs`. `_templated_panel` reuses the retired stub's wording:
```python
def _templated_panel(industry: str, stage: str) -> str:
    return f"Got it — a {industry} startup at the {stage} stage. Let's calibrate your workspace."
```
(Both helpers live in the onboarding service, e.g. `app/services/onboarding/ai_panel.py`.)

### 3. Worker — `app/worker/handlers/ai.py` (extend)

`handle_onboarding_panel(db, job)`:
1. `startup = db.get(Startup, job.payload["startup_id"])`; no-op if missing or `startup.profile is None`.
2. `messages = build_onboarding_panel_messages(industry=startup.industry, stage=(startup.stage.value if startup.stage else None), goals=(startup.profile.goals or []))` — PII-free (industry, stage, goals only; **never** full_name/role/country/phone).
3. `text = get_llm_client().complete(messages, max_tokens=settings.LLM_MAX_TOKENS)`.
4. `startup.profile.ai_panel = text.strip()`; `db.flush()`. Fail-loud on the LLM call (runner owns the txn).

Register: `register_handler("ai.onboarding.panel", handle_onboarding_panel)`.

**Prompt builder** in `app/services/onboarding/ai_panel.py`:
`build_onboarding_panel_messages(*, industry, stage, goals) -> list[LLMMessage]` — mirrors
`build_narrative_messages`; asks for a short (2-3 sentence) warm, concrete calibration message that
reflects the founder's inputs and sets expectations for how the AI co-founder will help.

### 4. Expose the field — `app/services/onboarding/workspace.py`

Add `"ai_panel": profile.ai_panel` to `serialize_state`'s returned dict (string | null). The FE renders
it when non-null; it is null before the calibration signals are complete, then the templated value,
then the AI value within seconds.

### 5. Retire the dead seam

Delete `app/platform/ai.py` (`AIPanel` Protocol, `StubAIPanel`, `ai_panel` singleton) and
`tests/platform/test_ai.py`. Grep confirms no other consumer. Its only real content (the templated
message wording) is preserved as `_templated_panel`.

### Data flow

```
PATCH /onboarding/state (industry+stage+goals now all set) → apply_step → _maybe_generate_ai_panel:
    profile.ai_panel = templated  →  enqueue ai.onboarding.panel {startup_id}   … worker …
  handle_onboarding_panel → complete() → overwrite profile.ai_panel
GET /onboarding/state → AI panel (templated is the instant value + fallback)
```

## Error handling

- **Signals incomplete**: nothing written, nothing enqueued until industry + stage + goals are all present.
- **Generate-once**: `ai_panel is None` gate + writing the templated value synchronously before enqueue
  → exactly one job; subsequent PATCHes re-enqueue nothing.
- **Fallback**: the templated `ai_panel` is the instant value; if the job fails it remains and the job
  retries (bounded backoff). Pre-existing profiles have `ai_panel = null` (FE renders nothing).
- **LLM failure**: fail-loud → retry; nothing partial persists.
- **PII**: prompt carries only industry/stage/goals — never founder name, role, country, or phone.
  `LLM_API_KEY` never logged.

## Testing

- **Unit — builder**: `build_onboarding_panel_messages` is PII-free (no name/role/country/phone) and
  includes industry/stage/goals.
- **Unit — trigger**: patching the final calibration field writes a non-null templated `ai_panel` and
  enqueues exactly one `ai.onboarding.panel`; a subsequent patch enqueues nothing (already set);
  incomplete signals (missing goals, or missing stage) enqueue nothing.
- **Unit — handler** (stub + fake LLM): a startup with a profile → `ai_panel` overwritten with AI text;
  stub marks it `[stub-llm] …`; missing startup/profile → no-op; LLM error → fail-loud.
- **E2E (stub)**: walk onboarding through the goals step → `GET /onboarding/state` shows the templated
  `ai_panel` → drain worker → `GET /onboarding/state` shows the `[stub-llm]` panel. Capture both states.
- DB-clean unit tests (`db` fixture; no `SessionLocal()` on the app DB). Coverage ≥ 95%.

## Security & privacy

Trigger is the already-authenticated onboarding state PATCH; the worker adds no endpoint. Prompt carries
only business context (industry, stage, goals) — no PII. `LLM_API_KEY` env-only, never logged.

## FE impact (integration guide)

Update the onboarding FE guide: `GET/PATCH /onboarding/state` now returns `ai_panel` (string | null) —
a short AI-authored calibration message shown once the founder's industry, stage, and goals are set;
templated instantly, AI-upgraded within seconds (re-fetch state to pick it up). `null` before the
signals are complete and on pre-existing workspaces. Render as opaque prose. Payloads verbatim from
captures.

## Global constraints (carried into the plan)

- **No AI attribution** in any commit/PR/issue/comment.
- Reproduce CI locally & green before push: black/isort/ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95%,
  **Migrations (fresh-DB round-trip + `alembic check` drift) — one new migration `0029`, single head**, e2e.
- DB-clean unit tests (`db` fixture, no `SessionLocal()` on the app DB).
- Worker no-commit convention (handlers end with `db.flush()`); seam fail-loud; enum/FK conventions.
- SOP + checklist + FE guide updated in the same pass; FE-guide payloads copied verbatim from live captures.

## Follow-ups

Regenerate the panel when the founder edits industry/stage/goals; an interactive chat panel (separate
subsystem). **This slice closes the Module 03 AI-consumer set** — Module 03's remaining scope after it
is only any non-OpenAI provider impl / infra, not consumers.
