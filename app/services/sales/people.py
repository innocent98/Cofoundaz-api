import uuid
from typing import Any

from sqlalchemy import func, inspect, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import SALES_ROLES, MembershipStatus
from app.db.models.membership import Membership
from app.db.models.sales import Deal, SalesAccount, SalesContact, SalesLead
from app.platform.activity import write_activity
from app.schemas.sales import AccountCreate, AccountUpdate, ContactCreate, ContactUpdate
from app.services.sales.errors import _validation


def check_owner(db: Session, startup_id: uuid.UUID, owner_id: uuid.UUID | None) -> None:
    """Owners must be current active founders/team members of this workspace."""
    if owner_id is None:
        return
    ok = db.scalar(
        select(Membership.id).where(
            Membership.startup_id == startup_id,
            Membership.user_id == owner_id,
            Membership.status == MembershipStatus.active,
            Membership.role.in_(SALES_ROLES),
        )
    )
    if ok is None:
        raise _validation("owner_id", "Owner must be a member of this workspace.")


def _like(q: str) -> str:
    esc = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


# ---- Accounts ----
def get_account(db: Session, startup_id: uuid.UUID, account_id: uuid.UUID) -> SalesAccount:
    row = db.scalar(
        select(SalesAccount).where(
            SalesAccount.id == account_id, SalesAccount.startup_id == startup_id
        )
    )
    if row is None:
        raise NotFound()
    return row


def _check_account(db: Session, startup_id: uuid.UUID, account_id: uuid.UUID | None) -> None:
    if account_id is None:
        return
    found = db.scalar(
        select(SalesAccount.id).where(
            SalesAccount.id == account_id, SalesAccount.startup_id == startup_id
        )
    )
    if found is None:
        raise _validation("account_id", "Account not found.")


def _flush_account(db: Session, row: SalesAccount, *, add: bool) -> None:
    # add() must be INSIDE the SAVEPOINT so a dup-name rollback discards the pending row
    try:
        with db.begin_nested():
            if add:
                db.add(row)
            db.flush()
    except IntegrityError as exc:
        if not add and inspect(row).persistent:
            # drop the rejected pending edit, else the next autoflush re-sends it outside the SAVEPOINT
            db.refresh(row)
        raise _validation("name", "An account with this name already exists.") from exc


def list_accounts(
    db: Session,
    startup_id: uuid.UUID,
    *,
    q: str | None,
    owner_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[SalesAccount], int]:
    cond = [SalesAccount.startup_id == startup_id]
    if q and q.strip():
        cond.append(SalesAccount.name.ilike(_like(q), escape="\\"))
    if owner_id is not None:
        cond.append(SalesAccount.owner_id == owner_id)
    total = db.scalar(select(func.count()).select_from(SalesAccount).where(*cond)) or 0
    rows = list(
        db.scalars(
            select(SalesAccount)
            .where(*cond)
            .order_by(func.lower(SalesAccount.name), SalesAccount.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def create_account(
    db: Session, startup_id: uuid.UUID, data: AccountCreate, *, actor_id: uuid.UUID | None
) -> SalesAccount:
    check_owner(db, startup_id, data.owner_id)
    row = SalesAccount(
        startup_id=startup_id,
        name=data.name,
        website=data.website,
        industry=data.industry,
        description=data.description,
        owner_id=data.owner_id,
    )
    _flush_account(db, row, add=True)
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.account.created",
        summary=f"Added account {row.name}",
        actor_user_id=actor_id,
        entity_type="sales_account",
        entity_id=row.id,
    )
    return row


def update_account(
    db: Session,
    startup_id: uuid.UUID,
    account_id: uuid.UUID,
    data: AccountUpdate,
    *,
    actor_id: uuid.UUID | None,
) -> SalesAccount:
    row = get_account(db, startup_id, account_id)
    fields = data.model_fields_set
    if "name" in fields and data.name is None:
        raise _validation("name", "Name is required.")
    if "owner_id" in fields:
        check_owner(db, startup_id, data.owner_id)
    if "name" in fields and data.name is not None:
        clash = db.scalar(
            select(SalesAccount.id).where(
                SalesAccount.startup_id == startup_id,
                func.lower(SalesAccount.name) == data.name.lower(),
                SalesAccount.id != row.id,
            )
        )
        if clash is not None:
            raise _validation("name", "An account with this name already exists.")
    for f in fields:
        setattr(row, f, getattr(data, f))
    _flush_account(db, row, add=False)
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.account.updated",
        summary=f"Updated account {row.name}",
        actor_user_id=actor_id,
        entity_type="sales_account",
        entity_id=row.id,
        meta={"fields": sorted(fields)},
    )
    return row


def delete_account(
    db: Session, startup_id: uuid.UUID, account_id: uuid.UUID, *, actor_id: uuid.UUID | None
) -> None:
    row = get_account(db, startup_id, account_id)
    has_deals = db.scalar(select(func.count()).select_from(Deal).where(Deal.account_id == row.id))
    if has_deals:
        raise _validation("account_id", "Remove or move its deals first.")
    # contacts and leads are detached, not deleted
    db.execute(
        update(SalesContact)
        .where(SalesContact.account_id == row.id)
        .values(account_id=None)
        .execution_options(synchronize_session="fetch")
    )
    db.execute(
        update(SalesLead)
        .where(SalesLead.converted_account_id == row.id)
        .values(converted_account_id=None)
        .execution_options(synchronize_session="fetch")
    )
    name, rid = row.name, row.id
    db.delete(row)
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.account.deleted",
        summary=f"Deleted account {name}",
        actor_user_id=actor_id,
        entity_type="sales_account",
        entity_id=rid,
    )


def account_detail(db: Session, startup_id: uuid.UUID, account_id: uuid.UUID) -> dict[str, Any]:
    account = get_account(db, startup_id, account_id)
    contacts = list(
        db.scalars(
            select(SalesContact)
            .where(SalesContact.startup_id == startup_id, SalesContact.account_id == account.id)
            .order_by(SalesContact.first_name, SalesContact.id)
        )
    )
    # local import: deals.py imports this module for check_owner/_check_account
    from app.services.sales.deals import account_deal_summary

    open_deals, lifetime_won = account_deal_summary(db, startup_id, account.id)
    return {
        "account": account,
        "contacts": contacts,
        "open_deals": [d.model_dump(mode="json") for d in open_deals],
        "lifetime_won_minor": lifetime_won,
    }


# ---- Contacts ----
def get_contact(db: Session, startup_id: uuid.UUID, contact_id: uuid.UUID) -> SalesContact:
    row = db.scalar(
        select(SalesContact).where(
            SalesContact.id == contact_id, SalesContact.startup_id == startup_id
        )
    )
    if row is None:
        raise NotFound()
    return row


def list_contacts(
    db: Session,
    startup_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[list[SalesContact], int]:
    cond = [SalesContact.startup_id == startup_id]
    if account_id is not None:
        cond.append(SalesContact.account_id == account_id)
    if q and q.strip():
        pat = _like(q)
        full = func.concat_ws(" ", SalesContact.first_name, SalesContact.last_name)
        cond.append(or_(full.ilike(pat, escape="\\"), SalesContact.email.ilike(pat, escape="\\")))
    total = db.scalar(select(func.count()).select_from(SalesContact).where(*cond)) or 0
    rows = list(
        db.scalars(
            select(SalesContact)
            .where(*cond)
            .order_by(func.lower(SalesContact.first_name), SalesContact.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def create_contact(
    db: Session, startup_id: uuid.UUID, data: ContactCreate, *, actor_id: uuid.UUID | None
) -> SalesContact:
    _check_account(db, startup_id, data.account_id)
    row = SalesContact(
        startup_id=startup_id,
        account_id=data.account_id,
        first_name=data.first_name,
        last_name=data.last_name,
        email=data.email,
        phone=data.phone,
        title=data.title,
    )
    db.add(row)
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.contact.created",
        summary=f"Added contact {row.first_name}",
        actor_user_id=actor_id,
        entity_type="sales_contact",
        entity_id=row.id,
    )
    return row


def update_contact(
    db: Session,
    startup_id: uuid.UUID,
    contact_id: uuid.UUID,
    data: ContactUpdate,
    *,
    actor_id: uuid.UUID | None,
) -> SalesContact:
    row = get_contact(db, startup_id, contact_id)
    fields = data.model_fields_set
    if "first_name" in fields and data.first_name is None:
        raise _validation("first_name", "First name is required.")
    if "account_id" in fields:
        _check_account(db, startup_id, data.account_id)
    for f in fields:
        setattr(row, f, getattr(data, f))
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.contact.updated",
        summary=f"Updated contact {row.first_name}",
        actor_user_id=actor_id,
        entity_type="sales_contact",
        entity_id=row.id,
        meta={"fields": sorted(fields)},
    )
    return row


def delete_contact(
    db: Session, startup_id: uuid.UUID, contact_id: uuid.UUID, *, actor_id: uuid.UUID | None
) -> None:
    row = get_contact(db, startup_id, contact_id)
    db.execute(
        update(Deal)
        .where(Deal.contact_id == row.id)
        .values(contact_id=None)
        .execution_options(synchronize_session="fetch")
    )
    name, rid = row.first_name, row.id
    db.delete(row)
    db.flush()
    write_activity(
        db,
        startup_id=startup_id,
        action="sales.contact.deleted",
        summary=f"Deleted contact {name}",
        actor_user_id=actor_id,
        entity_type="sales_contact",
        entity_id=rid,
    )
