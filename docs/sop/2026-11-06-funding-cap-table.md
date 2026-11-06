# Funding Hub: cap table (slice C)

**What shipped:** the API behind the Funding Hub **Cap table** page (PRD 14.3): holders and entries (common, preferred, options, SAFEs, notes) with vesting, a fully-diluted / basic ledger, a deterministic **round model** that converts SAFEs and notes, tops the option pool up and dilutes across up to five rounds, saved scenarios, and a before/after audit trail. **Founder only**, API only. FE guide `docs/fe-integration-guide-funding-cap-table.md`. Branch `feature/funding-cap-table`. Migration **`0099_funding_cap_table`** (parent `0096_auth_onboarding_gaps`; re-chained by the lead if another migration lands first).

**Why:** the mock's cap table was three seeded rows with a hard-coded 70% and a SAFE shown as a percentage (gap rows 21 to 27). Design and calls: `cofoundaz/docs/superpowers/specs/2026-11-05-funding-hub-design.md` section 6; D-FUND-4 (b) ledger + SAFEs and notes converted by the round model, no AI; D-FUND-5 (a) founder only; D-FUND-7 one currency per round.

**How**

| Topic | Decision |
|---|---|
| Founder only (D-FUND-5) | `CAP_TABLE_ROLES = {founder}` in `enums.py`; every route uses `require_role(*CAP_TABLE_ROLES)` (403 for team member and all others), then `assert_feature("funding")` (402 behind `PLAN_ENFORCEMENT`). Nothing is reachable through a data room share. No notifications and no export exist for it. Flip to F + TM by changing the one constant. |
| Pure engine | `app/services/funding/cap_engine.py`: no database, no AI, `Decimal` at 60 digits (half-even), integers out. The service only reads rows, builds engine inputs and shapes output. |
| Rounding (explicit, tested) | SAFE/note shares and new-investor shares **down**; pool top-up **up**; note interest half-even to a minor unit; vested shares down; percentages in basis points by the **largest-remainder** method (a column sums to exactly 10 000, ties go to the larger holding then the key). Values within 1e-9 of a whole number count as that number (the SAFE solve is iterative), so solver noise never costs a share. |
| Round maths | Pre-money includes the SAFEs and the pool top-up: `A = S0 + X + sigma`, price `P = V / A`, new shares `I = N / P` (post-money `V + N`), pool top-up `X` so the unallocated pool is the target share of the post-round fully-diluted total (existing pool counts toward it, never reduced). `S0` is issued + granted options + unallocated pool, SAFEs excluded. |
| SAFE and note conversion | Each converts at the **lowest** of cap price, discount price and the round price. Post-money cap price `cap / (S0 + sigma)` (company capitalisation excludes the new pool, as in the YC post-money SAFE), so a SAFE owns `amount / cap` of the company before the round; pre-money cap price `cap / S0`; discount price `P * (1 - d)`. `sigma` (all SAFE shares) and `P` depend on each other; solved by fixed-point iteration, which contracts while the SAFEs are worth less than the pre-money. Refusals: post-money caps owning 100% or more (`SAFE_CAPS_EXCEED`), SAFEs above the pre-money (`CONVERSION_DID_NOT_SETTLE`), pool target beyond the room (`POOL_TARGET_TOO_HIGH`), empty table, non-positive inputs. |
| MFN | An `mfn` instrument adopts, from non-MFN instruments issued on or after its own date (both dates required), the lowest cap **of the same cap type** and the highest discount. |
| Notes | Simple interest, actual/365, to the model's `as_of`, added to principal before converting. Interest is notes only. |
| Dilution across rounds | `model_rounds` applies rounds in order; convertibles convert in the first round; later rounds dilute everyone including earlier investors. The response gives per-step numbers and a before/after table with `change_bps`; the headline is the founders' combined stake before the first and after the last round. |
| Fully diluted toggle | Default true. Basic counts issued stock only; option and pool holders show `pct_bps: null`. SAFEs and notes are never in a percentage; they are listed as amounts (with accrued interest). |
| Vesting | Optional term + start (+ cliff, default 0). Vested shares = whole elapsed months from the start, 0 before the cliff, linear to the term, rounded down. |
| Currency (D-FUND-7) | The cap table's currency is the latest round's. Once any entry has an amount or share price, changing the round currency is 409 `CAP_TABLE_HAS_AMOUNTS` (`rounds.py`). |
| Concurrency | Every write takes `SELECT ... FOR UPDATE` on the workspace's `startups` row (`lock_workspace`), so two edits to one cap table serialise; entry edits also lock the entry row and merge from the stored values. |
| Audit | `cap_audit`: one row per create, update and delete of a holder, entry or scenario with `before` and `after` and the actor, stamped with wall-clock time. A holder delete audits each entry it removes. Kept out of the platform `audit_log` on purpose: it holds equity data that must stay founder-only. `GET /cap-table/audit` is founder-only. |
| Shape in the database | CHECKs keep each security honest (stock needs shares and no SAFE fields; SAFE/note needs an amount and no shares, price or vesting), discount 1 to 9999 bps, cap > 0, vesting term/start together with cliff <= term, interest on notes only, one pool holder per workspace (partial unique index). |
| Rejected | Float maths; deriving percentages by rounding each row (sums drift); a snapshot Document in Documents (see follow-ups); an Excel/CSV export (not asked, and a new founder-only surface); persisting a "converted" state after a round (the model is advisory). |

**What's involved**

| Area | Paths |
|---|---|
| Models, migration | `app/db/models/cap_table.py` (`CapHolder`, `CapEntry`, `CapScenario`, `CapAudit`), `app/db/models/__init__.py`, `alembic/versions/0099_funding_cap_table.py` |
| Engine | `app/services/funding/cap_engine.py` |
| Service | `app/services/funding/cap_table.py`; currency guard in `app/services/funding/rounds.py` |
| Schemas | `app/schemas/cap_table.py` |
| API | `app/api/v1/endpoints/funding_cap_table.py`, mounted at `/funding` in `app/api/v1/api.py`; role set `CAP_TABLE_ROLES` in `app/db/models/enums.py` |

Endpoints (all under `/api/v1/funding/cap-table`): `GET /` (`fully_diluted`, `as_of`), holders `POST/PATCH/DELETE`, entries `POST/PATCH/DELETE`, `POST /model`, scenarios `GET/POST`, `GET/PATCH/DELETE /scenarios/{id}`, `GET /audit`.

**Verification:** `tests/services/funding/test_cap_engine.py` (26 worked examples, each derived by hand in the comments: plain round, post-money and pre-money SAFE at a cap, discount, no-terms SAFE, cap vs discount, mixed SAFEs and a note, note interest, MFN, pool top-up with and without an existing pool, two rounds, every refusal, basis-point sums, vesting); `tests/api/test_funding_cap_table_api.py` (139 tests: every route refused for each non-founder role with nothing changed, every route allowed for the founder, 402 gate, cross-workspace 404, shape rules, ledger views, model over the API, scenarios as snapshots, audit before/after, workspace lock on every write, stale-row merge, currency guard); live journey `e2e/test_funding_cap_table.py` (26 captures, the source of the FE guide). Local: black, isort, ruff, mypy, bandit; migration upgrade, downgrade, upgrade and `alembic check` on a fresh scratch DB. CI runs the full suite.

**Operate / roll back:** no env var, no AI spend, no worker job, no notification. Revert by downgrading `0099` (drops the four tables) and reverting the PR; the round currency guard goes with the code. The page stays hidden until all Funding slices ship.

**Follow-ups**

- **Data room snapshot (gap row 28):** not built. A frozen, dated snapshot would have to be a Documents file or rich Document, and the Documents library is open to team members, which would break D-FUND-5. It needs a decision (for example a founder-only snapshot type, or putting the snapshot in the share only).
- "Apply this round" (persist the conversion into the ledger) and the SAFE row from a Committed investor (D-FUND-L3, second half) are not built; the model is advisory.
- No two-connection race test for the workspace lock (same gap as slice A); the lock and the stale-row merge are asserted.
- A SAFE's `Draft SAFE` button is a pure FE link to the existing SAFE template.
- Exit/waterfall modelling and pro-rata are out (D-FUND-4 c).

**Check fixes after the first CI run (2026-11-06):** gitleaks `generic-api-key` flagged the JSON field `"key": "<uuid>"` in the model output (not a secret), so the engine and API field is now `ref` (conversions, ownership rows, the dilution table), the captures were re-taken and the guide rebuilt from them. CodeQL: the currency guard's lazy import (`py/cyclic-import`) is gone because the "does the cap table hold money" check now lives in `rounds.py` reading the model directly; a dead assignment in the engine tests and the remaining side-effecting asserts were removed. Left: the Opus final review and the follow-ups above. 0099 is chained on 0096.
