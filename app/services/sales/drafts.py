import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import DraftKind, DraftStatus
from app.db.models.sales import SalesAccount, SalesContact
from app.db.models.sales_sequences import EmailSequence, SalesDraft
from app.platform.jobs import job_dispatcher
from app.services.sales.deals import get_deal
from app.services.sales.errors import _validation

JOB = "ai.sales.draft"


def get_draft(db: Session, startup_id: uuid.UUID, draft_id: uuid.UUID) -> SalesDraft:
    row = db.scalar(
        select(SalesDraft).where(SalesDraft.id == draft_id, SalesDraft.startup_id == startup_id)
    )
    if row is None:
        raise NotFound()
    return row


def _queue(db: Session, row: SalesDraft) -> SalesDraft:
    db.add(row)
    db.flush()
    job_dispatcher.enqueue(
        db, JOB, {"draft_id": str(row.id), "startup_id": str(row.startup_id)}, row.startup_id
    )
    return row


def request_step_draft(
    db: Session,
    seq: EmailSequence,
    step_index: int,
    user_id: uuid.UUID,
    instructions: str | None,
) -> SalesDraft:
    if step_index < 0 or step_index >= len(seq.steps_json):
        raise _validation("step", "That step does not exist.")
    row = SalesDraft(
        startup_id=seq.startup_id,
        kind=DraftKind.sequence_step,
        status=DraftStatus.generating,
        requested_by=user_id,
        sequence_id=seq.id,
        step_index=step_index,
        output_json={"instructions": (instructions or "").strip()[:500]},
    )
    return _queue(db, row)


def request_follow_up(
    db: Session, startup_id: uuid.UUID, deal_id: uuid.UUID, user_id: uuid.UUID
) -> SalesDraft:
    deal = get_deal(db, startup_id, deal_id, lock=True)  # serializes concurrent requests
    inflight = db.scalar(
        select(SalesDraft.id).where(
            SalesDraft.startup_id == startup_id,
            SalesDraft.deal_id == deal.id,
            SalesDraft.kind == DraftKind.follow_up,
            SalesDraft.status == DraftStatus.generating,
        )
    )
    if inflight is not None:
        raise AppError(
            "DRAFT_IN_PROGRESS", "A follow-up is already being written for this deal.", 409
        )
    row = SalesDraft(
        startup_id=startup_id,
        kind=DraftKind.follow_up,
        status=DraftStatus.generating,
        requested_by=user_id,
        deal_id=deal.id,
    )
    return _queue(db, row)


def recipient_for_deal(
    db: Session, startup_id: uuid.UUID, deal_id: uuid.UUID
) -> tuple[str | None, str, str, str]:
    """(email, first, last, company) of a deal's contact, for the follow-up send."""
    deal = get_deal(db, startup_id, deal_id)
    contact = db.get(SalesContact, deal.contact_id) if deal.contact_id else None
    if contact is None:
        return None, "", "", ""
    account = db.get(SalesAccount, contact.account_id) if contact.account_id else None
    return (
        contact.email,
        contact.first_name,
        contact.last_name or "",
        account.name if account else "",
    )


def serialize(d: SalesDraft) -> dict[str, Any]:
    out = d.output_json or {}
    return {
        "id": str(d.id),
        "kind": d.kind.value,
        "status": d.status.value,
        "sequence_id": str(d.sequence_id) if d.sequence_id else None,
        "step_index": d.step_index,
        "deal_id": str(d.deal_id) if d.deal_id else None,
        "subject": out.get("subject"),
        "body": out.get("body"),
        "error": d.error,
        "sent_at": d.sent_at.isoformat() if d.sent_at else None,
        "created_at": d.created_at.isoformat(),
    }


def latest_follow_up(db: Session, startup_id: uuid.UUID, deal_id: uuid.UUID) -> SalesDraft | None:
    get_deal(db, startup_id, deal_id)  # 404 for another workspace's deal
    return db.scalar(
        select(SalesDraft)
        .where(
            SalesDraft.startup_id == startup_id,
            SalesDraft.deal_id == deal_id,
            SalesDraft.kind == DraftKind.follow_up,
        )
        .order_by(SalesDraft.created_at.desc(), SalesDraft.id)
        .limit(1)
    )
