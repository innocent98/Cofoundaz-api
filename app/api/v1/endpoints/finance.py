import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import MembershipRole, TransactionDirection
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.budget import BudgetCreate, BudgetUpdate, SeedRequest
from app.schemas.expense import CategorySummary, ExpenseCreate, ExpenseUpdate
from app.schemas.finance import CashFlowResponse, TransactionCreate, TransactionUpdate
from app.schemas.finance_runway import AssumptionsUpdate, RunwayResponse
from app.schemas.invoice import InvoiceCreate, InvoiceUpdate
from app.services.finance import budgets as budget_svc
from app.services.finance import cashflow as cashflow_svc
from app.services.finance import expenses as expense_svc
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
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = expense_svc.list_expenses(
        db,
        startup_id=membership.startup_id,
        category=category,
        month=month,
        date_from=date_from,
        date_to=date_to,
        recurring=recurring,
    )
    return success_response(
        {"expenses": [expense_svc.serialize_expense(r).model_dump(mode="json") for r in rows]}
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
    return success_response(
        {
            "month": month,
            "budgets": [
                budget_svc.serialize_budget(b, spent_minor=spent).model_dump(mode="json")
                for b, spent in rows
            ],
        }
    )


def _seeded_response(
    db: Session, *, startup_id: uuid.UUID, month: str, created: list[Any]
) -> dict[str, Any]:
    created_ids = {b.id for b in created}
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
    db.commit()
    return _seeded_response(
        db, startup_id=membership.startup_id, month=payload.period_month, created=created
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
    db.commit()
    return _seeded_response(
        db, startup_id=membership.startup_id, month=payload.period_month, created=created
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
