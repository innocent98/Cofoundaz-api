# SOP — Marketing re-walk: BE fixes (gaps 1, 4, 5, 7, 9)

| | |
|---|---|
| **Shipped** | Branch `fix/marketing-rewalk-be` → PR to `develop` (link in the PR) |
| **Date** | 2026-10-21 |
| **Area** | Marketing: `analytics.py`, `plan_slots.py`, `ai_context.py`, `ai_content.py`, `media.py`, `search/service.py`, `marketing_ai.py`, `endpoints/marketing.py` |
| **Source** | FE repo `docs/checklist/marketing-rewalk.md`, "Gaps found" |

| Gap | Cause | Fix |
|---|---|---|
| 1 Overview "AI content ideas" = 14 | `count_ai_content_ideas` summed the gaps of every ready run; the SEO page lists the newest only | Count the newest ready run (`ORDER BY created_at DESC LIMIT 1`) |
| 4 "Plan my first week" proposed a past slot | The proposals had only `day_offset` (0 = today), so the FE built "today 09:00" even after 09:00 | `plan_slots.assign_slots` adds `scheduled_at`: next 09:00 UTC after now, one per day, never before the entry's own `day_offset`, skipping slots already on the calendar. UTC because the workspace has no timezone |
| 5 Search hits link to the list | `_fixed(href)` for campaign/post | campaign → `/marketing/campaigns/<id>`, post → `/marketing/calendar?entry=<id>`; segment stays `/marketing/segments` (the FE has no segment detail route) |
| 9 Old off-brand ideas stay listed | Nothing recorded which context a run was written from | The content-gap handler stores `inputs.context_hash` (sha256 prefix of the brand context, channels excluded because launches change them). `GET /seo/content-gaps[/{id}]` returns `stale` (false/true, null while not ready). Runs without a hash read `true` once. No auto-regenerate (costs tokens, surprises the founder). Hash is stripped from `inputs` in responses |
| 7 Media preview impossible | `media.url` is a server path | `GET /marketing/media/{id}/file`, `MARKETING_ROLES` only, scoped to the workspace: local storage streams the file (path checked to sit under the storage root, `nosniff`, private cache), a hosted URL gets a 302. The FE loads it with `fetch` + blob because `<img>` cannot send the token |

No migration, no new config. Tests: `tests/api/test_marketing_rewalk.py` (11) plus the adjusted
overview test; live: `e2e/test_marketing_rewalk_be.py` (captures in `e2e/_captures/marketing_rewalk_be/`).
Not exercised: the Cloudinary redirect branch.

**Operate / roll back:** code only; revert the PR. Existing rows are untouched (the hash lives in
the existing JSONB `inputs`).

**Follow-ups:** a workspace timezone setting (slots are UTC); a segment detail route/deep link on
the FE; `media.url` could be dropped from the response once the FE uses the file route.
