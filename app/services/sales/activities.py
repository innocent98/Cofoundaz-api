import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound
from app.db.models.sales import Deal, SalesAccount, SalesActivity, SalesContact
from app.platform.activity import write_activity
from app.schemas.sales import ActivityCreate, ActivityUpdate
from app.services.sales.errors import _validation


def touch_deal(db: Session, deal: Deal, *, at: datetime | None = None) -> None:
    """Record activity on a deal: bump `last_activity_at` and end any stall episode.

    Clearing `stalled_notified_at` is what lets the stall scan notify again after the next quiet
    period (the new `last_activity_at` also yields a new idempotency period key).
    """
    at = at or datetime.now(UTC)
    # Never move backwards: logging a call that happened last week must not re-open a stall.
    if deal.last_activity_at is None or at > deal.last_activity_at:
        deal.last_activity_at = at
    deal.stalled_notified_at = None
    db.flush()


def _owned(
    db: Session,
    model: type[SalesAccount] | type[SalesContact],
    startup_id: uuid.UUID,
    row_id: uuid.UUID,
    field: str,
) -> None:
    found = db.scalar(select(model.id).where(model.id == row_id, model.startup_id == startup_id))
    if found is None:
        raise _validation(field, "Not found.")


def get_activity(db: Session, startup_id: uuid.UUID, activity_id: uuid.UUID) -> SalesActivity:
    row = db.scalar(
        select(SalesActivity).where(
            SalesActivity.id == activity_id, SalesActivity.startup_id == startup_id
        )
    )
    if row is None:
        raise NotFound()
    return row


def list_activities(
    db: Session,
    startup_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    contact_id: uuid.UUID | None,
    deal_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[SalesActivity], int]:
    if account_id is None and contact_id is None and deal_id is None:
        raise _validation("account_id", "Filter by an account, contact or deal.")
    cond = [SalesActivity.startup_id == startup_id]
    if account_id is not None:
        cond.append(SalesActivity.account_id == account_id)
    if contact_id is not None:
        cond.append(SalesActivity.contact_id == contact_id)
    if deal_id is not None:
        cond.append(SalesActivity.deal_id == deal_id)
    total = db.scalar(select(func.count()).select_from(SalesActivity).where(*cond)) or 0
    rows = list(
        db.scalars(
            select(SalesActivity)
            .where(*cond)
            .order_by(SalesActivity.occurred_at.desc(), SalesActivity.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def create_activity(
    db: Session, startup_id: uuid.UUID, data: ActivityCreate, *, author_id: uuid.UUID
) -> SalesActivity:
    if data.account_id is None and data.contact_id is None and data.deal_id is None:
        raise _validation("account_id", "Link the activity to an account, contact or deal.")
    if data.account_id is not None:
        _owned(db, SalesAccount, startup_id, data.account_id, "account_id")
    if data.contact_id is not None:
        _owned(db, SalesContact, startup_id, data.contact_id, "contact_id")
    deal: Deal | None = None
    if data.deal_id is not None:
        deal = db.scalar(select(Deal).where(Deal.id == data.deal_id, Deal.startup_id == startup_id))
        if deal is None:
            raise _validation("deal_id", "Not found.")
    now = datetime.now(UTC)
    occurred = data.occurred_at or now
    row = SalesActivity(
        startup_id=startup_id,
        kind=data.kind,
        body=data.body,
        occurred_at=occurred,
        author_id=author_id,
        account_id=data.account_id,
        contact_id=data.contact_id,
        deal_id=data.deal_id,
    )
    db.add(row)
    db.flush()
    if deal is not None:
        touch_deal(db, deal, at=occurred if occurred <= now else now)
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.activity.created",
        summary=f"Logged {'an' if row.kind.value == 'email' else 'a'} {row.kind.value}",
        actor_user_id=author_id,
        entity_type="sales_activity",
        entity_id=row.id,
    )
    return row


def _author_only(row: SalesActivity, actor_id: uuid.UUID) -> None:
    if row.author_id != actor_id:
        raise Forbidden()


def update_activity(
    db: Session,
    startup_id: uuid.UUID,
    activity_id: uuid.UUID,
    data: ActivityUpdate,
    *,
    actor_id: uuid.UUID,
) -> SalesActivity:
    row = get_activity(db, startup_id, activity_id)
    _author_only(row, actor_id)
    fields = data.model_fields_set
    for f in ("kind", "body", "occurred_at"):
        if f in fields and getattr(data, f) is None:
            raise _validation(f, "This field can't be empty.")
    for f in fields:
        setattr(row, f, getattr(data, f))
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.activity.updated",
        summary=f"Edited a {row.kind.value}",
        actor_user_id=actor_id,
        entity_type="sales_activity",
        entity_id=row.id,
        meta={"fields": sorted(fields)},
    )
    return row


def delete_activity(
    db: Session, startup_id: uuid.UUID, activity_id: uuid.UUID, *, actor_id: uuid.UUID
) -> None:
    row = get_activity(db, startup_id, activity_id)
    _author_only(row, actor_id)
    kind, rid = row.kind.value, row.id
    db.delete(row)
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.activity.deleted",
        summary=f"Deleted a {kind}",
        actor_user_id=actor_id,
        entity_type="sales_activity",
        entity_id=rid,
    )
