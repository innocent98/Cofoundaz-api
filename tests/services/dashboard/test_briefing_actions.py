import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.errors import AppError, NotFound
from app.core.security import create_access_token
from app.db.models.activity import ActivityLog
from app.db.models.dashboard import DailyBriefing
from app.db.models.enums import BriefingStatus, MembershipRole
from app.db.models.mission import Mission, MissionSettings, MissionTask
from app.services.dashboard.briefing import accept_action
from app.services.dashboard.service import get_summary
from app.services.mission.service import mission_empty_reason
from tests.factories import create_membership, create_startup, create_user


def _briefing(db, startup, *, status=BriefingStatus.ready, day=None, actions=None):
    row = DailyBriefing(
        startup_id=startup.id,
        briefing_date=day or date.today(),
        status=status,
        briefing="B",
        risks="R",
        opportunities="O",
        actions=(
            actions
            if actions is not None
            else [
                {"label": "Call 3 users", "kind": "task", "module": "validation"},
                {"label": "Review runway", "kind": "navigate", "module": "finance"},
            ]
        ),
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def ws(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    return u, s


def test_task_action_adds_to_todays_mission_and_logs_activity(db, ws):
    u, s = ws
    row = _briefing(db, s)
    out = accept_action(db, s, u, row.id, 0)
    assert out["kind"] == "task"
    assert out["href"] == "/validation"
    assert out["mission_task"]["title"] == "Call 3 users"
    task = db.get(MissionTask, uuid.UUID(out["mission_task"]["id"]))
    mission = db.get(Mission, task.mission_id)
    assert mission.mission_date == date.today() and mission.startup_id == s.id
    db.refresh(row)
    assert row.actions[0]["accepted_task_id"] == out["mission_task"]["id"]
    log = db.query(ActivityLog).filter_by(entity_id=task.id).one()
    assert log.action == "mission.task.added"
    assert log.meta["source"] == "briefing"


def test_task_action_is_idempotent(db, ws):
    u, s = ws
    row = _briefing(db, s)
    first = accept_action(db, s, u, row.id, 0)
    second = accept_action(db, s, u, row.id, 0)
    assert first["mission_task"]["id"] == second["mission_task"]["id"]
    assert db.query(MissionTask).filter_by(title="Call 3 users").count() == 1


def test_navigate_action_returns_route_and_marks_accepted(db, ws):
    u, s = ws
    row = _briefing(db, s)
    out = accept_action(db, s, u, row.id, 1)
    assert out == {"action_index": 1, "kind": "navigate", "href": "/finance", "mission_task": None}
    db.refresh(row)
    assert row.actions[1]["accepted"] is True
    assert db.query(MissionTask).count() == 0


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"status": BriefingStatus.generating}, "BRIEFING_NOT_READY"),
        ({"day": date.today() - timedelta(days=1)}, "BRIEFING_STALE"),
    ],
)
def test_rejects_not_ready_or_stale(db, ws, kwargs, code):
    u, s = ws
    row = _briefing(db, s, **kwargs)
    with pytest.raises(AppError) as exc:
        accept_action(db, s, u, row.id, 0)
    assert exc.value.code == code
    assert exc.value.http_status == 409


def test_out_of_range_and_foreign_briefing_are_404(db, ws):
    u, s = ws
    row = _briefing(db, s)
    with pytest.raises(NotFound):
        accept_action(db, s, u, row.id, 5)
    other = create_startup(db, owner=create_user(db))
    with pytest.raises(NotFound):
        accept_action(db, other, u, row.id, 0)


def test_mission_empty_reasons(db, ws):
    _u, s = ws
    assert mission_empty_reason(db, s, None) == "no_roadmap"
    saturday = date.today() + timedelta(days=(5 - date.today().weekday()) % 7)
    weekday = saturday - timedelta(days=1)
    sat_mission = Mission(startup_id=s.id, mission_date=saturday, generated_by="system")
    wk_mission = Mission(startup_id=s.id, mission_date=weekday, generated_by="system")
    db.add_all([sat_mission, wk_mission])
    db.flush()
    assert mission_empty_reason(db, s, sat_mission) == "weekend_off"
    assert mission_empty_reason(db, s, wk_mission) == "all_caught_up"
    db.add(MissionSettings(startup_id=s.id, weekend_missions=True))
    db.flush()
    assert mission_empty_reason(db, s, sat_mission) == "all_caught_up"
    db.add(MissionTask(mission_id=wk_mission.id, title="t", order=0))
    db.flush()
    assert mission_empty_reason(db, s, wk_mission) is None


@pytest.mark.parametrize(
    "role",
    [
        MembershipRole.investor,
        MembershipRole.mentor,
        MembershipRole.accountant,
        MembershipRole.legal_advisor,
        MembershipRole.business_consultant,
    ],
)
def test_briefing_blocks_restricted_for_non_core_roles(db, ws, role):
    u, s = ws
    _briefing(db, s)
    out = get_summary(db, s, u, role=role)
    assert out["briefing"] == {"status": "restricted", "message": None, "id": None, "actions": []}
    assert out["risks"]["status"] == "restricted"
    assert out["risks"]["items"] == []
    assert out["opportunities"]["message"] is None


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def test_accept_endpoint_founder_ok_investor_forbidden(client, db):
    founder = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=founder)
    create_membership(db, founder, s, role=MembershipRole.founder)
    investor = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, investor, s, role=MembershipRole.investor)
    row = _briefing(db, s)
    db.commit()
    url = f"/api/v1/dashboard/briefing/{row.id}/actions/0/accept"

    r = client.post(url, headers=_headers(investor, s))
    assert r.status_code == 403
    r = client.post(url, headers=_headers(founder, s))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["mission_task"]["title"] == "Call 3 users"
    r = client.post(
        f"/api/v1/dashboard/briefing/{row.id}/actions/9/accept", headers=_headers(founder, s)
    )
    assert r.status_code == 404


def test_ensure_today_mission_without_roadmap_starts_a_user_mission(db, ws):
    from app.services.mission.service import ensure_today_mission

    _u, s = ws
    m = ensure_today_mission(db, s)
    assert (m.mission_date, m.generated_by) == (date.today(), "user")
    assert ensure_today_mission(db, s).id == m.id  # second call reuses it


def test_ensure_today_mission_adopts_a_concurrent_winner(db, ws, monkeypatch):
    from app.services.mission import service as mission_svc

    _u, s = ws
    winner = Mission(startup_id=s.id, mission_date=date.today(), generated_by="system")
    db.add(winner)
    db.flush()

    def racing(db_, startup_):
        # The losing racer: its own INSERT hits uq_mission_startup_date.
        db_.add(Mission(startup_id=startup_.id, mission_date=date.today(), generated_by="x"))
        db_.flush()

    monkeypatch.setattr(mission_svc, "get_or_generate_today", racing)
    assert mission_svc.ensure_today_mission(db, s).id == winner.id


def test_briefing_context_carries_real_finance_figures(db, ws):
    from app.db.models.enums import TransactionDirection
    from app.db.models.finance import Transaction
    from app.services.dashboard.service import gather_briefing_context

    _u, s = ws
    assert gather_briefing_context(db, s)["runway_status"] == "no_data"
    for direction, amount, days in (
        (TransactionDirection.inflow, 60_000_000, 200),
        (TransactionDirection.inflow, 3_000_000, 0),
        (TransactionDirection.outflow, 9_000_000, 0),
    ):
        db.add(
            Transaction(
                startup_id=s.id,
                date=date.today() - timedelta(days=days),
                description="seed",
                amount_minor=amount,
                currency="NGN",
                direction=direction,
            )
        )
    db.flush()
    ctx = gather_briefing_context(db, s)
    assert ctx["runway_months"] == 27.0
    assert ctx["monthly_revenue"] == "10,000 NGN"


def test_normalize_actions_drops_blank_labels():
    from app.services.dashboard.briefing import normalize_actions

    assert normalize_actions([{"label": "  ", "kind": "task"}, "junk"]) == []
    assert normalize_actions(None) == []
