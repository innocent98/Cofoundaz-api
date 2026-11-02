import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import ClosedReason, StageKind
from app.db.models.sales import (
    Deal,
    DealStageChange,
    PipelineStage,
    SalesAccount,
    SalesContact,
)
from app.platform.activity import write_activity
from app.platform.events import event_bus
from app.schemas.sales import (
    DealCreate,
    DealItem,
    DealUpdate,
    StageChangeOut,
)
from app.services.sales.activities import touch_deal
from app.services.sales.errors import _validation
from app.services.sales.people import _check_account, _like, check_owner
from app.services.sales.stages import ensure_stages, get_settings


# ---- Lookups ----
def get_deal(db: Session, startup_id: uuid.UUID, deal_id: uuid.UUID, *, lock: bool = False) -> Deal:
    stmt = select(Deal).where(Deal.id == deal_id, Deal.startup_id == startup_id)
    if lock:
        # populate_existing: a cached copy must not hide a concurrent writer's committed change
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise NotFound()
    return row


def _live_stage(db: Session, startup_id: uuid.UUID, stage_id: uuid.UUID) -> PipelineStage:
    stage = db.scalar(
        select(PipelineStage).where(
            PipelineStage.id == stage_id,
            PipelineStage.startup_id == startup_id,
            PipelineStage.archived_at.is_(None),
        )
    )
    if stage is None:
        raise _validation("stage_id", "Stage not found.")
    return stage


def _check_contact_account(
    db: Session,
    startup_id: uuid.UUID,
    contact_id: uuid.UUID | None,
    account_id: uuid.UUID | None,
) -> None:
    if contact_id is None:
        return
    contact = db.scalar(
        select(SalesContact).where(
            SalesContact.id == contact_id, SalesContact.startup_id == startup_id
        )
    )
    if contact is None:
        raise _validation("contact_id", "Contact not found.")
    if account_id is not None and contact.account_id != account_id:
        raise _validation("contact_id", "That contact doesn't belong to this account.")


# ---- Presentation ----
def deal_items(db: Session, startup_id: uuid.UUID, deals: list[Deal]) -> list[DealItem]:
    """List/detail shape: adds account and contact names and the stall flags."""
    if not deals:
        return []
    stall_days = get_settings(db, startup_id).stall_days
    acc_ids = {d.account_id for d in deals if d.account_id}
    con_ids = {d.contact_id for d in deals if d.contact_id}
    accounts = (
        {
            a.id: a.name
            for a in db.scalars(
                select(SalesAccount).where(
                    SalesAccount.startup_id == startup_id, SalesAccount.id.in_(acc_ids)
                )
            )
        }
        if acc_ids
        else {}
    )
    contacts = (
        {
            c.id: " ".join(p for p in (c.first_name, c.last_name) if p)
            for c in db.scalars(
                select(SalesContact).where(
                    SalesContact.startup_id == startup_id, SalesContact.id.in_(con_ids)
                )
            )
        }
        if con_ids
        else {}
    )
    kinds = {
        s.id: s.kind
        for s in db.scalars(
            select(PipelineStage).where(
                PipelineStage.id.in_({d.stage_id for d in deals}),
                PipelineStage.startup_id == startup_id,
            )
        )
    }
    now = datetime.now(UTC)
    out: list[DealItem] = []
    for d in deals:
        days = max(0, (now - d.last_activity_at).days)
        out.append(
            DealItem.model_validate(d).model_copy(
                update={
                    "account_name": accounts.get(d.account_id) if d.account_id else None,
                    "contact_name": contacts.get(d.contact_id) if d.contact_id else None,
                    "days_inactive": days,
                    "is_stalled": kinds.get(d.stage_id) == StageKind.open and days >= stall_days,
                }
            )
        )
    return out


def stage_history(db: Session, startup_id: uuid.UUID, deal_id: uuid.UUID) -> list[StageChangeOut]:
    changes = list(
        db.scalars(
            select(DealStageChange)
            .where(DealStageChange.deal_id == deal_id)
            .order_by(DealStageChange.changed_at, DealStageChange.id)
        )
    )
    ids = {c.to_stage_id for c in changes} | {c.from_stage_id for c in changes if c.from_stage_id}
    names = (
        {
            s.id: s.name
            for s in db.scalars(
                select(PipelineStage).where(
                    PipelineStage.startup_id == startup_id, PipelineStage.id.in_(ids)
                )
            )
        }
        if ids
        else {}
    )
    return [
        StageChangeOut(
            id=c.id,
            from_stage_id=c.from_stage_id,
            from_stage_name=names.get(c.from_stage_id) if c.from_stage_id else None,
            to_stage_id=c.to_stage_id,
            to_stage_name=names.get(c.to_stage_id),
            changed_by=c.changed_by,
            changed_at=c.changed_at,
        )
        for c in changes
    ]


def list_deals(
    db: Session,
    startup_id: uuid.UUID,
    *,
    stage_id: uuid.UUID | None,
    account_id: uuid.UUID | None,
    owner_id: uuid.UUID | None,
    q: str | None,
    open_only: bool,
    limit: int,
    offset: int,
) -> tuple[list[DealItem], int]:
    cond: list[Any] = [Deal.startup_id == startup_id]
    if stage_id is not None:
        cond.append(Deal.stage_id == stage_id)
    if account_id is not None:
        cond.append(Deal.account_id == account_id)
    if owner_id is not None:
        cond.append(Deal.owner_id == owner_id)
    if q and q.strip():
        cond.append(Deal.name.ilike(_like(q), escape="\\"))
    if open_only:
        cond.append(
            Deal.stage_id.in_(
                select(PipelineStage.id).where(
                    PipelineStage.startup_id == startup_id, PipelineStage.kind == StageKind.open
                )
            )
        )
    total = db.scalar(select(func.count()).select_from(Deal).where(*cond)) or 0
    rows = list(
        db.scalars(
            select(Deal)
            .where(*cond)
            .order_by(Deal.last_activity_at.desc(), Deal.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return deal_items(db, startup_id, rows), total


def account_deal_summary(
    db: Session, startup_id: uuid.UUID, account_id: uuid.UUID
) -> tuple[list[DealItem], int]:
    """Open deals of an account plus the sum of its deals currently in the won stage."""
    open_rows = list(
        db.scalars(
            select(Deal)
            .join(PipelineStage, PipelineStage.id == Deal.stage_id)
            .where(
                Deal.startup_id == startup_id,
                Deal.account_id == account_id,
                PipelineStage.kind == StageKind.open,
            )
            .order_by(Deal.last_activity_at.desc(), Deal.id)
        )
    )
    won = db.scalar(
        select(func.coalesce(func.sum(Deal.value_minor), 0))
        .join(PipelineStage, PipelineStage.id == Deal.stage_id)
        .where(
            Deal.startup_id == startup_id,
            Deal.account_id == account_id,
            PipelineStage.kind == StageKind.won,
        )
    )
    return deal_items(db, startup_id, open_rows), int(won or 0)


# ---- Events ----
def _href(deal_id: uuid.UUID) -> str:
    return f"/sales?deal={deal_id}"


def _publish_stage_event(
    db: Session,
    deal: Deal,
    *,
    from_stage_id: uuid.UUID | None,
    to_stage: PipelineStage,
    actor_id: uuid.UUID | None,
) -> None:
    payload = {
        "startup_id": str(deal.startup_id),
        "deal_id": str(deal.id),
        "name": deal.name,
        "from_stage_id": str(from_stage_id) if from_stage_id else None,
        "to_stage_id": str(to_stage.id),
        "actor_id": str(actor_id) if actor_id else None,
        "href": _href(deal.id),
    }
    event_bus.publish(db, "sales.deal.stage_changed", payload)
    if to_stage.kind == StageKind.won:
        event_bus.publish(db, "sales.deal.won", payload)
    elif to_stage.kind == StageKind.lost:
        event_bus.publish(db, "sales.deal.lost", payload)


# ---- Writes ----
def create_deal(
    db: Session, *, startup_id: uuid.UUID, actor_id: uuid.UUID | None, data: DealCreate
) -> Deal:
    live = ensure_stages(db, startup_id)
    if data.stage_id is None:
        stage = live[0]
    else:
        stage = _live_stage(db, startup_id, data.stage_id)
    if stage.kind == StageKind.lost:
        raise _validation("stage_id", "Use the stage endpoint to close a deal as lost.")
    _check_account(db, startup_id, data.account_id)
    _check_contact_account(db, startup_id, data.contact_id, data.account_id)
    check_owner(db, startup_id, data.owner_id)

    now = datetime.now(UTC)
    deal = Deal(
        startup_id=startup_id,
        name=data.name,
        account_id=data.account_id,
        contact_id=data.contact_id,
        value_minor=data.value_minor,
        currency="NGN",
        stage_id=stage.id,
        next_step=data.next_step,
        next_step_due=data.next_step_due,
        owner_id=data.owner_id,
        last_activity_at=now,
        closed_at=now if stage.kind == StageKind.won else None,
    )
    db.add(deal)
    db.flush()
    db.add(
        DealStageChange(
            deal_id=deal.id,
            from_stage_id=None,
            to_stage_id=stage.id,
            changed_by=actor_id,
            changed_at=now,
        )
    )
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.deal.created",
        summary=f"Added deal {deal.name}",
        actor_user_id=actor_id,
        entity_type="deal",
        entity_id=deal.id,
    )
    if stage.kind == StageKind.won:
        _publish_stage_event(db, deal, from_stage_id=None, to_stage=stage, actor_id=actor_id)
    return deal


def update_deal(
    db: Session,
    *,
    startup_id: uuid.UUID,
    deal_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    data: DealUpdate,
) -> Deal:
    deal = get_deal(db, startup_id, deal_id)
    fields = data.model_fields_set
    if "stage_id" in fields:
        raise _validation("stage_id", "Use the stage endpoint.")
    for required in ("name", "value_minor"):
        if required in fields and getattr(data, required) is None:
            raise _validation(required, f"{required.replace('_', ' ').capitalize()} is required.")
    if "owner_id" in fields:
        check_owner(db, startup_id, data.owner_id)
    account_id = data.account_id if "account_id" in fields else deal.account_id
    contact_id = data.contact_id if "contact_id" in fields else deal.contact_id
    if "account_id" in fields:
        _check_account(db, startup_id, account_id)
    if "account_id" in fields or "contact_id" in fields:
        _check_contact_account(db, startup_id, contact_id, account_id)
    for f in fields:
        setattr(deal, f, getattr(data, f))
    db.flush()
    if "next_step" in fields or "next_step_due" in fields:
        touch_deal(db, deal)
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.deal.updated",
        summary=f"Updated deal {deal.name}",
        actor_user_id=actor_id,
        entity_type="deal",
        entity_id=deal.id,
        meta={"fields": sorted(fields)},
    )
    return deal


def delete_deal(
    db: Session, *, startup_id: uuid.UUID, deal_id: uuid.UUID, actor_id: uuid.UUID | None
) -> None:
    deal = get_deal(db, startup_id, deal_id)
    name, rid = deal.name, deal.id
    db.delete(deal)
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.deal.deleted",
        summary=f"Deleted deal {name}",
        actor_user_id=actor_id,
        entity_type="deal",
        entity_id=rid,
    )


def move_stage(
    db: Session,
    *,
    startup_id: uuid.UUID,
    deal_id: uuid.UUID,
    stage_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    closed_reason: ClosedReason | None = None,
    closed_note: str | None = None,
) -> Deal:
    deal = get_deal(db, startup_id, deal_id, lock=True)
    stage = _live_stage(db, startup_id, stage_id)
    if deal.stage_id == stage.id:
        return deal
    if stage.kind == StageKind.lost and closed_reason is None:
        raise _validation("closed_reason", "Pick a reason for losing this deal.")

    from_stage_id = deal.stage_id
    now = datetime.now(UTC)
    deal.stage_id = stage.id
    if stage.kind == StageKind.open:
        deal.closed_at, deal.closed_reason, deal.closed_note = None, None, None
    elif stage.kind == StageKind.won:
        deal.closed_at, deal.closed_reason, deal.closed_note = now, None, None
    else:
        deal.closed_at, deal.closed_reason, deal.closed_note = now, closed_reason, closed_note
    db.add(
        DealStageChange(
            deal_id=deal.id,
            from_stage_id=from_stage_id,
            to_stage_id=stage.id,
            changed_by=actor_id,
            changed_at=now,
        )
    )
    db.flush()
    touch_deal(db, deal, at=now)

    action = {
        StageKind.won: "sales.deal.won",
        StageKind.lost: "sales.deal.lost",
        StageKind.open: "sales.deal.stage_changed",
    }[stage.kind]
    write_activity(
        db,
        startup_id=startup_id,
        action=action,
        summary=f"Moved deal {deal.name} to {stage.name}",
        actor_user_id=actor_id,
        entity_type="deal",
        entity_id=deal.id,
        meta={"from_stage_id": str(from_stage_id), "to_stage_id": str(stage.id)},
    )
    _publish_stage_event(db, deal, from_stage_id=from_stage_id, to_stage=stage, actor_id=actor_id)
    # Lead call 5: sequences that opted in stop when their contact's deal changes stage.
    from app.services.sales.sequences import stop_for_contact_stage_change

    stop_for_contact_stage_change(db, startup_id, deal.contact_id)
    return deal
