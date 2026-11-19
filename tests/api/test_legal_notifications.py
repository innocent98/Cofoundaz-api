from datetime import UTC, date, datetime

import pytest

from app.db.models.enums import MembershipRole
from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.services.notifications import registry
from tests.api.test_legal import _member

COMPLIANCE_PAYLOAD = {
    "title": "VAT filing",
    "due_on": date(2026, 7, 18).isoformat(),
    "days_out": 3,
}
IP_PAYLOAD = {
    "name": "Kolo (wordmark)",
    "renewal_on": date(2026, 7, 22).isoformat(),
    "days_out": 7,
}


def _workspace_with_every_role(db):
    founder, startup, _h = _member(db)
    others = {}
    for role in (
        MembershipRole.team_member,
        MembershipRole.legal_advisor,
        MembershipRole.mentor,
        MembershipRole.accountant,
        MembershipRole.business_consultant,
        MembershipRole.investor,
    ):
        u, _s, _hh = _member(db, role=role, startup=startup)
        others[role] = u
    db.flush()
    return founder, startup, others


@pytest.mark.parametrize(
    "event,payload",
    [
        ("legal.compliance.due_soon", COMPLIANCE_PAYLOAD),
        ("legal.compliance.overdue", COMPLIANCE_PAYLOAD),
        ("legal.ip.renewal_due_soon", IP_PAYLOAD),
    ],
)
def test_legal_reminders_reach_only_the_legal_reader_set(db, event, payload):
    registry.register()
    founder, startup, others = _workspace_with_every_role(db)
    event_bus.publish(db, event, {"startup_id": str(startup.id), **payload})
    db.flush()

    notes = db.query(Notification).filter_by(type=event).all()
    recipients = {n.user_id for n in notes}
    # The titles name the filing, and the four excluded roles are 403 on every legal route, so
    # delivering to them would leak exactly what the routes withhold (spec D4).
    assert recipients == {
        founder.id,
        others[MembershipRole.team_member].id,
        others[MembershipRole.legal_advisor].id,
    }
    for role in (
        MembershipRole.mentor,
        MembershipRole.accountant,
        MembershipRole.business_consultant,
        MembershipRole.investor,
    ):
        assert others[role].id not in recipients, f"{role.value} should receive nothing"


def test_a_mentor_cannot_see_a_legal_notification_even_if_one_exists(db):
    from app.services.notifications.audience import audience_visible

    # AUDIENCE drives the read filter as well as fan-out, so a legacy row written before the
    # rule landed is hidden rather than deleted.
    for event in (
        "legal.compliance.due_soon",
        "legal.compliance.overdue",
        "legal.ip.renewal_due_soon",
    ):
        assert audience_visible(MembershipRole.founder, event, {}) is True
        assert audience_visible(MembershipRole.team_member, event, {}) is True
        assert audience_visible(MembershipRole.legal_advisor, event, {}) is True
        assert audience_visible(MembershipRole.mentor, event, {}) is False
        assert audience_visible(MembershipRole.accountant, event, {}) is False


def test_the_title_names_the_filing_and_the_real_distance(db):
    registry.register()
    _f, startup, _o = _workspace_with_every_role(db)
    event_bus.publish(
        db,
        "legal.compliance.due_soon",
        {"startup_id": str(startup.id), **COMPLIANCE_PAYLOAD},
    )
    db.flush()
    note = db.query(Notification).filter_by(type="legal.compliance.due_soon").first()
    # days_out is computed, so a due14 claim firing three days out still reads "3 days" (D22).
    assert note.title == "Compliance filing due in 3 days: VAT filing"


def test_the_ip_title_names_the_asset(db):
    registry.register()
    _f, startup, _o = _workspace_with_every_role(db)
    event_bus.publish(
        db, "legal.ip.renewal_due_soon", {"startup_id": str(startup.id), **IP_PAYLOAD}
    )
    db.flush()
    note = db.query(Notification).filter_by(type="legal.ip.renewal_due_soon").first()
    assert note.title == "IP renewal due in 7 days: Kolo (wordmark)"


# --- the new category -------------------------------------------------------------------------


def test_legal_is_a_notification_category_with_email_off_by_default(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/notifications/preferences", headers=h)
    assert r.status_code == 200, r.text
    cats = r.json()["data"]["categories"]
    # No migration needed: preferences are JSONB read with a code-side default (spec D20).
    # But the EMAIL default is OFF, not ON: a category added after the original five starts off
    # (D-NOT-2) so nobody begins receiving a new kind of email unasked. In-app is always on.
    assert "legal" in cats and cats["legal"] is False


def test_a_put_that_omits_legal_leaves_it_on(client, db):
    _u, _s, h = _member(db)
    # Today's FE hard-codes five categories and does not send `legal` (spec D23). The merge only
    # copies keys it recognises, so an omitted category keeps its stored value.
    r = client.put(
        "/api/v1/notifications/preferences",
        json={
            "master_email": True,
            "categories": {
                "documents": False,
                "business": True,
                "roadmap_missions": True,
                "health_assessment": True,
                "team": True,
            },
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    cats = r.json()["data"]["categories"]
    assert cats["legal"] is False  # untouched, keeps its stored default
    assert cats["documents"] is False  # the ones it did send still apply


def test_legal_email_can_be_switched_on_explicitly(client, db):
    _u, _s, h = _member(db)
    r = client.put(
        "/api/v1/notifications/preferences",
        json={"master_email": True, "categories": {"legal": True}},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["categories"]["legal"] is True
    assert datetime.now(UTC).year >= 2026


# --- D-LEG-6: the overdue filing is critical, all three sit in the Deadlines chip ---------------


def test_compliance_overdue_is_a_locked_on_critical_type():
    from app.services.notifications.categories import CRITICAL_TYPES, is_critical

    assert "legal.compliance.overdue" in CRITICAL_TYPES
    assert is_critical("legal.compliance.overdue")
    # A due-soon nudge and a renewal reminder stay switchable.
    assert "legal.compliance.due_soon" not in CRITICAL_TYPES
    assert "legal.ip.renewal_due_soon" not in CRITICAL_TYPES


def test_every_legal_event_is_in_the_deadlines_inbox_filter():
    from app.services.notifications.categories import FILTER_TYPES

    assert {
        "legal.compliance.due_soon",
        "legal.compliance.overdue",
        "legal.ip.renewal_due_soon",
    } <= FILTER_TYPES["deadlines"]


# --- slice 2: the advisor request notification (row 47) ---------------------------------------

REQUEST_PAYLOAD = {
    "request_id": "11111111-1111-1111-1111-111111111111",
    "founder_name": "Ada Founder",
    "document_title": "Supplier Agreement",
}


def _publish_request(db, startup, advisor_user_id):
    event_bus.publish(
        db,
        "legal.advisor_request.created",
        {
            "startup_id": str(startup.id),
            "advisor_user_id": str(advisor_user_id),
            **REQUEST_PAYLOAD,
        },
    )
    db.flush()
    return db.query(Notification).filter_by(type="legal.advisor_request.created").all()


def test_an_advisor_request_reaches_only_the_named_advisor(db):
    registry.register()
    founder, startup, others = _workspace_with_every_role(db)
    advisor = others[MembershipRole.legal_advisor]

    notes = _publish_request(db, startup, advisor.id)

    # Unlike the slice-1 reminders, this one is addressed to ONE person: the founder who asked
    # does not need telling, and the other legal readers have no part in it (row 47).
    assert {n.user_id for n in notes} == {advisor.id}
    assert founder.id not in {n.user_id for n in notes}
    assert others[MembershipRole.team_member].id not in {n.user_id for n in notes}


def test_a_second_advisor_is_not_told_about_someone_elses_request(db):
    registry.register()
    _founder, startup, others = _workspace_with_every_role(db)
    named = others[MembershipRole.legal_advisor]
    other_advisor, _s, _h = _member(db, role=MembershipRole.legal_advisor, startup=startup)
    db.flush()

    notes = _publish_request(db, startup, named.id)

    assert {n.user_id for n in notes} == {named.id}
    assert other_advisor.id not in {n.user_id for n in notes}


def test_a_removed_advisor_is_told_nothing(db):
    from app.db.models.enums import MembershipStatus
    from app.db.models.membership import Membership

    registry.register()
    _founder, startup, others = _workspace_with_every_role(db)
    advisor = others[MembershipRole.legal_advisor]
    row = db.query(Membership).filter_by(user_id=advisor.id, startup_id=startup.id).one()
    row.status = MembershipStatus.removed
    db.flush()

    # The payload names them, but the payload was written when the event fired and is read when
    # it is processed. Trusting it would email a stranger about a legal document.
    assert _publish_request(db, startup, advisor.id) == []


def test_a_user_who_is_not_an_advisor_is_told_nothing(db):
    registry.register()
    _founder, startup, others = _workspace_with_every_role(db)

    # A team member's id in `advisor_user_id` is not a shortcut into the notification: the role
    # is re-checked, not taken from the payload.
    assert _publish_request(db, startup, others[MembershipRole.team_member].id) == []


def test_a_payload_with_no_advisor_tells_nobody(db):
    registry.register()
    _founder, startup, _others = _workspace_with_every_role(db)
    event_bus.publish(
        db,
        "legal.advisor_request.created",
        {"startup_id": str(startup.id), **REQUEST_PAYLOAD},
    )
    db.flush()
    assert db.query(Notification).filter_by(type="legal.advisor_request.created").all() == []


def test_the_request_title_names_the_founder_and_the_document(db):
    registry.register()
    _founder, startup, others = _workspace_with_every_role(db)
    notes = _publish_request(db, startup, others[MembershipRole.legal_advisor].id)
    assert notes[0].title == "Ada Founder requested your review of Supplier Agreement"


def test_the_request_copy_carries_no_email_address(db):
    registry.register()
    _founder, startup, others = _workspace_with_every_role(db)
    notes = _publish_request(db, startup, others[MembershipRole.legal_advisor].id)

    # D-LEG-15: the advisor may be emailed about their own request, but nothing they receive may
    # show another member's address. Names only, which is why the payload carries `founder_name`
    # rather than an id the renderer would have to resolve.
    assert "@" not in notes[0].title
    assert "@" not in (notes[0].body or "")


def test_a_mentor_cannot_see_an_advisor_request_notification(db):
    from app.services.notifications.audience import audience_visible

    event = "legal.advisor_request.created"
    assert audience_visible(MembershipRole.founder, event, {}) is True
    assert audience_visible(MembershipRole.team_member, event, {}) is True
    assert audience_visible(MembershipRole.legal_advisor, event, {}) is True
    assert audience_visible(MembershipRole.mentor, event, {}) is False
    assert audience_visible(MembershipRole.investor, event, {}) is False


def test_the_advisor_request_event_emails_under_the_legal_category():
    from app.services.notifications.categories import category_for

    assert category_for("legal.advisor_request.created") == "legal"
