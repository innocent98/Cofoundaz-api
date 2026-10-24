import pytest
from sqlalchemy import select

from app.core.errors import AppError, NotFound
from app.db.models.enums import EnrollmentStatus, MembershipRole, SequenceStatus, SuppressionReason
from app.db.models.membership import Membership
from app.db.models.sales_sequences import SequenceEnrollment
from app.schemas.sales import DealCreate
from app.schemas.sales_sequences import EnrollIn, SequenceCreate, SequenceUpdate, StepIn
from app.services.sales import deals as deals_svc
from app.services.sales import leads as leads_svc
from app.services.sales import sequences as seq_svc
from app.services.sales import stages as stages_svc
from app.services.sales.suppression import suppress
from tests.sales_outreach_helpers import (
    NOW,
    contact,
    enroll_contacts,
    lead,
    sequence,
    team_member,
    workspace,
)


def test_unknown_merge_tag_is_rejected_at_save_time(db):
    u, s = workspace(db)
    bad = SequenceCreate(name="x", steps=[StepIn(wait_days=0, subject="Hi {{nope}}", body="b")])
    with pytest.raises(AppError) as e:
        seq_svc.create_sequence(db, s.id, bad, actor_id=u.id)
    assert e.value.http_status == 422 and "nope" in e.value.field_errors[0]["message"]


def test_render_tags_blanks_missing_values_never_the_literal():
    out = seq_svc.render_tags("Hi {{first_name}} {{ company }}{{unknown}}!", {"first_name": "Ada"})
    assert out == "Hi Ada !"


def test_step_count_is_bounded():
    with pytest.raises(ValueError):
        SequenceCreate(name="x", steps=[])
    with pytest.raises(ValueError):
        SequenceCreate(name="x", steps=[StepIn(wait_days=0, subject="s", body="b")] * 11)


def test_update_cannot_archive_and_null_fields_are_rejected(db):
    u, s = workspace(db)
    seq = sequence(db, s, u)
    with pytest.raises(AppError):
        seq_svc.update_sequence(
            db, seq, SequenceUpdate(status=SequenceStatus.archived), actor_id=u.id
        )
    with pytest.raises(ValueError):
        SequenceUpdate.model_validate({"name": None})
    seq_svc.update_sequence(db, seq, SequenceUpdate(status=SequenceStatus.paused), actor_id=u.id)
    assert seq.status == SequenceStatus.paused


def test_enroll_freezes_merge_values_sender_and_first_due_time(db):
    u, s = workspace(db, founder_email="fiona@acme.test")
    seq = sequence(db, s, u, waits=(2, 3))
    c = contact(db, s, email="Ada@Prospect.test")
    rows, skipped = enroll_contacts(db, seq, u, c)
    e = rows[0]
    assert skipped == []
    assert (e.email, e.first_name, e.last_name) == ("ada@prospect.test", "Ada", "Obi")
    assert (e.sender_name, e.sender_email) == ("Fiona Founder", "fiona@acme.test")
    assert e.current_step == 0 and e.status == EnrollmentStatus.active
    assert e.next_send_at is not None


def test_enroll_skip_reasons(db):
    u, s = workspace(db)
    other_u, other_s = workspace(db)
    seq = sequence(db, s, u)
    ok = contact(db, s)
    no_mail = contact(db, s)
    no_mail.email = None
    sup = contact(db, s, email="gone@prospect.test")
    suppress(db, s.id, "gone@prospect.test", SuppressionReason.unsubscribed)
    foreign = contact(db, other_s)
    db.flush()
    enroll_contacts(db, seq, u, ok)
    rows, skipped = seq_svc.enroll(
        db,
        seq,
        EnrollIn(contact_ids=[ok.id, no_mail.id, sup.id, foreign.id]),
        actor_id=u.id,
    )
    reasons = {x["id"]: x["reason"] for x in skipped}
    assert rows == []
    assert reasons == {
        str(ok.id): "already_active",
        str(no_mail.id): "no_email",
        str(sup.id): "suppressed",
        str(foreign.id): "not_found",  # another workspace's contact is invisible
    }


def test_converted_lead_is_skipped_and_archived_sequence_refuses(db):
    u, s = workspace(db)
    seq = sequence(db, s, u)
    ld = lead(db, s)
    leads_svc.convert_lead(db, startup_id=s.id, lead_id=ld.id, actor_id=u.id)
    _, skipped = seq_svc.enroll(db, seq, EnrollIn(lead_ids=[ld.id]), actor_id=u.id)
    assert skipped == [{"id": str(ld.id), "reason": "converted"}]
    seq_svc.archive_sequence(db, seq, actor_id=u.id)
    with pytest.raises(AppError):
        seq_svc.enroll(db, seq, EnrollIn(contact_ids=[contact(db, s).id]), actor_id=u.id)


def test_converting_a_lead_moves_its_running_enrollment_to_the_new_contact(db):
    u, s = workspace(db)
    seq = sequence(db, s, u)
    ld = lead(db, s)
    rows, _ = seq_svc.enroll(db, seq, EnrollIn(lead_ids=[ld.id]), actor_id=u.id)
    res = leads_svc.convert_lead(db, startup_id=s.id, lead_id=ld.id, actor_id=u.id)
    db.expire_all()
    assert db.get(SequenceEnrollment, rows[0].id).contact_id == res.contact.id


def test_replied_and_stop_end_the_enrollment_and_cannot_repeat(db):
    u, s = workspace(db)
    seq = sequence(db, s, u)
    rows, _ = enroll_contacts(db, seq, u, contact(db, s), contact(db, s))
    r = seq_svc.mark_replied(db, s.id, rows[0].id, actor_id=u.id)
    assert (r.status, r.next_send_at) == (EnrollmentStatus.replied, None)
    st = seq_svc.stop_enrollment(db, s.id, rows[1].id, actor_id=u.id)
    assert st.status == EnrollmentStatus.stopped
    for fn in (seq_svc.mark_replied, seq_svc.stop_enrollment):
        with pytest.raises(AppError) as e:
            fn(db, s.id, rows[0].id, actor_id=u.id)
        assert e.value.code == "ENROLLMENT_NOT_ACTIVE" and e.value.http_status == 409


def test_another_workspace_cannot_touch_an_enrollment_or_sequence(db):
    u, s = workspace(db)
    other_u, other_s = workspace(db)
    seq = sequence(db, s, u)
    rows, _ = enroll_contacts(db, seq, u, contact(db, s))
    with pytest.raises(NotFound):
        seq_svc.mark_replied(db, other_s.id, rows[0].id, actor_id=other_u.id)
    with pytest.raises(NotFound):
        seq_svc.get_sequence(db, other_s.id, seq.id)


def test_stage_change_stops_only_sequences_that_opted_in(db):
    u, s = workspace(db)
    stages = {st.name: st for st in stages_svc.ensure_stages(db, s.id)}
    c = contact(db, s)
    opted = sequence(db, s, u, stop_on_stage_change=True)
    plain = sequence(db, s, u)
    (e1,), _ = enroll_contacts(db, opted, u, c)
    (e2,), _ = enroll_contacts(db, plain, u, c)
    deal = deals_svc.create_deal(
        db,
        startup_id=s.id,
        actor_id=u.id,
        data=DealCreate(name="D", stage_id=stages["Qualified"].id, contact_id=c.id),
    )
    deals_svc.move_stage(
        db, startup_id=s.id, deal_id=deal.id, stage_id=stages["Proposal"].id, actor_id=u.id
    )
    assert (e1.status, e1.stopped_reason) == (EnrollmentStatus.stopped, "deal_stage_changed")
    assert e2.status == EnrollmentStatus.active


def test_archive_stops_live_enrollments(db):
    u, s = workspace(db)
    seq = sequence(db, s, u)
    (e,), _ = enroll_contacts(db, seq, u, contact(db, s))
    assert seq_svc.archive_sequence(db, seq, actor_id=u.id) == 1
    assert (e.status, e.stopped_reason) == (EnrollmentStatus.stopped, "sequence_archived")


def test_sender_falls_back_to_creator_then_first_founder_when_enroller_left(db):
    founder, s = workspace(db, founder_email="fiona@acme.test")
    tm = team_member(db, s)
    seq = sequence(db, s, founder)
    (e,), _ = enroll_contacts(db, seq, tm, contact(db, s))
    assert seq_svc.resolve_sender(db, e, seq) == (e.sender_name, e.sender_email)
    mem = db.scalar(select(Membership).where(Membership.user_id == tm.id))
    mem.role = MembershipRole.mentor  # no longer a sales member
    db.flush()
    name, email = seq_svc.resolve_sender(db, e, seq)
    assert email == "fiona@acme.test" and name == "Fiona Founder"
    assert e.sender_email == "fiona@acme.test"  # refreshed on the enrollment itself


def test_list_counts_and_reply_rate(db):
    u, s = workspace(db)
    seq = sequence(db, s, u)
    rows, _ = enroll_contacts(db, seq, u, contact(db, s), contact(db, s))
    seq_svc.mark_replied(db, s.id, rows[0].id, actor_id=u.id)
    items, total = seq_svc.list_sequences(db, s.id, include_archived=False, limit=10, offset=0)
    assert total == 1
    assert (items[0]["active_enrollments"], items[0]["total_enrollments"]) == (1, 2)
    assert items[0]["reply_rate"] == 0.5 and items[0]["step_count"] == 2
    assert NOW  # silence unused
