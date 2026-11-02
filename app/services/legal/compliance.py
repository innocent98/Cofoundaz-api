import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import ComplianceStatus
from app.db.models.legal import ComplianceItem
from app.schemas.legal import ComplianceCreate, ComplianceUpdate
from app.services.legal.recurrence import next_due_on

# Not arbitrary: 14 is also the first reminder threshold, so the badge the user sees and the
# first notification they receive agree (spec D12). The FE prototype likewise badges "in 9 days"
# as Due soon, which rules out a 7-day window.
DUE_SOON_DAYS = 14

DONE = "done"
OVERDUE = "overdue"
DUE_SOON = "due_soon"
UPCOMING = "upcoming"


def derived_compliance_status(status: ComplianceStatus, due_on: date, today: date) -> str:
    """The four badges the FE shows, from the two stored values plus the date (spec D12).

    Order matters: a done filing is never overdue, however old. A filing due *today* has not
    passed, so it is `due_soon`, not `overdue`.
    """
    if status == ComplianceStatus.done:
        return DONE
    if due_on < today:
        return OVERDUE
    if (due_on - today).days <= DUE_SOON_DAYS:
        return DUE_SOON
    return UPCOMING


def serialize_item(row: ComplianceItem, *, today: date) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "authority": row.authority,
        # ISO, never the mock's "Was due Jul 10" / "in 9 days".
        "due_on": row.due_on.isoformat(),
        "status": row.status.value,
        "derived_status": derived_compliance_status(row.status, row.due_on, today),
        "done_at": row.done_at.isoformat() if row.done_at else None,
        "proof_url": row.proof_url,
        "recurrence": row.recurrence.value,
        "previous_item_id": str(row.previous_item_id) if row.previous_item_id else None,
        "created_by": str(row.created_by) if row.created_by else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def create_item(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID | None, data: ComplianceCreate
) -> ComplianceItem:
    fields = data.model_dump()
    row = ComplianceItem(
        startup_id=startup_id,
        created_by=created_by,
        # Derived here, never accepted from the caller (spec D24).
        anchor_day=fields["due_on"].day,
        **fields,
    )
    db.add(row)
    db.flush()
    return row


def get_item(db: Session, *, startup_id: uuid.UUID, item_id: uuid.UUID) -> ComplianceItem:
    row = db.query(ComplianceItem).filter_by(id=item_id, startup_id=startup_id).one_or_none()
    if row is None:
        # Uniform 404, never 403: a workspace must not be able to detect another's row ids.
        raise NotFound()
    return row


def list_items(
    db: Session,
    *,
    startup_id: uuid.UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    status: ComplianceStatus | None = None,
) -> list[ComplianceItem]:
    """The compliance calendar, soonest first.

    `status` filters the **stored** value (open/done) only. Filtering on a derived value would
    need the same date arithmetic in SQL and in Python, and the two would drift.
    """
    q = db.query(ComplianceItem).filter_by(startup_id=startup_id)
    if date_from is not None:
        q = q.filter(ComplianceItem.due_on >= date_from)
    if date_to is not None:
        q = q.filter(ComplianceItem.due_on <= date_to)
    if status is not None:
        q = q.filter(ComplianceItem.status == status)
    return q.order_by(ComplianceItem.due_on.asc(), ComplianceItem.created_at.asc()).all()


def _spawn_next_occurrence(db: Session, row: ComplianceItem) -> None:
    """Create the one next occurrence of a recurring series. Idempotent.

    `previous_item_id` is UNIQUE, so a second attempt for the same predecessor raises
    `IntegrityError`. The insert therefore sits in a SAVEPOINT and the collision is swallowed as
    a no-op, leaving the caller's transaction usable (spec D16).

    Nothing is pre-generated: each occurrence spawns the next only when it is completed, so every
    row gets its own id and therefore its own fresh reminder claims.

    `created_by` is copied from the predecessor — the series' originator — rather than attributed
    to whoever happened to tick this occurrence off.
    """
    nxt = next_due_on(row.due_on, row.recurrence, row.anchor_day)
    if nxt is None:
        return
    try:
        with db.begin_nested():
            db.add(
                ComplianceItem(
                    startup_id=row.startup_id,
                    title=row.title,
                    authority=row.authority,
                    due_on=nxt,
                    recurrence=row.recurrence,
                    anchor_day=row.anchor_day,
                    previous_item_id=row.id,
                    created_by=row.created_by,
                )
            )
            db.flush()
    except IntegrityError:
        # Already spawned from this predecessor. Marking done twice, or done -> reopen -> done,
        # therefore yields exactly one successor.
        return


def update_item(
    db: Session, *, startup_id: uuid.UUID, item_id: uuid.UUID, data: ComplianceUpdate
) -> ComplianceItem:
    row = get_item(db, startup_id=startup_id, item_id=item_id)
    changes = data.model_dump(exclude_unset=True)
    was = row.status

    for field, value in changes.items():
        setattr(row, field, value)

    # Moving the series date moves the series (spec D24).
    if "due_on" in changes:
        row.anchor_day = row.due_on.day

    if "status" in changes and row.status != was:
        row.done_at = datetime.now(UTC) if row.status == ComplianceStatus.done else None
        db.flush()
        # Only on an open -> done transition. Reopening keeps whatever was already spawned.
        if row.status == ComplianceStatus.done:
            _spawn_next_occurrence(db, row)

    db.flush()
    return row


def delete_item(db: Session, *, startup_id: uuid.UUID, item_id: uuid.UUID) -> None:
    row = get_item(db, startup_id=startup_id, item_id=item_id)
    db.delete(row)
    db.flush()
