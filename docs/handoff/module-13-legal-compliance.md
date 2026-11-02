# Handoff Brief: Module 13, Legal & Compliance (Slice 1)

> **For:** Victoria. This is your fifth module on `cofoundaz-api`: Module 17 Learning Academy, Module 09
> Validation Hub and Module 19 Calendar (merged 2026-10-12, PR #119 🎉) have all shipped. **Status:** not started.
> **Spec (authority):** `../Cofoundaz_Technical_PRD.md` → Module 13.
> **This brief is a starting point, not the spec.** It orients you, scopes Slice 1, points you at the
> patterns to copy, and flags what to settle with your lead. You still run the normal loop:
> **brainstorm → spec → plan → build (TDD) → review → verify → document.**
>
> **FE cross-check (do this first):** the prototype is at `../cofoundaz/`:
> - `app/(dashboard)/legal/page.tsx` (Formation), `.../legal/ip/page.tsx` (IP tracker) and
>   `.../legal/compliance/page.tsx` (Compliance);
> - all mock data and types are in `hooks/useLegalApi.ts`.
>
> It's a mock (local state, no API), and it disagrees with the PRD in several places (§3). Build to
> what the screens need, and write each disagreement into your spec, as you did for Calendar.

---

## 1. What you're building

The **"is my company legally in order?"** layer, in three parts:

- **Entity Formation (13.1):** a jurisdiction-specific checklist of incorporation steps that the
  founder ticks off.
- **IP Tracker (13.4):** a register of trademarks, patents, copyrights and domains, with renewal dates.
- **Compliance Calendar (13.5):** filings (VAT, PAYE, annual returns…) with due dates, a
  done-with-proof flow, an overdue banner, and **reminders at 14, 7 and 1 days before the due date,
  plus on the day it goes overdue**.

**Why this module is a good fit for you:** it's CRUD on dated, per-workspace records with derived
statuses, which is the Calendar pattern you just shipped. The new things it teaches are bounded and
all reuse code that already exists:
1. **A server-driven catalog.** The formation checklist is defined in code per jurisdiction, and only
   the founder's *progress* is stored.
2. **Your first scheduled job.** The reminders plug into the existing scheduler. You deferred this
   kind of work in Calendar; this time you build it.
3. **A file upload** for proof of filing, copying a shipped pattern.

**Not in Slice 1** (later slices, each has its own design questions):
- **13.6 Legal Advisor Requests (Slice 2):** an external role with scoped access, plus document versions.
- **13.2 Contract Templates and 13.3 AI Contract Review (Slice 3):** LLM jobs and PDF/DOCX parsing.

Leave those tabs alone. Their FE pages exist, but they aren't yours yet.

## 2. Access model ⚠️ settle first

The PRD says the module is accessible to **"F, LA (read/comment Legal module + shared docs, upload
reviewed drafts), TM (granted)"**. That's different from Calendar, where you deliberately excluded
everyone but founder and team member.

- **Writers:** founder and team member. This is the standard `require_role(...)` at
  `app/db/tenancy.py:39`, which returns the `Membership`. Same as `_calendar` at
  `app/api/v1/endpoints/calendar.py:21`.
- **`legal_advisor`** (a real `MembershipRole`, `app/db/models/enums.py:35-41`): **read-only on all
  three Slice 1 surfaces** is the lead's starting position. That means two dependencies: a reader set
  `(founder, team_member, legal_advisor)` on GETs, and a writer set `(founder, team_member)` on
  everything else. Commenting is Slice 2.
- **Everyone else** (mentor, accountant, business consultant, investor) gets 403. Tenancy is the
  usual: `startup_id` comes from the membership, **never from the request body**, and a cross-tenant
  id returns 404, never 403.

Confirm this split with your lead in brainstorming and write it into the spec as a decision. Test
**every role on every route**, including legal advisor getting 403 on writes.

## 3. Scope for Slice 1, and where the FE and PRD disagree

### 3.1 Entity Formation

- **The catalog lives in code, not the database.** Each jurisdiction maps to an ordered list of
  steps, and each step has a stable `key`, `title`, `authority`, `est_days` and `est_cost`. Use a
  module like `app/services/health_score/config.py` as the model for a code-defined config.
- **The database stores only progress:** which step keys this startup has completed, and when.
- **FE vs PRD:**
  - The mock offers `"Nigeria" | "Kenya" | "Ghana" | "Delaware, USA"` as **display strings**. Use
    **codes** (e.g. `NG`, `KE`, `GH`, `US-DE`) and return a display `name` alongside.
  - Each mock step has one free-text `meta` string ("CAC · est. 1 day · ₦500"). **Return structured
    fields** and let the FE format them.
  - The mock gives Kenya and Ghana the same generic steps as everyone else. That's mock laziness.
    Agree with your lead how many jurisdictions v1 really supports. Four with honest content beats
    ten with placeholders.
  - `est_cost` is money, so follow Finance and use **integer minor units plus a currency code**,
    never floats.
- **The PRD's `legal_entities` (name, type, reg_no, formed_on) and its "entity summary card" have no
  FE at all.** The mock only shows an "Entity Fully Formed" banner. **Senior checkpoint (§7.2).**

### 3.2 IP Tracker

- Full CRUD for `ip_assets`.
- The FE uses **type** (`trademark | patent | copyright | domain`), **name**, **jurisdiction**,
  **status** and **renewal date**.
- **FE vs PRD:**
  - The mock's `renewal` is display text ("Renews Feb 2027", "Renew now"). **Store `renewal_on` as a
    Date** and let the FE format it.
  - Status values disagree inside the mock itself: the table shows `Filed | Registered | Expired`,
    but the add drawer offers `Drafting | Filed / Pending | Registered`. Pick one stored enum.
    Consider whether **expired should be *derived*** from `renewal_on < today` rather than stored,
    the same move as Calendar's `calendar_status`.
  - The PRD's **"renewal date → auto compliance item"** is a cross-entity rule. **Senior checkpoint
    (§7.4).**

### 3.3 Compliance Calendar

- Full CRUD for `compliance_items`: title (the FE calls it `filing`), authority, `due_on`
  (**a Date**; the mock's "in 9 days" is display text), status, and an optional proof file.
- **Store only `open | done` (plus `done_at`). Derive `upcoming | due_soon | overdue`** from
  `due_on` against today, exactly as you did with `calendar_status`. The mock's four badges map onto
  that.
- **Mark done, optionally with proof.** The mock's "Mark done" button only shows a toast about proof
  upload. Proof is **optional** per the PRD.
- **The overdue banner** needs a count. A list endpoint that returns derived statuses lets the FE
  count them itself, or you can return a small summary. Your call, so record it in the spec.
- **The list/calendar toggle** is a placeholder in the mock. The list endpoint with a date range
  covers both.
- **Recurrence** (PRD: "recurring rules") has **no FE**, and **nothing in the codebase models
  recurrence yet** (the only near-match is the `Expense.recurring` boolean flag). **Senior checkpoint
  (§7.3).**

### 3.4 Reminders (scheduled job)

The PRD notification is **"Compliance filing due in 14 days: {item}"**, repeated at 7 and 1 days,
plus `legal.compliance.overdue`. Build it on the scheduler as described in §6. The full design
is in §7.5.

## 4. Data model (new migration; take the number at build time)

⚠️ **Migration coordination:** the head is `0046_calendar` today. The lead's next module (Sales or
Funding) will add migrations too. **Take the next free number against the live head when you
push**, and whoever merges second renumbers.

Same conventions as Calendar: `UUIDMixin` + `TimestampMixin`, `Enum(..., native_enum=False,
values_callable=…)`, `index=True` on every FK, and a per-workspace `startup_id`.

| Table | Columns (starting point, refine in the spec) |
|---|---|
| `legal_formation_progress` | `startup_id` · `jurisdiction` (code) · `step_key` · `completed_at` · `completed_by`; unique on `(startup_id, jurisdiction, step_key)` |
| `legal_entities` *(only if §7.2 says yes)* | `startup_id` (unique) · `jurisdiction` · `type` · `name` · `reg_no` · `formed_on` |
| `ip_assets` | `startup_id` · `type` (enum) · `name` · `jurisdiction` · `status` (enum) · `renewal_on` (Date, nullable) · `created_by` |
| `compliance_items` | `startup_id` · `title` · `authority` · `due_on` (Date, indexed with `startup_id`) · `status` (`open`/`done`) · `done_at` · `proof_url` / `proof_key` (nullable) · `recurrence` *(only if §7.3)* · `ip_asset_id` *(only if §7.4)* · `created_by` |

**Keying progress by jurisdiction is deliberate.** It means switching jurisdiction doesn't silently
wipe ticks; the mock's reset-on-switch is a mock shortcut. Decide in the spec whether switching
jurisdiction is even allowed once steps are done.

## 5. Endpoints (`/api/v1/legal`)

| Route | Access | Does |
|---|---|---|
| `GET /legal/formation/jurisdictions` | reader | The supported jurisdictions (code + name). |
| `GET /legal/formation/checklist?jurisdiction=` | reader | The catalog steps for that jurisdiction, merged with this startup's progress. |
| `PATCH /legal/formation/steps/{step_key}` | writer | `{jurisdiction, completed: bool}` ticks or unticks one step. An unknown key or jurisdiction returns 422 or 404 (decide which and document it). |
| CRUD `/legal/ip-assets` | reader GET · writer rest | IP register. |
| CRUD `/legal/compliance-items` (`?date_from=&date_to=&status=`) | reader GET · writer rest | Compliance list with derived status. Ordered by `due_on`. |
| `POST /legal/compliance-items/{id}/proof` (multipart) | writer | Attach the proof file (§6). |
| `GET/PUT /legal/entity` *(only if §7.2)* | reader / writer | The entity summary. |

**Events** use `event_bus.publish(db, event, payload)`, with **`db` as the first argument**. Emit
`legal.compliance.due_soon` and `legal.compliance.overdue` from the scheduled handler (§6).

## 6. Mirror this shipped code

- **Closest match: your own Calendar.** Use `app/db/models/calendar.py`, `app/schemas/calendar.py`,
  `app/services/calendar/{service,errors,milestones}.py` and `app/api/v1/endpoints/calendar.py` for
  the skeleton, the date-range list, the derived status, the explicit-null-on-PATCH 422 rule, and
  register the router in `app/api/v1/api.py`.
- **Proof upload: the Finance expense receipt.** Copy `attach_receipt` in
  `app/services/finance/expenses.py` (~line 106): it allows PNG, JPEG or PDF up to 10 MB, uploads via
  `get_storage()` (`app/platform/storage.py`), uses a key like `receipts/{startup_id}/{uuid}{ext}`,
  deletes the old key on replace, and stores `receipt_url`/`receipt_key` on the row
  (`app/db/models/expense.py:32-33`). The PRD names a `proof_doc_id` that would link into the
  Documents library (`document_files`), but **no module links to Documents yet**. Using the receipt
  pattern keeps Slice 1 self-contained. Note "show proofs in the Documents library" as deferred.
- **Reminders: the scheduler.**
  - The scheduler is `app/worker/scheduler.py`. Add a `_due_compliance(db, now)` that returns `Due`
    tuples, and include it in `scheduler_tick`.
  - Idempotency is `_claim()`: it inserts into `scheduled_runs` (unique on
    `task_key, scope_key, period_key`) inside `db.begin_nested()`, so each `(item, threshold)` fires
    once. Use `scope_key = item id` and `period_key = "due14" | "due7" | "due1" | "overdue"`.
  - The handler pattern is `app/worker/handlers/scheduled.py` → `handle_roadmap_overdue`. It
    **re-checks state before publishing** (skip if already done), then calls `event_bus.publish`.
  - For notifications:
    - Add `SPECS` entries in `app/services/notifications/registry.py`. Use the **dynamic `NotifSpec`
      form** so the title can include the filing name; see `marketing.post.published`.
    - Map the event in `app/services/notifications/categories.py`. **There's no `legal` category
      yet**, and an unmapped event is in-app only. Decide whether to add one, since it affects email
      preferences.
- **RBAC tests:** your `tests/api/test_calendar.py` already loops over forbidden roles. Extend it
  with the reader/writer split.

## 7. ⚠️ Senior checkpoints (settle in brainstorming)

1. **Legal-advisor access.** Read-only on Slice 1, as in §2? Confirm before you write any RBAC.
2. **`legal_entities` and the summary card.** The PRD wants it but the FE has nothing. Either build
   a minimal `GET/PUT /legal/entity` now, or defer it until the FE designs the card. Agree which.
3. **Recurrence.** Either build a small enum (`none | monthly | quarterly | annually`) where marking a
   recurring item done **creates the next occurrence**, or defer it. VAT and PAYE are monthly in
   Nigeria, so it's core to the domain. But there's no FE for it, and nothing in the repo to copy.
   If you build it, the next-occurrence date math (e.g. 31 Jan + 1 month) is the trap; test month-end
   explicitly.
4. **IP renewal → compliance item.** Either an IP asset with a `renewal_on` auto-creates or updates a
   linked compliance item (`ip_asset_id`), so renewals get the same reminders, or defer it. If you
   build it, define what happens when the renewal date changes or the asset is deleted.
5. **Reminder design.**
   - What "today" is: Calendar used the UTC date, and the scheduler has `settings.SCHEDULER_TIMEZONE`.
     Pick one and be consistent between the derived status and the reminders.
   - Who gets notified: all active members, which is the existing helper's behaviour.
   - What happens when a due date is moved after a reminder has fired. The claim is per
     `(item, threshold)`, so moving the date back won't re-fire it. Is that acceptable?
6. **Jurisdiction content.** Which jurisdictions v1 supports, and who supplies real step content.
   Don't ship placeholder legal steps as if they were advice. The PRD's §1.4 disclaimer applies to
   anything that looks like legal guidance.

## 8. How to work (the standard loop)

1. **Brainstorm** with your lead and settle §7. Write a short spec in `docs/superpowers/specs/` and
   open a "spec ready for review" issue, as you did with #118.
2. **Plan** it into small TDD tasks in `docs/superpowers/plans/`.
3. **Build test-first** in small commits, on branch `feat/legal-compliance` off the current `develop`.
4. **Verify** using the four layers in §9.
5. **Document:** an SOP (`docs/sop/`), a **captured-live** FE guide
   (`docs/fe-integration-guide-legal.md`), and tick the checklist.
6. **Open the PR into `develop`.** Rebase first, and settle the migration number against the live head.

**About the live e2e on your machine:** it doesn't run on your Windows host. Use the Calendar
precedent: write `e2e/test_legal.py` with the `capture` fixture, confirm it passes in CI on the PR,
and your lead runs `scripts/e2e_run.sh` on a Docker host and commits the captures. Ship the FE guide
labelled and with a capture map until then, exactly as you did for #119. That worked well.

## 9. Definition of done

- **Unit/integration**, using real Postgres with per-test rollback, written TDD. Cover every endpoint,
  plus these edge cases:
  - the derived-status boundaries (due today, due in 14 days exactly, overdue by one day);
  - the reader/writer RBAC split for every role;
  - a cross-tenant id returning 404;
  - an explicit null on PATCH returning 422;
  - an unknown jurisdiction or step key;
  - proof upload of a bad type or an oversized file.
- **Scheduler:**
  - each threshold fires exactly once;
  - a second tick doesn't re-fire;
  - a done item doesn't fire;
  - the overdue notification fires once.
  Test the `_due_compliance` selection and the handler separately, like the roadmap-overdue tests.
- **Sanity:** the full suite is green on a freshly migrated database, there's a single alembic head,
  and `alembic check` is clean.
- **Smoke:** the legal routes are added to the surface assertion in `e2e/test_smoke.py`.
- **Live e2e** (`e2e/test_legal.py`), real data over HTTP, captured to `e2e/_captures/legal/`:
  1. pick a jurisdiction, tick steps, see progress;
  2. add an IP asset;
  3. create compliance items across a range, list them and see the derived statuses;
  4. mark one done with proof;
  5. confirm the legal advisor can read but gets 403 on write.
- **Gates green:** `black` / `isort` / `ruff` / `mypy` / `pylint` / `bandit`, with coverage ≥ 95%.
- **Docs:** SOP, FE guide (captured-live) and checklist all updated.

## 10. Gotchas

- **Dates, not strings.** The mock's `due` and `renewal` values are display text. The API returns ISO
  dates, and the FE formats them. Call this out in the FE guide, as you did for `HH:MM` in Calendar.
- **Derived statuses must agree everywhere.** The list, the overdue count and the scheduler must
  agree on what "today" and "overdue" mean (§7.5).
- **Scheduler idempotency lives in `_claim()`, not your handler.** Don't add your own "already sent"
  flags. Make the handler's re-check (skip if done) the only state check.
- **Notification titles need the payload.** Payloads carry ids; the dynamic `NotifSpec` form reads
  the item title. Load it by id inside the spec function, or include `title` in the payload. Decide
  which, and keep it consistent.
- **SAVEPOINT rule** (repo memory): for any duplicate-key → 422 insert, `db.add()` must be **inside**
  `with db.begin_nested()`, or the failed row poisons the session. That applies to your
  `(startup_id, jurisdiction, step_key)` unique constraint, so test it with a follow-up read in the
  same test.
- **Migration numbering:** check `git fetch && ls alembic/versions | sort | tail` immediately before
  every push.
- **GitGuardian** may flag the e2e signup password. It's advisory, a known false positive, and not a
  blocker.
- **No legal advice in fixtures.** Seed and catalog content should be clearly labelled estimates with
  the §1.4 disclaimer, not authoritative statements.
