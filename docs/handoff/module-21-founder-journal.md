# Handoff Brief — Module 21: Founder Journal

> **For:** a developer new to `cofoundaz-api` picking up their first module.
> **Status:** not started. **Spec (authority):** `../Cofoundaz_Technical_PRD.md` → Module 21.
> **This brief is a starting point, not the spec.** It orients you, scopes v1, points you at the
> patterns to copy, and flags the parts where you must check in with a senior. You still run the
> normal loop: **brainstorm → spec → plan → build (TDD) → review → verify → document**.

---

## 1. What you're building

A **private journal for the founder**. A place to write a short daily entry, log a mood + stress
level, get a gentle reflection prompt, and see mood trends over time. Think "a diary that only the
founder can ever read."

**Why this is a good first module:** it is the most self-contained module in the product. Nothing
else depends on it, and it depends only on the auth/tenancy foundation that already exists. You
can own it end-to-end and you cannot break another module.

## 2. Access model — the simplest in the product

**Founder ONLY.** The RBAC matrix row is `journal | ✓ only | — | — | …` — no team member, mentor,
or admin can read journal routes. This is *hard-enforced server-side*: even a super-admin
impersonating the workspace must get `403` on journal content routes (only aggregate metadata
counts are ever visible to the system).

Practically: every journal endpoint requires a **verified founder** of the workspace. There is no
reader/editor split to reason about — if you're not the founder, you get nothing.

## 3. Scope for v1

**In scope:**
- CRUD for journal entries (`journal_entries`): date, rich-text content, mood, stress.
- Mood logging (`mood_logs`) + a mood/stress trend read over a date range.
- A "prompt of the day" endpoint (rotating prompts from a small static catalog).

**Defer / stub (with a one-line reason each — write these into your spec's Deferred table):**
- **Rich-text**: store content as text/markdown. Don't build a document model.
- **AI/context-aware prompts** ("You shipped {milestone}…"): v1 serves prompts from a **static
  catalog** (like the roadmap template catalog). Milestone-linked retros + smart prompts → later,
  once Module 03 (AI) exists.
- **The supportive low-mood card** (21.3): the *rule* (e.g. mood ≤2 for 14+ days) can be computed,
  but the **copy and trigger are sensitivity-bound — leave the card out of v1 or gate it behind a
  senior review** (see §7).
- **Certificates/PDF, notifications delivery**: not part of this module / Module 20.

## 4. Data model (new migration — next available number, currently `0008`)

From the PRD, mapped to our conventions (use `UUIDMixin` + `TimestampMixin`, `native_enum=False`
enums, JSONB where useful — copy the shape from `app/db/models/roadmap.py`):

- **`journal_entries`** — `id` · `startup_id` FK→startups (CASCADE, indexed) · `founder_id` FK→users
  · `entry_date` (Date) · `content_encrypted` (see §7) · `mood` (enum, 5-scale) · `stress` (int 1–10)
  · timestamps. Consider a unique `(startup_id, entry_date)` if one entry per day.
- **`mood_logs`** — `id` · `startup_id` · `entry_date` · `mood` · `stress`. (May be derived from
  entries rather than a separate table — a **design decision to make in brainstorming**.)
- **New enum** `Mood(rough|meh|okay|good|great)` in `app/db/models/enums.py`.

## 5. Endpoints (`/api/v1/journal`)

From the PRD API list. All **founder-only + verified**. Use `success_response(...)` envelope,
`X-Workspace-Id` tenancy, uniform-404 for anything not the caller's.

| Route | Does |
|---|---|
| `POST /journal/entries` | Create/upsert today's entry (content, mood, stress). |
| `GET /journal/entries` | Reverse-chron list (date, mood, first line); support `?search=`. |
| `GET /journal/entries/{id}` | One entry (decrypted for the founder). |
| `PATCH /journal/entries/{id}` | Autosave edits. |
| `DELETE /journal/entries/{id}` | Remove an entry. |
| `GET /journal/mood?range=` | Mood/stress series over a range (for the trend chart). |
| `GET /journal/prompts/today` | Today's prompt from the static catalog. |

**Events:** none required for v1 (the PRD's journal notifications are Module 20). Don't invent any.

## 6. Mirror this shipped module

Copy the patterns from an existing module rather than inventing:
- **Service + endpoints shape:** `app/services/assessment/` + `app/api/v1/endpoints/assessments.py`
  (single-resource CRUD, tenancy resolver helpers, founder gate).
- **Models + migration:** `app/db/models/roadmap.py` + `alembic/versions/0006_roadmap.py`.
- **Static catalog pattern (for prompts):** `app/services/roadmap/templates.py`.
- **Founder gate:** `require_role(MembershipRole.founder)` + `get_verified_user` (see any roadmap
  write endpoint).
- **Tests:** `tests/api/test_roadmap_*.py` for the real `_member` helper + envelope assertions;
  `tests/services/` for service-level tests.

## 7. ⚠️ Two senior checkpoints — do NOT solve these alone

1. **Content encrypted at rest.** The PRD requires journal content encrypted with a workspace key.
   **Reuse the existing encryption pattern — do not invent crypto.** We already encrypt MFA secrets:
   see `app/services/auth/mfa.py` and the key in `app/core/config.py`. Agree the approach with a
   senior (which key, encrypt/decrypt helper, where it lives) before writing it.
2. **Mental-health-adjacent copy.** Mood trends must be **pattern-only, never diagnostic, no advice
   framing**, and the sustained-low-mood card (if built at all) uses exact, reviewed copy. Get the
   copy and the trigger rule reviewed by a senior. When in doubt, ship the trends without the card.

## 8. How to work (the standard loop)

1. **Brainstorm** the design with your lead — settle the open decisions (one entry/day vs many;
   `mood_logs` as a table vs derived; the encryption approach). Produce a short spec in
   `docs/superpowers/specs/`.
2. **Plan** it into small TDD tasks in `docs/superpowers/plans/`.
3. **Build test-first.** Write the failing test, make it pass, commit. Small commits.
4. **Verify with the four layers** (see §9).
5. **Document**: an SOP (`docs/sop/`), a captured-live FE guide
   (`docs/fe-integration-guide-journal.md` — paste real responses, never schema guesses), and tick
   the checklist (`docs/checklist/PROJECT_CHECKLIST.md`).
6. **PR** into `main`.

## 9. Definition of done (certified)

- **Unit/integration**: every endpoint + edge case, real Postgres, per-test rollback, TDD.
- **Sanity**: full `make test` green on a fresh migrated DB.
- **Smoke**: journal routes added to `e2e/test_smoke.py`'s surface assertion.
- **Live E2E** (`make e2e`): a founder logs in, writes an entry, reads it back (decrypted), logs
  mood, fetches the trend + today's prompt — with **real data over HTTP**, responses captured to
  `e2e/_captures/`.
- **A second, non-founder member gets 403 on every journal route** (prove the privacy guarantee).
- Gates green: `black`/`isort`/`ruff`/`mypy`.
- SOP + FE guide + checklist updated.

## 10. Gotchas

- The privacy guarantee is the whole point — **write the 403 tests first**, including the
  admin/impersonation case if impersonation exists.
- Never log or return decrypted content anywhere except the founder's own read.
- Placeholder data in the FE guide must be obviously fake — never paste a real person's journal text.
