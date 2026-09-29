# Module 10 — Marketing Hub, Slice 4: SEO Tools

**Status:** design approved (2026-09-29), ready for implementation planning.

## Context

Module 10 (Marketing Hub) slices merged to `develop`: S1 (Calendar + Channels), S2 (Campaigns +
Segments), S3a (AI copy + plan-week), S3b (AI channel-plan recommender + fit notes). The AI seam
(`marketing_ai_generations`, async 202→poll, `llm_budget` metering) is complete.

**Slice 4 = SEO Tools (PRD 10.6, `/app/marketing/seo`)** — all four PRD features, built to the PRD
but **cross-checked against the FE UI handoff** at `../cofoundaz/` (the standing rule: build against
the FE, not the PRD alone).

### FE cross-check (authoritative for shapes)

The built FE screen `cofoundaz/app/(dashboard)/marketing/seo/page.tsx` and the design comp
`../"# Cofoundaz Web App UI Build"/Marketing Hub.dc.html` **agree** and render only **two** of the
four features:

1. A **keyword table** — columns `Keyword · Volume · Difficulty · Rank` (read-only today; no
   add/edit UI, no visible "page" column, no status).
2. A **brand positioning statement** — a formatted sentence (5 bolded parts), display-only today.

The FE hook `cofoundaz/hooks/useMarketingApi.ts` is a **mock prototype** (all `useState`, calls no
backend). Its `SEOKeyword` type is the shape contract:
`{ id; keyword: string; volume: string (e.g. "2.4K"); difficulty: number; currentRank?: number; targetPage: string }`.
The positioning parts in the comp: `audience · need · product · category · differentiator`.

**Content-gap suggestions** and the **on-page checklist** appear in **neither** the comp nor the
build — they are **build-ahead** here (per owner decision "all 4 per PRD, FE-aligned where it
exists"). Their shapes are **provisional** until the FE defines them; the FE integration guide must
mark them so.

## Goal

Ship the SEO Tools backend: a keyword tracker (CRUD), a brand positioning statement (upsert),
an on-page checklist per tracked page, and an AI content-gap generator — with the keyword and
positioning shapes matched to the FE, and the two build-ahead features flagged provisional.

## Global constraints (carried verbatim where they bind)

- **CRUD/tenancy:** services `db.flush()` only; endpoints `db.commit()`; every query scoped by
  `startup_id` (from the caller's membership, never the body); cross-tenant id → `NotFound` (404).
- **RBAC:** every route behind `_marketing = require_role(MembershipRole.founder,
  MembershipRole.team_member)`; other roles → 403 before any row lookup.
- **Async AI pattern (content-gap):** POST → **202** `{id, status:"generating"}` after
  `flush` + `job_dispatcher.enqueue`; the endpoint commits. Worker `_load` guards
  `status != generating → return`; `metered_complete_json` → `None` (over budget) →
  `_fail_over_budget` (terminal `failed`, `error="over_budget"`, `output={}`) + return; else write
  `output` + `status="ready"`; flush only. Register handler; module already imported in
  `app/worker/__main__.py`.
- **`AppError.http_status`** (not `.status_code`); `get_db` does **not** auto-commit.
- **CodeQL (required check):** no mutating call inside an `assert` (bind to a var first); no
  implicit string concatenation inside a list literal.
- **No AI attribution** in any commit message, PR body, or review comment.
- Migration id ≤ 32 chars (this slice uses `0037_seo_tools`).

## Design

### 1. Keyword tracker (FE-aligned)

- New `seo_keywords` table: `startup_id` FK CASCADE (index), `keyword` (`String(200)`, NN),
  `volume` (`String(20)`, nullable — **display string** like `"2.4K"`, matching the FE type, not an
  int), `difficulty` (`SmallInteger`, nullable, 0–100), `current_rank` (`Integer`, nullable),
  `target_page` (`String(500)`, nullable — free-text URL/slug), timestamps.
- CRUD, all founder/team_member-gated:
  - `GET /marketing/keywords` → list (ordered `created_at desc`).
  - `POST /marketing/keywords` → create (`keyword` required; metrics optional).
  - `PATCH /marketing/keywords/{keyword_id}` → partial update (`exclude_unset`).
  - `DELETE /marketing/keywords/{keyword_id}` → 204.
- Response fields snake_case (`current_rank`, `target_page`); the FE guide maps them to the FE's
  camelCase (`currentRank`, `targetPage`) and documents `volume` as a string.
- Validation: `difficulty` 0–100 if present (422 otherwise); `keyword` non-blank.

### 2. Brand positioning statement (FE-aligned)

- New `brand_positioning` table, **one row per startup** (`startup_id` unique + index): `audience`,
  `need`, `product`, `category`, `differentiator` (each `String(300)`, nullable), plus a
  server-composed `statement` (`Text`, nullable) rebuilt on every write:
  `For {audience} who {need}, {product} is the {category} that {differentiator}.`
- `GET /marketing/positioning` → the row, or `{... all fields null, statement: null}` when unset
  (never 404 — a startup always has "no positioning yet").
- `PUT /marketing/positioning` → upsert (create if absent, else update the single row);
  recomposes `statement` from the five fields; blank/absent parts render as an empty span (the
  statement is still composed with whatever parts are present).

### 3. On-page checklist (build-ahead — provisional)

- New `tracked_pages` table: `startup_id` FK CASCADE (index), `url` (`String(500)`, NN,
  **unique per startup** — `uq_tracked_page_startup_url`), `checklist` JSONB (NN, default set on
  create), timestamps.
- `checklist` is a fixed set of standard on-page SEO items, each a boolean:
  `title_tag, meta_description, h1, keyword_in_intro, image_alt, internal_links, url_slug,
  mobile_friendly` — seeded all-`false` on create. A module constant `ON_PAGE_ITEMS` is the source
  of truth.
- Routes:
  - `GET /marketing/seo/pages` → list; each row includes `checklist` + derived `{completed, total}`.
  - `POST /marketing/seo/pages` → create (`url` required; checklist seeded all-false); duplicate
    url for the startup → 422.
  - `PATCH /marketing/seo/pages/{page_id}` → merge a partial `{item_key: bool}` into `checklist`;
    an unknown item key → 422 (validated against `ON_PAGE_ITEMS`).
  - `DELETE /marketing/seo/pages/{page_id}` → 204.
- **Provisional:** no FE consumes this yet; the FE guide marks the item set + shape as backend-chosen
  and subject to change when the FE screen is built.

### 4. AI content-gap suggestions (build-ahead — provisional)

- New `MarketingGenerationKind.content_gap` (`"content_gap"`, 11 chars — fits the existing
  `kind` `Enum(native_enum=False, length=12)` column; **no enum-length migration**).
- `POST /marketing/seo/content-gaps/generate` → **202** (no body). Worker
  `handle_marketing_content_gap`: reads the startup's `seo_keywords` (top ~10 by presence) + stage +
  industry, calls `metered_complete_json` with `content_gap_schema()` →
  `output = {gaps: [{title, target_keyword, angle}]}` (≤7, keys enumerated so the stub emits them);
  over-budget → terminal `failed`/`over_budget`.
- `GET /marketing/seo/content-gaps/{generation_id}` → poll (`get_generation(..., kind=content_gap)`,
  cross-tenant/kind-mismatch → 404).
- `GET /marketing/seo/content-gaps` → list history (`content_gap` kind only).
- **"Write it"** is an FE composition (no new endpoint): the FE passes a gap's `title` /
  `target_keyword` into the existing `POST /marketing/copy/generate`.
- **Provisional:** no FE consumes this yet; the FE guide marks the gap shape provisional.

### New enums

`MarketingGenerationKind` gains `content_gap` (11 chars). No other enum changes.

## Data flow, tenancy, transactions

Identical to prior slices. Services flush; endpoints commit; the content-gap worker owns its unit.
All keyword/page/positioning reads+writes and the generation poll are `startup_id`-scoped. The
content-gap worker's keyword read precedes any write (no add-then-select-in-same-txn issue).

## Components / files

- `app/db/models/enums.py` — add `MarketingGenerationKind.content_gap`.
- `app/db/models/marketing.py` — `SeoKeyword`, `TrackedPage`, `BrandPositioning` models.
- `alembic/versions/0037_seo_tools.py` — three new tables (single head off
  `0036_channel_fit_notes`).
- `app/schemas/marketing.py` — `KeywordCreate/Update/Response`, `TrackedPageCreate/Update/Response`,
  `PositioningUpsert/Response`; `ON_PAGE_ITEMS` set. Reuse `GenerationResponse` for content-gap.
- `app/services/marketing/seo.py` (new file — keeps `service.py` focused) — keyword CRUD, tracked-page
  CRUD + checklist-merge, positioning upsert + statement composition, `serialize_*`.
- `app/services/marketing/ai_prompts.py` — `content_gap_schema()` + `build_content_gap_messages(*,
  keywords, stage, industry)`.
- `app/services/marketing/ai_content.py` — `create_content_gap_generation`, `list_content_gap_generations`.
- `app/worker/handlers/marketing_ai.py` — `handle_marketing_content_gap`; register
  `ai.marketing.content_gap`.
- `app/api/v1/endpoints/marketing.py` — keyword (4) + tracked-page (4) + positioning (2) + content-gap
  (3) routes.
- Docs: FE guide `docs/fe-integration-guide-marketing-seo.md` (keyword + positioning verbatim from
  captures & FE-aligned; content-gap + checklist flagged provisional); SOP
  `docs/sop/2026-09-29-marketing-slice4.md`; `docs/checklist/PROJECT_CHECKLIST.md`.

## Review focus (inputs the spec implies but happy-path tests may miss)

- **Keyword `difficulty` out of range** (e.g. 150 or -5) → 422, not stored. — keyword task.
- **Tracked-page duplicate url** for the same startup → 422 (unique constraint respected, not a 500).
  — tracked-page task.
- **Checklist PATCH with an unknown item key** → 422; a known key merges without dropping the other
  items. — tracked-page task.
- **Positioning GET before any PUT** → 200 with all-null fields + `statement: null` (never 404); PUT
  twice updates the same single row (no duplicate per startup). — positioning task.
- **content-gap over-budget** → terminal `failed`/`over_budget`; **kind-mismatch** poll (a
  content_gap id via another kind's route, and vice-versa) → 404. — content-gap task.
- **RBAC** 403 for mentor/investor across all new routes. — endpoints task.

## Decisions (rulings settled during brainstorming)

- **D1 — keyword metrics are manual** (no external SEO-provider integration); `volume` stored as a
  **string** to match the FE `SEOKeyword.volume` type.
- **D2 — on-page checklist = a `tracked_pages` entity** (url unique per startup) + fixed-item JSONB
  checklist toggled via PATCH; keywords keep a free-text `target_page` (not FK-coupled).
- **D3 — brand positioning = a dedicated 1-per-startup table**, server-composed `statement`.
- **D4 — content-gap = a new `content_gap` generation kind** on `marketing_ai_generations`, grounded
  in the startup's tracked keywords + stage/industry; "Write it" is an FE composition into the
  existing copy generator.
- **D5 — all 4 per PRD, FE-aligned where the FE exists**: keyword + positioning shapes match the FE
  handoff; content-gap + on-page checklist are **build-ahead with provisional shapes** (no FE yet),
  flagged as such in the FE guide.

## Out of scope

External SEO-provider metric sync (manual only); Performance Analytics + `ai_content_ideas` count +
campaign `metrics` (Slice 5); a server-side link between a content-gap and the copy it seeds (FE
composition); FE editing UI for keywords/positioning (the FE renders read-only/static today).
