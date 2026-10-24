import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import (
    SALES_ROLES,
    EnrollmentStatus,
    MembershipRole,
    MembershipStatus,
    SequenceStatus,
)
from app.db.models.membership import Membership
from app.db.models.sales import SalesAccount, SalesContact, SalesLead
from app.db.models.sales_sequences import EmailSequence, SequenceEnrollment
from app.db.models.startup import Startup
from app.db.models.user import User, UserProfile
from app.platform.activity import write_activity
from app.schemas.sales_sequences import EnrollIn, SequenceCreate, SequenceUpdate
from app.services.sales.errors import _validation
from app.services.sales.suppression import is_suppressed

MERGE_TAGS = ("first_name", "last_name", "company", "sender_name")
_TAG_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]*)\s*\}\}")


def render_tags(template: str, values: dict[str, str]) -> str:
    """Substitute merge tags. A tag with no value becomes an empty string, never the literal."""
    return _TAG_RE.sub(lambda m: values.get(m.group(1), ""), template)


def check_tags(steps: list[dict[str, Any]]) -> None:
    for i, step in enumerate(steps):
        for field in ("subject", "body"):
            for tag in _TAG_RE.findall(step[field]):
                if tag not in MERGE_TAGS:
                    raise _validation(
                        f"steps.{i}.{field}",
                        f"Unknown merge tag {{{{{tag}}}}}. Use {', '.join(MERGE_TAGS)}.",
                    )


def get_sequence(
    db: Session, startup_id: uuid.UUID, sequence_id: uuid.UUID, *, lock: bool = False
) -> EmailSequence:
    stmt = select(EmailSequence).where(
        EmailSequence.id == sequence_id, EmailSequence.startup_id == startup_id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise NotFound()
    return row


def _counts(db: Session, sequence_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    out: dict[uuid.UUID, dict[str, int]] = {
        sid: {"active": 0, "total": 0, "replied": 0} for sid in sequence_ids
    }
    if not sequence_ids:
        return out
    rows = db.execute(
        select(
            SequenceEnrollment.sequence_id,
            SequenceEnrollment.status,
            func.count(),
        )
        .where(SequenceEnrollment.sequence_id.in_(sequence_ids))
        .group_by(SequenceEnrollment.sequence_id, SequenceEnrollment.status)
    ).all()
    for sid, status, n in rows:
        out[sid]["total"] += n
        if status == EnrollmentStatus.active:
            out[sid]["active"] += n
        if status == EnrollmentStatus.replied:
            out[sid]["replied"] += n
    return out


def serialize(row: EmailSequence, counts: dict[str, int] | None = None) -> dict[str, Any]:
    c = counts or {"active": 0, "total": 0, "replied": 0}
    return {
        "id": str(row.id),
        "name": row.name,
        "steps": row.steps_json,
        "step_count": len(row.steps_json),
        "stop_on_stage_change": row.stop_on_stage_change,
        "status": row.status.value,
        "active_enrollments": c["active"],
        "total_enrollments": c["total"],
        "reply_rate": round(c["replied"] / c["total"], 3) if c["total"] else 0.0,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def list_sequences(
    db: Session, startup_id: uuid.UUID, *, include_archived: bool, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    cond = [EmailSequence.startup_id == startup_id]
    if not include_archived:
        cond.append(EmailSequence.status != SequenceStatus.archived)
    total = db.scalar(select(func.count()).select_from(EmailSequence).where(*cond)) or 0
    rows = list(
        db.scalars(
            select(EmailSequence)
            .where(*cond)
            .order_by(EmailSequence.created_at.desc(), EmailSequence.id)
            .limit(limit)
            .offset(offset)
        )
    )
    counts = _counts(db, [r.id for r in rows])
    return [serialize(r, counts[r.id]) for r in rows], total


def sequence_detail(db: Session, row: EmailSequence) -> dict[str, Any]:
    return serialize(row, _counts(db, [row.id])[row.id])


def create_sequence(
    db: Session, startup_id: uuid.UUID, data: SequenceCreate, *, actor_id: uuid.UUID
) -> EmailSequence:
    steps = [s.model_dump() for s in data.steps]
    check_tags(steps)
    row = EmailSequence(
        startup_id=startup_id,
        name=data.name,
        steps_json=steps,
        stop_on_stage_change=data.stop_on_stage_change,
        status=SequenceStatus.active,
        created_by=actor_id,
    )
    db.add(row)
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.sequence.created",
        summary=f"Created sequence {row.name}",
        actor_user_id=actor_id,
        entity_type="sales_sequence",
        entity_id=row.id,
    )
    return row


def update_sequence(
    db: Session, row: EmailSequence, data: SequenceUpdate, *, actor_id: uuid.UUID
) -> EmailSequence:
    fields = data.model_fields_set
    if "name" in fields and data.name is not None:
        row.name = data.name
    if "steps" in fields and data.steps is not None:
        steps = [s.model_dump() for s in data.steps]
        check_tags(steps)
        row.steps_json = steps
    if "stop_on_stage_change" in fields and data.stop_on_stage_change is not None:
        row.stop_on_stage_change = data.stop_on_stage_change
    if "status" in fields and data.status is not None and data.status != row.status:
        if data.status == SequenceStatus.archived:
            raise _validation("status", "Archive a sequence with DELETE.")
        row.status = data.status
    db.flush()
    write_activity(
        db,
        startup_id=row.startup_id,
        action="sales.sequence.updated",
        summary=f"Updated sequence {row.name}",
        actor_user_id=actor_id,
        entity_type="sales_sequence",
        entity_id=row.id,
        meta={"fields": sorted(fields)},
    )
    return row


def archive_sequence(db: Session, row: EmailSequence, *, actor_id: uuid.UUID) -> int:
    """Archive and stop every live enrollment. Returns how many were stopped."""
    row.status = SequenceStatus.archived
    live = db.scalars(
        select(SequenceEnrollment)
        .where(
            SequenceEnrollment.sequence_id == row.id,
            SequenceEnrollment.status == EnrollmentStatus.active,
        )
        .with_for_update()
    ).all()
    for e in live:
        stop(e, EnrollmentStatus.stopped, "sequence_archived")
    db.flush()
    write_activity(
        db,
        startup_id=row.startup_id,
        action="sales.sequence.archived",
        summary=f"Archived sequence {row.name}",
        actor_user_id=actor_id,
        entity_type="sales_sequence",
        entity_id=row.id,
        meta={"stopped": len(live)},
    )
    return len(live)


# ---- enrollments ----
def stop(e: SequenceEnrollment, status: EnrollmentStatus, reason: str | None) -> None:
    e.status = status
    e.stopped_reason = reason
    e.next_send_at = None


def get_enrollment(
    db: Session, startup_id: uuid.UUID, enrollment_id: uuid.UUID, *, lock: bool = False
) -> SequenceEnrollment:
    stmt = select(SequenceEnrollment).where(
        SequenceEnrollment.id == enrollment_id, SequenceEnrollment.startup_id == startup_id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise NotFound()
    return row


def list_enrollments(
    db: Session,
    startup_id: uuid.UUID,
    sequence_id: uuid.UUID,
    *,
    status: EnrollmentStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[SequenceEnrollment], int]:
    cond = [
        SequenceEnrollment.startup_id == startup_id,
        SequenceEnrollment.sequence_id == sequence_id,
    ]
    if status is not None:
        cond.append(SequenceEnrollment.status == status)
    total = db.scalar(select(func.count()).select_from(SequenceEnrollment).where(*cond)) or 0
    rows = list(
        db.scalars(
            select(SequenceEnrollment)
            .where(*cond)
            .order_by(SequenceEnrollment.created_at.desc(), SequenceEnrollment.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def _not_active() -> AppError:
    return AppError("ENROLLMENT_NOT_ACTIVE", "This enrollment has already ended.", 409)


def mark_replied(
    db: Session, startup_id: uuid.UUID, enrollment_id: uuid.UUID, *, actor_id: uuid.UUID
) -> SequenceEnrollment:
    e = get_enrollment(db, startup_id, enrollment_id, lock=True)
    if e.status != EnrollmentStatus.active:
        raise _not_active()
    stop(e, EnrollmentStatus.replied, "marked_replied")
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.enrollment.replied",
        summary=f"Marked {e.email} as replied",
        actor_user_id=actor_id,
        entity_type="sales_enrollment",
        entity_id=e.id,
    )
    return e


def stop_enrollment(
    db: Session, startup_id: uuid.UUID, enrollment_id: uuid.UUID, *, actor_id: uuid.UUID
) -> SequenceEnrollment:
    e = get_enrollment(db, startup_id, enrollment_id, lock=True)
    if e.status != EnrollmentStatus.active:
        raise _not_active()
    stop(e, EnrollmentStatus.stopped, "stopped_by_member")
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.enrollment.stopped",
        summary=f"Stopped the sequence for {e.email}",
        actor_user_id=actor_id,
        entity_type="sales_enrollment",
        entity_id=e.id,
    )
    return e


def stop_for_contact_stage_change(
    db: Session, startup_id: uuid.UUID, contact_id: uuid.UUID | None
) -> int:
    """A deal whose contact is enrolled moved stage: stop the sequences that opted in."""
    if contact_id is None:
        return 0
    rows = db.scalars(
        select(SequenceEnrollment)
        .join(EmailSequence, EmailSequence.id == SequenceEnrollment.sequence_id)
        .where(
            SequenceEnrollment.startup_id == startup_id,
            SequenceEnrollment.contact_id == contact_id,
            SequenceEnrollment.status == EnrollmentStatus.active,
            EmailSequence.stop_on_stage_change.is_(True),
        )
        .with_for_update(of=SequenceEnrollment)
    ).all()
    for e in rows:
        stop(e, EnrollmentStatus.stopped, "deal_stage_changed")
    return len(rows)


def move_lead_enrollments(
    db: Session, startup_id: uuid.UUID, lead_id: uuid.UUID, contact_id: uuid.UUID
) -> None:
    """Converting a lead keeps its running sequences, now attached to the new contact."""
    db.query(SequenceEnrollment).filter(
        SequenceEnrollment.startup_id == startup_id,
        SequenceEnrollment.lead_id == lead_id,
        SequenceEnrollment.status == EnrollmentStatus.active,
    ).update({SequenceEnrollment.contact_id: contact_id}, synchronize_session=False)


# ---- sender identity ----
def member_identity(db: Session, user_id: uuid.UUID) -> tuple[str, str] | None:
    row = db.execute(
        select(User.email, UserProfile.full_name)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(User.id == user_id)
    ).first()
    if row is None:
        return None
    email, full = str(row[0]), row[1]
    return (full or "").strip() or email.partition("@")[0], email


def _is_sales_member(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID | None) -> bool:
    if user_id is None:
        return False
    return (
        db.scalar(
            select(Membership.id).where(
                Membership.startup_id == startup_id,
                Membership.user_id == user_id,
                Membership.status == MembershipStatus.active,
                Membership.role.in_(list(SALES_ROLES)),
            )
        )
        is not None
    )


def resolve_sender(
    db: Session, e: SequenceEnrollment, seq: EmailSequence
) -> tuple[str, str] | None:
    """Lead call 1: the member who enrolled; if they left, the sequence creator; then the first
    founder. The frozen name/email on the enrollment is refreshed when the fallback is used."""
    if _is_sales_member(db, e.startup_id, e.enrolled_by):
        return e.sender_name, e.sender_email
    candidates: list[uuid.UUID | None] = [seq.created_by]
    founder = db.scalar(
        select(Membership.user_id)
        .where(
            Membership.startup_id == e.startup_id,
            Membership.status == MembershipStatus.active,
            Membership.role == MembershipRole.founder,
        )
        .order_by(Membership.created_at, Membership.id)
        .limit(1)
    )
    candidates.append(founder)
    for uid in candidates:
        if _is_sales_member(db, e.startup_id, uid):
            ident = member_identity(db, uid) if uid else None
            if ident:
                e.sender_name, e.sender_email = ident
                return ident
    return None


def _split(name: str | None, fallback: str) -> tuple[str, str]:
    first, _, rest = (name or fallback or "").strip().partition(" ")
    return first, rest.strip()


def enroll(
    db: Session, seq: EmailSequence, data: EnrollIn, *, actor_id: uuid.UUID
) -> tuple[list[SequenceEnrollment], list[dict[str, str]]]:
    if seq.status == SequenceStatus.archived:
        raise _validation("sequence_id", "This sequence is archived.")
    ident = member_identity(db, actor_id)
    if ident is None:
        raise NotFound()
    startup_id = seq.startup_id
    now = datetime.now(UTC)
    first_due = now + timedelta(days=int(seq.steps_json[0]["wait_days"]))
    enrolled: list[SequenceEnrollment] = []
    skipped: list[dict[str, str]] = []

    def add(
        target_id: uuid.UUID,
        *,
        email: str | None,
        first: str,
        last: str,
        company: str,
        lead_id: uuid.UUID | None,
        contact_id: uuid.UUID | None,
    ) -> None:
        addr = (email or "").strip().lower()
        if not addr:
            skipped.append({"id": str(target_id), "reason": "no_email"})
            return
        if is_suppressed(db, startup_id, addr):
            skipped.append({"id": str(target_id), "reason": "suppressed"})
            return
        row = SequenceEnrollment(
            startup_id=startup_id,
            sequence_id=seq.id,
            lead_id=lead_id,
            contact_id=contact_id,
            email=addr,
            first_name=first[:100],
            last_name=last[:100],
            company=company[:200],
            enrolled_by=actor_id,
            sender_name=ident[0][:200],
            sender_email=ident[1],
            status=EnrollmentStatus.active,
            current_step=0,
            next_send_at=first_due,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            skipped.append({"id": str(target_id), "reason": "already_active"})
            return
        enrolled.append(row)

    for lead_id in dict.fromkeys(data.lead_ids):
        lead = db.scalar(
            select(SalesLead).where(SalesLead.id == lead_id, SalesLead.startup_id == startup_id)
        )
        if lead is None:
            skipped.append({"id": str(lead_id), "reason": "not_found"})
        elif lead.converted_contact_id is not None:
            skipped.append({"id": str(lead_id), "reason": "converted"})
        else:
            first, last = _split(lead.name, lead.company or "")
            add(
                lead_id,
                email=lead.email,
                first=first,
                last=last,
                company=lead.company or "",
                lead_id=lead.id,
                contact_id=None,
            )
    for contact_id in dict.fromkeys(data.contact_ids):
        contact = db.scalar(
            select(SalesContact).where(
                SalesContact.id == contact_id, SalesContact.startup_id == startup_id
            )
        )
        if contact is None:
            skipped.append({"id": str(contact_id), "reason": "not_found"})
            continue
        account = db.get(SalesAccount, contact.account_id) if contact.account_id else None
        add(
            contact_id,
            email=contact.email,
            first=contact.first_name,
            last=contact.last_name or "",
            company=account.name if account else "",
            lead_id=None,
            contact_id=contact.id,
        )
    if enrolled:
        write_activity(
            db,
            startup_id=startup_id,
            action="sales.sequence.enrolled",
            summary=f"Enrolled {len(enrolled)} in {seq.name}",
            actor_user_id=actor_id,
            entity_type="sales_sequence",
            entity_id=seq.id,
            meta={"enrolled": len(enrolled), "skipped": len(skipped)},
        )
    return enrolled, skipped


def startup_name(db: Session, startup_id: uuid.UUID) -> str:
    row = db.get(Startup, startup_id)
    return row.name if row is not None and row.name else "our team"
