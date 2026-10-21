from datetime import UTC, datetime, timedelta

from app.db.models.enums import ClosedReason, MembershipRole, MembershipStatus
from app.db.models.notification import Notification
from app.platform.events import DispatchingEventBus
from app.schemas.sales import DealCreate
from app.services.notifications import registry
from app.services.notifications.audience import AUDIENCE, audience_visible
from app.services.notifications.service import list_notifications, unread_count
from app.services.sales import deals as deals_svc
from app.services.sales import stages as stages_svc
from app.services.sales.stall import notify_stalled
from tests.factories import create_membership, create_startup, create_user

NOW = datetime.now(UTC)
CORE = {MembershipRole.founder, MembershipRole.team_member}


_members: dict = {}


def _team(db, monkeypatch):
    bus = DispatchingEventBus()
    registry.register(bus)
    monkeypatch.setattr(deals_svc, "event_bus", bus)
    import app.services.sales.stall as stall_mod

    monkeypatch.setattr(stall_mod, "event_bus", bus)
    owner = create_user(db, email_verified_at=NOW)
    s = create_startup(db, owner=owner)
    users = {}
    for role in MembershipRole:
        u = owner if role == MembershipRole.founder else create_user(db)
        _members[u.id] = create_membership(db, u, s, role=role)
        users[role] = u
    db.flush()
    stages = {st.name: st for st in stages_svc.ensure_stages(db, s.id)}
    return s, users, stages


def _deal(db, s, users, stages, **kw):
    return deals_svc.create_deal(
        db,
        startup_id=s.id,
        actor_id=users[MembershipRole.founder].id,
        data=DealCreate(name="Acme", stage_id=stages["Qualified"].id, **kw),
    )


def _move(db, s, users, d, stage):
    deals_svc.move_stage(
        db,
        startup_id=s.id,
        deal_id=d.id,
        stage_id=stage.id,
        actor_id=users[MembershipRole.team_member].id,
    )


def _notes(db, s, type_):
    return db.query(Notification).filter_by(startup_id=s.id, type=type_).all()


def _stall(db, s, d):
    d.last_activity_at = NOW - timedelta(days=20)
    db.flush()
    assert notify_stalled(db, deal_id=d.id, startup_id=s.id, now=NOW)


def test_types_are_core_audience_and_in_app_only():
    assert AUDIENCE["sales.deal.won"] == CORE and AUDIENCE["sales.deal.stalled"] == CORE
    assert "sales.deal.stage_changed" not in registry.SPECS
    assert "sales.deal.lost" not in registry.SPECS
    assert registry.category_for("sales.deal.won") is None
    assert registry.category_for("sales.deal.stalled") is None


def test_stalled_goes_to_owner_with_copy(db, monkeypatch):
    s, users, stages = _team(db, monkeypatch)
    owner = users[MembershipRole.team_member]
    d = _deal(db, s, users, stages, owner_id=owner.id)
    _stall(db, s, d)
    [n] = _notes(db, s, "sales.deal.stalled")
    assert n.user_id == owner.id
    assert n.title == "Deal stalled: Acme has had no activity for 20 days"
    assert n.body == "Suggest a follow-up"
    assert n.data["href"] == f"/sales?deal={d.id}&followup=1"


def test_stalled_falls_back_to_founders_when_owner_left_or_missing(db, monkeypatch):
    s, users, stages = _team(db, monkeypatch)
    founder = users[MembershipRole.founder]
    leaver = users[MembershipRole.team_member]
    d = _deal(db, s, users, stages, owner_id=leaver.id)
    _members[leaver.id].status = MembershipStatus.removed
    db.flush()
    _stall(db, s, d)
    assert {n.user_id for n in _notes(db, s, "sales.deal.stalled")} == {founder.id}


def test_stalled_without_owner_goes_to_all_founders_never_mentor(db, monkeypatch):
    s, users, stages = _team(db, monkeypatch)
    second = create_user(db)
    create_membership(db, second, s, role=MembershipRole.founder)
    db.flush()
    d = _deal(db, s, users, stages)
    _stall(db, s, d)
    got = {n.user_id for n in _notes(db, s, "sales.deal.stalled")}
    assert got == {users[MembershipRole.founder].id, second.id}


def test_stalled_with_no_active_founder_notifies_nobody(db, monkeypatch):
    s, users, stages = _team(db, monkeypatch)
    d = _deal(db, s, users, stages)
    _members[users[MembershipRole.founder].id].status = MembershipStatus.removed
    db.flush()
    _stall(db, s, d)
    assert _notes(db, s, "sales.deal.stalled") == []


def test_won_notifies_core_including_mover_never_mentor(db, monkeypatch):
    s, users, stages = _team(db, monkeypatch)
    d = _deal(db, s, users, stages)
    _move(db, s, users, d, stages["Closed Won"])
    notes = _notes(db, s, "sales.deal.won")
    assert {n.user_id for n in notes} == {
        users[MembershipRole.founder].id,
        users[MembershipRole.team_member].id,  # the mover is included
    }
    assert all(n.title == "Deal moved to Closed Won: Acme 🎉" for n in notes)


def test_won_deduped_per_deal_per_utc_day_and_renotifies_next_day(db, monkeypatch):
    s, users, stages = _team(db, monkeypatch)
    d = _deal(db, s, users, stages)
    _move(db, s, users, d, stages["Closed Won"])
    _move(db, s, users, d, stages["Qualified"])  # reopen
    _move(db, s, users, d, stages["Closed Won"])  # won again, same day
    assert len(_notes(db, s, "sales.deal.won")) == 2  # one per recipient, not four

    for n in _notes(db, s, "sales.deal.won"):
        n.created_at = n.created_at - timedelta(days=1)
    db.flush()
    _move(db, s, users, d, stages["Qualified"])
    _move(db, s, users, d, stages["Closed Won"])
    assert len(_notes(db, s, "sales.deal.won")) == 4


def test_won_dedupe_is_per_deal(db, monkeypatch):
    s, users, stages = _team(db, monkeypatch)
    a, b = _deal(db, s, users, stages), _deal(db, s, users, stages)
    _move(db, s, users, a, stages["Closed Won"])
    _move(db, s, users, b, stages["Closed Won"])
    assert len(_notes(db, s, "sales.deal.won")) == 4


def test_lost_and_stage_changes_create_no_notifications(db, monkeypatch):
    s, users, stages = _team(db, monkeypatch)
    d = _deal(db, s, users, stages)
    _move(db, s, users, d, stages["Proposal"])
    deals_svc.move_stage(
        db,
        startup_id=s.id,
        deal_id=d.id,
        stage_id=stages["Closed Lost"].id,
        actor_id=users[MembershipRole.founder].id,
        closed_reason=ClosedReason.price,
    )
    assert db.query(Notification).filter_by(startup_id=s.id).count() == 0


def test_mentor_is_not_a_recipient_and_read_filter_hides_the_types(db, monkeypatch):
    s, users, stages = _team(db, monkeypatch)
    mentor = users[MembershipRole.mentor]
    d = _deal(db, s, users, stages)
    _move(db, s, users, d, stages["Closed Won"])
    _stall(db, s, _deal(db, s, users, stages))
    assert all(n.user_id != mentor.id for n in db.query(Notification).filter_by(startup_id=s.id))

    # a legacy/forged row addressed to the mentor stays invisible on every read path
    for type_ in ("sales.deal.won", "sales.deal.stalled"):
        db.add(Notification(user_id=mentor.id, startup_id=s.id, type=type_, title="t", data={}))
        assert not audience_visible(MembershipRole.mentor, type_, {})
    db.flush()
    kw = {"user_id": mentor.id, "startup_id": s.id, "role": MembershipRole.mentor}
    assert list_notifications(db, unread=False, limit=20, cursor=None, **kw)[0] == []
    assert unread_count(db, **kw) == 0
