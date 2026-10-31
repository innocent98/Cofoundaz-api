# Meeting reminders by email

**What shipped:** meeting reminders now go by email as well as in-app. Milestone and deadline reminders stay in-app.

**Why:** Adebayo asked for it on 2026-10-31 after a staging meeting produced no email. Calendar reminders were in-app only by design.

**How**
- Meeting reminders publish a new type, `calendar.meeting.reminder`, so email can apply to meetings alone.
- New Calendar row in notification preferences, email on by default. This is an exception to D-NOT-2 (new rows start off), because Adebayo asked for this email.
- `TIME_SENSITIVE_TYPES`: a reminder is useless in a digest, so these skip it. The switches and quiet hours still apply, unlike critical types.

**What's involved:** `app/worker/handlers/calendar.py`, `app/services/notifications/{categories,preferences,digest,registry}.py`, `docs/fe-integration-guide-calendar-v2.md`. No migration. The preferences grid comes from the API, so the row should appear without an FE change.

**Verification:** `tests/api/test_calendar_feed_reminders.py`, `tests/services/notifications`, `tests/api/test_notifications_gaps.py` pass: a meeting enqueues an email for a digest user, and a deadline enqueues none. Not verified: a real email arriving on staging, or the row in the FE.

**Operate / roll back:** revert the PR. Old notifications keep their type.

**Follow-ups:** none.
