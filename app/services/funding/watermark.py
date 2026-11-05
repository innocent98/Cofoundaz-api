"""Per-viewer PDF watermark (D-FUND-8): the viewer's verified email and the time of viewing,
stamped diagonally and in the footer of every page at serve time. Built on pypdf + reportlab,
which Documents already depends on. Fails closed: a PDF we cannot stamp is never served bare."""

import io
from datetime import UTC, datetime

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas

from app.core.errors import AppError
from app.services.documents.esign_pdf import _PDF_ERRORS, MAX_PAGES


def unstampable() -> AppError:
    return AppError(
        "DOCUMENT_UNAVAILABLE",
        "This document can't be shown right now.",
        422,
    )


def _label(email: str, at: datetime) -> str:
    safe = email.encode("latin-1", "replace").decode("latin-1")
    return f"{safe}  |  {at.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}  |  Confidential"


def _overlay(width: float, height: float, label: str) -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(width, height))
    c.saveState()
    c.setFillColor(colors.Color(0.45, 0.45, 0.45, alpha=0.22))
    c.setFont("Helvetica-Bold", max(14, min(width, height) / 22))
    c.translate(width / 2, height / 2)
    c.rotate(35)
    for dy in (-height / 4, 0, height / 4):
        c.drawCentredString(0, dy, label)
    c.restoreState()
    c.setFillColor(colors.Color(0.3, 0.3, 0.3, alpha=0.7))
    c.setFont("Helvetica", 7)
    c.drawString(24, 14, label)
    c.save()
    return buf.getvalue()


def stamp_pdf(source: bytes, *, email: str, at: datetime | None = None) -> bytes:
    """The same pages with the viewer's email and the time on each. Raises `unstampable` for an
    encrypted, damaged or over-long PDF."""
    at = at or datetime.now(UTC)
    try:
        reader = PdfReader(io.BytesIO(source))
        if reader.is_encrypted or len(reader.pages) > MAX_PAGES:
            raise unstampable()
        label = _label(email, at)
        writer = PdfWriter()
        cache: dict[tuple[float, float], bytes] = {}
        for page in reader.pages:
            w, h = float(page.mediabox.width), float(page.mediabox.height)
            key = (round(w, 1), round(h, 1))
            if key not in cache:
                cache[key] = _overlay(w, h, label)
            added = writer.add_page(page)
            added.merge_page(PdfReader(io.BytesIO(cache[key])).pages[0])
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except AppError:
        raise
    except _PDF_ERRORS:
        raise unstampable() from None
