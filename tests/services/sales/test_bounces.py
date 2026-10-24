import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models.enums import EnrollmentStatus, SendStatus
from app.db.models.sales_sequences import (
    ImapProcessed,
    SalesSuppression,
    SequenceEnrollment,
    SequenceSend,
)
from app.platform.outreach import OutreachUnavailableError
from app.services.sales import bounces as bounce_svc
from app.services.sales import sending as sending_svc
from app.services.sales.stages import get_settings
from tests.outreach_fakes import FakeMailbox, FakeSender, dsn
from tests.sales_outreach_helpers import (
    NOW,
    configure,
    contact,
    enroll_contacts,
    sequence,
    workspace,
)


@pytest.fixture
def cfg(monkeypatch):
    configure(monkeypatch)


def _sent(db, n=1, waits=(0, 3)):
    u, s = workspace(db)
    seq = sequence(db, s, u, waits=waits)
    rows, _ = enroll_contacts(db, seq, u, *[contact(db, s) for _ in range(n)])
    for e in rows:
        e.next_send_at = NOW - timedelta(minutes=1)
    db.commit()
    sending_svc.run_send_tick(db, now=NOW, sender=FakeSender())
    sends = list(
        db.scalars(select(SequenceSend).order_by(SequenceSend.reserved_at, SequenceSend.id))
    )
    by_enrollment = {x.enrollment_id: x for x in sends}
    return s, rows, [by_enrollment[e.id] for e in rows]


def test_parse_recognises_a_permanent_failure_report_and_extracts_our_id():
    sid = uuid.uuid4()
    p = bounce_svc.parse_bounce(dsn(send_id=sid, recipient="Gone@Prospect.test"))
    assert p is not None
    assert (p.send_id, p.status, p.recipient) == (sid, "5.1.1", "gone@prospect.test")


def test_parse_falls_back_to_the_message_id_when_our_header_was_stripped():
    sid = uuid.uuid4()
    p = bounce_svc.parse_bounce(dsn(send_id=sid, recipient="a@b.test", with_header=False))
    assert p is not None and p.send_id == sid


def test_parse_ignores_soft_bounces_ordinary_mail_and_garbage():
    assert (
        bounce_svc.parse_bounce(dsn(send_id=uuid.uuid4(), recipient="a@b.test", status="4.2.2"))
        is None
    )
    assert bounce_svc.parse_bounce(b"From: a@b.test\nSubject: hi\n\nhello") is None
    assert bounce_svc.parse_bounce(b"\xff\xfe not mail at all") is None


def test_poll_applies_a_bounce_suppresses_stops_and_marks_the_mail(db, cfg):
    s, rows, sends = _sent(db)
    box = FakeMailbox(messages=[(7, dsn(send_id=sends[0].id, recipient=rows[0].email))])
    res = bounce_svc.poll_bounces(db, box)
    assert (res.seen, res.bounced) == (1, 1) and box.processed == [7]
    assert db.get(SequenceEnrollment, rows[0].id).status == EnrollmentStatus.bounced
    db.refresh(sends[0])
    assert sends[0].status == SendStatus.bounced and sends[0].bounce_code == "5.1.1"
    assert db.scalar(select(SalesSuppression.email)) == rows[0].email
    assert db.scalar(select(ImapProcessed.outcome).where(ImapProcessed.uid == 7)) == "bounced"


def test_poll_is_idempotent_across_repeats_and_restarts(db, cfg):
    s, rows, sends = _sent(db, n=2)
    msgs = [
        (3, dsn(send_id=sends[0].id, recipient=rows[0].email)),
        (4, b"From: friend@x.test\nSubject: lunch?\n\nhi"),
    ]
    box = FakeMailbox(messages=list(msgs))
    first = bounce_svc.poll_bounces(db, box)
    second = bounce_svc.poll_bounces(db, FakeMailbox(messages=list(msgs)))  # same mail, new poller
    assert (first.bounced, first.ignored) == (1, 1)
    assert (second.seen, second.bounced) == (0, 0)  # both UIDs are in the ledger already
    assert db.query(ImapProcessed).count() == 3  # the two messages + the first-run baseline
    assert box.processed == [3]  # only real bounces are moved; other mail is left alone


def test_a_uid_already_in_the_ledger_is_not_applied_again(db, cfg):
    s, rows, sends = _sent(db)
    db.add(ImapProcessed(uidvalidity=1, uid=9, outcome="bounced"))
    db.commit()
    box = FakeMailbox(messages=[(9, dsn(send_id=sends[0].id, recipient=rows[0].email))])
    # fetch_after would not return it, but a racing poller could: the claim must refuse it
    box.fetch_after = lambda last: box.messages  # type: ignore[method-assign]
    res = bounce_svc.poll_bounces(db, box)
    assert res.bounced == 0
    assert db.get(SequenceEnrollment, rows[0].id).status == EnrollmentStatus.active


def test_uidvalidity_change_starts_a_fresh_namespace(db, cfg):
    s, rows, sends = _sent(db, n=2)
    bounce_svc.poll_bounces(
        db,
        FakeMailbox(
            uidvalidity=1, messages=[(5, dsn(send_id=sends[0].id, recipient=rows[0].email))]
        ),
    )
    res = bounce_svc.poll_bounces(
        db,
        FakeMailbox(
            uidvalidity=2, messages=[(1, dsn(send_id=sends[1].id, recipient=rows[1].email))]
        ),
    )
    assert res.bounced == 1


def test_unmatched_and_wrong_recipient_reports_change_nothing(db, cfg):
    s, rows, sends = _sent(db)
    box = FakeMailbox(
        messages=[
            (1, dsn(send_id=uuid.uuid4(), recipient="x@y.test")),
            (2, dsn(send_id=sends[0].id, recipient="someone.else@y.test")),
            (3, dsn(send_id=None, recipient="x@y.test")),
        ]
    )
    res = bounce_svc.poll_bounces(db, box)
    assert (res.unmatched, res.bounced) == (3, 0) and box.processed == []
    assert db.get(SequenceEnrollment, rows[0].id).status == EnrollmentStatus.active
    assert db.query(SalesSuppression).count() == 0


def test_report_without_a_recipient_fails_closed(db, cfg):
    s, rows, sends = _sent(db)
    raw = dsn(send_id=sends[0].id, recipient="x@y.test").replace(
        b"Final-Recipient: rfc822; x@y.test\n", b""
    )
    res = bounce_svc.poll_bounces(db, FakeMailbox(messages=[(1, raw)]))
    assert (res.bounced, res.unmatched) == (0, 1)
    assert db.get(SequenceEnrollment, rows[0].id).status == EnrollmentStatus.active


def test_bounce_never_crosses_workspaces(db, cfg):
    s1, rows1, sends1 = _sent(db)
    s2, rows2, _ = _sent(db)
    bounce_svc.poll_bounces(
        db, FakeMailbox(messages=[(1, dsn(send_id=sends1[0].id, recipient=rows1[0].email))])
    )
    assert db.scalar(select(SalesSuppression.startup_id)) == s1.id
    assert db.get(SequenceEnrollment, rows2[0].id).status != EnrollmentStatus.bounced


def test_bounce_rate_pauses_the_workspace(db, cfg):
    s, rows, sends = _sent(db, n=10, waits=(0,))
    msgs = [(i + 1, dsn(send_id=sends[i].id, recipient=rows[i].email)) for i in range(2)]
    bounce_svc.poll_bounces(db, FakeMailbox(messages=msgs))  # 2 of 10 = 20%
    st = get_settings(db, s.id)
    assert st.paused_at is not None and st.paused_reason == "bounces"


def test_unreachable_mailbox_is_survived(db, cfg):
    class Broken(FakeMailbox):
        def fetch_after(self, last_uid):
            raise OutreachUnavailableError("down")

    box = Broken()
    res = bounce_svc.poll_bounces(db, box)
    assert res.seen == 0 and box.closed is False  # a passed-in mailbox is the caller's to close


def test_poll_reports_not_configured_without_touching_anything(db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SALES_OUTREACH_PROVIDER", "")
    assert bounce_svc.poll_bounces(db).configured is False


def test_first_run_starts_at_uidnext_and_never_backfills_a_shared_inbox(db, cfg):
    s, rows, sends = _sent(db)
    old = dsn(send_id=sends[0].id, recipient=rows[0].email)
    box = FakeMailbox(messages=[(3, old)], uidnext=50)  # 49 messages already existed
    res = bounce_svc.poll_bounces(db, box)
    assert res.seen == 0 and box.processed == []
    assert db.get(SequenceEnrollment, rows[0].id).status == EnrollmentStatus.active
    assert db.scalar(select(ImapProcessed.outcome)) == "baseline"
    # mail that arrives after the baseline is read
    box.messages.append((50, old))
    assert bounce_svc.poll_bounces(db, box).bounced == 1
