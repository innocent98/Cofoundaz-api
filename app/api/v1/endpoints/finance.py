import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.config import settings
from app.core.envelope import Meta, success_response
from app.core.errors import AppError, NotFound
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.db.models.enums import FINANCE_ROLES, InvoiceStatus, TransactionDirection
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.platform.signed_urls import InvalidSignedToken, verify_key
from app.platform.storage import LocalStorage, get_storage
from app.schemas.budget import BudgetCreate, BudgetUpdate, SeedRequest
from app.schemas.expense import CategorySummary, ExpenseCreate, ExpenseUpdate
from app.schemas.finance import CashFlowResponse, TransactionCreate, TransactionUpdate
from app.schemas.finance_runway import AssumptionsUpdate, RunwayResponse
from app.schemas.invoice import InvoiceCreate, InvoiceUpdate
from app.services.finance import budgets as budget_svc
from app.services.finance import cashflow as cashflow_svc
from app.services.finance import categories as category_svc
from app.services.finance import expenses as expense_svc
from app.services.finance import invoices as invoice_svc
from app.services.finance import model_service as model_svc
from app.services.finance import reminders as reminder_svc
from app.services.finance import runway as runway_svc
from app.services.finance import service as finance_svc
from app.services.finance.invoice_pdf import render_invoice_pdf
from app.services.finance.payments import connections as conn_svc
from app.services.finance.payments import paylinks

router = APIRouter()

_finance = require_role(*FINANCE_ROLES)


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
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),  # noqa: B008
    cursor: str | None = None,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows, next_cursor, total = finance_svc.list_transactions(
        db,
        startup_id=membership.startup_id,
        category=category,
        uncategorized=uncategorized,
        direction=direction,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        cursor=cursor,
    )
    return success_response(
        {"transactions": [finance_svc.serialize_transaction(r).model_dump() for r in rows]},
        Meta(next_cursor=next_cursor, total_estimate=total),
    )


@router.get("/categories", response_model=dict[str, Any])
def list_categories(
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        {"categories": category_svc.list_categories(db, startup_id=membership.startup_id)}
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
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),  # noqa: B008
    cursor: str | None = None,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    rows, next_cursor, total = invoice_svc.list_invoices(
        db,
        startup_id=membership.startup_id,
        status=status,
        today=today,
        limit=limit,
        cursor=cursor,
    )
    return success_response(
        {
            "invoices": [
                invoice_svc.serialize_invoice(r, today=today).model_dump(mode="json") for r in rows
            ]
        },
        Meta(next_cursor=next_cursor, total_estimate=total),
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


@router.get("/invoices/{invoice_id}/pdf", response_class=Response)
def get_invoice_pdf(
    invoice_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    inv = invoice_svc.get_invoice(db, startup_id=membership.startup_id, invoice_id=invoice_id)
    startup = db.get(Startup, inv.startup_id)
    name = startup.name if startup and startup.name else "Cofoundaz"
    # The number is server-generated (INV-YYYY-NNN), but keep the header value strictly safe.
    filename = "".join(c for c in inv.number if c.isalnum() or c in "-_.")
    return Response(
        content=render_invoice_pdf(inv, name),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="invoice-{filename}.pdf"'},
    )


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
    # Best effort: a provider outage leaves pay_url null and never fails the send.
    paylinks.ensure_pay_link(db, invoice=inv)
    db.commit()
    return success_response(invoice_svc.serialize_invoice(inv, today=today).model_dump(mode="json"))


def _pay_link_validation(message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Please check the highlighted fields.",
        422,
        field_errors=[{"field": "status", "message": message}],
    )


@router.post("/invoices/{invoice_id}/pay-link", response_model=dict[str, Any])
def create_invoice_pay_link(
    invoice_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    inv = invoice_svc.get_invoice(db, startup_id=membership.startup_id, invoice_id=invoice_id)
    if inv.status != InvoiceStatus.sent:
        raise _pay_link_validation("Only a sent invoice can have a pay link.")
    if inv.currency != paylinks.LINK_CURRENCY:
        raise _pay_link_validation("Pay links are only available for NGN invoices.")
    if inv.pay_url is None:
        live = conn_svc.get_active(db, membership.startup_id)
        if live is None or live.status != "active":
            raise AppError(
                "NO_PAYMENT_CONNECTION",
                "Connect Paystack or Flutterwave before creating a pay link.",
                422,
            )
        if not paylinks.ensure_pay_link(db, invoice=inv):
            raise AppError(
                "PAYMENT_PROVIDER_UNAVAILABLE",
                "We could not create a pay link right now. Try again in a few minutes.",
                502,
            )
        db.commit()
    today = datetime.now(UTC).date()
    return success_response(invoice_svc.serialize_invoice(inv, today=today).model_dump(mode="json"))


@router.post("/invoices/{invoice_id}/remind", response_model=dict[str, Any])
def remind_invoice(
    invoice_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    inv = reminder_svc.send_manual_reminder(
        db, startup_id=membership.startup_id, invoice_id=invoice_id, now=datetime.now(UTC)
    )
    db.commit()
    today = datetime.now(UTC).date()
    return success_response(invoice_svc.serialize_invoice(inv, today=today).model_dump(mode="json"))


@router.post("/invoices/{invoice_id}/mark-paid", response_model=dict[str, Any])
def mark_invoice_paid(
    invoice_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    inv = invoice_svc.mark_paid(db, startup_id=membership.startup_id, invoice_id=invoice_id)
    runway_svc.evaluate_runway_alert(db, startup_id=membership.startup_id)
    runway_svc.upsert_runway_signal(db, startup_id=membership.startup_id)
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
    runway_svc.evaluate_runway_alert(db, startup_id=membership.startup_id)
    runway_svc.upsert_runway_signal(db, startup_id=membership.startup_id)
    db.commit()
    today = datetime.now(UTC).date()
    return success_response(invoice_svc.serialize_invoice(inv, today=today).model_dump(mode="json"))


_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


@router.post("/expenses", response_model=dict[str, Any])
def create_expense(
    payload: ExpenseCreate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    exp = expense_svc.create_expense(
        db, startup_id=membership.startup_id, created_by=user.id, data=payload
    )
    # The expense posted an outflow, so burn / runway changed: re-evaluate in the same transaction.
    runway_svc.evaluate_runway_alert(db, startup_id=membership.startup_id)
    runway_svc.upsert_runway_signal(db, startup_id=membership.startup_id)
    db.commit()
    return success_response(expense_svc.serialize_expense(exp).model_dump(mode="json"))


@router.get("/expenses", response_model=dict[str, Any])
def list_expenses(
    category: str | None = None,
    month: str | None = Query(default=None, pattern=_MONTH_PATTERN),  # noqa: B008
    date_from: date | None = None,
    date_to: date | None = None,
    recurring: bool | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),  # noqa: B008
    cursor: str | None = None,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows, next_cursor, total = expense_svc.list_expenses(
        db,
        startup_id=membership.startup_id,
        category=category,
        month=month,
        date_from=date_from,
        date_to=date_to,
        recurring=recurring,
        limit=limit,
        cursor=cursor,
    )
    return success_response(
        {"expenses": [expense_svc.serialize_expense(r).model_dump(mode="json") for r in rows]},
        Meta(next_cursor=next_cursor, total_estimate=total),
    )


# Declared before /expenses/{expense_id} so "summary" is never captured as an id.
@router.get("/expenses/summary", response_model=dict[str, Any])
def expense_summary(
    month: str | None = Query(default=None, pattern=_MONTH_PATTERN),  # noqa: B008
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    target = month or datetime.now(UTC).strftime("%Y-%m")
    data = expense_svc.category_summary(db, startup_id=membership.startup_id, month=target)
    return success_response(CategorySummary(**data).model_dump(mode="json"))


@router.get("/expenses/{expense_id}", response_model=dict[str, Any])
def get_expense(
    expense_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    exp = expense_svc.get_expense(db, startup_id=membership.startup_id, expense_id=expense_id)
    return success_response(expense_svc.serialize_expense(exp).model_dump(mode="json"))


@router.patch("/expenses/{expense_id}", response_model=dict[str, Any])
def update_expense(
    expense_id: uuid.UUID,
    payload: ExpenseUpdate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    exp = expense_svc.update_expense(
        db, startup_id=membership.startup_id, expense_id=expense_id, data=payload
    )
    # The linked outflow may have changed, so burn / runway changed: re-evaluate in the same txn.
    runway_svc.evaluate_runway_alert(db, startup_id=membership.startup_id)
    runway_svc.upsert_runway_signal(db, startup_id=membership.startup_id)
    db.commit()
    return success_response(expense_svc.serialize_expense(exp).model_dump(mode="json"))


@router.delete("/expenses/{expense_id}", response_model=dict[str, Any])
def delete_expense(
    expense_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    expense_svc.delete_expense(db, startup_id=membership.startup_id, expense_id=expense_id)
    # The outflow is gone, so burn / runway changed: re-evaluate in the same transaction.
    runway_svc.evaluate_runway_alert(db, startup_id=membership.startup_id)
    runway_svc.upsert_runway_signal(db, startup_id=membership.startup_id)
    db.commit()
    return success_response({"deleted": True})


@router.post("/expenses/{expense_id}/receipt", response_model=dict[str, Any])
async def upload_expense_receipt(
    expense_id: uuid.UUID,
    file: UploadFile = File(...),  # noqa: B008
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    # Bounded read: one byte past the cap is enough for the service to reject, without
    # buffering an arbitrarily large upload in memory.
    content = await file.read(expense_svc.MAX_RECEIPT_BYTES + 1)
    exp = expense_svc.attach_receipt(
        db,
        startup_id=membership.startup_id,
        expense_id=expense_id,
        filename=file.filename,
        content_type=file.content_type,
        size_bytes=len(content),
        content=content,
    )
    db.commit()
    return success_response(expense_svc.serialize_expense(exp).model_dump(mode="json"))


@router.get("/expenses/{expense_id}/receipt", response_model=dict[str, Any])
def get_expense_receipt(
    expense_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """The only way to read a receipt: a signed link that expires in a few minutes."""
    link = expense_svc.receipt_link(
        db,
        startup_id=membership.startup_id,
        expense_id=expense_id,
        api_public_url=settings.API_PUBLIC_URL,
    )
    db.commit()  # persists a legacy receipt's move behind authentication
    return success_response({**link, "expires_at": link["expires_at"].isoformat()})


@router.get("/receipts/download", response_class=FileResponse)
def download_receipt(
    token: str = Query(..., min_length=1, max_length=2000)
) -> FileResponse:  # noqa: B008
    """Serves a locally stored receipt to the holder of a valid, unexpired signed link.

    The link is the credential (it is only ever issued by `GET /expenses/{id}/receipt` after the
    role and workspace checks). Expired, tampered or unknown tokens are all the same 404.
    """
    try:
        key = verify_key(token)
    except InvalidSignedToken:
        raise NotFound() from None
    if not expense_svc.RECEIPT_KEY_PATTERN.fullmatch(key):
        raise NotFound()
    storage = get_storage()
    path = storage.open_private(key) if isinstance(storage, LocalStorage) else None
    if path is None:
        raise NotFound()
    return FileResponse(
        path,
        media_type=expense_svc.receipt_content_type(key),
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="receipt{path.suffix}"',
        },
    )


@router.delete("/expenses/{expense_id}/receipt", response_model=dict[str, Any])
def delete_expense_receipt(
    expense_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    exp = expense_svc.remove_receipt(db, startup_id=membership.startup_id, expense_id=expense_id)
    db.commit()
    return success_response(expense_svc.serialize_expense(exp).model_dump(mode="json"))


@router.post("/budgets", response_model=dict[str, Any])
def create_budget(
    payload: BudgetCreate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    budget = budget_svc.create_budget(
        db, startup_id=membership.startup_id, created_by=user.id, data=payload
    )
    spent = budget_svc.spent_for(db, startup_id=membership.startup_id, budget=budget)
    db.commit()
    return success_response(
        budget_svc.serialize_budget(budget, spent_minor=spent).model_dump(mode="json")
    )


@router.get("/budgets", response_model=dict[str, Any])
def list_budgets(
    month: str = Query(..., pattern=_MONTH_PATTERN),  # noqa: B008
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = budget_svc.list_budgets(db, startup_id=membership.startup_id, month=month)
    unbudgeted = budget_svc.unbudgeted_rows(
        db, startup_id=membership.startup_id, month=month, budgeted=[b for b, _ in rows]
    )
    return success_response(
        {
            "month": month,
            "budgets": [
                budget_svc.serialize_budget(b, spent_minor=spent).model_dump(mode="json")
                for b, spent in rows
            ]
            + [u.model_dump(mode="json") for u in unbudgeted],
        }
    )


def _seeded_response(
    db: Session, *, startup_id: uuid.UUID, month: str, created_ids: set[uuid.UUID]
) -> dict[str, Any]:
    rows = budget_svc.list_budgets(db, startup_id=startup_id, month=month)
    return success_response(
        {
            "month": month,
            "budgets": [
                budget_svc.serialize_budget(b, spent_minor=spent).model_dump(mode="json")
                for b, spent in rows
                if b.id in created_ids
            ],
        }
    )


@router.post("/budgets/draft-from-actuals", response_model=dict[str, Any])
def draft_budgets_from_actuals(
    payload: SeedRequest,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    created = budget_svc.draft_from_actuals(
        db, startup_id=membership.startup_id, created_by=user.id, period_month=payload.period_month
    )
    created_ids = {b.id for b in created}  # read before commit: expire_on_commit would re-SELECT
    db.commit()
    return _seeded_response(
        db, startup_id=membership.startup_id, month=payload.period_month, created_ids=created_ids
    )


@router.post("/budgets/copy-last-month", response_model=dict[str, Any])
def copy_budgets_from_last_month(
    payload: SeedRequest,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    created = budget_svc.copy_last_month(
        db, startup_id=membership.startup_id, created_by=user.id, period_month=payload.period_month
    )
    created_ids = {b.id for b in created}  # read before commit: expire_on_commit would re-SELECT
    db.commit()
    return _seeded_response(
        db, startup_id=membership.startup_id, month=payload.period_month, created_ids=created_ids
    )


@router.get("/budgets/{budget_id}", response_model=dict[str, Any])
def get_budget(
    budget_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    budget = budget_svc.get_budget(db, startup_id=membership.startup_id, budget_id=budget_id)
    spent = budget_svc.spent_for(db, startup_id=membership.startup_id, budget=budget)
    return success_response(
        budget_svc.serialize_budget(budget, spent_minor=spent).model_dump(mode="json")
    )


@router.patch("/budgets/{budget_id}", response_model=dict[str, Any])
def update_budget(
    budget_id: uuid.UUID,
    payload: BudgetUpdate,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    budget = budget_svc.update_budget(
        db, startup_id=membership.startup_id, budget_id=budget_id, data=payload
    )
    spent = budget_svc.spent_for(db, startup_id=membership.startup_id, budget=budget)
    db.commit()
    return success_response(
        budget_svc.serialize_budget(budget, spent_minor=spent).model_dump(mode="json")
    )


@router.delete("/budgets/{budget_id}", response_model=dict[str, Any])
def delete_budget(
    budget_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    budget_svc.delete_budget(db, startup_id=membership.startup_id, budget_id=budget_id)
    db.commit()
    return success_response({"deleted": True})


@router.post("/model/generate", status_code=status.HTTP_202_ACCEPTED, response_model=dict[str, Any])
def generate_model(
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = model_svc.create_model(db, startup_id=membership.startup_id, created_by=user.id)
    db.commit()
    return success_response({"id": str(row.id), "status": row.status.value})


@router.get("/model", response_model=dict[str, Any])
def get_latest_model(
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = model_svc.latest_model(db, startup_id=membership.startup_id)
    if row is None:
        return success_response({"status": "none"})
    return success_response(model_svc.serialize_model(row))


@router.get("/model/{model_id}", response_model=dict[str, Any])
def get_model(
    model_id: uuid.UUID,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = model_svc.get_model(db, startup_id=membership.startup_id, model_id=model_id)
    return success_response(model_svc.serialize_model(row))
