# FE Integration Guide: Funding Hub (Module 14, slice D: term sheets)

Every JSON body below is pasted **verbatim** from live captures taken by `e2e/test_funding_term_sheets.py`
against a real server (`e2e/_captures/funding_term_sheets/*.json`; the capture name is given above each
block). Nothing is retyped from the schema. IDs, names and emails are throwaway values from that run. Where a
body was trimmed it says so. **One thing is faked in the live run: the language model.** The extraction job
and the plain-language wording job were drained in the test process with a canned model, so the numbers it
"extracted" and the wording text in `ai_text` are the fake's, not a real model's. Everything else is the real
API, queue, PDF text extraction, validation, rules table, confirm step, comparison and audit trail. **What is
not covered live is marked inline and listed in the verification table at the end.**

Scope: the **Term sheets** page (`/funding/term-sheets`): add an offer by hand or from a PDF, review and
confirm extracted values, see flags, compare offers side by side. Design:
`cofoundaz/docs/superpowers/specs/2026-11-05-funding-hub-design.md` section 6. Calls: D-FUND-5 **founder
only**, D-FUND-6 deterministic rules table for the flags, AI only extracts (founder confirms) and words each
flag. Funding stays hidden in the sidebar until all slices ship.

Base path `/api/v1/funding/term-sheets`. Every route needs `Authorization: Bearer <token>` and
`X-Workspace-Id`. Success bodies are `{"data": ..., "meta": null}`; errors are
`{"error": {"code", "message", "field_errors"}}`.

## 1. Who can call what

| Role | Every route in this guide |
|---|---|
| founder | allowed |
| team_member, mentor, business_consultant, accountant, legal_advisor, investor | **403** |

**Team members get 403 too.** Hide the Term sheets tab for anyone who is not the founder. A team member joined
through a real invitation was refused live:

capture `team_member_403`
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

Plan module `funding` (Scale): after the role check, **402** `PLAN_REQUIRED` when `PLAN_ENFORCEMENT` is on and
the workspace lacks it (off today). *Not captured live (enforcement is off in the e2e server); unit-tested in
`tests/api/test_funding_term_sheets_api.py`.*

## 2. Conventions

- **Money** is an integer in **minor units** of the round currency (`currency` in the list and comparison:
  `NGN` or `USD`). The round currency cannot change once any term sheet states a valuation or investment (or
  the cap table has amounts): 409 `CAP_TABLE_HAS_AMOUNTS`.
- **Rates are basis points** (1% = 100): `option_pool_pre_bps`. **Multiples are hundredths**: `liq_pref_x100`
  100 = 1.0x, 150 = 1.5x. Show `value / 100` with an `x`.
- **Every term is optional.** A term the sheet does not state is simply absent (`null` and absent mean "not
  stated"). **An unstated term is never flagged and never compared.**
- The page must show the `disclaimer` from the API ("Negotiate with clarity. ... not legal or financial
  advice"). Flags are wording about being *unusual for this stage*, never a verdict.

### The terms

| Key | Meaning |
|---|---|
| `stage` | `pre_seed`, `seed`, `series_a`, `series_b`, `later`, `other` (drives the board seats rule) |
| `pre_money_minor`, `investment_minor` | valuation before the money, and the new money |
| `option_pool_pre_bps` | the option pool as a share of the pre-money fully diluted capitalisation |
| `liq_pref_x100` | liquidation preference multiple |
| `participating` / `participation_cap_x100` | participating preferred, and its total cap (only with `participating: true`) |
| `investor_board_seats`, `founder_board_seats` | seats |
| `pro_rata` | `none`, `standard`, `super` |
| `exclusivity_days` | the no-shop period |
| `anti_dilution` | `none`, `broad_based`, `narrow_based`, `full_ratchet` |
| `notes` | free text (typed sheets) |

## 3. The sheet and its states

`status` is one of `extracting`, `needs_confirmation`, `confirmed`, `failed`. **Only `confirmed` sheets have
`terms`, `flags`, can be compared, edited, or are announced.** A typed sheet is `confirmed` on save.

| status | what the page shows | what the FE does |
|---|---|---|
| `extracting` | "Reading your term sheet..." | poll `GET /term-sheets/{id}` every 3 to 5 seconds (it normally takes seconds) |
| `needs_confirmation` | the proposed values beside the source quotes, every field editable; **warn first when `currency_mismatch` is true** | `POST /{id}/confirm` with the final values |
| `confirmed` | the sheet, its flags, in the comparison | edit with `PATCH` |
| `failed` | `error`: `no_text` (scanned or image-only PDF), `unreadable`, `file_missing`, `generation_failed`, `over_budget`, `job_failed` | offer "Enter the terms by hand" or "Try again" (a new extract of the same file is allowed after a failure) |

Fields of every sheet in `list`/`detail`:

capture `list`
```json
{
  "data": {
    "items": [
      {
        "id": "76b8924d-ca2a-4c69-9b25-139c5ebb89f9",
        "label": "Gamma Partners",
        "status": "confirmed",
        "error": null,
        "investor_id": "7c6f13be-06f0-4f8e-bb2c-6f4ab195dbf8",
        "investor_name": "Tolu Bello",
        "source_file_id": "261f7916-7106-4858-8bdc-6f10045b3e8b",
        "source_filename": "gamma-term-sheet.pdf",
        "job_id": "1f057efd-df5f-4c18-8fc1-b313c5258da5",
        "extracted_by_ai": true,
        "terms": {
          "stage": "seed",
          "pro_rata": "super",
          "anti_dilution": "full_ratchet",
          "liq_pref_x100": 200,
          "participating": true,
          "pre_money_minor": 40000000000,
          "exclusivity_days": 45,
          "investment_minor": 10000000000,
          "founder_board_seats": 2,
          "option_pool_pre_bps": 1250,
          "investor_board_seats": 1,
          "participation_cap_x100": 300
        },
        "flags": [
          {
            "rule": "liq_pref_above_1x",
            "term": "liq_pref_x100",
            "text": "The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
            "title": "Liquidation preference above 1x",
            "value": 200,
            "ai_text": null,
            "threshold": "1x"
          },
          {
            "rule": "participating_preferred",
            "term": "participating",
            "text": "The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
            "title": "Participating preferred",
            "value": true,
            "ai_text": null,
            "threshold": "non-participating"
          },
          {
            "rule": "super_pro_rata",
            "term": "pro_rata",
            "text": "The investor can buy more than their share of future rounds, which can crowd out new investors.",
            "title": "Super pro-rata",
            "value": "super",
            "ai_text": null,
            "threshold": "standard pro-rata"
          },
          {
            "rule": "full_ratchet",
            "term": "anti_dilution",
            "text": "If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
            "title": "Full-ratchet anti-dilution",
            "value": "full_ratchet",
            "ai_text": null,
            "threshold": "broad-based weighted average"
          }
        ],
        "flag_count": 4,
        "confirmed_at": "2026-11-06T21:44:46.590650+00:00",
        "created_at": "2026-11-06T21:44:46.230662+00:00"
      },
      {
        "id": "1fef9738-f943-4891-96f1-9b826ee2cd6c",
        "label": "Beta Capital",
        "status": "confirmed",
        "error": null,
        "investor_id": null,
        "investor_name": null,
        "source_file_id": null,
        "source_filename": null,
        "job_id": null,
        "extracted_by_ai": false,
        "terms": {
          "stage": "seed",
          "pro_rata": "super",
          "anti_dilution": "full_ratchet",
          "liq_pref_x100": 200,
          "participating": true,
          "pre_money_minor": 50000000000,
          "exclusivity_days": 60,
          "investment_minor": 10000000000,
          "option_pool_pre_bps": 1800,
          "investor_board_seats": 2,
          "participation_cap_x100": 300
        },
        "flags": [
          {
            "rule": "liq_pref_above_1x",
            "term": "liq_pref_x100",
            "text": "The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
            "title": "Liquidation preference above 1x",
            "value": 200,
            "ai_text": "In plain words: The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
            "threshold": "1x"
          },
          {
            "rule": "participating_preferred",
            "term": "participating",
            "text": "The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
            "title": "Participating preferred",
            "value": true,
            "ai_text": "In plain words: The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
            "threshold": "non-participating"
          },
          {
            "rule": "option_pool_above_15pct",
            "term": "option_pool_pre_bps",
            "text": "The option pool is 18% of the pre-money. Because it sits in the pre-money, founders carry all of that dilution.",
            "title": "Large option pool in the pre-money",
            "value": 1800,
            "ai_text": "In plain words: The option pool is 18% of the pre-money. Because it sits in the pre-money, founders carry all of that dilution.",
            "threshold": "15% of pre-money"
          },
          {
            "rule": "exclusivity_above_45_days",
            "term": "exclusivity_days",
            "text": "You agree not to talk to other investors for 60 days. Up to 45 days is common.",
            "title": "Long exclusivity (no-shop)",
            "value": 60,
            "ai_text": "In plain words: You agree not to talk to other investors for 60 days. Up to 45 days is common.",
            "threshold": "45 days"
          },
          {
            "rule": "investor_board_seats_early",
            "term": "investor_board_seats",
            "text": "The investor gets 2 board seats on a seed round. At this stage one seat, or an observer, is more common.",
            "title": "More than one investor board seat on an early round",
            "value": 2,
            "ai_text": "In plain words: The investor gets 2 board seats on a seed round. At this stage one seat, or an observer, is more common.",
            "threshold": "1 investor seat"
          },
          {
            "rule": "super_pro_rata",
            "term": "pro_rata",
            "text": "The investor can buy more than their share of future rounds, which can crowd out new investors.",
            "title": "Super pro-rata",
            "value": "super",
            "ai_text": "In plain words: The investor can buy more than their share of future rounds, which can crowd out new investors.",
            "threshold": "standard pro-rata"
          },
          {
            "rule": "full_ratchet",
            "term": "anti_dilution",
            "text": "If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
            "title": "Full-ratchet anti-dilution",
            "value": "full_ratchet",
            "ai_text": "In plain words: If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
            "threshold": "broad-based weighted average"
          }
        ],
        "flag_count": 7,
        "confirmed_at": "2026-11-06T21:44:45.698312+00:00",
        "created_at": "2026-11-06T21:44:45.686365+00:00"
      },
      {
        "...": "more sheets, same shape"
      }
    ],
    "currency": "NGN",
    "disclaimer": "Negotiate with clarity. This is an automated reading of the terms you entered, not legal or financial advice. Ask a lawyer to review a real term sheet before you sign."
  },
  "meta": null
}
```

`flags[]` (confirmed sheets only): `rule`, `term`, `title`, `value`, `threshold`, `text` (the fixed wording, always
present) and `ai_text` (a plainer rewording, `null` until the optional wording job finishes or when it
failed). **Show `ai_text` when present, else `text`.** Keep `text` available ("the exact rule") because the
rewording is the model's.

## 4. Type a sheet in

`POST /term-sheets` `{label, investor_id?, terms}` returns 201 `confirmed`. A clean sheet has no flags:

capture `create_typed_clean`
```json
{
  "data": {
    "id": "36aafb75-d143-4a40-ae95-44f56669e374",
    "label": "Alpha Ventures",
    "status": "confirmed",
    "error": null,
    "investor_id": "02848807-a8db-46af-9fb5-14043c5da9ee",
    "investor_name": "Tolu Bello",
    "source_file_id": null,
    "source_filename": null,
    "job_id": null,
    "extracted_by_ai": false,
    "terms": {
      "stage": "seed",
      "pro_rata": "standard",
      "anti_dilution": "broad_based",
      "liq_pref_x100": 100,
      "participating": false,
      "pre_money_minor": 40000000000,
      "exclusivity_days": 30,
      "investment_minor": 10000000000,
      "founder_board_seats": 2,
      "option_pool_pre_bps": 1000,
      "investor_board_seats": 1
    },
    "flags": [],
    "flag_count": 0,
    "confirmed_at": "2026-11-12T12:00:34.433941+00:00",
    "created_at": "2026-11-12T12:00:34.421312+00:00",
    "proposed": null,
    "currency_mismatch": false,
    "document_currency": null,
    "round_currency": null
  },
  "meta": null
}
```

A sheet that trips rules gets flags immediately (fixed text); `ai_text` fills in a moment later:

capture `create_typed_flagged`
```json
{
  "data": {
    "id": "1fef9738-f943-4891-96f1-9b826ee2cd6c",
    "label": "Beta Capital",
    "status": "confirmed",
    "error": null,
    "investor_id": null,
    "investor_name": null,
    "source_file_id": null,
    "source_filename": null,
    "job_id": null,
    "extracted_by_ai": false,
    "terms": {
      "stage": "seed",
      "pro_rata": "super",
      "anti_dilution": "full_ratchet",
      "liq_pref_x100": 200,
      "participating": true,
      "pre_money_minor": 50000000000,
      "exclusivity_days": 60,
      "investment_minor": 10000000000,
      "option_pool_pre_bps": 1800,
      "investor_board_seats": 2,
      "participation_cap_x100": 300
    },
    "flags": [
      {
        "rule": "liq_pref_above_1x",
        "term": "liq_pref_x100",
        "text": "The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
        "title": "Liquidation preference above 1x",
        "value": 200,
        "ai_text": null,
        "threshold": "1x"
      },
      {
        "rule": "participating_preferred",
        "term": "participating",
        "text": "The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
        "title": "Participating preferred",
        "value": true,
        "ai_text": null,
        "threshold": "non-participating"
      },
      {
        "rule": "option_pool_above_15pct",
        "term": "option_pool_pre_bps",
        "text": "The option pool is 18% of the pre-money. Because it sits in the pre-money, founders carry all of that dilution.",
        "title": "Large option pool in the pre-money",
        "value": 1800,
        "ai_text": null,
        "threshold": "15% of pre-money"
      },
      {
        "rule": "exclusivity_above_45_days",
        "term": "exclusivity_days",
        "text": "You agree not to talk to other investors for 60 days. Up to 45 days is common.",
        "title": "Long exclusivity (no-shop)",
        "value": 60,
        "ai_text": null,
        "threshold": "45 days"
      },
      {
        "rule": "investor_board_seats_early",
        "term": "investor_board_seats",
        "text": "The investor gets 2 board seats on a seed round. At this stage one seat, or an observer, is more common.",
        "title": "More than one investor board seat on an early round",
        "value": 2,
        "ai_text": null,
        "threshold": "1 investor seat"
      },
      {
        "rule": "super_pro_rata",
        "term": "pro_rata",
        "text": "The investor can buy more than their share of future rounds, which can crowd out new investors.",
        "title": "Super pro-rata",
        "value": "super",
        "ai_text": null,
        "threshold": "standard pro-rata"
      },
      {
        "rule": "full_ratchet",
        "term": "anti_dilution",
        "text": "If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
        "title": "Full-ratchet anti-dilution",
        "value": "full_ratchet",
        "ai_text": null,
        "threshold": "broad-based weighted average"
      }
    ],
    "flag_count": 7,
    "confirmed_at": "2026-11-06T21:44:45.698312+00:00",
    "created_at": "2026-11-06T21:44:45.686365+00:00",
    "proposed": null
  },
  "meta": null
}
```

After the wording job (canned in this run):

capture `detail_flagged_with_wording`
```json
{
  "data": {
    "id": "1fef9738-f943-4891-96f1-9b826ee2cd6c",
    "label": "Beta Capital",
    "status": "confirmed",
    "error": null,
    "investor_id": null,
    "investor_name": null,
    "source_file_id": null,
    "source_filename": null,
    "job_id": null,
    "extracted_by_ai": false,
    "terms": {
      "stage": "seed",
      "pro_rata": "super",
      "anti_dilution": "full_ratchet",
      "liq_pref_x100": 200,
      "participating": true,
      "pre_money_minor": 50000000000,
      "exclusivity_days": 60,
      "investment_minor": 10000000000,
      "option_pool_pre_bps": 1800,
      "investor_board_seats": 2,
      "participation_cap_x100": 300
    },
    "flags": [
      {
        "rule": "liq_pref_above_1x",
        "term": "liq_pref_x100",
        "text": "The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
        "title": "Liquidation preference above 1x",
        "value": 200,
        "ai_text": "In plain words: The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
        "threshold": "1x"
      },
      {
        "rule": "participating_preferred",
        "term": "participating",
        "text": "The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
        "title": "Participating preferred",
        "value": true,
        "ai_text": "In plain words: The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
        "threshold": "non-participating"
      },
      {
        "rule": "option_pool_above_15pct",
        "term": "option_pool_pre_bps",
        "text": "The option pool is 18% of the pre-money. Because it sits in the pre-money, founders carry all of that dilution.",
        "title": "Large option pool in the pre-money",
        "value": 1800,
        "ai_text": "In plain words: The option pool is 18% of the pre-money. Because it sits in the pre-money, founders carry all of that dilution.",
        "threshold": "15% of pre-money"
      },
      {
        "rule": "exclusivity_above_45_days",
        "term": "exclusivity_days",
        "text": "You agree not to talk to other investors for 60 days. Up to 45 days is common.",
        "title": "Long exclusivity (no-shop)",
        "value": 60,
        "ai_text": "In plain words: You agree not to talk to other investors for 60 days. Up to 45 days is common.",
        "threshold": "45 days"
      },
      {
        "rule": "investor_board_seats_early",
        "term": "investor_board_seats",
        "text": "The investor gets 2 board seats on a seed round. At this stage one seat, or an observer, is more common.",
        "title": "More than one investor board seat on an early round",
        "value": 2,
        "ai_text": "In plain words: The investor gets 2 board seats on a seed round. At this stage one seat, or an observer, is more common.",
        "threshold": "1 investor seat"
      },
      {
        "rule": "super_pro_rata",
        "term": "pro_rata",
        "text": "The investor can buy more than their share of future rounds, which can crowd out new investors.",
        "title": "Super pro-rata",
        "value": "super",
        "ai_text": "In plain words: The investor can buy more than their share of future rounds, which can crowd out new investors.",
        "threshold": "standard pro-rata"
      },
      {
        "rule": "full_ratchet",
        "term": "anti_dilution",
        "text": "If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
        "title": "Full-ratchet anti-dilution",
        "value": "full_ratchet",
        "ai_text": "In plain words: If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
        "threshold": "broad-based weighted average"
      }
    ],
    "flag_count": 7,
    "confirmed_at": "2026-11-06T21:44:45.698312+00:00",
    "created_at": "2026-11-06T21:44:45.686365+00:00",
    "proposed": null,
    "disclaimer": "Negotiate with clarity. This is an automated reading of the terms you entered, not legal or financial advice. Ask a lawyer to review a real term sheet before you sign."
  },
  "meta": null
}
```

Validation is 422 with the field named:

capture `create_invalid_422`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "terms.liq_pref_x100",
        "message": "Input should be less than or equal to 2000"
      }
    ]
  }
}
```

## 5. Extract from a PDF, then confirm

`POST /term-sheets/extract` `{file_id, investor_id?, label?}`: the file is a PDF already in Documents (clean
scan; upload through Documents first). **202** with the sheet to poll:

capture `extract_queued`
```json
{
  "data": {
    "deduplicated": false,
    "term_sheet": {
      "id": "52618e87-d4c9-481f-a35b-8698674d4962",
      "label": "gamma-term-sheet.pdf",
      "status": "extracting",
      "error": null,
      "investor_id": "02848807-a8db-46af-9fb5-14043c5da9ee",
      "investor_name": "Tolu Bello",
      "source_file_id": "21dabddc-0890-4c33-b770-67662a5f0ec6",
      "source_filename": "gamma-term-sheet.pdf",
      "job_id": "05f4a8cb-4f3c-4dbe-a914-a2fb6bd95a1d",
      "extracted_by_ai": true,
      "terms": {},
      "flags": [],
      "flag_count": 0,
      "confirmed_at": null,
      "created_at": "2026-11-12T12:00:35.193349+00:00",
      "proposed": null,
      "currency_mismatch": false,
      "document_currency": null,
      "round_currency": null
    }
  },
  "meta": null
}
```

Sending the **same file again** returns the existing sheet with **200** `deduplicated: true` and spends nothing
(also after it finished; only a `failed` one can be retried):

capture `extract_deduplicated`
```json
{
  "data": {
    "deduplicated": true,
    "term_sheet": {
      "id": "52618e87-d4c9-481f-a35b-8698674d4962",
      "label": "gamma-term-sheet.pdf",
      "status": "extracting",
      "error": null,
      "investor_id": "02848807-a8db-46af-9fb5-14043c5da9ee",
      "investor_name": "Tolu Bello",
      "source_file_id": "21dabddc-0890-4c33-b770-67662a5f0ec6",
      "source_filename": "gamma-term-sheet.pdf",
      "job_id": "05f4a8cb-4f3c-4dbe-a914-a2fb6bd95a1d",
      "extracted_by_ai": true,
      "terms": {},
      "flags": [],
      "flag_count": 0,
      "confirmed_at": null,
      "created_at": "2026-11-12T12:00:35.193349+00:00",
      "proposed": null,
      "currency_mismatch": false,
      "document_currency": null,
      "round_currency": null
    }
  },
  "meta": null
}
```

While it runs, `terms` and `flags` are empty:

capture `detail_extracting`
```json
{
  "data": {
    "id": "52618e87-d4c9-481f-a35b-8698674d4962",
    "label": "gamma-term-sheet.pdf",
    "status": "extracting",
    "error": null,
    "investor_id": "02848807-a8db-46af-9fb5-14043c5da9ee",
    "investor_name": "Tolu Bello",
    "source_file_id": "21dabddc-0890-4c33-b770-67662a5f0ec6",
    "source_filename": "gamma-term-sheet.pdf",
    "job_id": "05f4a8cb-4f3c-4dbe-a914-a2fb6bd95a1d",
    "extracted_by_ai": true,
    "terms": {},
    "flags": [],
    "flag_count": 0,
    "confirmed_at": null,
    "created_at": "2026-11-12T12:00:35.193349+00:00",
    "proposed": null,
    "currency_mismatch": false,
    "document_currency": null,
    "round_currency": null,
    "disclaimer": "Negotiate with clarity. This is an automated reading of the terms you entered, not legal or financial advice. Ask a lawyer to review a real term sheet before you sign."
  },
  "meta": null
}
```

You cannot confirm early:

capture `confirm_too_early_409`
```json
{
  "error": {
    "code": "NOT_AWAITING_CONFIRMATION",
    "message": "This term sheet is not waiting for confirmation.",
    "field_errors": []
  }
}
```

When the job finishes the sheet is `needs_confirmation`. `proposed.terms` are the model's guesses, converted to
our units; `proposed.quotes` maps a term to a short verbatim quote from the PDF so the founder can check it
(only for terms that survived validation). **The proposal is not the sheet:** `terms` and `flags` are still
empty and the sheet cannot be compared. *The values here are the canned model's.*

capture `detail_needs_confirmation`
```json
{
  "data": {
    "id": "52618e87-d4c9-481f-a35b-8698674d4962",
    "label": "gamma-term-sheet.pdf",
    "status": "needs_confirmation",
    "error": null,
    "investor_id": "02848807-a8db-46af-9fb5-14043c5da9ee",
    "investor_name": "Tolu Bello",
    "source_file_id": "21dabddc-0890-4c33-b770-67662a5f0ec6",
    "source_filename": "gamma-term-sheet.pdf",
    "job_id": "05f4a8cb-4f3c-4dbe-a914-a2fb6bd95a1d",
    "extracted_by_ai": true,
    "terms": {},
    "flags": [],
    "flag_count": 0,
    "confirmed_at": null,
    "created_at": "2026-11-12T12:00:35.193349+00:00",
    "proposed": {
      "terms": {
        "stage": "seed",
        "pro_rata": "super",
        "anti_dilution": "full_ratchet",
        "liq_pref_x100": 200,
        "participating": true,
        "pre_money_minor": 40000000000,
        "exclusivity_days": 60,
        "investment_minor": 10000000000,
        "founder_board_seats": 2,
        "option_pool_pre_bps": 1250,
        "investor_board_seats": 2,
        "participation_cap_x100": 300
      },
      "quotes": {
        "pre_money_minor": "Pre-money valuation of NGN 400,000,000"
      },
      "currency": "NGN"
    },
    "currency_mismatch": false,
    "document_currency": null,
    "round_currency": null,
    "disclaimer": "Negotiate with clarity. This is an automated reading of the terms you entered, not legal or financial advice. Ask a lawyer to review a real term sheet before you sign."
  },
  "meta": null
}
```

An unconfirmed sheet cannot be compared:

capture `compare_unconfirmed_409`
```json
{
  "error": {
    "code": "TERM_SHEET_NOT_CONFIRMED",
    "message": "Confirm the values of every term sheet before comparing it.",
    "field_errors": []
  }
}
```

`POST /{id}/confirm` `{terms, label?, acknowledge_currency_mismatch?}` takes **the founder's final values** (prefill the form from
`proposed.terms`; they may correct anything). The sheet becomes `confirmed`, flags are computed from what the
founder confirmed, and `funding.term_sheet.added` is announced (below):

capture `confirm`
```json
{
  "data": {
    "id": "52618e87-d4c9-481f-a35b-8698674d4962",
    "label": "Gamma Partners",
    "status": "confirmed",
    "error": null,
    "investor_id": "02848807-a8db-46af-9fb5-14043c5da9ee",
    "investor_name": "Tolu Bello",
    "source_file_id": "21dabddc-0890-4c33-b770-67662a5f0ec6",
    "source_filename": "gamma-term-sheet.pdf",
    "job_id": "05f4a8cb-4f3c-4dbe-a914-a2fb6bd95a1d",
    "extracted_by_ai": true,
    "terms": {
      "stage": "seed",
      "pro_rata": "super",
      "anti_dilution": "full_ratchet",
      "liq_pref_x100": 200,
      "participating": true,
      "pre_money_minor": 40000000000,
      "exclusivity_days": 45,
      "investment_minor": 10000000000,
      "founder_board_seats": 2,
      "option_pool_pre_bps": 1250,
      "investor_board_seats": 1,
      "participation_cap_x100": 300
    },
    "flags": [
      {
        "rule": "liq_pref_above_1x",
        "term": "liq_pref_x100",
        "text": "The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
        "title": "Liquidation preference above 1x",
        "value": 200,
        "ai_text": null,
        "threshold": "1x"
      },
      {
        "rule": "participating_preferred",
        "term": "participating",
        "text": "The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
        "title": "Participating preferred",
        "value": true,
        "ai_text": null,
        "threshold": "non-participating"
      },
      {
        "rule": "super_pro_rata",
        "term": "pro_rata",
        "text": "The investor can buy more than their share of future rounds, which can crowd out new investors.",
        "title": "Super pro-rata",
        "value": "super",
        "ai_text": null,
        "threshold": "standard pro-rata"
      },
      {
        "rule": "full_ratchet",
        "term": "anti_dilution",
        "text": "If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
        "title": "Full-ratchet anti-dilution",
        "value": "full_ratchet",
        "ai_text": null,
        "threshold": "broad-based weighted average"
      }
    ],
    "flag_count": 4,
    "confirmed_at": "2026-11-12T12:00:35.651044+00:00",
    "created_at": "2026-11-12T12:00:35.193349+00:00",
    "proposed": null,
    "currency_mismatch": false,
    "document_currency": null,
    "round_currency": null
  },
  "meta": null
}
```

Confirming twice is 409 `NOT_AWAITING_CONFIRMATION`:

capture `confirm_twice_409`
```json
{
  "error": {
    "code": "NOT_AWAITING_CONFIRMATION",
    "message": "This term sheet is not waiting for confirmation.",
    "field_errors": []
  }
}
```

### The document's currency (check it before confirming)

The extraction also reads the **currency the document states** (`proposed.currency`, an ISO code such as
`NGN`, `USD`, `GBP`, or `null` when none is stated). Amounts are always stored in the **round's** currency, so
if the two differ the detail of a `needs_confirmation` sheet carries `currency_mismatch: true`,
`document_currency` and `round_currency`. **Show a clear warning before the form**: "This document states
amounts in USD; your round is in NGN. Convert the amounts below, or confirm they are already in NGN." The
proposed values are the document's own numbers, **not converted**. (`currency_mismatch` is `false` on every
other status, and when the document states no currency.)

capture `detail_currency_mismatch`
```json
{
  "data": {
    "id": "1065568d-c7cd-4144-8d43-aa29b381cd14",
    "label": "delta-term-sheet.pdf",
    "status": "needs_confirmation",
    "error": null,
    "investor_id": null,
    "investor_name": null,
    "source_file_id": "b08ee9ac-b83d-448b-85a3-d1a729c84b6a",
    "source_filename": "delta-term-sheet.pdf",
    "job_id": "337a3d70-eb2f-4a17-ae67-24b2371b8580",
    "extracted_by_ai": true,
    "terms": {},
    "flags": [],
    "flag_count": 0,
    "confirmed_at": null,
    "created_at": "2026-11-12T12:00:35.731362+00:00",
    "proposed": {
      "terms": {
        "stage": "seed",
        "pro_rata": "super",
        "anti_dilution": "full_ratchet",
        "liq_pref_x100": 200,
        "participating": true,
        "pre_money_minor": 40000000000,
        "exclusivity_days": 60,
        "investment_minor": 10000000000,
        "founder_board_seats": 2,
        "option_pool_pre_bps": 1250,
        "investor_board_seats": 2,
        "participation_cap_x100": 300
      },
      "quotes": {
        "pre_money_minor": "Pre-money valuation of NGN 400,000,000"
      },
      "currency": "USD"
    },
    "currency_mismatch": true,
    "document_currency": "USD",
    "round_currency": "NGN",
    "disclaimer": "Negotiate with clarity. This is an automated reading of the terms you entered, not legal or financial advice. Ask a lawyer to review a real term sheet before you sign."
  },
  "meta": null
}
```

Confirming amounts (`pre_money_minor` or `investment_minor`) on such a sheet without saying so is refused:

capture `confirm_currency_mismatch_409`
```json
{
  "error": {
    "code": "CURRENCY_MISMATCH",
    "message": "This document states amounts in USD, but your round is in NGN. Convert the amounts, or confirm that they are already in NGN.",
    "field_errors": []
  }
}
```

Send `"acknowledge_currency_mismatch": true` in the confirm body once the founder has converted the amounts
or confirmed they are already in the round currency. A confirm with no money terms needs no acknowledgement.
The flag is a safeguard, not a conversion: we never convert currencies.


Failure example, a scanned PDF with no text layer (nothing was sent to the model):

capture `detail_failed_no_text`
```json
{
  "data": {
    "id": "82ae6789-d877-4e27-9a19-f083da58a66e",
    "label": "scan.pdf",
    "status": "failed",
    "error": "no_text",
    "investor_id": null,
    "investor_name": null,
    "source_file_id": "d8c5b694-c10e-4139-b070-4135da056109",
    "source_filename": "scan.pdf",
    "job_id": "a8f204b8-16ea-455c-a67f-eaff4be3cef0",
    "extracted_by_ai": true,
    "terms": {},
    "flags": [],
    "flag_count": 0,
    "confirmed_at": null,
    "created_at": "2026-11-12T12:00:35.947844+00:00",
    "proposed": null,
    "currency_mismatch": false,
    "document_currency": null,
    "round_currency": null,
    "disclaimer": "Negotiate with clarity. This is an automated reading of the terms you entered, not legal or financial advice. Ask a lawyer to review a real term sheet before you sign."
  },
  "meta": null
}
```

Not a PDF is 422 at the request:

capture `extract_not_a_pdf_422`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Extraction needs a PDF. Enter the terms by hand otherwise.",
    "field_errors": [
      {
        "field": "file_id",
        "message": "Extraction needs a PDF. Enter the terms by hand otherwise."
      }
    ]
  }
}
```

Other request errors (*unit-tested, not captured*): file not found or trashed 404; file not scan-clean 409
`FILE_NOT_CLEAN`; today's AI allowance used up **429 `AI_BUDGET_EXHAUSTED`** (show the "used up for today"
state and offer manual entry).

## 6. The rules table (the flags)

The flags come from a fixed table, not from the model. `GET /term-sheets/rules` returns it so the page can
show what each flag compares against. Trimmed to two rules:

capture `rules`
```json
{
  "data": {
    "rules": [
      {
        "id": "liq_pref_above_1x",
        "term": "liq_pref_x100",
        "title": "Liquidation preference above 1x",
        "threshold": "1x"
      },
      {
        "id": "participating_preferred",
        "term": "participating",
        "title": "Participating preferred",
        "threshold": "non-participating"
      },
      {
        "...": "5 more rules, same shape"
      }
    ],
    "disclaimer": "Negotiate with clarity. This is an automated reading of the terms you entered, not legal or financial advice. Ask a lawyer to review a real term sheet before you sign."
  },
  "meta": null
}
```

The seven rules: liquidation preference above 1x; participating preferred; option pool above 15% of the
pre-money; exclusivity above 45 days; more than one investor board seat on a `pre_seed` or `seed` sheet;
super pro-rata; full-ratchet anti-dilution. Each is "strictly above" its threshold. The board seats rule needs
`stage` (without a stage it does not flag).

## 7. Compare

`GET /term-sheets/compare?ids=a,b,c` (2 to 6 **confirmed** sheets, comma separated; duplicates are collapsed so
the same id twice is "fewer than 2"). One column per sheet, one row per term **that at least one sheet
states**; a sheet that does not state it has `null`. `best` and `worst` are lists of sheet ids, empty when the
values tie, when only one sheet states the term, or for terms that are never ranked (`better: null`:
investment, post-money, participation cap). "Best" is from the founder's side by a plain ordering on the
number (higher pre-money, lower preference, ...); **terms trade off, so show it as a highlight, not advice**.
`investor_ownership_bps` and `post_money_minor` are derived (`investment / (pre + investment)`, half-even to
a basis point). Trimmed to four rows:

capture `compare`
```json
{
  "data": {
    "columns": [
      {
        "id": "8f028911-9a50-42d5-870d-84fc10f60ae3",
        "label": "Alpha Ventures",
        "investor_id": "7c6f13be-06f0-4f8e-bb2c-6f4ab195dbf8",
        "investor_name": "Tolu Bello",
        "stage": "seed",
        "flag_count": 0
      },
      {
        "id": "1fef9738-f943-4891-96f1-9b826ee2cd6c",
        "label": "Beta Capital",
        "investor_id": null,
        "investor_name": null,
        "stage": "seed",
        "flag_count": 7
      },
      {
        "id": "76b8924d-ca2a-4c69-9b25-139c5ebb89f9",
        "label": "Gamma Partners",
        "investor_id": "7c6f13be-06f0-4f8e-bb2c-6f4ab195dbf8",
        "investor_name": "Tolu Bello",
        "stage": "seed",
        "flag_count": 4
      }
    ],
    "rows": [
      {
        "term": "pre_money_minor",
        "label": "Pre-money valuation",
        "unit": "money",
        "better": "higher",
        "values": {
          "8f028911-9a50-42d5-870d-84fc10f60ae3": 40000000000,
          "1fef9738-f943-4891-96f1-9b826ee2cd6c": 50000000000,
          "76b8924d-ca2a-4c69-9b25-139c5ebb89f9": 40000000000
        },
        "best": [
          "1fef9738-f943-4891-96f1-9b826ee2cd6c"
        ],
        "worst": [
          "76b8924d-ca2a-4c69-9b25-139c5ebb89f9",
          "8f028911-9a50-42d5-870d-84fc10f60ae3"
        ]
      },
      {
        "term": "investor_ownership_bps",
        "label": "Investor ownership (investment / post-money)",
        "unit": "bps",
        "better": "lower",
        "values": {
          "8f028911-9a50-42d5-870d-84fc10f60ae3": 2000,
          "1fef9738-f943-4891-96f1-9b826ee2cd6c": 1667,
          "76b8924d-ca2a-4c69-9b25-139c5ebb89f9": 2000
        },
        "best": [
          "1fef9738-f943-4891-96f1-9b826ee2cd6c"
        ],
        "worst": [
          "76b8924d-ca2a-4c69-9b25-139c5ebb89f9",
          "8f028911-9a50-42d5-870d-84fc10f60ae3"
        ]
      },
      {
        "term": "liq_pref_x100",
        "label": "Liquidation preference",
        "unit": "multiple_x100",
        "better": "lower",
        "values": {
          "8f028911-9a50-42d5-870d-84fc10f60ae3": 100,
          "1fef9738-f943-4891-96f1-9b826ee2cd6c": 200,
          "76b8924d-ca2a-4c69-9b25-139c5ebb89f9": 200
        },
        "best": [
          "8f028911-9a50-42d5-870d-84fc10f60ae3"
        ],
        "worst": [
          "1fef9738-f943-4891-96f1-9b826ee2cd6c",
          "76b8924d-ca2a-4c69-9b25-139c5ebb89f9"
        ]
      },
      {
        "term": "participating",
        "label": "Participating preferred",
        "unit": "bool",
        "better": "lower",
        "values": {
          "8f028911-9a50-42d5-870d-84fc10f60ae3": false,
          "1fef9738-f943-4891-96f1-9b826ee2cd6c": true,
          "76b8924d-ca2a-4c69-9b25-139c5ebb89f9": true
        },
        "best": [
          "8f028911-9a50-42d5-870d-84fc10f60ae3"
        ],
        "worst": [
          "1fef9738-f943-4891-96f1-9b826ee2cd6c",
          "76b8924d-ca2a-4c69-9b25-139c5ebb89f9"
        ]
      },
      {
        "...": "more rows, same shape"
      }
    ],
    "flags": {
      "8f028911-9a50-42d5-870d-84fc10f60ae3": [],
      "1fef9738-f943-4891-96f1-9b826ee2cd6c": [
        {
          "rule": "liq_pref_above_1x",
          "term": "liq_pref_x100",
          "text": "The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
          "title": "Liquidation preference above 1x",
          "value": 200,
          "ai_text": "In plain words: The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
          "threshold": "1x"
        },
        {
          "rule": "participating_preferred",
          "term": "participating",
          "text": "The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
          "title": "Participating preferred",
          "value": true,
          "ai_text": "In plain words: The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
          "threshold": "non-participating"
        },
        {
          "rule": "option_pool_above_15pct",
          "term": "option_pool_pre_bps",
          "text": "The option pool is 18% of the pre-money. Because it sits in the pre-money, founders carry all of that dilution.",
          "title": "Large option pool in the pre-money",
          "value": 1800,
          "ai_text": "In plain words: The option pool is 18% of the pre-money. Because it sits in the pre-money, founders carry all of that dilution.",
          "threshold": "15% of pre-money"
        },
        {
          "rule": "exclusivity_above_45_days",
          "term": "exclusivity_days",
          "text": "You agree not to talk to other investors for 60 days. Up to 45 days is common.",
          "title": "Long exclusivity (no-shop)",
          "value": 60,
          "ai_text": "In plain words: You agree not to talk to other investors for 60 days. Up to 45 days is common.",
          "threshold": "45 days"
        },
        {
          "rule": "investor_board_seats_early",
          "term": "investor_board_seats",
          "text": "The investor gets 2 board seats on a seed round. At this stage one seat, or an observer, is more common.",
          "title": "More than one investor board seat on an early round",
          "value": 2,
          "ai_text": "In plain words: The investor gets 2 board seats on a seed round. At this stage one seat, or an observer, is more common.",
          "threshold": "1 investor seat"
        },
        {
          "rule": "super_pro_rata",
          "term": "pro_rata",
          "text": "The investor can buy more than their share of future rounds, which can crowd out new investors.",
          "title": "Super pro-rata",
          "value": "super",
          "ai_text": "In plain words: The investor can buy more than their share of future rounds, which can crowd out new investors.",
          "threshold": "standard pro-rata"
        },
        {
          "rule": "full_ratchet",
          "term": "anti_dilution",
          "text": "If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
          "title": "Full-ratchet anti-dilution",
          "value": "full_ratchet",
          "ai_text": "In plain words: If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
          "threshold": "broad-based weighted average"
        }
      ],
      "76b8924d-ca2a-4c69-9b25-139c5ebb89f9": [
        {
          "rule": "liq_pref_above_1x",
          "term": "liq_pref_x100",
          "text": "The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
          "title": "Liquidation preference above 1x",
          "value": 200,
          "ai_text": null,
          "threshold": "1x"
        },
        {
          "rule": "participating_preferred",
          "term": "participating",
          "text": "The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
          "title": "Participating preferred",
          "value": true,
          "ai_text": null,
          "threshold": "non-participating"
        },
        {
          "rule": "super_pro_rata",
          "term": "pro_rata",
          "text": "The investor can buy more than their share of future rounds, which can crowd out new investors.",
          "title": "Super pro-rata",
          "value": "super",
          "ai_text": null,
          "threshold": "standard pro-rata"
        },
        {
          "rule": "full_ratchet",
          "term": "anti_dilution",
          "text": "If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
          "title": "Full-ratchet anti-dilution",
          "value": "full_ratchet",
          "ai_text": null,
          "threshold": "broad-based weighted average"
        }
      ]
    },
    "currency": "NGN",
    "disclaimer": "Negotiate with clarity. This is an automated reading of the terms you entered, not legal or financial advice. Ask a lawyer to review a real term sheet before you sign."
  },
  "meta": null
}
```

capture `compare_one_sheet_422`
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Pick between 2 and 6 term sheets.",
    "field_errors": [
      {
        "field": "ids",
        "message": "Pick between 2 and 6 term sheets."
      }
    ]
  }
}
```

## 8. List, filter, edit, delete, history

`GET /term-sheets?investor_id=` lists newest first (lean items; no `proposed`). A workspace can hold 200 sheets; `failed` ones do not count toward that.

capture `list_for_investor`
```json
{
  "data": {
    "items": [
      {
        "id": "76b8924d-ca2a-4c69-9b25-139c5ebb89f9",
        "label": "Gamma Partners",
        "status": "confirmed",
        "error": null,
        "investor_id": "7c6f13be-06f0-4f8e-bb2c-6f4ab195dbf8",
        "investor_name": "Tolu Bello",
        "source_file_id": "261f7916-7106-4858-8bdc-6f10045b3e8b",
        "source_filename": "gamma-term-sheet.pdf",
        "job_id": "1f057efd-df5f-4c18-8fc1-b313c5258da5",
        "extracted_by_ai": true,
        "terms": {
          "stage": "seed",
          "pro_rata": "super",
          "anti_dilution": "full_ratchet",
          "liq_pref_x100": 200,
          "participating": true,
          "pre_money_minor": 40000000000,
          "exclusivity_days": 45,
          "investment_minor": 10000000000,
          "founder_board_seats": 2,
          "option_pool_pre_bps": 1250,
          "investor_board_seats": 1,
          "participation_cap_x100": 300
        },
        "flags": [
          {
            "rule": "liq_pref_above_1x",
            "term": "liq_pref_x100",
            "text": "The investor gets 2x their money back before anyone else shares in a sale. 1x is the usual ask at this stage.",
            "title": "Liquidation preference above 1x",
            "value": 200,
            "ai_text": null,
            "threshold": "1x"
          },
          {
            "rule": "participating_preferred",
            "term": "participating",
            "text": "The investor takes their money back first and then also shares in what is left (double dip), up to 3x in total.",
            "title": "Participating preferred",
            "value": true,
            "ai_text": null,
            "threshold": "non-participating"
          },
          {
            "rule": "super_pro_rata",
            "term": "pro_rata",
            "text": "The investor can buy more than their share of future rounds, which can crowd out new investors.",
            "title": "Super pro-rata",
            "value": "super",
            "ai_text": null,
            "threshold": "standard pro-rata"
          },
          {
            "rule": "full_ratchet",
            "term": "anti_dilution",
            "text": "If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
            "title": "Full-ratchet anti-dilution",
            "value": "full_ratchet",
            "ai_text": null,
            "threshold": "broad-based weighted average"
          }
        ],
        "flag_count": 4,
        "confirmed_at": "2026-11-06T21:44:46.590650+00:00",
        "created_at": "2026-11-06T21:44:46.230662+00:00"
      }
    ],
    "currency": "NGN",
    "disclaimer": "Negotiate with clarity. This is an automated reading of the terms you entered, not legal or financial advice. Ask a lawyer to review a real term sheet before you sign."
  },
  "meta": null
}
```

`PATCH /term-sheets/{id}` on a **confirmed** sheet (409 `NOT_CONFIRMED` otherwise) takes `label`, `investor_id`
(send null to detach) and `terms` (**replaces the terms whole**; send the full object). Flags are recomputed
and the plain wording is regenerated. A label or terms cannot be null (422).

capture `update_terms_flags_recomputed`
```json
{
  "data": {
    "id": "1fef9738-f943-4891-96f1-9b826ee2cd6c",
    "label": "Beta Capital",
    "status": "confirmed",
    "error": null,
    "investor_id": null,
    "investor_name": null,
    "source_file_id": null,
    "source_filename": null,
    "job_id": null,
    "extracted_by_ai": false,
    "terms": {
      "stage": "seed",
      "pro_rata": "super",
      "anti_dilution": "full_ratchet",
      "liq_pref_x100": 100,
      "participating": false,
      "pre_money_minor": 50000000000,
      "exclusivity_days": 60,
      "investment_minor": 10000000000,
      "option_pool_pre_bps": 1800,
      "investor_board_seats": 2
    },
    "flags": [
      {
        "rule": "option_pool_above_15pct",
        "term": "option_pool_pre_bps",
        "text": "The option pool is 18% of the pre-money. Because it sits in the pre-money, founders carry all of that dilution.",
        "title": "Large option pool in the pre-money",
        "value": 1800,
        "ai_text": null,
        "threshold": "15% of pre-money"
      },
      {
        "rule": "exclusivity_above_45_days",
        "term": "exclusivity_days",
        "text": "You agree not to talk to other investors for 60 days. Up to 45 days is common.",
        "title": "Long exclusivity (no-shop)",
        "value": 60,
        "ai_text": null,
        "threshold": "45 days"
      },
      {
        "rule": "investor_board_seats_early",
        "term": "investor_board_seats",
        "text": "The investor gets 2 board seats on a seed round. At this stage one seat, or an observer, is more common.",
        "title": "More than one investor board seat on an early round",
        "value": 2,
        "ai_text": null,
        "threshold": "1 investor seat"
      },
      {
        "rule": "super_pro_rata",
        "term": "pro_rata",
        "text": "The investor can buy more than their share of future rounds, which can crowd out new investors.",
        "title": "Super pro-rata",
        "value": "super",
        "ai_text": null,
        "threshold": "standard pro-rata"
      },
      {
        "rule": "full_ratchet",
        "term": "anti_dilution",
        "text": "If you later raise at a lower price, the investor's price resets all the way down to it. Broad-based weighted average is the usual protection.",
        "title": "Full-ratchet anti-dilution",
        "value": "full_ratchet",
        "ai_text": null,
        "threshold": "broad-based weighted average"
      }
    ],
    "flag_count": 5,
    "confirmed_at": "2026-11-06T21:44:45.698312+00:00",
    "created_at": "2026-11-06T21:44:45.686365+00:00",
    "proposed": null
  },
  "meta": null
}
```

`DELETE /term-sheets/{id}` removes it:

capture `delete`
```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```

`GET /term-sheets/audit?limit=&offset=`: every create, confirm, update and delete with `before` and `after` (the
snapshot holds the terms and the rule ids that flagged, not the wording). **Changes made by the worker** (an extraction finishing or failing) are rows with `actor_id: null`. A PATCH that changes nothing writes no row. Trimmed to 4 rows:

capture `audit`
```json
{
  "data": {
    "total": 5,
    "items": [
      {
        "id": "723980bd-ec32-4063-ac48-90fde3b61ebb",
        "at": "2026-11-06T21:44:46.692100+00:00",
        "actor_id": "abe61c6d-bbe5-4c2f-ab10-a41081bb975f",
        "term_sheet_id": "1fef9738-f943-4891-96f1-9b826ee2cd6c",
        "action": "update",
        "before": {
          "flags": [
            "liq_pref_above_1x",
            "participating_preferred",
            "option_pool_above_15pct",
            "exclusivity_above_45_days",
            "investor_board_seats_early",
            "super_pro_rata",
            "full_ratchet"
          ],
          "label": "Beta Capital",
          "terms": {
            "stage": "seed",
            "pro_rata": "super",
            "anti_dilution": "full_ratchet",
            "liq_pref_x100": 200,
            "participating": true,
            "pre_money_minor": 50000000000,
            "exclusivity_days": 60,
            "investment_minor": 10000000000,
            "option_pool_pre_bps": 1800,
            "investor_board_seats": 2,
            "participation_cap_x100": 300
          },
          "status": "confirmed",
          "investor_id": null,
          "extracted_by_ai": false
        },
        "after": {
          "flags": [
            "option_pool_above_15pct",
            "exclusivity_above_45_days",
            "investor_board_seats_early",
            "super_pro_rata",
            "full_ratchet"
          ],
          "label": "Beta Capital",
          "terms": {
            "stage": "seed",
            "pro_rata": "super",
            "anti_dilution": "full_ratchet",
            "liq_pref_x100": 100,
            "participating": false,
            "pre_money_minor": 50000000000,
            "exclusivity_days": 60,
            "investment_minor": 10000000000,
            "option_pool_pre_bps": 1800,
            "investor_board_seats": 2
          },
          "status": "confirmed",
          "investor_id": null,
          "extracted_by_ai": false
        }
      },
      {
        "id": "9a8af7c7-4541-4e90-b5f6-8f960c95907f",
        "at": "2026-11-06T21:44:46.592578+00:00",
        "actor_id": "abe61c6d-bbe5-4c2f-ab10-a41081bb975f",
        "term_sheet_id": "76b8924d-ca2a-4c69-9b25-139c5ebb89f9",
        "action": "confirm",
        "before": {
          "flags": [],
          "label": "gamma-term-sheet.pdf",
          "terms": {},
          "status": "needs_confirmation",
          "investor_id": "7c6f13be-06f0-4f8e-bb2c-6f4ab195dbf8",
          "extracted_by_ai": true
        },
        "after": {
          "flags": [
            "liq_pref_above_1x",
            "participating_preferred",
            "super_pro_rata",
            "full_ratchet"
          ],
          "label": "Gamma Partners",
          "terms": {
            "stage": "seed",
            "pro_rata": "super",
            "anti_dilution": "full_ratchet",
            "liq_pref_x100": 200,
            "participating": true,
            "pre_money_minor": 40000000000,
            "exclusivity_days": 45,
            "investment_minor": 10000000000,
            "founder_board_seats": 2,
            "option_pool_pre_bps": 1250,
            "investor_board_seats": 1,
            "participation_cap_x100": 300
          },
          "status": "confirmed",
          "investor_id": "7c6f13be-06f0-4f8e-bb2c-6f4ab195dbf8",
          "extracted_by_ai": true
        }
      },
      {
        "id": "f9c0ea8c-4063-45ee-9ff8-874dad3c82ce",
        "at": "2026-11-06T21:44:46.248409+00:00",
        "actor_id": "abe61c6d-bbe5-4c2f-ab10-a41081bb975f",
        "term_sheet_id": "76b8924d-ca2a-4c69-9b25-139c5ebb89f9",
        "action": "create",
        "before": null,
        "after": {
          "flags": [],
          "label": "gamma-term-sheet.pdf",
          "terms": {},
          "status": "extracting",
          "investor_id": "7c6f13be-06f0-4f8e-bb2c-6f4ab195dbf8",
          "extracted_by_ai": true
        }
      },
      {
        "id": "554efd03-2b49-4ee4-b61e-40e10d931a65",
        "at": "2026-11-06T21:44:45.699400+00:00",
        "actor_id": "abe61c6d-bbe5-4c2f-ab10-a41081bb975f",
        "term_sheet_id": "1fef9738-f943-4891-96f1-9b826ee2cd6c",
        "action": "create",
        "before": null,
        "after": {
          "flags": [
            "liq_pref_above_1x",
            "participating_preferred",
            "option_pool_above_15pct",
            "exclusivity_above_45_days",
            "investor_board_seats_early",
            "super_pro_rata",
            "full_ratchet"
          ],
          "label": "Beta Capital",
          "terms": {
            "stage": "seed",
            "pro_rata": "super",
            "anti_dilution": "full_ratchet",
            "liq_pref_x100": 200,
            "participating": true,
            "pre_money_minor": 50000000000,
            "exclusivity_days": 60,
            "investment_minor": 10000000000,
            "option_pool_pre_bps": 1800,
            "investor_board_seats": 2,
            "participation_cap_x100": 300
          },
          "status": "confirmed",
          "investor_id": null,
          "extracted_by_ai": false
        }
      }
    ]
  },
  "meta": null
}
```

## 9. Notification

`funding.term_sheet.added`: when a sheet is **confirmed or typed**, "Term sheet added, comparison updated" with
the disclaimer as its body, to **founders only, minus the person who did it**. *Not captured live: this
workspace has one founder, so nobody else is notified (unit-tested with a second founder).* Link target
`/funding/term-sheets`.

## 10. Currency

> **Update 2026-11-13 (slice E, #235 lows):** while the workspace has **no round yet**, there is no round
> currency to compare with, so a document in another currency is **not** a mismatch:
> `currency_mismatch` is `false` and `round_currency` is `null` (the list's `currency` still shows the
> NGN default). Create the round first if the founder must be warned. Verified by a unit test
> (`test_no_round_means_no_currency_mismatch`); the captures below pre-date the change and were taken
> with a round present. Also: more than 50 failed extractions in a workspace returns 422 "Too many failed
> extractions. Delete some of them, then try again." until some are deleted.


capture `round_currency_locked_409`
```json
{
  "error": {
    "code": "CAP_TABLE_HAS_AMOUNTS",
    "message": "Your cap table or term sheets already have amounts in NGN. Clear them before changing the currency.",
    "field_errors": []
  }
}
```

## 11. UX consequences the FE must surface

- Show the disclaimer on the page and wherever flags appear. Never colour a flag as "bad": say "unusual for
  this stage".
- After an upload, the user must confirm. Show each proposed value with its quote and let them correct it; make
  it clear the AI may be wrong ("Check each value against your document").
- `ai_text` is a model's reword. Show the rule's `title` and `threshold` beside it, and keep the original
  `text` one click away.
- Team members must not see the Term sheets tab (403).
- "Ask a Legal Advisor to review" is a link to Documents e-review, not an API call.

## 12. Verification table

| Behaviour | Verified |
|---|---|
| Empty list, rules table, typed clean and flagged sheets, validation 422 | live (`e2e/test_funding_term_sheets.py`) |
| Extract 202, dedupe 200, polling states, proposal with quotes, confirm, double confirm 409, early confirm 409 | live (with a **canned model**) |
| Flag wording job and `ai_text` | live (canned model); a real model's wording quality not exercised |
| PDF text extraction, no-text failure, non-PDF 422 | live |
| Compare 3 sheets, one sheet 422, unconfirmed 409 | live |
| List, filter by investor, edit with flags recomputed, delete, audit | live |
| Currency lock once a sheet states money | live |
| Team member 403 | live (real invitation); other roles 403 and the 402 gate: unit tests |
| Notification to other founders | unit tests only (one founder in the live run) |
| Rules thresholds, comparison ordering and ties, unit conversion, number-preserving wording, stale wording dropped, dead job reconcile, daily allowance | unit tests |
| Currency mismatch flag and the 409 `CURRENCY_MISMATCH` without an acknowledgement | live (canned model reports USD) |
| Acknowledged confirm, no-money confirm, same or unstated currency, round currency follow | unit tests |
| Wording cannot overwrite newer flags (row lock before compare and write), swapped figures discarded, system audit rows with no actor, no-op PATCH writes no row, failed sheets outside the cap | unit tests |
| A real model's extraction accuracy (including reading the currency) | **not exercised** |
