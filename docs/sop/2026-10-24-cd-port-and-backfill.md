# SOP: SSH port out of secrets, receipts backfill runnable in the image

| | |
|---|---|
| **What shipped** | CD workflows read the SSH port from `vars.VPS_PORT` (default 22) instead of `secrets.VPS_PORT`; the Finance receipts backfill runs as `python -m app.services.finance.receipts_backfill` inside the deployed image. Branch `chore/cd-port-and-backfill`. |
| **Date** | 2026-10-24 |

## Why
- **Staging CD failed on `940340e0` (#173)** after the deploy and the live E2E had both passed. The "Mint the staging-verified proof" job got an empty `TESTED_REF`. Root cause: the image digest contained the value of the `VPS_PORT` secret (`22`; the log shows `...c039***9f12...` and `Skip output 'image_ref' since it may contain secret`). GitHub masks secret values and drops any job output that contains one. A 2-character secret shows up in about one random 64-hex digest in five, so this was a recurring coin flip, and production CD has the same handover.
- **The backfill command in the Finance (c) SOP failed** (`No module named 'scripts'`): the Dockerfile copies only `app/`, `alembic/` and `content/`.

## How
- `vps_port` / `port` in `cd-staging.yml`, `cd-production.yml` and `live-e2e.yml` use `${{ vars.VPS_PORT || 22 }}`. A secret no workflow references is not sent to the runner, so it is no longer masked. Rejected: re-running (same bytes, same digest, same failure); stripping `***` from the output (can't recover the masked characters).
- `receipts_backfill.main()` holds the runner; `scripts/privatise_legacy_receipts.py` now just calls it. Rejected: shipping `scripts/` in the image (more surface for one one-off).

## What's involved
| Area | Files |
|---|---|
| CD | `.github/workflows/cd-staging.yml:278`, `cd-production.yml:320`, `live-e2e.yml:102` |
| Docs | `docs/deployment/GITHUB_ACTIONS_SETUP.md` (`VPS_PORT` is a variable; why never a secret), `docs/sop/2026-10-22-finance-ledger-correctness.md` (backfill command) |
| Backfill | `app/services/finance/receipts_backfill.py` (`main`), `scripts/privatise_legacy_receipts.py`, `tests/services/finance/test_receipts_backfill.py` |

## Verification
- New unit test for `main()` (prints `moved=1 failed=1`, exits 1). Full local CI gates (see the PR).
- The CD change is only provable on the next develop deploy: the proof job must receive a digest reference.

## Operate / roll back
- Optional cleanup: delete the `VPS_PORT` **secret** from the `staging` and `production` environments. If sshd ever moves off 22, add `VPS_PORT` as an environment **variable**.
- `940340e0` has no `staging-verified` tag. The next merge to develop builds a new digest and mints it normally; production promotes that one.
- Roll back: revert the commit.

## Follow-ups
- None.
