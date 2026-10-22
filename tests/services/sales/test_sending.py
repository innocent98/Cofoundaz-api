import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.models.enums import (
    EnrollmentStatus,
    SendKind,
    SendStatus,
    SequenceStatus,
    SuppressionReason,
)
from app.db.models.sales import SalesSettings
from app.db.models.sales_sequences import (
    EmailSequence,
    SalesDraft,
    SalesSuppression,
    SequenceEnrollment,
    SequenceSend,
)
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform import events as events_mod
from app.services.sales import sending as sending_svc
from app.services.sales.stages import get_settings
from app.services.sales.suppression import suppress
from tests.outreach_fakes import FakeSender
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


def _due(db, enrollments, at=NOW):
    for e in enrollments:
        e.next_send_at = at - timedelta(minutes=1)
    db.commit()


def _setup(db, *, waits=(0, 3), n=1):
    u, s = workspace(db)
    seq = sequence(db, s, u, waits=waits)
    cs = [contact(db, s) for _ in range(n)]
    rows, _ = enroll_contacts(db, seq, u, *cs)
    _due(db, rows)
    return u, s, seq, rows


def test_not_configured_does_nothing_and_reports_it(db, monkeypatch):
    monkeypatch.setattr(settings, "SALES_OUTREACH_PROVIDER", "")
    _, _, _, rows = _setup(db)
    res = sending_svc.run_send_tick(db, now=NOW, sender=FakeSender())
    assert res.configured is False and res.sent == 0
    assert db.get(SequenceEnrollment, rows[0].id).current_step == 0


def test_tick_sends_renders_headers_and_schedules_the_next_step(db, cfg):
    u, s, seq, rows = _setup(db)
    fake = FakeSender()
    res = sending_svc.run_send_tick(db, now=NOW, sender=fake)
    assert res.sent == 1
    m = fake.sent[0]
    e = db.get(SequenceEnrollment, rows[0].id)
    assert m.to == e.email and m.reply_to == u.email
    assert m.from_email == "outreach@mail.example.test"
    assert m.from_name == "Fiona Founder at Acme"
    assert m.subject == "Hi Ada step 1"
    assert "Hello Ada from Fiona Founder at ." in m.text  # missing {{company}} -> empty, no literal
    assert "{{" not in m.text and "{{" not in m.html
    assert "Unsubscribe:" in m.text and "/unsubscribe/" in m.html
    assert m.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert m.headers["List-Unsubscribe"].startswith("<") and "/public/sales/unsubscribe/" in (
        m.headers["List-Unsubscribe"]
    )
    send = db.scalar(select(SequenceSend))
    assert m.headers["X-Cofoundaz-Step"] == str(send.id) and m.message_id == send.message_id
    assert send.status == SendStatus.sent and send.sent_at is not None
    assert e.current_step == 1 and e.status == EnrollmentStatus.active
    assert e.next_send_at - send.sent_at == timedelta(days=3)


def test_last_step_completes_the_enrollment(db, cfg):
    _, _, _, rows = _setup(db, waits=(0,))
    sending_svc.run_send_tick(db, now=NOW, sender=FakeSender())
    e = db.get(SequenceEnrollment, rows[0].id)
    assert (e.status, e.next_send_at, e.stopped_reason) == (
        EnrollmentStatus.completed,
        None,
        "finished",
    )


def test_postal_address_is_part_of_the_footer(db, cfg):
    _, s, _, _ = _setup(db)
    get_settings(db, s.id).outreach_postal_address = "1 Marina Road, Lagos"
    db.commit()
    fake = FakeSender()
    sending_svc.run_send_tick(db, now=NOW, sender=fake)
    assert "1 Marina Road, Lagos" in fake.sent[0].text


def test_a_step_is_never_sent_twice(db, cfg):
    _, _, _, rows = _setup(db)
    fake = FakeSender()
    sending_svc.run_send_tick(db, now=NOW, sender=fake)
    # Even if the enrollment is wrongly re-armed for the same step, the ledger row (unique per
    # enrollment+step) stops a second mail: it advances instead of resending.
    e = db.get(SequenceEnrollment, rows[0].id)
    e.current_step = 0
    e.next_send_at = NOW - timedelta(minutes=1)
    db.commit()
    sending_svc.run_send_tick(db, now=NOW + timedelta(days=5), sender=fake)
    steps = [m.subject for m in fake.sent]
    assert steps.count("Hi Ada step 1") == 1


def test_in_flight_step_is_leased_and_not_picked_again(db, cfg):
    _, _, _, rows = _setup(db)
    res = sending_svc.reserve_step(db, rows[0].id, NOW)
    assert isinstance(res, sending_svc.Reservation)
    fake = FakeSender()
    sending_svc.run_send_tick(db, now=NOW + timedelta(minutes=5), sender=fake)
    assert fake.sent == []  # still leased: the first sender may be mid-SMTP


def test_worker_that_died_mid_send_is_never_resent(db, cfg):
    _, _, _, rows = _setup(db)
    sending_svc.reserve_step(db, rows[0].id, NOW)  # reserved, then the process "died"
    fake = FakeSender()
    sending_svc.run_send_tick(db, now=NOW + timedelta(hours=1), sender=fake)
    send = db.scalar(select(SequenceSend))
    e = db.get(SequenceEnrollment, rows[0].id)
    assert fake.sent == [] or fake.sent[0].subject != "Hi Ada step 1"
    assert send.status == SendStatus.unknown and send.error == "interrupted"
    assert e.current_step == 1  # moved on to step 2, not back to step 1


def test_paused_sequence_and_paused_workspace_do_not_send(db, cfg):
    u, s, seq, rows = _setup(db)
    seq.status = SequenceStatus.paused
    db.commit()
    fake = FakeSender()
    assert sending_svc.run_send_tick(db, now=NOW, sender=fake).sent == 0
    seq.status = SequenceStatus.active
    get_settings(db, s.id).paused_at = NOW
    db.commit()
    assert sending_svc.run_send_tick(db, now=NOW, sender=fake).sent == 0
    assert fake.sent == []


def test_suppression_added_after_enrolment_wins_at_send_time(db, cfg):
    _, s, _, rows = _setup(db)
    db.add(SalesSuppression(startup_id=s.id, email=rows[0].email, reason=SuppressionReason.bounced))
    db.commit()
    fake = FakeSender()
    sending_svc.run_send_tick(db, now=NOW, sender=fake)
    e = db.get(SequenceEnrollment, rows[0].id)
    assert fake.sent == [] and e.status == EnrollmentStatus.bounced


def test_unsubscribed_contact_is_never_mailed_by_any_sequence(db, cfg):
    u, s, seq, rows = _setup(db)
    seq2 = sequence(db, s, u)
    c = contact(db, s)
    (e2,), _ = enroll_contacts(db, seq2, u, c)
    _due(db, [e2])
    suppress(db, s.id, c.email, SuppressionReason.unsubscribed)
    db.commit()
    fake = FakeSender()
    sending_svc.run_send_tick(db, now=NOW, sender=fake)
    assert c.email not in {m.to for m in fake.sent}
    assert db.get(SequenceEnrollment, e2.id).status == EnrollmentStatus.unsubscribed


def test_hourly_cap_is_global_across_workspaces_and_stops_the_tick(db, monkeypatch):
    configure(monkeypatch, hourly=2)
    _, _, _, rows_a = _setup(db, n=2)
    _, _, _, rows_b = _setup(db, n=2)
    fake = FakeSender()
    res = sending_svc.run_send_tick(db, now=NOW, sender=fake)
    assert res.sent == 2 and res.hourly_capped is True
    assert sending_svc.run_send_tick(db, now=NOW + timedelta(minutes=10), sender=fake).sent == 0
    # An hour later the mailbox has room again and the remainder goes out, oldest first.
    later = sending_svc.run_send_tick(db, now=NOW + timedelta(hours=1, minutes=1), sender=fake)
    assert later.sent == 2 and len(fake.sent) == 4


def test_daily_workspace_cap_rolls_the_rest_to_tomorrow(db, cfg, monkeypatch):
    monkeypatch.setattr(sending_svc, "DAILY_CAP", 2)
    _, s, _, rows = _setup(db, n=3)
    fake = FakeSender()
    sending_svc.run_send_tick(db, now=NOW, sender=fake)
    assert len(fake.sent) == 2
    left = [e for e in rows if db.get(SequenceEnrollment, e.id).current_step == 0]
    assert len(left) == 1
    assert db.get(SequenceEnrollment, left[0].id).next_send_at == datetime(
        2026, 10, 23, 0, 0, tzinfo=UTC
    )
    sending_svc.run_send_tick(db, now=datetime(2026, 10, 23, 0, 5, tzinfo=UTC), sender=fake)
    assert len(fake.sent) == 3


def test_one_email_per_recipient_per_day_across_sequences(db, cfg):
    u, s, seq, rows = _setup(db)
    other = sequence(db, s, u)
    (e2,), _ = seq_enroll_same_address(db, other, u, rows[0].email)
    _due(db, [e2])
    fake = FakeSender()
    sending_svc.run_send_tick(db, now=NOW, sender=fake)
    assert len(fake.sent) == 1
    states = [db.get(SequenceEnrollment, e.id) for e in (rows[0], e2)]
    deferred = [e for e in states if e.current_step == 0]
    assert len(deferred) == 1  # exactly one of the two went out; the other rolls to tomorrow
    assert deferred[0].next_send_at == datetime(2026, 10, 23, 0, 0, tzinfo=UTC)


def seq_enroll_same_address(db, seq, user, email):
    c = contact(db, db.get(Startup, seq.startup_id), email=email)
    return enroll_contacts(db, seq, user, c)


def test_transient_failure_retries_with_backoff_then_gives_up(db, cfg):
    _, _, _, rows = _setup(db)
    fake = FakeSender()
    fake.fail[rows[0].email] = FakeSender.unavailable()
    t = NOW
    for attempt in (1, 2, 3):
        sending_svc.run_send_tick(db, now=t, sender=fake)
        e = db.get(SequenceEnrollment, rows[0].id)
        send = db.scalar(select(SequenceSend))
        assert send.attempts == attempt and send.status == SendStatus.failed
        if attempt < 3:
            assert e.status == EnrollmentStatus.active and e.current_step == 0
            t = e.next_send_at + timedelta(seconds=1)
    assert (e.status, e.stopped_reason) == (EnrollmentStatus.stopped, "send_failed")
    assert fake.sent == []


def test_permanent_rejection_bounces_suppresses_and_stops_everywhere(db, cfg):
    u, s, seq, rows = _setup(db)
    other = sequence(db, s, u)
    c2 = contact(db, s, email=rows[0].email)
    (e2,), _ = enroll_contacts(db, other, u, c2)
    fake = FakeSender()
    fake.fail[rows[0].email] = FakeSender.rejected()
    res = sending_svc.run_send_tick(db, now=NOW, sender=fake)
    assert res.rejected == 1
    assert db.get(SequenceEnrollment, rows[0].id).status == EnrollmentStatus.bounced
    assert db.get(SequenceEnrollment, e2.id).status == EnrollmentStatus.bounced
    send = db.scalar(select(SequenceSend))
    assert send.status == SendStatus.bounced and send.bounce_code == "550"
    assert sending_svc.sending_status(db, s.id, NOW)["sent_today"] == 1


def test_bounce_rate_over_ten_percent_pauses_the_workspace_and_notifies_founders(
    db, cfg, monkeypatch
):
    published = []
    monkeypatch.setattr(
        events_mod.event_bus,
        "publish",
        lambda _db, name, payload: published.append((name, payload)),
    )
    _, s, _, rows = _setup(db, n=12)
    fake = FakeSender()
    for r in rows[:3]:  # 3 of 12 = 25%
        fake.fail[r.email] = FakeSender.rejected()
    sending_svc.run_send_tick(db, now=NOW, sender=fake)
    st = sending_svc.sending_status(db, s.id, NOW)
    assert st["paused"] is True and st["paused_reason"] == "bounces"
    assert [n for n, _ in published].count("sales.sending.paused") == 1
    # While paused nothing else goes out, and resume clears it.
    before = len(fake.sent)
    sending_svc.run_send_tick(db, now=NOW + timedelta(days=1), sender=fake)
    assert len(fake.sent) == before
    sending_svc.resume_sending(db, s.id)
    assert sending_svc.sending_status(db, s.id, NOW)["paused"] is False


def test_a_single_bounce_in_a_tiny_sample_does_not_pause(db, cfg):
    _, s, _, rows = _setup(db, n=2)
    fake = FakeSender()
    fake.fail[rows[0].email] = FakeSender.rejected()
    sending_svc.run_send_tick(db, now=NOW, sender=fake)
    assert sending_svc.sending_status(db, s.id, NOW)["paused"] is False


def test_sending_status_reports_not_configured_cleanly(db, monkeypatch):
    monkeypatch.setattr(settings, "SALES_OUTREACH_PROVIDER", "")
    _, s = workspace(db)
    st = sending_svc.sending_status(db, s.id, NOW)
    assert st["configured"] is False and st["reason"] == "not_configured"
    assert st["domain"] is None and st["daily_cap"] == 100 and st["sent_today"] == 0


# ---- deal follow-up ----
def _follow_up_setup(db, *, email="buyer@prospect.test"):
    from app.schemas.sales import DealCreate
    from app.services.sales import deals as deals_svc
    from app.services.sales import stages as stages_svc

    u, s = workspace(db)
    stages = {st.name: st for st in stages_svc.ensure_stages(db, s.id)}
    c = contact(db, s, email=email)
    d = deals_svc.create_deal(
        db,
        startup_id=s.id,
        actor_id=u.id,
        data=DealCreate(name="Acme deal", stage_id=stages["Qualified"].id, contact_id=c.id),
    )
    draft = SalesDraft(
        startup_id=s.id,
        kind="follow_up",
        status="ready",
        deal_id=d.id,
        requested_by=u.id,
        output_json={"subject": "s", "body": "b"},
    )
    db.add(draft)
    db.commit()
    return u, s, d, c, draft


def _send_follow_up(db, u, s, d, draft, sender, *, to="buyer@prospect.test"):
    return sending_svc.send_follow_up(
        db,
        startup_id=s.id,
        actor_id=u.id,
        deal_id=d.id,
        to_email=to,
        contact_first="Ada",
        contact_last="Obi",
        company="Bolt",
        draft=draft,
        subject="Following up",
        body="Hi {{first_name}}",
        sender=sender,
    )


def test_follow_up_sends_once_through_the_same_guardrails(db, cfg):
    u, s, d, c, draft = _follow_up_setup(db)
    fake = FakeSender()
    sent = _send_follow_up(db, u, s, d, draft, fake)
    assert sent.kind == SendKind.follow_up and sent.status == SendStatus.sent
    assert fake.sent[0].subject == "Following up" and fake.sent[0].reply_to == u.email
    assert "Hi Ada" in fake.sent[0].text and "Unsubscribe:" in fake.sent[0].text
    with pytest.raises(AppError) as e:
        _send_follow_up(db, u, s, d, draft, fake)
    assert e.value.code == "DRAFT_ALREADY_SENT"


def test_follow_up_refuses_when_unconfigured_paused_suppressed_or_capped(db, cfg, monkeypatch):
    u, s, d, c, draft = _follow_up_setup(db)
    fake = FakeSender()
    get_settings(db, s.id).paused_at = NOW
    db.commit()
    with pytest.raises(AppError) as e:
        _send_follow_up(db, u, s, d, draft, fake)
    assert e.value.code == "SENDING_PAUSED"
    get_settings(db, s.id).paused_at = None
    suppress(db, s.id, "buyer@prospect.test", SuppressionReason.unsubscribed)
    db.commit()
    with pytest.raises(AppError) as e:
        _send_follow_up(db, u, s, d, draft, fake)
    assert e.value.code == "RECIPIENT_SUPPRESSED"
    db.query(SalesSuppression).delete()
    monkeypatch.setattr(settings, "SALES_OUTREACH_PROVIDER", "")
    with pytest.raises(AppError) as e:
        _send_follow_up(db, u, s, d, draft, fake)
    assert e.value.code == "SENDING_NOT_CONFIGURED"
    assert fake.sent == []


def test_follow_up_respects_the_per_recipient_daily_limit_and_frees_the_draft_on_failure(db, cfg):
    u, s, d, c, draft = _follow_up_setup(db)
    fake = FakeSender()
    fake.fail["buyer@prospect.test"] = FakeSender.unavailable()
    with pytest.raises(AppError) as e:
        _send_follow_up(db, u, s, d, draft, fake)
    assert e.value.code == "SEND_FAILED" and e.value.http_status == 503
    db.refresh(draft)
    assert draft.sent_at is None  # the member can retry
    fake.fail.clear()
    _send_follow_up(db, u, s, d, draft, fake)
    draft2 = SalesDraft(
        startup_id=s.id,
        kind="follow_up",
        status="ready",
        deal_id=d.id,
        requested_by=u.id,
        output_json={"subject": "s", "body": "b"},
    )
    db.add(draft2)
    db.commit()
    with pytest.raises(AppError) as e:
        _send_follow_up(db, u, s, d, draft2, fake)
    assert e.value.code == "SEND_LIMIT_REACHED" and e.value.http_status == 429


# ---- concurrency: real transactions, real row locks ----
def test_two_workers_never_send_the_same_step_twice(engine, cfg):
    with Session(engine) as setup:
        u, s = workspace(setup)
        seq = sequence(setup, s, u, waits=(0,))
        c = contact(setup, s)
        rows, _ = enroll_contacts(setup, seq, u, c)
        rows[0].next_send_at = NOW - timedelta(minutes=1)
        ids = (u.id, s.id, rows[0].id)
        setup.commit()
    user_id, startup_id, enrollment_id = ids
    in_send = threading.Event()
    release = threading.Event()
    results: dict[str, object] = {}

    class SlowSender(FakeSender):
        def send(self, msg):
            in_send.set()
            release.wait(10)
            return super().send(msg)

    slow, other = SlowSender(), FakeSender()

    def first():
        with Session(engine) as sess:
            results["first"] = sending_svc.run_send_tick(sess, now=NOW, sender=slow)

    def second():
        in_send.wait(10)
        with Session(engine) as sess:
            results["second"] = sending_svc.run_send_tick(sess, now=NOW, sender=other)

    try:
        t1, t2 = threading.Thread(target=first), threading.Thread(target=second)
        t1.start()
        t2.start()
        t2.join(10)  # the second worker finishes while the first is still inside SMTP
        assert not t2.is_alive()
        assert other.sent == [], "second worker must see the lease and skip"
        release.set()
        t1.join(10)
        assert len(slow.sent) == 1
        with Session(engine) as check:
            n = check.scalar(
                select(SequenceSend.id).where(SequenceSend.enrollment_id == enrollment_id)
            )
            assert n is not None
            assert (
                len(
                    list(
                        check.scalars(
                            select(SequenceSend).where(SequenceSend.enrollment_id == enrollment_id)
                        )
                    )
                )
                == 1
            )
    finally:
        release.set()
        with Session(engine) as cleanup:
            st = cleanup.get(Startup, startup_id)
            if st is not None:
                cleanup.query(SalesSettings).filter_by(startup_id=startup_id).delete()
                cleanup.delete(st)
            cleanup.commit()
            usr = cleanup.get(User, user_id)
            if usr is not None:
                cleanup.delete(usr)
            cleanup.commit()
    assert EmailSequence  # (import kept for the cascade on cleanup)
