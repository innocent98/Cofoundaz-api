# SOP — Module 10 Marketing Hub, Slice 4: SEO Tools (Keyword Tracker, On-Page Checklist, Brand Positioning, AI Content-Gap)

**What shipped** — Module 10's SEO Tools (PRD 10.6), four sub-features under `/marketing`:

1. **Keyword tracker** — CRUD over founder-entered keywords with manual metrics (`volume` as a
   **string**, `difficulty` 0–100, `current_rank`, `target_page`).
2. **On-page checklist** — tracked pages (`/marketing/seo/pages`) carrying a fixed 8-item boolean
   checklist, seeded all-false, merge-toggled by `PATCH`, with server-computed `completed`/`total`.
3. **Brand positioning** — one record per workspace (`GET`/`PUT /marketing/positioning`): five
   fields plus a server-composed `statement`.
4. **AI content-gap** — `POST /marketing/seo/content-gaps/generate` → 202 → poll, on the same
   `marketing_ai_generations` table and async seam as Slices 3a/3b (new `kind` value `content_gap`),
   grounded in the workspace's tracked keywords; new worker job `ai.marketing.content_gap`.

Migration **`0037_seo_tools`** (three new tables: `seo_keywords`, `tracked_pages`,
`brand_positioning`), 13 new `/marketing` routes behind the existing
`require_role(founder, team_member)` dependency.

Commits (branch `feat/module-10-marketing-slice4`, off Slice 3b's merge point (PR #104, base
`e7527e5`); not yet merged, no PR opened yet), oldest to newest:
`7481fc3` (design spec) → `9904378` (implementation plan) →
`050824d` (SEO tables + `content_gap` kind, migration `0037_seo_tools`) →
`9e5239f` (keyword tracker CRUD, FE-aligned shapes) →
`215b583` (fix: reject explicit-null `keyword` on PATCH — 422 not 500) →
`5655647` (on-page checklist via `tracked_pages`) →
`346d4ad` (test: session usable after dup-url 422) →
`f7c1051` (fix: add tracked page inside SAVEPOINT) →
`254f6a1` (brand positioning: upsert + composed statement) →
`6be03a6` (fix: positioning statement null when all parts blank) →
`84beeb2` (AI content-gap: `ai.marketing.content_gap`, 202 + poll) →
`00b5373` (e2e SEO journey + 8 captures) → this docs commit.

## Why

Slice 4 of 5 in the Module 10 plan: **SEO Tools** (PRD 10.6). Before building, the four sub-features
were cross-checked against the FE's current UI (the `cofoundaz` handoff), and they split cleanly:

| Sub-feature | FE status | Consequence |
|---|---|---|
| Keyword tracker | An FE screen exists (`SEOKeyword` type) | **FE-aligned** — response shape and field types follow the FE type |
| Brand positioning | An FE screen exists (positioning-statement builder) | **FE-aligned** — 5 parts + composed statement |
| On-page checklist | **No FE screen yet** | **Build-ahead** — shape is provisional |
| AI content-gap | **No FE screen yet** | **Build-ahead** — shape is provisional |

Build-ahead work was included because the PRD scopes it into 10.6 and the async AI seam makes it
cheap, but its shapes are explicitly labelled provisional in the FE guide so the FE is not misled
into treating them as a contract.

**Deliberately deferred** (per the design spec): pulling live keyword metrics from an external
SEO provider (metrics are manual for now); the `ai_content_ideas` Overview count; Performance
Analytics (Slice 5).

## How

**Manual metrics, `volume` stored and returned as a string** (design decision D1). The FE's
`SEOKeyword.volume` is a string (`"2.4K"`), so the column is `String(20)`, not an integer — the
backend does no parsing or normalization. `difficulty` is `int` with a `0..100` `Field` constraint
(out-of-range → 422 via the request schema); `current_rank` is `int >= 0`. Response fields are
snake_case (`current_rank`, `target_page`); the FE maps to its camelCase — same convention as every
other endpoint in this API (no camelCase aliasing on the server).

**`PATCH {"keyword": null}` → 422, not 500** (fix `215b583`). `KeywordUpdate` uses
`model_dump(exclude_unset=True)` for partial updates, which means an explicit `null` on the
non-nullable `keyword` column would otherwise reach the DB as a `NOT NULL` violation (500). A
`field_validator("keyword")` rejects an explicit null. Optional metric fields legitimately accept
`null` (clears them).

**On-page checklist: fixed 8 items, JSON column, merge-toggle** (design decision D2). `ON_PAGE_ITEMS` (`title_tag`,
`meta_description`, `h1`, `keyword_in_intro`, `image_alt`, `internal_links`, `url_slug`,
`mobile_friendly`) is the single source of truth. `tracked_pages.checklist` is a JSON column seeded
all-false on create; `PATCH` accepts a partial `{item: bool}` map, **merges it over the stored
values** (each stored item normalized to a bool first), and rejects any unknown key with 422.
`completed`/`total` are computed at serialize time, never stored. A JSON column instead of eight
boolean columns keeps the migration small and lets the checklist evolve without a schema change —
tradeoff: no DB-level constraint on the keys, so the service is the only guard (hence the explicit
unknown-key 422).

**Duplicate url per workspace → 422 via SAVEPOINT** (fixes `f7c1051`/`346d4ad`). `tracked_pages`
has a `UNIQUE (startup_id, url)`. The row is `add()`ed and `flush()`ed **inside `begin_nested()`**,
and the `IntegrityError` is translated into a 422 `VALIDATION_ERROR`. An earlier cut did the `add()`
outside the SAVEPOINT, which left the failed row in `session.new` and re-flushed it on the next
query, poisoning the request's session — the fix moves `add()` inside, mirroring `list_channels`'
seeding pattern from Slice 1. A dedicated test proves the session is still usable after the 422 and
that only the first page persisted.

**Brand positioning: one row per workspace, composed statement, null when blank** (design decision
D3). `brand_positioning` has `UNIQUE (startup_id)`. `PUT` is a **full replace** — all five fields
are overwritten from the payload (an omitted field becomes `null`), and `statement` is recomputed
server-side by `compose_statement` using the template `For {audience} who {need}, {product} is the
{category} that {differentiator}.` `GET` before any `PUT` returns **200 with all six fields
`null`** (never 404) so the FE has one empty shape. Fix `6be03a6`: when all five parts are blank the
composed statement is **`null`**, not a dangling `"For  who ,  is the  that ."` skeleton — keeping
the all-blank `PUT` result identical to the never-saved `GET`. A *partially* filled `PUT` still
composes the template with blank gaps; that is a known, untested edge (see Follow-ups).

**Content-gap: same async seam, grounded in tracked keywords, no new table** (design decision D4).
`MarketingGenerationKind` gains `content_gap` (11 chars — fits the existing 12-char `kind` column,
so no enum-length migration; same reasoning as Slices 3a/3b's one-table decision).
`create_content_gap_generation` flushes a `generating` row and enqueues `ai.marketing.content_gap`
with `generation_id`. `handle_marketing_content_gap` loads the generation, reads the workspace's
**10 most recent** `SeoKeyword`s plus stage/industry from the `Startup` row, calls
`metered_complete_json` with `content_gap_schema()` (`{gaps: [{title, target_keyword, angle}]}`,
`maxItems: 7`), and writes `output = {"gaps": [...]}` (non-dict entries dropped, capped at 7).
Over-budget uses the shared `_fail_over_budget` helper → `status: "failed"`, `error:
"over_budget"`, HTTP 200 on poll. Routes are kind-scoped (`get_generation(..., kind=content_gap)`),
so a mismatched id 404s like a cross-tenant one. Unlike plan-week/channel-plan/fit-notes,
content-gap **has a history list** (`GET /seo/content-gaps`), since the FE is expected to browse
past suggestions. "Write it" on a gap is a pure FE composition into the existing
`POST /marketing/copy/generate` — no new endpoint, no server-side link.

## What's involved

**Migration `0037_seo_tools`** (`alembic/versions/0037_seo_tools.py`, chains off
`0036_channel_fit_notes`, sole alembic head) — three additive `create_table`s: `seo_keywords`,
`tracked_pages` (`UNIQUE (startup_id, url)`), `brand_positioning` (`UNIQUE (startup_id)`). No
existing table touched; reversible downgrade drops all three.

**Enums** — `app/db/models/enums.py`: `MarketingGenerationKind` gains `content_gap`.

**Models** — `app/db/models/marketing.py`: `SeoKeyword`, `TrackedPage`, `BrandPositioning`.

**Schemas** — `app/schemas/marketing.py`: `KeywordCreate`/`KeywordUpdate`/`KeywordResponse`,
`TrackedPageCreate`/`TrackedPageUpdate`/`TrackedPageResponse`, `PositioningUpsert`/
`PositioningResponse`.

**Service** — `app/services/marketing/seo.py` (new): keyword CRUD, `ON_PAGE_ITEMS`,
`create_page`/`update_page_checklist`/`list_pages`/`delete_page`/`serialize_page`,
`compose_statement`, `get_positioning`/`upsert_positioning`/`serialize_positioning`.
`app/services/marketing/ai_content.py` (extended): `create_content_gap_generation`,
`list_content_gap_generations`.

**Prompt builders** — `app/services/marketing/ai_prompts.py` (extended): `content_gap_schema`,
`build_content_gap_messages(*, keywords, stage, industry)`.

**Worker** — `app/worker/handlers/marketing_ai.py` (extended): `handle_marketing_content_gap`,
registered as `ai.marketing.content_gap`.

**Endpoints** — `app/api/v1/endpoints/marketing.py` (extended), all behind the existing
`_marketing = require_role(founder, team_member)`:

| Route | Purpose |
|---|---|
| `GET` / `POST /marketing/keywords` | list / create keyword |
| `PATCH` / `DELETE /marketing/keywords/{id}` | update / delete (`{"deleted": true}`) |
| `GET` / `POST /marketing/seo/pages` | list / create tracked page |
| `PATCH` / `DELETE /marketing/seo/pages/{id}` | merge-toggle checklist / delete |
| `GET` / `PUT /marketing/positioning` | read / full-replace positioning |
| `POST /marketing/seo/content-gaps/generate` (202) | start a content-gap generation |
| `GET /marketing/seo/content-gaps/{id}` | poll |
| `GET /marketing/seo/content-gaps` | history |

**Tests**
- `tests/db/test_seo_models.py` + `tests/test_seo_tools_migration.py` — `content_gap` kind exists,
  the three models persist, single head `0037`, upgrade/downgrade round-trips.
- `tests/services/marketing/test_ai_prompts.py` (extended) — `content_gap_schema` shape +
  message builder.
- `tests/worker/test_marketing_ai_handlers.py` (extended) — content-gap happy path grounded in
  keywords, over-budget failure.
- `tests/api/test_marketing_seo.py` (new) — keyword CRUD round trip, difficulty out-of-range 422,
  explicit-null `keyword` 422 + partial update, RBAC 403; tracked-page seed + merge-toggle,
  dup-url 422 with session still usable, unknown-key 422; positioning GET-before-PUT, all-blank
  PUT, upsert + single-row; content-gap 202 + poll + history, kind-mismatch 404, RBAC 403 incl. the
  poll route.
- `e2e/test_marketing.py::test_marketing_seo_journey` (new) — keyword create/list/patch → tracked
  page create + tick `h1` → positioning PUT → content-gap generate → drain worker → poll; **8 live
  captures** under `e2e/_captures/marketing/` (`keyword_created`, `keywords_list`,
  `keyword_updated`, `tracked_page_created`, `tracked_page_checklist`, `positioning_upserted`,
  `content_gap_accepted`, `content_gap_ready`).

**Errors / API surface — additive only** (no existing route or shape changed): `VALIDATION_ERROR`
(422) — `difficulty` out of range, explicit-null `keyword`, duplicate tracked-page `url`, unknown
checklist key; `FORBIDDEN` (403) for non-marketing roles (rejected before any row lookup);
`NOT_FOUND` (404) for missing/cross-tenant ids or a kind-mismatched content-gap id. A
budget-exhausted content-gap generation is **not** an HTTP error — 200 poll with `status: "failed"`,
`error: "over_budget"`.

**Docs**
- `docs/fe-integration-guide-marketing-seo.md` (new) — every success body pasted verbatim from the
  8 captures; error/DELETE/history shapes labelled not-captured; keyword tracker + positioning
  marked FE-aligned, on-page checklist + content-gap marked **build-ahead / provisional**; cross-refs
  `docs/fe-integration-guide-ai-status.md`; verification table.
- `docs/checklist/PROJECT_CHECKLIST.md` (Slice 4 marked complete).

## Verification

**Unit/integration:** this slice adds tests for models/migration, keyword CRUD, checklist +
SAVEPOINT, positioning, and content-gap prompts/worker/endpoints (listed above), landed with each
feature commit. The whole-repo unit + CI-parity run is owned by the controller before the PR is
opened and is not reproduced by this docs commit.

**Live e2e (`bash scripts/e2e_run.sh`, full suite): 57 passed**, including the new
`test_marketing_seo_journey`. That journey's captures are the source of every success body in the FE
guide. `alembic heads` — single head `0037_seo_tools`.

**Honest gap disclosure.** The e2e journey only exercises the happy paths under the stub LLM
provider. Not e2e-captured (unit-tested or source-derived only, and labelled as such in the FE
guide): all 422/403/404 error responses; the `DELETE` bodies (`{"deleted": true}` for keywords is
read from the endpoint; tracked-page `DELETE` has no dedicated test); `GET /seo/pages`;
`GET /positioning` before any `PUT`; content-gap history; the `over_budget` failure for this kind;
and real-provider content-gap output (the stub emits `"[stub-llm] …"` placeholders and a single
gap).

## Operate / roll back

**New deploy-time requirement: none.** The new worker handler runs in the existing worker process
via the existing job dispatcher, same as every other `ai.*` job; no new config beyond the LLM
seam's existing settings. Run `alembic upgrade head` to apply `0037_seo_tools`.

**Rollback:** revert this slice's commits as a unit (`7481fc3..00b5373`, plus this docs commit) and
downgrade (`poetry run alembic downgrade 0036_channel_fit_notes`), which drops the three new
tables. Safe — nothing else reads them, and the `content_gap` `kind` value is additive (no existing
row uses it; any `content_gap` rows would need deleting first if the enum value is later removed).
Downgrade the migration only *after* the app code is rolled back, same ordering as every slice in
this module.

## Follow-ups

- **External SEO-provider sync** — keyword `volume`/`difficulty`/`current_rank` are all
  founder-entered; wiring a provider (and a refresh job) is a separate future slice. `volume` stays a
  string, so a provider would need to format its numeric volume to fit.
- **Content-gap and on-page-checklist FE screens** — both are **build-ahead**: no FE screen consumes
  them yet, so their shapes are provisional. When the FE is built, reconcile the guide and adjust
  the API if needed.
- **Partially-filled positioning `PUT`** composes the template with blank gaps (e.g. `"For x who ,
  Kolo is the  that ."`); `statement` is `null` only when all five parts are blank. Untested edge —
  decide whether to require all five parts or compose more gracefully once the FE builder is tested.
- **`DELETE /seo/pages/{id}`** has no dedicated test or e2e capture.
- **Slice 5 — Performance Analytics** remains (fills `Overview.top_channel_by_conversions` and
  campaign `metrics`, currently always `{}`), as do the older noted gaps
  (`Overview.ai_content_ideas` and `Overview.active_campaigns` still `null`).
