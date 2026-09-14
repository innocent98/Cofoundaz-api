# Module 17 — Learning Academy

**Date:** 2026-09-11
**Module:** 17 Learning Academy (single slice)
**Status:** Approved design (all ten decisions signed off by the lead) → implementation plan next
**Base branch / PR target:** `develop`
**Depends on:** nothing new. Reads the startup's stage (Onboarding). No AI, storage, or email.

---

## 1. Context & Scope

### Sources

1. **Technical PRD** — `C:\Users\User\Desktop\Cofoundaz_Technical_PRD.md` (the repo docs reference
   `../Cofoundaz_Technical_PRD.md`; that path does not exist on this machine).
   - Module 17 — **lines 630–642**
   - RBAC matrix, `academy` row — **line 900**
   - Module 25.4 Content Management, Learning Academy CMS — **line 776**
2. Handoff brief — `docs/handoff/module-17-learning-academy.md`
3. Planned blueprint — `docs/architecture/planned/modules-17-21-junior-handoff.md`
4. Design decisions — agreed with the lead on GitHub (the Module 17 design issue, questions 1–10,
   and its follow-up #51)

The brief and the blueprint are orientation documents, not the spec, by their own headers. Where
they disagree with the PRD, the PRD wins (see §9, waivers).

### What this module is

A learning hub inside the founder's workspace: a catalog of **courses** made of ordered
**lessons**, **learning paths** (ordered lists of courses), and **articles**. A member can
**enrol** in a course, **mark lessons complete**, and earn a **certificate** when a course reaches
100%. A "recommended for you" shelf, with a "continue watching" row, sits on the front page.

### This slice delivers

- A read-only, versioned, in-code catalog of courses, lessons, paths and articles, populated with
  **clearly labelled placeholder content** (§4)
- Enrolments, per-lesson progress, and certificates, persisted per user, per workspace
- Automatic, race-safe enrolment when a lesson is completed in a course the caller has not enrolled
  in (§5)
- Course and path progress derived from completed lessons by an exact, documented formula (§5)
- Certificate issuance on completion: the record, an unguessable credential code, the
  `learning.course.completed` event, and the `learning.certificate.generate` job
- A deterministic recommendations endpoint that also returns the caller's in-progress courses

### Non-goals (this slice)

- **Real catalog content.** v1 ships placeholder courses, lessons, paths and articles. Replacing
  them with real content is a **required pre-go-live task** (§9, follow-ups).
- **AI-picked recommendations and the reason line** (PRD 17.1, *"Because your assessment flagged
  pricing…"*) — needs Module 03.
- **The Health Score boost in recommendations** — deferred; the ranker is structured so it can be
  added later (§5).
- **PDF rendering, "Download PDF", "Share to LinkedIn"** (PRD 17.5) — the job is enqueued, nothing
  renders it.
- **A public certificate verification endpoint** — later.
- **Video hosting.** Lessons store a `video_ref` only. No uploads, no streaming.
- **Notifications** (*"New course recommended for your stage"*, *"Certificate earned"*) — Module 20.
- **Course and lesson authoring** — Module 25.4 plans a Learning Academy CMS. The catalog moves to
  the database when that lands (§4).
- **Private lesson notes** (PRD 17.2 notes tab) — deferred (§9, D4).
- **Un-completing a lesson.** PRD 17.2 specifies "Mark complete" only.

---

## 2. Access

**Founders and team members only, reads included** — PRD line 632 (*"Access: F, TM"*) and the RBAC
matrix at line 900 (`marketplace / academy | ✓ | ✓ | — …`). Confirmed by the lead.

- Every route uses `require_role(MembershipRole.founder, MembershipRole.team_member)` — the same
  dependency the roadmap, mission, documents and business routers already bind as `_editor`.
- Mentor, accountant, legal advisor, business consultant and investor receive **403** on every
  learning route.
- Not a member of the workspace → **403** (`require_workspace` raises `Forbidden`).
- Unauthenticated → **401**. Email not verified → **403** (`get_verified_user`).

The brief and blueprint's *"any active member can browse"* is recorded as waiver W1 (§9).

### Progress is personal, and per workspace

Enrolments, lesson progress and certificates belong to **one person in one workspace**. Every read
and write filters on **both** `startup_id` **and** `user_id == current_user.id`. A teammate in the
same workspace must not see or change another member's progress.

A person who belongs to two workspaces enrols and progresses **separately in each** — the same way
every other module scopes data per workspace (§9, D7).

A record that exists but belongs to someone else returns the same **404** as a record that does not
exist, so ids cannot be probed.

---

## 3. Data model — three user-state tables (+ migration)

All three use `UUIDMixin` + `TimestampMixin`, following `app/db/models/roadmap.py`.

`course_id` and `lesson_id` are **text catalog keys** — the stable ids defined in the in-code
catalog (§4). They are not foreign keys, because the catalog is not in the database in v1.

### `enrollments`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `startup_id` | UUID FK → `startups` | `ondelete=CASCADE`, indexed |
| `user_id` | UUID FK → `users` | `ondelete=CASCADE`, indexed |
| `course_id` | Text | catalog course key |
| `progress` | Integer | 0–100, `CHECK`; derived from `lesson_progress` (§5) |
| `completed_at` | timestamptz, nullable | set once, when progress first reaches 100 |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin` |

Unique `(startup_id, user_id, course_id)` — one enrolment per person, per course, per workspace. A
row is created either by `POST /learning/enrollments` or automatically by the first lesson
completion (§5); both paths go through the same race-safe get-or-create.

### `lesson_progress`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `startup_id` | UUID FK → `startups` | `ondelete=CASCADE`, indexed |
| `user_id` | UUID FK → `users` | `ondelete=CASCADE`, indexed |
| `course_id` | Text | catalog course key |
| `lesson_id` | Text | catalog lesson key |
| `completed_at` | timestamptz | |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin` |

Unique `(startup_id, user_id, lesson_id)` — a lesson is completed at most once per person, per
workspace.

### `certificates`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `startup_id` | UUID FK → `startups` | `ondelete=CASCADE`, indexed |
| `user_id` | UUID FK → `users` | `ondelete=CASCADE`, indexed |
| `course_id` | Text | catalog course key |
| `credential_code` | Text | unique; unguessable (§6) |
| `issued_at` | timestamptz | |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin` |

Unique `(startup_id, user_id, course_id)` — one certificate per course, per person, per workspace.

### Why this differs from the PRD's entity list

PRD line 640 lists `enrollments(user_id,course_id,progress,completed_at)` and
`certificates(id,user_id,course_id,credential_code,issued_at)`. This spec adds:

- **`startup_id` on all three tables** — required by the house tenancy rule; every read is
  workspace-scoped.
- **A `lesson_progress` table** — the PRD has none, but without it course progress would be a
  stored number with nothing to derive it from, and could drift from reality.

The PRD's `courses` and `lessons` entities are not tables in v1 — they live in the in-code catalog
until Module 25.4 (§4).

### Enum

`CourseLevel(beginner | intermediate | advanced)` in `app/db/models/enums.py`.

### Migration

- **`0019_learning`**, with `down_revision = "0018_document_shares"` — the `develop` head at the
  time of writing. The brief's `0008` is out of date. If anything merges first, re-point
  `down_revision` at the new head before pushing.
- `created_at` / `updated_at` carry `server_default=sa.text("now()")`. Core-level inserts do not
  apply the ORM default, so the migration-built table must supply it.
- `CHECK` constraints pass a short name (e.g. `name="progress_range"`) and let the naming
  convention in `app/db/base.py` build the full name. Passing the full name doubles the prefix.
- `alembic heads` shows exactly one head before pushing.

---

## 4. Catalog — in-code config

**Config now, database when Module 25.4 lands.** The catalog is a read-only, versioned, in-code
registry at `app/services/learning/catalog.py`, following the existing convention of
`app/services/roadmap/templates.py`, `app/services/roadmap/gallery.py`, the business canvas
definitions, and the Module 18 document-template registry. There is no admin authoring surface in
v1, so a table would only hold content written by hand anyway.

PRD Module 25.4 (line 776) plans a CMS to create and edit courses and lessons with a publish toggle.
When it is built, the catalog moves into the database. To make that move mechanical, the in-code
shape is kept clean and normalized so it maps one-to-one onto tables:

- **Courses** — each with a stable id, title, level, stage tags, and an ordered list of lessons.
- **Lessons** — each with a stable id, title, order, `video_ref`, duration, and transcript; belongs
  to exactly one course.
- **Paths** — each with a stable id, title, stage, and an **ordered list of course ids**. A path
  references courses by id; it does not copy course content.
- **Articles** — each with a stable id, title, tags, and body.
- A version constant, `LEARNING_CATALOG_VERSION`.

### Placeholder content (v1)

v1 ships **placeholder content**, agreed with the lead (§9, D8). It must be:

- **Unmistakably labelled.** The catalog module's docstring states that all content is
  placeholder, and **every course, lesson, path and article title starts with `[Placeholder] `**, so
  any placeholder that reaches a screen is obvious.
- **Realistic in shape**, so the recommender and the path-percentage maths are genuinely exercised:
  - **one stage-tagged course for each of the six stages** — idea, validation, build, launch,
    growth, scale
  - courses at more than one level, so beginner-first ordering is tested
  - **at least one path** with **two or more courses in order**
  - **at least two articles**

Replacing the placeholders with real content is a **required pre-go-live task**, not optional
(§9, follow-ups). The academy is founders and team members only and there are no live users yet, so
the interim risk is low — but the swap must land before go-live.

### Rules for the catalog

- **Every course, lesson, path and article has a stable id that never changes** once anything
  references it. Enrolment, progress and certificate rows store these ids; renaming one silently
  orphans people's records. This is the same discipline as roadmap template keys.
- **Lesson ids are unique across the whole catalog**, not just within their course, because
  `lesson_progress` is unique on `lesson_id` and `PATCH /learning/lessons/{id}/progress` addresses
  a lesson by id alone.
- **Every course has at least one lesson, and every path has at least one course**, so no
  percentage divides by zero.
- **Every course id a path lists must exist in the catalog.**
- **Counts are derived, not stored.** Lesson count and total duration are computed from the lesson
  list each time, as `template_counts` does for roadmap templates. A path's total time is the sum of
  its courses' durations.

---

## 5. API — `/api/v1/learning`

All routes: `require_role(founder, team_member)` + `get_verified_user`, standard envelope.

| Method | Path | Does |
|---|---|---|
| `GET` | `/learning/recommendations` | The recommended shelf, plus the caller's continue-watching courses |
| `GET` | `/learning/courses` | Catalog grid, plus the caller's progress on each course |
| `GET` | `/learning/courses/{id}` | Course detail, curriculum, and the caller's progress; unknown id → 404 |
| `GET` | `/learning/paths` | Paths (ordered course lists), total time, the caller's completion % |
| `GET` | `/learning/articles` | Article index |
| `POST` | `/learning/enrollments` | Enrol the caller in a course — idempotent; first time 201, repeat 200 |
| `PATCH` | `/learning/lessons/{id}/progress` | Mark a lesson complete → auto-enrol if needed → recompute progress → at 100%, issue a certificate |
| `GET` | `/learning/certificates` | The caller's certificates |

### Recommendations

A deterministic rule, no AI:

1. Include courses whose stage tags contain the startup's current stage.
2. Exclude any course the caller has already completed in this workspace.
3. Order beginner courses first, then intermediate, then advanced.
4. Break ties by catalog order, so the result is stable.
5. **If the startup has no stage set**, include beginner courses from every stage.

**The ranker is built for extension.** Ordering is expressed as a list of sort keys applied in turn —
currently stage match, then level, then catalog order. The Health Score signal (the product,
market, money, legal and team dimensions from Module 06) can later be added as one more sort key,
without rewriting the ranker.

### Continue watching

Returned inside `GET /learning/recommendations`, so the front page loads in a single request — the
same approach as the dashboard summary. It lists the caller's courses in this workspace that are
**enrolled but not yet completed**, with their progress, most recently active first.

### Enrolment, including automatic enrolment

`POST /learning/enrollments` and the first `PATCH /learning/lessons/{id}/progress` in an unenrolled
course both call **one race-safe get-or-create** on the `(startup_id, user_id, course_id)` unique
constraint (§9, D9):

1. Select the enrolment. If it exists, use it.
2. Otherwise, insert it **inside a savepoint** (`db.begin_nested()`).
3. If that insert raises `IntegrityError`, a concurrent request won the race — **re-select** the
   now-committed row.

This is the same pattern as `get_or_create_canvas` in `app/services/business/service.py` and the
positioning-map lazy-create in `app/services/business/positioning.py`. Two near-simultaneous lesson
completions in an unenrolled course therefore produce exactly one enrolment.

Automatic enrolment on lesson completion leaves the caller in exactly the same state as enrolling
first and then completing the lesson.

### Progress formula

Both percentages are integers from 0 to 100, and both use Python's built-in `round()` — the same
rounding `recompute_milestone_progress` already uses in `app/services/roadmap/service.py`. Python
rounds exact halves to the nearest even number, so `62.5` → `62` and `63.5` → `64`.

**Course %** = `round(100 × lessons completed ÷ total lessons in the course)`

- counts only this caller's completions, in this workspace
- written to `enrollments.progress` on each completion
- a course the caller has not enrolled in is **0**

**Path %** = `round(mean of the course % of every course in the path)`

- the mean is taken over **all** courses the path lists, in catalog order
- a course in the path that the caller has not enrolled in counts as **0**
- the mean uses the integer course percentages above, then is rounded once

Worked examples:

- A course with 3 lessons, 1 completed → `round(33.33…)` → **33**. With 2 completed → **67**.
- A path of four courses at 100, 50, 50 and 50 → mean `62.5` → **62**.
- A path of two courses, one at 100 and one never enrolled → mean `50` → **50**.

### Service

`app/services/learning/service.py`. Services `flush()`; they never commit.

### Idempotency

- **Enrol twice** → one `enrollments` row; the repeat returns the existing enrolment with 200.
- **Complete a lesson twice** → one `lesson_progress` row; progress is unchanged.
- **Reaching 100% again** — for example a repeat completion on an already-finished course — does
  not issue a second certificate, publish a second event, or enqueue a second job.

Concurrent duplicates must be safe too, not only sequential ones. Two simultaneous requests must
produce one row and no `IntegrityError` → 500.

The `PATCH` response includes the certificate only when that completion took the course to 100%.

### Committing

`get_db()` does not commit. **Every write handler calls `db.commit()`** after the service call,
matching `mission.py` and `documents.py`. Unit tests cannot detect a missing commit because they run
inside one rolled-back transaction; the live e2e is what catches it.

---

## 6. Cross-cutting

- **Credential code:** generated with `secrets.token_urlsafe`, the same way the project already
  makes invitation links and auth tokens (`app/services/onboarding/invites.py`,
  `app/services/auth/tokens.py`). Unique in the database and not guessable or enumerable. A public
  verification endpoint can be added later.
- **Event:** `learning.course.completed` via `event_bus`, payload
  `{startup_id, user_id, course_id, certificate_id}`, published exactly once per completion. No
  consumer until Module 20.
- **Job (stub):** `learning.certificate.generate` via
  `job_dispatcher.enqueue(db, type, payload, startup_id)`, enqueued exactly once per certificate.
  Enqueue-only; no worker and no PDF, matching the existing deferred-job pattern.
- **Errors:** reuse `Forbidden` (403), `NotFound` (404), and `VALIDATION_ERROR` (422). No new error
  codes expected.
- **Config / secrets:** none.

---

## 7. Testing

**Unit (real Postgres, per-test rollback):**

- **Access matrix** — founder and team member allowed on every route; mentor, accountant, legal
  advisor, business consultant and investor → 403 on every route; non-member → 403;
  unauthenticated → 401; unverified → 403.
- **Per-user isolation** — a teammate in the same workspace cannot read or change another member's
  enrolment, progress or certificates → 404 or empty list.
- **Per-workspace isolation** — another workspace's record → 404. The same person enrols and
  progresses separately in two workspaces.
- **Catalog shape** — every course, lesson, path and article id is unique; lesson ids are unique
  across the whole catalog; every path's course ids exist; every course has at least one lesson and
  every path at least one course; derived counts are correct.
- **Placeholder labelling** — every course, lesson, path and article title starts with
  `[Placeholder] `; there is one course for each of the six stages; courses span more than one
  level; at least one path lists two or more courses; there are at least two articles.
- **Enrolment** — idempotent (201 then 200); unknown course → 404.
- **Lesson completion** — idempotent; unknown lesson → 404.
- **Automatic enrolment** — completing a lesson in an unenrolled course creates the enrolment and
  records the completion; the result matches enrolling first.
- **Course %** — equals `round(100 × completed ÷ total)` after each completion, including a
  non-whole case (1 of 3 → 33).
- **Path %** — mean of course percentages; unenrolled courses count as 0; the half-even rounding
  case (100, 50, 50, 50 → 62).
- **Completion** — at 100%: `completed_at` set, one certificate, one event, one job.
- **Credential code** — unique across certificates; generated with `secrets.token_urlsafe`.
- **Recommendations** — stage match; completed courses excluded; beginner first; stable order;
  no-stage fallback returns beginner courses from every stage.
- **Continue watching** — lists enrolled, not-completed courses only; excludes completed and
  not-enrolled; most recently active first; another member's enrolments never appear.
- **Concurrency** — two simultaneous enrolments; two simultaneous completions of the same lesson;
  and two simultaneous completions of **different** lessons in an **unenrolled** course. Each
  produces exactly one row per unique key and no error. Mirrors
  `tests/services/business/test_concurrency.py`.
- **Migration** — applies cleanly and leaves exactly one alembic head.

**Sanity:** full `make test` green on a freshly migrated database.

**Smoke:** learning routes added to `e2e/test_smoke.py`'s route list.

**Live e2e (`e2e/test_learning.py`):** a founder views recommendations → enrols → completes every
lesson → progress reaches 100% → a certificate is issued and appears in `GET /learning/certificates`.
Every body captured to `e2e/_captures/learning/`.

**FE integration guide:** `docs/fe-integration-guide-learning.md`, built from the captures only. It
must state that v1 catalog content is placeholder.

---

## 8. File structure

| File | Change |
|---|---|
| `app/db/models/enums.py` | add `CourseLevel` |
| `app/db/models/learning.py` | **new** — `Enrollment`, `LessonProgress`, `Certificate` |
| `app/db/models/__init__.py` | register the models |
| `alembic/versions/0019_learning.py` | **new** migration |
| `app/services/learning/__init__.py` | **new** package |
| `app/services/learning/catalog.py` | **new** — placeholder in-code catalog + `LEARNING_CATALOG_VERSION` |
| `app/services/learning/service.py` | **new** |
| `app/schemas/learning.py` | **new** — request models |
| `app/api/v1/endpoints/learning.py` | **new** — router |
| `app/api/v1/api.py` | register the router |
| `tests/…/learning/` | unit tests per §7 |
| `e2e/test_learning.py` (+ `e2e/_captures/learning/`) | live journey |
| `e2e/test_smoke.py` | add learning routes |
| `docs/fe-integration-guide-learning.md` | verified guide |
| `docs/sop/<date>-learning-academy.md` | SOP |
| `docs/checklist/PROJECT_CHECKLIST.md` | tick Module 17 |

---

## 9. Decisions & waivers

All ten decisions below were agreed with the lead on GitHub.

- **D1 — The catalog is in-code config, not database tables.** It follows the existing in-code
  registry convention, and with no admin authoring there is nothing a table would hold that is not
  written by hand. **Config now, database when Module 25.4 lands**: the shape is kept clean and
  normalized, with stable ids and a clear course → lesson → path structure, so the move maps
  one-to-one onto tables. Enrolments, lesson progress and certificates are in the database.
- **D2 — Recommendations use a deterministic rule.** Stage match, completed courses excluded,
  beginner first, stable tie-break, and a no-stage fallback. No AI (Module 03). The Health Score
  boost is deferred, and the ranker is structured as a list of sort keys so it can be added later
  without a rewrite.
- **D3 — Certificates are a record, an unguessable credential code, and an enqueued
  `learning.certificate.generate` job.** No PDF generation and no PDF library. Enqueue-only, like
  the existing deferred jobs. A public verification endpoint can come later.
- **D4 — Private lesson notes are deferred.** The PRD's data model does not list a notes entity, so
  one is not invented for v1. If notes return, they need their own table and an author-only access
  rule.
- **D5 — Continue watching is returned inside `GET /learning/recommendations`**, so the front page
  loads in one request, in the same spirit as the dashboard summary.
- **D6 — Access is founders and team members only, reads included**, per PRD line 632 and the RBAC
  matrix at line 900.
- **D7 — Enrolment is unique per `(startup_id, user_id, course_id)`, with progress scoped per
  workspace.** The platform is `startup_id`-scoped throughout. One enrolment per person across all
  workspaces would require learning progress to be visible across workspaces, breaking the
  per-workspace scoping every other module upholds — a larger redesign, not v1. A person in two
  workspaces enrols and progresses separately in each.
- **D8 — v1 ships a labelled placeholder catalog.** Unmistakably labelled, and realistic in shape —
  one stage-tagged course per stage, at least one path with ordered courses, and at least two
  articles — so the recommender and the path-percentage maths are genuinely exercised by the tests.
  Real content is a required pre-go-live task.
- **D9 — Completing a lesson in an unenrolled course enrols the caller automatically.** It is more
  forgiving, and the end state is identical to enrolling first. The auto-enrol is race-safe: a
  get-or-create on the `(startup_id, user_id, course_id)` unique constraint inside a savepoint, with
  a re-select on `IntegrityError`, as in `get_or_create_canvas`.
- **D10 — A path's completion % is the average progress across its courses.** It moves as the
  person makes progress, rather than only when a whole course finishes. Course % is lessons
  completed ÷ total lessons; path % is the mean of its courses' percentages, with unenrolled courses
  counting as 0, both rounded with Python's `round()`. The exact formula and worked examples are in
  §5.

### Waivers

- **W1 — The brief and the blueprint say "any active member can browse."** That is looser than,
  and contradicts, the PRD's RBAC row. The PRD is the source of truth, and the code restricts every
  route, reads included, to founders and team members. Recorded so the handoff documents do not
  appear to contradict the code.
- **W2 — The brief and the blueprint are otherwise out of date** on two points: they give the
  migration slot as `0008` (it is `0019`), and the brief says to open the PR into `main` (it goes
  into `develop`).

### Follow-ups

- **Replace the placeholder catalog with real content — required before go-live** (D8).
- Move the catalog into the database when Module 25.4 lands (D1).
- Add the Health Score signal as a recommendation sort key (D2).
- Add a public certificate verification endpoint, and PDF rendering and sharing (D3).

### Settled by the PRD or house rules

- **`startup_id` on all three tables** — house tenancy rule; the PRD entity list omits it.
- **A `lesson_progress` table** — the PRD omits it; progress must be derived, not stored alone.
- **Deferred:** AI recommendations (Module 03), video hosting (`video_ref` only), notifications
  (Module 20), authoring (Module 25.4), private lesson notes, un-completing a lesson.