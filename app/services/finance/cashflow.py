import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.models.enums import TransactionDirection
from app.db.models.finance import Transaction

_IN = TransactionDirection.inflow
_OUT = TransactionDirection.outflow


def _months_back(today: date, n: int) -> date:
    """First day of the month n months before `today`'s month."""
    y, m = today.year, today.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def cash_flow_summary(db: Session, *, startup_id: uuid.UUID) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    base = db.query(Transaction).filter(Transaction.startup_id == startup_id)

    inflow = func.coalesce(
        func.sum(case((Transaction.direction == _IN, Transaction.amount_minor), else_=0)), 0
    )
    outflow = func.coalesce(
        func.sum(case((Transaction.direction == _OUT, Transaction.amount_minor), else_=0)), 0
    )

    # cash on hand: all-time
    all_in, all_out = base.with_entities(inflow, outflow).one()
    cash_on_hand = int(all_in) - int(all_out)

    # trailing 3 months (from the first day of the month 2 months ago through today)
    since_3mo = _months_back(today, 2)
    win_in, win_out = (
        base.filter(Transaction.date >= since_3mo).with_entities(inflow, outflow).one()
    )
    win_in, win_out = int(win_in), int(win_out)
    net_out = win_out - win_in
    monthly_burn = max(0, round(net_out / 3))
    monthly_revenue = round(win_in / 3)

    # Runway only exists when we are actually burning AND have cash left; otherwise null
    # (never a divide-by-zero, never a negative runway).
    runway_months = (
        round(cash_on_hand / monthly_burn, 1) if monthly_burn > 0 and cash_on_hand > 0 else None
    )
    runway_low = runway_months is not None and runway_months < 6

    # by_month: last 6 calendar months, ascending, zero-filled
    since_6mo = _months_back(today, 5)
    month = func.to_char(func.date_trunc("month", Transaction.date), "YYYY-MM")
    rows = (
        base.filter(Transaction.date >= since_6mo)
        .with_entities(month.label("m"), inflow, outflow)
        .group_by(month)
        .all()
    )
    got = {m: (int(i), int(o)) for m, i, o in rows}
    by_month = []
    for k in range(5, -1, -1):
        d = _months_back(today, k)
        key = f"{d.year:04d}-{d.month:02d}"
        i, o = got.get(key, (0, 0))
        by_month.append({"month": key, "inflow": i, "outflow": o, "net": i - o})

    # prevailing currency: most common on the startup's transactions, else default
    cur_row = (
        base.with_entities(Transaction.currency, func.count())
        .group_by(Transaction.currency)
        .order_by(func.count().desc())
        .first()
    )
    currency = cur_row[0] if cur_row is not None else "NGN"

    return {
        "cash_on_hand": cash_on_hand,
        "monthly_burn": monthly_burn,
        "monthly_revenue": monthly_revenue,
        "runway_months": runway_months,
        "runway_low": runway_low,
        "currency": currency,
        "by_month": by_month,
    }
