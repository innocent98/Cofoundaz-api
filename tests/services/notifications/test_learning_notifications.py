from datetime import UTC, datetime

from app.db.models.enums import MembershipRole, MembershipStatus, StartupStage
from app.db.models.notification import Notification
from app.platform.events import DispatchingEventBus
from app.services.learning import recommendations as rec
from app.services.learning import service as svc
from app.services.notifications import registry
from app.services.notifications.audience import AUDIENCE, audience_visible
from tests.factories import create_membership, create_startup, create_user

CORE = {MembershipRole.founder, MembershipRole.team_member}


def _setup(db, monkeypatch):
    bus = DispatchingEventBus()
    registry.register(bus)
    monkeypatch.setattr(svc, "event_bus", bus)
    monkeypatch.setattr(rec, "event_bus", bus)
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    owner.profile.full_name = "Ada Founder"
    s = create_startup(db, owner=owner, stage=StartupStage.idea)
    members = {}
    for role in MembershipRole:
        u = owner if role == MembershipRole.founder else create_user(db)
        members[role] = (u, create_membership(db, u, s, role=role))
    db.flush()
    return s, members


def _notes(db, s, type_):
    return db.query(Notification).filter_by(startup_id=s.id, type=type_).all()


def test_both_types_are_core_audience_and_in_app_only():
    for event in ("learning.course.completed", "learning.course.recommended"):
        assert AUDIENCE[event] == CORE
        assert event in registry.SPECS
        assert registry.category_for(event) is None  # in-app only, no email
        assert audience_visible(MembershipRole.mentor, event, {}) is False
        assert audience_visible(MembershipRole.team_member, event, {}) is True


def test_finishing_a_course_notifies_only_the_learner(db, academy, monkeypatch):
    s, members = _setup(db, monkeypatch)
    founder, _m = members[MembershipRole.founder]
    mate, _mm = members[MembershipRole.team_member]
    for n in (1, 2, 3):
        svc.update_lesson_progress(
            db, s.id, mate.id, f"build-scope-the-mvp-{n}", completed=True, position_s=None
        )
    [note] = _notes(db, s, "learning.course.completed")
    assert note.user_id == mate.id  # not the founder, not a mentor
    assert note.title == "Certificate earned: Scope the MVP \U0001f393"
    assert note.body == "Download it"
    assert note.data["certificate_id"] and note.data["course_id"] == "build-scope-the-mvp"
    assert founder.id != note.user_id


def test_a_learner_who_left_the_workspace_gets_nothing(db, academy, monkeypatch):
    s, members = _setup(db, monkeypatch)
    mate, membership = members[MembershipRole.team_member]
    membership.status = MembershipStatus.removed
    db.flush()
    for n in (1, 2, 3):
        svc.update_lesson_progress(
            db, s.id, mate.id, f"build-scope-the-mvp-{n}", completed=True, position_s=None
        )
    assert _notes(db, s, "learning.course.completed") == []


def test_a_new_hero_after_a_stage_change_notifies_the_reader(db, academy, monkeypatch):
    s, members = _setup(db, monkeypatch)
    founder, _m = members[MembershipRole.founder]
    state = svc.load_state(db, s.id, founder.id)
    svc.build_recommendations(db, s, founder.id, state)
    assert _notes(db, s, "learning.course.recommended") == []  # the first shelf is not news
    s.stage = StartupStage.launch
    db.flush()
    hero = svc.build_recommendations(db, s, founder.id, state)["hero"]["course_id"]
    [note] = _notes(db, s, "learning.course.recommended")
    assert note.user_id == founder.id and note.data["course_id"] == hero
    assert note.title.startswith("New course recommended for your stage: ")
    assert note.body == "Start course"
