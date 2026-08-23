# Handoff Brief — Module 17: Learning Academy

> **For:** a developer new to `cofoundaz-api` picking up their first module.
> **Status:** not started. **Spec (authority):** `../Cofoundaz_Technical_PRD.md` → Module 17.
> **This brief is a starting point, not the spec.** It orients you, scopes v1, points you at the
> patterns to copy, and flags the parts to stub or check with a senior. You still run the normal
> loop: **brainstorm → spec → plan → build (TDD) → review → verify → document**.

---

## 1. What you're building

A **learning hub** inside the founder's workspace: a catalog of **courses** (made of ordered
**lessons**), **learning paths** and **articles**, the ability to **enroll** and track **progress**
per lesson, and **certificates** earned on course completion. Plus a "recommended for you" shelf.

**Why this is a good first module:** it's mostly read-heavy CRUD over a fixed content catalog, with
no cross-module coupling and no sensitive data. It's a clean way to learn our stack. It has a few
more entities than a trivial module, and two pieces you'll deliberately **stub** for v1 (AI
recommendations, certificate PDFs) — which is itself good practice with our "seam it, defer it"
discipline.

## 2. Access model

**Founder + Team Member** (`academy` RBAC row). Any active member can browse and enroll; progress
and certificates are **per-user** (`enrollments.user_id`, `certificates.user_id`), so a member sees
their own progress, not the whole team's. Reads = any active member; enroll/progress = the acting
member for their own records. Standard `require_workspace` / `get_verified_user`.

## 3. Scope for v1

**In scope:**
- A **static content catalog** (courses, lessons, paths, articles) — served from config, like our
  roadmap template catalog. No CMS/admin authoring in v1.
- **Enrollments** + **per-lesson progress** (persisted per user).
- **Certificates** earned on course completion (the record + credential code; see the PDF note).
- A **recommendations** endpoint (deterministic v1 — see below).

**Defer / stub (write into your spec's Deferred table with the reason):**
- **AI-picked recommendations** (17.1's "Because your assessment flagged pricing…"): v1 is
  **deterministic** — recommend courses whose `stage_tags` match the startup's current stage (and
  optionally the assessment's weakest dimension). The AI reason-line comes with Module 03.
- **Certificate PDF generation**: the PRD marks this a job. v1 **stubs the PDF** — issue the
  certificate record + `credential_code`, enqueue a `learning.certificate.generate` job (job seam),
  and return metadata. Actual PDF rendering + `Download PDF` / `Share to LinkedIn` land later.
- **Video hosting**: store a `video_ref` (URL/id) only. No uploads/streaming.
- **Notifications**: Module 20.

## 4. Data model (new migration — next available number, currently `0008`)

From the PRD, mapped to our conventions (`UUIDMixin` + `TimestampMixin`, `native_enum=False`
enums, JSONB for arrays — copy `app/db/models/roadmap.py`). Content that's part of the static
catalog can live in **config** (like templates) rather than DB tables — a **design decision to make
in brainstorming**: catalog-in-config (recommended, matches our pattern) vs catalog-in-DB. Either
way, the **user-state** tables are real:

- **`enrollments`** — `id` · `startup_id` · `user_id` FK→users · `course_id` (catalog key) ·
  `progress` (int 0–100) · `completed_at` (nullable) · timestamps. Unique `(user_id, course_id)`.
- **`lesson_progress`** — `id` · `enrollment_id` (or `user_id` + `lesson_id`) · `lesson_id` ·
  `completed_at`. (Progress on a course rolls up from completed lessons — mirror how roadmap
  milestone `progress` is derived from tasks.)
- **`certificates`** — `id` · `startup_id` · `user_id` · `course_id` · `credential_code` (unique) ·
  `issued_at`.
- If you keep the catalog in DB: `courses(id,title,level,duration,stage_tags[])`,
  `lessons(id,course_id,order,video_ref,transcript)`, `articles(...)`. Otherwise these are config.

New enum: `CourseLevel(beginner|intermediate|advanced)` (or as the PRD implies).

## 5. Endpoints (`/api/v1/learning`)

From the PRD API list. Envelope + tenancy + `get_verified_user` throughout.

| Route | Access | Does |
|---|---|---|
| `GET /learning/recommendations` | member | Deterministic stage-matched course shelf. |
| `GET /learning/courses` | member | Catalog grid (+ the caller's progress per course). |
| `GET /learning/courses/{id}` | member | Course detail + curriculum + the caller's progress. |
| `GET /learning/paths` | member | Learning paths (ordered course lists, completion %). |
| `GET /learning/articles` | member | Article index (search, tags) + reader payloads. |
| `POST /learning/enrollments` | member | Enroll the caller in a course (idempotent). |
| `PATCH /learning/lessons/{id}/progress` | member | Mark a lesson complete → roll up course progress → on 100% issue a certificate + `learning.course.completed`. |
| `GET /learning/certificates` | member | The caller's earned certificates. |

**Event:** `learning.course.completed` (payload `{startup_id, user_id, course_id, certificate_id}`).
**Job (stub):** `learning.certificate.generate` via the job dispatcher.

## 6. Mirror this shipped module

- **Static catalog + counts:** `app/services/roadmap/templates.py` / `gallery.py` (courses/paths as
  config; derived counts).
- **Derived progress rollup:** `recompute_milestone_progress` in `app/services/roadmap/service.py`
  (lesson-completion → course progress works the same way).
- **Idempotent create + dedup:** the dependency/template-apply endpoints in
  `app/api/v1/endpoints/roadmap.py` (enroll-once, complete-lesson-once).
- **202-job pattern:** `POST /roadmap/generate` (for the certificate-generate job shape).
- **Tests:** `tests/api/test_roadmap_*.py` (real `_member` helper, envelope) and `tests/services/`.

## 7. ⚠️ Senior checkpoints (lighter than a sensitive module, but confirm)

1. **Catalog location** — config vs DB. Recommend config (matches our pattern, no CMS needed).
   Confirm in brainstorming so you don't build tables you'll throw away.
2. **Recommendation rule** — keep it deterministic and simple (stage-tag match). Don't reach for AI;
   that's Module 03. Agree the exact rule with your lead.
3. **Certificate PDF** — stub it (record + job) for v1. Don't pull in a PDF/rendering dependency
   without sign-off.

## 8. How to work (the standard loop)

1. **Brainstorm** the design with your lead — settle catalog-in-config-vs-DB, the recommendation
   rule, and the certificate stub. Write a short spec in `docs/superpowers/specs/`.
2. **Plan** it into small TDD tasks in `docs/superpowers/plans/`.
3. **Build test-first**, small commits.
4. **Verify** with the four layers (§9).
5. **Document**: SOP (`docs/sop/`), captured-live FE guide
   (`docs/fe-integration-guide-learning.md` — real responses, never schema guesses), tick the
   checklist.
6. **PR** into `main`.

## 9. Definition of done (certified)

- **Unit/integration**: every endpoint + edge case (enroll idempotency, progress rollup, certificate
  issuance on 100%), real Postgres, per-test rollback, TDD.
- **Sanity**: full `make test` green on a fresh migrated DB.
- **Smoke**: learning routes added to `e2e/test_smoke.py`'s surface assertion.
- **Live E2E** (`make e2e`): a member views recommendations → enrolls → completes every lesson →
  course progress hits 100% → a certificate is issued and appears in `GET /learning/certificates` —
  **real data over HTTP**, responses captured to `e2e/_captures/`.
- Gates green: `black`/`isort`/`ruff`/`mypy`.
- SOP + FE guide + checklist updated.

## 10. Gotchas

- Progress is **per-user**, not per-workspace — scope every read/write to `user_id` *and*
  `startup_id`. A teammate must not see or move your progress.
- Enroll and complete-lesson must be **idempotent** (re-clicks happen) — copy the roadmap
  dependency/template-apply idempotency pattern.
- Roll course progress up from lesson completion; don't let a stored `progress` number drift from
  reality (same lesson we learned on roadmap milestones).
