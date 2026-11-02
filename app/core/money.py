"""Shared money formatting for user-facing copy (notifications, emails)."""


def format_money(minor: int, currency: str) -> str:
    """Minor units to display copy, e.g. 150000 NGN -> `NGN 1,500.00`."""
    return f"{currency} {minor / 100:,.2f}"
