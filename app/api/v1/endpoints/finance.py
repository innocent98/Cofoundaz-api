import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import MembershipRole, TransactionDirection
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.finance import CashFlowResponse, TransactionCreate, TransactionUpdate
from app.schemas.finance_runway import AssumptionsUpdate, RunwayResponse
from app.schemas.invoice import InvoiceCreate, InvoiceUpdate
from app.services.finance import cashflow as cashflow_svc
from app.services.finance import invoices as invoice_svc
from app.services.finance import runway as runway_svc
from app.services.finance import service as finance_svc

router = APIRouter()

_finance = require_role(
    MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant
)


@router.post("/transactions", response_model=dict[str, Any])
def create_transaction(
    payload: TransactionCreate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = finance_svc.create_transaction(db, startup_id=membership.startup_id, data=payload)
    runway_svc.evaluate_runway_alert(db, startup_id=membership.startup_id)
    runway_svc.upsert_runway_signal(db, startup_id=membership.startup_id)
    db.commit()
    return success_response(finance_svc.serialize_transaction(row).model_dump())


@router.get("/transactions", response_model=dict[str, Any])
def list_transactions(
    category: str | None = None,
    uncategorized: bool = False,
    direction: TransactionDirection | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = finance_svc.list_transactions(
        db,
        startup_id=membership.startup_id,
        category=category,
        uncategorized=uncategorized,
        direction=direction,
        date_from=date_from,
        date_to=date_to,
    )
    return success_response(
        {"transactions": [finance_svc.serialize_transaction(r).model_dump() for r in rows]}
    )


@router.patch("/transactions/{transaction_id}", response_model=dict[str, Any])
def update_transaction(
    transaction_id: uuid.UUID,
    payload: TransactionUpdate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = finance_svc.update_transaction(
        db, startup_id=membership.startup_id, transaction_id=transaction_id, data=payload
    )
    runway_svc.evaluate_runway_alert(db, startup_id=membership.startup_id)
    runway_svc.upsert_runway_signal(db, startup_id=membership.startup_id)
    db.commit()
    return success_response(finance_svc.serialize_transaction(row).model_dump())


@router.delete("/transactions/{transaction_id}", response_model=dict[str, Any])
def delete_transaction(
    transaction_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    finance_svc.delete_transaction(
        db, startup_id=membership.startup_id, transaction_id=transaction_id
    )
    runway_svc.evaluate_runway_alert(db, startup_id=membership.startup_id)
    runway_svc.upsert_runway_signal(db, startup_id=membership.startup_id)
    db.commit()
    return success_response({"deleted": True})


@router.get("/cash-flow", response_model=dict[str, Any])
def cash_flow(
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    data = cashflow_svc.cash_flow_summary(db, startup_id=membership.startup_id)
    return success_response(CashFlowResponse(**data).model_dump(mode="json"))


@router.get("/runway", response_model=dict[str, Any])
def get_runway(
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    data = runway_svc.runway_payload(db, startup_id=membership.startup_id)
    return success_response(RunwayResponse(**data).model_dump(mode="json"))


@router.put("/runway/assumptions", response_model=dict[str, Any])
def put_runway_assumptions(
    payload: AssumptionsUpdate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    runway_svc.upsert_assumptions(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    data = runway_svc.runway_payload(db, startup_id=membership.startup_id)
    return success_response(RunwayResponse(**data).model_dump(mode="json"))


@router.post("/invoices", response_model=dict[str, Any])
def create_invoice(
    payload: InvoiceCreate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    inv = invoice_svc.create_invoice(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    today = datetime.now(UTC).date()
    return success_response(invoice_svc.serialize_invoice(inv, today=today).model_dump(mode="json"))


@router.get("/invoices", response_model=dict[str, Any])
def list_invoices(
    status: Literal["draft", "sent", "paid", "overdue"] | None = None,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    rows = invoice_svc.list_invoices(
        db, startup_id=membership.startup_id, status=status, today=today
    )
    return success_response(
        {
            "invoices": [
                invoice_svc.serialize_invoice(r, today=today).model_dump(mode="json") for r in rows
            ]
        }
    )


@router.get("/invoices/{invoice_id}", response_model=dict[str, Any])
def get_invoice(
    invoice_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    inv = invoice_svc.get_invoice(db, startup_id=membership.startup_id, invoice_id=invoice_id)
    today = datetime.now(UTC).date()
    return success_response(invoice_svc.serialize_invoice(inv, today=today).model_dump(mode="json"))


@router.patch("/invoices/{invoice_id}", response_model=dict[str, Any])
def update_invoice(
    invoice_id: uuid.UUID,
    payload: InvoiceUpdate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    inv = invoice_svc.update_invoice(
        db, startup_id=membership.startup_id, invoice_id=invoice_id, data=payload
    )
    db.commit()
    today = datetime.now(UTC).date()
    return success_response(invoice_svc.serialize_invoice(inv, today=today).model_dump(mode="json"))


@router.delete("/invoices/{invoice_id}", response_model=dict[str, Any])
def delete_invoice(
    invoice_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    invoice_svc.delete_invoice(db, startup_id=membership.startup_id, invoice_id=invoice_id)
    db.commit()
    return success_response({"deleted": True})


@router.post("/invoices/{invoice_id}/send", response_model=dict[str, Any])
def send_invoice(
    invoice_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    inv = invoice_svc.send_invoice(
        db, startup_id=membership.startup_id, invoice_id=invoice_id, today=today
    )
    db.commit()
    return success_response(invoice_svc.serialize_invoice(inv, today=today).model_dump(mode="json"))


@router.post("/invoices/{invoice_id}/mark-paid", response_model=dict[str, Any])
def mark_invoice_paid(
    invoice_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    inv = invoice_svc.mark_paid(db, startup_id=membership.startup_id, invoice_id=invoice_id)
    db.commit()
    today = datetime.now(UTC).date()
    return success_response(invoice_svc.serialize_invoice(inv, today=today).model_dump(mode="json"))


@router.post("/invoices/{invoice_id}/mark-unpaid", response_model=dict[str, Any])
def mark_invoice_unpaid(
    invoice_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    inv = invoice_svc.mark_unpaid(db, startup_id=membership.startup_id, invoice_id=invoice_id)
    db.commit()
    today = datetime.now(UTC).date()
    return success_response(invoice_svc.serialize_invoice(inv, today=today).model_dump(mode="json"))
