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
