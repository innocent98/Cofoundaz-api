# Admin Tickets ledger (branch `feature/admin-tickets`, migration 0087 on 0086)

Resume file for a successor. Worktree `wt/admin-tickets`; scratch DB/Redis containers `tk-pg` (55987) and `tk-redis` (56987); env in untracked `.envrc-tk`. Venv `cofoundaz-api-kvMkS96W-py3.14`. Never merge; no AI attribution; fast local checks only.

## Product calls (sent to the lead 2026-11-02, building on them)
| # | Call |
|---|---|
| T1 | SLA = first staff reply deadline: P1 1h, P2 4h, P3 24h, P4 72h. Chips: ok / due_soon (<=1h left) / breached; met / late once answered. Founders can't set priority; default by category (billing, account = P2, rest P3). |
| T2 | No AI triage in v1. |
| T3 | Scope `support` (+ Super Admin) sees all tickets; assign to any active support staff; Mine = assigned to me; no step-up TOTP on ticket actions; reason required on escalate. Replying to or resolving an unassigned ticket assigns it to the actor. |
| T4 | No attachments in v1; text only, body <= 5000 chars. |
| T5 | Rate limits (Redis counters): 5 tickets/hour, 20/day per user; 30 founder replies/hour. 429 `RATE_LIMITED`. |
| T6 | A founder sees tickets they opened; the workspace's founder role sees all of that workspace's tickets; never notes; other workspaces are 404. |
| T7 | A founder reply on a resolved ticket reopens it within 14 days (clock restarts); after that 409 `TICKET_CLOSED`. |
| T8 | Escalate: P1 + escalated flag + staff-only note with the reason + audit; resolve emails the founder. `GET /admin/dashboard/attention` gives tickets {open, breached} (A9); KPI payload untouched. |

## Status
- [x] Task 1: models, migration 0087, services/endpoints, 45 API tests
- [x] Opus review of task 1: 1 Critical (sequence missing in migration), 3 Important, 1 Minor, all fixed
- [x] Task 2: e2e journey + captures + FE guide (live, passes)
- [ ] Task 3: SOP, API checklist, FE brief (FE repo branch `docs/admin-tickets-brief`)
- [ ] PR to develop, CI green
