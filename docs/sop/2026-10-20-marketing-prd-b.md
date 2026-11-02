# SOP: Marketing PRD gap build (b): AI context, refine, platform limits, fit level, weekly readout

| | |
|---|---|
| **What shipped** | Gap rows 5, 12, 16, 17, 18, 20, 21, 22, 24 of `cofoundaz/docs/checklist/marketing-prd-gaps.md`. Branch `claude/feat-marketing-prd-b`, stacked on `claude/feat-marketing-prd-a` (PR #156). PR #157. |
| **Why** | The live walk found Marketing prompts saw only name/industry/stage, so "Plan my first week" returned `[specific payment challenge]` and the channel split put 25% on a channel the fit notes called low priority; no Refine, no length limits, fit notes had no level, no weekly readout, launching a campaign left every channel "Not started", keyword numbers looked authoritative though typed by hand, and a segment `definition` accepted anything. |

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| One context builder (`ai_context.py`) feeds all six prompts: Business Builder problem/solution/UVP + pains, personas (4), positioning statement, segments (6), channels with status + latest fit level + fit note | the contradiction came from prompts not seeing each other's outputs; bounded sizes keep tokens in check | per-prompt ad-hoc context |
| Context goes in as a JSON block in `<context>` with `<` escaped; system prompt says it is data, never instructions; refine's draft and instruction and copy's key message / CTA are JSON-quoted | all of it is founder text (Validation #150/#151 treatment) | raw interpolation |
| Placeholder check: regex `\[...\]` on the output; **one** retry naming the problem; the retry wins, the first answer is kept if the budget is spent; never a loop | the prompt rule alone is not enough; bounded cost | unlimited retries; stripping brackets (would leave broken sentences) |
| Platform limits are a fixed table (`platforms.py`), `platform` optional on copy; stated in the prompt, retried once if over, `lengths` / `over_limit` returned, never silently truncated (D-MKT-3) | the FE shows `n / limit`; truncating would cut a sentence mid-word | per-channel-category limits (a category has no length); truncation |
| Refine = a NEW `copy` generation with the parent's inputs (original key message kept) + `inputs.refine {parent_generation_id, variant_index, instruction}`; parent must be `ready`; same job type and polling | no new table, history titles stay the key message, same FE polling code | mutating the parent; a chat thread table |
| Fit schema is `{level, note}` per channel; `marketing_channels.fit_level`; a bare string note is still accepted | PRD "High fit: ..." badge; old stored notes/tests unaffected | parsing the level out of the sentence |
| `stats_30d` on every channel row from channel-scoped points only | an honest per-channel number; campaign spend is attributed separately and labelled in Analytics | mixing in estimated attribution |
| Auto-status: only on draft to active, only channels with share > 0 that are `not_started`, to `testing` (D-MKT-4) | never overrides a founder's choice; pause/resume does not re-run | on every status change |
| Weekly readout: scheduler scan once per ISO week per active campaign with metrics in the last 14 days, from 08:00; job inserts the row first (unique `(campaign, week)`), then calls the model; prompt = numbers only (no name, no text); notification only when `ready` | idempotent, no tokens for empty campaigns, nothing user-written can steer the prompt | one job per workspace; including the campaign name in the prompt |
| Keyword `source: "manual"` + `metrics_updated_at` (changed only when volume/difficulty/rank actually change) | D-MKT-8: no SEO provider, so say who produced the number and when | pretending volume is measured |
| Segment `definition` validated against fixed attributes (location, business_type, company_size, income_band, channel_acquired, behaviour_tags), cleaned on write; old rows untouched (D-MKT-5) | FE renders chips safely | validating on read (would break old rows) |
| Row 12: no change; one `channel` per post stays and is tested (D-MKT-2) | decision recorded | multi-channel entries |

## What's involved
- Migration `0062_marketing_prd_b`: `marketing_channels.fit_level`, `seo_keywords.metrics_updated_at`, table `campaign_readouts` (unique `(campaign_id, week_start)`). Downgrade drops them.
- New: `app/services/marketing/ai_context.py`, `platforms.py`, `readouts.py`. Changed: `ai_prompts.py` (context + rules + refine + readout builders), `ai_content.py` (`refine_copy_generation`), `analytics.py` (`channel_stats`), `campaigns.py` (`_start_launched_channels`), `seo.py`, `app/schemas/marketing.py` (platform, refine, readout, segment validation), `app/worker/handlers/marketing_ai.py` (`_complete_checked`, copy/refine handler, fit levels, `handle_marketing_readout`), `app/worker/scheduler.py` (`_due_campaign_readouts`, `SCHED_MARKETING_READOUT`), `app/services/notifications/registry.py` (`marketing.campaign.readout.ready`), `app/db/models/enums.py` (`CopyPlatform`, `FitLevel`).
- New routes (all `MARKETING_ROLES`): `GET /marketing/copy/platforms`, `POST /marketing/copy/generations/{id}/refine`, `GET /marketing/campaigns/{id}/readouts`, `GET /marketing/segments/attributes`. Changed: `GET|PATCH /channels` (+`fit_level`, `stats_30d`), keywords (+`source`, `metrics_updated_at`), segments (definition validated).
- Tests: `tests/api/test_marketing_prd_b.py`, `tests/test_marketing_prd_b_migration.py`; live `e2e/test_marketing_prd_b.py`; guide `docs/fe-integration-guide-marketing-prd.md` §7 to §14.

## Verification
TDD. Unit suite + coverage floor, black, isort, ruff, mypy, pylint, bandit, alembic upgrade/check/downgrade base/upgrade on scratch databases (numbers in the PR). Live e2e on a scratch DB with a real uvicorn and the **stub model**: platform copy, refine, fit levels + stats, launch auto-status, a scheduler tick + worker producing the readout and its notification (once per week), keyword stamps, segment validation. **No real-model capture was taken**: the build session has no model key, so real-model text quality, the placeholder retry against a real model, and the readout prose are untested live (the prompts are unit-tested by capturing them). To run it: follow the recipe in `2026-10-19-validation-prd-b.md` against a scratch DB with a key and re-run `e2e/test_marketing_prd_b.py` with `LLM_PROVIDER=openai`, watching the daily 100k-token budget (a copy generation with a retry is ~2 calls).

## Operate / roll back
`alembic upgrade head` and **restart the worker** (new scheduled job type and handler). Rolling back: downgrade to `0061_marketing_prd_a` (drops `campaign_readouts`, `fit_level`, `metrics_updated_at`) and revert. Cost note: copy and Plan-my-first-week can now make a second model call when the first answer has a placeholder or is over the platform limit; the weekly readout adds one call per active campaign with data per week.

## Follow-ups
- Real-model run (above). If real answers still carry placeholders after the retry, consider a stricter schema or a post-filter.
- FE: Refine UI, counters, platform picker, History tab, fit badge + stat, readout card, segment chip builder, keyword "estimated" label.
- Row 1 (module grants) is decided closed by D-MKT-1; rows 9 to 11, 14, 15, 19, 23, 28 to 33 are FE.
