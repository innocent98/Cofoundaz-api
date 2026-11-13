# FE Integration Guide — Legal & Compliance (Module 13, Slice 1)

> **Provenance.** Every response body in this guide is pasted verbatim from a live capture
> (`e2e/test_legal.py` → `e2e/_captures/legal/`, run 2026-11-13 against a real uvicorn server on an
> isolated `cofoundaz_e2e` database migrated from zero to `0106_legal`). IDs, dates and timestamps
> in the bodies are **placeholders from that test run**. Behaviour the live run did **not**
> exercise is labelled inline and listed in the verification table at the end (§8).

Base path: `/api/v1/legal`. Every route requires a Bearer access token and an `X-Workspace-Id`
header naming the active workspace (`GET /auth/me` → `data.active_workspace_id`), the same as every
other tenant-scoped endpoint. There are **no public routes** in this module.

**Access — two sets, which is new.** Legal is the first module with a read set wider than its
write set:

| Role | GET | POST / PATCH / DELETE |
|---|---|---|
| founder | ✅ | ✅ |
| team_member | ✅ | ✅ |
| **legal_advisor** | ✅ | **403** |
| mentor, accountant, business_consultant, investor | **403** | **403** |

Unauthenticated → **401**. Unverified email → **403** `EMAIL_NOT_VERIFIED`.

> Do not reuse a single `canAccessLegal` boolean. A legal advisor must see every list and detail
> and must not see a single create, edit, delete or upload control. Hide the controls as well as
> handling the 403 — a disabled button a user can never use is worse than no button.

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}`.

---

## 0. Nine things the mock prototype will mislead you about

The prototype at `app/(dashboard)/legal/` is a mock — `hooks/useLegalApi.ts` is 144 lines of
`useState` with hard-coded arrays, no network, and every write is a `triggerToast(…)`. It
contradicts the real API in nine places. Read this before wiring anything.

**1. Jurisdictions are CODES, and there are two, not four.** The dropdown offers Nigeria, Kenya,
Ghana and Delaware, and sends the display name. The API serves **`NG`** and **`US-DE`** and takes
the code. `?jurisdiction=Nigeria` is a 404, and so are Kenya and Ghana — those two are **not
supported in Slice 1** (see `D-LEG-2`). Drive the dropdown from `GET /formation/jurisdictions`:
send `code`, render `name`.

**2. A formation step is structured, not one `meta` string.** The mock squashes everything into
`meta: "CAC · est. 1 day · ₦500"`. The API returns `authority`, `est_days`, `est_cost_minor` and
`est_cost_currency` separately. Build the sentence yourself.

**3. Money is integer MINOR units plus a currency code.** `est_cost_minor: 50000` with
`est_cost_currency: "NGN"` is **₦500.00**, not ₦50,000. Divide by 100. `null` for both together
means "we do not know the fee" — render nothing, never "₦0", which reads as "free".

**4. Dates are ISO, never prose.** The mock ships `due: "Was due Jul 10"` and
`renewal: "Renews Mar 2027"`. The API returns `"2026-11-16"`. Build the sentence on the front end.

**5. There are TWO status fields, and they mean different things.** `status` is what is
**stored** and what an edit form sends back. `derived_status` is what you **render**. Never send
`derived_status`, and never badge from `status`.

| Resource | `status` (stored, sendable) | `derived_status` (render this) |
|---|---|---|
| IP asset | `drafting` · `filed` · `registered` | the same three, **or `expired`** |
| Compliance item | `open` · `done` | `upcoming` · `due_soon` · `overdue` · `done` |

**6. `expired` cannot be created or sent.** It is derived from `renewal_on` falling before today.
The mock's add drawer lists it as a choice; the API rejects it. Offer `drafting`, `filed` and
`registered` only.

**7. `statusBg` and `dueColor` do not exist.** The mock carries Tailwind classes as data. Colour is
a front-end decision, mapped from `derived_status`.

**8. The compliance field is `title`, not `filing`.** The mock's `ComplianceItem.filing` is
`title` on the wire. `authority` keeps its name and is **nullable**.

**9. There is no overdue-count endpoint.** Every row carries its own `derived_status`, so count
`derived_status === "overdue"` client-side for the banner — which is what the mock already does.

---

## 1. Formation — jurisdictions

### `GET /api/v1/legal/formation/jurisdictions` → **200**

```json
{
  "data": {
    "jurisdictions": [
      { "code": "NG", "name": "Nigeria" },
      { "code": "US-DE", "name": "Delaware, USA" }
    ]
  },
  "meta": null
}
```

Drive the dropdown from this. Do not hard-code the list — when a jurisdiction is added, the FE
should pick it up without a release.

---

## 2. Formation — the checklist

### `GET /api/v1/legal/formation/checklist?jurisdiction=NG` → **200**

`jurisdiction` is **required**; omitting it is a **422**. An unsupported code is a **404**.

```json
{
  "data": {
    "jurisdiction": { "code": "NG", "name": "Nigeria" },
    "estimates": true,
    "disclaimer": "These steps, timings and fees are general guidance, not professional legal advice. Figures are estimates and change. For anything non-standard, involve a licensed professional — you can find one in the Marketplace.",
    "steps": [
      {
        "key": "reserve_name",
        "title": "Reserve company name",
        "authority": "CAC",
        "est_days": 1,
        "est_cost_minor": 50000,
        "est_cost_currency": "NGN",
        "completed": false,
        "completed_at": null
      },
      {
        "key": "file_incorporation",
        "title": "File incorporation documents",
        "authority": "CAC",
        "est_days": 3,
        "est_cost_minor": 2000000,
        "est_cost_currency": "NGN",
        "completed": false,
        "completed_at": null
      },
      {
        "key": "tax_id",
        "title": "Get Tax Identification Number",
        "authority": "FIRS",
        "est_days": 5,
        "est_cost_minor": null,
        "est_cost_currency": null,
        "completed": false,
        "completed_at": null
      },
      {
        "key": "bank_account",
        "title": "Open a corporate bank account",
        "authority": "Your bank",
        "est_days": 2,
        "est_cost_minor": null,
        "est_cost_currency": null,
        "completed": false,
        "completed_at": null
      }
    ]
  },
  "meta": null
}
```

**`disclaimer` must be rendered.** It is not decoration — it is the module's "this is not legal
advice" statement, and nothing resembling legal guidance ships without it. `estimates: true` says
the same thing about the figures: present them as estimates, never as verified fact.

**Steps live in code, not the database.** Only a startup's progress is stored, and `key` is the
stable identifier — never the array index, never `title`.

### `PATCH /api/v1/legal/formation/steps/{step_key}` → **200**

```json
{ "jurisdiction": "NG", "completed": true }
```

Both fields are required. The body is the **desired state, not a delta** — sending
`completed: true` twice is a no-op, not an error, so an impatient double-click is safe.

The response is **the whole checklist again**, so you never need a follow-up GET:

```json
{
  "data": {
    "jurisdiction": { "code": "NG", "name": "Nigeria" },
    "estimates": true,
    "disclaimer": "These steps, timings and fees are general guidance, not professional legal advice. Figures are estimates and change. For anything non-standard, involve a licensed professional — you can find one in the Marketplace.",
    "steps": [
      {
        "key": "reserve_name",
        "title": "Reserve company name",
        "authority": "CAC",
        "est_days": 1,
        "est_cost_minor": 50000,
        "est_cost_currency": "NGN",
        "completed": true,
        "completed_at": "2026-11-13T10:07:36.007267+00:00"
      }
    ]
  },
  "meta": null
}
```

*(three further steps omitted here for brevity — the real body returns all four, unchanged)*

**Progress is kept per jurisdiction**, so switching the dropdown and switching back never loses
ticks.

### Unsupported jurisdiction → **404**

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

A 404, not a 422, because it names a resource — and it keeps the catalog non-enumerable. Today
Kenya and Ghana in the mock's dropdown land here.

---

## 3. IP assets

### `POST /api/v1/legal/ip-assets` → **200**

```json
{
  "type": "trademark",
  "name": "Kolo (wordmark)",
  "jurisdiction": "Nigeria",
  "status": "registered",
  "renewal_on": "2026-11-23"
}
```

`type`: `trademark` · `patent` · `copyright` · `domain`. `status`: `drafting` · `filed` ·
`registered`. `jurisdiction` here is **free text**, not a code — it must hold "Global" and "EU",
so it is deliberately not the formation code list. `renewal_on` is optional.

```json
{
  "data": {
    "id": "22cb15a5-0b82-454d-b386-771a54190c68",
    "type": "trademark",
    "name": "Kolo (wordmark)",
    "jurisdiction": "Nigeria",
    "status": "registered",
    "derived_status": "registered",
    "renewal_on": "2026-11-23",
    "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
    "created_at": "2026-11-13T10:07:36.576229+00:00",
    "updated_at": "2026-11-13T10:07:36.576229+00:00"
  },
  "meta": null
}
```

### A passed renewal reads as `expired` — derived, never stored

```json
{
  "data": {
    "id": "58665117-f1a3-4f92-bf76-92ee0aa2a132",
    "type": "domain",
    "name": "kolo.africa",
    "jurisdiction": "Global",
    "status": "registered",
    "derived_status": "expired",
    "renewal_on": "2026-10-14",
    "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
    "created_at": "2026-11-13T10:07:36.727760+00:00",
    "updated_at": "2026-11-13T10:07:36.727760+00:00"
  },
  "meta": null
}
```

Note `status` is still `registered`. **An edit form must send `status` back, not
`derived_status`** — sending `expired` is a 422.

### `GET /api/v1/legal/ip-assets` → **200**

Optional filters: `?type=` and `?status=` (the stored status). Ordered newest first by renewal
relevance as captured below.

```json
{
  "data": {
    "ip_assets": [
      {
        "id": "58665117-f1a3-4f92-bf76-92ee0aa2a132",
        "type": "domain",
        "name": "kolo.africa",
        "jurisdiction": "Global",
        "status": "registered",
        "derived_status": "expired",
        "renewal_on": "2026-10-14",
        "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
        "created_at": "2026-11-13T10:07:36.727760+00:00",
        "updated_at": "2026-11-13T10:07:36.727760+00:00"
      },
      {
        "id": "22cb15a5-0b82-454d-b386-771a54190c68",
        "type": "trademark",
        "name": "Kolo (wordmark)",
        "jurisdiction": "Nigeria",
        "status": "registered",
        "derived_status": "registered",
        "renewal_on": "2026-11-23",
        "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
        "created_at": "2026-11-13T10:07:36.576229+00:00",
        "updated_at": "2026-11-13T10:07:36.576229+00:00"
      }
    ]
  },
  "meta": null
}
```

### `GET` / `PATCH` / `DELETE /api/v1/legal/ip-assets/{asset_id}`

`PATCH` is partial — send only what changed. Sending an explicit `null` for a **required** field
is a **422**, not a clear; omitting it is the no-op. `renewal_on` **is** nullable, so an explicit
`null` there legitimately clears the date.

```json
{
  "data": {
    "id": "22cb15a5-0b82-454d-b386-771a54190c68",
    "type": "trademark",
    "name": "Kolo (wordmark, class 36)",
    "jurisdiction": "Nigeria",
    "status": "registered",
    "derived_status": "registered",
    "renewal_on": "2026-11-23",
    "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
    "created_at": "2026-11-13T10:07:36.576229+00:00",
    "updated_at": "2026-11-13T10:07:36.932525+00:00"
  },
  "meta": null
}
```

`DELETE` → **200**:

```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```

The row is gone; a following `GET` on that id is a 404.

---

## 4. Compliance items

### `POST /api/v1/legal/compliance-items` → **200**

```json
{
  "title": "VAT return",
  "authority": "FIRS",
  "due_on": "2026-11-16",
  "recurrence": "monthly"
}
```

`title` and `due_on` are required. `authority` is optional and nullable. `recurrence` is
`none` (default) · `monthly` · `quarterly` · `annually`.

```json
{
  "data": {
    "id": "542abf3c-18fe-4818-90ab-639a76255fe6",
    "title": "VAT return",
    "authority": "FIRS",
    "due_on": "2026-11-16",
    "status": "open",
    "derived_status": "due_soon",
    "done_at": null,
    "proof_url": null,
    "recurrence": "monthly",
    "previous_item_id": null,
    "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
    "created_at": "2026-11-13T10:07:37.543539+00:00",
    "updated_at": "2026-11-13T10:07:37.543539+00:00"
  },
  "meta": null
}
```

### An item created already past its date

```json
{
  "data": {
    "id": "211ef6b0-b3dc-4645-a181-99eddaec8315",
    "title": "Annual return",
    "authority": "CAC",
    "due_on": "2026-11-08",
    "status": "open",
    "derived_status": "overdue",
    "done_at": null,
    "proof_url": null,
    "recurrence": "none",
    "previous_item_id": null,
    "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
    "created_at": "2026-11-13T10:07:37.882494+00:00",
    "updated_at": "2026-11-13T10:07:37.882494+00:00"
  },
  "meta": null
}
```

Backfilling a filing that is already late is normal and allowed — there is no "due date must be in
the future" rule. It comes back `overdue` immediately.

### How `derived_status` is decided

In this order, which matters:

1. `status === "done"` → **`done`**. A done filing is never overdue, however old.
2. `due_on` **before** today → **`overdue`**
3. `due_on` within **14 days** of today → **`due_soon`**
4. otherwise → **`upcoming`**

A filing due **today** is `due_soon`, not `overdue` — today has not passed. "Today" is the **UTC**
date, the same clock the reminders use.

### `GET /api/v1/legal/compliance-items` → **200**

Optional: `?date_from=`, `?date_to=` (inclusive ISO dates) and `?status=` — which filters the
**stored** value (`open`/`done`) only. There is no filter on `derived_status`; do that client-side.

```json
{
  "data": {
    "compliance_items": [
      {
        "id": "211ef6b0-b3dc-4645-a181-99eddaec8315",
        "title": "Annual return",
        "authority": "CAC",
        "due_on": "2026-11-08",
        "status": "open",
        "derived_status": "overdue",
        "done_at": null,
        "proof_url": null,
        "recurrence": "none",
        "previous_item_id": null,
        "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
        "created_at": "2026-11-13T10:07:37.882494+00:00",
        "updated_at": "2026-11-13T10:07:37.882494+00:00"
      },
      {
        "id": "542abf3c-18fe-4818-90ab-639a76255fe6",
        "title": "VAT return",
        "authority": "FIRS",
        "due_on": "2026-11-16",
        "status": "open",
        "derived_status": "due_soon",
        "done_at": null,
        "proof_url": null,
        "recurrence": "monthly",
        "previous_item_id": null,
        "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
        "created_at": "2026-11-13T10:07:37.543539+00:00",
        "updated_at": "2026-11-13T10:07:37.543539+00:00"
      }
    ]
  },
  "meta": null
}
```

Ordered by `due_on` ascending. The overdue banner counts
`items.filter(i => i.derived_status === "overdue").length`.

### `PATCH /api/v1/legal/compliance-items/{item_id}` → **200**

Partial. Marking done is `{"status": "done"}`; the API stamps `done_at` itself.

```json
{
  "data": {
    "id": "542abf3c-18fe-4818-90ab-639a76255fe6",
    "title": "VAT return",
    "authority": "FIRS",
    "due_on": "2026-11-16",
    "status": "done",
    "derived_status": "done",
    "done_at": "2026-11-13T10:07:38.704091+00:00",
    "proof_url": "/api/v1/legal/compliance-items/542abf3c-18fe-4818-90ab-639a76255fe6/proof",
    "recurrence": "monthly",
    "previous_item_id": null,
    "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
    "created_at": "2026-11-13T10:07:37.543539+00:00",
    "updated_at": "2026-11-13T10:07:38.684115+00:00"
  },
  "meta": null
}
```

An explicit `null` on `title`, `due_on`, `recurrence` or `status` is a **422**. An explicit `null`
on `authority` legitimately **clears** it, because that column is nullable. Omitting a field is
always a no-op.

`DELETE` → `{"data": {"deleted": true}, "meta": null}`, and it deletes any stored proof with it.

---

## 5. Recurrence — refetch the list after marking done

**Marking a recurring filing done creates the next occurrence.** Nothing tells you this in the
PATCH response, so **refetch the list** after a successful mark-done or the new row will not
appear until the next navigation.

```json
{
  "data": {
    "compliance_items": [
      {
        "id": "542abf3c-18fe-4818-90ab-639a76255fe6",
        "title": "VAT return",
        "due_on": "2026-11-16",
        "status": "done",
        "derived_status": "done",
        "recurrence": "monthly",
        "previous_item_id": null
      },
      {
        "id": "1ee4c3fa-c0bb-4c21-bb62-33945f82d2b1",
        "title": "VAT return",
        "authority": "FIRS",
        "due_on": "2026-12-16",
        "status": "open",
        "derived_status": "upcoming",
        "done_at": null,
        "proof_url": null,
        "recurrence": "monthly",
        "previous_item_id": "542abf3c-18fe-4818-90ab-639a76255fe6",
        "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
        "created_at": "2026-11-13T10:07:38.684115+00:00",
        "updated_at": "2026-11-13T10:07:38.684115+00:00"
      }
    ]
  },
  "meta": null
}
```

*(the first row is trimmed above to show the pairing; the real body returns both in full)*

The successor is a **fresh filing**: `status: "open"`, no `done_at`, **no proof of its own**, and
`previous_item_id` pointing at the one just completed. Use that link to render "next occurrence"
or a series history.

**The series cannot drift.** The next date is anchored to the original day-of-month, so a series
on the 31st reads 31 Jan → 28 Feb → **31 Mar**: February clamps, March recovers. A non-recurring
item (`recurrence: "none"`) spawns nothing.

---

## 6. Proof of filing

### `POST /api/v1/legal/compliance-items/{item_id}/proof` → **200**

`multipart/form-data`, field name **`file`**. PNG, JPEG or PDF, up to **10 MB**.

Proof is **optional** — mark-done never requires one. Uploading replaces any existing proof and
deletes the old file.

Response is the full item with `proof_url` populated. Note the item is still `open` here —
uploading proof does **not** mark the filing done; those are two separate actions.

```json
{
  "data": {
    "id": "542abf3c-18fe-4818-90ab-639a76255fe6",
    "title": "VAT return",
    "authority": "FIRS",
    "due_on": "2026-11-16",
    "status": "open",
    "derived_status": "due_soon",
    "done_at": null,
    "proof_url": "/api/v1/legal/compliance-items/542abf3c-18fe-4818-90ab-639a76255fe6/proof",
    "recurrence": "monthly",
    "previous_item_id": null,
    "created_by": "c8375f96-b0ef-45b4-be70-e395c21e2916",
    "created_at": "2026-11-13T10:07:37.543539+00:00",
    "updated_at": "2026-11-13T10:07:38.172601+00:00"
  },
  "meta": null
}
```

> **The browser's `Content-Type` is ignored.** The file is classified by its actual leading bytes.
> Renaming `report.html` to `proof.png` does not get it past this — you will get the 422 below. Do
> not try to "fix" a rejection by changing the declared type; convert the file instead.

### Rejected upload → **422**

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Proof must be a PNG, JPEG, or PDF.",
    "field_errors": [
      {
        "field": "proof",
        "message": "Proof must be a PNG, JPEG, or PDF."
      }
    ]
  }
}
```

The same shape, with `"Proof exceeds the 10 MB limit."`, is returned for an oversized file.

### ⚠️ `proof_url` is not yet fetchable

`proof_url` is an **API route**, not a storage address — the file is stored privately and has no
public URL. But **only `POST` is implemented on that route today.** A `GET` will not return the
file.

The authenticated download is `D-LEG-5`, awaiting a decision between two designs (a short-lived
signed link, as Finance uses, or a direct authenticated stream). **Until it ships: use
`proof_url !== null` as a "proof attached" indicator only — do not link to it.** This section will
be updated with the real download shape once the route exists.

---

## 7. Errors you will actually hit

### Legal advisor attempting any write → **403**

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

### Mentor, accountant, consultant or investor on any route → **403**

Identical body. These roles see nothing in Legal — hide the nav entry as well.

### Another workspace's id → **404**, never 403

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

Deliberate: a 403 would confirm the id exists. Treat a 404 on an id you believe in as "gone or
never yours", and send the user back to the list.

---

## 8. Capture map

Every body above comes from one of these files in `e2e/_captures/legal/` (captured 2026-11-13).
Re-run `e2e/test_legal.py` against a live server to regenerate them.

Two bodies are **trimmed** in this guide to keep a point readable, and say so where they appear —
the capture files hold them in full. One capture is **evidence only**: it proves a behaviour but
no body is pasted, because its shape is identical to a body already shown.

| Section | Capture |
|---|---|
| `GET /formation/jurisdictions` | `formation_jurisdictions.json` |
| `GET /formation/checklist` | `formation_checklist.json` |
| `PATCH /formation/steps/{key}` | `formation_step_ticked.json` *(trimmed above)* |
| Unsupported jurisdiction 404 | `formation_unsupported_jurisdiction.json` |
| `POST /ip-assets` | `ip_asset_created.json` |
| `POST /ip-assets`, passed renewal | `ip_asset_expired.json` |
| `GET /ip-assets` | `ip_assets_list.json` |
| `PATCH /ip-assets/{id}` | `ip_asset_updated.json` |
| `DELETE /ip-assets/{id}` | `ip_asset_deleted.json` |
| `POST /compliance-items` | `compliance_item_created.json` |
| `POST /compliance-items`, already overdue | `compliance_item_overdue.json` |
| `GET /compliance-items` | `compliance_items_list.json` |
| `POST …/proof` | `compliance_proof_attached.json` |
| `POST …/proof` 422, lying content type | `compliance_proof_rejected.json` |
| `PATCH …` mark done | `compliance_item_done.json` |
| Recurrence successor | `compliance_recurrence_spawned.json` *(trimmed above)* |
| Legal advisor GET 200 | `legal_advisor_read.json` *(evidence only — body identical to the list in §4)* |
| Legal advisor write 403 | `legal_advisor_write_forbidden.json` |
| Mentor 403 | `forbidden_role.json` |
| Cross-tenant 404 | `cross_tenant_404.json` |

---

## 9. Verification

| Behaviour | Status |
|---|---|
| Jurisdictions list; checklist shape, disclaimer, `estimates` flag | ✅ verified live |
| Tick a step; repeat is idempotent; untick | ✅ verified live |
| Progress kept per jurisdiction across a switch | ⚠️ unit tests only |
| Unsupported jurisdiction **404**; missing param **422** | ✅ 404 live · ⚠️ 422 unit tests only |
| IP create / list / patch / delete; `{"deleted": true}` | ✅ verified live |
| `derived_status: "expired"` from a passed `renewal_on`, `status` unchanged | ✅ verified live |
| `?type=` and `?status=` filters on IP list | ⚠️ unit tests only |
| Compliance create, `due_soon` and `overdue` derivation, list ordering | ✅ verified live |
| Date-range filter (`date_from` / `date_to`) | ✅ verified live |
| Mark done stamps `done_at`; `derived_status: "done"` | ✅ verified live |
| Recurrence successor with `previous_item_id`, open and proof-free | ✅ verified live |
| Month-end anchoring (31 Jan → 28 Feb → 31 Mar), leap years | ⚠️ unit tests only (`tests/services/legal/test_recurrence.py`) |
| Proof upload stores privately; `proof_url` is the route | ✅ verified live |
| Lying `Content-Type` rejected **422**, nothing stored | ✅ verified live |
| Oversized file **422** | ⚠️ API tests only |
| Explicit-null-on-required **422** | ⚠️ API tests only |
| Legal advisor reads all three surfaces, is 403 on every write | ✅ verified live |
| Mentor 403 on every read | ✅ verified live |
| Accountant / consultant / investor 403 | ⚠️ API tests only (`tests/api/test_legal.py`) |
| Cross-tenant **404** on GET, PATCH, DELETE | ✅ GET and DELETE live · ⚠️ PATCH API tests only |
| Unauthenticated → 401 | ⚠️ API test accepts **401 or 403**, so the exact code is not pinned; handle both |
| `GET` on `proof_url` | ❌ **not implemented** — see §6 and `D-LEG-5` |
| Reminder notifications at 14 / 7 / 1 days and on going overdue | ⚠️ worker tests only (`tests/worker/test_legal_scheduler.py`) |
