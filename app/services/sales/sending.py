"""Outreach sending: reservations, caps, the due-step scheduler and the deal follow-up send.

THE INVARIANTS (the lead reviews these hardest)
-----------------------------------------------
1. A step is never sent twice. Sending is at-most-once: a `sequence_sends` row is committed as a
   `pending` reservation BEFORE the SMTP call, under `FOR UPDATE SKIP LOCKED` on the enrollment, and
   `UNIQUE (enrollment_id, step_index)` makes a second reservation impossible whatever races. The
   enrollment is leased (`next_send_at` pushed out) while the mail is in flight. If the worker dies
   after the reservation, the row is later marked `unknown` and the step is skipped, not resent: a
   missed email is recoverable, a duplicate to a prospect is not.
2. Caps are exact. Every reservation takes one transaction-scoped Postgres advisory lock, then
   COUNTS the ledger (`sequence_sends`) and inserts, so concurrent workers serialize on the lock and
   cannot both see room. No counter exists to drift: the ledger is the count.
3. Suppressed, unsubscribed and bounced addresses are re-checked at send time, under the lock, so a
   suppression that landed after enrolment still wins.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy import func, select, text
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
from app.db.models.sales_sequences import (
    EmailSequence,
    SalesDraft,
    SalesSuppression,
    SequenceEnrollment,
    SequenceSend,
)
from app.platform.outreach import (
    OutreachAmbiguousError,
    OutreachNotConfiguredError,
    OutreachRejectedError,
    OutreachSender,
    OutreachUnavailableError,
    get_outreach_sender,
    outreach_configured,
    outreach_domain,
)
from app.services.sales import sequences as seq_svc
from app.services.sales.outreach_render import RenderInput, message_id_for, render_message
from app.services.sales.stages import get_settings
from app.services.sales.suppression import (
    _STOP_STATUS,
    maybe_auto_pause,
    sent_today,
    suppress,
)

log = logging.getLogger(__name__)
_T = TypeVar("_T")

# Arbitrary constant key for pg_advisory_xact_lock: one lock serializes every reservation, which is
# the right granularity for ONE shared mailbox (the hourly cap is global).
OUTREACH_LOCK_KEY = 0x5A1E5B01
DAILY_CAP = 100  # per workspace per UTC day, D-SAL-8
LEASE = timedelta(minutes=30)  # how long an in-flight step stays out of the due scan
MAX_ATTEMPTS = 3
RETRY_BACKOFF = timedelta(minutes=15)
TICK_BATCH = 200
_COUNTED = (SendStatus.pending, SendStatus.sent, SendStatus.bounced, SendStatus.unknown)

HOURLY = "hourly"
WORKSPACE_DAY = "workspace_day"
HOURLY_SHARE = "hourly_share"
RECIPIENT_DAY = "recipient_day"


@dataclass
class Reservation:
    send_id: uuid.UUID
    enrollment_id: uuid.UUID | None
    startup_id: uuid.UUID


@dataclass
class TickResult:
    configured: bool = True
    sent: int = 0
    rejected: int = 0
    failed: int = 0
    unknown: int = 0
    deferred: int = 0
    hourly_capped: bool = False


def _present(row: _T | None) -> _T:
    """A row we just wrote or locked must exist; a missing one is a bug, surfaced loudly (an
    `assert` would vanish under `python -O`)."""
    if row is None:
        raise LookupError("expected row is missing")
    return row


def hourly_limit() -> int:
    return max(1, settings.SALES_OUTREACH_HOURLY_LIMIT)


def _day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def next_day_start(now: datetime) -> datetime:
    return _day_start(now) + timedelta(days=1)


def _lock_ledger(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": OUTREACH_LOCK_KEY})


def cap_blocker(
    db: Session, startup_id: uuid.UUID, email: str, now: datetime, share: int | None = None
) -> str | None:
    """Which cap (if any) stops one more email now. MUST be called holding the ledger lock."""
    hour_ago = now - timedelta(hours=1)
    in_hour = (
        db.scalar(
            select(func.count())
            .select_from(SequenceSend)
            .where(SequenceSend.reserved_at > hour_ago, SequenceSend.status.in_(_COUNTED))
        )
        or 0
    )
    if in_hour >= hourly_limit():
        return HOURLY
    if share is not None:
        own = (
            db.scalar(
                select(func.count())
                .select_from(SequenceSend)
                .where(
                    SequenceSend.startup_id == startup_id,
                    SequenceSend.reserved_at > hour_ago,
                    SequenceSend.status.in_(_COUNTED),
                )
            )
            or 0
        )
        if own >= share:
            return HOURLY_SHARE  # this workspace used its slice while others are waiting
    if sent_today(db, startup_id, now) >= DAILY_CAP:
        return WORKSPACE_DAY
    to_recipient = (
        db.scalar(
            select(func.count())
            .select_from(SequenceSend)
            .where(
                SequenceSend.startup_id == startup_id,
                SequenceSend.recipient_email == email,
                SequenceSend.reserved_at >= _day_start(now),
                SequenceSend.status.in_(_COUNTED),
            )
        )
        or 0
    )
    return RECIPIENT_DAY if to_recipient >= 1 else None


def _suppression_for(db: Session, startup_id: uuid.UUID, email: str) -> SuppressionReason | None:
    return db.scalar(
        select(SalesSuppression.reason).where(
            SalesSuppression.startup_id == startup_id, SalesSuppression.email == email
        )
    )


def _advance(e: SequenceEnrollment, seq: EmailSequence, sent_at: datetime) -> None:
    e.current_step += 1
    if e.current_step >= len(seq.steps_json):
        e.status = EnrollmentStatus.completed
        e.stopped_reason = "finished"
        e.next_send_at = None
    else:
        e.next_send_at = sent_at + timedelta(days=int(seq.steps_json[e.current_step]["wait_days"]))


# ---- reserve (phase 1) ----
def reserve_step(
    db: Session, enrollment_id: uuid.UUID, now: datetime, share: int | None = None
) -> Reservation | str | None:
    """Decide and book one due step. Commits. Returns a Reservation to deliver, HOURLY when the
    mailbox is at its hourly cap (caller stops the whole tick), or None (nothing to send)."""
    e = db.scalar(
        select(SequenceEnrollment)
        .where(SequenceEnrollment.id == enrollment_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if e is None:  # another worker holds it right now
        db.rollback()
        return None
    result = _reserve_locked(db, e, now, share)
    if isinstance(result, str):
        db.rollback()  # nothing was written for a hard stop; release the lock
        return result
    db.commit()
    return result


def _reserve_locked(
    db: Session, e: SequenceEnrollment, now: datetime, share: int | None = None
) -> Reservation | str | None:
    if e.status != EnrollmentStatus.active or e.next_send_at is None or e.next_send_at > now:
        return None
    seq = db.get(EmailSequence, e.sequence_id)
    if seq is None or seq.status != SequenceStatus.active:
        return None
    if get_settings(db, e.startup_id).paused_at is not None:
        return None
    if e.current_step >= len(seq.steps_json):
        seq_svc.stop(e, EnrollmentStatus.completed, "finished")
        return None
    reason = _suppression_for(db, e.startup_id, e.email)
    if reason is not None:
        seq_svc.stop(e, _STOP_STATUS[reason], reason.value)
        return None
    if seq_svc.resolve_sender(db, e, seq) is None:
        seq_svc.stop(e, EnrollmentStatus.stopped, "no_sender")
        return None

    _lock_ledger(db)
    existing = db.scalar(
        select(SequenceSend).where(
            SequenceSend.enrollment_id == e.id, SequenceSend.step_index == e.current_step
        )
    )
    if existing is not None:
        verdict = _settle_existing(e, seq, existing, now)
        if verdict is not None:
            return None
    blocker = cap_blocker(db, e.startup_id, e.email, now, share)
    if blocker == HOURLY:
        return HOURLY
    if blocker == HOURLY_SHARE:
        return None  # stays due; the other workspaces get their turn this hour
    if blocker is not None:
        e.next_send_at = next_day_start(now)  # rolls to tomorrow, keeping its place in the queue
        return None

    if existing is None:
        send_id = uuid.uuid4()
        db.add(
            SequenceSend(
                id=send_id,
                startup_id=e.startup_id,
                enrollment_id=e.id,
                step_index=e.current_step,
                kind=SendKind.sequence,
                recipient_email=e.email,
                sent_by=e.enrolled_by,
                message_id=message_id_for(send_id),
                status=SendStatus.pending,
                attempts=1,
                reserved_at=now,
            )
        )
    else:  # a retry of a failed attempt reuses its row (and Message-ID)
        send_id = existing.id
        existing.status = SendStatus.pending
        existing.attempts += 1
        existing.reserved_at = now
        existing.error = None
    e.next_send_at = now + LEASE
    db.flush()
    return Reservation(send_id=send_id, enrollment_id=e.id, startup_id=e.startup_id)


def _settle_existing(
    e: SequenceEnrollment, seq: EmailSequence, existing: SequenceSend, now: datetime
) -> str | None:
    """A ledger row already exists for this step. Returns None to go on and (re)send, else a
    verdict meaning 'handled, do not send now'."""
    if existing.status == SendStatus.failed:
        if existing.attempts >= MAX_ATTEMPTS:
            seq_svc.stop(e, EnrollmentStatus.stopped, "send_failed")
            return "stopped"
        return None
    if existing.status == SendStatus.pending:
        if now - existing.reserved_at < LEASE:
            return "in_flight"  # lease not over: the first sender may still be mid-SMTP
        existing.status = SendStatus.unknown  # died mid-send: never resend a maybe-delivered mail
        existing.error = "interrupted"
    # sent / bounced / unknown: the step already went out, so just move on
    _advance(e, seq, existing.sent_at or existing.reserved_at)
    return "advanced"


# ---- deliver (phase 2) ----
def _render_for(db: Session, res: Reservation, send: SequenceSend):  # type: ignore[no-untyped-def]
    e = db.get(SequenceEnrollment, res.enrollment_id) if res.enrollment_id else None
    e = _present(e)
    seq = db.get(EmailSequence, e.sequence_id)
    seq = _present(seq)
    step = seq.steps_json[send.step_index or 0]
    return render_message(
        RenderInput(
            send_id=send.id,
            to=e.email,
            subject=step["subject"],
            body=step["body"],
            first_name=e.first_name,
            last_name=e.last_name,
            company=e.company,
            sender_name=e.sender_name,
            sender_email=e.sender_email,
            startup_name=seq_svc.startup_name(db, e.startup_id),
            postal_address=get_settings(db, e.startup_id).outreach_postal_address,
        )
    )


def deliver(db: Session, res: Reservation, sender: OutreachSender) -> str:
    """Send a reserved step and settle the ledger. Returns 'sent' | 'rejected' | 'failed'."""
    send = db.get(SequenceSend, res.send_id)
    send = _present(send)
    msg = _render_for(db, res, send)
    db.rollback()  # no transaction is held open across the network call
    if not _still_sendable(db, res):
        return "skipped"
    try:
        sender.send(msg)
    except OutreachAmbiguousError as exc:
        _settle_unknown(db, res, str(exc) or "ambiguous")
        return "unknown"
    except OutreachRejectedError as exc:
        _settle_rejected(db, res, exc)
        return "rejected"
    except OutreachUnavailableError as exc:
        _settle_failed(db, res, str(exc))
        return "failed"
    except Exception as exc:  # noqa: BLE001 - an unexpected sender bug must not strand the step
        log.warning(f"[outreach] unexpected send error: {type(exc).__name__}")
        _settle_failed(db, res, type(exc).__name__)
        return "failed"
    _settle_sent(db, res, msg.message_id)
    return "sent"


def _still_sendable(db: Session, res: Reservation) -> bool:
    """Last look right before SMTP: an unsubscribe, bounce or Stop that landed since the
    reservation wins. Settles the ledger row itself when the step must not go out."""
    send, e = _locked_pair(db, res)
    reason = _suppression_for(db, send.startup_id, send.recipient_email)
    if reason is None and (e is None or e.status == EnrollmentStatus.active):
        db.rollback()
        return True
    send.status = SendStatus.failed
    send.error = "suppressed" if reason is not None else "enrollment_ended"
    if reason is not None and e is not None and e.status == EnrollmentStatus.active:
        seq_svc.stop(e, _STOP_STATUS[reason], reason.value)
    db.commit()
    return False


def _settle_unknown(db: Session, res: Reservation, error: str) -> None:
    """Delivery could not be confirmed after DATA started. Never retried: count it as sent for
    the caps and move the enrollment on."""
    send, e = _locked_pair(db, res)
    now = datetime.now(UTC)
    send.status = SendStatus.unknown
    send.error = ("ambiguous: " + error)[:300]
    send.sent_at = now
    if e is not None and e.status == EnrollmentStatus.active:
        seq = db.get(EmailSequence, e.sequence_id)
        if seq is not None:
            _advance(e, seq, now)
    db.commit()


def _locked_pair(db: Session, res: Reservation) -> tuple[SequenceSend, SequenceEnrollment | None]:
    send = db.scalar(
        select(SequenceSend)
        .where(SequenceSend.id == res.send_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    send = _present(send)
    e = None
    if res.enrollment_id is not None:
        e = db.scalar(
            select(SequenceEnrollment)
            .where(SequenceEnrollment.id == res.enrollment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return send, e


def _settle_sent(db: Session, res: Reservation, provider_id: str) -> None:
    send, e = _locked_pair(db, res)
    now = datetime.now(UTC)
    send.status = SendStatus.sent
    send.sent_at = now
    send.provider_message_id = provider_id
    send.error = None
    if e is not None and e.status == EnrollmentStatus.active:
        seq = db.get(EmailSequence, e.sequence_id)
        if seq is not None:
            _advance(e, seq, now)
    # Bounces that landed before the sample was large enough are judged as sends accumulate.
    maybe_auto_pause(db, send.startup_id, now=now)
    db.commit()


def _settle_failed(db: Session, res: Reservation, error: str) -> None:
    send, e = _locked_pair(db, res)
    send.status = SendStatus.failed
    send.error = error[:300]
    if e is not None and e.status == EnrollmentStatus.active:
        if send.attempts >= MAX_ATTEMPTS:
            seq_svc.stop(e, EnrollmentStatus.stopped, "send_failed")
        else:
            e.next_send_at = datetime.now(UTC) + RETRY_BACKOFF * send.attempts
    db.commit()


def _settle_rejected(db: Session, res: Reservation, exc: OutreachRejectedError) -> None:
    """The mail server refused the recipient outright (5xx at SMTP time): that is a hard bounce."""
    send, _ = _locked_pair(db, res)
    send.status = SendStatus.bounced
    send.bounce_code = exc.code[:20]
    send.error = exc.detail[:300]
    suppress(db, send.startup_id, send.recipient_email, SuppressionReason.bounced)
    maybe_auto_pause(db, send.startup_id)
    db.commit()


# ---- the scheduled tick ----
def fair_order(
    items: list[tuple[uuid.UUID, uuid.UUID]],
) -> tuple[list[uuid.UUID], int | None]:
    """Interleave due steps across workspaces (each keeps its own oldest-first order) so one
    workspace's backlog cannot starve the others inside a tick, and return each workspace's
    hourly slice: the global cap split between the workspaces waiting right now (None when only
    one workspace is waiting, which may use all of it)."""
    queues: dict[uuid.UUID, list[uuid.UUID]] = {}
    for enrollment_id, startup_id in items:
        queues.setdefault(startup_id, []).append(enrollment_id)
    waiting = len(queues)
    ordered: list[uuid.UUID] = []
    while queues:
        for startup_id in list(queues):
            ordered.append(queues[startup_id].pop(0))
            if not queues[startup_id]:
                del queues[startup_id]
    share = max(1, hourly_limit() // waiting) if waiting > 1 else None
    return ordered, share


def run_send_tick(
    db: Session, *, now: datetime | None = None, sender: OutreachSender | None = None
) -> TickResult:
    """Send every due step, oldest first, until the mailbox's hourly cap. Safe to run from several
    workers at once: each step is claimed with SKIP LOCKED and booked in the ledger."""
    if not outreach_configured():
        return TickResult(configured=False)
    now = now or datetime.now(UTC)
    sender = sender or get_outreach_sender()
    out = TickResult()
    rows = db.execute(
        select(SequenceEnrollment.id, SequenceEnrollment.startup_id)
        .join(EmailSequence, EmailSequence.id == SequenceEnrollment.sequence_id)
        .where(
            SequenceEnrollment.status == EnrollmentStatus.active,
            SequenceEnrollment.next_send_at <= now,
            EmailSequence.status == SequenceStatus.active,
        )
        .order_by(SequenceEnrollment.next_send_at, SequenceEnrollment.id)
        .limit(TICK_BATCH)
    ).all()
    db.rollback()  # end the read transaction; each step runs in its own
    due, share = fair_order([(r[0], r[1]) for r in rows])
    for enrollment_id in due:
        verdict = reserve_step(db, enrollment_id, now, share)
        if verdict == HOURLY:
            out.hourly_capped = True
            break
        if isinstance(verdict, Reservation):
            outcome = deliver(db, verdict, sender)
            if outcome == "sent":
                out.sent += 1
            elif outcome == "rejected":
                out.rejected += 1
            elif outcome == "unknown":
                out.unknown += 1
            elif outcome == "skipped":
                out.deferred += 1
            else:
                out.failed += 1
        else:
            out.deferred += 1
    return out


# ---- status ----
def sending_status(db: Session, startup_id: uuid.UUID, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    configured = outreach_configured()
    st = get_settings(db, startup_id)
    return {
        "configured": configured,
        "reason": None if configured else "not_configured",
        "domain": outreach_domain() if configured else None,
        "sent_today": sent_today(db, startup_id, now),
        "daily_cap": DAILY_CAP,
        "hourly_cap": hourly_limit() if configured else None,
        "paused": st.paused_at is not None,
        "paused_reason": st.paused_reason,
        "paused_at": st.paused_at.isoformat() if st.paused_at else None,
    }


def resume_sending(db: Session, startup_id: uuid.UUID) -> None:
    st = get_settings(db, startup_id)
    st.paused_at = None
    st.paused_reason = None
    db.flush()


# ---- deal follow-up: one email, same sender, same guardrails ----
def send_follow_up(
    db: Session,
    *,
    startup_id: uuid.UUID,
    actor_id: uuid.UUID,
    deal_id: uuid.UUID,
    to_email: str | None,
    contact_first: str,
    contact_last: str,
    company: str,
    draft: SalesDraft,
    subject: str,
    body: str,
    sender: OutreachSender | None = None,
) -> SequenceSend:
    if not outreach_configured():
        raise AppError("SENDING_NOT_CONFIGURED", "Sending isn't set up yet.", 409)
    if get_settings(db, startup_id).paused_at is not None:
        raise AppError("SENDING_PAUSED", "Sales emails are paused for this workspace.", 409)
    addr = (to_email or "").strip().lower()
    if not addr:
        raise AppError(
            "VALIDATION_ERROR",
            "This deal's contact has no email address.",
            422,
            field_errors=[{"field": "contact", "message": "Add an email to the contact."}],
        )
    ident = seq_svc.member_identity(db, actor_id)
    if ident is None:
        raise AppError("FORBIDDEN", "Sender not found.", 403)

    now = datetime.now(UTC)
    _lock_ledger(db)  # lock first, then look: a suppression decided meanwhile is seen
    if _suppression_for(db, startup_id, addr) is not None:
        raise AppError("RECIPIENT_SUPPRESSED", "This contact opted out or bounced.", 409)
    locked = db.scalar(
        select(SalesDraft)
        .where(SalesDraft.id == draft.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    locked = _present(locked)
    if locked.sent_at is not None:
        raise AppError("DRAFT_ALREADY_SENT", "This follow-up was already sent.", 409)
    blocker = cap_blocker(db, startup_id, addr, now)
    if blocker is not None:
        messages = {
            HOURLY: "The outreach mailbox is at its hourly limit. Try again later.",
            WORKSPACE_DAY: "You reached today's sending limit for this workspace.",
            RECIPIENT_DAY: "This contact already got an email today.",
        }
        raise AppError("SEND_LIMIT_REACHED", messages[blocker], 429)
    send_id = uuid.uuid4()
    send = SequenceSend(
        id=send_id,
        startup_id=startup_id,
        enrollment_id=None,
        step_index=None,
        kind=SendKind.follow_up,
        recipient_email=addr,
        deal_id=deal_id,
        sent_by=actor_id,
        message_id=message_id_for(send_id),
        status=SendStatus.pending,
        attempts=1,
        reserved_at=now,
    )
    db.add(send)
    locked.sent_at = now  # claimed: a double click or a retry cannot send it twice
    db.flush()
    msg = render_message(
        RenderInput(
            send_id=send_id,
            to=addr,
            subject=subject,
            body=body,
            first_name=contact_first,
            last_name=contact_last,
            company=company,
            sender_name=ident[0],
            sender_email=ident[1],
            startup_name=seq_svc.startup_name(db, startup_id),
            postal_address=get_settings(db, startup_id).outreach_postal_address,
        )
    )
    db.commit()  # reservation durable; the SMTP call runs outside any lock
    try:
        (sender or get_outreach_sender()).send(msg)
    except OutreachNotConfiguredError as exc:
        _release_follow_up(db, send_id, draft.id, "not_configured")
        raise AppError("SENDING_NOT_CONFIGURED", "Sending isn't set up yet.", 409) from exc
    except OutreachAmbiguousError as exc:
        # The mail may have gone: keep the draft claimed and count it, never invite a blind retry.
        unsure = _present(db.get(SequenceSend, send_id))
        unsure.status, unsure.error = SendStatus.unknown, "ambiguous"
        unsure.sent_at = datetime.now(UTC)
        db.commit()
        raise AppError(
            "SEND_UNCERTAIN",
            "We couldn't confirm this email was delivered. Check before sending it again.",
            503,
        ) from exc
    except OutreachRejectedError as exc:
        bounced = _present(db.get(SequenceSend, send_id))
        bounced.status, bounced.bounce_code = SendStatus.bounced, exc.code[:20]
        bounced.error = exc.detail[:300]
        suppress(db, startup_id, addr, SuppressionReason.bounced)
        locked_draft = db.get(SalesDraft, draft.id)
        if locked_draft is not None:
            locked_draft.sent_at = None
        db.commit()
        raise AppError("RECIPIENT_REJECTED", "The mail server rejected this address.", 422) from exc
    except OutreachUnavailableError as exc:
        _release_follow_up(db, send_id, draft.id, str(exc))
        raise AppError("SEND_FAILED", "The email could not be sent. Try again.", 503) from exc
    row = _present(db.get(SequenceSend, send_id))
    row.status, row.sent_at = SendStatus.sent, datetime.now(UTC)
    row.provider_message_id = msg.message_id
    db.flush()
    return row


def _release_follow_up(db: Session, send_id: uuid.UUID, draft_id: uuid.UUID, error: str) -> None:
    """The mail never left: free the draft so the member can retry, and keep the failed row."""
    row = db.get(SequenceSend, send_id)
    draft = db.get(SalesDraft, draft_id)
    if row is not None:
        row.status, row.error = SendStatus.failed, error[:300]
    if draft is not None:
        draft.sent_at = None
    db.commit()
