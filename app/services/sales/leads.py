import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import LeadSource, LeadStatus
from app.db.models.sales import Deal, SalesAccount, SalesContact, SalesLead
from app.platform.activity import write_activity
from app.schemas.sales import DealCreate, LeadCreate, LeadUpdate
from app.services.sales import deals as deals_svc
from app.services.sales.errors import _validation
from app.services.sales.people import _like, check_owner

_CONVERTED_MSG = "This lead was already converted."
_IDENTITY_MSG = "Add a name or a company."


def get_lead(db: Session, startup_id: uuid.UUID, lead_id: uuid.UUID) -> SalesLead:
    row = db.scalar(
        select(SalesLead).where(SalesLead.id == lead_id, SalesLead.startup_id == startup_id)
    )
    if row is None:
        raise NotFound()
    return row


def list_leads(
    db: Session,
    startup_id: uuid.UUID,
    *,
    status: LeadStatus | None,
    owner_id: uuid.UUID | None,
    source: LeadSource | None,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[list[SalesLead], int]:
    cond = [SalesLead.startup_id == startup_id]
    if status is not None:
        cond.append(SalesLead.status == status)
    if owner_id is not None:
        cond.append(SalesLead.owner_id == owner_id)
    if source is not None:
        cond.append(SalesLead.source == source)
    if q and q.strip():
        pat = _like(q)
        cond.append(
            or_(
                SalesLead.name.ilike(pat, escape="\\"),
                SalesLead.company.ilike(pat, escape="\\"),
                SalesLead.email.ilike(pat, escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(SalesLead).where(*cond)) or 0
    rows = list(
        db.scalars(
            select(SalesLead)
            .where(*cond)
            .order_by(SalesLead.created_at.desc(), SalesLead.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def _label(row: SalesLead) -> str:
    return row.name or row.company or "lead"


def create_lead(
    db: Session, startup_id: uuid.UUID, data: LeadCreate, *, actor_id: uuid.UUID | None
) -> SalesLead:
    if not (data.name or data.company):
        raise _validation("name", _IDENTITY_MSG)
    check_owner(db, startup_id, data.owner_id)
    row = SalesLead(
        startup_id=startup_id,
        name=data.name,
        company=data.company,
        email=data.email,
        source=data.source,
        status=data.status,
        owner_id=data.owner_id,
    )
    db.add(row)
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.lead.created",
        summary=f"Added lead {_label(row)}",
        actor_user_id=actor_id,
        entity_type="sales_lead",
        entity_id=row.id,
    )
    return row


def update_lead(
    db: Session,
    startup_id: uuid.UUID,
    lead_id: uuid.UUID,
    data: LeadUpdate,
    *,
    actor_id: uuid.UUID | None,
) -> SalesLead:
    row = get_lead(db, startup_id, lead_id)
    if row.status == LeadStatus.converted:
        raise _validation("status", _CONVERTED_MSG)
    fields = data.model_fields_set
    if "status" in fields and data.status is None:
        raise _validation("status", "Status is required.")
    if "source" in fields and data.source is None:
        raise _validation("source", "Source is required.")
    if data.status == LeadStatus.converted:
        raise _validation("status", "Use convert to mark a lead as converted.")
    if "owner_id" in fields:
        check_owner(db, startup_id, data.owner_id)
    name = data.name if "name" in fields else row.name
    company = data.company if "company" in fields else row.company
    if not (name or company):
        raise _validation("name", _IDENTITY_MSG)
    for f in fields:
        setattr(row, f, getattr(data, f))
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.lead.updated",
        summary=f"Updated lead {_label(row)}",
        actor_user_id=actor_id,
        entity_type="sales_lead",
        entity_id=row.id,
        meta={"fields": sorted(fields)},
    )
    return row


def delete_lead(
    db: Session, startup_id: uuid.UUID, lead_id: uuid.UUID, *, actor_id: uuid.UUID | None
) -> None:
    row = get_lead(db, startup_id, lead_id)
    if row.status == LeadStatus.converted:
        raise _validation("status", _CONVERTED_MSG)
    label, rid = _label(row), row.id
    db.delete(row)
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.lead.deleted",
        summary=f"Deleted lead {label}",
        actor_user_id=actor_id,
        entity_type="sales_lead",
        entity_id=rid,
    )


@dataclass
class ConvertResult:
    deal: Deal
    account: SalesAccount | None
    contact: SalesContact


def _account_by_name(db: Session, startup_id: uuid.UUID, name: str) -> SalesAccount | None:
    return db.scalar(
        select(SalesAccount).where(
            SalesAccount.startup_id == startup_id,
            func.lower(SalesAccount.name) == name.lower(),
        )
    )


def _find_or_create_account(
    db: Session, startup_id: uuid.UUID, company: str, owner_id: uuid.UUID | None
) -> SalesAccount:
    found = _account_by_name(db, startup_id, company)
    if found is not None:
        return found
    row = SalesAccount(startup_id=startup_id, name=company, owner_id=owner_id)
    try:
        # add() INSIDE the SAVEPOINT: a racing duplicate-name insert must not poison the session
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        racing = _account_by_name(db, startup_id, company)
        if racing is None:
            raise
        return racing
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.account.created",
        summary=f"Added account {row.name}",
        actor_user_id=owner_id,
        entity_type="sales_account",
        entity_id=row.id,
    )
    return row


def convert_lead(
    db: Session, *, startup_id: uuid.UUID, lead_id: uuid.UUID, actor_id: uuid.UUID
) -> ConvertResult:
    lead = db.scalar(
        select(SalesLead)
        .where(SalesLead.id == lead_id, SalesLead.startup_id == startup_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if lead is None:
        raise NotFound()
    if lead.status == LeadStatus.converted:
        raise _validation("status", _CONVERTED_MSG)

    company = lead.company
    account = _find_or_create_account(db, startup_id, company, lead.owner_id) if company else None

    first, _, rest = (lead.name or company or "").strip().partition(" ")
    contact = SalesContact(
        startup_id=startup_id,
        account_id=account.id if account else None,
        first_name=first,
        last_name=rest.strip() or None,
        email=lead.email,
    )
    db.add(contact)
    db.flush()

    deal = deals_svc.create_deal(
        db,
        startup_id=startup_id,
        actor_id=actor_id,
        data=DealCreate(
            name=company or lead.name or "Deal",
            account_id=account.id if account else None,
            contact_id=contact.id,
            owner_id=lead.owner_id or actor_id,
        ),
    )
    lead.status = LeadStatus.converted
    lead.converted_deal_id = deal.id
    lead.converted_contact_id = contact.id
    lead.converted_account_id = account.id if account else None
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.lead.converted",
        summary=f"Converted lead {_label(lead)} to a deal",
        actor_user_id=actor_id,
        entity_type="sales_lead",
        entity_id=lead.id,
        meta={"deal_id": str(deal.id)},
    )
    return ConvertResult(deal=deal, account=account, contact=contact)
