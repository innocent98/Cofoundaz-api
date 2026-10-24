from typing import Literal

# D-FIN-8: 36 months, with a yearly rollup. The engine is deterministic, so the longer horizon
# costs no extra AI spend.
MODEL_HORIZON_MONTHS = 36
MONTHS_PER_YEAR = 12

PNL_ROWS: tuple[str, ...] = (
    "Revenue",
    "COGS",
    "Gross profit",
    "Opex",
    "Depreciation",
    "Net income",
)
CASH_FLOW_ROWS: tuple[str, ...] = (
    "Opening cash",
    "Collections",
    "Payments",
    "Capex",
    "Net cash flow",
    "Closing cash",
)
BALANCE_SHEET_ROWS: tuple[str, ...] = (
    "Cash",
    "Accounts receivable",
    "Fixed assets (net)",
    "Total assets",
    "Accounts payable",
    "Total liabilities",
    "Paid-in capital",
    "Retained earnings",
    "Total equity",
    "Total liabilities + equity",
    "Balance check",
)

RollupKind = Literal["sum", "first", "last"]

# How each row folds into a year: flows add up, an opening balance is the year's first month,
# every other balance is the year's last month. Labels from models generated before the 36-month
# engine are included so an old row can still be rolled up.
ROLLUP_KIND: dict[str, RollupKind] = {
    "Revenue": "sum",
    "COGS": "sum",
    "Gross profit": "sum",
    "Opex": "sum",
    "Depreciation": "sum",
    "Net income": "sum",
    "Opening cash": "first",
    "Collections": "sum",
    "Payments": "sum",
    "Capex": "sum",
    "Net cash flow": "sum",
    "Closing cash": "last",
    "Cash": "last",
    "Accounts receivable": "last",
    "Fixed assets (net)": "last",
    "Total assets": "last",
    "Accounts payable": "last",
    "Total liabilities": "last",
    "Paid-in capital": "last",
    "Retained earnings": "last",
    "Total equity": "last",
    "Total liabilities + equity": "last",
    "Balance check": "last",
}
