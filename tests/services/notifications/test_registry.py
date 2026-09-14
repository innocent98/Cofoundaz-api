from app.db.models.enums import MembershipRole
from app.db.models.notification import Notification
from app.platform.events import DispatchingEventBus
from app.services.notifications.registry import SPECS, _handle
from tests.factories import create_membership, create_startup, create_user


def _workspace(db):
    owner = create_user(db)
    s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    other = create_user(db)
    create_membership(db, other, s, role=MembershipRole.team_member)
    db.flush()
    return owner, other, s


def test_members_minus_actor(db):
    owner, other, s = _workspace(db)
    # owner shares a document -> everyone in the workspace EXCEPT owner is notified
    _handle(db, "document.shared", {"startup_id": str(s.id), "shared_by": str(owner.id)})
    rows = db.query(Notification).all()
    assert {r.user_id for r in rows} == {other.id}
    assert rows[0].type == "document.shared"


def test_members_minus_actor_real_document_shared_payload(db):
    owner, other, s = _workspace(db)
    # real payload shape produced by app.services.documents.shares.create_share
    _handle(
        db,
        "document.shared",
        {
            "startup_id": str(s.id),
            "document_id": "00000000-0000-0000-0000-0000000000aa",
            "share_id": "00000000-0000-0000-0000-0000000000bb",
            "shared_by_id": str(owner.id),
        },
    )
    rows = db.query(Notification).all()
    assert owner.id not in {r.user_id for r in rows}
    assert {r.user_id for r in rows} == {other.id}
    assert rows[0].type == "document.shared"


def test_member_joined_notifies_existing_members_not_joiner(db):
    owner, other, s = _workspace(db)
    joiner = create_user(db)
    _handle(
        db,
        "workspace.member.joined",
        {"startup_id": str(s.id), "user_id": str(joiner.id), "role": "team_member"},
    )
    rows = db.query(Notification).all()
    assert {r.user_id for r in rows} == {owner.id, other.id}
    assert rows[0].type == "workspace.member.joined"


def test_all_specs_have_generic_copy(db):
    # every handled event renders a non-empty title from its payload without KeyError
    for _event, spec in SPECS.items():
        payload = {"startup_id": "00000000-0000-0000-0000-000000000000"}
        assert isinstance(spec.title(payload), str) and spec.title(payload)


def test_handle_unknown_event_noop(db):
    _handle(db, "not.registered", {"startup_id": "x"})  # must not raise / create nothing
    assert db.query(Notification).count() == 0


def test_register_subscribes_on_bus(db):
    from app.services.notifications import registry

    bus = DispatchingEventBus()
    registry.register(bus)  # register onto a fresh bus
    owner, other, s = _workspace(db)
    bus.publish(db, "document.shared", {"startup_id": str(s.id), "shared_by": str(owner.id)})
    assert db.query(Notification).filter_by(user_id=other.id).count() == 1
