# Admin Reported issues: founder "report this" and staff moderation

**What shipped:** the API for reported content. Any workspace member reports one AI Co-Founder reply or one comment; staff with the `moderation` scope (and Super Admin) work a queue with tabs, filters and search, and can dismiss, escalate, remove (soft-hide) or restore. Branch `feature/admin-reports`, migration `0088_content_reports` (on `0087_support_tickets`, so it re-chains after the Tickets PR #211). PRD 25.5, gap row A21. PR: see the checklist entry.

**Why:** the Reports tab was a mock and no founder-side action existed to feed it. Moderation also needs a safe way to see what was reported without giving staff the chat, journal or documents (D-ADM-9).

**How, and the calls made**

| Call | Decision |
|---|---|
| R1 | Reasons: spam, abusive, harmful, inaccurate, privacy, other. `other` needs a note. Note max 500. |
| R2 | Reportable: AI Co-Founder assistant replies (own conversations only) and comments that are not document comments. Marketplace reviews are out until Marketplace is built. |
| R3 | No reporting your own comment. One live (open or escalated) report per person per item (partial unique index). 10 reports per person per hour (Redis counter, 429 `RATE_LIMITED`). |
| R4 | The snippet (max 2000 chars) is copied into the report when it is filed. It survives edits and deletes of the source. **No staff route reads the source thread.** The only time the item is touched is to flip its `removed_by_moderation_at` flag. |
| R5 | Staff see snippet, reason, note, reporter name and email, author name only, workspace name and plan. |
| R6 | Statuses open, escalated, dismissed, removed, restored. Tabs Open / Escalated / Closed. |
| R7 | A moderator decides open reports; only a Super Admin decides an escalated one. This also covers removing an item that has an escalated sibling report, and restoring a removal that closed an escalated report (both found by the automated security review). |
| R8 | Remove closes every live report on the item. Dismiss closes only that one. |
| R9 | Remove and restore need a fresh authenticator code (Super Admin step-up pattern, cheap here because every staff user has TOTP); dismiss and escalate need only a reason (Adebayo's 2026-11-02 rule for routine actions). Every action writes an audit row (ids and reason, never the snippet). |
| R10 | Restore is allowed (reason + code) and sets the reports to `restored`. |
| R11 | Authors see `Removed by moderation` in place of the text, with `removed_by_moderation: true`. No notification yet. |
| R12 | Founder: `POST /reports`, `GET /reports/mine`. Staff: `/admin/reports...`. |

Rejected: copying the whole conversation into the report (breaks D-ADM-9); hard-deleting removed items (not reversible); a separate Super Admin route for escalations (one route set with a role check is simpler and keeps one audit trail); a `reviews` type now (nothing to report yet).

**What's involved**
- `alembic/versions/0088_content_reports.py`: table `content_reports` (partial unique index `uq_content_reports_live`), nullable `removed_by_moderation_at` on `comments` and `ai_messages`.
- `app/db/models/content_report.py`, enums `ReportItemType/Reason/Status` in `app/db/models/enums.py`.
- `app/services/reports/{core,founder}.py`, `app/services/staff/reports.py`, `app/schemas/reports.py`.
- Routes: `app/api/v1/endpoints/reports.py` (founder), `app/api/v1/endpoints/admin.py` (`/admin/reports...`), `app/api/v1/api.py`.
- Serializers that now hide removed text: `serialize_comment` and the threads-index snippet (`app/services/team/service.py`, `app/services/team/members.py`), `serialize_message` (`app/services/ai_cofounder/service.py`). An author cannot edit a removed comment.
- Audit actions: `report.filed|dismissed|escalated|item_removed|item_restored`.

**Verification**
- `tests/api/test_staff_reports.py` (33): founder validation, IDOR and cross-workspace, a teammate's private AI chat, document comments, rate limit, duplicates; the guard matrix for all 7 staff routes; queue tabs, filters, search, paging; staff never see the thread text, prompt or author email; every action with audit rows; step-up on remove and restore; escalation stays a Super Admin's call; removed text hidden in threads, the threads index and AI chats; deleted source.
- `e2e/test_admin_reports.py`: live journey (founder reports a comment and two AI replies; moderator dismisses, escalates and removes; Super Admin removes the escalated one; authors see it removed; restore; audit). Captures in `e2e/_captures/admin_reports/`; guide `docs/fe-integration-guide-admin-reports.md`.
- Migration upgrade, downgrade to 0087, upgrade, downgrade to base and upgrade on a scratch DB; `alembic check` shows no drift for the new objects.
- An automated security review of the first commit found three issues, all fixed with tests: a moderator could close an escalation by removing a sibling report; a moderator could restore a Super Admin's removal; the threads-index snippet showed the removed text.

**Operate / roll back**
- No config. Grant the `moderation` scope through the existing staff invite.
- Roll back: revert the PR, then `alembic downgrade 0087_support_tickets` (drops `content_reports` and the two flag columns; reports are lost, hidden items become visible again).

**Follow-ups**
- FE: the Report action on AI replies and comments, plus the Admin Reports tab (brief `docs/fe-handoff/admin-reported-issues.md` in the FE repo).
- Not built: reviews (Marketplace), notifying the author or reporter, withdrawing your own report, reporting Sales Coach or Marketing AI output, a repeat-offender view, and keeping a removed AI message out of the model's own conversation context.
