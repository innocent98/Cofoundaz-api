"""XLSX exports of the ledger and of the financial model."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import FinancialModelStatus, TransactionDirection
from app.db.models.financial_model import FinancialModel
from app.services.finance import service as finance_svc
from app.services.finance.errors import _validation
from app.services.finance.model_service import ModelNotReady
from app.services.finance.xlsx import build_xlsx

# A workspace's ledger is small; this is a guard against an unbounded response, not a page size.
MAX_EXPORT_ROWS = 50_000
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _major(minor: int) -> Decimal:
    return Decimal(minor) / 100


def transactions_xlsx(
    db: Session,
    *,
    startup_id: uuid.UUID,
    category: str | None = None,
    uncategorized: bool = False,
    direction: TransactionDirection | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> bytes:
    """Every ledger row matching the same filters as GET /finance/transactions, newest first."""
    q = finance_svc.filtered_query(
        db,
        startup_id=startup_id,
        category=category,
        uncategorized=uncategorized,
        direction=direction,
        date_from=date_from,
        date_to=date_to,
    )
    rows = finance_svc.ordered(q).limit(MAX_EXPORT_ROWS + 1).all()
    if len(rows) > MAX_EXPORT_ROWS:
        raise _validation(
            "date_from",
            f"Too many transactions to export at once (over {MAX_EXPORT_ROWS:,})."
            " Narrow the date range.",
        )
    sheet: list[list[Any]] = [
        ["Date", "Description", "Category", "Direction", "Amount", "Currency", "Source"]
    ]
    for r in rows:
        sheet.append(
            [
                r.date.isoformat(),
                r.description,
                r.category or "Uncategorized",
                r.direction.value,
                _major(r.amount_minor),
                r.currency,
                r.source.value,
            ]
        )
    return build_xlsx([("Transactions", sheet)])


def model_xlsx(row: FinancialModel) -> bytes:
    """The three statements as three sheets, one column per projected month, amounts in naira."""
    if row.status != FinancialModelStatus.complete:
        raise ModelNotReady()
    months: list[str] = next(
        (s["months"] for s in (row.pnl, row.cash_flow, row.balance_sheet) if s and s.get("months")),
        [],
    )
    sheets = []
    for title, stmt in (
        ("P&L", row.pnl),
        ("Cash flow", row.cash_flow),
        ("Balance sheet", row.balance_sheet),
    ):
        if not stmt:
            continue
        data: list[list[Any]] = [["Line", *months]]
        for line in stmt["rows"]:
            data.append([line["label"], *[_major(int(v)) for v in line["values"]]])
        sheets.append((title, data))
    return build_xlsx(sheets)
