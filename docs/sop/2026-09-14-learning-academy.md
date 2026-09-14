# SOP — Learning Academy (Module 17)

**What shipped** — A learning hub inside the workspace: eight routes under `/api/v1/learning` to
browse a catalog of courses, lessons, learning paths and articles, enrol, mark lessons complete,
and earn a certificate when a course reaches 100%. A deterministic "recommended for you" shelf is
returned together with a "continue watching" row. Access is **founders and team members only,
reads included**. Progress is **personal and per workspace**: every read and write filters on both
`startup_id` and the caller's `user_id`. The catalog lives in code and is **labelled placeholder
content — real content is required before go-live**. Three new tables (`enrollments`,
`lesson_progress`, `certificates`) in migration `0019_learning`.

Commits (branch `feat/learning-academy`, PR into `develop`):

- `feat(learning): enrolments, lesson progress and certificates tables` — Task 1
- `feat(learning): placeholder in-code course catalog` — Task 2
- `feat(learning): enrolment, lesson completion, certificates, recommendations and path progress`
  — Tasks 3 and 4 in one commit, because both build `app/services/learning/service.py`
- `feat(learning): /learning endpoints and router registration` — Task 5
- the Task 6 commit — smoke routes, live e2e, FE guide, this SOP, checklist

Design: `docs/superpowers/specs/2026-09-11-learning-academy-design.md` (decisions D1–D10, agreed
with the lead on GitHub). Plan: `docs/superpowers/plans/2026-09-11-learning-academy.md`.

## Why

PRD Module 17 (lines 630–642) asks for a learning hub: courses made of ordered lessons, learning
paths, articles, enrolment with progress, and a certificate on completion, with a recommended shelf
on the front page. Nothing in the codebase held learning content or learning progress before this
module.

v1 is deliberately small and honest about it: no AI recommendations (Module 03), no video hosting,
no PDF rendering, no authoring surface (Module 25.4), and placeholder content labelled on every
title so it can never be mistaken for the real catalog.

## How

**In-code catalog, shaped for Module 25.4 (D1).** `app/services/learning/catalog.py` is a
read-only, versioned registry (`LEARNING_CATALOG_VERSION = 1`) of frozen dataclasses, following the
in-code registry convention of the roadmap and document templates. A `Course` owns an ordered tuple
of `Lesson`s, a `LearningPath` references courses **by id, in order** rather than copying them, and
an `Article` stands alone. The shape maps one-to-one onto tables, so moving the catalog into the
database when the Module 25.4 CMS lands is mechanical. Lesson and course durations are derived,
never stored.

**Ids are stable, and lesson ids are unique catalog-wide.** Enrolment, lesson-progress and
certificate rows store catalog ids, so renaming one silently orphans people's records. Lesson ids
are `<course_id>-<n>`, unique across the whole catalog, because `lesson_progress` is unique on
`lesson_id` and `PATCH /learning/lessons/{lesson_id}/progress` addresses a lesson by id alone.

**Placeholder content (D8).** Six courses — one tagged for each stage from idea to scale, across
beginner, intermediate and advanced — two paths (three courses and two courses) and two articles.
Every course, lesson, path and article title starts with `[Placeholder] `;
`test_content_is_labelled_placeholder_and_realistic_in_shape` fails if one does not.

**Race-safe get-or-create for enrolment (D9).** `get_or_create_enrollment` selects the
`(startup_id, user_id, course_id)` row; if it is missing, it inserts inside `db.begin_nested()`, and
on `IntegrityError` re-selects the row a concurrent caller just committed — the same pattern as
`get_or_create_canvas`. Both `POST /learning/enrollments` and the first lesson completion in an
unenrolled course go through it, so automatic enrolment leaves the caller in exactly the same state
as enrolling first.

**The enrolment row lock keeps progress from drifting.** `complete_lesson` re-reads the enrolment
with `SELECT … FOR UPDATE` before it records the lesson and recomputes the percentage. Without the
lock, two simultaneous completions of *different* lessons in a two-lesson course could each count
only their own lesson, both store 50% with both lessons done, and never issue the certificate.
With the lock, the roll-up runs one caller at a time.
`test_concurrent_completions_of_different_lessons_enrol_once_and_finish` races exactly that case
and asserts one enrolment, two lesson rows, progress 100 and one certificate. The spec required the
savepoint get-or-create; this lock is one step beyond it.

**Progress formula (D10).** Course % = `round(100 × lessons completed ÷ total lessons)`, written to
`enrollments.progress` on each completion. Path % = `round(mean of its courses' course %)`,
computed on read, with any course the caller has not enrolled in counting as 0. Both use Python's
`round()`, which rounds exact halves to the nearest even number — `100, 50, 50, 50` → `62.5` →
`62` — matching `recompute_milestone_progress` in the roadmap service.

**Idempotent completion, exactly one certificate (D3).** A repeat completion records nothing new.
A certificate is issued only when progress reaches 100 while `completed_at` is still null, so it
happens once per course, per person, per workspace; `uq_certificates_startup_user_course` backs
that up. Issuing it publishes `learning.course.completed` (payload `startup_id`, `user_id`,
`course_id`, `certificate_id`) and enqueues `learning.certificate.generate` with
`{"certificate_id": …}`. Enqueue-only: no worker and no PDF. The credential code is
`secrets.token_urlsafe(16)` (22 characters), unique in the database.

**Deterministic recommendations (D2).** `recommended_courses` keeps courses tagged with the
startup's stage — or, when no stage is set, beginner courses from every stage — removes courses the
caller has completed in this workspace, then sorts by `RECOMMENDATION_SORT_KEYS`: level, then
catalog position. Stage match is applied as the inclusion filter rather than a sort key. The Health
Score signal can later be added as one more entry in `RECOMMENDATION_SORT_KEYS` without rewriting
the ranker. Continue watching (D5) comes back in the same response: the caller's enrolled,
not-completed courses, most recently active first, so the front page loads in one request.

**Access and commits (D6).** Every route binds `require_role(founder, team_member)` (as
`_academy`) plus `get_verified_user`, and scopes every query to `membership.startup_id` and
`membership.user_id`. Services only `flush()`; the two write handlers — `POST /enrollments` and
`PATCH /lessons/{lesson_id}/progress` — call `db.commit()`. The `PATCH` response always carries a
`certificate` key, `null` unless that call finished the course, so the frontend reads one stable
shape. The handoff brief's "any active member can browse" is superseded by the PRD's RBAC row
(waiver W1).

## What's involved

**Data model / migration**

- `alembic/versions/0019_learning.py` — three brand-new tables, no lock on any existing table.
  Autogenerated from `app/db/models/learning.py`; only the revision id, `down_revision`, Create
  Date and docstring were hand-edited. Chains off `0020_signatures` — Module 18 Slice 4 merged
  first and `0019` was reserved for this module, so the chain runs `0018_document_shares` →
  `0020_signatures` → `0019_learning`. `alembic heads` shows `0019_learning` as the single head
  and `alembic check` reports no drift.
  - `enrollments` — unique `(startup_id, user_id, course_id)`; `progress` integer, default 0, with
    `ck_enrollments_progress_range` (0–100); `completed_at` nullable.
  - `lesson_progress` — unique `(startup_id, user_id, lesson_id)`.
  - `certificates` — unique `(startup_id, user_id, course_id)` and unique `credential_code`.
  - All three: `startup_id` → `startups` and `user_id` → `users`, both `ON DELETE CASCADE` and
    indexed; `created_at` / `updated_at` with `server_default now()`. `course_id` and `lesson_id`
    are text catalog keys, not foreign keys.
- `app/db/models/learning.py` — `Enrollment`, `LessonProgress`, `Certificate`; registered in
  `app/db/models/__init__.py`.
- `app/db/models/enums.py` — `CourseLevel` (`beginner` | `intermediate` | `advanced`).

**Endpoints** — `app/api/v1/endpoints/learning.py`, registered in `app/api/v1/api.py` at
`prefix="/learning"`. Request models in `app/schemas/learning.py`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/learning/recommendations` | stage, recommended shelf, continue watching |
| GET | `/api/v1/learning/courses` | every course with the caller's enrolled / progress / completed |
| GET | `/api/v1/learning/courses/{course_id}` | course detail with per-lesson `completed`; unknown id → 404 |
| GET | `/api/v1/learning/paths` | every path with the caller's path % |
| GET | `/api/v1/learning/articles` | article index (no database read) |
| POST | `/api/v1/learning/enrollments` | `{"course_id"}` → 201 first time, 200 on a repeat; commits |
| PATCH | `/api/v1/learning/lessons/{lesson_id}/progress` | `{"completed": true}` only (`false` → 422); auto-enrols; commits |
| GET | `/api/v1/learning/certificates` | the caller's certificates, newest first |

All eight: founder or team member (other roles → 403), active member of the workspace (otherwise
403), signed in (otherwise 401), verified email (otherwise 403 `EMAIL_NOT_VERIFIED`).

**Service** — `app/services/learning/service.py`

- Write path: `get_or_create_enrollment`, `complete_lesson`, `course_progress`,
  `list_certificates`, `serialize_enrollment`, `serialize_certificate`.
- Read path: `enrollments_by_course`, `completed_lesson_ids`, `recommended_courses`,
  `RECOMMENDATION_SORT_KEYS`, `continue_watching`, `path_progress`, `course_summary`,
  `course_detail`, `path_view`, `article_view`.

**Catalog** — `app/services/learning/catalog.py`: `Lesson`, `Course`, `LearningPath`, `Article`,
`ALL_COURSES` / `ALL_PATHS` / `ALL_ARTICLES`, lookups `COURSES` / `PATHS` / `ARTICLES` /
`LESSONS`, `get_course` and `get_lesson` (404 on unknown ids), `course_duration_min`,
`path_duration_min`.

**Tests**

- `tests/db/test_learning_models.py` — defaults, the three unique constraints, the 0–100 check,
  the same person in two workspaces.
- `tests/test_learning_migration.py` — the migration applies with its named constraints; exactly
  one alembic head.
- `tests/services/learning/test_catalog.py` — unique ids, ordered lessons, known path courses,
  placeholder labelling and shape, 404 lookups, derived durations.
- `tests/services/learning/test_service.py` — idempotent enrolment, automatic enrolment, repeat
  completion, the course formula, one certificate + event + job, credential codes, newest-first
  certificates, per-person and per-workspace isolation.
- `tests/services/learning/test_concurrency.py` — real committing sessions racing two enrolments,
  two completions of the same lesson, and two completions of different lessons in an unenrolled
  course.
- `tests/services/learning/test_browse.py` — recommendations (stage match, completed excluded,
  beginner first, no-stage fallback), path % including half-to-even rounding, continue watching and
  its isolation, the course and article views.
- `tests/api/test_learning.py` — the full access matrix on all eight routes, 201 then 200, 404s,
  the 422, finishing a course over HTTP, recommendations with continue watching, path %, and
  per-person and per-workspace privacy.
- `e2e/test_smoke.py` — the eight learning routes added to the live route list.
- `e2e/test_learning.py` — the live journey, capturing every body to `e2e/_captures/learning/`.

## Verification

- **Learning unit and API tests: 123 passed** — models 7, migration 2, catalog 6, service 12,
  concurrency 3, browse 11, API 82 (14 tests, parametrised across roles and routes).
- `ruff`, `black --check` and `mypy` clean on every new and changed learning file.
- Migration: `alembic upgrade head` applied on the development database; `alembic heads` shows the
  single head `0019_learning`; `alembic check` reports "No new upgrade operations detected."
- **Full project checks — clean, on the latest `develop`.** `ruff check app tests`: all checks
  passed. `black --check app tests`: 299 files unchanged. `mypy app`: no issues in 134 source
  files. Full `pytest` with `--cov-fail-under=95`: **1,139 passed**, coverage **97.98%**. Run from
  the test container with `REDIS_URL=redis://host.docker.internal:6379/0`, because the Redis-backed
  Auth tests otherwise look for Redis at `localhost` inside the container.
- **Pending — live e2e.** `e2e/test_learning.py` and the smoke routes are written but have not been
  run yet; how to run the e2e suite on a Windows machine is with the lead. The FE guide is built
  from this run's captures.

## Operate / roll back

- No new environment variables, secrets or config.
- **Roll back** with `alembic downgrade 0020_signatures`, which drops `lesson_progress`,
  `enrollments` and `certificates` with their indexes. **Lossy** — every enrolment, lesson
  completion and certificate is destroyed. Roll the code back together with the migration: with the
  tables gone, every learning route except `GET /learning/articles` would fail.
- `learning.certificate.generate` rows sit in `jobs` with status `queued` — nothing consumes them
  yet. `jobs` has no foreign key to `startups`, so a downgrade does not remove them.
- **Never rename a catalog course or lesson id** once real users have progress against it; existing
  rows store those ids.

## Follow-ups

- **Replace the placeholder catalog with real content — required before go-live** (D8).
- Move the catalog into the database when Module 25.4 lands (D1).
- Add the Health Score signal as a recommendation sort key (D2).
- PDF rendering, sharing, and a public certificate verification endpoint (D3) — the
  `learning.certificate.generate` jobs are enqueued but nothing renders them.
- Private lesson notes, if they return (D4) — they need their own table and an author-only access
  rule.
- Deferred by design: AI-picked recommendations and the reason line (Module 03), notifications
  (Module 20), video hosting (`video_ref` only), un-completing a lesson.
