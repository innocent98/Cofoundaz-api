"""Dependency-free invoice PDF writer.

A deliberately tiny PDF 1.4 emitter: Helvetica (a standard font, so nothing is embedded), one
uncompressed content stream per page, Latin-1 text only. No new package means no new supply-chain
or audit surface; the trade-off is no Unicode, so anything outside Latin-1 is replaced with "?".
"""

import re
from decimal import Decimal

from app.db.models.enums import InvoiceStatus
from app.db.models.invoice import Invoice

_PAGE_W = 595
_PAGE_H = 842
_MARGIN_X = 50
_TOP_Y = 790
_LEADING = 18
_LINES_PER_PAGE = 30
_AMOUNT_X = 400
_QTY_X = 300

# (x, text, bold) runs that make up one visual line.
_Run = tuple[int, str, bool]
_Line = list[_Run]


def _latin1(text: str) -> bytes:
    # Control characters (CR/LF/tab) would break a content-stream line; flatten them first.
    flat = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return flat.encode("latin-1", "replace")


def _escape(text: str) -> bytes:
    return _latin1(text).replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _money(minor: int, currency: str) -> str:
    return f"{currency} {Decimal(minor) / 100:,.2f}"


def _wrapped(text: str, width: int = 90) -> list[_Line]:
    """Free text as PDF lines (the writer has no wrapping): split on newlines, then on width."""
    lines: list[_Line] = []
    for para in text.splitlines() or [""]:
        words, row = para.split(), ""
        for word in words:
            if row and len(row) + 1 + len(word) > width:
                lines.append([(_MARGIN_X, row, False)])
                row = word
            else:
                row = f"{row} {word}".strip()
        lines.append([(_MARGIN_X, row, False)])
    return lines


def _lines(inv: Invoice, startup_name: str) -> list[_Line]:
    out: list[_Line] = []
    out.append([(_MARGIN_X, f"Invoice {inv.number}", True)])
    out.append([(_MARGIN_X, f"From: {startup_name}", False)])
    out.append([(_MARGIN_X, f"Bill to: {inv.client_name}", False)])
    out.append([(_MARGIN_X, inv.client_email, False)])
    issued = inv.issued_on.isoformat() if inv.issued_on else "Not issued yet"
    due = inv.due_on.isoformat() if inv.due_on else "On receipt"
    out.append([(_MARGIN_X, f"Issued: {issued}", False), (_QTY_X, f"Due: {due}", False)])
    out.append([])
    out.append(
        [
            (_MARGIN_X, "Description", True),
            (_QTY_X, "Qty x Unit", True),
            (_AMOUNT_X, "Amount", True),
        ]
    )
    for li in inv.line_items:
        qty = int(li["quantity"])
        unit = int(li["unit_price_minor"])
        out.append(
            [
                (_MARGIN_X, str(li["description"])[:48], False),
                (_QTY_X, f"{qty} x {_money(unit, inv.currency)}", False),
                (_AMOUNT_X, _money(qty * unit, inv.currency), False),
            ]
        )
    out.append([])
    out.append(
        [(_QTY_X, "Subtotal", False), (_AMOUNT_X, _money(inv.subtotal_minor, inv.currency), False)]
    )
    out.append(
        [
            (_QTY_X, f"Tax ({Decimal(str(inv.tax_percent)):.2f}%)", False),
            (_AMOUNT_X, _money(inv.tax_minor, inv.currency), False),
        ]
    )
    out.append([(_QTY_X, "Total", True), (_AMOUNT_X, _money(inv.total_minor, inv.currency), True)])
    pd = inv.payment_details
    if pd:
        out.append([])
        out.append([(_MARGIN_X, "Pay by bank transfer", True)])
        out.append([(_MARGIN_X, f"Bank: {str(pd.get('bank_name', ''))[:60]}", False)])
        out.append([(_MARGIN_X, f"Account name: {str(pd.get('account_name', ''))[:60]}", False)])
        out.append(
            [(_MARGIN_X, f"Account number: {str(pd.get('account_number', ''))[:40]}", False)]
        )
        if pd.get("instructions"):
            out.extend(_wrapped(str(pd["instructions"])))
    if inv.notes:
        out.append([])
        out.append([(_MARGIN_X, "Notes", True)])
        out.extend(_wrapped(inv.notes))
    if inv.status == InvoiceStatus.paid and inv.paid_at is not None:
        out.append([])
        out.append([(_MARGIN_X, f"Paid on {inv.paid_at.date().isoformat()}", False)])
    elif inv.pay_url and inv.pay_url.startswith(("https://", "http://")):
        out.append([])
        out.append([(_MARGIN_X, f"Pay online: {inv.pay_url}", False)])
    return out


def _page_stream(lines: list[_Line], *, stamp: bool) -> bytes:
    parts: list[bytes] = []
    if stamp:
        parts.append(b"BT /F2 64 Tf 1 0 0 1 380 740 Tm 0.1 0.55 0.25 rg (PAID) Tj 0 g ET")
    y = _TOP_Y
    for line in lines:
        for x, text, bold in line:
            font = b"/F2" if bold else b"/F1"
            parts.append(
                b"BT "
                + font
                + b" 11 Tf 1 0 0 1 "
                + str(x).encode()
                + b" "
                + str(y).encode()
                + b" Tm ("
                + _escape(text)
                + b") Tj ET"
            )
        y -= _LEADING
    return b"\n".join(parts)


def render_invoice_pdf(inv: Invoice, startup_name: str) -> bytes:
    """Render `inv` to PDF bytes; a "PAID" stamp is drawn on page 1 when the invoice is paid."""
    lines = _lines(inv, startup_name)
    pages = [lines[i : i + _LINES_PER_PAGE] for i in range(0, len(lines), _LINES_PER_PAGE)] or [[]]
    stamp = inv.status == InvoiceStatus.paid

    # Object layout: 1 catalog, 2 pages, 3 Helvetica, 4 Helvetica-Bold, then page+content pairs.
    n_pages = len(pages)
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(n_pages))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    for i, page_lines in enumerate(pages):
        content_id = 6 + 2 * i
        objects.append(
            (
                f"<< /Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 {_PAGE_W} {_PAGE_H}]\n"
                "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >>\n"
                f"/Contents {content_id} 0 R >>"
            ).encode()
        )
        stream = _page_stream(page_lines, stamp=stamp and i == 0)
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)
