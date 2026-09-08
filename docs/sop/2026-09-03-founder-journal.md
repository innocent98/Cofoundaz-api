# SOP — Founder Journal (Module 21)

**What shipped** — A private, founder-only journal: seven routes under `/api/v1/journal`
(CRUD on `journal_entries`, a mood/stress trend read, and a prompt of the day). Entry content
is encrypted at rest with Fernet and never stored, logged, or returned in plaintext to anyone
but its author. Access is **founder-only AND author-only**: every query filters on both
`startup_id` and `founder_id == current_user.id`, so a second founder in the same workspace
cannot read the first founder's entries. Two new tables (`journal_entries`, `mood_logs`) in
migration `0013_journal`.

Commits (branch `feature/founder-journal`): see the PR, which targets `develop`. The branch was
rebased onto `develop` and the migration renumbered from `0008_journal` to `0013_journal`,
chained onto `0011_business_canvases`. Note: `0012` is reserved for an open PR - if that lands
first, the down_revision here must be re-pointed at it before merge.

## Why

PRD Module 21 asks for "the honest record of the journey" — a place a founder writes freely,
knowing nobody else will ever read it. That promise is the whole feature. Everything below
follows from taking it literally: content encrypted at rest so a database dump reveals
nothing; no plaintext preview column, because a preview leaks exactly what the encryption
protects; a uniform 404 for both "no such entry" and "not yours", so nobody can probe which
entries exist; and no event emitted to any other module, because the PRD's own service map
marks `journal-service` as isolated.

## How

**Encryption.** `app/services/journal/encryption.py` wraps Fernet. `encrypt_content` and
`decrypt_content` read `JOURNAL_ENCRYPTION_KEY` at call time, mirroring the `MFA_ENCRYPTION_KEY`
seam — the app still boots without the key, and a journal route touched without it raises
`JOURNAL_NOT_CONFIGURED` rather than failing startup for every deployment. A corrupt or
wrong-key token raises `JOURNAL_CONTENT_UNREADABLE`. Neither error carries plaintext or
ciphertext.

**One entry per founder per day.** `uq_journal_entries_startup_founder_date` enforces it.
`POST /journal/entries` is an **upsert**, not a create: it issues a single
`INSERT ... ON CONFLICT DO UPDATE`, so the autosave the PRD describes can fire repeatedly and
two concurrent saves cannot both win a check-then-insert race. `updated_at` is set explicitly
in the `ON CONFLICT` clause, because `TimestampMixin`'s `onupdate` does not fire for a
Core-level upsert. `mood_logs` is written the same way.

**Access.** `require_workspace` + `get_verified_user` gate every route, then
`check_founder_access` requires an active `founder` membership, and every query additionally
filters `founder_id == current_user.id`. Denials raise the shared `Forbidden()` / `NotFound()`
`AppError`s so they render as 403/404 through the global handler.

**Search** decrypts the founder's own rows in application memory and filters there — encrypted
content cannot be searched in SQL.

## What's involved

| Path | Role |
|---|---|
| `alembic/versions/0013_journal.py` | `journal_entries` + `mood_logs`, unique and stress-range constraints |
| `app/db/models/journal.py` | `JournalEntry`, `MoodLog` |
| `app/schemas/journal.py` | Request/response shapes; rejects future dates and blank content |
| `app/services/journal/encryption.py` | Fernet wrapper |
| `app/services/journal/service.py` | Access checks, upsert, list/search, mood series, prompt |
| `app/api/v1/endpoints/journal.py` | The seven routes |
| `app/core/errors.py` | `JOURNAL_NOT_CONFIGURED`, `JOURNAL_CONTENT_UNREADABLE` |
| `app/core/config.py` | `JOURNAL_ENCRYPTION_KEY: str \| None = None` |

## Verification

Unit and API suites (run against Postgres):

- `tests/services/journal/test_encryption.py` — round trip, unicode, corrupt token, wrong key,
  missing/malformed key, non-string input
- `tests/services/journal/test_upsert.py` — **the concurrency regression**: two connections
  saving the same `(startup, founder, date)` simultaneously produce exactly one row and no
  `IntegrityError`. Fails against the old check-then-insert code.
- `tests/services/journal/test_service.py` — the five-point mood scale both ways
- `tests/api/test_journal_access.py` — every non-founder role × every route → 403;
  unauthenticated → 401; unverified founder → 403; a second founder in the same workspace and a
  founder of another workspace → 404; denial bodies contain neither plaintext nor ciphertext;
  and the "not yours" 404 is **byte-identical** to the "no such id" 404
- `tests/api/test_journal_entries.py` — create, read back, encrypted at rest, autosave updates
  rather than duplicates, and the 422s for future dates, blank content, out-of-range stress
- `tests/api/test_journal_list.py` — newest-first ordering, `total` vs page size, paging,
  first-line previews, search hit and miss
- `tests/api/test_journal_edit.py` — partial edits leave other fields alone, edited content is
  re-encrypted, unknown ids 404
- `tests/db/test_journal_models.py` — the database's own rules: one entry per founder per day,
  two founders may each have one, stress outside 1–10 rejected, workspace deletion cascades
- `tests/test_journal_migration.py` — the migration applies and leaves exactly one alembic head
- `e2e/test_journal.py` — the live journey, capturing every body to `e2e/_captures/journal/`

## Operate / roll back

The only operational input is `JOURNAL_ENCRYPTION_KEY` (Fernet format;
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).

**This key is not rotatable in place.** Every entry is encrypted with it; lose it and every
existing entry is permanently unreadable. Back it up somewhere that outlives the host. Unset,
the app still boots and every other module works — only journal routes fail, with
`JOURNAL_NOT_CONFIGURED`.

Roll back with `alembic downgrade` past `0013_journal`, which drops both tables and everything
in them. There is no partial rollback: the data is the feature.

## Follow-ups

Deliberately not in this slice:

- **Per-workspace key derivation** (spec §4, senior checkpoint #1) — v1 uses one global key.
  A leaked key exposes every workspace rather than one. Row filters still isolate tenants;
  this is about blast radius, not access control. **Gated — needs sign-off.**
- **Themed prompt catalog** (`prompts.py`, PRD 21.4) — today's prompt rotates through seven
  strings held inline in the service, with no themes and no version constant. **Gated on copy
  review — senior checkpoint #2.**
- **The supportive low-mood card** (PRD 21.3) — designed in spec §6.2–6.3, not built. **Gated
  on sign-off for the 7-day-logged floor and 30-day suppression window.**
- **`mood_logs`** — the PRD lists this table; the spec's Decision 2 argued against it and said
  mood should be derived from entries. The code has the table. One of the two documents must
  change.
- **Nullable `mood`/`stress`** — the spec describes text-only entries; both columns are
  currently `NOT NULL`.
- Roadmap-event annotations and insight lines on the trend, retrospectives (21.5), notification
  toggle and evening reminder (Module 20), the prompt library browse screen, indexed search,
  key rotation, and workspace-timezone "today" — all deferred with reasons in the spec's
  Deferred table.