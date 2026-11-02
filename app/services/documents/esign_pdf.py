"""The executed PDF: the original pages untouched, followed by a signature page and the audit
certificate (gap-list row 15).

No field placement (BE-D decision): a typed-name signature is stamped on the appended signature
page, not at a position on the document. The caveat on the certificate states what these
signatures are: simple electronic signatures, not certificate-based ones.
"""

import io
from dataclasses import dataclass, field
from datetime import datetime
from html import escape

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.errors import AppError
from app.services.documents.export import _pdf_text

MAX_PAGES = 300
# Malformed PDFs surface as all sorts of errors from the parser; every one is "unreadable".
_PDF_ERRORS = (
    PyPdfError,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    AttributeError,
    OSError,
    RecursionError,
    AssertionError,
)

LEGAL_CAVEAT = (
    "These are simple electronic signatures: each signer typed their name to adopt it as their "
    "signature. Cofoundaz records the signer's email, IP address, browser, the time and the "
    "document's SHA-256 hash, but does not issue a certificate-based or qualified signature and is "
    "not a trust service provider. Whether a typed-name signature is enforceable depends on the "
    "document and the jurisdiction; take legal advice for high-stakes agreements."
)


@dataclass
class SignerStamp:
    name: str
    email: str
    signed_name: str
    signed_at: datetime
    ip: str | None
    user_agent: str | None
    viewed_at: datetime | None
    link_copies: list[str] = field(default_factory=list)


@dataclass
class Certificate:
    request_id: str
    title: str
    filename: str
    sender: str
    sent_at: datetime
    completed_at: datetime
    document_hash: str
    signers: list[SignerStamp]


def _unreadable() -> AppError:
    return AppError(
        "PDF_UNREADABLE",
        "This PDF can't be signed: it is encrypted, damaged or too long. Upload a plain PDF.",
        422,
    )


def validate_pdf(content: bytes) -> int:
    """The page count of a PDF that can be stamped, else 422 `PDF_UNREADABLE`."""
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise _unreadable()
        pages = len(reader.pages)
    except AppError:
        raise
    except _PDF_ERRORS:
        raise _unreadable() from None
    if pages < 1 or pages > MAX_PAGES:
        raise _unreadable()
    return pages


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def _p(text: str | None, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_pdf_text(text or "-"), quote=False).replace("\n", "<br/>"), style)


def _footer(canvas: Canvas, _doc: object) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(20 * mm, 12 * mm, "Cofoundaz signature certificate")
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Certificate page {canvas.getPageNumber()}")
    canvas.restoreState()


def render_certificate(cert: Certificate) -> bytes:
    buf = io.BytesIO()
    base = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=base["Title"], fontName="Helvetica-Bold", fontSize=18)
    h2 = ParagraphStyle(
        "h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12, spaceBefore=10
    )
    body = ParagraphStyle(
        "b", parent=base["BodyText"], fontName="Helvetica", fontSize=9, leading=12
    )
    mono = ParagraphStyle("m", parent=body, fontName="Courier", fontSize=8, leading=10)
    sig = ParagraphStyle("s", parent=body, fontName="Times-Italic", fontSize=20, leading=24)
    note = ParagraphStyle("n", parent=body, fontName="Helvetica-Oblique", fontSize=8, leading=11)
    width = A4[0] - 40 * mm

    flow: list = [_p("Signatures", h1)]
    flow.append(_p(f"{cert.title}", body))
    for s in cert.signers:
        box = Table(
            [
                [_p(s.signed_name, sig)],
                [_p(f"{s.name} <{s.email}>  |  signed {_ts(s.signed_at)}", body)],
            ],
            colWidths=[width],
        )
        box.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#94A3B8")),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor("#CBD5E1")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        flow += [box, Spacer(1, 6)]

    flow.append(_p("Audit certificate", h2))
    meta = [
        ["Document", _p(cert.filename, body)],
        ["Request", _p(cert.request_id, mono)],
        ["Sent by", _p(cert.sender, body)],
        ["Sent", _p(_ts(cert.sent_at), body)],
        ["Completed", _p(_ts(cert.completed_at), body)],
        ["Document SHA-256", _p(cert.document_hash, mono)],
    ]
    meta_table = Table(meta, colWidths=[35 * mm, width - 35 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (0, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
            ]
        )
    )
    flow.append(meta_table)

    flow.append(_p("Signers", h2))
    for i, s in enumerate(cert.signers, 1):
        rows = [
            ["Signer", _p(f"{i}. {s.name}", body)],
            ["Email", _p(s.email, body)],
            ["Typed signature", _p(s.signed_name, body)],
            ["Viewed", _p(_ts(s.viewed_at) if s.viewed_at else None, body)],
            [
                "Link handed out",
                _p("\n".join(s.link_copies) or "Never left the signer's inbox", body),
            ],
            ["Signed", _p(_ts(s.signed_at), body)],
            ["IP address", _p(s.ip, mono)],
            ["User agent", _p(s.user_agent, mono)],
        ]
        t = Table(rows, colWidths=[35 * mm, width - 35 * mm])
        t.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (0, -1), 9),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
                ]
            )
        )
        flow += [t, Spacer(1, 8)]

    box = Table([[_p(LEGAL_CAVEAT, note)]], colWidths=[width])
    box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#B45309")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FEF3C7")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    flow += [Spacer(1, 6), box]

    SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title="Signature certificate",
        author="Cofoundaz",
    ).build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def build_executed_pdf(source: bytes, cert: Certificate) -> bytes:
    """The source's pages followed by the certificate's. The source is never modified."""
    try:
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(source), strict=False))
        writer.append(PdfReader(io.BytesIO(render_certificate(cert))))
        writer.add_metadata({"/Title": _pdf_text(cert.title)[:200], "/Producer": "Cofoundaz"})
        out = io.BytesIO()
        writer.write(out)
    except _PDF_ERRORS:
        raise _unreadable() from None
    return out.getvalue()
