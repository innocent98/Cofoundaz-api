# Founder Journal (Module 21) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the founder's private journal — encrypted daily entries with mood and stress, a themed prompt of the day, a mood/stress trend, and the PRD 21.3 supportive card — behind the strictest access gate in the product: founder-only *and* author-only, hard-enforced server-side.

**Architecture:** A modular-monolith service (`app/services/journal/`) storing one entry per founder per day, claimed with an idempotent `ON CONFLICT (startup_id, founder_id, entry_date) DO UPDATE` upsert so concurrent autosaves cannot duplicate or lose a write. Content is Fernet-encrypted at rest under a **per-workspace key** derived with HKDF-SHA256 from a single master env var — the PRD's "workspace key" with no key-management infra. Endpoints live in one file (`endpoints/journal.py`); every route requires a verified founder *and* filters `founder_id == user.id`, so anything not the caller's own returns a uniform 404. Mood trends and the support card are derived on read; the module emits no events and owns no cross-module reads.

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.0 (typed `Mapped`) · Alembic · PostgreSQL · `cryptography` (Fernet + HKDF) · pytest (real Postgres, per-test rollback).

**Spec:** `docs/superpowers/specs/2026-08-29-founder-journal-design.md`
**PRD:** `Cofoundaz_Technical_PRD.md` → Module 21, lines 699–711 (also 789, 879, 903, 922). *The file is on the Desktop, not the `../` path the repo docs reference.*

## Global Constraints

- **Response envelope:** all success bodies via `success_response(data)` from `app.core.envelope`; errors via `AppError` subclasses / `AppError("VALIDATION_ERROR", msg, 422, field_errors=[…])`.
- **No AI-attribution trailers** in any commit message or PR body (project rule — overrides global CLAUDE.md).
- **Access (the whole point of this module):** every route requires `get_verified_user` **and** `require_role(MembershipRole.founder)` **and** an explicit `founder_id == user.id` filter in the query. There is no reader/editor split. A non-founder gets `403`; another founder's row gets `404`. Never a 403 that confirms a row exists.
- **Tenancy:** every resource resolves to `startup_id == membership.startup_id`; cross-workspace ids raise `NotFound()` (uniform 404 — no enumeration leak).
- **Never log plaintext.** No `logger` call, no exception message, and no error payload may contain decrypted content or a raw ciphertext token. This is a review gate on every task that touches content.
- **Enums:** `native_enum=False, length=20` (match the `Assessment.status` / `RoadmapStatus` precedent).
- **Table prefix:** `journal_` on both tables. **Migration:** `0008_journal`, `down_revision = '0007_roadmap_applied_templates'`.
- **Config version:** `JOURNAL_PROMPT_VERSION = 1`, stamped on the prompt response. `KEY_VERSION = 1`, stamped on `journal_entries.key_version`.
- **Dates:** `Date` columns (calendar), not timestamps. Serialized as `date` in API responses (the PRD's field name); the column is `entry_date`.
- **Events:** none. Do not add any. PRD line 879 marks this service isolated.

---

## File Structure

- `app/core/config.py` — `JOURNAL_ENCRYPTION_KEY: str | None = None` (modify — currently a required `str`).
- `app/core/errors.py` — add `JournalNotConfigured`, `JournalContentUnreadable` (modify).
- `app/services/journal/__init__.py` — package (exists, empty).
- `app/services/journal/encryption.py` — per-workspace key derivation + encrypt/decrypt (modify — currently single-key).
- `app/services/journal/prompts.py` — `JOURNAL_PROMPT_VERSION`, themed `PROMPTS`, `prompt_for_date` (create).
- `app/services/journal/service.py` — `upsert_entry`, `serialize_entry`, `preview_of`, `mood_series`, `support_card_for` , `SUPPORT_CARD_TEXT` (create).
- `app/db/models/enums.py` — add `Mood` (modify).
- `app/db/models/journal.py` — `JournalEntry`, `JournalSupportDismissal` (create).
- `app/db/models/__init__.py` — register the models (modify).
- `alembic/versions/0008_journal.py` — migration (create).
- `app/schemas/journal.py` — `EntryCreate`, `EntryUpdate` (create).
- `app/api/v1/endpoints/journal.py` — 8 routes + `_entry` resolver (create).
- `app/api/v1/api.py` — register the router at `/journal` (modify).
- `tests/factories.py` — add `create_journal_entry`, `create_support_dismissal` (modify).
- `.env.example`, `docker-compose.yml`, `.github/workflows/*` — add `JOURNAL_ENCRYPTION_KEY` (modify).
- `e2e/test_journal.py` — live journey (create).
- `e2e/test_smoke.py` — add the 8 journal paths to the openapi assertion (modify).
- `docs/sop/2026-08-30-founder-journal.md`, `docs/fe-integration-guide-journal.md` — docs (create).
- `docs/checklist/PROJECT_CHECKLIST.md` — tick Module 21 (modify).

---

## ⚠️ Gates before starting

Three tasks are blocked on a senior sign-off recorded in the spec. **Do not start a gated task until its sign-off is noted in the PR description.**

| Task | Gate | What needs approving |
|---|---|---|
| 1 | Spec §4 — senior checkpoint #1 | HKDF-SHA256 derivation, fixed salt `b"cofoundaz.journal.v1"`, `startup_id` as `info`, the `key_version` column, and the two new error codes. |
| 4 | Spec §6 — senior checkpoint #2 | The ~30 prompt strings, reviewed **as copy** (neutral, reflective, no advice framing). |
| 10 | Spec §6.3 | The 7-day-logged floor and the 30-day suppression window. The card's text is the PRD's verbatim and is **not** up for editing. |

Tasks 2, 3, 5–9 and 11 are ungated. Task 5 in particular should be pulled forward if a gate stalls — the privacy matrix is the module's real deliverable.

---

## Task 1: Encryption — per-workspace key, errors, config ⚠️ *gated*

**Files:**
- Modify: `app/services/journal/encryption.py` (rewrite — the branch has a single-key version)
- Modify: `app/core/errors.py`, `app/core/config.py`, `.env.example`, `docker-compose.yml`, `.github/workflows/ci.yml`
- Test: `tests/services/journal/test_encryption.py` (exists, empty)

**Interfaces:**
- Produces: `KEY_VERSION: int = 1`; `encrypt_content(startup_id: uuid.UUID, content: str) -> str`; `decrypt_content(startup_id: uuid.UUID, token: str) -> str`; `JournalNotConfigured` (`JOURNAL_NOT_CONFIGURED`, 500); `JournalContentUnreadable` (`JOURNAL_CONTENT_UNREADABLE`, 500).
- **Breaking:** both functions gain a leading `startup_id` argument. Nothing imports them yet, so no call sites to fix.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/journal/test_encryption.py
import uuid

import pytest

from app.core.config import settings
from app.core.errors import JournalContentUnreadable, JournalNotConfigured
from app.services.journal.encryption import KEY_VERSION, decrypt_content, encrypt_content

W1 = uuid.uuid4()
W2 = uuid.uuid4()


def test_round_trip():
    token = encrypt_content(W1, "shipped the thing today")
    assert token != "shipped the thing today"
    assert decrypt_content(W1, token) == "shipped the thing today"


def test_unicode_and_emoji_survive():
    text = "café ☕ — 今日はよかった 🎉"
    assert decrypt_content(W1, encrypt_content(W1, text)) == text


def test_same_plaintext_differs_across_workspaces():
    assert encrypt_content(W1, "same") != encrypt_content(W2, "same")


def test_workspace_cannot_decrypt_another_workspaces_token():
    token = encrypt_content(W1, "private")
    with pytest.raises(JournalContentUnreadable):
        decrypt_content(W2, token)


def test_derivation_is_stable_across_calls():
    token = encrypt_content(W1, "stable")
    assert decrypt_content(W1, token) == "stable"          # new Fernet instance each call


def test_corrupt_token_raises_unreadable():
    with pytest.raises(JournalContentUnreadable):
        decrypt_content(W1, "not-a-fernet-token")


def test_missing_key_raises_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "JOURNAL_ENCRYPTION_KEY", None)
    with pytest.raises(JournalNotConfigured):
        encrypt_content(W1, "x")


def test_key_version_is_one():
    assert KEY_VERSION == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/services/journal/test_encryption.py -q
```

- [ ] **Step 3: Add the error classes**

```python
# app/core/errors.py  (append near MfaInvalidCode)
class JournalNotConfigured(AppError):  # noqa: N818
    code, http_status = "JOURNAL_NOT_CONFIGURED", 500
    message = "Journal storage is not configured on the server."


class JournalContentUnreadable(AppError):  # noqa: N818
    code, http_status = "JOURNAL_CONTENT_UNREADABLE", 500
    message = "That entry could not be read."
```

Neither message may echo the token or the plaintext.

- [ ] **Step 4: Rewrite the encryption module**

```python
# app/services/journal/encryption.py
import base64
import uuid

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings
from app.core.errors import JournalContentUnreadable, JournalNotConfigured

_SALT = b"cofoundaz.journal.v1"   # domain separation, not a secret
KEY_VERSION = 1


def _fernet(startup_id: uuid.UUID) -> Fernet:
    master = settings.JOURNAL_ENCRYPTION_KEY
    if not master:
        raise JournalNotConfigured()
    derived = HKDF(
        algorithm=SHA256(), length=32, salt=_SALT, info=str(startup_id).encode()
    ).derive(master.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_content(startup_id: uuid.UUID, content: str) -> str:
    """Encrypt journal plaintext under the workspace's derived key."""
    return _fernet(startup_id).encrypt(content.encode("utf-8")).decode("utf-8")


def decrypt_content(startup_id: uuid.UUID, token: str) -> str:
    """Decrypt journal ciphertext. Raises rather than returning a partial read."""
    try:
        return _fernet(startup_id).decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise JournalContentUnreadable() from exc
```

Note `from exc` — the chained cause carries no plaintext, and the handler only ever serializes `exc.message`.

- [ ] **Step 5: Make the config key optional and add it everywhere it is missing**

```python
# app/core/config.py
    JOURNAL_ENCRYPTION_KEY: str | None = None  # 32-byte urlsafe base64 (Fernet), per-workspace HKDF root
```

Then add `JOURNAL_ENCRYPTION_KEY=<Fernet.generate_key() output>` to `.env.example`, to the `api` service env in `docker-compose.yml`, and to the CI workflow env. Generate one with:

```bash
poetry run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
poetry run pytest tests/services/journal/test_encryption.py -q
```

- [ ] **Step 7: Verify the app still boots without the key**

```bash
poetry run python -c "from app.main import app; print(len(app.routes))"
```

Unset `JOURNAL_ENCRYPTION_KEY` in the environment first — this must succeed, proving the config regression is fixed.

- [ ] **Step 8: Commit** — `feat(journal): per-workspace encryption key derivation`

---

## Task 2: `Mood` enum + two models + factories

**Files:**
- Modify: `app/db/models/enums.py`, `app/db/models/__init__.py`, `tests/factories.py`
- Create: `app/db/models/journal.py`
- Test: `tests/models/test_journal_models.py`

**Interfaces:**
- Produces: `Mood(rough|meh|okay|good|great)`; `JournalEntry`, `JournalSupportDismissal`; factories `create_journal_entry(db, startup, founder, *, entry_date=None, content="…", mood=None, stress=None) -> JournalEntry` (encrypts via Task 1) and `create_support_dismissal(db, startup, founder, *, dismissed_at=None) -> JournalSupportDismissal`.

- [ ] **Step 1: Write the failing test** — `tests/models/test_journal_models.py` covering: an entry persists and its `content_encrypted` is not the plaintext; `mood`/`stress` accept `None`; the unique `(startup_id, founder_id, entry_date)` raises `IntegrityError` on a duplicate; `stress = 0` and `stress = 11` each raise `IntegrityError` (the CHECK); `key_version` defaults to `1`; deleting the startup cascades both tables; a dismissal row persists with only a timestamp.

- [ ] **Step 2: Run the test to verify it fails**

- [ ] **Step 3: Add the enum**

```python
# app/db/models/enums.py  (append)
class Mood(str, enum.Enum):
    rough = "rough"
    meh = "meh"
    okay = "okay"
    good = "good"
    great = "great"


MOOD_VALUES: dict[Mood, int] = {
    Mood.rough: 1, Mood.meh: 2, Mood.okay: 3, Mood.good: 4, Mood.great: 5,
}
```

The 1–5 ordinal lives beside the enum so the service and the API never re-derive it.

- [ ] **Step 4: Create the models**

```python
# app/db/models/journal.py
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, SmallInteger, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import Mood


class JournalEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("startup_id", "founder_id", "entry_date", name="uq_journal_entry_day"),
        CheckConstraint("stress IS NULL OR (stress BETWEEN 1 AND 10)", name="ck_journal_stress"),
        Index("ix_journal_entries_owner_date", "startup_id", "founder_id", "entry_date"),
    )

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    founder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    content_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    mood: Mapped[Mood | None] = mapped_column(
        Enum(Mood, native_enum=False, length=20), nullable=True
    )
    stress: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class JournalSupportDismissal(UUIDMixin, Base):
    """PRD 21.3: 'no tracking beyond dismissal.' This table holds nothing else."""

    __tablename__ = "journal_support_dismissals"
    __table_args__ = (
        Index("ix_journal_dismissals_owner", "startup_id", "founder_id", "dismissed_at"),
    )

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    founder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

`JournalSupportDismissal` intentionally omits `TimestampMixin` — `created_at`/`updated_at` would duplicate `dismissed_at` and add state the PRD forbids.

- [ ] **Step 5: Register the models** in `app/db/models/__init__.py` beside the roadmap imports.

- [ ] **Step 6: Add the factories** to `tests/factories.py`, encrypting content through `encrypt_content(startup.id, content)` so every fixture exercises the real crypto path.

- [ ] **Step 7: Run the tests to verify they pass**

- [ ] **Step 8: Commit** — `feat(journal): mood enum, entry + dismissal models, factories`

---

## Task 3: Migration `0008_journal`

**Files:** Create `alembic/versions/0008_journal.py`; Test: `tests/test_journal_migration.py` (mirror `tests/test_assessment_migration.py`).

- [ ] **Step 1: Autogenerate**

```bash
poetry run alembic revision --autogenerate -m "journal"
```

- [ ] **Step 2: Verify the migration content** — rename the file to `0008_journal.py`, set `revision = "0008_journal"` and `down_revision = "0007_roadmap_applied_templates"`. Confirm autogenerate emitted: both tables, the unique constraint, the CHECK, all three indexes, `native_enum=False` for `mood` (a `VARCHAR` + CHECK, **not** a Postgres `ENUM` type), and cascade on all four FKs. Autogenerate frequently drops named CHECK constraints — add it by hand if missing.

- [ ] **Step 3: Run the migration up and down**

```bash
poetry run alembic upgrade head; poetry run alembic downgrade -1; poetry run alembic upgrade head
```

- [ ] **Step 4: Write the migration test** asserting both tables exist with the expected columns and that the chain applies `0001 → 0008` on a fresh DB.

- [ ] **Step 5: Verify the full suite still migrates** — `make test`.

- [ ] **Step 6: Commit** — `feat(journal): migration 0008_journal`

---

## Task 4: Themed prompt catalog + `GET /journal/prompts/today` ⚠️ *gated*

**Files:**
- Create: `app/services/journal/prompts.py`, `app/api/v1/endpoints/journal.py` (first route), `tests/services/journal/test_prompts.py`, `tests/api/test_journal_prompts.py`
- Modify: `app/api/v1/api.py`

**Interfaces:**
- Produces: `JOURNAL_PROMPT_VERSION: int = 1`; `PROMPTS: list[dict]` with `{key, theme, text}`; `prompt_for_date(day: date) -> dict`.
- Route: `GET /api/v1/journal/prompts/today` → `{"data": {"key", "theme", "text", "version", "date"}}`.

- [ ] **Step 1: Write the failing tests** — service: `prompt_for_date` is deterministic for a given date, stable within a day, differs across consecutive days, every catalog entry is reachable across `len(PROMPTS)` consecutive days, all four PRD themes (`decisions`, `energy`, `team`, `wins`) appear, keys are unique, no prompt text is empty. API: a founder gets `200` with all five fields and `version == 1`; a `team_member` gets `403`; a `mentor` gets `403`; an unauthenticated call gets `401`.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Write the catalog** — `app/services/journal/prompts.py`, ~30 prompts balanced across the four themes:

```python
JOURNAL_PROMPT_VERSION = 1

PROMPTS: list[dict[str, str]] = [
    {"key": "decisions.hardest_call", "theme": "decisions",
     "text": "What was the hardest call you made this week, and what made it hard?"},
    {"key": "energy.drained", "theme": "energy",
     "text": "Which part of this week took the most out of you?"},
    {"key": "team.unsaid", "theme": "team",
     "text": "Is there something you have been meaning to say to someone on the team?"},
    {"key": "wins.better_than_expected", "theme": "wins",
     "text": "What went better than you expected recently?"},
    # … ~30 total
]


def prompt_for_date(day: date) -> dict[str, str]:
    return PROMPTS[day.toordinal() % len(PROMPTS)]
```

**Copy rules (spec §6.1), enforced in review:** reflective and business-flavoured; no advice framing; no evaluative or clinical language; no assumption that the week went badly (or well).

- [ ] **Step 4: Create the endpoint file with the founder gate**

```python
# app/api/v1/endpoints/journal.py
router = APIRouter()
_founder = require_role(MembershipRole.founder)


@router.get("/prompts/today")
def prompt_today(
    membership: Membership = Depends(_founder),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    p = prompt_for_date(today)
    return success_response({**p, "version": JOURNAL_PROMPT_VERSION, "date": today.isoformat()})
```

Register in `app/api/v1/api.py`: `api_router.include_router(journal.router, prefix="/journal", tags=["journal"])`.

- [ ] **Step 5: Run to verify they pass**

- [ ] **Step 6: Commit** — `feat(journal): themed prompt catalog + prompts/today`

---

## Task 5: The privacy matrix — founder-only + author-only access tests

> **This is the module's real deliverable** (handoff §10: "write the 403 tests first"). It lands *before* any content route exists, so every route added afterwards is born under a passing gate. Routes not yet implemented are expected to 404-by-routing; the test file is written to assert the gate for each route as it lands, so **Step 4 re-runs this file after every subsequent task**.

**Files:** Create `tests/api/test_journal_access.py`; Modify: `app/api/v1/endpoints/journal.py` (resolver only).

**Interfaces:**
- Produces: `_entry(db, membership, user, entry_id) -> JournalEntry` — the single resolver every content route uses. It filters on **all three** of `id`, `startup_id`, `founder_id` and raises `NotFound()` otherwise.
- Test helper `_founder_member(db)` / `_other_role(db, role)` mirroring `tests/api/test_roadmap_get.py`'s `_member`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_journal_access.py — shape
NON_FOUNDER_ROLES = [
    MembershipRole.team_member, MembershipRole.mentor, MembershipRole.accountant,
    MembershipRole.legal_advisor, MembershipRole.business_consultant, MembershipRole.investor,
]

ROUTES = [  # (method, path_template) — all 8
    ("POST", "/api/v1/journal/entries"),
    ("GET", "/api/v1/journal/entries"),
    ("GET", "/api/v1/journal/entries/{id}"),
    ("PATCH", "/api/v1/journal/entries/{id}"),
    ("DELETE", "/api/v1/journal/entries/{id}"),
    ("GET", "/api/v1/journal/mood"),
    ("POST", "/api/v1/journal/mood/support-card/dismiss"),
    ("GET", "/api/v1/journal/prompts/today"),
]
```

Assertions:
1. **Every role × every route → 403.** Parametrized over `NON_FOUNDER_ROLES × ROUTES`.
2. **Unauthenticated → 401** on every route.
3. **Unverified founder → 403** `EMAIL_NOT_VERIFIED` on every route.
4. **Second founder in the same workspace → 404** on the first founder's entry id (GET/PATCH/DELETE), and an **empty list** from `GET /journal/entries`.
5. **Founder of another workspace → 404** on the first founder's entry id.
6. **No leakage:** the response body of every 403/404 contains neither the plaintext nor the `content_encrypted` token.
7. **Uniform 404:** the body for "another founder's real id" is byte-identical to the body for a random UUID — the proof there is no enumeration oracle.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Add the resolver** (routes come in later tasks)

```python
def _entry(db: Session, membership: Membership, user: User, entry_id: uuid.UUID) -> JournalEntry:
    e = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.id == entry_id,
            JournalEntry.startup_id == membership.startup_id,
            JournalEntry.founder_id == user.id,      # author-only — not just founder-only
        )
        .first()
    )
    if e is None:
        raise NotFound()
    return e
```

- [ ] **Step 4: Re-run this file at the end of every later task.** Tasks 6–10 each add routes; none may weaken this matrix.

- [ ] **Step 5: Commit** — `test(journal): founder-only and author-only access matrix`

---

## Task 6: `POST /journal/entries` upsert + `GET /journal/entries/{id}`

**Files:**
- Create: `app/services/journal/service.py`, `app/schemas/journal.py`, `tests/services/journal/test_upsert.py`, `tests/api/test_journal_entries.py`
- Modify: `app/api/v1/endpoints/journal.py`

**Interfaces:**
- Produces: `upsert_entry(db, *, startup_id, founder_id, entry_date, content, mood, stress) -> tuple[JournalEntry, bool]` (the bool is `created`); `serialize_entry(entry, *, content: str) -> dict`.
- Schemas: `EntryCreate(content: str, mood: Mood | None, stress: int | None, date: date | None)`, `EntryUpdate` (all optional, **no `date`**).
- Routes: `POST /journal/entries` → `201`/`200`; `GET /journal/entries/{id}` → `200`.

- [ ] **Step 1: Write the failing service test** — upsert twice on the same date yields one row with the second content and `created=False` the second time, **including when both calls happen inside a single test transaction** (this is the case that breaks a naive `created_at == updated_at` check); different dates yield two rows; different founders on the same date and workspace yield two rows; content is stored encrypted (the column never equals the plaintext) and `key_version=1`.

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement the upsert**

```python
# app/services/journal/service.py
from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert


def upsert_entry(db, *, startup_id, founder_id, entry_date, content, mood, stress):
    ciphertext = encrypt_content(startup_id, content)
    stmt = (
        pg_insert(JournalEntry)
        .values(
            startup_id=startup_id, founder_id=founder_id, entry_date=entry_date,
            content_encrypted=ciphertext, key_version=KEY_VERSION, mood=mood, stress=stress,
        )
        .on_conflict_do_update(
            constraint="uq_journal_entry_day",
            set_={
                "content_encrypted": ciphertext, "key_version": KEY_VERSION,
                "mood": mood, "stress": stress, "updated_at": func.now(),
            },
        )
        # xmax = 0 is the canonical Postgres tell for "this row was INSERTed, not UPDATEd".
        # Do NOT use `created_at == updated_at` here: both default to now(), which is the
        # *transaction* timestamp, so two upserts inside one transaction — exactly what the
        # per-test rollback harness does — would both report `created=True`.
        .returning(JournalEntry.id, literal_column("xmax = 0"))
    )
    row = db.execute(stmt).one()
    db.flush()
    return db.get(JournalEntry, row[0]), bool(row[1])
```

`updated_at` must be set explicitly in `set_`: `TimestampMixin`'s `onupdate=func.now()` is an ORM-level hook and does **not** fire for a Core `on_conflict_do_update`.

- [ ] **Step 4: Run to verify the service tests pass**

- [ ] **Step 5: Write the failing endpoint tests** — `201` on first write with the full body echoed (decrypted `content`, `date`, `mood`, `stress`); `200` on the second; omitting `date` defaults to today; a future `date` → `422`; empty/whitespace `content` → `422`; `stress = 0` or `11` → `422`; an invalid `mood` string → `422`; `GET /entries/{id}` returns decrypted content; the response **never** contains `content_encrypted`.

- [ ] **Step 6: Write the concurrency test** — two connections upserting the same `(startup, founder, date)` concurrently produce exactly one row and no `IntegrityError`, mirroring `tests/services/test_roadmap_generate.py`'s two-connection race test.

- [ ] **Step 7: Implement the two routes**, then run all of Task 5's access tests again.

- [ ] **Step 8: Commit** — `feat(journal): entry upsert and read`

---

## Task 7: `GET /journal/entries` — list, preview, search, pagination

**Files:** Modify `app/services/journal/service.py`, `app/api/v1/endpoints/journal.py`; Create `tests/api/test_journal_list.py`.

**Interfaces:** `preview_of(content: str, limit: int = 140) -> str` — first non-empty line, truncated on a word boundary with `…`.

- [ ] **Step 1: Write the failing tests** — reverse-chron order by `entry_date`; `from`/`to` filter inclusively; `limit` defaults to 30 and is clamped at 100 (`limit=500` → `422`); `offset` pages without overlap; `preview` is the first non-empty line, ≤140 chars, ending `…` only when truncated; a leading blank line is skipped; `search` is case-insensitive, matches decrypted content, and excludes non-matches; `search` never matches against the ciphertext; the list omits `content` entirely (per PRD 21.2 it carries date, mood, first line); another founder's entries never appear.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement.** Query is always `startup_id` + `founder_id` filtered, date-ranged (default: the last 365 days), ordered `entry_date DESC`, then decrypted and — when `search` is given — filtered in Python **before** pagination is applied, so a page is never short. Document that ordering choice in a comment; it is the one place where the encryption forces a non-SQL path.

- [ ] **Step 4: Run to verify they pass; re-run Task 5's file.**

- [ ] **Step 5: Commit** — `feat(journal): entry list with preview, search, pagination`

---

## Task 8: `PATCH` + `DELETE /journal/entries/{id}`

**Files:** Modify `app/api/v1/endpoints/journal.py`; Create `tests/api/test_journal_edit.py`.

- [ ] **Step 1: Write the failing tests** — PATCH updates `content` alone, `mood` alone, `stress` alone; an omitted field is untouched; `mood: null` clears it (explicit null vs. absent — use `model_fields_set`); sending `date` is rejected `422` (immutable); an empty `content` → `422`; PATCH bumps `updated_at`; PATCH on another founder's id → `404`. DELETE returns `{"deleted": true}`, and a follow-up GET is `404`; DELETE twice → `404` the second time; deleting one entry leaves the others.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement.** Re-encrypt on any `content` change (never patch ciphertext in place). Use `EntryUpdate.model_fields_set` to distinguish "absent" from "explicit null" — the same trap that produced the PATCH-null 500 caught during Roadmap review.

- [ ] **Step 4: Run to verify they pass; re-run Task 5's file.**

- [ ] **Step 5: Commit** — `feat(journal): entry autosave edit and delete`

---

## Task 9: `GET /journal/mood` — series + summary

**Files:** Modify `app/services/journal/service.py`, `app/api/v1/endpoints/journal.py`; Create `tests/services/journal/test_mood_series.py`, `tests/api/test_journal_mood.py`.

**Interfaces:** `mood_series(db, *, startup_id, founder_id, range_key) -> dict` returning `{range, from, to, points, summary}` per spec §5.4.

- [ ] **Step 1: Write the failing tests** — each `range` (`7d`/`30d`/`90d`/`365d`) computes the right window; the default is `30d`; a bad `range` → `422`; days with no entry are **omitted, not zero-filled**; an entry with `mood=None` is omitted from `points` but still counted in `summary.entries`; `mood_value` maps `rough`→1 … `great`→5; `average_mood`/`average_stress` are rounded to one decimal and are `None` when nothing is logged; points are ascending by date (a chart series, unlike the reverse-chron list); another founder's entries never appear.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement.** `points` selects only `entry_date`, `mood`, `stress` — **this query must never decrypt content.** Add a comment saying so; it is the cheapest guard against a later refactor pulling content into the trend path.

- [ ] **Step 4: Run to verify they pass; re-run Task 5's file.**

- [ ] **Step 5: Commit** — `feat(journal): mood and stress trend series`

---

## Task 10: The supportive card + dismissal ⚠️ *gated*

**Files:** Modify `app/services/journal/service.py`, `app/api/v1/endpoints/journal.py`; Create `tests/services/journal/test_support_card.py`, `tests/api/test_journal_support_card.py`.

**Interfaces:** `SUPPORT_CARD_TEXT: str` (a constant, never composed); `support_card_for(db, *, startup_id, founder_id, today) -> dict | None`.

- [ ] **Step 1: Write the failing tests — the rule table**

| Scenario | Expected |
|---|---|
| 14 days, 10 logged, all `rough`/`meh` | card shown |
| 14 days, 6 logged, all `rough` | **hidden** (7-day floor) |
| 14 days, 10 logged, one `good` among them | hidden |
| 14 days, 10 logged all low, dismissed 5 days ago | hidden |
| same, dismissed 31 days ago | shown |
| no entries at all | hidden |
| low mood only *outside* the 14-day window | hidden |
| another founder's dismissal exists, this founder's does not | shown (dismissals are per-founder) |

Plus: the served `text` is byte-identical to `SUPPORT_CARD_TEXT`; the payload is `{"key": "support.sustained_low", "text": …, "dismissible": true}`; `POST …/dismiss` writes exactly one row whose only non-key data is `dismissed_at`; a second dismiss inside the window is accepted (`200`) and does not change visibility; dismissing never touches an entry.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

```python
# PRD 21.3 — verbatim. Do not template, interpolate, or reword.
SUPPORT_CARD_TEXT = (
    "It looks like it's been heavy lately. Building is hard — talking to someone "
    "you trust, or a professional, can genuinely help."
)

_WINDOW_DAYS = 14
_MIN_LOGGED_DAYS = 7      # spec §6.3 — prevents one bad day in an empty fortnight triggering
_SUPPRESS_DAYS = 30       # PRD: "never repeated more than monthly"
```

The rule: over the trailing 14 days, at least 7 days logged **and** every logged day `mood_value ≤ 2` **and** no dismissal within 30 days. Evaluated only inside `GET /journal/mood`, only for the authoring founder.

- [ ] **Step 4: Wire `support_card` into the mood response** (it is `null` in every other case) and add the dismiss route.

- [ ] **Step 5: Run to verify they pass; re-run Task 5's file** — the dismiss route is in the 8-route matrix.

- [ ] **Step 6: Commit** — `feat(journal): sustained-low-mood support card and dismissal`

---

## Task 11: Live E2E, smoke, and documentation

**Files:** Create `e2e/test_journal.py`, `docs/sop/2026-08-30-founder-journal.md`, `docs/fe-integration-guide-journal.md`; Modify `e2e/test_smoke.py`, `docs/checklist/PROJECT_CHECKLIST.md`, `docs/handoff/module-21-founder-journal.md` (mark it shipped).

- [ ] **Step 1: Add the 8 journal paths to the smoke surface assertion** in `e2e/test_smoke.py` and run `make e2e` to confirm every route answers its expected status through the live ASGI stack.

- [ ] **Step 2: Write the live journey** — `e2e/test_journal.py`: a founder signs up, verifies, onboards → writes today's entry with mood and stress → reads it back **decrypted** → autosaves an edit → lists entries → searches and matches → fetches the `30d` trend → fetches today's prompt → **a `team_member` on the same workspace is asserted `403` on all 8 routes** → a second workspace's founder gets `404`. Capture every response to `e2e/_captures/`.

- [ ] **Step 3: Run the four verification layers**

```bash
make test && make lint && make e2e
```

All must be green. Coverage must not regress below the existing gate.

- [ ] **Step 4: Write the FE integration guide** — `docs/fe-integration-guide-journal.md`, every payload **pasted from `e2e/_captures/`**, never written from the schema. Include: both upsert responses (201 and 200), the list with previews, a single decrypted read, the mood series with `support_card: null` **and** a populated example, the prompt, every error shape (403 non-founder, 404 cross-founder, 422 validation), and a note that the uniform 404 is deliberate. **All journal text in the guide must be obviously fake** (handoff §10) — no real reflection, and nothing that reads like a real person's diary.
- Document the §0 gap: the UI comps were unavailable, so these captures *are* the contract.

- [ ] **Step 5: Write the SOP** — `docs/sop/2026-08-30-founder-journal.md`: what shipped, why the design decisions went the way they did (one entry/day, mood derived, per-workspace HKDF key), the verification evidence, the rollback (drop `0008_journal`), and the follow-ups from the spec's Deferred table.

- [ ] **Step 6: Update the checklist** — add a Module 21 section to `docs/checklist/PROJECT_CHECKLIST.md`, tick it, update the Snapshot tally (4 complete → 5) and the endpoint/test counts.

- [ ] **Step 7: Commit** — `docs(journal): SOP, FE guide, checklist; test(journal): live e2e`

---

## Definition of done (spec §8, handoff §9)

- [ ] Every endpoint and edge case covered by unit/integration tests on real Postgres, TDD.
- [ ] `make test` green on a freshly migrated DB (`0001 → 0008`).
- [ ] The 8 journal routes present in the smoke surface assertion.
- [ ] `make e2e` green with captures written.
- [ ] **A non-founder member gets 403 on every journal route**, and a second founder gets 404 on the first founder's entries — proven by Task 5 and re-proven live in Task 11.
- [ ] No plaintext or ciphertext in any log line or error payload.
- [ ] `black --check` · `isort --check` · `ruff check` · `mypy` all clean.
- [ ] SOP + FE guide + checklist updated; no AI-attribution trailers in any commit or the PR body.
- [ ] Both senior sign-offs (spec §4, §6.3) recorded in the PR description.
