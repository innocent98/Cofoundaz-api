# FE Integration Guide — Documents templates, generator and export (BE-C)

Every response body below is pasted **verbatim** from `e2e/test_documents_templates_export.py` running
against a real server (live captures in `e2e/_captures/documents_templates_export/*.json`, one file per
body cited). IDs, timestamps and tokens are values from that ephemeral run: the **shapes are exact**, the
values are placeholders (sign-link tokens appear as `REDACTED_TOKEN`; the signer email is a placeholder).
Where a body is shortened, the text says `…[trimmed]` and only long strings are cut, never fields.
Anything not exercised live is marked **NOT LIVE** with where its shape comes from (§11 lists them all).

Base path `/api/v1`. Auth: Bearer access token + `X-Workspace-Id` header. Success = `{"data": …, "meta": null}`;
errors = `{"error": {"code", "message", "field_errors"}}`.

> **Role rule (unchanged, D-DOC-1).** Everything here is **founder + team_member only**. Any other role gets
> `403 FORBIDDEN` (a mentor's refusal, live: `template_form_mentor_403.json` and `export_mentor_403.json` below).
> Grants and token links do not open any route in this guide.

## 0. What changed for the FE (read first)

| Area | Before | Now |
|---|---|---|
| Template categories | FE grouped by BUSINESS / FUNDRAISING / FINANCE / OPERATIONS | Each template carries `category`: **`Legal` / `Financial` / `Pitch` / `Operational`** (the PRD four). Group by that field. |
| Legal templates | none | `nda`, `contractor_agreement`, `founder_agreement`, `safe` (kind = the same string). Each carries a `disclaimer`. |
| "Use template" | `POST /documents` with `template_key` made a blank doc | New **form state** (`GET /document-templates/{key}` → `form`) and **`POST /documents/templates/{key}/instantiate`**. `POST /documents` with `template_key` still works (and now gives legal templates their draft text with `[Label]` brackets), but the new flow is the one to use. |
| AI drafts | none for templates | Optional `ai_draft` on instantiate queues a worker job; poll `GET /jobs/{id}`. Retry with `POST /documents/{id}/ai-draft`. |
| Export | none | `POST /documents/{id}/export` → a stored PDF or DOCX **file**, which is signable like any upload. |
| Document/file rows | no `type`, no `disclaimer`, no export link | Document rows: `type: "document"`, `disclaimer`. File rows: `type: "file"`, `origin`, `source_document_id`, `source_version`. |

### Field-nesting traps

* `sections` in the **catalog and the template read** is a list of **heading strings**. `form.sections` is a
  list of **objects** (`key`, `heading`, `value`, `state`, `source`, `ai_draftable`). Do not mix them up.
* `GET /document-templates` (list) has **no** `form`. `GET /document-templates/{key}` has `form` nested at the
  top level of `data`, next to `key`, `name`…
* `POST …/instantiate` returns `{document, form, ai}`: the document is **nested under `document`**, unlike
  `POST /documents`, which returns the document at the top level.
* `disclaimer` is `null` (present, not absent) for non-legal templates and documents.
* `source_document_id` and `source_version` are `null` (present) on an uploaded file; `origin` tells you which it is.
* A section's `value` and a field's `value` are strings, `""` when empty (never `null`); `source` is `null` when empty.

## 1. Template catalog — `GET /document-templates`

Query: none. Returns every template (shown here: the first two entries and the four legal ones are in the
full capture; the body is cut to the first 3 entries for length).

`templates_list.json` — HTTP 200 (only `data.templates` shown)

```json
[
  {
    "key": "business_plan",
    "name": "Business Plan",
    "description": "A full business plan.",
    "kind": "business_plan",
    "category": "Pitch",
    "sections": [
      "Executive Summary",
      "Problem",
      "Solution",
      "Market & Customer",
      "Business Model",
      "Go-to-Market",
      "Team",
      "Financials",
      "The Ask"
    ],
    "disclaimer": null
  },
  {
    "key": "pitch_deck",
    "name": "Pitch Deck",
    "description": "An investor pitch narrative.",
    "kind": "pitch_deck",
    "category": "Pitch",
    "sections": [
      "Hook",
      "Problem",
      "Solution",
      "Why Now",
      "Market",
      "Product",
      "Team",
      "The Ask"
    ],
    "disclaimer": null
  },
  {
    "key": "financial_model",
    "name": "Financial Model",
    "description": "A lightweight financial narrative.",
    "kind": "financial_model",
    "category": "Financial",
    "sections": [
      "Assumptions",
      "Revenue",
      "Costs",
      "Runway",
      "Projections"
    ],
    "disclaimer": null
  }
]
```


Categories present in the live response: `Legal`, `Financial`, `Pitch`, `Operational` (the test asserts the set).
`kind` equals the template `key` for the four legal templates and for the five existing ones.

## 2. Form state — `GET /document-templates/{key}`

Nothing is created. The server reads the workspace's startup profile and Business Builder (lean canvas
blocks and records) and tells the FE which things are already filled, from where, and which an AI draft can
write. Use this to render the form with the ✨ chips **before** "Create document".

`state` is `"filled"` or `"empty"`. `source` is where a filled value came from:

| `source` | Meaning | Suggested chip |
|---|---|---|
| `profile` | the startup profile (name, description, industry…) | "From your profile" |
| `business_builder` | a Business Builder canvas block or record | "From Business Builder" |
| `account` | the signed-in person's name | "From your account" |
| `system` | today's date | – |
| `template` | the template's own text, or a default (for example a 2-year confidentiality period) | – |
| `you` | a value the caller supplied to `instantiate` | – |
| `null` | empty | show the empty input; show ✨ when `ai_draftable` |

Live: a business plan for a workspace with a lean canvas and one revenue stream. (Executive Summary is empty
because the profile in this run has no description; Problem and Solution are filled from the canvas.)

`template_form_business_plan.json` — HTTP 200 (long text trimmed where marked)

```json
{
  "data": {
    "key": "business_plan",
    "name": "Business Plan",
    "description": "A full business plan.",
    "kind": "business_plan",
    "category": "Pitch",
    "sections": [
      "Executive Summary",
      "Problem",
      "Solution",
      "Market & Customer",
      "Business Model",
      "Go-to-Market",
      "Team",
      "Financials",
      "The Ask"
    ],
    "disclaimer": null,
    "form": {
      "template_key": "business_plan",
      "fields": [],
      "sections": [
        {
          "key": "executive_summary",
          "heading": "Executive Summary",
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": true
        },
        {
          "key": "problem",
          "heading": "Problem",
          "value": "- Traders lose money to slow cash handling\n- Card fraud",
          "state": "filled",
          "source": "business_builder",
          "ai_draftable": true
        },
        {
          "key": "solution",
          "heading": "Solution",
          "value": "- A mobile wallet for market traders",
          "state": "filled",
          "source": "business_builder",
          "ai_draftable": true
        },
        {
          "key": "market_customer",
          "heading": "Market & Customer",
          "value": "- Open-air market traders",
          "state": "filled",
          "source": "business_builder",
          "ai_draftable": true
        },
        {
          "key": "business_model",
          "heading": "Business Model",
          "value": "- Transaction fees (pricing basis: 1% per payment)",
          "state": "filled",
          "source": "business_builder",
          "ai_draftable": true
        },
        {
          "key": "go_to_market",
          "heading": "Go-to-Market",
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": true
        },
        {
          "key": "team",
          "heading": "Team",
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": false
        },
        {
          "key": "financials",
          "heading": "Financials",
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": true
        },
        {
          "key": "the_ask",
          "heading": "The Ask",
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": false
        }
      ],
      "unfilled_required": [],
      "disclaimer": null,
      "ai_available": true
    }
  },
  "meta": null
}
```


Reading it:

* `sections[].value` is the exact text `instantiate` will put in that section (a list becomes `- item` lines).
* `ai_draftable: true` means "if this section is still empty, an AI draft may write it". `false` for
  sections only the founder can know (Team, The Ask) and for every legal clause.
* `form.ai_available` is `false` when the template has nothing AI can draft (legal templates, meeting notes):
  hide the "Draft with AI" control.
* `unfilled_required` lists the **field** keys that are required and empty (always `[]` for templates with no fields).

A legal template adds **`fields`**, the inputs the clause text reuses (`{{key}}` in the clause):

`template_form_nda.json` — HTTP 200 (long text trimmed where marked)

```json
{
  "data": {
    "key": "nda",
    "name": "Non-Disclosure Agreement",
    "description": "A one-way NDA for sharing confidential information with a partner, investor or contractor.",
    "kind": "nda",
    "category": "Legal",
    "sections": [
      "Parties",
      "Purpose",
      "Confidential Information",
      "Obligations of the Receiving Party",
      "Exclusions",
      "Term",
      "Return or Destruction",
      "Remedies",
      "Governing Law",
      "Signatures"
    ],
    "disclaimer": "This document is a draft template provided for convenience and is not legal advice. Cofoundaz is not a law firm. Have a  \u2026[trimmed]",
    "form": {
      "template_key": "nda",
      "fields": [
        {
          "key": "disclosing_party",
          "label": "Disclosing party",
          "type": "text",
          "required": true,
          "value": "NovaPay",
          "state": "filled",
          "source": "profile",
          "ai_draftable": false
        },
        {
          "key": "receiving_party",
          "label": "Receiving party",
          "type": "text",
          "required": true,
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": false
        },
        {
          "key": "effective_date",
          "label": "Effective date",
          "type": "date",
          "required": false,
          "value": "2026-10-25",
          "state": "filled",
          "source": "system",
          "ai_draftable": false
        },
        {
          "key": "purpose",
          "label": "Purpose of sharing",
          "type": "longtext",
          "required": true,
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": false
        },
        {
          "key": "term_years",
          "label": "Confidentiality period (years)",
          "type": "text",
          "required": false,
          "value": "2",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "governing_law",
          "label": "Governing law",
          "type": "text",
          "required": true,
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": false
        }
      ],
      "sections": [
        {
          "key": "parties",
          "heading": "Parties",
          "value": "This Non-Disclosure Agreement is made on 2026-10-25 between NovaPay (the \"Disclosing Party\") and [Receiving party] (the  \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "purpose",
          "heading": "Purpose",
          "value": "The Disclosing Party will share confidential information with the Receiving Party solely for the following purpose: [Pur \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "confidential_information",
          "heading": "Confidential Information",
          "value": "\"Confidential Information\" means any non-public information the Disclosing Party shares in any form, including business  \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "obligations_of_the_receiving_party",
          "heading": "Obligations of the Receiving Party",
          "value": "The Receiving Party will: keep the Confidential Information secret using at least the care it uses for its own confident \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "exclusions",
          "heading": "Exclusions",
          "value": "These duties do not apply to information that: is or becomes public through no fault of the Receiving Party; was already \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "term",
          "heading": "Term",
          "value": "This agreement covers information shared from the effective date. The duties of confidentiality continue for 2 years aft \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "return_or_destruction",
          "heading": "Return or Destruction",
          "value": "On request, the Receiving Party will promptly return or destroy all Confidential Information and confirm in writing that \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "remedies",
          "heading": "Remedies",
          "value": "The Receiving Party acknowledges that a breach may cause harm that money alone cannot repair, and that the Disclosing Pa \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "governing_law",
          "heading": "Governing Law",
          "value": "This agreement is governed by the laws of [Governing law], and its courts have jurisdiction over any dispute.",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "signatures",
          "heading": "Signatures",
          "value": "Signed for NovaPay:\n\nName: ______________________\nTitle: ______________________\nSignature: ___________________\nDate: ___ \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        }
      ],
      "unfilled_required": [
        "receiving_party",
        "purpose",
        "governing_law"
      ],
      "disclaimer": "This document is a draft template provided for convenience and is not legal advice. Cofoundaz is not a law firm. Have a  \u2026[trimmed]",
      "ai_available": false
    }
  },
  "meta": null
}
```


Field `type` is `text`, `longtext` (use a textarea) or `date` (ISO `YYYY-MM-DD`, today by default). Required
fields the founder must fill are the ones in `unfilled_required`; the API does **not** refuse to create the
document while they are empty (the text carries a visible `[Receiving party]` bracket instead), so the FE
should block "Create document" until they are filled, or warn.

## 3. Create from a template — `POST /documents/templates/{key}/instantiate`

Request (all optional):

```json
{
  "title": "NovaPay business plan",
  "folder": "Fundraising",
  "values": { "receiving_party": "Bright Ventures Ltd" },
  "ai_draft": true,
  "ai_sections": ["go_to_market"]
}
```

* `values`: field keys from the template's `fields` only. Text only, trimmed, control characters removed,
  at most 500 characters (5000 for `longtext`). A `{{x}}` typed into a value is inserted literally.
* `ai_draft: true` queues an AI draft for **every empty draftable section**; `ai_sections` narrows it
  (and implies `ai_draft`). Naming a section that is not draftable is a 422.
* Success is **201** and the document is created **immediately**, whether or not AI is requested.

With AI requested (live; the long `form` is trimmed to its first entries):

`instantiate_business_plan_ai.json` — HTTP 201 (long text trimmed where marked)

```json
{
  "data": {
    "document": {
      "id": "cec804f2-8862-4a3b-b7f0-8f3b576615aa",
      "type": "document",
      "kind": "business_plan",
      "mime": "application/vnd.cofoundaz.document+json",
      "title": "NovaPay business plan",
      "status": "draft",
      "ai_generated": false,
      "folder": "Fundraising",
      "tags": [],
      "template_key": "business_plan",
      "disclaimer": null,
      "version": 1,
      "owner": {
        "id": "97ef66ef-8c63-4ebb-a8de-c10d50c71a2f",
        "name": "Ada Founder"
      },
      "share_count": 0,
      "updated_at": "2026-10-25T15:03:54.282168+00:00",
      "trashed_at": null,
      "purges_at": null,
      "sections": [
        {
          "id": "5c48f141-8232-479d-bfbd-b6748e9f5b14",
          "body": "",
          "heading": "Executive Summary"
        },
        {
          "id": "1a0c62cf-7938-440a-be06-791a4131cb7b",
          "body": "- Traders lose money to slow cash handling\n- Card fraud",
          "heading": "Problem"
        },
        {
          "id": "17d4e21d-d785-4dc3-8e0b-99eeae313dcc",
          "body": "- A mobile wallet for market traders",
          "heading": "Solution"
        },
        {
          "id": "621f2515-eca2-40d9-86d7-f3ff8a9e63c0",
          "body": "- Open-air market traders",
          "heading": "Market & Customer"
        },
        {
          "id": "35e65be9-5f1d-469d-ac57-2598f15435b7",
          "body": "- Transaction fees (pricing basis: 1% per payment)",
          "heading": "Business Model"
        },
        {
          "id": "01283ccb-be04-43d5-a041-c208d6f55bda",
          "body": "",
          "heading": "Go-to-Market"
        },
        {
          "id": "66c4c983-563f-4fed-a103-ab3dde91bc23",
          "body": "",
          "heading": "Team"
        },
        {
          "id": "f155add2-0c5f-4c4e-917d-fc5eb8e9e55d",
          "body": "",
          "heading": "Financials"
        },
        {
          "id": "5f8a4a7c-800e-4191-9ec9-41c9e99efa35",
          "body": "",
          "heading": "The Ask"
        }
      ]
    },
    "form": {
      "template_key": "business_plan",
      "fields": [],
      "sections": [
        {
          "key": "executive_summary",
          "heading": "Executive Summary",
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": true
        },
        {
          "key": "problem",
          "heading": "Problem",
          "value": "- Traders lose money to slow cash handling\n- Card fraud",
          "state": "filled",
          "source": "business_builder",
          "ai_draftable": true
        },
        {
          "key": "solution",
          "heading": "Solution",
          "value": "- A mobile wallet for market traders",
          "state": "filled",
          "source": "business_builder",
          "ai_draftable": true
        },
        {
          "key": "market_customer",
          "heading": "Market & Customer",
          "value": "- Open-air market traders",
          "state": "filled",
          "source": "business_builder",
          "ai_draftable": true
        },
        {
          "key": "business_model",
          "heading": "Business Model",
          "value": "- Transaction fees (pricing basis: 1% per payment)",
          "state": "filled",
          "source": "business_builder",
          "ai_draftable": true
        },
        {
          "key": "go_to_market",
          "heading": "Go-to-Market",
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": true
        },
        {
          "key": "team",
          "heading": "Team",
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": false
        },
        {
          "key": "financials",
          "heading": "Financials",
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": true
        },
        {
          "key": "the_ask",
          "heading": "The Ask",
          "value": "",
          "state": "empty",
          "source": null,
          "ai_draftable": false
        }
      ],
      "unfilled_required": [],
      "disclaimer": null,
      "ai_available": true
    },
    "ai": {
      "status": "queued",
      "job_id": "0aab2096-3db2-49b5-ace6-15450d26beaf",
      "sections": [
        "executive_summary",
        "go_to_market",
        "financials"
      ]
    }
  },
  "meta": null
}
```


`ai.status`:

| `ai.status` | Meaning | `job_id` | FE |
|---|---|---|---|
| `not_requested` | `ai_draft` was false | `null` | nothing |
| `queued` | a job will fill `ai.sections` | the job id | poll (§4) |
| `nothing_to_draft` | every draftable section was already filled | `null` | nothing to show |
| `over_budget` | the workspace's daily AI allowance is used up; **the document was still created**, no job exists | `null` | tell the user, see §9 |

**NOT LIVE:** the `nothing_to_draft` and `over_budget` bodies (shape from the unit tests, the same
`{status, job_id, sections}` object with `job_id: null` and `sections: []`).

A legal template with the caller's values (live; clause text trimmed). Note `form.fields[].source: "you"` for
what was supplied, `"profile"` for the party name taken from the startup, and `disclaimer` on the document:

`instantiate_nda.json` — HTTP 201 (long text trimmed where marked)

```json
{
  "data": {
    "document": {
      "id": "34bb977d-7a11-48e4-a98a-ab84b9998775",
      "type": "document",
      "kind": "nda",
      "mime": "application/vnd.cofoundaz.document+json",
      "title": "Non-Disclosure Agreement",
      "status": "draft",
      "ai_generated": false,
      "folder": null,
      "tags": [],
      "template_key": "nda",
      "disclaimer": "This document is a draft template provided for convenience and is not legal advice. Cofoun \u2026[trimmed]",
      "version": 1,
      "owner": {
        "id": "97ef66ef-8c63-4ebb-a8de-c10d50c71a2f",
        "name": "Ada Founder"
      },
      "share_count": 0,
      "updated_at": "2026-10-25T15:03:54.564669+00:00",
      "trashed_at": null,
      "purges_at": null,
      "sections": [
        {
          "id": "985a119f-bc29-4900-8a10-ae7942c776b7",
          "body": "This Non-Disclosure Agreement is made on 2026-10-25 between NovaPay (the \"Disclosing Party \u2026[trimmed]",
          "heading": "Parties"
        },
        {
          "id": "bf3b7a09-ffb3-4caa-8e77-2ee068ba030d",
          "body": "The Disclosing Party will share confidential information with the Receiving Party solely f \u2026[trimmed]",
          "heading": "Purpose"
        },
        {
          "id": "631d0390-8adc-4171-b69b-42e4b58af5fc",
          "body": "\"Confidential Information\" means any non-public information the Disclosing Party shares in \u2026[trimmed]",
          "heading": "Confidential Information"
        },
        {
          "id": "ccdcca76-e71e-4582-924f-3fa6093d109c",
          "body": "The Receiving Party will: keep the Confidential Information secret using at least the care \u2026[trimmed]",
          "heading": "Obligations of the Receiving Party"
        },
        {
          "id": "919ca025-7a33-47b5-853c-1ea89f2c5c3d",
          "body": "These duties do not apply to information that: is or becomes public through no fault of th \u2026[trimmed]",
          "heading": "Exclusions"
        },
        {
          "id": "f91be9cb-63b6-4c1f-aa0f-1acb98e0cffd",
          "body": "This agreement covers information shared from the effective date. The duties of confidenti \u2026[trimmed]",
          "heading": "Term"
        },
        {
          "id": "964ecfaa-5c3c-47fa-a82c-e744ea71edf4",
          "body": "On request, the Receiving Party will promptly return or destroy all Confidential Informati \u2026[trimmed]",
          "heading": "Return or Destruction"
        },
        {
          "id": "3e3e6394-5f4b-42df-ad1d-50feddc397bb",
          "body": "The Receiving Party acknowledges that a breach may cause harm that money alone cannot repa \u2026[trimmed]",
          "heading": "Remedies"
        },
        {
          "id": "7b55d319-0423-4946-b855-dd5d2f7a6e7f",
          "body": "This agreement is governed by the laws of Lagos State, Nigeria, and its courts have jurisd \u2026[trimmed]",
          "heading": "Governing Law"
        },
        {
          "id": "cbe4828b-e906-435b-9d99-951e4fcc119e",
          "body": "Signed for NovaPay:\n\nName: ______________________\nTitle: ______________________\nSignature: \u2026[trimmed]",
          "heading": "Signatures"
        }
      ]
    },
    "form": {
      "template_key": "nda",
      "fields": [
        {
          "key": "disclosing_party",
          "label": "Disclosing party",
          "type": "text",
          "required": true,
          "value": "NovaPay",
          "state": "filled",
          "source": "profile",
          "ai_draftable": false
        },
        {
          "key": "receiving_party",
          "label": "Receiving party",
          "type": "text",
          "required": true,
          "value": "Bright Ventures Ltd",
          "state": "filled",
          "source": "you",
          "ai_draftable": false
        },
        {
          "key": "effective_date",
          "label": "Effective date",
          "type": "date",
          "required": false,
          "value": "2026-10-25",
          "state": "filled",
          "source": "system",
          "ai_draftable": false
        },
        {
          "key": "purpose",
          "label": "Purpose of sharing",
          "type": "longtext",
          "required": true,
          "value": "Evaluating a seed investment",
          "state": "filled",
          "source": "you",
          "ai_draftable": false
        },
        {
          "key": "term_years",
          "label": "Confidentiality period (years)",
          "type": "text",
          "required": false,
          "value": "2",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "governing_law",
          "label": "Governing law",
          "type": "text",
          "required": true,
          "value": "Lagos State, Nigeria",
          "state": "filled",
          "source": "you",
          "ai_draftable": false
        }
      ],
      "sections": [
        {
          "key": "parties",
          "heading": "Parties",
          "value": "This Non-Disclosure Agreement is made on 2026-10-25 between NovaPay (the \"Disclosing Party \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "purpose",
          "heading": "Purpose",
          "value": "The Disclosing Party will share confidential information with the Receiving Party solely f \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "confidential_information",
          "heading": "Confidential Information",
          "value": "\"Confidential Information\" means any non-public information the Disclosing Party shares in \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "obligations_of_the_receiving_party",
          "heading": "Obligations of the Receiving Party",
          "value": "The Receiving Party will: keep the Confidential Information secret using at least the care \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "exclusions",
          "heading": "Exclusions",
          "value": "These duties do not apply to information that: is or becomes public through no fault of th \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "term",
          "heading": "Term",
          "value": "This agreement covers information shared from the effective date. The duties of confidenti \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "return_or_destruction",
          "heading": "Return or Destruction",
          "value": "On request, the Receiving Party will promptly return or destroy all Confidential Informati \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "remedies",
          "heading": "Remedies",
          "value": "The Receiving Party acknowledges that a breach may cause harm that money alone cannot repa \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "governing_law",
          "heading": "Governing Law",
          "value": "This agreement is governed by the laws of Lagos State, Nigeria, and its courts have jurisd \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        },
        {
          "key": "signatures",
          "heading": "Signatures",
          "value": "Signed for NovaPay:\n\nName: ______________________\nTitle: ______________________\nSignature: \u2026[trimmed]",
          "state": "filled",
          "source": "template",
          "ai_draftable": false
        }
      ],
      "unfilled_required": [],
      "disclaimer": "This document is a draft template provided for convenience and is not legal advice. Cofoun \u2026[trimmed]",
      "ai_available": false
    },
    "ai": {
      "status": "not_requested",
      "job_id": null,
      "sections": []
    }
  },
  "meta": null
}
```


Errors (live):

An unknown field key:

`instantiate_unknown_field_422.json` — HTTP 422

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "That isn't a field on this template.",
    "field_errors": [
      {
        "field": "values.nonsense",
        "message": "That isn't a field on this template."
      }
    ]
  }
}
```


AI requested on a template with nothing AI can draft:

`instantiate_nda_ai_422.json` — HTTP 422

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "AI can't draft sections of this template.",
    "field_errors": []
  }
}
```


(A non-text value, an over-long value and an unknown `ai_sections` key are also `422 VALIDATION_ERROR` with the
offending name in `field_errors[].field` — `values.<key>` or `ai_sections`; **NOT LIVE**, unit-tested.)
An unknown template `key` is `404 NOT_FOUND`; a non F/TM role is `403` before anything else.

## 4. The AI draft job

The job is an ordinary worker job: poll **`GET /jobs/{job_id}`** (readable by any active member of the
workspace; no workspace header needed). Suggested cadence: every 2 s for the first 10 s, then every 5 s, give up
after about 2 minutes and show "still working, check back".

Queued:

`job_queued.json` — HTTP 200

```json
{
  "data": {
    "id": "0aab2096-3db2-49b5-ace6-15450d26beaf",
    "type": "ai.documents.template_draft",
    "status": "queued",
    "result": null,
    "error": null
  },
  "meta": null
}
```


Finished:

`job_done.json` — HTTP 200

```json
{
  "data": {
    "id": "0aab2096-3db2-49b5-ace6-15450d26beaf",
    "type": "ai.documents.template_draft",
    "status": "succeeded",
    "result": {
      "failed": [],
      "reason": null,
      "status": "ready",
      "drafted": [
        "executive_summary",
        "go_to_market",
        "financials"
      ],
      "skipped": [],
      "document_version": 2
    },
    "error": null
  },
  "meta": null
}
```


Job `status` values: `queued`, `running`, `succeeded`, `failed`, `cancelled`. **The outcome is in
`result.status`, not in the job `status`:** a draft that ran but could not finish still has job
`status: "succeeded"`.

| `result.status` | Meaning | FE |
|---|---|---|
| `ready` | every requested section was drafted (or skipped, see below) | reload the document |
| `partial` | some sections drafted, `failed` lists the rest | reload, show "N sections could not be drafted. Try again" with the retry in §5 |
| `failed` | nothing was drafted (`reason`: `generation_failed` or `empty_draft`) | show an error + retry |
| `over_budget` | the daily AI allowance ran out before or during the job (`reason: "over_budget"`) | tell the user; retry tomorrow |
| `gone` | the document was trashed or deleted while the job waited | ignore |

`result` also has `drafted`, `skipped` and `failed` (section keys) and `document_version` (the new version,
`null` if nothing was written). **`skipped`** are sections the founder typed into while the job was queued:
the AI **never overwrites existing text**.

**NOT LIVE:** `partial`, `failed`, `over_budget` and `gone` results (the live stack uses the stub model, which
cannot fail). Their shapes are the same `result` object (`status, drafted, skipped, failed, reason,
document_version`), taken from `tests/api/test_documents_templates.py`. A job that crashes outside the handler
ends with job `status: "failed"` and an `error` string, as for every other job type.

The job runs the same metered LLM path as the business plan and counts against the workspace's daily token
budget. On provider errors the worker retries with backoff before reporting `failed`, so a `failed` result can
take a few minutes to appear.

### What the FE must do when the job finishes

The draft **bumps the document version** (a new snapshot "AI drafted: …" with no human author, visible in
`GET /documents/{id}/versions`) and sets `ai_generated: true`. If the founder has the editor open, their next
save is a `409 DOCUMENT_VERSION_CONFLICT`. So: when `result.document_version` is not `null`, reload the
document (or show the existing "Reload latest" banner) rather than letting them save over the AI text.

`document_after_ai.json` — HTTP 200 (long text trimmed where marked)

```json
{
  "data": {
    "id": "cec804f2-8862-4a3b-b7f0-8f3b576615aa",
    "type": "document",
    "kind": "business_plan",
    "mime": "application/vnd.cofoundaz.document+json",
    "title": "NovaPay business plan",
    "status": "draft",
    "ai_generated": true,
    "folder": "Fundraising",
    "tags": [],
    "template_key": "business_plan",
    "disclaimer": null,
    "version": 2,
    "owner": {
      "id": "97ef66ef-8c63-4ebb-a8de-c10d50c71a2f",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "updated_at": "2026-10-25T15:03:54.482766+00:00",
    "trashed_at": null,
    "purges_at": null,
    "sections": [
      {
        "id": "5c48f141-8232-479d-bfbd-b6748e9f5b14",
        "body": "[stub-llm] AI-generated assessment narrative.",
        "heading": "Executive Summary"
      },
      {
        "id": "1a0c62cf-7938-440a-be06-791a4131cb7b",
        "body": "- Traders lose money to slow cash handling\n- Card fraud",
        "heading": "Problem"
      },
      {
        "id": "17d4e21d-d785-4dc3-8e0b-99eeae313dcc",
        "body": "- A mobile wallet for market traders",
        "heading": "Solution"
      },
      {
        "id": "621f2515-eca2-40d9-86d7-f3ff8a9e63c0",
        "body": "- Open-air market traders",
        "heading": "Market & Customer"
      },
      {
        "id": "35e65be9-5f1d-469d-ac57-2598f15435b7",
        "body": "- Transaction fees (pricing basis: 1% per payment)",
        "heading": "Business Model"
      },
      {
        "id": "01283ccb-be04-43d5-a041-c208d6f55bda",
        "body": "[stub-llm] AI-generated assessment narrative.",
        "heading": "Go-to-Market"
      },
      {
        "id": "66c4c983-563f-4fed-a103-ab3dde91bc23",
        "body": "",
        "heading": "Team"
      },
      {
        "id": "f155add2-0c5f-4c4e-917d-fc5eb8e9e55d",
        "body": "[stub-llm] AI-generated assessment narrative.",
        "heading": "Financials"
      },
      {
        "id": "5f8a4a7c-800e-4191-9ec9-41c9e99efa35",
        "body": "",
        "heading": "The Ask"
      }
    ]
  },
  "meta": null
}
```


## 5. Retry a draft — `POST /documents/{id}/ai-draft`

Body `{}` (all empty AI-draftable sections) or `{"sections": ["go_to_market"]}`. **202** with the same `ai`
object as §3 (`status: "queued"`). Rate limit: 20 per hour per caller. (`instantiate` is limited to 60 per hour.)

Errors:

* Nothing left to draft (live):

`ai_draft_nothing_left_422.json` — HTTP 422

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "There are no empty sections for AI to draft.",
    "field_errors": []
  }
}
```


* The document was not made from a business template, or is trashed: `422 VALIDATION_ERROR`
  ("AI can only draft documents made from a business template.") / `404`. **NOT LIVE**, unit-tested.
* A draft job is already queued or running for this document: **`409 AI_DRAFT_IN_PROGRESS`** ("An AI draft is already running for this document."). Wait for the job (§4). **NOT LIVE**, unit-tested.
* Over the daily AI allowance: **`429 AI_BUDGET_EXHAUSTED`**, message "Today's AI allowance for this workspace is
  used up. Try again tomorrow." **NOT LIVE**, unit-tested.

## 6. Export — `POST /documents/{id}/export`

Body `{"format": "pdf"}` or `{"format": "docx"}`. The response is the **file** (the same shape as every file
row), not a download. Fetch the bytes the usual way: `GET /documents/files/{id}/download` returns a 5-minute
signed link.

New export (**201**):

`export_pdf.json` — HTTP 201

```json
{
  "data": {
    "id": "0f069faa-155d-40c1-ad14-561390cab32d",
    "filename": "Non-Disclosure Agreement.pdf",
    "content_type": "application/pdf",
    "mime": "application/pdf",
    "type": "file",
    "origin": "export",
    "source_document_id": "34bb977d-7a11-48e4-a98a-ab84b9998775",
    "source_version": 1,
    "size_bytes": 4444,
    "folder": null,
    "tags": [],
    "owner": {
      "id": "97ef66ef-8c63-4ebb-a8de-c10d50c71a2f",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "scan_status": "clean",
    "uploaded_at": "2026-10-25T15:03:54.614057+00:00",
    "trashed_at": null,
    "purges_at": null
  },
  "meta": null
}
```


Exporting the same version, with the same title and folder, in the same format again returns the **same file** with **200**, nothing is
created:

`export_pdf_again_200.json` — HTTP 200

```json
{
  "data": {
    "id": "0f069faa-155d-40c1-ad14-561390cab32d",
    "filename": "Non-Disclosure Agreement.pdf",
    "content_type": "application/pdf",
    "mime": "application/pdf",
    "type": "file",
    "origin": "export",
    "source_document_id": "34bb977d-7a11-48e4-a98a-ab84b9998775",
    "source_version": 1,
    "size_bytes": 4444,
    "folder": null,
    "tags": [],
    "owner": {
      "id": "97ef66ef-8c63-4ebb-a8de-c10d50c71a2f",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "scan_status": "clean",
    "uploaded_at": "2026-10-25T15:03:54.614057+00:00",
    "trashed_at": null,
    "purges_at": null
  },
  "meta": null
}
```


* A new version of the document (any save, restore or AI draft), or a rename or move, makes the next export a new file (201); the old
  export stays in the library.
* DOCX works the same way (live):

`export_docx.json` — HTTP 201

```json
{
  "data": {
    "id": "3385527b-9a00-4b32-84c6-e04aeaf5ad11",
    "filename": "Non-Disclosure Agreement.docx",
    "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "type": "file",
    "origin": "export",
    "source_document_id": "34bb977d-7a11-48e4-a98a-ab84b9998775",
    "source_version": 1,
    "size_bytes": 38319,
    "folder": null,
    "tags": [],
    "owner": {
      "id": "97ef66ef-8c63-4ebb-a8de-c10d50c71a2f",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "scan_status": "clean",
    "uploaded_at": "2026-10-25T15:03:54.655989+00:00",
    "trashed_at": null,
    "purges_at": null
  },
  "meta": null
}
```


* The download link (live; the `url` is a signed link, valid 5 minutes):

`export_pdf_download_link.json` — HTTP 200

```json
{
  "data": {
    "url": "http://127.0.0.1:8031/api/v1/documents/files/download?token=REDACTED_TOKEN",
    "expires_at": "2026-10-25T15:08:54+00:00",
    "content_type": "application/pdf",
    "filename": "Non-Disclosure Agreement.pdf"
  },
  "meta": null
}
```


The downloaded bytes begin `%PDF-1.4` (live: `export_pdf_bytes.json`):

`export_pdf_bytes.json` — HTTP n/a (test-side check)

```json
{
  "starts_with": "%PDF-1.4",
  "size": 4444
}
```


and the DOCX text begins with the title, the disclaimer paragraph, then the sections (live: first paragraphs of
the exported legal draft):

`export_docx_paragraphs.json` — HTTP n/a (test-side check)

```json
{
  "first_paragraphs": [
    "Non-Disclosure Agreement",
    "This document is a draft template provided for convenience and is not legal advice. Cofoundaz is not a law firm. Have a qualified lawyer review it before you sign or rely on it.",
    "Parties",
    "This Non-Disclosure Agreement is made on 2026-10-25 between NovaPay (the \"Disclosing Party\") and Bright Ventures Ltd (the \"Receiving Party\")."
  ]
}
```


### How an export behaves

* It is a normal private file: stored privately, scanned like an upload (`scan_status`; a `blocked` export gets
  no download link, `409 FILE_NOT_CLEAN`), same 15 MB ceiling; a document with more than 2000 lines or 200,000 characters is refused
  (`422 VALIDATION_ERROR` "This document is too large to export."), same Trash rules.
* It lands in the **same folder as the document** and is named `<title>.pdf` / `<title>.docx`.
* `origin: "export"`, `source_document_id`, `source_version` identify it. The file **outlives its document**:
  if the document is purged, `source_document_id` becomes `null` and `origin` stays `"export"`.
* **Send for signature:** an exported file can be sent for signature exactly like an upload (live):

`export_sent_for_signature.json` — HTTP 201

```json
{
  "data": {
    "id": "b6170852-a01a-4e04-9f86-6144a89c7ee8",
    "title": "Non-Disclosure Agreement.pdf",
    "status": "awaiting",
    "file_id": "0f069faa-155d-40c1-ad14-561390cab32d",
    "filename": "Non-Disclosure Agreement.pdf",
    "signed_count": 0,
    "total": 1,
    "expires_at": "2026-11-08T15:03:54.760678+00:00",
    "completed_at": null,
    "created_at": "2026-10-25T15:03:54.757240+00:00",
    "signers": [
      {
        "email": "signer@example.com",
        "name": "Bright Signer",
        "position": 0,
        "status": "pending",
        "signed_at": null,
        "signed_name": null,
        "signed_ip": null,
        "signed_user_agent": null
      }
    ],
    "signer_links": [
      "http://localhost:3000/sign/REDACTED_TOKEN"
    ]
  },
  "meta": null
}
```


  Send the **PDF** to signers. Sending a DOCX export for signature was not exercised (**NOT LIVE**); prefer the PDF.
* **Legal drafts print their disclaimer**: a boxed notice at the top of page 1 and a footer line on every PDF page;
  in DOCX a paragraph under the title and a footer line.
* PDF text uses the standard Latin-1 font: characters outside it (for example Yoruba dot-below vowels, or
  non-Latin scripts) are reduced to their base letter (or `?`) in the PDF. DOCX keeps the original text.
* There is no download of the **document itself** as a single call; export first, then download the file.

Library listing: exports show up in `GET /documents/files` with the new fields (live; the list is a file list,
so there is one list of files with a `type` and an `origin`):

`files_list_with_exports.json` — HTTP 200 (only `data.files` shown)

```json
[
  {
    "id": "3385527b-9a00-4b32-84c6-e04aeaf5ad11",
    "filename": "Non-Disclosure Agreement.docx",
    "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "type": "file",
    "origin": "export",
    "source_document_id": "34bb977d-7a11-48e4-a98a-ab84b9998775",
    "source_version": 1,
    "size_bytes": 38319,
    "folder": null,
    "tags": [],
    "owner": {
      "id": "97ef66ef-8c63-4ebb-a8de-c10d50c71a2f",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "scan_status": "clean",
    "uploaded_at": "2026-10-25T15:03:54.655989+00:00",
    "trashed_at": null,
    "purges_at": null
  },
  {
    "id": "0f069faa-155d-40c1-ad14-561390cab32d",
    "filename": "Non-Disclosure Agreement.pdf",
    "content_type": "application/pdf",
    "mime": "application/pdf",
    "type": "file",
    "origin": "export",
    "source_document_id": "34bb977d-7a11-48e4-a98a-ab84b9998775",
    "source_version": 1,
    "size_bytes": 4444,
    "folder": null,
    "tags": [],
    "owner": {
      "id": "97ef66ef-8c63-4ebb-a8de-c10d50c71a2f",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "scan_status": "clean",
    "uploaded_at": "2026-10-25T15:03:54.614057+00:00",
    "trashed_at": null,
    "purges_at": null
  }
]
```


Errors (live):

* A role that is not founder/team member:

`export_mentor_403.json` — HTTP 403

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```


* A trashed or unknown document (or one in another workspace):

`export_trashed_404.json` — HTTP 404

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```


* A format other than `pdf` / `docx`:

`export_bad_format_422.json` — HTTP 422

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "format",
        "message": "Input should be 'pdf' or 'docx'"
      }
    ]
  }
}
```


Rate limit: 20 exports per minute.

## 7. Legal drafts: the disclaimer (D-DOC-4)

The four legal templates are structured drafts, **not legal advice**. The API returns the banner text; the FE
must show it — never hard-code a copy of it:

* `disclaimer` on the template (list, read), on `form.disclaimer`, on the document (`GET /documents/{id}`, rows
  of `GET /documents`, and the instantiate response) and on the public token view (`GET /shared/{token}`;
  **NOT LIVE** for that last one — it is the same `disclaimer` field added to the public field list, covered by a
  unit test).
* Show it as a **persistent banner above the form and above the editor** whenever `disclaimer` is a string.
  It is not dismissible permanently.

Text, as returned:

> This document is a draft template provided for convenience and is not legal advice. Cofoundaz is not a law
> firm. Have a qualified lawyer review it before you sign or rely on it.

The clause text is a placeholder until a lawyer supplies the final wording (D-DOC-4): it will change without an
API change. Do not copy clause text into the FE.

## 8. What is the same

`PUT /documents/{id}` (editor save) and all the BE-A/BE-B rules (versions, trash, folders, grants, search,
shared-with-me) are unchanged; see `docs/fe-integration-guide-documents-library.md`. Existing documents get
`type: "document"` and `disclaimer` (null unless legal); existing files get `type: "file"`,
`origin: "upload"` and null source fields.

## 9. UX consequences the FE must surface

1. **Over the AI allowance on create:** the document is created anyway. Show it, plus a notice "AI drafting
   isn't available right now (daily limit reached)" when `ai.status` is `over_budget`. Do not retry in a loop.
2. **AI never overwrites:** after a job, tell the user which sections were drafted (`drafted`) and that
   sections they edited were left alone (`skipped`).
3. **Reload after AI:** see §4. The version number changes under an open editor.
4. **Brackets are visible gaps:** `[Receiving party]` in a legal clause means the field is empty. Block or warn
   before the user exports or sends such a document.
5. **Re-export is idempotent:** if a user clicks Export twice they get the same file the second time (200).
   Say "Already exported" rather than "Exported" when the status is 200.
6. **A trashed document cannot be exported** (404). Exports already made stay in the library.
7. **PDF accents:** characters outside Latin-1 lose their accents in the PDF only (§6).
8. **The disclaimer must be visible** whenever it is a string (§7).

## 10. Not built here (so the FE does not wait for it)

* **Sharing an exported file** (a share link or grant on a file): files are still not shareable; BE-B grants and
  token links are on rich documents only. Share the source document, or send the export for signature.
* Field placement for e-signature, executed-PDF stamping and sign order are BE-D.
* The clause text of the legal drafts (counsel-reviewed wording) is a later content change, not an API change.

## 11. Verification table

| Behaviour | Verified |
|---|---|
| Catalog with the four categories and the four legal templates | **live** (`templates_list`) |
| Form state: auto-fill from profile + Business Builder, empty vs filled, `ai_draftable` | **live** (`template_form_business_plan`) |
| Legal form: fields, required, defaults, today's date, `source` values | **live** (`template_form_nda`, `instantiate_nda`) |
| Instantiate, with and without AI; `ai.status: queued` | **live** (`instantiate_business_plan_ai`, `instantiate_nda`) |
| AI job through the worker (stub model): queued → succeeded, `result.status: ready`, version bump, only empty sections filled, autofill untouched | **live** (`job_queued`, `job_done`, `document_after_ai`) |
| Retry with nothing left; AI on a non-draftable template; unknown field | **live** (422s above) |
| AI `partial`, `failed`, `over_budget`, `gone` job results; `nothing_to_draft` / `over_budget` on create; `429 AI_BUDGET_EXHAUSTED` | **not live** (fake-client unit tests; the live stack uses the stub model) |
| Export PDF/DOCX: 201, idempotent 200, scan clean, download link, bytes start `%PDF`, DOCX text and disclaimer | **live** |
| Export sent for signature | **live** (`export_sent_for_signature`) |
| Export refused: mentor 403, trashed 404, bad format 422 | **live** |
| Blocked scan verdict, oversized document, source purged (`source_document_id` → null), folder inheritance, trashed export not reused | **not live** (unit/API tests) |
| Role matrix: every non-F/TM role 403 on the new routes | unit/API (`tests/api/test_documents_access.py`); mentor live |
| Disclaimer on the public share view | **not live** (unit test) |
| `409 AI_DRAFT_IN_PROGRESS`, rename making a new export, >2000-line refusal, non-ASCII filename download | **not live** (API tests) |
| Daily-budget error text, rate limits (20/min export, 20/h re-draft) | **not live** |
