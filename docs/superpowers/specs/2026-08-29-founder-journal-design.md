# Design — Module 21 Founder Journal

> **Status:** Draft (brainstorm) · **Date:** 2026-08-29 · **Revised:** 2026-08-30 — reconciled
> against the PRD (see §0) · **Repo:** `cofoundaz-api`
>
> **Sources of truth, in the order the kickoff brief §2 gives them:**
> 1. **Technical PRD** — `Cofoundaz_Technical_PRD.md`, **Module 21 (lines 699–711)**, plus the RBAC
>    matrix (line 903), the service map (line 879), the impersonation rule (line 789), and the AI
>    safety rule (line 922). *(The file is on the **Desktop**, not at the `../` path every repo doc
>    references — that path does not exist on this machine.)*
> 2. UI handoff comps `../# Cofoundaz Web App UI Build/` — **absent**, see §0.
> 3. Frontend repo `../cofoundaz/` (`content/types.ts`) — **absent**, see §0.
> 4. The handoff brief `docs/handoff/module-21-founder-journal.md` — an orientation doc, *not* the
>    spec, by its own header.
>
> Pattern exemplar: the shipped Roadmap
> (`docs/superpowers/specs/2026-08-21-roadmap-core-design.md`).
>
> **⚠️ Two senior checkpoints** (handoff §7): the **encryption approach** (§4) and the
> **mental-health-adjacent surface** (§6). Both are resolved below and marked for sign-off.
> Module 21 is a single slice — one spec → one plan → one PR.

---

## 0. Provenance & known gaps

This spec was first drafted from the handoff brief alone, then **reconciled against PRD Module 21**.
What the PRD confirmed, and what it changed, is recorded here so a reviewer can see the difference.

**Confirmed by the PRD** (no change): founder-only, hard-enforced server-side, with SA impersonation
returning 403 on journal routes (lines 701, 789); content "encrypted at rest **with workspace key**"
(line 701) — the per-workspace derivation in §4 is the literal requirement, not an embellishment;
the 5-point mood scale **Rough / Meh / Okay / Good / Great** (line 703) matching the enum exactly;
stress **1–10** (line 703); the three API groups (line 709); `founder_id` as a real entity column
(line 710); `journal-service | encrypted entries, mood | — (isolated)` (line 879) confirming the
module emits and consumes nothing.

**Changed by the PRD** (this revision): the low-mood card's copy and rule exist verbatim in the PRD,
so it is **in scope** (§6) rather than deferred; prompts are a **library by theme** (§5.5); mood
trends carry **roadmap-event annotations** and **insight lines**, both now explicitly deferred with
reasons (§1); and PRD line 922 — *journal content is never retrievable by any agent* — is recorded
as a standing constraint (§6.4).

**Known gap.** The UI handoff comps and the frontend repo are not available on this machine, so
kickoff brief §7 ("check `content/types.ts` before finalizing schemas") could not be honoured.
Payload shapes here are derived from the PRD and from the conventions of the shipped modules.
**Decision:** proceed; the live captures in `e2e/_captures/` become the contract the FE guide
documents, exactly as the shipped roadmap and health-score guides were built. If the comps surface
before the PR, re-check the field names against them.

## 1. Scope

**In scope** — one private journal per founder: `journal_entries` CRUD (date, content encrypted at
rest, mood, stress); a mood/stress **trend read** over a date range derived from those entries; a
themed **prompt of the day** from a static versioned catalog; and the PRD 21.3 **supportive
low-mood card** with its dismissal state. Founder-only, hard-enforced server-side, scoped to the
*authoring* founder.

**Deferred:**

| Deferred | To | Why |
|---|---|---|
| A separate `mood_logs` table (PRD line 710 lists one) | later, if standalone mood logging is wanted | Decision 2 — the PRD's `mood_logs(date,mood,stress)` carries no `startup_id`/`founder_id`/`id` and is fully covered by the entry's own columns at one entry per day. A second table means a second write path and a sync rule for no gain in v1. |
| **Roadmap-event annotations** on the trend ("Launch week") — PRD 21.3 | follow-up, after this module ships | Needs a cross-module read into `roadmap_milestones`. Deliberately kept out of the first slice so the journal stays the isolated service the PRD's own service map (line 879) describes. |
| **Insight lines** ("Stress tends to spike around compliance deadlines") — PRD 21.3 | Module 03 (AI Co-Founder), constrained by §6.4 | These are generated prose about a founder's mood. They need the AI layer *and* a copy review, and PRD line 922 forbids any agent retrieving journal content — so the mechanism itself needs design, not just a prompt. |
| **Retrospectives** (PRD 21.5) — milestone-linked retro template, auto-offered on completion | Modules 03 + 05 | Needs a Roadmap event hook and a template model; a slice of its own. |
| **"Daily prompt notifications"** toggle (21.4) + the evening reflection notification (line 711) | Module 20 | Delivery and preferences live there. This module emits nothing (§7.1). |
| Prompt **library browse** screen `/app/journal/prompts` (21.4) | follow-up | The PRD's API list (line 709) exposes only `prompts/today`; the catalog is built themed now so a browse route is additive. |
| Rich-text document model (PRD says `RichTextEditor`) | — | Content is stored as markdown/plain text; the FE owns rendering. |
| Server-side **search over content** at scale (index / `pg_trgm`) | follow-up | Content is encrypted, so SQL cannot see it; v1 decrypts the founder's own rows and filters in Python (§5.3). |
| Encryption **key rotation** (re-wrap on a new key) | follow-up | One master key, one derivation version in v1; a `key_version` column is written now so rotation is additive. |
| **"Month jump"** as a dedicated endpoint (21.2) | — | Covered by the existing `from`/`to` query params; no extra route needed. |
| Export / PDF, certificates | — | Not this module. |
| Workspace-timezone "today" (v1 uses the server date, UTC) | follow-up | The same simplification the Roadmap made for generation base dates. |

## 2. Decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Entry cardinality | **One entry per `(startup_id, founder_id, entry_date)`** — DB unique constraint. `POST /journal/entries` **upserts** that day's entry. Matches the PRD's single "Today's Entry" surface with autosave (21.1), makes the trend a clean one-point-per-day series, and makes autosave unambiguous. |
| 2 | Mood storage | **Two tables.** `journal_entries` carries `mood`/`stress`, and a `mood_logs` row is upserted alongside each entry — matching the PRD's `mood_logs(date,mood,stress)` entity (line 710). `GET /journal/mood` reads `mood_logs`. **Revised 2026-09-06:** this spec originally rejected a second table on the grounds that it meant a second write path and a sync rule. The shipped code kept it and review accepted it: both writes happen inside the same `create_entry` call, each as a single `ON CONFLICT DO UPDATE`, so there is one write path in practice and the PRD's entity list is honoured. Deriving the trend from `journal_entries` alone remains a valid future simplification. |
| 3 | Encryption | **Single global key**, wrapped in Fernet, read from `JOURNAL_ENCRYPTION_KEY`. **Revised 2026-09-06 — waiver granted in review:** this spec originally mandated a per-workspace HKDF-SHA256 derived key (§4, senior checkpoint #1). v1 ships one global key; the derivation and `key_version` are deferred to a follow-up. Accepted impact: a leaked key exposes every workspace rather than one. Row-level filters still isolate tenants, so this is blast radius, not access control. |
| 4 | Config shape | `JOURNAL_ENCRYPTION_KEY: str \| None = None` — **mirrors `MFA_ENCRYPTION_KEY`**. Unset + a journal route touched → `JOURNAL_NOT_CONFIGURED` (500), exactly like `MfaNotConfigured`. The app still boots without it. *(Corrects the current branch state, where the key is required and breaks boot anywhere `.env` lacks it.)* |
| 5 | Access model | **Founder-only AND author-only.** `require_role(MembershipRole.founder)` + `get_verified_user`, and every query additionally filters `founder_id == current_user.id`. The PRD's RBAC row is `journal \| ✓ only \| — \| …` (line 903) — no other role has any cell. A workspace with two founders gives each a private journal. |
| 6 | Prompts | **Static versioned catalog** `app/services/journal/prompts.py` (`JOURNAL_PROMPT_VERSION = 1`), **themed** Decisions / Energy / Team / Wins per PRD 21.4, deterministic rotation by date ordinal. Same shape as `roadmap/templates.py`. |
| 7 | Events | **None.** PRD line 879 marks `journal-service` as isolated; the journal notifications (line 711) are Module 20. No event is invented here. |
| 8 | Low-mood card | **In scope**, built to the PRD's verbatim copy and rule (§6). |
| 9 | Column naming | The PRD entity says `date`; this spec uses **`entry_date`** (as the handoff brief does) — `date` collides with the SQL type name and reads badly in queries. A naming choice only; the API field is `date` in responses, matching the PRD. |

## 3. Data model (migration `0008_journal`)

Two tables. One new enum `Mood(rough|meh|okay|good|great)` in `app/db/models/enums.py`, matching the
PRD's Rough / Meh / Okay / Good / Great exactly.

### `journal_entries`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `startup_id` | UUID FK→startups | `ondelete=CASCADE`, indexed |
| `founder_id` | UUID FK→users | `ondelete=CASCADE`, indexed — the author; every read filters on it |
| `entry_date` | Date | a calendar date, not a timestamp (serialized as `date`) |
| `content_encrypted` | Text | Fernet token (§4); **never** logged, never returned except to its author |
| `key_version` | SmallInt | default `1` — makes rotation additive later |
| `mood` | `Mood` (Enum, `native_enum=False`, len 10) | **nullable** — an entry may be text-only |
| `stress` | SmallInt | **nullable**, `CHECK (stress BETWEEN 1 AND 10)` |
| (TimestampMixin) | | `created_at` / `updated_at` |

**Constraints & indexes**
- `UNIQUE (startup_id, founder_id, entry_date)` — enforces Decision 1 and backs the upsert claim.
- Index `(startup_id, founder_id, entry_date DESC)` — serves the reverse-chron list and the range read.
- `CHECK (stress IS NULL OR stress BETWEEN 1 AND 10)`.

**Notes.** No `title` and no `preview` column: the list's "first line" (PRD 21.2) is derived after
decryption (§5.3) — storing a plaintext preview would leak exactly what the encryption protects.
Content is `NOT NULL`; an empty entry is a delete, not a blank row.

### `journal_support_dismissals` — the low-mood card's only state

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | `ondelete=CASCADE` |
| `founder_id` | UUID FK→users | `ondelete=CASCADE` |
| `dismissed_at` | timestamptz | `server_default=now()` |

Index `(startup_id, founder_id, dismissed_at DESC)`.

**This table holds nothing else, deliberately.** PRD 21.3: *"no tracking beyond dismissal."* No mood
snapshot, no trigger reason, no shown-count, no reading time. A row means "this founder dismissed
the card at this moment", and that is the entire record.

## 4. Encryption at rest — ⚠️ senior checkpoint #1

> **Superseded for v1 (2026-09-06).** Review granted a waiver: the shipped code uses a single
> global `JOURNAL_ENCRYPTION_KEY`, with no HKDF derivation and no `key_version` column. The
> design below is the agreed follow-up, not what is on the branch. See Decision 3.

**Requirement (PRD line 701):** content "encrypted at rest with **workspace key**."
**Constraint (handoff §7):** reuse the existing pattern, do not invent crypto.

**Approach.** Keep Fernet (as MFA does). Instead of one global key, derive a **per-workspace** key
from the master with HKDF-SHA256 — a standard KDF from the same `cryptography` package already in
the dependency set:

```python
# app/services/journal/encryption.py
_SALT = b"cofoundaz.journal.v1"          # domain separation, not a secret
KEY_VERSION = 1

def _workspace_key(startup_id: uuid.UUID) -> bytes:
    master = settings.JOURNAL_ENCRYPTION_KEY
    if not master:
        raise JournalNotConfigured()
    derived = HKDF(algorithm=SHA256(), length=32, salt=_SALT,
                   info=str(startup_id).encode()).derive(master.encode())
    return base64.urlsafe_b64encode(derived)          # Fernet key format

def encrypt_content(startup_id: uuid.UUID, content: str) -> str: ...
def decrypt_content(startup_id: uuid.UUID, token: str) -> str: ...
```

**Why this over one global key:** it is what the PRD asks for, and a leaked derived key exposes one
workspace rather than every founder's journal. The cost is one HKDF per call (microseconds) and no
new infrastructure — the master key is still a single env var, handled exactly like
`MFA_ENCRYPTION_KEY`.

**Rejected:** a per-workspace key *column* (needs a KEK, a rotation story, and a secrets story we
don't have yet) and application-transparent DB encryption (doesn't satisfy "only the founder reads
it").

**Errors.** `JournalNotConfigured` → `JOURNAL_NOT_CONFIGURED` (500), mirroring `MfaNotConfigured`.
A token that will not decrypt → `JOURNAL_CONTENT_UNREADABLE` (500) — never a silent empty string,
and the raw token is never echoed.

**Current branch state.** `app/services/journal/encryption.py` already exists in the single-key form
and `tests/services/journal/test_encryption.py` is empty. Task 1 of the plan rewrites the module to
the derived form **test-first** and makes the config key optional.

> **Sign-off needed before Task 1 lands:** the derivation (HKDF-SHA256, fixed salt, `startup_id` as
> `info`), the `key_version` column, and the two new error codes.

## 5. Service & endpoints

All under `/api/v1/journal`. **Every route: verified user + `require_role(founder)` + author-scoped.**
`_founder = require_role(MembershipRole.founder)`; every query filters
`startup_id == membership.startup_id AND founder_id == user.id`. Anything not the caller's own →
**uniform 404** (no enumeration leak), never a 403 that confirms the row exists.

| # | Route | Behaviour | PRD |
|---|---|---|---|
| 1 | `POST /journal/entries` | Upsert `(startup_id, founder_id, entry_date)`. Body: `content`, `mood?`, `stress?`, `date?` (default = server today). `201` on create, `200` on update. | 21.1 |
| 2 | `GET /journal/entries` | Reverse-chron list; `?search=` · `?from=` · `?to=` · `?limit=` (default 30, max 100) · `?offset=`. Returns `date`, `mood`, `stress`, `preview` (first line). | 21.2 |
| 3 | `GET /journal/entries/{id}` | One entry, **decrypted**, full content. | 21.1 |
| 4 | `PATCH /journal/entries/{id}` | Autosave — partial `content` / `mood` / `stress`. `date` is immutable. | 21.1 |
| 5 | `DELETE /journal/entries/{id}` | `success_response({"deleted": True})`, matching the roadmap convention. | 21.2 |
| 6 | `GET /journal/mood?range=30d` | Mood/stress series + summary + `support_card` (§5.4, §6). `range` ∈ `7d\|30d\|90d\|365d`, default `30d`. | 21.3 |
| 7 | `POST /journal/mood/support-card/dismiss` | Records one dismissal row. Idempotent-ish: a second call within the window is accepted and simply extends nothing. | 21.3 |
| 8 | `GET /journal/prompts/today` | Today's themed prompt from the catalog. | 21.4 |

### 5.1 Upsert (route 1)

`INSERT … ON CONFLICT (startup_id, founder_id, entry_date) DO UPDATE` — the same create-once claim
shape the roadmap uses, so two concurrent autosaves cannot produce a duplicate or a lost row. The
handler reports `201` vs `200` from whether the row's `created_at == updated_at` after the write.

### 5.2 Autosave (route 4)

PRD 21.1 specifies autosave on the writing surface. Route 1 (upsert by date) is the autosave path
for *today*; route 4 exists for editing an entry by id, including a past one. `date` is immutable on
PATCH — moving an entry between days would collide with the unique constraint and silently rewrite
history.

### 5.3 List, preview and `search`

The list decrypts the founder's own rows and derives `preview` = the first non-empty line, truncated
to 140 chars on a word boundary with `…` (PRD 21.2: "date, mood emoji, first line" — the emoji is
the FE's rendering of `mood`). `?search=` filters **in Python** over those decrypted rows
(case-insensitive substring) — SQL cannot see ciphertext, and PRD 21.2 scopes search to "own entries
only" anyway. Bounded by construction: one entry per day per founder, always date-ranged and
paginated; the default corpus is the founder's last 365 days. Listed in §1 as a follow-up if it ever
needs a real index.

### 5.4 Mood series (route 6)

```json
{ "data": {
  "range": "30d", "from": "2026-07-31", "to": "2026-08-30",
  "points": [ {"date": "2026-08-27", "mood": "okay", "mood_value": 3, "stress": 6} ],
  "summary": {"entries": 12, "days_logged": 12, "average_mood": 3.4, "average_stress": 5.8},
  "support_card": null
}}
```

Days with no entry, and entries with no mood, are **omitted** — not zero-filled and not
interpolated. `mood_value` is the 1–5 ordinal (`rough`=1 … `great`=5) so the FE can plot without
owning the mapping. `summary` is **descriptive only** — counts and averages, no labels, no
trajectory verdict, no advice. `support_card` is `null` or the object in §6.2.

### 5.5 Prompt catalog (route 8)

`app/services/journal/prompts.py`, `JOURNAL_PROMPT_VERSION = 1`. Prompts are **themed** per PRD 21.4
— `decisions` · `energy` · `team` · `wins` — carried as a `theme` field so the deferred library
screen is additive:

```python
JOURNAL_PROMPT_VERSION = 1
PROMPTS = [
    {"key": "decisions.hardest_call", "theme": "decisions",
     "text": "What was the hardest call you made this week, and what made it hard?"},
    {"key": "wins.small_win", "theme": "wins",
     "text": "What went better than you expected recently?"},
    # … ~30 total, balanced across the four themes
]
```

Selection is deterministic and stable for a whole day: `PROMPTS[date.toordinal() % len(PROMPTS)]`.
The response carries `{key, theme, text, version, date}` so a captured FE payload stays reproducible.
The PRD's *context-aware* variant ("You shipped {milestone}…") is deferred to Module 03 (§1).

## 6. Mental-health-adjacent surface — ⚠️ senior checkpoint #2

The PRD is unusually prescriptive here, and this section follows it literally rather than
paraphrasing.

### 6.1 What the trend may say

Mood and stress are the founder's **own recorded numbers**, played back as a series plus counts and
averages. PRD 21.3: *"Never diagnostic; no advice framing beyond patterns; no health claims."*
Therefore no payload and no prompt contains evaluative language — no "declining", "concerning",
"burnout risk", "you seem…". The API returns numbers and dates; the FE owns neutral presentation.
Generated insight lines are deferred (§1).

### 6.2 The supportive card — verbatim copy

Copy is stored as a **single constant, never composed or templated**, exactly as the PRD writes it:

```python
SUPPORT_CARD_TEXT = (
    "It looks like it's been heavy lately. Building is hard — talking to someone "
    "you trust, or a professional, can genuinely help."
)
```

Served inside `GET /journal/mood` as
`{"key": "support.sustained_low", "text": SUPPORT_CARD_TEXT, "dismissible": true}`, or `null`.

### 6.3 The trigger rule

PRD 21.3 writes the rule as *"rule-based, e.g. mood ≤2 for 14+ days"* — an example, so the exact
predicate is a decision this spec makes explicit:

> Over the trailing **14 days**, the card shows when **at least 7 days are logged** and **every
> logged day has `mood_value ≤ 2`** (`rough` or `meh`) — **and** no dismissal row exists for this
> founder in the last **30 days** (PRD: *"never repeated more than monthly"*).

The 7-day floor is deliberate: without it a single logged `rough` day in an otherwise empty
fortnight would trigger the card, which is both wrong and intrusive. Evaluated only inside
`GET /journal/mood`, for the authoring founder, never proactively.

> **Sign-off needed:** the 7-day floor and the 30-day suppression window, since the PRD's rule is
> written as an example. The copy itself is the PRD's and is not up for editing here.

### 6.4 Standing constraints

- **PRD line 922:** *journal content is never retrievable by any agent.* No AI code path may read
  `content_encrypted` or its plaintext — recorded here so it binds Module 03 before it is built.
- **PRD lines 701, 789:** admin and super-admin views never see journal content; SA impersonation
  gets **403** on journal routes; *only metadata counts* are ever visible to the system. This spec
  exposes no such counts, so nothing extra is needed — but a future admin/analytics module must add
  counts only, never content or mood.
- **Never** derive or expose an aggregate that would let anyone but the author infer mood — the
  founder-only gate covers content *and* mood *and* the card.
- Prompt catalog copy is reviewed as **copy** before merge (Task 4).

## 7. Events, errors, config

### 7.1 Events

**None.** PRD line 879 lists `journal-service` as `— (isolated)`; the evening reflection
notification (line 711) belongs to Module 20. No `journal.*` event is invented here.

### 7.2 Errors

| Code | HTTP | When |
|---|---|---|
| `NOT_FOUND` | 404 | unknown id · another founder's entry · another workspace's entry |
| `VALIDATION_ERROR` | 422 | bad `mood` enum · `stress` outside 1–10 · empty `content` · bad `range` · `date` in the future |
| `FORBIDDEN` | 403 | a non-founder member — including an impersonating admin — on **any** journal route |
| `EMAIL_NOT_VERIFIED` | 403 | unverified user (shared guard) |
| `JOURNAL_NOT_CONFIGURED` | 500 | **new** — `JOURNAL_ENCRYPTION_KEY` unset (mirrors `MFA_NOT_CONFIGURED`) |
| `JOURNAL_CONTENT_UNREADABLE` | 500 | **new** — a stored token will not decrypt (wrong or rotated key) |

Two new `AppError` subclasses in `app/core/errors.py`; everything else reuses the taxonomy.

### 7.3 Config

`JOURNAL_ENCRYPTION_KEY: str | None = None` in `app/core/config.py`, **plus** the entries the current
branch is missing: `.env.example`, `docker-compose.yml`, and the CI env — without them the app boots
but every journal route 500s. Generated the same way as the MFA key (`Fernet.generate_key()`).

## 8. Testing

- **TDD**, real Postgres + per-test rollback; factories `create_journal_entry` and
  `create_support_dismissal` in `tests/factories.py`.
- **Encryption:** round-trip; ciphertext ≠ plaintext; **two workspaces encrypt the same plaintext to
  different tokens and cannot decrypt each other's** (the whole point of the derivation); unset key →
  `JOURNAL_NOT_CONFIGURED`; corrupt token → `JOURNAL_CONTENT_UNREADABLE`; unicode/emoji survive.
- **Privacy first (handoff §10 — written before any content route):** every one of the 8 routes
  returns **403** for `team_member`, `mentor`, and every other non-founder role; a **second founder**
  in the same workspace gets **404** on the first founder's entry ids and an empty list; a founder of
  another workspace gets **404**; ciphertext never appears in a response and decrypted content never
  appears in a log.
- **Upsert:** the same date twice → one row, updated content, `201` then `200`; a **two-connection
  concurrency test** on the `ON CONFLICT` claim (mirrors the roadmap race test).
- **CRUD:** create/read/patch/delete; `date` immutable on PATCH; a future date → `422`; empty content
  → `422`; delete removes it and a subsequent read is `404`.
- **List:** reverse-chron order; `from`/`to` filtering; pagination bounds (`limit` max 100);
  `preview` is a truncated first line; `search` matches decrypted content and misses non-matches.
- **Mood:** the series omits missing days and mood-less entries; `mood_value` mapping; averages; each
  `range` value; a bad `range` → `422`.
- **Support card (the rule table):** 14 days all `rough` with 10 logged → shows; the same with only
  6 logged → **hidden** (the 7-day floor); one `good` day among lows → hidden; shown then dismissed →
  hidden for 30 days → shows again on day 31; the copy is byte-identical to `SUPPORT_CARD_TEXT`;
  dismissal writes exactly one row and records nothing but the timestamp; another founder's
  dismissal never suppresses this founder's card.
- **Prompts:** deterministic for a given date, stable within a day, rotates across days, every
  catalog entry reachable, all four themes present, version stamped.
- **Smoke:** the 8 journal routes added to `e2e/test_smoke.py`'s OpenAPI surface assertion.
- **Live E2E** (`e2e/test_journal.py`): a founder logs in → writes today's entry with mood and stress
  → reads it back **decrypted** → autosaves an edit → lists entries → searches → fetches the 30d
  trend → fetches today's prompt → **a team_member on the same workspace gets 403 on every route** →
  a second workspace's founder gets 404. Responses captured to `e2e/_captures/`.
- **FE guide** (`docs/fe-integration-guide-journal.md`): every payload pasted from a capture. Journal
  text in the guide must be **obviously fake** (handoff §10). Note the §0 gap — the comps were not
  available, so the captures *are* the contract.

## 9. Plan shape

One plan (`docs/superpowers/plans/2026-08-30-founder-journal.md`), ~11 TDD tasks, subagent-driven
(a fresh implementer plus an independent review per task, then a whole-branch review):

1. `encryption.py` → per-workspace derived key + `JournalNotConfigured` / `JournalContentUnreadable`
   + config made optional + `.env.example`/compose/CI entries **(gated on checkpoint #1)**
2. `Mood` enum + `JournalEntry` + `JournalSupportDismissal` models + factories
3. Migration `0008_journal`
4. Themed prompt catalog + `GET /journal/prompts/today` **(gated on checkpoint #2 copy review)**
5. Founder-only + author-scoped access tests (the 403/404 matrix) — **written before any content route**
6. `POST /journal/entries` upsert (+ race test) + `GET /journal/entries/{id}`
7. `GET /journal/entries` list, preview, search, pagination
8. `PATCH` + `DELETE /journal/entries/{id}`
9. `GET /journal/mood` series + summary
10. Support card rule + dismissal endpoint **(gated on the §6.3 rule sign-off)**
11. Live E2E journey + captures; smoke surface; SOP + FE guide + checklist
