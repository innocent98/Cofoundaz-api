"""D-MIS-8 follow-up: the Dashboard activity feed is filtered by the caller's role.

Mission rows are founder + team member only, finance rows FINANCE_ROLES, marketing rows
MARKETING_ROLES; health score, roadmap, business, document, assessment and member rows stay
open to every role (#153). Filtering happens in SQL so the cursor pages stay full."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import create_access_token
from app.db.models.enums import FINANCE_ROLES, MARKETING_ROLES, MembershipRole
from tests.factories import create_activity, create_membership, create_startup, create_user

MISSION_TITLE = "Call the seed lead investor"


def _headers(u, s):
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }


def _workspace(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    return owner, s


def _join(db, s, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, s, role=role)
    return u


def _at(base, seconds):
    return base + timedelta(seconds=seconds)


def _seed_mixed(db, s, actor):
    """One row of every audience, distinct timestamps, newest last."""
    base = datetime.now(UTC) - timedelta(hours=1)
    rows = [
        ("mission.task.added", "mission_task", f"Ada added '{MISSION_TITLE}' to today's mission"),
        ("finance.transaction.created", "transaction", "Ada recorded a 5,000 expense"),
        ("marketing.campaign.launched", "campaign", "Ada launched the spring campaign"),
        ("roadmap.milestone.completed", "roadmap_milestone", "Ada completed milestone 'MVP'"),
        ("member.joined", "membership", "Ben joined the workspace"),
        ("assessment.completed", "assessment", "Ada completed the startup assessment"),
    ]
    for i, (action, entity_type, summary) in enumerate(rows):
        row = create_activity(
            db, startup=s, actor=actor, action=action, entity_type=entity_type, summary=summary
        )
        row.created_at = _at(base, i)
    db.flush()


def _actions(client, h, **params):
    r = client.get("/api/v1/dashboard/activity", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_mentor_feed_hides_mission_task_titles(client, db):
    owner, s = _workspace(db)
    mentor = _join(db, s, MembershipRole.mentor)
    _seed_mixed(db, s, owner)
    db.commit()

    data = _actions(client, _headers(mentor, s))
    assert MISSION_TITLE not in str(data)
    actions = {i["action"] for i in data["items"]}
    assert not any(a.startswith("mission.") for a in actions)
    assert {"roadmap.milestone.completed", "member.joined", "assessment.completed"} <= actions


@pytest.mark.parametrize("role", list(MembershipRole))
def test_finance_and_marketing_rows_follow_module_roles(client, db, role):
    owner, s = _workspace(db)
    viewer = owner if role == MembershipRole.founder else _join(db, s, role)
    _seed_mixed(db, s, owner)
    db.commit()

    actions = {i["action"] for i in _actions(client, _headers(viewer, s))["items"]}
    assert ("finance.transaction.created" in actions) is (role in FINANCE_ROLES)
    assert ("marketing.campaign.launched" in actions) is (role in MARKETING_ROLES)


@pytest.mark.parametrize("role", [MembershipRole.founder, MembershipRole.team_member])
def test_founder_and_team_member_see_everything(client, db, role):
    owner, s = _workspace(db)
    viewer = owner if role == MembershipRole.founder else _join(db, s, role)
    _seed_mixed(db, s, owner)
    create_activity(db, startup=s, action="system.sync", summary="an unclassified row")
    db.commit()

    assert len(_actions(client, _headers(viewer, s))["items"]) == 7


def test_unclassified_actions_are_core_team_only(client, db):
    """A new action namespace nobody classified yet fails closed (core team only)."""
    owner, s = _workspace(db)
    investor = _join(db, s, MembershipRole.investor)
    create_activity(db, startup=s, action="legal.contract.signed", summary="new module row")
    db.commit()

    assert _actions(client, _headers(investor, s))["items"] == []
    assert len(_actions(client, _headers(owner, s))["items"]) == 1


def test_restricted_entity_type_hidden_even_under_open_action(client, db):
    owner, s = _workspace(db)
    mentor = _join(db, s, MembershipRole.mentor)
    create_activity(
        db, startup=s, action="roadmap.task.linked", entity_type="mission_task", summary="x"
    )
    db.commit()

    assert _actions(client, _headers(mentor, s))["items"] == []


def test_filtered_pagination_returns_full_pages(client, db):
    """Hidden rows never shorten a page: a run of mission rows longer than `limit` sits
    between visible rows, and the mentor still gets full pages, then a clean end."""
    owner, s = _workspace(db)
    mentor = _join(db, s, MembershipRole.mentor)
    base = datetime.now(UTC) - timedelta(hours=1)
    plan = (
        ["roadmap.a"] + ["mission.task.added"] * 5 + ["roadmap.b", "roadmap.c"]
        + ["mission.task.completed"] * 4 + ["roadmap.d", "roadmap.e"]
    )  # fmt: skip
    for i, action in enumerate(plan):
        row = create_activity(db, startup=s, actor=owner, action=action, summary=action)
        row.created_at = _at(base, i)
    db.commit()
    h = _headers(mentor, s)

    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        data = _actions(client, h, **params)
        pages += 1
        seen += [i["action"] for i in data["items"]]
        cursor = data["next_cursor"]
        if cursor is None:
            break
        assert len(data["items"]) == 2  # never a short page while more rows exist
    assert seen == ["roadmap.e", "roadmap.d", "roadmap.c", "roadmap.b", "roadmap.a"]
    assert pages == 3

    # The founder pages through every row with the same cursor scheme.
    founder_total, cursor = 0, None
    while True:
        params = {"limit": 5} | ({"cursor": cursor} if cursor else {})
        data = _actions(client, _headers(owner, s), **params)
        founder_total += len(data["items"])
        cursor = data["next_cursor"]
        if cursor is None:
            break
    assert founder_total == len(plan)


def test_activity_query_count_is_constant(client, db):
    """One SELECT for the page regardless of how many rows are filtered out (no N+1)."""
    from sqlalchemy import event

    owner, s = _workspace(db)
    mentor = _join(db, s, MembershipRole.mentor)
    for i in range(30):
        create_activity(
            db, startup=s, action="mission.task.added" if i % 3 else "roadmap.x", summary=str(i)
        )
    db.commit()

    statements: list[str] = []

    def _count(conn, cursor, statement, *args):
        if "activity_log" in statement:
            statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        data = _actions(client, _headers(mentor, s), limit=50)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert len(data["items"]) == 10
    assert len(statements) == 1
