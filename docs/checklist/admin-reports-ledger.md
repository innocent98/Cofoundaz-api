# Admin Reports ledger (branch `feature/admin-reports`, migration 0088 on 0087)

Resume file for a successor. Worktree `wt/admin-reports`; scratch containers `rep-pg` (55610) and `rep-redis` (56610); env in untracked `.envrc.local`; e2e runner script lives in the session scratchpad (boots uvicorn on :8011 against DB `cofoundaz_e2e`, `PYTHONPATH=.`). Venv `cofoundaz-api-kvMkS96W-py3.14`. Never merge; no AI attribution; fast local checks only.

**Migration chain:** `0088_content_reports` has `down_revision = "0087_support_tickets"`, which exists only on `origin/feature/admin-tickets` (API #211). For local runs, `alembic/versions/0087_support_tickets.py` is copied in untracked (git-excluded). When the lead says "Tickets merged": rebase on develop (expect conflicts in `app/db/models/enums.py`, `app/db/models/__init__.py`, `app/api/v1/api.py`, `app/api/v1/endpoints/admin.py`, `docs/checklist/PROJECT_CHECKLIST.md`: keep both sides), delete the untracked 0087 copy, rerun the migration round trip and the reports tests.

## Product calls R1-R12 (build-session defaults, sent to the lead 2026-11-02)
See `docs/sop/2026-11-02-admin-reports.md` for the table. Remove and restore have step-up (R9); everything else needs only a reason.

## Status
- [x] Task 1: models, migration 0088, founder + staff services/endpoints, 33 API tests
- [x] Security review of task 1: 3 findings fixed (R7 bypasses x2, threads-index leak)
- [x] Task 2: e2e journey + captures + FE guide (live, passes)
- [x] Task 3: SOP, checklist
- [ ] Opus review of the whole diff
- [ ] FE brief (FE repo branch `docs/admin-reports-brief`)
- [ ] PR to develop, CI green
