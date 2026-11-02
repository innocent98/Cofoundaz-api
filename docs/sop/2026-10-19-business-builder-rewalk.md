# SOP: Business Builder re-walk fixes (API)

| | |
|---|---|
| **What shipped** | A competitor saved with an unselected threat level (or blank map inputs) no longer 422s. Branch `fix/bb-rewalk`. |
| **Why** | Step 1 re-walk: "Add competitor" with Threat level left on "Select..." did nothing visible. The FE sends `""`, `CompetitorData.threat_level` is an enum, so the API returned a field error the form never showed. |
| **How** | `field_validator(mode="before")` on `CompetitorData`: `""`/`None` threat level → `ThreatLevel.medium` (the existing default), `""` map coordinate → `None`. The FE fix (show `field_errors`, don't send blank selects) goes in the FE re-walk PR. Rejected: making `threat_level` nullable (the positioning map and export assume a level). |
| **What's involved** | `app/services/business/record_defs.py`, `tests/services/business/test_record_defs.py`, guide `docs/fe-integration-guide-business-builder-prd.md` (last section), capture `e2e/_captures/business_rewalk/`. |
| **Verification** | Business tests 60 passed; live: the form now saves (201, `threat_level: "medium"`). Full CI parity in the PR. |
| **Operate / roll back** | No migration. Revert the commit. |
| **Follow-ups** | Product call pending: the 15,000 token/startup/day AI budget is smaller than one business plan generation (about 24,000 tokens measured live), so a plan fails as `over_budget` whenever AI was used earlier that day. |

## Update: whole-canvas AI fill reports its outcome (branch `fix/canvas-fill-result`)
Closing the re-walk's ⚠ row on "Fill with AI": on a filled canvas the job did nothing (by design, it drafts empty blocks
only) but the FE said "AI draft added". `handle_canvas_ai_fill` now sets `job.result` to `ready` (+ filled keys),
`nothing_new` or `over_budget`, matching the section-AI job; `handle_record_ai_fill` (records "Draft with AI") likewise sets `ready`/`nothing_new`/`over_budget` with an `added` count. FE message mapping is in the FE follow-up PR.
Tests: `test_ai_fill_reports_its_outcome_on_the_job`, `test_ai_fill_reports_over_budget`; live capture in the guide.
