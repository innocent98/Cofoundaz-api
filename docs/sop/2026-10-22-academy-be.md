# SOP: Learning Academy backend (real content, YouTube video, certificates, notes, recommendations)

| | |
|---|---|
| **What shipped** | Academy BE gaps from `cofoundaz/docs/checklist/academy-prd-gaps.md`: every row marked BE or both: **1, 2, 4, 5, 6, 7, 8, 9, 10, 13, 14, 16, 17, 20, 23**, and **24** (decided: `learning.course.completed` stays an audit event, see Follow-ups). Rows marked FE (3, 11, 12, 15, 18, 19, 21, 22) are untouched. Branch `feature/academy-be`, migration `0069_academy_content` (chained on `0068_sales_sequences`, re-chained 2026-10-24 after #171 and #172 merged). PR: see the checklist entry. |
| **Why** | The catalog was placeholder code, Download PDF and Share were fake, no handler existed for `learning.certificate.generate` (every job failed "no handler"), notes had no storage, "AI-picked" meant the same stage list for everyone, and search could not find a course. |

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| **D-ACA-1** content is markdown in `content/academy/` (courses with a folder of lessons, paths, articles; front-matter `slug title stage_tags level duration order video review_status`). `python -m app.services.learning.content` validates and upserts by slug, prunes rows the files dropped (a course with enrolments or certificates is kept) | repo-reviewable, no CMS yet, same read endpoints later fed by Module 25.4 | content in code; a seed JSON; a DB-first CMS now |
| Loader fails loudly (`ContentError` lists every problem; non-zero exit) on: bad slug/level/stage/gap tag, empty body, `[Placeholder]` text, `placeholder://`, em or en dash, unknown course in a path, duplicate slugs, bad video | a broken file must block the deploy, not ship | warn and skip |
| `review_status: draft` is required on every file; a course is `reviewed` only if it and all its lessons are; the API returns it on courses, paths, articles | FE shows a "Draft" badge until Adebayo reviews | a global flag |
| Deploy runs the loader in the `migrate` one-shot after `alembic upgrade head` (`docker-compose.prod.yml`); the image now copies `content/` (Dockerfile, `.dockerignore`) | every deploy converges the catalog to what shipped | loading at API start (races across workers) |
| **D-ACA-2** `lessons.video_id` stores only an 11-character YouTube id. `parse_youtube_id` accepts a bare id or an https URL from youtube.com / m. / youtu.be / youtube-nocookie.com (`watch?v=`, `/embed/`, `/shorts/`, `/live/`), rejects everything else. API: `video: {provider: "youtube", id, embed_url: "https://www.youtube-nocookie.com/embed/{id}"}` or `null`. No video in the seeded content | the embed URL is built from a validated id, never from stored text | storing URLs; signed hosting |
| **D-ACA-5** certificates: row gets `holder_name` (frozen), `verify_code` `CFZ-XXXX-XXXX-XXXX` (alphabet without 0 O 1 I, unique, 32^12 = 2^60 values), `revoked_at`. PDF is rendered by the worker handler `learning.certificate.generate` with reportlab (A4 landscape, issuer, holder, course, date, credential ID, QR to `{APP_BASE_URL}/verify/{code}`) and stored in `certificate_files` (bytes in the DB, so access control is the route and nothing is world-readable) | one template; no storage-backend or public-URL question | Cloudinary or local file (public or backend specific URLs) |
| PDF access: `GET /learning/certificates/{id}/pdf` filters by `startup_id` AND `user_id` AND not revoked; any other id is a 404 (same as a typo), not yet rendered is a 409 `CERTIFICATE_NOT_READY` | no enumeration, no cross-member leak | 403 for others (confirms the id exists) |
| Public `GET /api/v1/verify/{code}`: only the credential code; returns `valid, holder_name, course_title, issued_on, issuer`. Unknown or malformed: 404. Revoked: `{valid: false}`. `@limiter.limit("30/minute")` per caller | minimal disclosure, brute force of 32^12 codes is impractical at 30 a minute | returning 200 `valid:false` for unknown (same leak, worse API) |
| LinkedIn URL built server side with `organizationName=Cofoundaz`; optional `ACADEMY_LINKEDIN_ORG_ID` switches to `organizationId` | per Adebayo; hook ready for the company page | requiring the id |
| PDF font is built-in Helvetica; letters outside cp1252 are decomposed (`Ọ` to `O`), the rest dropped | no font file to ship; names stay readable | embedding a TTF (later) |
| **D-ACA-3** recommendations: shelf = courses for the stage, unfinished, ranked by gap overlap, then beginner first, then catalog order. Gaps = Assessment dimensions under 60 (two weakest; money also means pricing) plus Validation signals (no interview logged = `customers`, no experiment = `demand`). `learning_recommendations` is now one row per (workspace, hero course) with a `context_key` (hash of stage and gaps); a changed key resets the reason to a template and queues `ai.learning.recommendations` again. Strict-mode schema (walker test), prompt data inside a fenced `<context>` block, budgeted via `metered_complete_json`. A stale job (older key) is a no-op | one reason per hero, regenerated only on change | a reason per shelf; a timer |
| **D-ACA-4** any lesson order; `PATCH progress` takes `completed: true` and/or `position_s` (0 to 86400); positions live in `lesson_positions` (completion semantics of `lesson_progress` untouched). Course summary adds `last_lesson_id`, `next_lesson_id`, `position_s`; `continue_watching` is only started, unfinished courses | Continue opens the right lesson | position column on `lesson_progress` |
| Notes: `lesson_notes` unique per (workspace, user, lesson); read and write take the caller's ids only; never in search, never in AI prompts; 20000 character cap | private by construction | keying by lesson alone |
| Notifications: `learning.course.completed` ("Certificate earned: {course} 🎓", body "Download it") to the learner; `learning.course.recommended` ("New course recommended for your stage: {course}", "Start course") when a new hero row appears after the stage changed and the reader had an earlier row. Both F+TM audience, in-app only | PRD 17 copy | notifying every member |
| Search: `course` and `article` sources for founder and team member; titles and summaries only | notes stay out | indexing transcripts |

## What's involved
- Migration `alembic/versions/0069_academy_content.py`: tables `academy_courses`, `academy_lessons`, `academy_paths`, `academy_articles`, `lesson_notes`, `lesson_positions`, `certificate_files`; `certificates` + `holder_name`, `verify_code` (unique), `revoked_at` (existing rows backfilled with the profile name and a fresh code); `learning_recommendations` + `course_id`, `context_key`, unique `(startup_id, course_id)` (the cache is emptied, it regenerates on the next read).
- Code: `app/services/learning/{content,catalog,service,recommendations,certificates,ai_reason}.py`, `app/api/v1/endpoints/{learning,verify}.py`, `app/schemas/learning.py`, `app/worker/handlers/{learning,ai}.py`, `app/services/notifications/{registry,audience}.py`, `app/services/search/service.py`, `app/db/models/learning.py`, `app/core/config.py` (`ACADEMY_LINKEDIN_ORG_ID`).
- Content: `content/academy/` (8 courses, 24 lessons, 6 paths, 10 articles, all `draft`).
- Deps: `reportlab` (+ `pillow`, `charset-normalizer`), `pyyaml` (already locked). `pip-audit` clean for the new packages.
- Ops files: `Dockerfile`, `.dockerignore`, `docker-compose.prod.yml` (migrate command), `scripts/e2e_run.sh` (loads content).
- Docs: `docs/fe-integration-guide-academy.md` (new), captures `e2e/_captures/learning/`, this SOP, the checklist.

## Verification
- Unit and API tests: content loader and sync (51), service, recommendations, certificates (PDF, codes, scoping, public verify), worker handlers, concurrency (real sessions), notifications, search, models, migration including a backfill round trip, 135+ API tests including the role matrix, the owner-only PDF and the verify rate limit. Full suite and coverage figures are in the PR.
- Live e2e: `e2e/test_learning.py` against a real server and scratch Postgres (one journey, 20+ captures, a real PDF).
- Not verified live: the real-model reason text (stub model only), the LinkedIn dialog (URL shape only), team-member and mentor isolation (unit tests), the revoked-credential shape (no revoke feature), a lesson with a video (none exists).

## Operate / roll back
- Deploy needs nothing extra: the `migrate` service now also runs the loader. Locally and in the dev stack run `python -m app.services.learning.content` after `alembic upgrade head` (the dev compose does not run it for you).
- Editing content: change the markdown, deploy (or run the loader). To go live with a video: put the YouTube URL or id in the lesson's `video:` front-matter. To mark reviewed: set `review_status: reviewed` on the course and every lesson.
- `ACADEMY_LINKEDIN_ORG_ID` defaults to `146743971` (the Cofoundaz LinkedIn company page, given by Adebayo 2026-10-24; public, so no env change is needed, and an env value overrides it). `APP_BASE_URL` (the verify link and QR use it; required in staging and prod).
- Roll back: `alembic downgrade 0068_sales_sequences` drops the new tables and columns; certificates keep their rows. Old code reads `catalog.py` placeholders again only if redeployed, which is not recommended. Certificate PDFs live in the DB and go with the downgrade.
- To revoke a credential today: `UPDATE certificates SET revoked_at = now() WHERE verify_code = 'CFZ-...'` (the verify route then answers `valid: false`, and the owner's list and PDF stop).

## Follow-ups
- FE: everything in `docs/fe-integration-guide-academy.md` (course page, reader, notes, resume, certificates actions, public `/verify/[code]` page, draft badge, Learn next hero).
- Real videos (set `video:`), Adebayo's review of the 8 courses and 10 articles (flip `review_status`), the LinkedIn company id.
- Team-progress view and CMS (Module 25.4) are out of scope. Gap 24 (complete a course ticks a Roadmap task or lifts Health) is not built; `learning.course.completed` is now consumed only by Notifications.
- Behind a proxy, the public verify limit keys on the connecting address; if all traffic arrives from one proxy address the 30 a minute is shared (the same limit behaviour as the other public routes).
- Names with letters outside Western European Latin are folded on the PDF; embed a font if that matters.

## Update 2026-10-25: content reviewed
Adebayo read all 48 content files (6 paths, 8 courses with their lessons, 10 articles) and approved them as they stand. Every file now
carries `review_status: reviewed` (PR `chore/academy-content-reviewed`), so production shows them without the Draft badge. Tests that
asserted the shipped content was a draft now assert `reviewed`. New content files still start as `draft` until reviewed.
**Operate:** after merge, reload content on each environment: `docker compose exec -T api python -m app.services.learning.content`.
