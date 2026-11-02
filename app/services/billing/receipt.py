"""A one-page PDF receipt for a paid Cofoundaz billing charge (D-SET-12)."""

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.core.money import format_money
from app.db.models.billing import BillingDetails, BillingInvoice


def build_receipt(
    inv: BillingInvoice, details: BillingDetails | None, startup_name: str | None
) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 60
    c.setFont("Helvetica-Bold", 20)
    c.drawString(50, y, "Cofoundaz")
    c.setFont("Helvetica", 10)
    c.drawRightString(w - 50, y, f"Receipt {inv.number}")
    y -= 40
    rows = [
        (
            "Billed to",
            (details.company_name if details and details.company_name else startup_name)
            or "Workspace",
        ),
        ("Tax ID", details.tax_id if details and details.tax_id else "-"),
        ("Date", (inv.paid_at or inv.created_at).strftime("%d %b %Y")),
        ("Description", inv.description),
        ("Status", inv.status.capitalize()),
        ("Reference", inv.reference),
        ("Amount", format_money(inv.amount_minor, inv.currency)),
    ]
    if inv.refunded_minor:
        rows.append(("Refunded", format_money(inv.refunded_minor, inv.currency)))
    for label, value in rows:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(160, y, str(value)[:90])
        y -= 22
    if details and details.address:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, "Address")
        c.setFont("Helvetica", 10)
        for line in details.address.splitlines()[:5]:
            c.drawString(160, y, line[:90])
            y -= 14
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(50, 60, "Payments are processed by Paystack. This receipt is for your records.")
    c.showPage()
    c.save()
    return buf.getvalue()
