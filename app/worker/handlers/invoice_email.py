import html
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models.invoice import Invoice
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.platform.email import EmailMessage, get_email_sender
from app.worker.runner import register_handler


def _fmt_money(minor: int, currency: str) -> str:
    # minor units -> major, e.g. 134375, "NGN" -> "1,343.75 NGN"
    return f"{Decimal(minor) / 100:,.2f} {html.escape(currency)}"


def render_invoice_email(inv: Invoice, startup_name: str) -> str:
    # Line-item descriptions and client_name are user-controlled: escape everything interpolated.
    rows = "".join(
        f"<tr><td>{html.escape(str(li['description']))} (x{int(li['quantity'])})</td>"
        f"<td style='text-align:right'>"
        f"{_fmt_money(int(li['quantity']) * int(li['unit_price_minor']), inv.currency)}</td></tr>"
        for li in inv.line_items
    )
    due = html.escape(inv.due_on.isoformat()) if inv.due_on else "On receipt"
    return (
        f"<div style='font-family:system-ui,sans-serif;max-width:560px'>"
        f"<h2>Invoice {html.escape(inv.number)}</h2>"
        f"<p>From {html.escape(startup_name)}</p>"
        f"<p>Bill to {html.escape(inv.client_name)}</p>"
        f"<table style='width:100%'>{rows}</table>"
        f"<p>Subtotal: {_fmt_money(inv.subtotal_minor, inv.currency)}<br>"
        f"Tax: {_fmt_money(inv.tax_minor, inv.currency)}<br>"
        f"<strong>Total: {_fmt_money(inv.total_minor, inv.currency)}</strong></p>"
        f"<p>Due: {due}</p></div>"
    )


def handle_invoice_email(db: Session, job: Job) -> None:
    inv = db.get(Invoice, uuid.UUID(str(job.payload["invoice_id"])))
    if inv is None or not inv.client_email:
        return  # deleted since enqueue / no recipient — nothing to send
    startup = db.get(Startup, inv.startup_id)
    name = startup.name if startup and startup.name else "Cofoundaz"
    # Strip CR/LF to prevent header injection in the SMTP backend.
    subject = f"Invoice {inv.number} from {name}".replace("\r", " ").replace("\n", " ")
    get_email_sender().send(
        EmailMessage(to=inv.client_email, subject=subject, html=render_invoice_email(inv, name))
    )


register_handler("email.invoice_sent", handle_invoice_email)
