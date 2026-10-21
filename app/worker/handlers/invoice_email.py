import html
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models.enums import InvoiceStatus, MembershipRole, MembershipStatus
from app.db.models.invoice import Invoice
from app.db.models.job import Job
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import UserProfile
from app.platform.email import EmailAttachment, EmailMessage, get_email_sender
from app.services.finance.invoice_pdf import render_invoice_pdf
from app.worker.runner import register_handler


def _fmt_money(minor: int, currency: str) -> str:
    # minor units -> major, e.g. 134375, "NGN" -> "1,343.75 NGN"
    return f"{Decimal(minor) / 100:,.2f} {html.escape(currency)}"


def _pay_button(inv: Invoice) -> str:
    # The link comes from a third-party API response: allow only absolute http(s) URLs (never
    # javascript:, data:, protocol-relative) and escape it for the attribute context.
    url = inv.pay_url
    if not url or inv.status != InvoiceStatus.sent or not url.startswith(("https://", "http://")):
        return ""
    return (
        f'<p><a href="{html.escape(url, quote=True)}" '
        "style='display:inline-block;padding:10px 18px;background:#0b6b3a;color:#fff;"
        "text-decoration:none;border-radius:6px'>Pay now</a></p>"
    )


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
        f"<p>Due: {due}</p>{_pay_button(inv)}</div>"
    )


def handle_invoice_email(db: Session, job: Job) -> None:
    inv = db.get(Invoice, uuid.UUID(str(job.payload["invoice_id"])))
    if inv is None or not inv.client_email:
        return  # deleted since enqueue / no recipient — nothing to send
    startup = db.get(Startup, inv.startup_id)
    name = startup.name if startup and startup.name else "Cofoundaz"
    # Strip CR/LF to prevent header injection in the SMTP backend.
    subject = f"Invoice {inv.number} from {name}".replace("\r", " ").replace("\n", " ")
    pdf = EmailAttachment(
        filename=f"invoice-{inv.number}.pdf",
        content=render_invoice_pdf(inv, name),
        content_type="application/pdf",
    )
    get_email_sender().send(
        EmailMessage(
            to=inv.client_email,
            subject=subject,
            html=render_invoice_email(inv, name),
            attachments=[pdf],
        )
    )


def _founder_name(db: Session, startup: Startup | None, fallback: str) -> str:
    """Display name of the workspace owner (creator, when still an active founder), else the
    earliest active founder; falls back to the startup name when nobody has a name set."""
    if startup is None:
        return fallback
    rows = (
        db.query(Membership.user_id, UserProfile.full_name)
        .outerjoin(UserProfile, UserProfile.user_id == Membership.user_id)
        .filter(
            Membership.startup_id == startup.id,
            Membership.role == MembershipRole.founder,
            Membership.status == MembershipStatus.active,
        )
        .order_by(Membership.created_at, Membership.id)
        .all()
    )
    rows.sort(key=lambda r: r[0] != startup.created_by)  # stable: owner first
    for _uid, full_name in rows:
        if full_name and full_name.strip():
            return str(full_name).strip()
    return fallback


def _safe_pay_url(inv: Invoice) -> str | None:
    url = inv.pay_url
    if url and url.startswith(("https://", "http://")):
        return url
    return None


def render_reminder_email(inv: Invoice, startup_name: str, founder_name: str) -> str:
    """Friendly overdue nudge. Every interpolated value is user- or provider-controlled, so all
    of it is HTML-escaped; the pay link is only shown for an absolute http(s) URL."""
    words = inv.client_name.split()
    client_first = words[0] if words else "there"
    due = inv.due_on.isoformat() if inv.due_on else "the due date"
    text = (
        f"Hi {html.escape(client_first)}, a friendly nudge that invoice {html.escape(inv.number)} "
        f"for {_fmt_money(inv.total_minor, inv.currency)} was due on {html.escape(due)}."
    )
    url = _safe_pay_url(inv)
    if url:
        safe = html.escape(url, quote=True)
        text += f' You can view and pay it here: <a href="{safe}">{safe}</a>.'
    text += f" Thanks, {html.escape(founder_name)}"
    return f"<div style='font-family:system-ui,sans-serif;max-width:560px'><p>{text}</p></div>"


def handle_invoice_reminder(db: Session, job: Job) -> None:
    inv = db.get(Invoice, uuid.UUID(str(job.payload["invoice_id"])))
    # Paid (or deleted) between queueing and sending: stay silent.
    if inv is None or inv.status != InvoiceStatus.sent or not inv.client_email:
        return
    startup = db.get(Startup, inv.startup_id)
    name = startup.name if startup and startup.name else "Cofoundaz"
    subject = f"Reminder: invoice {inv.number} from {name}".replace("\r", " ").replace("\n", " ")
    pdf = EmailAttachment(
        filename=f"invoice-{inv.number}.pdf",
        content=render_invoice_pdf(inv, name),
        content_type="application/pdf",
    )
    get_email_sender().send(
        EmailMessage(
            to=inv.client_email,
            subject=subject,
            html=render_reminder_email(inv, name, _founder_name(db, startup, name)),
            attachments=[pdf],
        )
    )


register_handler("email.invoice_sent", handle_invoice_email)
register_handler("email.invoice_reminder", handle_invoice_reminder)
