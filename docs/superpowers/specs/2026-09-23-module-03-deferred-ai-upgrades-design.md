# Module 03 — two deferred AI upgrades: learning recommendation reason + context-aware journal prompt (design)

**Status:** approved-for-planning
**Date:** 2026-09-23
**Module:** 03 (AI Co-Founder) consumers — builds the two remaining "seam it, defer it" AI slices left in
otherwise-complete modules 17 and 21.
**Depends on:** the LLM seam (`app/platform/llm.py`), the job worker, and the per-workspace token budget
(`app/platform/llm_budget.py`) — all shipped.

## Goal

Turn the two deterministic/static stubs left behind in modules 17 and 21 into AI-personalized values,
using the established async-upgrade pattern:

1. **Module 17 — shelf-level recommendation reason.** `GET /learning/recommendations` gains a single
   AI-authored line explaining *why this set of courses is recommended* (PRD 17.1: "Because your
   assessment flagged pricing…"). Today: no reason at all; recommendations are deterministic stage-tag
   matches computed fresh per request.
2. **Module 21 — context-aware journal prompt.** `GET /journal/prompts/today` returns an AI prompt
   grounded in the founder's operational progress ("You shipped {milestone} — how did it feel?"). Today:
   a 7-prompt static pool on a date rotation, identical for every founder.

Both are **lazy on-read** (generating→ready), **metered** through `llm_budget`, and **keep their
fallback** whenever the AI has not run yet or the workspace is over budget.

## Why

- Both were deliberately seamed-and-deferred at build time with the note "comes with Module 03". Module
  03's infrastructure now exists, so these are unblocked, not blocked — only unbuilt.
- Both stubs were built **stateless** (a pure function / a served-but-never-stored value), so neither
  module has an existing row to write an AI value onto — unlike mission-reason / health-recommendations,
  which wrote onto an existing entity field. Each therefore needs a small new status-tracked table. This
  is the cheapest correct persistence seam and mirrors the dashboard-briefing table.

## Design decisions (settled in brainstorming)

| Decision | Choice | Consequence |
| --- | --- | --- |
| 17 reason granularity | **One shelf-level reason** (not per-course) | One row per startup, one LLM call, cheapest; matches PRD's singular "reason-line". |
| 21 prompt grounding | **Operational signals only** — recent shipped milestone(s) + current mission | **Never** sends decrypted `JournalEntry.content` or `MoodLog` to OpenAI. The load-bearing privacy guardrail of this slice. |
| Trigger (both) | **Lazy on-read** (generating→ready) | Matches `ai.dashboard.briefing`; self-healing; no event subscriptions. |

## Scope

### In scope

1. **Two new tables + migrations** `0031_learning_recommendations`, `0032_journal_prompts` (single linear
   head off `0030_llm_usage_daily`).
2. **Two prompt/schema builder modules**: `app/services/learning/ai_reason.py`,
   `app/services/journal/ai_prompt.py`.
3. **Two worker handlers** in `app/worker/handlers/ai.py`: `ai.learning.recommendations`,
   `ai.journal.prompt` (registered via `register_handler`).
4. **Two endpoint changes**: `GET /learning/recommendations` (add `recommendation_reason`, upsert+enqueue);
   `GET /journal/prompts/today` (upsert+enqueue; response shape unchanged).
5. **Tests** (unit + e2e capture per feature), **two FE-guide updates**, **SOP**, **checklist** reconcile.

### Out of scope (deferred)

- **Per-course** reasons on the learning shelf (v1 is one shelf-level line).
- Grounding the journal prompt in **mood/journal history** (privacy — operational signals only for v1).
- **Event-driven** regeneration (v1 is lazy on-read; a stage change on 17 regenerates on the next read via
  the `stage` staleness key; a new day on 21 is naturally a new row).
- Wiring the **assessment weakest-dimension** into the learning reason if it is not trivially reachable —
  stage + recommended course titles is the v1 grounding floor (see §Feature A worker).
- A per-field terminal state distinct from `generating` when skipped for budget (the `/ai/status`
  `over_budget` flag explains a stuck `generating`, same as dashboard briefing).

## Architecture

Both features are instances of one shape, already proven by `ai.dashboard.briefing`:

```
GET endpoint                         worker handler (ai.*)
------------                         ---------------------
load scope (startup [, founder])     load scope; if missing -> return
upsert status row:                   load status row; if missing OR status != generating -> return
  if absent/stale:                     (idempotency: a second drained job no-ops)
    write templated/static fallback    gather grounding context (operational only)
    status = generating                if nothing to personalize -> keep fallback, status = ready, no LLM
    enqueue ai.* job                   text = metered_complete_json(db, startup_id, msgs, schema=..)
return current value (fallback          if text is None (over budget) -> return (keep fallback, still generating)
  first, AI once ready)                write row.<field> = text[:300]; row.status = ready
                                       db.flush()   (runner owns the txn; never commit/rollback)
```

`status` is `Enum("generating", "ready", native_enum=False, length=16)` — matches the project's enum
convention (`native_enum=False`, explicit `length`). It exists for worker idempotency and to let the FE
optionally show a "personalizing…" hint; the value field always carries a usable fallback so the FE can
ignore `status` and just render the text.

### Feature A — Module 17 shelf-level recommendation reason

#### A1. Data model — `app/db/models/learning.py` (append) + migration `0031`

`LearningRecommendation(UUIDMixin, TimestampMixin, Base)` → table `learning_recommendations`:

- `startup_id: Mapped[uuid.UUID]` — FK `startups.id` `ondelete="CASCADE"`, `index=True`.
- `stage: Mapped[str | None]` — `String(32)`, nullable. The `StartupStage` value the reason was generated
  for; the **staleness key** (reason regenerates when the startup's stage changes).
- `reason: Mapped[str | None]` — `String(300)`, nullable.
- `status: Mapped[str]` — `Enum("generating", "ready", native_enum=False, length=16)`, non-null,
  `default="generating"`.
- `__table_args__ = (UniqueConstraint("startup_id", name="uq_learning_reco_startup"),)` — one row per
  startup.

Registered in `app/db/models/__init__.py`. Migration `0031_learning_recommendations` (`--autogenerate`,
renumber, `down_revision="0030_llm_usage_daily"`).

#### A2. Prompt builder — `app/services/learning/ai_reason.py` (new)

- `learning_reason_schema() -> dict` — a JSON schema `{"reason": "string"}` (mirrors
  `mission_reason_schema` shape).
- `build_learning_reason_messages(*, stage: StartupStage, course_titles: list[str], weakest_dimension: str
  | None) -> list[LLMMessage]` — a system + user message instructing a single ≤ ~200-char line explaining
  why these courses fit this founder's stage (and weakest dimension when present). Course titles and stage
  are the grounding floor; `weakest_dimension` is optional context.

#### A3. Endpoint — `app/api/v1/endpoints/learning.py` `get_recommendations`

After computing `recommended` / `continue_watching`, resolve the reason for `membership.startup_id`:

- Load the `LearningRecommendation` row (unique by `startup_id`).
- Determine `current_stage` (the same stage value already used to pick courses).
- If the row is absent, **or** `row.stage != current_stage`:
  - Upsert: set `stage = current_stage`, `reason = _templated_reason(current_stage)`,
    `status = "generating"` (create the row if absent).
  - Enqueue `job_dispatcher.enqueue(db, "ai.learning.recommendations", {"startup_id": str(startup_id)},
    startup_id)`.
- Add `recommendation_reason: <row.reason>` as a new top-level key in the response dict (additive;
  alongside `stage` / `recommended` / `continue_watching`).

`_templated_reason(stage)` (a small helper in the service): e.g. `f"Recommended for your
{stage.value.replace('_', ' ')} stage."`, or a stage-agnostic `"Recommended to get you started."` when
`stage is None`.

> **GET with a write:** this endpoint now upserts a row and enqueues a job. This is the same
> read-triggers-generation behaviour already shipped in `ai.dashboard.briefing`'s dashboard endpoint. The
> request session commits at request end (`get_db`), so the enqueued job sees a committed row.

#### A4. Worker — `app/worker/handlers/ai.py` `handle_learning_recommendations`

```
startup = db.get(Startup, job.payload["startup_id"]); if None: return
row = db.query(LearningRecommendation).filter_by(startup_id=startup.id).one_or_none()
if row is None or row.status != "generating": return          # idempotency
stage = <startup's current stage>
if stage is None:                                              # nothing to personalize
    row.status = "ready"; db.flush(); return                  # keep generic fallback, no LLM call
course_titles = [c.title for c in recommended_courses(stage, completed_ids=set())]
weakest = <latest assessment weakest dimension if trivially reachable, else None>
result = metered_complete_json(db, startup.id, build_learning_reason_messages(
            stage=stage, course_titles=course_titles, weakest_dimension=weakest),
            schema=learning_reason_schema(), max_tokens=settings.LLM_MAX_TOKENS)
if result is None: return                                      # over budget: keep fallback, stay generating
row.reason = str(result["reason"])[:300]; row.status = "ready"; db.flush()
```

`register_handler("ai.learning.recommendations", handle_learning_recommendations)`.

The worker recomputes the recommended set with `completed_ids=set()` purely to ground the prompt in the
stage's course titles; it does not need the caller's enrollments (the reason is stage-level, not
per-user-progress).

### Feature B — Module 21 context-aware journal prompt

#### B1. Data model — `app/db/models/journal.py` (append) + migration `0032`

`JournalPrompt(UUIDMixin, TimestampMixin, Base)` → table `journal_prompts`:

- `startup_id: Mapped[uuid.UUID]` — FK `startups.id` `ondelete="CASCADE"`, `index=True`.
- `founder_id: Mapped[uuid.UUID]` — FK `users.id` `ondelete="CASCADE"`, `index=True`.
- `date: Mapped[date]`.
- `prompt: Mapped[str]` — `String(300)`, non-null (always carries the static fallback at minimum).
- `status: Mapped[str]` — `Enum("generating", "ready", native_enum=False, length=16)`, non-null,
  `default="generating"`.
- `__table_args__ = (UniqueConstraint("startup_id", "founder_id", "date",
  name="uq_journal_prompts_startup_founder_date"),)`.

**Not encrypted.** The prompt is a question grounded only in operational signals (milestone/mission
names); it contains no diary content, so it never touches Fernet. Registered in
`app/db/models/__init__.py`. Migration `0032_journal_prompts` (`down_revision="0031_learning_recommendations"`).

#### B2. Prompt builder — `app/services/journal/ai_prompt.py` (new)

- `journal_prompt_schema() -> dict` — `{"prompt": "string"}`.
- `build_journal_prompt_messages(*, milestone_title: str | None, mission_title: str | None) -> list[LLMMessage]`
  — a system + user message asking for one warm, open reflective question (≤ ~200 chars) that references
  the founder's recent progress. **Its only inputs are the two operational strings.** It has no parameter
  through which journal content or mood could ever be passed — enforced by signature and asserted by test.

#### B3. Endpoint — `app/api/v1/endpoints/journal.py` `get_today_journal_prompt`

Response shape unchanged (`JournalPromptResponse(prompt=...)`). Before returning:

- Resolve `startup` + founder `user` (already done, incl. `_require_founder`).
- Load today's `JournalPrompt` for `(startup.id, user.id, today)`.
- If absent: create with `prompt = JournalService.get_prompt(today=today)` (the existing static value),
  `status = "generating"`; enqueue `job_dispatcher.enqueue(db, "ai.journal.prompt", {"startup_id":
  str(startup.id), "founder_id": str(user.id), "date": today.isoformat()}, startup.id)`.
- Return `row.prompt` (static first, AI once ready).

Founder-only auth is preserved (the enqueue only happens for founders, who are the only callers).

#### B4. Worker — `app/worker/handlers/ai.py` `handle_journal_prompt`

```
startup = db.get(Startup, job.payload["startup_id"]); if None: return
founder = db.get(User, job.payload["founder_id"]); if None: return
d = date.fromisoformat(job.payload["date"])
row = db.query(JournalPrompt).filter_by(startup_id=startup.id, founder_id=founder.id, date=d).one_or_none()
if row is None or row.status != "generating": return          # idempotency
milestone_title = <title of the most recent shipped RoadmapMilestone for startup, else None>
mission_title   = <current Mission title/focus for startup, else None>
if milestone_title is None and mission_title is None:          # no operational signal
    row.status = "ready"; db.flush(); return                  # keep static prompt, no LLM call
result = metered_complete_json(db, startup.id, build_journal_prompt_messages(
            milestone_title=milestone_title, mission_title=mission_title),
            schema=journal_prompt_schema(), max_tokens=settings.LLM_MAX_TOKENS)
if result is None: return                                      # over budget: keep static, stay generating
row.prompt = str(result["prompt"])[:300]; row.status = "ready"; db.flush()
```

`register_handler("ai.journal.prompt", handle_journal_prompt)`.

**Privacy invariant (load-bearing):** this handler imports and reads only `RoadmapMilestone` / `Mission`
(operational, `startup_id`-scoped). It must never import, query, or pass `JournalEntry.content` or
`MoodLog` into the prompt builder. Enforced by: (a) the builder's signature (only two string params); (b)
a unit test asserting the builder is called with operational strings only; (c) a code-level review check
in the task's Global Constraints.

## Data flow / FE contract

- **17:** `GET /learning/recommendations` response gains `recommendation_reason: str` (additive; always
  present — templated on first read, AI once ready). No other field changes.
- **21:** `GET /journal/prompts/today` response is **unchanged** (`{"prompt": "<string>"}`); the *value*
  may change from the static pool line to a personalized line on a later poll.
- **Over budget:** both keep the fallback and `status` stays `generating`; `GET /ai/status`'s
  `over_budget` flag is how the FE learns why personalization paused (cross-reference the existing
  `docs/fe-integration-guide-ai-status.md`).

## Error handling / concurrency

- Worker follows the no-commit convention (`db.flush()` only; the runner owns the transaction) and is
  fail-loud on real LLM errors — only a budget skip returns `None`, which is handled as "keep fallback".
- Idempotency: a second drained copy of the same job finds `status != generating` and no-ops. The unique
  constraints (one per startup for 17; one per startup+founder+date for 21) prevent duplicate rows under a
  concurrent first read; the endpoint upsert must tolerate the race (catch the unique violation / use
  `on_conflict_do_nothing` or a re-select) — spec the plan to use a get-or-create that re-selects on
  conflict, consistent with how the dashboard briefing row is created.
- **Autoflush caution** (`test-db-fixture-autoflush-gotcha`): production `SessionLocal` is
  `autoflush=False`. The endpoint creates the row then returns `row.reason`/`row.prompt` from the **same
  object** (no select-after-insert), so it is safe. The worker reads its row in a **separate committed
  transaction**, so it is safe. Any test that inserts then selects in one session must flush explicitly;
  at least one worker test should use an `autoflush=False` session to mirror production.

## Testing

Per feature:

- **Unit — endpoint:** first read creates the row with the fallback, sets `generating`, enqueues exactly
  one job of the right type/payload; the response carries the fallback value (17: new field present; 21:
  shape unchanged). A second read before the worker runs does not enqueue a duplicate (row already
  `generating`).
- **Unit — worker happy path:** with budget available (`LLM_DAILY_TOKEN_BUDGET` high) and the stub client,
  the handler writes the value and flips `status=ready`; monkeypatch target is
  `app.platform.llm_budget.get_llm_client` (per the budget slice's lesson — routing through
  `metered_complete*` resolves the client via `llm_budget`).
- **Unit — over budget:** seed `llm_usage_daily` over budget → handler returns without an LLM call, value
  unchanged, `status` stays `generating`.
- **Unit — idempotency:** row already `ready` → handler no-ops.
- **Unit — no-signal path:** 17 with `stage=None`, 21 with no milestone/mission → `status=ready`, fallback
  kept, **no LLM call** (assert the client was not called).
- **17 staleness:** a read at a new stage rewrites `stage`, resets `status=generating`, re-enqueues.
- **21 privacy:** assert `build_journal_prompt_messages` is invoked with operational strings only and that
  the handler never queries `JournalEntry` / `MoodLog` (e.g. the builder is called and its args contain no
  entry/mood data; a focused test that would fail if a diary field were threaded in).
- **Migration:** fresh-DB round-trip + `alembic check` drift-clean for `0031` and `0032`; single head.
- **E2E (stub):** drive each endpoint through the live harness, let the worker drain, capture the real
  response. Under the stub provider the captured value is `"[stub-llm] …"` — label it as the stub value in
  the FE guide (real OpenAI returns real text), consistent with the existing AI guides. Captures:
  `e2e/_captures/learning/recommendations.json`, `e2e/_captures/journal/prompt_today_ai.json`.
- Coverage ≥ 95%; DB-clean unit tests.

## Security & privacy

- **21 is the sensitive one.** The operational-signals-only rule is the primary control: no decrypted
  journal content and no mood/stress values are ever sent to the LLM. The prompt row is plaintext because
  it holds only a generated question referencing operational milestone/mission names.
- **17** sends only stage + public catalog course titles (+ optional assessment weakest-dimension label) —
  no PII.
- Both endpoints stay workspace/founder-scoped exactly as today; no auth change.
- Metering unchanged; `LLM_API_KEY` never logged.

## FE impact (integration guides)

- **Learning guide** (`docs/fe-integration-guide-learning*.md`, or a new
  `docs/fe-integration-guide-learning-recommendations.md` if none covers recommendations): document the new
  `recommendation_reason` field, the generating→ready upgrade (poll `/learning/recommendations` again to
  get the personalized line), and the over-budget behaviour (cross-ref ai-status). Payload verbatim from
  the capture.
- **Journal guide** (`docs/fe-integration-guide-journal.md`): note that `GET /journal/prompts/today`'s
  value may upgrade from a static line to a personalized one on a later poll; shape is unchanged; over-budget
  keeps the static line (cross-ref ai-status). Payload verbatim from the capture.

## Global constraints (carried into the plan)

- **No AI attribution** in any commit/PR/issue/comment (binds every subagent).
- **Reproduce CI locally & green before push:** black/isort/ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95%,
  **Migrations (round-trip + drift) — two new migrations `0031`/`0032`, single head**, e2e. Run through the
  project's pinned toolchain (`poetry run …`).
- DB-clean unit tests; worker no-commit convention (`db.flush()` only); seam fail-loud (only budget-skip
  returns `None`).
- SOP + checklist + both FE guides updated in the same pass; FE-guide payloads verbatim from live captures.
- **Privacy (21):** operational-signals-only is a hard requirement, not a preference — enforced in code
  and asserted by test.

## Follow-ups

- Per-course learning reasons; wiring assessment weakest-dimension as a ranking key and a richer reason
  input; mood/journal-aware prompts behind an explicit consent decision; event-driven regeneration; a
  distinct per-field "skipped for budget" terminal state.
