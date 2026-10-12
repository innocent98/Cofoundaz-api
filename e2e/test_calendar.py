"""Live Calendar journey (Module 19).

`test_calendar_journey`: a founder onboards, then creates four events across one month -- one
of each type, covering an all-day event, a timed event and one carrying notes -- lists the whole
month (Month view), lists a single week (Week view), filters by type (the FE's chips), reads one
event, edits its time, clears the time with an explicit null, is refused an all-day event that
carries a time, deletes one event, and finally reads the read-only roadmap-milestones projection.
A second workspace then proves a cross-tenant id is a 404, not a 403.

Every response body is captured to `e2e/_captures/calendar/*.json` -- those files are the
verbatim source for the calendar FE integration guide. They must be REAL bodies from this live
run, complete and untrimmed.

Calendar is founder/team_member RBAC (app/api/v1/endpoints/calendar.py) -- accountant is NOT
granted (spec D4) -- so this journey needs nothing but a freshly onboarded founder + workspace.

Dates are pinned inside the CURRENT month (UTC, matching the server's clock) so the journey stays
valid whenever it runs, and so the Month/Week range queries are meaningful rather than empty.
"""

from datetime import UTC, date, datetime

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_finance.py)."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": stage},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


def _workspace(c: httpx.Client, make_verified_user, *, name: str) -> dict:
    """Onboard a founder and return headers carrying their workspace."""
    u = make_verified_user(c)
    access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
    auth = _auth_header(access)
    _onboard_steps(c, auth, stage="validation", name=name)
    done = c.post("/api/v1/onboarding/complete", headers=auth)
    assert done.status_code == 200, done.text
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _in_month(today: date, day: int) -> date:
    """A day inside today's own month, so range queries are never empty."""
    return date(today.year, today.month, day)


def test_calendar_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        wh = _workspace(c, make_verified_user, name="Cofoundaz Calendar")

        today = datetime.now(UTC).date()
        first = _in_month(today, 1)
        mid = _in_month(today, 15)
        late = _in_month(today, 20)
        last = _in_month(today, 28)

        # 1. A timed meeting.
        meeting = c.post(
            "/api/v1/calendar/events",
            headers=wh,
            json={
                "title": "Partner call, Sahel Fund",
                "type": "meeting",
                "event_date": mid.isoformat(),
                "start_time": "11:00",
                "end_time": "11:45",
                "description": "Bring the latest traction deck.",
            },
        )
        assert meeting.status_code == 200, meeting.text
        meeting_body = meeting.json()["data"]
        meeting_id = meeting_body["id"]
        # HH:MM on the wire, never HH:MM:SS (spec D6).
        assert meeting_body["start_time"] == "11:00"
        assert meeting_body["end_time"] == "11:45"
        assert meeting_body["all_day"] is False
        capture("calendar", "event_created_timed", meeting)

        # 2. An all-day deadline: the flag is DECLARED, not inferred from the missing time (D12).
        deadline = c.post(
            "/api/v1/calendar/events",
            headers=wh,
            json={
                "title": "Grant deadline, GIZ Digital Inclusion Fund",
                "type": "deadline",
                "event_date": late.isoformat(),
                "all_day": True,
            },
        )
        assert deadline.status_code == 200, deadline.text
        assert deadline.json()["data"]["all_day"] is True
        assert deadline.json()["data"]["start_time"] is None
        capture("calendar", "event_created_all_day", deadline)

        # 3. An untimed task -- no time AND no all_day flag, which must stay all_day false.
        task = c.post(
            "/api/v1/calendar/events",
            headers=wh,
            json={
                "title": "Interview 3 gig workers",
                "type": "task",
                "event_date": first.isoformat(),
            },
        )
        assert task.status_code == 200, task.text
        assert task.json()["data"]["all_day"] is False  # untimed is not all-day
        assert task.json()["data"]["start_time"] is None
        capture("calendar", "event_created_untimed", task)

        # 4. A milestone-typed event -- what the Milestones tab's "+ Milestone" button creates
        # (spec D11/D13). It lands on the grids, NOT in the milestones projection below.
        milestone_event = c.post(
            "/api/v1/calendar/events",
            headers=wh,
            json={
                "title": "Close validation milestone",
                "type": "milestone",
                "event_date": last.isoformat(),
                "all_day": True,
            },
        )
        assert milestone_event.status_code == 200, milestone_event.text
        capture("calendar", "event_created_milestone_type", milestone_event)

        # 5. Month view: the whole of this month, both boundaries inclusive.
        month = c.get(
            "/api/v1/calendar/events",
            headers=wh,
            params={"date_from": first.isoformat(), "date_to": last.isoformat()},
        )
        assert month.status_code == 200, month.text
        month_events = month.json()["data"]["events"]
        assert len(month_events) == 4
        # Ordered by date, then time with untimed/all-day first within a day.
        assert [e["event_date"] for e in month_events] == [
            first.isoformat(),
            mid.isoformat(),
            late.isoformat(),
            last.isoformat(),
        ]
        capture("calendar", "events_month", month)

        # 6. Week view: the same endpoint, a narrower range.
        week = c.get(
            "/api/v1/calendar/events",
            headers=wh,
            params={
                "date_from": _in_month(today, 14).isoformat(),
                "date_to": _in_month(today, 20).isoformat(),
            },
        )
        assert week.status_code == 200, week.text
        assert [e["title"] for e in week.json()["data"]["events"]] == [
            "Partner call, Sahel Fund",
            "Grant deadline, GIZ Digital Inclusion Fund",
        ]
        capture("calendar", "events_week", week)

        # 7. The FE's filter chips map onto ?type=.
        meetings_only = c.get("/api/v1/calendar/events", headers=wh, params={"type": "meeting"})
        assert meetings_only.status_code == 200, meetings_only.text
        assert [e["id"] for e in meetings_only.json()["data"]["events"]] == [meeting_id]
        capture("calendar", "events_filtered_by_type", meetings_only)

        # 8. Read one event.
        one = c.get(f"/api/v1/calendar/events/{meeting_id}", headers=wh)
        assert one.status_code == 200, one.text
        assert one.json()["data"]["title"] == "Partner call, Sahel Fund"
        capture("calendar", "event_detail", one)

        # 9. Move the meeting an hour later.
        moved = c.patch(
            f"/api/v1/calendar/events/{meeting_id}",
            headers=wh,
            json={"start_time": "12:00", "end_time": "12:45"},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["data"]["start_time"] == "12:00"
        capture("calendar", "event_patched", moved)

        # 10. An explicit null CLEARS a nullable time -- that is how a caller un-sets one.
        untimed = c.patch(
            f"/api/v1/calendar/events/{meeting_id}",
            headers=wh,
            json={"start_time": None, "end_time": None},
        )
        assert untimed.status_code == 200, untimed.text
        assert untimed.json()["data"]["start_time"] is None
        capture("calendar", "event_time_cleared", untimed)

        # 11. An all-day event may not carry a time: refused, not silently discarded (D9).
        contradiction = c.post(
            "/api/v1/calendar/events",
            headers=wh,
            json={
                "title": "All day and also at nine",
                "type": "deadline",
                "event_date": mid.isoformat(),
                "all_day": True,
                "start_time": "09:00",
            },
        )
        assert contradiction.status_code == 422, contradiction.text
        assert contradiction.json()["error"]["code"] == "VALIDATION_ERROR"
        capture("calendar", "event_all_day_with_time_422", contradiction)

        # 12. A PATCH is validated against the MERGED row, not the payload alone: a bare
        # start_time looks harmless, but this event is already stored as all_day, and an
        # all-day event may not carry a time (D9).
        merged = c.patch(
            f"/api/v1/calendar/events/{milestone_event.json()['data']['id']}",
            headers=wh,
            json={"start_time": "18:00"},
        )
        assert merged.status_code == 422, merged.text
        capture("calendar", "event_patch_merged_conflict_422", merged)

        # 13. Delete the untimed task.
        removed = c.delete(f"/api/v1/calendar/events/{task.json()['data']['id']}", headers=wh)
        assert removed.status_code == 200, removed.text
        assert removed.json()["data"] == {"deleted": True}
        capture("calendar", "event_deleted", removed)

        after_delete = c.get("/api/v1/calendar/events", headers=wh)
        assert len(after_delete.json()["data"]["events"]) == 3
        capture("calendar", "events_after_delete", after_delete)

        # 14. The read-only roadmap-milestones projection. Onboarding generates a roadmap, so
        # this is usually non-empty; assert the SHAPE rather than a count, and confirm the
        # milestone-typed EVENT created in step 4 is not in here (spec D13).
        milestones = c.get("/api/v1/calendar/milestones", headers=wh)
        assert milestones.status_code == 200, milestones.text
        rows = milestones.json()["data"]["milestones"]
        assert isinstance(rows, list)
        for row in rows:
            assert set(row) == {
                "id",
                "title",
                "due_on",
                "status",
                "calendar_status",
                "progress",
            }
            assert row["status"] in {"todo", "in_progress", "done"}
            assert row["calendar_status"] in {
                "complete",
                "at_risk",
                "on_track",
                "not_started",
            }
        assert "Close validation milestone" not in [r["title"] for r in rows]
        capture("calendar", "milestones", milestones)

        # 15. Another workspace's event id is a 404, never a 403 -- a workspace must not be able
        # to detect that someone else's row exists.
        other_wh = _workspace(c, make_verified_user, name="Someone Else Ltd")
        cross = c.get(f"/api/v1/calendar/events/{meeting_id}", headers=other_wh)
        assert cross.status_code == 404, cross.text
        capture("calendar", "event_cross_tenant_404", cross)

        # ...and their calendar is empty, not ours.
        theirs = c.get("/api/v1/calendar/events", headers=other_wh)
        assert theirs.json()["data"]["events"] == []
        capture("calendar", "events_other_workspace_empty", theirs)
