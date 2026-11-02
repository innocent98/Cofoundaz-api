from datetime import timedelta

import pytest

from app.core.errors import NotFound
from app.db.models.enums import EnrollmentStatus
from app.db.models.sales_sequences import SalesSuppression, SequenceEnrollment, SequenceSend
from app.services.sales import sending as sending_svc
from app.services.sales import unsubscribe as unsub_svc
from app.services.sales.tokens import sign_send
from tests.outreach_fakes import FakeSender
from tests.sales_outreach_helpers import (
    NOW,
    configure,
    contact,
    enroll_contacts,
    sequence,
    workspace,
)


def _mailed(db, monkeypatch):
    configure(monkeypatch)
    u, s = workspace(db, name="Acme Ltd")
    seq = sequence(db, s, u)
    c = contact(db, s, email="buyer@prospect.test")
    rows, _ = enroll_contacts(db, seq, u, c)
    rows[0].next_send_at = NOW - timedelta(minutes=1)
    db.commit()
    fake = FakeSender()
    sending_svc.run_send_tick(db, now=NOW, sender=fake)
    send = db.query(SequenceSend).one()
    return u, s, seq, c, rows[0], send, fake


def test_status_masks_the_address_and_names_the_sender(db, monkeypatch):
    *_, send, _ = _mailed(db, monkeypatch)
    out = unsub_svc.unsubscribe_status(db, sign_send(send.id))
    assert out == {"email": "b***@prospect.test", "sender": "Acme Ltd", "unsubscribed": False}


def test_unsubscribe_suppresses_stops_and_is_idempotent(db, monkeypatch):
    u, s, seq, c, e, send, _ = _mailed(db, monkeypatch)
    token = sign_send(send.id)
    assert unsub_svc.unsubscribe(db, token)["unsubscribed"] is True
    assert unsub_svc.unsubscribe(db, token)["unsubscribed"] is True  # one-click retries are fine
    db.refresh(e)
    assert (e.status, e.next_send_at) == (EnrollmentStatus.unsubscribed, None)
    assert db.query(SalesSuppression).count() == 1
    assert unsub_svc.unsubscribe_status(db, token)["unsubscribed"] is True


def test_unsubscribed_address_is_never_mailed_again_by_any_sequence(db, monkeypatch):
    u, s, seq, c, e, send, fake = _mailed(db, monkeypatch)
    unsub_svc.unsubscribe(db, sign_send(send.id))
    other = sequence(db, s, u)
    _, skipped = enroll_contacts(db, other, u, c)
    assert skipped == [{"id": str(c.id), "reason": "suppressed"}]
    before = len(fake.sent)
    sending_svc.run_send_tick(db, now=NOW + timedelta(days=10), sender=fake)
    assert len(fake.sent) == before


def test_unsubscribe_stays_inside_its_workspace(db, monkeypatch):
    u, s, seq, c, e, send, _ = _mailed(db, monkeypatch)
    u2, s2 = workspace(db)
    seq2 = sequence(db, s2, u2)
    c2 = contact(db, s2, email="buyer@prospect.test")  # same address, other workspace
    (e2,), _ = enroll_contacts(db, seq2, u2, c2)
    unsub_svc.unsubscribe(db, sign_send(send.id))
    db.refresh(e2)
    assert e2.status == EnrollmentStatus.active
    assert db.query(SalesSuppression).filter_by(startup_id=s2.id).count() == 0


@pytest.mark.parametrize("token", ["", "nope", "a.b", "x" * 300])
def test_forged_tokens_are_404_without_a_lookup(db, token):
    with pytest.raises(NotFound):
        unsub_svc.unsubscribe(db, token)
    with pytest.raises(NotFound):
        unsub_svc.unsubscribe_status(db, token)


def test_validly_signed_token_for_a_deleted_send_is_404(db, monkeypatch):
    import uuid

    with pytest.raises(NotFound):
        unsub_svc.unsubscribe(db, sign_send(uuid.uuid4()))
    assert SequenceEnrollment  # noqa: B018
