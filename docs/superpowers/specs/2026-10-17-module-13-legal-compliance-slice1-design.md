# Module 13 — Legal & Compliance, Slice 1

**Status:** draft, pending lead review · **Branch:** `feat/legal-compliance` (off `develop`)
**Author:** Victoria · **Date:** 2026-10-17

## Context

**Authority:** `Cofoundaz_Technical_PRD.md` → Module 13. The handoff brief
`docs/handoff/module-13-legal-compliance.md` orients the work; it is not the spec.

The "is my company legally in order?" layer, in three parts: a per-jurisdiction **formation
checklist**, an **IP register** with renewal dates, and a **compliance calendar** of filings with
derived statuses, proof of filing, recurrence and reminders.

All six senior checkpoints were settled with the lead before this spec, plus four findings from
the FE cross-check (§Decisions, D1–D16). Nothing is open.

**Not in Slice 1:** Advisor Requests (13.6, Slice 2), Contract Templates and AI Contract Review
(13.2/13.3, Slice 3). Their FE pages exist and are not touched.

### FE cross-check (per the standing rule)

Read on 2026-10-17 from `../cofoundaz` — `hooks/useLegalApi.ts` (144 lines) and
`app/(dashboard)/legal/{page,ip/page,compliance/page}.tsx` (87 / 179 / 105). A mock: `useState`
and hard-coded arrays, no network. Seven places where it disagrees with what this slice builds:

1. **Every date is a sentence.** Compliance `due` is `"Was due Jul 10"`, `"in 9 days"`, `"Aug 10"`,
   `"Jun 30"` — four formats. IP `renewal` is `"Renews 2034"`, `"Renews Feb 2027"`,
   `"No renewal"`, `"Renew now"` — four formats, two of which are not dates at all
   (`"No renewal"` means null, `"Renew now"` means overdue). The API returns ISO dates.
2. **Formation step ids are positional**, not identities: `"step-1"`, `"step-2"`. Switching
   jurisdiction reassigns `"step-1"` to a different step — "Reserve company name" in Nigeria,
   "File Certificate of Incorporation" in Delaware. Progress here is keyed on
   `(startup_id, jurisdiction, step_key)`, so **the FE must send real step keys**. This is a
   required FE change, not a formatting note (D14).
3. **Only one and a half jurisdictions have real content.** `"Delaware, USA"` gets three genuine US
   steps; **Nigeria, Kenya and Ghana all share the same four generic steps**. The detailed Nigerian
   ones (CAC, FIRS, ₦500) appear only on first load — switch away and back and Nigeria silently
   becomes generic. That is a mock bug, and it is why Slice 1 supports two jurisdictions (D6).
4. **Step detail is one squashed string:** `"CAC · est. 1 day · ₦500"` mixes authority, duration
   and cost. Returned as separate fields, cost in integer minor units plus a currency (D5).
5. **The IP status contradiction resolves itself.** The table can *display* `Expired`, but the add
   drawer only offers `Drafting | Filed / Pending | Registered` — a user cannot choose Expired. So
   it is not a stored status; it is a consequence of `renewal_on` passing. Three stored values,
   expired derived (D9).
6. **The two screens use different jurisdiction vocabularies.** Formation offers Nigeria / Kenya /
   Ghana / Delaware USA; IP offers Global / Nigeria / USA / EU. Only Nigeria overlaps, and they are
   different concepts — where you incorporated, versus where a mark is registered. They stay
   separate: formation a closed set of codes, IP a bounded free string (D10).
7. **Capitalisation differs.** The mock uses `"Trademark"`, `"Filed"`, `"Overdue"`. The API returns
   lowercase enum values throughout, as Calendar does.

**Evidence that settled two open questions:**

- **No overdue-count endpoint is needed.** `compliance/page.tsx:14` already computes it:
  `complianceItems.filter(i => i.status === "Overdue").length`. Derived statuses in the list
  suffice (D13).
- **The due-soon window is 14 days, not 7.** The mock badges `"in 9 days"` as `Due soon`, and 14
  is also the first reminder threshold — so the badge and the first reminder agree, which the
  brief's gotcha requires (D12).

**Fields with no backend source** (omitted, labelled not-backed in the FE guide):
`FormationStep.meta` as a single string; the **"Docs" button on every formation step**, which has
no handler at all; and every `*Bg` / `dueColor` styling field.

**Most of the CRUD has no screen yet.** IP has add only — no edit, no delete. Compliance has **no
create form at all**, and no edit or delete. Those shapes are build-ahead and labelled provisional
in the FE guide, as Module 10 Slice 4 did (D11).

## Goal

Three tables, one code-defined catalog, fourteen routes, and two scheduled reminder tasks.

## Global constraints (carried verbatim where they bind)

- **RBAC, two dependencies (D1):** a new
  `LEGAL_ROLES = frozenset({founder, team_member, legal_advisor})` in `app/db/models/enums.py`
  alongside `FINANCE_ROLES`, `MARKETING_ROLES` and `SALES_ROLES`; `_legal_reader` is built from it
  and guards every GET, and `_legal_writer = require_role(founder, team_member)` guards every
  write. Mentor, accountant, business consultant and investor get **403** throughout. Legal
  advisor gets **403 on every write**. Every role is tested on every route.
- `success_response` envelope; `AppError` via a local `_validation` helper copied from
  `app/services/finance/errors.py`.
- `get_db()` never commits. Write endpoints call `db.commit()`; services only `flush()`.
- `startup_id` comes from the returned `Membership`, **never** from the body or query. A
  cross-tenant id raises `NotFound` → uniform **404**, never 403.
- `Enum(..., native_enum=False, length=N, values_callable=…)` so the database stores enum
  **values**; `index=True` on every FK; `UUIDMixin` + `TimestampMixin`.
- **"Today" is `datetime.now(UTC).date()`** in the derived statuses *and* in the scheduled
  handlers — deliberately **not** the scheduler's `_local(now)`, which resolves through
  `settings.SCHEDULER_TIMEZONE` (D4).
- **SAVEPOINT rule:** any insert that can hit a unique constraint goes **inside**
  `with db.begin_nested()`, or the failed row poisons the session. Binds on
  `(startup_id, jurisdiction, step_key)` and on `previous_item_id`.
- **Scheduler idempotency lives in `_claim()`**, keyed `(task_key, scope_key, period_key)`. No
  "already sent" flags of our own.
- Migration number taken against the live `develop` head at build time — **`0067_legal`**,
  chaining `0066_finance_invoice_automation`, re-checked immediately before pushing. (The review
  said `0059_legal`; by the time the rebase landed, `develop` had moved 147 commits rather than 75,
  and `0059` turned out to be a *skipped slot* between `0058_mvp_feedback` and
  `0060_assessment_stage_at_time` rather than the next free number.) One alembic head, `alembic check` clean.
- No new dependencies. `python-dateutil ^2.8.2` is already in `pyproject.toml:98`.

## Design

### 1. Formation catalog — in code, not the database

`app/services/legal/catalog.py`, modelled on `app/services/health_score/config.py`.

```python
CATALOG_VERSION = 1
# Figures below are transcribed from the FE prototype and are NOT verified.
# The lead verifies them before production; until then this stays None (D6).
CATALOG_REVIEWED_ON: date | None = None

JURISDICTIONS: dict[str, str] = {"NG": "Nigeria", "US-DE": "Delaware, USA"}
```

Each step carries `key`, `title`, `authority`, `est_days`, `est_cost_minor` (nullable) and
`est_cost_currency` (nullable), in catalog order.

| Jurisdiction | Steps (key · title · authority · est_days · est_cost) |
|---|---|
| `NG` | `reserve_name` · Reserve company name · CAC · 1 · 50000 NGN (₦500) |
| | `file_incorporation` · File incorporation documents · CAC · 3 · 2000000 NGN (₦20,000) |
| | `tax_id` · Get Tax Identification Number · FIRS · 5 · — |
| | `bank_account` · Open a corporate bank account · Your bank · 2 · — |
| `US-DE` | `certificate_of_incorporation` · File Certificate of Incorporation · Delaware Secretary of State · 1 · — |
| | `ein` · Obtain EIN · IRS · 2 · — |
| | `bank_account` · Open a corporate bank account · Mercury / Brex · 2 · — |

Costs are **integer minor units plus a currency code**, following Finance — never floats (D5).
Where the mock gives no figure, both fields are null rather than zero: "we don't know" and "free"
are different.

`est_days` and `est_cost_minor` are **labelled estimates** in the response (an `estimates: true`
marker and the PRD §1.4 disclaimer string), so no client can present them as verified fact (D6).

**Table `legal_formation_progress`** — progress only, never the catalog:

| Column | Type | Notes |
|---|---|---|
| `startup_id` | UUID FK `startups.id` CASCADE | not null, `index=True` |
| `jurisdiction` | `String(10)` | not null — a `JURISDICTIONS` key |
| `step_key` | `String(60)` | not null |
| `completed_at` | `DateTime(tz)` | not null, `server_default now()` |
| `completed_by` | UUID FK `users.id` SET NULL | nullable, `index=True` |

Unique on `(startup_id, jurisdiction, step_key)`.

**A row's existence *is* the tick.** Unticking deletes the row rather than nulling a flag, which
keeps the unique constraint meaningful and avoids a nullable boolean that can disagree with
`completed_at` (D7).

**Keying by jurisdiction is deliberate.** Switching jurisdiction does not wipe ticks — the other
jurisdiction's rows simply aren't read. The mock's reset-on-switch is a mock shortcut. Switching
is always allowed; nothing is destroyed (D8).

### 2. `ip_assets`

| Column | Type | Notes |
|---|---|---|
| `startup_id` | UUID FK CASCADE | not null, `index=True` |
| `type` | `Enum(IpAssetType)` | `trademark \| patent \| copyright \| domain` |
| `name` | `String(200)` | not null, 1–200 trimmed |
| `jurisdiction` | `String(100)` | not null, 1–100 trimmed — a free string, because it must hold `"Global"` (D10) |
| `status` | `Enum(IpAssetStatus)` | `drafting \| filed \| registered` — **three values** (D9) |
| `renewal_on` | `Date` | nullable |
| `created_by` | UUID FK `users.id` SET NULL | nullable, `index=True` |

**`expired` is derived, never stored (D9).** Returned as `derived_status`:

| Condition | `derived_status` | FE badge |
|---|---|---|
| `renewal_on` is set and `renewal_on < today` | `expired` | Expired (with "Renew now") |
| otherwise | the raw `status` value | Drafting / Filed / Registered |

Checked in that order, uniformly on the date — a `drafting` row with a past renewal date is odd
input, and deriving uniformly is more predictable than special-casing it. Both `status` and
`derived_status` are returned, as Calendar returns both.

### 3. `compliance_items`

| Column | Type | Notes |
|---|---|---|
| `startup_id` | UUID FK CASCADE | not null, `index=True` |
| `title` | `String(200)` | not null, 1–200 trimmed. **The FE calls this `filing`** — stated in the guide |
| `authority` | `String(120)` | **nullable**, 1–120 when given (D15) |
| `due_on` | `Date` | not null |
| `status` | `Enum(ComplianceStatus)` | `open \| done`, default `open` |
| `done_at` | `DateTime(tz)` | nullable, set on mark-done, cleared on reopen |
| `proof_url` | `String(500)` | nullable |
| `proof_key` | `String(500)` | nullable |
| `recurrence` | `Enum(Recurrence)` | `none \| monthly \| quarterly \| annually`, default `none` |
| `anchor_day` | `Integer` | not null — the series' anchor day-of-month, 1–31. Lifecycle in D24 |
| `previous_item_id` | UUID FK `compliance_items.id` SET NULL | nullable, **unique** |
| `created_by` | UUID FK `users.id` SET NULL | nullable, `index=True` |

Indexes: `ix_compliance_items_startup_id`, `ix_compliance_items_created_by`, and a composite
`ix_compliance_items_startup_id_due_on` for the range query.

**Derived status (D12).** Stored is only `open | done`; the four badges are computed:

| Condition | `derived_status` |
|---|---|
| `status == done` | `done` |
| `due_on < today` | `overdue` |
| `due_on <= today + 14 days` | `due_soon` |
| otherwise | `upcoming` |

In that order: a done item is never overdue. The **14-day window** matches the first reminder
threshold, so the badge and the first notification agree — and it matches the mock, which badges
"in 9 days" as Due soon. Due *today* is `due_soon`, not `overdue`.

**Recurrence (D16).** Marking a recurring item done creates **one** next occurrence. Nothing is
pre-generated.

```
n = {monthly: 1, quarterly: 3, annually: 12}[recurrence]
next_due = due_on + relativedelta(months=+n, day=anchor_day)
```

**Computed from the anchor, not from the previous date.** `day=` clamps to month-end, and
anchoring prevents drift: from the previous occurrence, 31 Jan → 28 Feb → **28 Mar**; from the
anchor, 31 Jan → 28 Feb → **31 Mar**. Required tests:

| Recurrence | From | Then | Then |
|---|---|---|---|
| monthly | 31 Jan | 28 Feb (29 in a leap year) | 31 Mar |
| quarterly | 30 Nov | 28 Feb | 30 May |
| annually | 29 Feb | 28 Feb | 29 Feb in the next leap year |

The new row copies `startup_id`, `title`, `authority`, `recurrence` and `anchor_day`; takes
`due_on = next_due`, `status = open`, `done_at = null`, no proof; and sets
`previous_item_id = <the item just completed>`.

**Idempotent spawn.** `previous_item_id` is **unique**, so a second spawn for the same predecessor
raises `IntegrityError`. The insert sits inside `with db.begin_nested()`; the `IntegrityError` is
caught and treated as a **no-op**, leaving the outer transaction usable. Marking done twice, or
done → reopen → done, therefore yields exactly one successor.

**`anchor_day`'s lifecycle (D24).** Set from `due_on.day` on create, and **reset whenever a
PATCH changes `due_on`** — a founder moving the series date means the series moves, so an item on
the 31st patched to the 15th then marked done yields a successor on the **15th**, not the 31st.
The spawn always uses the completed item's own anchor.

**Reopening keeps the successor.** Reopening sets `status = open` and `done_at = null` and does
**not** delete the occurrence it already spawned — deleting someone's future filing as a side
effect of a correction would be worse than a duplicate row they can delete themselves.
**Deleting an item deletes only that occurrence**; `previous_item_id` is `SET NULL`, so a
successor survives its predecessor's deletion and simply loses the back-link. A consequence worth
stating: once that back-link is null, `done -> reopen -> done` on the predecessor **spawns a fresh
successor**, because the uniqueness guard has nothing left to collide with. That is the intended
behaviour — the series should continue after someone deletes an occurrence by mistake.

**Proof upload.** Copies `attach_receipt` (`app/services/finance/expenses.py:106`) exactly:
PNG / JPEG / PDF only, **10 MB** cap, `get_storage().save(...)`, key
`legal-proofs/{startup_id}/{uuid}{ext}`, the new asset saved **before** the old key is deleted so
a failed upload never orphans the row, and `proof_url` / `proof_key` stored on the row. Proof is
**optional** — mark-done works without it, per the PRD.

### 4. API — `/api/v1/legal`

| Route | Access | Does |
|---|---|---|
| `GET /legal/formation/jurisdictions` | reader | `[{code, name}]` from the catalog. |
| `GET /legal/formation/checklist?jurisdiction=` | reader | Catalog steps merged with this workspace's progress. Unknown or unsupported jurisdiction → **404**. |
| `PATCH /legal/formation/steps/{step_key}` | writer | `{jurisdiction, completed}`. Unknown jurisdiction → 404; a `step_key` not in that jurisdiction's catalog → **404** (D3). |
| `POST /legal/ip-assets` | writer | Create. |
| `GET /legal/ip-assets?type=&status=` | reader | List, newest first. |
| `GET /legal/ip-assets/{id}` | reader | One. |
| `PATCH /legal/ip-assets/{id}` | writer | Partial edit. |
| `DELETE /legal/ip-assets/{id}` | writer | `{"deleted": true}`. |
| `POST /legal/compliance-items` | writer | Create. |
| `GET /legal/compliance-items?date_from=&date_to=&status=` | reader | List with `derived_status`, ordered `due_on ASC`, then `created_at ASC`. |
| `GET /legal/compliance-items/{id}` | reader | One. |
| `PATCH /legal/compliance-items/{id}` | writer | Partial edit; `{"status": "done"}` marks done and may spawn a successor. |
| `DELETE /legal/compliance-items/{id}` | writer | `{"deleted": true}`. |
| `POST /legal/compliance-items/{id}/proof` | writer | multipart. Attach or replace the proof file. |

`status=` on the compliance list filters on the **stored** value (`open` / `done`), not the
derived one — a filter on a derived value would need the same date maths in SQL and in Python,
and they would drift. Stated in the FE guide.

**Explicit-null on PATCH** follows Calendar: an explicit `null` for a NOT NULL column
(`title`, `due_on`, `recurrence`, `type`, `name`, `jurisdiction`, `status`) → **422**; an explicit
`null` for a nullable one (`authority`, `renewal_on`) **clears** it; omitting a field is a no-op.

**No pagination** on either list in Slice 1, matching the shipped Finance and Calendar lists.
Noted as a follow-up.

### 5. Reminders — two scheduled tasks

Two new task keys in `app/worker/scheduler.py`:

```python
SCHED_COMPLIANCE = "scheduled.legal.compliance.due"
SCHED_IP_RENEWAL = "scheduled.legal.ip.renewal"
```

**`_due_compliance(db, now)`** selects `open` items in non-deleted workspaces and yields `Due`
tuples with `scope_key = str(item.id)` and:

| `period_key` | Condition |
|---|---|
| `due14` | `due_on <= today + 14` |
| `due7` | `due_on <= today + 7` |
| `due1` | `due_on <= today + 1` |
| `overdue` | `due_on < today` |

**Thresholds are windows (`<=`), not exact days (D17).** An exact-day match
(`due_on == today + 14`) loses that reminder permanently if the scheduler misses a day; a window
plus the once-only `_claim` is self-healing.

**Simultaneous thresholds collapse into one notification (D21).** When several windows qualify in
the same tick — an item created inside a window, or created already overdue — **every qualifying
`period_key` is claimed**, so none of them can fire again later, but **only the most urgent is
enqueued**:

```
overdue  >  due1  >  due7  >  due14
```

An item created five days out therefore claims `due14` and `due7` together, enqueues one `due7`
notification, and still fires `due1` on its own four days later. An item created already overdue
claims all four and enqueues one `overdue`. This removes D17's "three notifications close
together" cost without giving up the self-healing window.

**`_due_ip_renewals(db, now)`** selects assets where `status != drafting` and `renewal_on` is not
null, with the same `due14` / `due7` / `due1` windows. **No `overdue` key** — a passed renewal is
already visible as `derived_status: expired` with a "Renew now" action, and a notification about
something possibly expired for a year is noise (D18).

Both are added to the `due` list in `scheduler_tick`.

**Handlers** in `app/worker/handlers/scheduled.py`, following `handle_roadmap_overdue`: load the
row, **re-check state** and return silently if it has gone (deleted) or no longer qualifies
(compliance now `done`; asset now `drafting` or `renewal_on` cleared), then publish.

**Events and payloads.** `event_bus.publish(db, event, payload)` — `db` first.

| Event | From |
|---|---|
| `legal.compliance.due_soon` | `due14` / `due7` / `due1` |
| `legal.compliance.overdue` | `overdue` |
| `legal.ip.renewal_due_soon` | IP `due14` / `due7` / `due1` |

**The payload carries the title and the due date**, not just ids:
`{startup_id, item_id, title, due_on, days_out}`. The alternative — loading the row inside the
notification spec function — renders an empty title if the row was deleted between enqueue and
render, and the spec functions in `registry.py` take only the payload (D19).

**`days_out` is computed when the handler publishes, never taken from the `period_key` (D22).**
Because thresholds are windows, a `due14` claim can fire for an item due in three days, so the
threshold name is not the distance. The handler computes `(due_on - today).days`, and the title
reads from that:

| `days_out` | Copy |
|---|---|
| `0` | "due today" |
| `1` | "due tomorrow" |
| otherwise | "due in {n} days" |

**Notifications go to the legal reader set, not to every active member (D4).** The titles name
the filing, and mentors, accountants, consultants and investors are 403 on every legal route — so
delivering these to them would leak exactly what the routes withhold. The same leak was closed
across the other modules in #150/#152/#153.

**The mechanism is `AUDIENCE`, not a bespoke recipients function.** The review pointed at a
`_validation_members` helper in `registry.py`; that no longer exists. What the repo has now is
`app/services/notifications/audience.py`, whose `AUDIENCE` map is the single source for **both**
fan-out and the read filter — `registry._audience(event)` builds recipients from it, and
`service.py` applies `audience_filter(role)` on every read path, so what is delivered and what is
shown cannot drift. Using it also hides the row from a mentor who somehow holds one, which a
bespoke recipients function would not.

Three entries in `AUDIENCE`, all mapped to `LEGAL_ROLES`:

```python
"legal.compliance.due_soon": LEGAL_ROLES,
"legal.compliance.overdue": LEGAL_ROLES,
"legal.ip.renewal_due_soon": LEGAL_ROLES,
```

Then three `SPECS` entries in `registry.py`, using the **dynamic `NotifSpec`** form so the title
can name the filing, as `marketing.post.published` does, with `minus_actor=False` because these
originate from the scheduler and have no actor:

```python
"legal.compliance.due_soon": NotifSpec(
    _audience("legal.compliance.due_soon", minus_actor=False),
    lambda p: f"Compliance filing {_due_phrase(p)}: {p.get('title', 'a filing')}",
    lambda _p: "",
),
```

A new `"legal"` category is added to `app/services/notifications/categories.py` and the three
events mapped to it, so founders can control compliance email volume separately. **No migration is
needed:** preferences are JSONB read through `stored.get(c, CATEGORY_DEFAULTS[c])`, so a new
category defaults ON automatically (D20).

**The FE must add a row for it (D23).** The preferences screen hard-codes its five categories in
`hooks/useNotificationPreferences.ts` and
`components/notifications/notification-preferences.tsx`, so the new toggle will not appear on its
own. That is a required FE change, called out in the FE guide. A PUT from today's FE omits `legal`
entirely, and the merge in `app/services/notifications/preferences.py` only copies keys it
recognises, so an omitted category keeps its stored value rather than being switched off —
asserted by a test on the PUT.

## Data flow / tenancy / transactions

1. Request → `_legal_reader` or `_legal_writer` → `Membership` (or 403).
2. Service functions take `startup_id` as a keyword and filter on it in every query. Nothing reads
   a workspace id off the request.
3. A read for an id outside the workspace raises `NotFound()` → uniform 404.
4. Services `flush()` only. The endpoint commits on create, patch, delete and proof upload; the
   reads never commit.
5. The two unique-constraint inserts — formation ticks and recurrence spawn — sit inside
   `db.begin_nested()`.
6. The scheduler claims and enqueues, committing once at the end of `scheduler_tick`; the handler
   re-checks state, then publishes.

## Components / files

**New**

- `app/db/models/legal.py` — `LegalFormationProgress`, `IpAsset`, `ComplianceItem`
- `app/schemas/legal.py` — formation, IP and compliance request/response models
- `app/services/legal/__init__.py`
- `app/services/legal/errors.py` — `_validation`, copied from the Finance helper
- `app/services/legal/catalog.py` — the code-defined jurisdictions and steps
- `app/services/legal/formation.py` — checklist merge, tick and untick
- `app/services/legal/ip.py` — IP CRUD and `derived_status`
- `app/services/legal/compliance.py` — compliance CRUD, `derived_status`, recurrence, proof
- `app/services/legal/recurrence.py` — `next_due_on(due_on, recurrence, anchor_day)`, pure
- `app/api/v1/endpoints/legal.py` — the fourteen routes
- `alembic/versions/0067_legal.py`
- `tests/db/test_legal_models.py`, `tests/test_legal_migration.py`
- `tests/services/legal/test_catalog.py`, `test_formation.py`, `test_ip.py`,
  `test_compliance.py`, `test_recurrence.py`
- `tests/api/test_legal.py`
- `tests/worker/test_legal_scheduler.py` — `_due_compliance` / `_due_ip_renewals` selection and
  the two handlers, tested separately as the roadmap-overdue tests are
- `e2e/test_legal.py`
- `docs/fe-integration-guide-legal.md`, `docs/sop/<ship-date>-legal-compliance-slice1.md`

**Touched**

- `app/db/models/enums.py` — `+ IpAssetType`, `IpAssetStatus`, `ComplianceStatus`, `Recurrence`,
  and `LEGAL_ROLES` beside the other module role sets
- `app/db/models/__init__.py` — export the three models
- `app/api/v1/api.py` — `include_router(legal.router, prefix="/legal", tags=["legal"])`
- `app/worker/scheduler.py` — `SCHED_COMPLIANCE`, `SCHED_IP_RENEWAL`, `_due_compliance`,
  `_due_ip_renewals`, both added to `scheduler_tick`
- `app/worker/handlers/scheduled.py` — two handlers + `register_handler` calls
- `app/services/notifications/audience.py` — three `AUDIENCE` entries mapped to `LEGAL_ROLES`
- `app/services/notifications/registry.py` — three `SPECS` entries built with
  `_audience(..., minus_actor=False)`, plus a `_due_phrase(payload)` helper for the D22 copy
- `app/services/notifications/categories.py` — `+ "legal"` category and three mappings
- `e2e/test_smoke.py` — the legal route shapes
- `docs/checklist/PROJECT_CHECKLIST.md`

## Review focus (inputs the spec implies but happy-path tests may miss)

- **Recurrence month-end**, all three series above, plus a leap year on both sides.
- **Idempotent spawn:** mark done twice; done → reopen → done; two concurrent mark-dones. Exactly
  one successor, and the session still usable afterwards (the SAVEPOINT).
- **Reopen keeps the successor; delete removes only one occurrence** and leaves the successor with
  a null `previous_item_id`.
- **Derived-status boundaries:** due today → `due_soon`; due in exactly 14 days → `due_soon`; due
  in 15 → `upcoming`; overdue by one day → `overdue`; done and overdue → `done`.
- **IP `derived_status`:** `renewal_on` null → raw status; `renewal_on` yesterday → `expired`;
  `renewal_on` today → **not** expired.
- **Formation:** tick, untick, re-tick; the same `step_key` ticked under two jurisdictions is two
  independent rows; a duplicate tick returns 200 and does not create a second row, with a
  follow-up read in the same test to prove the session survived; unknown jurisdiction → 404;
  `step_key` valid in `NG` but sent with `jurisdiction=US-DE` → 404.
- **RBAC, every role on every route:** legal advisor 200 on all GETs and **403 on all writes**,
  including the proof upload; mentor / accountant / business consultant / investor 403 everywhere.
- **Cross-tenant** id on every GET / PATCH / DELETE / proof route → 404.
- **Explicit-null PATCH** → 422 on NOT NULL columns; clears `authority` and `renewal_on`.
- **Proof upload:** wrong content type → 422; 10 MB + 1 byte → 422; replacing a proof deletes the
  old key; upload to another workspace's item → 404.
- **Scheduler:** each `(item, threshold)` fires once; a second tick enqueues nothing; a `done`
  item is skipped by the handler; a deleted item is skipped; a `drafting` asset and a null
  `renewal_on` never appear; no IP `overdue` key is ever produced.
- **Threshold collapse (D21):** an item created five days out yields **one** notification, with
  both `due14` and `due7` claimed, and `due1` still firing four days later; an item created
  already overdue yields **one** `overdue` with all four claimed.
- **`days_out` copy (D22):** an item created three days out reads "due in 3 days", **not** "due in
  14 days"; `0` reads "due today" and `1` reads "due tomorrow".
- **Recipients (D4):** a mentor who is an active member of the workspace receives **nothing** from
  any of the three legal events, and cannot see such a row on any read path.
- **`anchor_day` reset (D24):** an item on the 31st, PATCHed to the 15th, then marked done — the
  successor is on the 15th.
- **Reopen after the successor was deleted:** `done -> reopen -> done` spawns a fresh successor
  once the back-link is null, and the session survives it.
- **Preferences PUT (D23):** a PUT that omits `legal` entirely leaves it ON rather than switching
  it off.
- **Catalog:** every step key unique within its jurisdiction; `US-DE` and `NG` both contain
  `bank_account` and that is fine; `CATALOG_REVIEWED_ON` is `None` and the response says the
  figures are estimates.

## Decisions (all settled with the lead)

| # | Decision |
|---|---|
| D1 | **Two RBAC dependencies.** Reader `(founder, team_member, legal_advisor)` on GETs; writer `(founder, team_member)` on everything else; the other four roles 403 throughout. Commenting is Slice 2. |
| D2 | **`legal_entities` is deferred.** The FE has nothing for it, and the "Entity Fully Formed" banner is computed on the front end from the step list, so nothing consumes it yet. |
| D3 | **Unknown jurisdiction or step key → 404**, not 422. Both name a resource in a code-defined catalog rather than a malformed field, and 404 keeps the catalog non-enumerable. |
| D4 | **"Today" is `datetime.now(UTC).date()`**, in the derived statuses and in both handlers — deliberately not the scheduler's `_local(now)`, which depends on `settings.SCHEDULER_TIMEZONE`. **Recipients are the legal reader set (`LEGAL_ROLES`), not all active members** — the titles name the filing, and the four excluded roles are 403 on every legal route, so delivering to them would leak what the routes withhold (the leak closed elsewhere in #150/#152/#153). Wired through `AUDIENCE`, which drives fan-out and the read filter from one map. |
| D5 | **Catalog costs are integer minor units plus a currency code**, following Finance. Unknown cost is null in both fields, not zero. |
| D6 | **Slice 1 supports Nigeria and Delaware only.** Kenya and Ghana stay out of the supported list rather than shipping the mock's placeholder steps. Figures are transcribed from the prototype, marked as estimates, carry the PRD §1.4 disclaimer, and `CATALOG_REVIEWED_ON = None` until the lead verifies them. |
| D7 | **A progress row's existence is the tick.** Unticking deletes the row; there is no nullable completed flag to disagree with `completed_at`. |
| D8 | **Switching jurisdiction never destroys progress**, because progress is keyed by jurisdiction. The mock's reset-on-switch is a mock shortcut. |
| D9 | **IP stores three statuses — `drafting \| filed \| registered` — and derives `expired`** from `renewal_on < today`. The add drawer cannot produce Expired, so it was never a stored value. Both `status` and `derived_status` are returned. |
| D10 | **The two jurisdiction vocabularies stay separate.** Formation uses a closed set of codes it has content for; IP's is a free string bounded to 1–100 characters trimmed, because it must hold `"Global"`. |
| D11 | **Build-ahead CRUD is labelled provisional** in the FE guide: IP edit and delete, and all of compliance create / edit / delete, have no screen yet. |
| D12 | **Compliance stores `open \| done` and derives `upcoming \| due_soon \| overdue \| done`.** The due-soon window is **14 days**, matching the first reminder threshold so the badge and the notification agree, and matching the mock's "in 9 days → Due soon". Due today is `due_soon`. |
| D13 | **No overdue-count endpoint.** The screen already computes it from the list. |
| D14 | **The FE must send real step keys.** Its positional `"step-1"` ids are not identities and would make a Nigerian tick appear in Delaware. Called out prominently in the FE guide, not buried. |
| D15 | **`authority` is nullable.** There is no create form to confirm it is always supplied, and requiring a field the FE may not send is the wrong risk on a build-ahead shape. |
| D16 | **Recurrence is built in Slice 1** — `none \| monthly \| quarterly \| annually`. Marking done spawns exactly one successor, computed from the stored `anchor_day` with `relativedelta(months=+n, day=anchor_day)` so the series cannot drift. Spawn is idempotent via a unique `previous_item_id` inside a SAVEPOINT. Reopening keeps the successor; deleting removes only that occurrence, after which `done -> reopen -> done` legitimately spawns a fresh one because the back-link is null. Anchor lifecycle in D24. |
| D17 | **Reminder thresholds are windows (`due_on <= today + N`), not exact days.** A missed scheduler day would permanently lose an exact-day reminder; a window plus the once-only `_claim` is self-healing. The documented consequence is that an item created inside a window fires those thresholds together on the first tick. |
| D18 | **No notification for an already-passed IP renewal.** It is already visible as `derived_status: expired` with a "Renew now" action; a notification about something possibly expired for a year is noise. The 14 / 7 / 1-day reminders are the useful part. |
| D19 | **Notification payloads carry `title`, `due_on` and `days_out`**, not ids alone. Loading the row inside the spec function would render an empty title for a row deleted between enqueue and render. |
| D20 | **A new `"legal"` notification category** is added so compliance email volume can be controlled separately. No migration: preferences are JSONB read with a code-side default, so a new category defaults ON. |
| D21 | **Simultaneous thresholds collapse into one notification.** Every qualifying `period_key` is claimed so none can fire again, but only the most urgent is enqueued: `overdue > due1 > due7 > due14`. An item created five days out gets one `due7` and still fires `due1` later; one created already overdue gets one `overdue`. This removes D17's "three notifications at once" cost while keeping the self-healing window. |
| D22 | **`days_out` is computed in the handler, never taken from the `period_key`.** With windows, a `due14` claim can fire for an item due in three days, so the threshold name is not the distance. `(due_on - today).days`, rendered as "due today" / "due tomorrow" / "due in {n} days". |
| D23 | **The `"legal"` category needs a front-end row.** The preferences screen hard-codes five categories in `hooks/useNotificationPreferences.ts` and `components/notifications/notification-preferences.tsx`, so the toggle will not appear on its own — a required FE change, called out in the FE guide. A PUT from today's FE omits `legal`, and the merge only copies recognised keys, so an omitted category keeps its stored value rather than switching off. Asserted by a test on the PUT. |
| D24 | **`anchor_day` is set from `due_on.day` on create and reset whenever a PATCH changes `due_on`.** Moving the series date moves the series: an item on the 31st patched to the 15th then marked done yields a successor on the 15th. The spawn always uses the completed item's own anchor. |

## Out of scope / deferred (each with its reason)

| Deferred | Reason |
|---|---|
| `legal_entities` and the entity summary card | No FE consumes it; the banner is derived on the front end (D2). Revisit when the card is designed. |
| Advisor Requests (13.6) and document versions | Slice 2. An external role with scoped access is its own design. |
| Contract Templates (13.2) and AI Contract Review (13.3) | Slice 3. LLM jobs plus PDF/DOCX parsing. |
| Kenya and Ghana formation content | No real step content exists; the mock gives them generic placeholders. Shipping placeholder legal steps as guidance is worse than not supporting the jurisdiction (D6). |
| Linking proofs into the Documents library (`proof_doc_id`) | No module links to Documents yet. The Finance receipt pattern keeps Slice 1 self-contained. |
| An IP asset auto-creating a linked compliance item | Replaced by the scheduled job scanning `ip_assets.renewal_on` directly — same thresholds, same claim, no cross-entity writes and nothing to keep in sync. |
| Pagination or a span cap on either list | Consistent with the shipped Finance and Calendar lists. Revisit if a workspace's register grows large. |
| A calendar-grid view for compliance | The mock's toggle is an explicit placeholder; the date-range list covers both views. |
| `FormationStep.meta`, the per-step "Docs" button, and all `*Bg` / `dueColor` fields | No backend source. The Docs button has no handler at all. Omitted and labelled not-backed. |
| Re-arming a reminder when a due date moves | The claim is per `(item, threshold)`, so moving a date later does not re-fire. Documented as a known limitation; recurrence keeps it from mattering in the common case. |
| Verifying the formation figures | `CATALOG_REVIEWED_ON = None` and an SOP follow-up line: **formation catalog figures unverified — lead to verify before production**. |
