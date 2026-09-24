# FE Integration Guide — Founder Journal (Module 21)

> ⚠️ **Provenance.** `e2e/test_journal.py::test_journal_journey` has now been run
> (`bash scripts/e2e_run.sh`) and every body is captured verbatim under
> `e2e/_captures/journal/`. **§1 below (today's prompt, including the AI upgrade) is pasted
> verbatim from those captures** — that part of this guide is live-verified. The **rest of this
> guide (§2–§7) has not yet been regenerated from the real captures** and remains derived from
> the response models in `app/schemas/journal.py`: the shapes and field names are exact (they
> come from the Pydantic models the API actually serialises), but the example values are
> illustrative, not copied from a live run. This is a known, pre-existing gap flagged for a
> future docs pass — it predates and is out of scope for the Module 03 deferred-AI-upgrades work
> that added §1's AI-prompt section. Regenerate §2–§7 from `e2e/_captures/journal/*.json` and
> delete this note when that pass happens.

Base path: `/api/v1/journal`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header identifying the active
workspace (`GET /auth/me` → `data.active_workspace_id` is the source for that header), same as
every other tenant-scoped endpoint in this API.

**Access is narrower than any other module.** Every route is **founder-only AND author-only**.
A team member, mentor, accountant, legal advisor, business consultant or investor gets **403**
on every route. A *second founder in the same workspace* gets **404** on the first founder's
entries and an empty list of their own — each founder's journal is private to them, not shared
across the workspace. Plan your UI accordingly: do not show journal navigation to non-founders.

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see §8).

---

## 1. `GET /api/v1/journal/prompts/today` — the daily prompt

The writing surface asks for this first, to fill the dismissible prompt card. The response shape
is always exactly `{"prompt": "..."}` — no other keys, ever (verified live, see the assertion in
Verification).

`e2e/_captures/journal/prompts_today.json` — the first read of the day, before any AI upgrade:

```json
{
  "data": {
    "prompt": "What went well today that you do not want to overlook?"
  },
  "meta": null
}
```

The static prompt rotates deterministically by date — the same founder gets the same prompt all
day, and a different one tomorrow (one of 7 fixed strings, `JournalService.get_prompt`). It takes
no parameters.

> **Not yet built:** the PRD's themed prompt library (Decisions / Energy / Team / Wins) and
> the `/journal/prompts` browse screen. Today there is one prompt string and no theme field.

### `prompt` — static first, AI-personalized on a later poll (Module 03 deferred AI upgrade)

**The shape never changes — this is still just `{"prompt": "..."}`.** What changes is the
*value*, in place, after a later poll:

1. The **first** `GET /journal/prompts/today` for a given founder+day writes a row seeded with
   the static rotating line above and enqueues an `ai.journal.prompt` job. The static line is
   returned immediately.
2. Within seconds, the `ai.journal.prompt` worker (`app/worker/handlers/ai.py:386`) tries to
   personalize it, **grounded only in operational signals** — the founder's most recently
   *shipped* (`status: done`) roadmap milestone, and/or today's mission's current focus task
   (`app/services/journal/ai_prompt.py::gather_prompt_context`). **It never reads journal
   content or mood/stress** — this is a deliberate privacy boundary (the module that grounds the
   prompt does not import `JournalEntry` or `MoodLog` at all), so nothing the founder has written
   or felt can leak into a system-generated prompt.
3. If **neither** signal exists (no shipped milestone, no mission focus task), the job no-ops and
   the static line is kept **permanently** for that day — there is no infinite-retry, no
   "still trying" state.
4. **There is no separate "is this AI-authored" flag and no `status` field on this response** —
   poll `GET /journal/prompts/today` again (the same call the screen already makes on load) to
   pick up the personalized line once the job has run. Do not try to distinguish static from
   AI-authored text at render time.

`e2e/_captures/journal/prompt_today_ai.json` — the same founder, same day, re-fetched after
seeding a mission-focus signal and draining the `ai.journal.prompt` worker:

```json
{
  "data": {
    "prompt": "[stub-llm] prompt"
  },
  "meta": null
}
```

**`"[stub-llm] prompt"` is the offline stub provider's deterministic marker**
(`LLM_PROVIDER=stub`, used in tests and e2e) — a real OpenAI provider returns a genuine
reflective question in its place, same field, same shape, same ≤300-character cap (the worker
truncates defensively, `prompt[:300]`).

### Over-budget behavior

When the workspace is over its daily LLM token budget, `ai.journal.prompt` skips its LLM call
entirely and `prompt` **stays on the static line** — not an error state, no "failed" signal on
this endpoint. See `docs/fe-integration-guide-ai-status.md` — `GET /ai/status`'s `over_budget`
field explains *why* new AI enrichment is paused workspace-wide; as with every other AI consumer
in this API, this endpoint gives no per-record signal distinguishing "still static because of
budget" from "still static because no operational signal existed yet."

---

## 2. `POST /api/v1/journal/entries` — write or autosave today's entry

**This is an upsert, not a create.** There is **one entry per founder per calendar date**.
POSTing the same `date` again **updates** that entry and returns it — it does not create a
second one, and it does not error. This is exactly what autosave needs: just POST repeatedly.

**Request:**

```json
{
  "date": "2026-09-03",
  "content": "Shipped the invite flow.\nStill unsure about pricing.",
  "mood": "good",
  "stress": 4
}
```

| Field | Type | Rules |
|---|---|---|
| `date` | `YYYY-MM-DD` | Required. **Must not be in the future** → 422 |
| `content` | string | Required. Must not be empty or only whitespace → 422 |
| `mood` | enum | Required. One of `rough`, `meh`, `okay`, `good`, `great` |
| `stress` | integer | Required. **1–10 inclusive** → 422 outside that |

The five `mood` values map to the PRD's emoji scale in that order (Rough → Great).

**Response — 200:**

```json
{
  "data": {
    "id": "3f2c9a10-7b41-4e8e-9c2a-1d5f7b0e4a63",
    "startup_id": "9a1e7c33-05b2-4f6d-8a10-c7e2b9d41f58",
    "founder_id": "b47d2e91-6c30-4a5f-9e18-2f6a8c0d3b74",
    "date": "2026-09-03",
    "content": "Shipped the invite flow.\nStill unsure about pricing.",
    "mood": "good",
    "stress": 4
  },
  "meta": null
}
```

**Note the status code: 200, not 201.** Because the route upserts, there is no meaningful
distinction between "created" and "updated" — don't branch on 201.

**Autosave guidance:** debounce on the client, then POST the whole entry. Two saves firing at
the same instant are safe — the database resolves the conflict atomically, so you will never
get a duplicate or a 500 from a race.

---

## 3. `GET /api/v1/journal/entries` — the past-entries list

Reverse-chronological (newest first), paginated.

**Query parameters:**

| Param | Default | Rules |
|---|---|---|
| `skip` | `0` | ≥ 0 |
| `limit` | `20` | 1–100 |
| `search` | — | Optional; at least 1 character |

```json
{
  "data": {
    "entries": [
      {
        "id": "3f2c9a10-7b41-4e8e-9c2a-1d5f7b0e4a63",
        "date": "2026-09-03",
        "mood": "good",
        "first_line": "Shipped the invite flow."
      },
      {
        "id": "c81b4d27-9e50-4a3b-b6f1-7d2c5a9e08b4",
        "date": "2026-09-02",
        "mood": "meh",
        "first_line": "Long day of investor emails."
      }
    ],
    "total": 2
  },
  "meta": null
}
```

### The `first_line` trap — there is no `content` here

List items carry **only the first line** of the entry, never the body. That is deliberate: a
preview column would leak exactly what the encryption protects. To show a full entry you must
fetch it by id (§4).

`first_line` is the text up to the first newline. An entry whose content is a single line
returns that whole line. **It can be long** — there is no server-side truncation, so clamp it
in CSS rather than assuming a short string.

### `total` is the full count, not the page size

`total` counts every entry matching the query, ignoring `skip`/`limit`. Use it for the pager;
use `entries.length` for what you just received.

### Search

`?search=pricing` filters to the founder's own entries containing that text. Because content is
encrypted, the server decrypts the founder's rows and filters in memory — so **search is not
paginated efficiently and will slow down as a journal grows**. Fine for a first year of daily
entries; not something to build an as-you-type experience on. Debounce generously.

---

## 4. `GET /api/v1/journal/entries/{entry_id}` — one full entry

Returns the same shape as §2, with the decrypted `content`.

```json
{
  "data": {
    "id": "3f2c9a10-7b41-4e8e-9c2a-1d5f7b0e4a63",
    "startup_id": "9a1e7c33-05b2-4f6d-8a10-c7e2b9d41f58",
    "founder_id": "b47d2e91-6c30-4a5f-9e18-2f6a8c0d3b74",
    "date": "2026-09-03",
    "content": "Shipped the invite flow.\nStill unsure about pricing.",
    "mood": "good",
    "stress": 4
  },
  "meta": null
}
```

---

## 5. `PATCH /api/v1/journal/entries/{entry_id}` — edit an entry

Partial update. Send only the fields you are changing; omitted fields are left alone.

```json
{ "stress": 6 }
```

| Field | Rules |
|---|---|
| `content` | Optional. If sent, must not be empty or whitespace → 422 |
| `mood` | Optional. One of the five values |
| `stress` | Optional. 1–10 |

`date` cannot be changed — an entry belongs to its day. To move an entry, delete it and POST a
new one.

**Do not send `null`** to clear a field. Explicit nulls are rejected; omit the key instead.

Returns the full updated entry, same shape as §4.

---

## 6. `DELETE /api/v1/journal/entries/{entry_id}` — delete an entry

```json
{
  "data": {
    "id": "3f2c9a10-7b41-4e8e-9c2a-1d5f7b0e4a63",
    "deleted": true
  },
  "meta": null
}
```

**200, not 204** — there is a body. Deletion is permanent and unrecoverable; the encrypted row
is removed. Confirm destructively in the UI.

---

## 7. `GET /api/v1/journal/mood` — the mood/stress trend

Oldest-first series for charting.

| Param | Default | Rules |
|---|---|---|
| `start_date` | — | `YYYY-MM-DD` |
| `end_date` | — | `YYYY-MM-DD`; **`start_date` after `end_date` → 422** |
| `limit` | `90` | 1–365 |

```json
{
  "data": {
    "points": [
      { "date": "2026-09-02", "mood": "meh", "stress": 7 },
      { "date": "2026-09-03", "mood": "good", "stress": 4 }
    ]
  },
  "meta": null
}
```

`mood` comes back as the **string enum**, not a number — map it to your 1–5 scale on the client
if you are plotting it (`rough`=1 … `great`=5). `stress` is already 1–10.

Only days with an entry appear. Gaps are real gaps — do not interpolate; a missing day means
the founder did not write, which is not the same as a neutral mood.

> **Not yet built:** roadmap-event annotations ("Launch week"), the gentle insight lines, and
> the supportive low-mood card described in PRD 21.3. None of these fields exist on this
> response today. When the card lands it will appear inside this payload — design the chart so
> a card can be slotted beneath it later.

---

## 8. Errors

Standard envelope:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

| Status | Code | When |
|---|---|---|
| 401 | — | Missing or invalid access token |
| 403 | `EMAIL_NOT_VERIFIED` | Signed in, but email not verified |
| 403 | `FORBIDDEN` | Not an active founder of this workspace |
| 404 | `NOT_FOUND` | Entry does not exist, **or is not yours** |
| 422 | `VALIDATION_ERROR` | Future date, blank content, stress outside 1–10, `start_date` after `end_date` |
| 500 | `JOURNAL_NOT_CONFIGURED` | Server has no encryption key — operational, not user error |
| 500 | `JOURNAL_CONTENT_UNREADABLE` | Stored content could not be decrypted |

### The uniform 404 — you cannot tell "missing" from "not yours"

An entry that does not exist and an entry belonging to another founder return **byte-identical**
404s. That is deliberate: it stops anyone probing ids to discover which entries exist. **Do not
write UI copy that distinguishes the two cases** — you have no way to, and any wording implying
"this exists but isn't yours" would defeat the protection.

### The two 500s are not the user's fault

`JOURNAL_NOT_CONFIGURED` means the server is missing its encryption key. Show an "unavailable,
try later" state, not a validation message — retrying with different input will never help.

---

## Verification table

| Claim | Source |
|---|---|
| Field names and types | `app/schemas/journal.py` — the models FastAPI serialises |
| Status codes | `app/api/v1/endpoints/journal.py` |
| Access matrix (403/404 per role) | `tests/api/test_journal_access.py` — passing |
| Uniform 404 byte-identity | `tests/api/test_journal_access.py::test_uniform_404_gives_no_enumeration_oracle` |
| Upsert on repeat POST | `tests/api/test_journal_entries.py`, `tests/services/journal/test_upsert.py` |
| Validation 422s | `tests/api/test_journal_entries.py`, `tests/api/test_journal_edit.py` |
| List ordering, `total`, `first_line` | `tests/api/test_journal_list.py` |
| `GET /journal/prompts/today` — static line on first read, shape is exactly `{"prompt": ...}` | ✅ | `prompts_today.json` |
| `prompt` upgraded to AI-authored text after `ai.journal.prompt` drains; re-fetch picks it up | ✅ | `prompts_today.json` → `prompt_today_ai.json` |
| `gather_prompt_context` grounds only in a shipped milestone / mission focus, never journal content or mood | ⚠️ unit only — the live journey seeds a mission-focus signal, not journal content, so there's nothing live to *not* leak; the negative-case proof (seeding a diary entry with distinctive content and asserting it never reaches the LLM messages) is a unit test | `tests/services/journal/test_ai_prompt.py::test_privacy_journal_and_mood_never_surface` |
| No-signal fallback keeps the static prompt permanently, no LLM call | ⚠️ unit only | `tests/services/journal/test_ai_prompt.py::test_gather_returns_none_without_signals` |
| Over-budget → `ai.journal.prompt` skips the LLM call, keeps the static prompt | ⚠️ unit only — same class of gap as every other AI consumer in this API (see `docs/fe-integration-guide-ai-status.md`) | `app/worker/handlers/ai.py::handle_journal_prompt` (`metered_complete_json` returns `None`), `tests/worker/test_journal_prompt_handler.py` |
| **Example response bodies (§2–§7, entries/mood/errors)** | ⚠️ **Still derived from the schemas, not yet regenerated from the live run.** `e2e/_captures/journal/` now has real bodies for every one of these calls (`entry_created.json`, `entry_autosaved.json`, `entries_list.json`, `entries_search.json`, `entry_detail.json`, `entry_updated.json`, `entry_deleted.json`, `mood_trend.json`) — swapping them in is a follow-up docs pass, not done here (see the provenance note at the top of this guide) |