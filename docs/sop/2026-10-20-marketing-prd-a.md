# SOP: Marketing PRD gap build (a): scheduler, stats, links, metrics, export, media

| | |
|---|---|
| **What shipped** | Gap rows 2, 3, 4 (BE), 6, 7, 8, 13, 25, 26, 27, 34 of `cofoundaz/docs/checklist/marketing-prd-gaps.md`. Branch `claude/feat-marketing-prd-a`, PR #156. Slice (b) is stacked on it. |
| **Why** | A live walk found: scheduled posts never published (no marketing job), notifications had no CTA or deep link, campaigns had no stats or detail data, posts and copy could not be tied to a campaign, a mistyped metric was permanent, recording one number at two scopes double-counted the funnel, there was no export and no media slot (and the obvious reuse, Documents upload, is readable by every role). |

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| Scheduler scans for due `scheduled` posts every tick (60 s) and enqueues one job per (post, `scheduled_at`); the job re-locks the row (`SELECT … FOR UPDATE`) and re-checks status and time before flipping | same claim/enqueue pattern as the other scheduled tasks; a post edited back to draft, moved later or already published between scan and run is left alone; moving a post to a new time makes it claimable again | one bulk job per tick (one bad row blocks the batch); `skip_locked` (a post under edit would be claimed and never retried) |
| `published_at` = the time it was due; the event has **no actor** | the author is notified too (the scheduler did it, not a person); D-MKT-8: status only, nothing is sent to a platform | `now()` |
| Handler checks the post belongs to the payload's `startup_id` | the job payload is a hint, not an authority | trusting the payload |
| Notification contract: `body` = CTA "See results", `data.href` = deep link, `data.was_scheduled` picks "Scheduled post published" vs "Post published" | the repo convention (assessment quarterly: body is the CTA, payload carries `href`) | a new `cta` column |
| Campaign `metrics` (was always `{}`) now holds all-time totals + CTR + conversion rate from campaign-scoped points, one grouped query for the list | no new field to keep in sync, one query not N+1 | a separate `stats` key; a stored/denormalised counter |
| Performance endpoint: daily series + totals + `by_channel`, ranges 7d/30d/90d/all | the charts the PRD asks for. `by_channel` is the campaign's `channel_mix` applied to totals and always `estimated: true` (campaign-scoped points have no channel) | pretending to have per-channel campaign data |
| Single scope per metric point (422 if `channel` and `campaign_id` are both set) (D-MKT-7) | one event cannot be counted twice; existing rows are untouched (no back-fill) | de-duplicating in the aggregation |
| CAC by channel adds campaign-scoped spend/conversions split by `channel_mix`; `attributed_*` + `estimated` break out the estimate | PRD wants campaign spend visible per channel; honest labelling | hiding the estimate |
| Metric list is paginated (limit ≤ 500) with a `total`; delete is a hard delete | typo correction is the purpose | soft delete (nothing reads history) |
| CSV export streams raw points (not the aggregates), prefixes `'` on cells starting `= + - @ \t \r` | interim for "Add to report" (D-MKT-6); campaign names are user text, so formula injection is real | exporting only aggregates |
| Media lives in its own table `marketing_media`, storage key `marketing/{startup_id}/{uuid}.ext`, routes gated by `MARKETING_ROLES` | security row 2: Documents are all-roles; unpublished assets must not be visible to mentors/investors/consultants | a `module` column on `document_files` (one forgotten filter leaks) |
| Media accepts PNG/JPEG/GIF/WebP only, magic bytes must match the declared type, 5 MB, 200 per workspace | no SVG (script-bearing), no disguised files, bounded storage | trusting `Content-Type` |
| `content_calendar.media_id` / `campaign_id` and `marketing_ai_generations.campaign_id` are nullable FKs, `ON DELETE SET NULL` | deleting media or a campaign never deletes a post | cascade |

## What's involved
- Migration `0061_marketing_prd_a`: table `marketing_media`; columns `content_calendar.media_id`, `content_calendar.campaign_id`, `marketing_ai_generations.campaign_id` (all nullable, indexed, FK SET NULL). Downgrade drops all of it.
- `app/worker/scheduler.py`: `_due_marketing_posts`, `SCHED_MARKETING_PUBLISH`, batch cap 500 per tick. `app/worker/handlers/scheduled.py`: `handle_marketing_publish`.
- `app/services/marketing/service.py`: `publish_due_entry`, `_emit_published` (new payload), `serialize_entries` (media joined in one query), link validation.
- `app/services/marketing/analytics.py`: `campaign_stats`, `campaign_performance`, `_cac_by_channel`, `list_metrics`, `delete_metric`, `export_csv`. `campaigns.py`: `serialize_many`.
- `app/services/marketing/media.py` (new). `app/services/notifications/registry.py`: CTA and copy. `app/schemas/marketing.py`, `app/api/v1/endpoints/marketing.py`.
- New routes (all `MARKETING_ROLES`): `GET|DELETE /marketing/metrics[/{id}]`, `GET /marketing/analytics/export`, `GET /marketing/campaigns/{id}/performance`, `POST|GET /marketing/media`, `DELETE /marketing/media/{id}`. New params: `campaign_id` on entries and copy list/create, `media_id` on entries.
- Tests: `tests/api/test_marketing_prd_a.py` (35), `tests/test_marketing_prd_a_migration.py` (2); live journey `e2e/test_marketing_prd_a.py`; guide `docs/fe-integration-guide-marketing-prd.md`.

## Verification
TDD. Unit suite + coverage floor, black, isort, ruff, mypy, pylint, bandit, `alembic upgrade/check/downgrade base/upgrade` on scratch databases (numbers in the PR). Live e2e on a scratch DB with a real uvicorn: a post scheduled in the past was published by a scheduler tick + worker drain, `published_at` equal to the due time, the founder got the notification with `href` and "See results"; metric two-scope 422, list, delete, estimated CAC, CSV, media upload/attach/delete all captured in `e2e/_captures/marketing_prd_a/`.

## Operate / roll back
`alembic upgrade head` (CD runs it) and **restart the worker** (new scheduled job type). Rolling back: downgrade to `0060_assessment_stage_at_time` (drops the media table and the three link columns) and revert; posts already flipped to `published` stay published. Media files already uploaded stay in storage under `marketing/{startup_id}/` (delete them by prefix if needed).
Security review points: the scheduler job and its row lock, the media role gate and storage key, the CSV formula guard, the cross-workspace checks on `media_id` / `campaign_id` / metric ids.

## Follow-ups
- Slice (b): AI context, refine, platform limits, fit level, weekly readout, channel auto-status, keyword estimates, segment validation, one channel per post.
- The scheduled-post flip cannot post to a platform (D-MKT-8). Needs OAuth apps per platform when D-MKT-8 is revisited.
- Media bytes are served from the storage URL (Cloudinary), which is not authenticated; the protection is that the URL is only handed out by marketing-only routes and the key is random. Same as Documents today.
- FE: campaign detail page, "Recorded entries" table, scope picker, media slot, export button, deep links (gap list FE rows).
