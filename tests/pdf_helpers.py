"""A real one-page PDF for tests that send a file for signature (the API reads and validates it)."""

import io

from reportlab.pdfgen.canvas import Canvas

from app.db.models.document import DocumentFile
from app.platform.storage import get_storage


def make_pdf(text: str = "Test agreement") -> bytes:
    buf = io.BytesIO()
    canvas = Canvas(buf)
    canvas.drawString(72, 720, text)
    canvas.showPage()
    canvas.save()
    return buf.getvalue()


def store_pdf(file: DocumentFile, text: str = "Test agreement") -> bytes:
    """Save a PDF under the file's storage key and make the row consistent with it."""
    content = make_pdf(text)
    get_storage().save_private(file.storage_key, content, "application/pdf")
    file.content_type = "application/pdf"
    file.size_bytes = len(content)
    file.is_private = True
    return content
