"""Per-viewer PDF watermark (D-FUND-8): the viewer's verified email and the time of viewing,
stamped diagonally and in the footer of every page at serve time. Built on pypdf + reportlab,
which Documents already depends on. Fails closed: a PDF we cannot stamp is never served bare."""

import io
from datetime import UTC, datetime

from PIL import Image
from pypdf import PdfReader, PdfWriter, Transformation
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


MAX_IMAGE_PIXELS = 40_000_000  # ~120 MB decoded as RGB; a small PNG can otherwise expand hugely


def image_to_pdf(raw: bytes) -> bytes:
    """One-page PDF of a PNG/JPEG so an image can carry the same watermark as a PDF. The size is
    checked from the header before anything is decoded."""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.width * img.height > MAX_IMAGE_PIXELS:
                raise unstampable()
            out = io.BytesIO()
            img.convert("RGB").save(out, "PDF")
            return out.getvalue()
    except AppError:
        raise
    except Exception:  # noqa: BLE001 - Pillow raises many types for a bad or oversized image
        raise unstampable() from None


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
            # The visible area is the crop box (it defaults to the media box), and its corner need
            # not be the origin: place the overlay on that box, not on (0, 0).
            box = page.cropbox
            w, h = float(box.width), float(box.height)
            key = (round(w, 1), round(h, 1))
            if key not in cache:
                cache[key] = _overlay(w, h, label)
            added = writer.add_page(page)
            overlay = PdfReader(io.BytesIO(cache[key])).pages[0]
            added.merge_transformed_page(
                overlay, Transformation().translate(float(box.left), float(box.bottom))
            )
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except AppError:
        raise
    except _PDF_ERRORS:
        raise unstampable() from None
