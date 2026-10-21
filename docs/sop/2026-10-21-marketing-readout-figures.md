# SOP — Marketing weekly readout: figures computed in Python, prose verified

| | |
|---|---|
| **Shipped** | Branch `fix/marketing-readout-figures` → PR to `develop` (link in the PR) |
| **Date** | 2026-10-21 |
| **Area** | Marketing readout: `app/services/marketing/{readout_figures,readouts,ai_prompts}.py`, `app/worker/handlers/marketing_ai.py`, `app/schemas/marketing.py` |

## What shipped

The weekly campaign readout can no longer state a number the API did not compute. All figures
are computed in Python and formatted (₦, commas, %, signs); the model is given only those strings
and told to quote them verbatim; its prose is checked after generation; the structured figures are
returned beside the prose so the FE renders numbers without trusting text.

## Why

The first real-model run (Marketing FE live check, FE repo `docs/sop/marketing-prd-fe.md`)
wrote prose with figures slightly off the recorded data and printed money in raw kobo. The model
was handed raw metric JSON and asked to compare and report, so it did arithmetic and unit
conversion itself.

## How

| Step | Behaviour |
|---|---|
| Compute | `build_figures`: this week (the 7 days before today, today excluded) vs the 7 before, plus all time: spend, impressions, clicks, CTR, conversions, CAC, week-on-week change, `days_live`. Decimal half-up rounding, no float division. `n/a` where there is no denominator |
| Format | `format_money` converts minor → major with `₦` for NGN. Marketing metrics carry no currency, so `NGN` is the constant `MARKETING_CURRENCY` |
| Prompt | `llm_facts`: strings only (no raw numbers, so no kobo to repeat, no campaign name); instructed to quote verbatim and never compute or add a number |
| Verify | `unverified_numbers`: every number in headline, summary, wins, watch, next_step must equal a number in the provided strings (commas stripped, compared as decimals); ratio words (doubled, twice…) are rejected |
| Retry / fallback | One retry telling the model which numbers were rejected; then `fallback_output` (template prose from the same figures). Budget exhausted on the retry also falls back. Unverified prose is never stored |
| Store / return | `output` = prose + `source` (`ai`/`template`); `output.figures` is stored in the existing JSONB and surfaced as a top-level `figures` on `GET /campaigns/{id}/readouts` (no migration) |

Rejected: asking the model to self-check (it is the thing that errs); rounding in the prompt
(still model arithmetic); a migration for a `figures` column (JSONB already holds it).

**Known limits:** the check is on numbers, not meaning, so a correct number attached to the wrong
metric, or the wrong direction ("up" for a falling figure), is not caught; spelled-out numbers
("three") are not caught. Campaign `budget` was dropped from the prompt (its unit is not
established). Old rows have `figures: null` and unverified prose.

## What's involved

New `readout_figures.py`; `readouts.py` (`generate_verified_output`, `serialize_readout`);
`ai_prompts.py::build_readout_messages` (+ `rejected`); `marketing_ai.py::handle_marketing_readout`;
`ReadoutResponse.figures`; `docs/fe-integration-guide-marketing-prd.md` §11; e2e
`e2e/test_marketing_prd_b.py` (now seeds two weeks of metrics, asserts figures).

## Verification

* Unit: `tests/services/marketing/test_readout_figures.py` (14: computation, ₦, zero denominators, no raw
  numbers in the prompt, validator cases, fallback) and 5 handler/endpoint tests in
  `tests/api/test_marketing_prd_b.py` (verified prose stored, retry then stored, two bad answers →
  template, budget on retry, endpoint returns figures).
* Live (stub model): `e2e/test_marketing_prd_b.py`, captures in `e2e/_captures/marketing_prd_b/`.
* **Real model: not run.** The brief allowed one only without touching secrets or calling a provider with
  a real key; this session did neither. The scripted-model unit tests stand in for it.

## Operate / roll back

No migration or config. Revert the PR to restore the old prompt. Rows written meanwhile keep
`output.figures` (ignored by the old serializer). Watch the log line
`marketing readout: model stated unverified numbers` for how often the model needs a retry; many
`source: "template"` rows means the prompt needs tuning.

## Follow-ups

* One real-model run to review prose quality and the retry rate.
* Direction check (up/down vs the sign of the change figure).
* Whether `campaign.budget` is kobo; add it to the figures once settled.
* Regenerate or hide readouts written before this change.
