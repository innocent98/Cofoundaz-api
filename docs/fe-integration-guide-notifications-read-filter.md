# FE integration guide — Notifications: role-filtered reads

All bodies below are copied from live captures in `e2e/_captures/notifications_read_filter/`
(`e2e/test_notifications_read_filter.py`, real server, real Postgres/Redis). IDs are placeholders
of real captured runs; do not rely on them.

## What changed, in the FE's terms

No new endpoint and no new field. The four existing read calls now return only the notifications
the member's **current role** is meant to receive. Older rows that were stored before role-based
delivery (mission, daily briefing, assessment for a mentor or consultant) are simply not returned
or counted any more. They are not deleted: if the member's role changes, they reappear.

| Call | Behaviour for a role outside a type's audience |
|---|---|
| `GET /notifications` (incl. `unread=true`, cursor pagination) | rows omitted; pages stay full, `next_cursor` is correct |
| `GET /notifications/unread-count` | not counted |
| `POST /notifications/{id}/read` | `404 NOT_FOUND` (same as another user's id) |
| `POST /notifications/read-all` | marks only visible rows; `marked` counts only those |
| SSE `unread` frame (first event of `/notifications/stream`) | counted with the same filter |

Types with no audience rule (documents, roadmap, health score, business, member joined, mentions,
unknown types) are visible to every role, as before. The audience table is in
`docs/sop/2026-10-21-notifications-read-filter.md`.

## Captured responses

Setup: founder + mentor + business consultant; each gets legacy rows `mission.ready`,
`dashboard.briefing.ready`, `assessment.completed` and an open `healthscore.dropped`.

Mentor `GET /api/v1/notifications` (`list_mentor.json`, trimmed to the shape; legacy mission,
briefing and assessment rows are absent):

```json
{
  "data": {
    "notifications": [
      {
        "id": "1c99a386-7977-4306-a43c-fca02ce773d1",
        "type": "healthscore.dropped",
        "title": "legacy healthscore.dropped",
        "body": "",
        "data": {},
        "read": false,
        "created_at": "2026-10-21T09:15:29.444093+00:00"
      },
      { "id": "545a64b8-…", "type": "workspace.member.joined", "…": "…" }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

Mentor `GET /notifications/unread-count` (`unread_count_mentor.json`): `{"data":{"unread":2},"meta":null}`
(the 2 open rows; the founder's count in `unread_count_founder.json` includes the 3 legacy rows too).

Mentor `POST /notifications/{their own legacy mission.ready id}/read` (`mark_read_hidden_mentor_404.json`), status 404:

```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

Mentor `POST /notifications/read-all` (`read_all_mentor.json`): `{"data":{"marked":2},"meta":null}`; the
hidden rows stay unread in the database.

After the mentor is made a `team_member` (`list_after_role_change_to_team_member.json`): the
`mission.ready`, `dashboard.briefing.ready` and `assessment.completed` rows are returned, and
`unread_count_after_role_change.json` is `{"data":{"unread":3},"meta":null}`.

## Traps and UX consequences

* **The unread badge can drop on a role change or after this deploy.** A mentor who saw "5" may
  now see "2". That is correct; do not cache the badge across role changes, re-fetch
  `unread-count` after any membership/role refresh.
* **Do not treat a 404 on mark-read as an error toast** for a row you rendered from an older cached
  list; refetch the list instead.
* The live `notification.created` SSE event already respected roles; only stored rows changed.

## Verification table

| Behaviour | Status | Evidence |
|---|---|---|
| Legacy rows hidden from mentor and consultant (list + unread-count) | verified live | `list_mentor.json`, `list_business_consultant.json`, `unread_count_*.json` |
| Founder still sees the legacy rows | verified live | `list_founder.json`, `unread_count_founder.json` |
| Mark-read of a hidden row → 404; read-all skips it | verified live | `mark_read_hidden_mentor_404.json`, `read_all_mentor.json` |
| Role change re-filters on the next request | verified live (role flipped in the DB; no role-change endpoint exists) | `list_after_role_change_to_team_member.json` |
| Pagination/cursor counts only visible rows | unit-tested only | `tests/services/notifications/test_read_filter.py::test_pagination_counts_only_visible_rows` |
| SSE initial `unread` frame filtered by role | unit-tested only | `tests/api/notifications/test_stream.py::test_stream_initial_unread_is_computed_for_the_members_role` |
| `kpi.threshold.crossed` payload-dependent audience | unit-tested only | `test_kpi_threshold_audience_follows_payload` |
