# Module 13 Legal & Compliance, Slice 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The "is my company legally in order?" layer — a code-defined per-jurisdiction formation checklist with stored progress, an IP register with derived expiry, and a compliance calendar with derived statuses, optional proof upload, recurrence, and reminders on the scheduler.

**Architecture:** A new `legal` service package + three tables (migration `0067_legal`). The formation catalog lives in code, not the database. Statuses are derived on read, as Calendar's `calendar_status` is. Two new scheduled tasks reuse `_claim` for idempotency, and notifications are scoped through the existing `AUDIENCE` map. Mirrors Module 19 Calendar for the skeleton and Module 12 Finance for the file upload.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 (typed `Mapped`), Postgres, Alembic, Poetry, pytest, `python-dateutil` (already a dependency).

**Spec:** `docs/superpowers/specs/2026-10-17-module-13-legal-compliance-slice1-design.md` (D1–D24, reviewed and approved)

## Global Constraints

- **RBAC (D1):** `LEGAL_ROLES = frozenset({founder, team_member, legal_advisor})` in
  `app/db/models/enums.py` beside `FINANCE_ROLES`. `_legal_reader = require_role(*LEGAL_ROLES)` on
  every GET; `_legal_writer = require_role(MembershipRole.founder, MembershipRole.team_member)` on
  every write. Mentor / accountant / business consultant / investor → **403** everywhere; legal
  advisor → **403 on every write**, including the proof upload. Every role tested on every route.
- **CRUD/tenancy:** services `db.flush()` only; endpoints `db.commit()`; every query scoped by
  `startup_id` from `membership.startup_id`, **never** the body or query; cross-tenant id →
  `NotFound` → **404**, never 403.
- **Endpoint conventions (match `calendar.py`):** `response_model=dict[str, Any]`, return
  `success_response(...)`; deps `payload, membership=Depends(_legal_reader|_legal_writer),
  user=Depends(get_verified_user), db=Depends(get_db)` each with `# noqa: B008`; DELETE returns
  `success_response({"deleted": True})`.
- **Validation** via `_validation(field, message)` in a NEW dependency-free
  `app/services/legal/errors.py` (copy `app/services/finance/errors.py`). Do **not** put it in a
  service module — keeps the legal service package cycle-free.
- **"Today" is `datetime.now(UTC).date()`** in the derived statuses *and* both handlers — never
  the scheduler's `_local(now)` (D4).
- **SAVEPOINT rule:** every insert that can hit a unique constraint goes inside
  `with db.begin_nested()`. Binds on `(startup_id, jurisdiction, step_key)` and on
  `previous_item_id`. Each such test does a **follow-up read in the same test** to prove the
  session survived.
- **Scheduler idempotency is `_claim()` only** — no "already sent" flags. The handler's state
  re-check is the only other gate.
- **`AppError.http_status`** (not `.status_code`); `get_db` does NOT auto-commit.
- **CodeQL (required):** no mutating call inside an `assert` (bind to a variable first); no
  implicit string-concat in a list literal.
- **Migration:** id ≤32 chars (`0067_legal`), `down_revision = "0066_finance_invoice_automation"`.
  The head test asserts **single head + revision present in `alembic history`**, matched on the
  **full** id. Re-check the live head immediately before pushing and renumber if anything merges
  first.
- **No new dependencies. No AI attribution** in any commit message.

## Review Focus

- **Recurrence month-end (D16):** monthly `31 Jan → 28 Feb → 31 Mar`; quarterly
  `30 Nov → 28 Feb → 30 May`; annually `29 Feb → 28 Feb → 29 Feb`. Leap years on both sides. — Task 5.
- **Idempotent spawn:** mark done twice; `done → reopen → done`; and **after the successor is
  deleted**, `done → reopen → done` spawns a fresh one (back-link nulled) — intended. Session
  usable after each. — Task 5.
- **`anchor_day` reset (D24):** item on the 31st, PATCHed to the 15th, then done → successor on
  the **15th**. — Task 5.
- **Derived compliance status (D12):** due today → `due_soon`; due in exactly 14 → `due_soon`; 15
  → `upcoming`; overdue by one day → `overdue`; done *and* overdue → `done`. — Task 4.
- **Derived IP status (D9):** `renewal_on` null → raw status; yesterday → `expired`; **today → not
  expired**. — Task 3.
- **Threshold collapse (D21):** created five days out → **one** notification, `due14` + `due7`
  both claimed, `due1` fires four days later; created already overdue → **one** `overdue`, all
  four claimed. — Task 6.
- **`days_out` copy (D22):** created three days out reads "due in 3 days", **not** "due in 14
  days"; `0` → "due today"; `1` → "due tomorrow". — Task 6.
- **Recipients (D4):** an active **mentor** in the workspace receives **nothing** from all three
  legal events, and cannot see such a row on any read path. — Task 6.
- **Preferences PUT (D23):** a PUT omitting `legal` leaves it **ON**. — Task 6.
- **Formation:** same `step_key` under two jurisdictions = two independent rows; duplicate tick →
  200 and no second row; unknown jurisdiction → 404; a key valid in `NG` sent with
  `jurisdiction=US-DE` → 404. — Task 2.
- **Proof upload:** wrong content type → 422; 10 MB + 1 byte → 422; replacing deletes the old key;
  upload to another workspace's item → 404. — Task 6 (upload is Task 6's sibling, see Task 6a).
- **RBAC, every role on every route**, legal advisor 403 on all writes. — Tasks 2–6.

---

## Task 1: Enums + role set + three models + errors + migration `0067_legal`

**Files:**
- Modify: `app/db/models/enums.py` (`IpAssetType`, `IpAssetStatus`, `ComplianceStatus`,
  `Recurrence`, `LEGAL_ROLES`)
- Modify: `app/db/models/__init__.py`
- Create: `app/db/models/legal.py`
- Create: `app/services/legal/__init__.py` (empty), `app/services/legal/errors.py`
- Create: `alembic/versions/0067_legal.py`
- Test: `tests/db/test_legal_models.py`, `tests/test_legal_migration.py`

**Interfaces (produced):** the four enums; `LEGAL_ROLES`; `LegalFormationProgress`, `IpAsset`,
`ComplianceItem`; `app.services.legal.errors._validation`.

- [ ] **Step 1: Write failing model tests** — `tests/db/test_legal_models.py`, following
  `tests/db/test_calendar_models.py`. Cover:
  - the four enums' values, and `LEGAL_ROLES == {founder, team_member, legal_advisor}`
  - `ComplianceItem` defaults: `status == open`, `recurrence == none`, `done_at`, `proof_url`,
    `proof_key`, `previous_item_id`, `created_by` all null
  - the enum columns store their **values** (`text("SELECT status FROM …")` → `"open"`), as
    `tests/db/test_finance_transaction_model.py` does
  - `IpAsset` with `renewal_on=None` persists; `jurisdiction` holds `"Global"`
  - each of the three tables dies with its startup (query-count, **not** `db.get`, which answers
    from the session identity map)
  - `created_by` / `completed_by` survive their user (`SET NULL`) — assert the column is nullable
    rather than deleting a user; nothing in this repo deletes users
  - the unique constraint on `(startup_id, jurisdiction, step_key)` raises `IntegrityError`
  - the unique constraint on `previous_item_id` raises `IntegrityError`

- [ ] **Step 2: Write the failing migration test** — `tests/test_legal_migration.py`, copying
  `tests/test_calendar_migration.py`:

```python
REVISION = "0067_legal"


def test_migration_chain_single_head_includes_legal():
    # Match the id in FULL: a loose "_legal" substring would also match any future
    # legal-prefixed revision and make this test unfailable.
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic("history")
    assert history.returncode == 0, history.stderr
    assert REVISION in history.stdout
```

  …plus the upgrade → downgrade → upgrade round-trip against
  `0066_finance_invoice_automation`.

- [ ] **Step 3: Watch them fail** on `ModuleNotFoundError: app.db.models.legal`.

- [ ] **Step 4: Add the enums and `LEGAL_ROLES`.** Four `enum.StrEnum`s appended to
  `app/db/models/enums.py`; `LEGAL_ROLES` goes **in the module-access-sets block** beside
  `FINANCE_ROLES` / `MARKETING_ROLES` / `SALES_ROLES`, not at the end of the file, with a comment
  in the same voice explaining that notifications and routes share one definition.

```python
LEGAL_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.founder, MembershipRole.team_member, MembershipRole.legal_advisor}
)
```

- [ ] **Step 5: Create `app/db/models/legal.py`** — three models per spec §1–§3. Points to get
  right:
  - `Enum(..., native_enum=False, length=N, values_callable=lambda e: [m.value for m in e])` on
    all four enum columns
  - `index=True` on every FK; `ondelete="CASCADE"` to `startups`, `ondelete="SET NULL"` to `users`
  - `UniqueConstraint("startup_id", "jurisdiction", "step_key", name="uq_legal_formation_progress_step")`
  - `previous_item_id` is a **self-referential** FK to `compliance_items.id` with
    `ondelete="SET NULL"` and `unique=True`
  - `Index("ix_compliance_items_startup_id_due_on", "startup_id", "due_on")`
  - `anchor_day` is a plain `Integer`, not null (range enforced in the schema, not the DB)

- [ ] **Step 6: Export the three models** in `app/db/models/__init__.py`, alphabetically
  (`from app.db.models.legal import ComplianceItem, IpAsset, LegalFormationProgress  # noqa: F401`).

- [ ] **Step 7: Create `app/services/legal/__init__.py`** (empty) and
  `app/services/legal/errors.py` (copy of the Finance helper).

- [ ] **Step 8: Autogenerate the migration** — `alembic revision --autogenerate -m "legal"`, then
  hand-edit **only** the revision id, `down_revision`, Create Date and docstring. Rename to
  `0067_legal.py`. Confirm the body creates exactly the three tables, their indexes and the two
  unique constraints, and **nothing else** — if it proposes a change to another table, stop: the
  models and the database have drifted.

- [ ] **Step 9: Green** — rebuild the dev DB, `alembic upgrade head`, run both test files, then
  `alembic heads` (one head) and `alembic check` (no drift).

---

## Task 2: Formation — code catalog, progress service, three routes

**Files:**
- Create: `app/services/legal/catalog.py`, `app/services/legal/formation.py`
- Create: `app/schemas/legal.py` (formation models; IP and compliance added in Tasks 3–4)
- Create: `app/api/v1/endpoints/legal.py` (the two deps + three formation routes)
- Modify: `app/api/v1/api.py`
- Test: `tests/services/legal/__init__.py`, `tests/services/legal/test_catalog.py`,
  `tests/services/legal/test_formation.py`, `tests/api/test_legal.py`

**Interfaces (produced):** `JURISDICTIONS`, `STEPS`, `CATALOG_VERSION`, `CATALOG_REVIEWED_ON`,
`steps_for(code)`; `checklist(db, *, startup_id, jurisdiction)`,
`set_step(db, *, startup_id, jurisdiction, step_key, completed, actor_id)`; `_legal_reader`,
`_legal_writer`.

- [ ] **Step 1: Write failing catalog tests** — `tests/services/legal/test_catalog.py`:
  - `JURISDICTIONS` is exactly `{"NG": "Nigeria", "US-DE": "Delaware, USA"}` (D6 — Kenya and
    Ghana are deliberately absent)
  - every `step_key` is unique **within** its jurisdiction, and `bank_account` appearing in both
    is fine
  - every step has `title`, `authority`, `est_days`; `est_cost_minor` and `est_cost_currency` are
    either **both** set or **both** null (never zero-for-unknown, D5)
  - `CATALOG_REVIEWED_ON is None` (D6)
  - `steps_for("KE")` raises / returns nothing that a route would turn into a 200

- [ ] **Step 2: Write failing formation service tests** — `tests/services/legal/test_formation.py`:
  - `checklist` returns catalog order with `completed: false` for a fresh workspace
  - tick → `completed: true` with a `completed_at`; untick → the row is gone
  - re-tick after untick works
  - **the same `step_key` under two jurisdictions is two independent rows** — tick
    `bank_account` under `NG`, and `US-DE`'s `bank_account` stays false
  - **a duplicate tick is a no-op, not an error**: tick twice → still exactly one row, **and a
    follow-up `checklist` read in the same test succeeds** (proves the SAVEPOINT left the session
    usable)
  - unknown jurisdiction → `NotFound`
  - a `step_key` valid in `NG` passed with `jurisdiction="US-DE"` → `NotFound` (D3)
  - another workspace's progress is never visible

- [ ] **Step 3: Write the failing API tests** — `tests/api/test_legal.py`. Copy the `_headers` /
  `_member` helpers from `tests/api/test_calendar.py`. Define **two** role lists:

```python
READER_ROLES = [MembershipRole.founder, MembershipRole.team_member, MembershipRole.legal_advisor]
FORBIDDEN_ROLES = [
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.business_consultant,
    MembershipRole.investor,
]
```

  Cover the three formation routes: the envelope, the catalog shape (separate `authority`,
  `est_days`, `est_cost_minor`, `est_cost_currency` — **not** one `meta` string), the estimates
  marker and disclaimer, 404s, and **parametrised RBAC**: every `READER_ROLES` entry 200 on GETs,
  **`legal_advisor` 403 on the PATCH**, every `FORBIDDEN_ROLES` entry 403 on all three.

- [ ] **Step 4: Watch them fail.**

- [ ] **Step 5: Create `app/services/legal/catalog.py`** — transcribe the figures from the FE mock
  (spec §1 table). Shape:

```python
CATALOG_VERSION = 1
# Transcribed from the FE prototype; NOT verified. The lead verifies before production (D6).
CATALOG_REVIEWED_ON: date | None = None
ESTIMATE_DISCLAIMER = "..."  # PRD §1.4 wording

class FormationStepDef(NamedTuple):
    key: str
    title: str
    authority: str
    est_days: int
    est_cost_minor: int | None
    est_cost_currency: str | None

JURISDICTIONS: dict[str, str] = {"NG": "Nigeria", "US-DE": "Delaware, USA"}
STEPS: dict[str, tuple[FormationStepDef, ...]] = {...}
```

- [ ] **Step 6: Create `app/services/legal/formation.py`** — `checklist` merges catalog order with
  a single query of this workspace's rows for that jurisdiction; `set_step` validates the
  jurisdiction and key against the catalog first (404 before any write), then inserts inside
  `with db.begin_nested()` catching `IntegrityError` as a no-op, or deletes on untick.

- [ ] **Step 7: Create the endpoints file** with both deps and the three formation routes; register
  the router in `app/api/v1/api.py`. The import list is alphabetical and `isort` enforces it:
  `learning` < `legal` < `marketing`, so `legal` sits between those two. Run `ruff` to confirm
  rather than eyeballing it.

- [ ] **Step 8: Green** + `ruff` / `black` / `mypy`.

---

## Task 3: IP assets — CRUD with derived expiry

**Files:**
- Create: `app/services/legal/ip.py`
- Modify: `app/schemas/legal.py`, `app/api/v1/endpoints/legal.py`
- Test: `tests/services/legal/test_ip.py`, add API cases to `tests/api/test_legal.py`

**Interfaces (produced):** `IpAssetCreate`, `IpAssetUpdate`, `IpAssetResponse`;
`create_asset`, `get_asset`, `list_assets`, `update_asset`, `delete_asset`, `serialize_asset`,
`derived_ip_status(status, renewal_on, today)`.

- [ ] **Step 1: Write failing service tests.** `derived_ip_status` is pure — test it directly and
  exhaustively (D9): `renewal_on` null → raw status for each of the three; yesterday → `expired`
  for each; **today → not expired**; tomorrow → raw. Then the CRUD: create with the add-drawer's
  five fields; `jurisdiction` trimmed and bounded 1–100 (D10); `name` 1–200; cross-workspace `get`
  → `NotFound`; `update` with explicit `null` on `renewal_on` **clears** it; explicit `null` on
  `name` → 422; `delete` then `delete` again → `NotFound`; list filters by `type` and `status` and
  never crosses workspaces.

- [ ] **Step 2: Write the failing API cases** — the CRUD round-trip, `renewal_on` as an **ISO
  date** in the response (never `"Renews Feb 2027"`), `status` and `derived_status` both present,
  `type`/`status` lowercase, cross-tenant 404, and the reader/writer RBAC split.

- [ ] **Step 3: Watch them fail.**

- [ ] **Step 4: Schemas** — `IpAssetUpdate` carries the Calendar `model_validator(mode="after")`
  explicit-null rule for `type`, `name`, `jurisdiction` and `status` (NOT NULL columns);
  `renewal_on` is nullable so an explicit null clears it.

- [ ] **Step 5: Service** — `derived_ip_status` first (pure, no DB), then CRUD mirroring
  `app/services/calendar/service.py`. List order: `created_at DESC`.

- [ ] **Step 6: Five routes**, reader on the two GETs, writer on the rest.

- [ ] **Step 7: Green** + gates.

---

## Task 4: Compliance items — CRUD with derived status

**Files:**
- Create: `app/services/legal/compliance.py`
- Modify: `app/schemas/legal.py`, `app/api/v1/endpoints/legal.py`
- Test: `tests/services/legal/test_compliance.py`, add API cases to `tests/api/test_legal.py`

**Interfaces (produced):** `ComplianceCreate`, `ComplianceUpdate`, `ComplianceResponse`;
`create_item`, `get_item`, `list_items`, `update_item`, `delete_item`, `serialize_item`,
`derived_compliance_status(status, due_on, today)`, `DUE_SOON_DAYS = 14`.

- [ ] **Step 1: Write failing service tests.** `derived_compliance_status` is pure — test every
  boundary from Review Focus (D12), **including done-and-overdue → `done`** and
  **due-today → `due_soon`**. Then CRUD: `title` 1–200 required; `authority` **nullable** (D15)
  and 1–120 when given; `due_on` required; `anchor_day` set from `due_on.day` on create (D24);
  explicit `null` on `authority` clears it, on `title`/`due_on`/`recurrence` → 422; range filter
  boundaries inclusive; `status=` filters the **stored** value only; order `due_on ASC`, then
  `created_at ASC`; cross-workspace `get` → `NotFound`.

- [ ] **Step 2: Write the failing API cases** — round-trip, `due_on` as an ISO date (never
  `"in 9 days"`), `status` + `derived_status`, the FE's `filing` ↔ our `title` naming noted in a
  comment, cross-tenant 404, reader/writer RBAC.

- [ ] **Step 3: Watch them fail.**

- [ ] **Step 4–6: Schemas, service, five routes.** `anchor_day` is **never** accepted from the
  client — it is derived from `due_on` on create and reset on any PATCH that changes `due_on`
  (D24). Marking done (`{"status": "done"}`) sets `done_at`; the spawn arrives in Task 5, so for
  now assert only that `done_at` is set and reopening clears it.

- [ ] **Step 7: Green** + gates.

---

## Task 5: Recurrence — pure date maths, then the idempotent spawn

**Files:**
- Create: `app/services/legal/recurrence.py`
- Modify: `app/services/legal/compliance.py`
- Test: `tests/services/legal/test_recurrence.py`, extend `test_compliance.py` and
  `tests/api/test_legal.py`

**Interfaces (produced):** `MONTHS = {monthly: 1, quarterly: 3, annually: 12}`,
`next_due_on(due_on, recurrence, anchor_day)`.

- [ ] **Step 1: Write the failing pure tests first** — `tests/services/legal/test_recurrence.py`.
  This is the trap in the module, so test it before any database is involved:

```python
@pytest.mark.parametrize(
    "start,recurrence,anchor,expected",
    [
        # Anchored, so the series cannot drift: from 31 Jan the next is 28/29 Feb, and the one
        # after that returns to 31 Mar. Computing from the PREVIOUS date would give 28 Mar.
        (date(2026, 1, 31), Recurrence.monthly, 31, date(2026, 2, 28)),
        (date(2026, 2, 28), Recurrence.monthly, 31, date(2026, 3, 31)),
        (date(2028, 1, 31), Recurrence.monthly, 31, date(2028, 2, 29)),  # leap
        (date(2026, 11, 30), Recurrence.quarterly, 30, date(2027, 2, 28)),
        (date(2027, 2, 28), Recurrence.quarterly, 30, date(2027, 5, 30)),
        (date(2028, 2, 29), Recurrence.annually, 29, date(2029, 2, 28)),
        (date(2029, 2, 28), Recurrence.annually, 29, date(2030, 2, 28)),
    ],
)
def test_next_due_on_anchors_and_clamps(start, recurrence, anchor, expected):
    assert next_due_on(start, recurrence, anchor) == expected


def test_none_has_no_next():
    assert next_due_on(date(2026, 1, 31), Recurrence.none, 31) is None
```

  Note the annually case: from `29 Feb 2028` the next is `28 Feb 2029`, and from there the anchor
  pulls it back to `29 Feb` only in the next leap year — assert whichever the implementation
  genuinely produces and **document it**, rather than bending the test.

- [ ] **Step 2: Write the failing spawn tests** in `test_compliance.py`:
  - a `none` item marked done spawns nothing
  - a `monthly` item marked done spawns exactly one successor, with `due_on = next_due_on(...)`,
    `status=open`, `done_at=null`, no proof, the same `title`/`authority`/`recurrence`/`anchor_day`,
    and `previous_item_id` pointing at the predecessor
  - **marking done twice spawns one successor**, and a follow-up read in the same test succeeds
  - **`done → reopen → done` spawns one successor**, and reopening does **not** delete it
  - **after deleting the successor, `done → reopen → done` spawns a fresh one** — the back-link is
    null so the guard has nothing to collide with. Intended (spec §3)
  - deleting a predecessor leaves the successor alive with `previous_item_id is None`
  - **the anchor reset (D24):** create on the 31st, PATCH `due_on` to the 15th, mark done →
    successor on the **15th**

- [ ] **Step 3: Watch them fail.**

- [ ] **Step 4: Create `recurrence.py`** — pure, no `Session` import:

```python
from dateutil.relativedelta import relativedelta

MONTHS: dict[Recurrence, int] = {
    Recurrence.monthly: 1,
    Recurrence.quarterly: 3,
    Recurrence.annually: 12,
}


def next_due_on(due_on: date, recurrence: Recurrence, anchor_day: int) -> date | None:
    """The next occurrence, anchored so a series cannot drift.

    `day=anchor_day` clamps to the month's end, so a 31st series lands on the 28th/29th in
    February and returns to the 31st in March. Computing from the previous occurrence instead
    would walk the series backwards: 31 Jan -> 28 Feb -> 28 Mar.
    """
    months = MONTHS.get(recurrence)
    if months is None:
        return None
    return due_on + relativedelta(months=+months, day=anchor_day)
```

- [ ] **Step 5: Wire the spawn into `update_item`** — only on an `open → done` transition and only
  when `recurrence != none`. The insert goes inside `with db.begin_nested()`, catching
  `IntegrityError` and returning the existing state unchanged (the no-op). Reopen clears
  `status`/`done_at` and touches nothing else.

- [ ] **Step 6: Green** + gates.

---

## Task 6: Proof upload

**Files:**
- Modify: `app/services/legal/compliance.py`, `app/api/v1/endpoints/legal.py`
- Test: extend `test_compliance.py` and `tests/api/test_legal.py`

**Interfaces (produced):** `PROOF_EXT`, `MAX_PROOF_BYTES`, `attach_proof`, `remove_proof`.

- [ ] **Step 1: Write the failing tests** — content type not in `{image/png, image/jpeg,
  application/pdf}` → 422; `MAX_PROOF_BYTES + 1` → 422; a valid upload sets `proof_url` and
  `proof_key`; **replacing deletes the old key** (assert via a fake/spy storage, as the Finance
  receipt tests do); another workspace's item → 404; **legal advisor → 403**; mark-done works with
  **no** proof at all (it is optional per the PRD).

- [ ] **Step 2: Watch them fail.**

- [ ] **Step 3: Copy `attach_receipt`** (`app/services/finance/expenses.py:106`) as `attach_proof`,
  changing only the key prefix to `legal-proofs/{startup_id}/{uuid}{ext}`. Keep the ordering
  exactly: **save the new asset before deleting the old key**, so a failed upload never orphans
  the row.

- [ ] **Step 4: The route** — `async def`, `file: UploadFile = File(...)  # noqa: B008`, read
  `MAX_PROOF_BYTES + 1` bytes so an oversized file is detected without buffering it all, writer
  dep, commit.

- [ ] **Step 5: Green** + gates.

---

## Task 7: Reminders — scheduler selection, collapse, handlers, notifications

**Files:**
- Modify: `app/worker/scheduler.py`, `app/worker/handlers/scheduled.py`,
  `app/services/notifications/audience.py`, `app/services/notifications/registry.py`,
  `app/services/notifications/categories.py`
- Test: `tests/worker/test_legal_scheduler.py` (selection — the house pattern for a module's own
  scheduler, as `test_invoice_overdue_scheduler.py` and `test_sales_stall_scheduler.py` are), plus
  handler cases in `tests/worker/test_scheduled_handlers.py`, plus a preferences case in the
  notifications API tests

**Interfaces (produced):** `SCHED_COMPLIANCE`, `SCHED_IP_RENEWAL`, `_due_compliance`,
`_due_ip_renewals`, `handle_legal_compliance_due`, `handle_legal_ip_renewal_due`, `_due_phrase`.

- [ ] **Step 1: Write the failing selection tests** — `tests/worker/test_legal_scheduler.py`:
  - an item due in 20 days yields nothing
  - due in exactly 14 → one `Due`, `period_key == "due14"`
  - **due in 5 → `due14` and `due7` both claimed, but only `due7` enqueued** (D21)
  - **already overdue → all four claimed, only `overdue` enqueued** (D21)
  - a second tick enqueues **nothing**
  - after a `due7` collapse, `due1` still fires when the item reaches one day out
  - a `done` item never appears
  - a soft-deleted startup never appears
  - IP: `drafting` assets and null `renewal_on` never appear; **no `overdue` key is ever
    produced** (D18)
  - `scheduler_tick` includes both new sources and enqueues once

- [ ] **Step 2: Write the failing handler tests** in `tests/worker/test_scheduled_handlers.py`:
  - a deleted item → the handler returns silently, publishing nothing
  - a now-`done` item → nothing published
  - an asset now `drafting`, or with `renewal_on` cleared → nothing published
  - a live item → publishes the right event with
    `{startup_id, item_id, title, due_on, days_out}`
  - **`days_out` is computed** (D22): an item claimed under `due14` but actually three days out
    publishes `days_out == 3`
  - `_due_phrase`: `0` → "due today", `1` → "due tomorrow", `3` → "due in 3 days"

- [ ] **Step 3: Write the failing recipients tests** — an active **mentor** in the workspace
  receives **nothing** from all three events, while founder, team member and legal advisor each
  do; and the mentor cannot see such a row on any read path (`audience_filter` covers this, so
  assert it rather than assuming). Plus the D23 case: a preferences **PUT omitting `legal` leaves
  it ON**.

- [ ] **Step 4: Watch them fail.**

- [ ] **Step 5: Scheduler.** Two task keys, then selection with the collapse:

```python
_COMPLIANCE_THRESHOLDS = (("overdue", -1), ("due1", 1), ("due7", 7), ("due14", 14))
```

  For each open item, build the list of qualifying `period_key`s (most urgent first), **claim
  every one of them**, and enqueue a job only for the first that was newly claimed. Claiming the
  rest is what stops a collapsed threshold firing again later (D21). Keep the per-item `try` so
  one bad row cannot stop the tick, as `scheduler_tick` already does.

  > **Note for the implementer:** `_claim` is called from `scheduler_tick`, not from the `_due_*`
  > functions. Collapsing therefore needs either a `Due` that carries its extra `period_key`s, or
  > the claim loop moved for these two sources. Pick one, keep `scheduler_tick`'s existing
  > behaviour for the other sources unchanged, and say which in the SOP.

- [ ] **Step 6: Handlers** in `app/worker/handlers/scheduled.py`, following
  `handle_roadmap_overdue`: load, re-check, compute `days_out = (due_on - today).days`, publish.
  Register both with `register_handler`.

- [ ] **Step 7: Notifications.** Three `AUDIENCE` entries → `LEGAL_ROLES`; three `SPECS` entries
  using `_audience(event, minus_actor=False)` and `_due_phrase`; `"legal"` added to `CATEGORIES`
  and the three events mapped in `EVENT_CATEGORY`. **No migration** (D20).

- [ ] **Step 8: Green** + gates.

---

## Task 8: E2E journey + smoke surface

**Files:** create `e2e/test_legal.py`; modify `e2e/test_smoke.py`

- [ ] **Step 1: Add the legal route shapes** to the smoke surface assertion — all fourteen.

- [ ] **Step 2: Write `e2e/test_legal.py`**, modelled on `e2e/test_calendar.py`, covering the
  journey from the brief's §9:
  1. list jurisdictions, read the `NG` checklist, tick two steps, re-read and see progress
  2. add an IP asset with a renewal date
  3. create compliance items across a range (one overdue, one due soon, one upcoming), list them
     and see the derived statuses
  4. mark one done **with** a proof upload
  5. create a `monthly` item, mark it done, and show the successor in the list
  6. a **legal advisor** reads every GET and gets **403** on a write
  7. a cross-tenant id → 404

- [ ] **Step 3: Capture every response** to `e2e/_captures/legal/`, including the failures.

- [ ] **Step 4: Do not run it locally.** The runner needs Poetry on the Windows host. CI proves it
  on the PR, and the lead runs `scripts/e2e_run.sh` on a Docker host and commits the captures
  (agreed arrangement). Verify locally only that the file parses and passes `ruff`/`black`, and
  that every fixture it uses exists with the signature it calls.

---

## Task 9: Docs — FE guide + SOP + checklist

**Files:** create `docs/fe-integration-guide-legal.md`, `docs/sop/<ship-date>-legal-compliance-slice1.md`; modify `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: FE integration guide.** Ships **labelled** with a capture map until the lead
  commits real captures (agreed). It must state, in so many words:
  1. **Every date is an ISO date.** The mock's `"Was due Jul 10"`, `"in 9 days"`,
     `"Renews Feb 2027"`, `"No renewal"`, `"Renew now"` are all display text. `"No renewal"` means
     `renewal_on: null`; `"Renew now"` means `derived_status: "expired"`.
  2. **The FE must send real step keys**, not `"step-1"` (D14). Its positional ids would make a
     Nigerian tick appear under Delaware. **Required FE change** — put it at the top, as Calendar's
     guide did.
  3. **IP stores three statuses**; `expired` is derived (D9).
  4. **Only `NG` and `US-DE` are supported** (D6), and `est_days` / `est_cost_minor` are
     **estimates** carrying the PRD §1.4 disclaimer.
  5. **The two jurisdiction vocabularies are separate** (D10).
  6. **`title` is what the FE calls `filing`.**
  7. **The FE must add a `legal` row to the notification preferences screen** (D23) — required FE
     change, with the two file paths.
  8. **Build-ahead and provisional:** IP edit/delete and all compliance create/edit/delete have no
     screen yet.
  9. **Not backed:** `FormationStep.meta` as one string, the per-step "Docs" button (no handler),
     every `*Bg` / `dueColor`, and the Sync-style absent features.

- [ ] **Step 2: SOP** in the house shape: what shipped, the files, the derived statuses and their
  boundaries, the recurrence anchor rule, the reminder collapse and why, the RBAC sets, roll-back
  (`alembic downgrade 0066_finance_invoice_automation` — **lossy**, destroys all three tables, and
  note that proof **files in storage are not removed** by a downgrade), and a Verification section
  with real numbers. Follow-ups must include the lead's required line: **"formation catalog figures
  unverified — lead to verify before production"**, plus `legal_entities`, recurrence UI,
  Documents-library linking, pagination, and the re-arm-on-date-move limitation.

- [ ] **Step 3: Checklist** — a Module 13 Slice 1 section in the same shape as Module 19's, plus
  the Upcoming tally.

---

## Final verification (before opening the PR)

- [ ] `git fetch origin` then `ls alembic/versions | sort | tail` — **re-check the live head**. If
  anything merged past `0066_finance_invoice_automation`, renumber and update every reference: the
  migration docstring, `REVISION` in `tests/test_legal_migration.py`, its `downgrade` target, the
  SOP and the checklist.
- [ ] Rebase onto current `develop`; resolve `app/db/models/enums.py`,
  `app/db/models/__init__.py`, `app/api/v1/api.py`, `app/worker/scheduler.py`,
  `app/worker/handlers/scheduled.py`, the three notification modules and
  `docs/checklist/PROJECT_CHECKLIST.md` by **keeping both sides** — those are the files every
  module touches.
- [ ] Rebuild the dev DB from zero; `alembic upgrade head`; one head; `alembic check` clean.
- [ ] **All six gates**, exactly as CI runs them: `ruff check app tests e2e`,
  `black --check app tests e2e`, `mypy app`, `isort --check-only app tests`,
  `bandit -r app/ --quiet`, `pylint app --fail-under=9.5`.
- [ ] Full suite green with `REDIS_URL=redis://host.docker.internal:6379/0`; coverage ≥ 95%. If
  one invocation exceeds the tooling's time limit, run it in parts and **say so** in the SOP with
  the per-part counts, as Module 19 did. Check no stray pytest process is running first
  (`docker exec cfz-test ps -eo pid,etime,cmd | grep pytest`) — a leftover run competing for the
  test database produced 494 phantom failures on Module 19.
- [ ] Record the real numbers in the SOP and checklist. Never carry numbers over from a previous
  run.
- [ ] **PR base is `develop`**, not `main`. CI is working again, so open a **normal** PR, not a
  draft, and its checks are real.
- [ ] No `Co-Authored-By` or "Generated with" lines anywhere.

## Self-Review

- **The recurrence anchor is the whole point of Task 5.** Computing from the previous occurrence
  instead of the stored anchor passes a naive test and silently walks a 31st series back to the
  28th forever. The parametrised table in Step 1 is the only thing that catches it, and it is
  written before any database code exists for exactly that reason.
- **The collapse in Task 7 has a structural catch.** `_claim` lives in `scheduler_tick`, not in
  the `_due_*` functions, so claiming several periods while enqueueing one does not fit the
  existing shape. The step says so and names the two options rather than pretending it is a
  one-liner — if the implementer picks the wrong one, the extra claims never happen and the
  collapsed thresholds fire again a day later.
- **`days_out` must not come from the `period_key` (D22).** It is the easy, wrong implementation:
  the names look like distances and the tests would pass for any item that happens to be exactly
  14 days out. The three-days-out test is the one that fails.
- **The `AUDIENCE` map is the right lever, not a bespoke recipients function.** It drives fan-out
  *and* the read filter from one definition, so a mentor can neither be delivered nor shown the
  row. A recipients function alone would leave the read path open.
- **`anchor_day` must never be client-settable.** Accepting it from the body lets a caller
  desynchronise the series from its own `due_on`; it is derived on create and reset on any PATCH
  that moves `due_on` (D24).
- **Three SAVEPOINT sites, not one:** the formation duplicate tick, the recurrence spawn, and
  anything else that touches a unique constraint. Each needs a follow-up read in the same test, or
  the test passes while leaving a poisoned session that only shows up as a later unrelated failure.
- **The migration number will probably move again.** It has already gone `0047 → 0059 → 0067`
  during review. The head test matches the full id and the final-verification step re-checks it.
- **e2e has never run on this machine** and will not. Say so in the SOP rather than implying a
  green run.
