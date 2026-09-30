# Handoff Brief — Module 19: Calendar

> **For:** Victoria, your third module on `cofoundaz-api` (Module 17 Learning Academy 🎉 and Module 09
> Validation Hub 🎉 both shipped). **Status:** not started.
> **Spec (authority):** `../Cofoundaz_Technical_PRD.md` → Module 19.
> **This brief is a starting point, not the spec.** It orients you, scopes v1, points you at the
> patterns to copy, and flags what to stub or check with your lead. You still run the normal loop:
> **brainstorm → spec → plan → build (TDD) → review → verify → document.**
>
> **FE cross-check (do this first):** the real prototype is at `../cofoundaz/` —
> `app/(dashboard)/calendar/page.tsx` (Month/Week/Agenda), `.../calendar/milestones/page.tsx`,
> `.../calendar/sync/page.tsx`, and the hook `hooks/useCalendarApi.ts`. It's a mock (local state, no
> API). Build the backend to what those screens actually need — not from the PRD alone.

---

## 1. What you're building

A **calendar / planning workspace** — the "where does my time go, and what's due" layer. Founders
create and view **events** (meetings, deadlines, tasks, milestones) across **Month / Week / Agenda**
views, filter them by type, and (later) sync their real Google/Outlook/Apple calendars. It ties the
startup's roadmap, missions, and deadlines into one timeline.

**Why this is a good third module:** it's CRUD-rich with a small, self-contained core
(`calendar_events`), and the one list endpoint — filtered by **date range + type** — powers all three
views (Month/Week/Agenda just ask for different ranges). It reuses patterns you already have (dated
per-workspace records with an enum type + filtered lists — almost identical to the Finance
`transactions` list), and its heavy pieces (external OAuth sync, cross-module aggregation, the
reminder scheduler) are cleanly **deferrable**, so you practice "seam it, defer it" again. It also
teaches a new discipline: **read from an existing module instead of duplicating it** (roadmap
milestones already exist — see §7.1).

## 2. Access model

**Founder + Team Member + Accountant?** — settle the exact set with your lead, but the calendar is a
**shared workspace timeline**: everyone active in the startup sees the same events. Standard
`require_role(...)` (returns the `Membership`) + `get_verified_user`, everything scoped to the active
workspace via `startup_id` from the membership — **never from the request body**. No public/unauth
endpoints here (unlike the Validation survey route). Decide **who can write**: founder-only, or any
active member (see §7.4).

## 3. Scope for v1

**In scope (per workspace):**
- **Calendar events — full CRUD.** Fields the FE actually uses: `title`, `type`
  (milestone/meeting/deadline/task), `event_date`, optional `start_time`/`end_time`, an `all_day`
  flag, and free-text `description`/notes. The create-event modal maps 1:1 (title required, type = one
  of the four, date required; time is optional).
- **One list endpoint, three views.** `GET /calendar/events?date_from=&date_to=&type=` returns the
  events in a range, optionally filtered by type. Month view asks for the visible month, Week view a
  week, Agenda the next N days — **the grouping (Today/Tomorrow/…) is done on the front end**; you
  just return the rows in range, ordered by date then time. The type filter chips (All / Milestone /
  Meeting / Deadline / Task) map to the `type` param.

**Defer / stub (write each into your spec's Deferred table with the reason):**
- **External calendar sync** — the whole **Sync page** (Google / Outlook / Apple connect/disconnect).
  That's OAuth + third-party APIs with no infra in the repo — same call we made deferring the
  integrations/SEO-provider sync. v1 ships **no sync**; don't model connection tables yet unless your
  lead wants a stub.
- **Cross-module aggregation** — the Agenda mock shows items "From your mission", "Roadmap milestone",
  "Sales pipeline". Pulling events out of missions/roadmap/finance/sales into the calendar is a big
  integration. **v1 is the calendar's own event store only.** Surfacing roadmap-milestone due dates
  read-only is a *possible* v1.1 (see §7.1/§7.2) — agree the line with your lead.
- **Reminder scheduler** — "remind me 3 days / 1 day before deadlines". That's a scheduled job (the
  repo has scheduler infra in `app/db/models/scheduled_run.py` + the roadmap-overdue tick), but v1
  **defers** it, exactly like the Finance invoice-reminder scheduler was deferred. Store the intent if
  you must, but don't build the job.
- **Sync preferences** (block focus time, remind before deadlines, show weekends) — lightweight
  toggles. Defer, or a tiny per-workspace settings store if your lead wants it. Not core.
- **Milestones as a new entity** — **do not build one.** `RoadmapMilestone` already exists (§7.1).

## 4. Data model (new migration — number settled at build time)

⚠️ **Migration coordination (you've hit this twice now):** develop's head moves as modules merge —
Finance Slices 1–3 (`0039`→`0042`) and your own Validation (`0041_validation`) all landed recently.
Do **not** hard-code a number early — take the next free number against the **live head at build
time**, and whoever merges second renumbers to keep a single alembic head.

Conventions: `UUIDMixin` + `TimestampMixin`, `Enum(..., native_enum=False, values_callable=…)` so the
DB stores enum **values**, standalone `index=True` on every FK, per-workspace `startup_id`.

- **`calendar_events`** — `id` · `startup_id` FK (index) · `created_by` (user FK) · `title` ·
  `type` (enum) · `event_date` (Date, index or a composite `(startup_id, event_date)` index for the
  range query) · `start_time` (Time, nullable) · `end_time` (Time, nullable) · `all_day` (bool,
  default false) · `description` (Text, nullable) · timestamps.

New enum: `CalendarEventType(milestone | meeting | deadline | task)` — the four types the FE badges.

(Deferred, do **not** create in v1: `calendar_connections`, `calendar_settings`.)

## 5. Endpoints (`/api/v1/calendar`)

Envelope + tenancy + RBAC throughout; `startup_id` from the membership.

| Route | Access | Does |
|---|---|---|
| `POST /calendar/events` | member (writer) | Create an event. |
| `GET /calendar/events?date_from=&date_to=&type=` | member | List in range + optional type filter; ordered by date, then time. Powers Month/Week/Agenda. |
| `GET /calendar/events/{id}` | member | One event. |
| `PATCH /calendar/events/{id}` | member (writer) | Edit (reject explicit-null on required fields → 422, the pattern you'll see in Finance `TransactionUpdate`). |
| `DELETE /calendar/events/{id}` | member (writer) | Delete → `{"deleted": true}`. |
| `GET /calendar/milestones` | member | **Senior checkpoint (§7.1):** read-only projection of existing roadmap milestones, or defer. |

**Events (optional, minimal):** you *may* emit `calendar.event.created` via
`event_bus.publish(db, event, payload)` (note the **`db` first arg**) for a future reminder consumer —
but there's no consumer yet, so it's fine to skip and add when the scheduler lands.

## 6. Mirror this shipped code

- **Closest match — Finance transactions (just shipped):** `app/services/finance/service.py` +
  `app/db/models/finance.py` + the transaction routes in `app/api/v1/endpoints/finance.py`. A
  per-startup **dated record with an enum type and a list filtered by date-range + type** is exactly
  the calendar-events list. Copy the list-with-filters shape (`date_from`/`date_to`/`type`), the
  `_validation` 422 helper (`app/services/finance/errors.py`), and the explicit-null-on-PATCH →
  422 `model_validator`.
- **Your own Module 09/17** — for the service/endpoint/enum/test skeleton you already know.
- **Reading an existing module (for the Milestones surface, if in scope):** `app/db/models/roadmap.py`
  (`RoadmapMilestone`) + the roadmap service — read them, don't re-model milestones.
- **RBAC:** `require_role(...)` returning `Membership` (see the Finance `_finance = require_role(...)`
  dependency).
- **Tests:** `tests/api/test_finance.py` (the `_member`/`_headers` helper, envelope, real Postgres
  per-test rollback, RBAC + cross-tenant 404) and your own Module 09 tests.

## 7. ⚠️ Senior checkpoints (settle in brainstorming)

1. **Milestones — reuse, don't duplicate.** `RoadmapMilestone` already exists. The calendar's
   Milestones tab should either **read** roadmap milestones (a projection: title + due date + status),
   or be **out of scope** for this module (owned by Roadmap). Do not create a second milestone table.
   Agree which with your lead **before** you spec it.
2. **How much aggregation.** The Agenda shows cross-module items. v1 = calendar's own events only.
   Decide if surfacing roadmap-milestone due dates (read-only) is in v1 or v1.1.
3. **External sync — defer.** Confirm Google/Outlook/Apple OAuth is out of v1 (it is, unless your lead
   says otherwise). Don't model connection tables speculatively.
4. **Write access.** Founder-only writes, or any active member? The calendar is shared; pick the rule
   and enforce it in the RBAC dep.
5. **Reminders + preferences — defer.** The reminder scheduler and the sync-page toggles are v1.1.
6. **Time & timezone (real gotcha, §10).** Date + optional time vs a full datetime; the `all_day`
   flag; and **which timezone** times are stored/returned in. Finance uses UTC `date <= today` — be
   deliberate and consistent, and document it in the FE guide.

## 8. How to work (the standard loop)

1. **Brainstorm** with your lead — settle the v1 cut (§7), the milestones question, the time/timezone
   model. Write a short spec in `docs/superpowers/specs/`.
2. **Plan** it into small TDD tasks in `docs/superpowers/plans/`.
3. **Build test-first**, small commits.
4. **Verify** with the four layers (§9).
5. **Document:** SOP (`docs/sop/`), a **captured-live** FE guide
   (`docs/fe-integration-guide-calendar.md` — **real captured responses, never schema guesses**),
   tick the checklist.
6. **PR into `develop`** (team convention — not `main`). Rebase onto current `develop` before you
   push, and settle the migration number against the live head.

## 9. Definition of done (certified)

- **Unit/integration:** every endpoint + edge cases (date-range filter boundaries, type filter,
  all-day vs timed, explicit-null-PATCH → 422, tenancy cross-tenant 404, RBAC forbidden roles), real
  Postgres, per-test rollback, TDD.
- **Sanity:** full test suite green on a fresh migrated DB; single alembic head; `alembic check` clean.
- **Smoke:** calendar routes added to `e2e/test_smoke.py`'s surface assertion.
- **Live E2E** (`bash scripts/e2e_run.sh`): a member creates events across a date range → lists them
  by month/week → filters by type → edits one → deletes one — **real data over HTTP**, responses
  captured to `e2e/_captures/calendar/`.
- Gates green: `black` / `isort` / `ruff` / `mypy` / `pylint` / `bandit`; coverage ≥ 95%.
- SOP + FE guide (captured-live) + checklist updated.

## 10. Gotchas

- **Don't duplicate `RoadmapMilestone`.** Milestones are a read/reuse, not a new entity (§7.1). This
  is the main design trap in this module.
- **Timezone/date handling.** Decide `date` + optional `time` vs full `datetime`, the `all_day` flag,
  and the workspace timezone story. Return dates/times in a shape the FE can render without guessing;
  state it in the FE guide. Be consistent with the Finance UTC-date convention.
- **Migration numbering** — take the live head's next number at build time; renumber if you merge
  second. Right after Finance Slice 3 merges, the head is `0042_finance_invoices` (or higher) — check
  `git fetch && ls alembic/versions | tail` immediately before every push.
- **Defer cleanly (seam it).** External sync, aggregation, reminders, preferences — each goes in the
  spec's Deferred table with a one-line reason, not a half-built stub.
- **Event bus signature is `publish(db, event, payload)`** — the `db` first arg. Only emit if you add
  an event; no consumer exists yet.
- **GitGuardian** may flag the e2e signup password (advisory, non-required) — same known
  false-positive; not a blocker.
