"""Course certificates (D-ACA-5): the credential ID, the PDF, the LinkedIn link and public verify.

Access rules, in one place:

- The owner-only reads (the list and the PDF) filter by ``startup_id`` AND ``user_id`` on every
  query, so another member, a mentor or a stranger who guesses a certificate id gets a 404.
- The public verify takes only the short credential ID (``CFZ-XXXX-XXXX-XXXX``, 32 ** 12 = 2 ** 60 possible
  values, rate limited at the route) and returns holder name, course, date, issuer and validity.
  Nothing else about the learner or the workspace is reachable from it.
"""

import io
import re
import secrets
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, NotFound
from app.db.models.learning import AcademyCourse, Certificate, CertificateFile
from app.db.models.user import UserProfile

ISSUER = "Cofoundaz"
# No 0, O, 1 or I: a code is read aloud and typed from a printout.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
VERIFY_CODE_RE = re.compile(r"^CFZ-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
_FALLBACK_HOLDER = "Cofoundaz member"
# Bounds the work in _printable/_fit whatever length a profile name has; longer text is cut anyway.
_MAX_PRINTED_CHARS = 120
_BRAND = HexColor("#9C5B34")
_INK = HexColor("#2B2118")
_MUTED = HexColor("#6B5E52")


class CertificateNotReady(AppError):  # noqa: N818
    code, http_status = "CERTIFICATE_NOT_READY", 409
    message = "Your certificate is still being prepared. Try again in a moment."


def new_verify_code() -> str:
    parts = ["".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "CFZ-" + "-".join(parts)


def normalize_verify_code(raw: str) -> str | None:
    """Upper-case and trim a typed code; ``None`` when it is not shaped like a credential ID."""
    code = raw.strip().upper()
    return code if VERIFY_CODE_RE.fullmatch(code) else None


def _base_url() -> str:
    return (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")


def verify_url(code: str) -> str:
    """The FE page a QR scan or a LinkedIn visitor lands on; it reads the public API route."""
    return f"{_base_url()}/verify/{code}"


def holder_name_for(db: Session, user_id: uuid.UUID) -> str:
    profile = db.get(UserProfile, user_id)
    name = (profile.full_name or "").strip() if profile is not None else ""
    return name or _FALLBACK_HOLDER


def issue_certificate(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str
) -> Certificate:
    """Create the certificate row with a unique credential ID. Retries on the (rare) code clash."""
    holder = holder_name_for(db, user_id)
    scope = {"startup_id": startup_id, "user_id": user_id, "course_id": course_id}
    existing = db.query(Certificate).filter_by(**scope).first()
    if existing is not None:
        return existing
    for _ in range(5):
        try:
            with db.begin_nested():
                cert = Certificate(
                    **scope,
                    credential_code=secrets.token_urlsafe(16),
                    verify_code=new_verify_code(),
                    holder_name=holder,
                    issued_at=datetime.now(UTC),
                )
                db.add(cert)
                db.flush()
            return cert
        except IntegrityError:
            # Either a concurrent issue for this learner and course won (return theirs, never a
            # 500), or the random credential ID clashed (draw another).
            winner = db.query(Certificate).filter_by(**scope).first()
            if winner is not None:
                return winner
    raise RuntimeError("could not allocate a unique credential ID")


def _printable(name: str) -> str:
    """Make a name safe for the PDF's built-in font: keep what it can draw, fold the rest.

    Helvetica covers Western European letters. Letters outside that (for example the dotted
    Yoruba vowels) are decomposed and stripped of the mark so the name stays readable instead of
    printing a blank box.
    """
    out: list[str] = []
    for char in name[:_MAX_PRINTED_CHARS]:
        try:
            char.encode("cp1252")
            out.append(char)
        except UnicodeEncodeError:
            base = "".join(
                c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c)
            )
            out.append(base if base.isascii() else "")
    return " ".join("".join(out).split()) or _FALLBACK_HOLDER


def _fit(
    c: canvas.Canvas, text: str, font: str, size: float, max_width: float
) -> tuple[str, float]:
    """Shrink to a 14pt floor, then truncate with an ellipsis so text never overruns the border."""
    while size > 14 and c.stringWidth(text, font, size) > max_width:
        size -= 1
    if c.stringWidth(text, font, size) > max_width:
        while text and c.stringWidth(text + "...", font, size) > max_width:
            text = text[:-1]
        text = text.rstrip() + "..."
    return text, size


def render_pdf(*, holder: str, course: str, issued_at: datetime, code: str) -> bytes:
    """One landscape A4 page: issuer, holder, course, completion date, credential ID and a QR."""
    width, height = landscape(A4)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height), invariant=1)
    c.setTitle(f"Cofoundaz certificate {code}")
    c.setAuthor(ISSUER)

    c.setStrokeColor(_BRAND)
    c.setLineWidth(3)
    c.rect(24, 24, width - 48, height - 48)
    c.setLineWidth(0.75)
    c.rect(32, 32, width - 64, height - 64)

    centre = width / 2
    c.setFillColor(_BRAND)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(centre, height - 90, ISSUER.upper())
    c.setFillColor(_INK)
    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(centre, height - 140, "Certificate of Completion")

    c.setFillColor(_MUTED)
    c.setFont("Helvetica", 13)
    c.drawCentredString(centre, height - 190, "This certifies that")

    name = _printable(holder)
    name, size = _fit(c, name, "Helvetica-Bold", 36, width - 140)
    c.setFillColor(_INK)
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(centre, height - 240, name)

    c.setFillColor(_MUTED)
    c.setFont("Helvetica", 13)
    c.drawCentredString(centre, height - 280, "has completed the Cofoundaz Academy course")

    title = _printable(course)
    title, size = _fit(c, title, "Helvetica-Bold", 24, width - 140)
    c.setFillColor(_BRAND)
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(centre, height - 318, title)

    c.setFillColor(_MUTED)
    c.setFont("Helvetica", 12)
    c.drawCentredString(centre, height - 352, f"Completed on {issued_at:%d %B %Y}")

    c.setFillColor(_INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(70, 98, f"Credential ID  {code}")
    c.setFont("Helvetica", 10)
    c.setFillColor(_MUTED)
    c.drawString(70, 80, f"Issued by {ISSUER}")
    c.drawString(70, 64, f"Verify this certificate at {verify_url(code)}")

    qr = QrCodeWidget(verify_url(code))
    x0, y0, x1, y1 = qr.getBounds()
    side = 84
    drawing = Drawing(side, side, transform=[side / (x1 - x0), 0, 0, side / (y1 - y0), 0, 0])
    drawing.add(qr)
    renderPDF.draw(drawing, c, width - 70 - side, 52)
    c.setFont("Helvetica", 8)
    c.drawCentredString(width - 70 - side / 2, 42, "Scan to verify")

    c.showPage()
    c.save()
    return buf.getvalue()


def generate_pdf_for(db: Session, certificate: Certificate) -> bool:
    """Render and store the PDF once. Returns ``False`` when it already existed. No commit."""
    if db.get(CertificateFile, certificate.id) is not None:
        return False
    course = db.get(AcademyCourse, certificate.course_id)
    pdf = render_pdf(
        holder=certificate.holder_name,
        course=course.title if course is not None else certificate.course_id,
        issued_at=certificate.issued_at,
        code=certificate.verify_code,
    )
    db.add(CertificateFile(certificate_id=certificate.id, content=pdf))
    db.flush()
    return True


def linkedin_url(*, course_title: str, issued_at: datetime, code: str) -> str:
    """LinkedIn's add-to-profile (certification) URL, prefilled. FE opens it in a new tab.

    Uses ``organizationName`` until ``ACADEMY_LINKEDIN_ORG_ID`` is set, then ``organizationId``
    (which links the entry to the Cofoundaz LinkedIn page).
    """
    org = (
        {"organizationId": settings.ACADEMY_LINKEDIN_ORG_ID}
        if settings.ACADEMY_LINKEDIN_ORG_ID
        else {"organizationName": ISSUER}
    )
    params = {
        "startTask": "CERTIFICATION_NAME",
        "name": course_title,
        **org,
        "issueYear": str(issued_at.year),
        "issueMonth": str(issued_at.month),
        "certId": code,
        "certUrl": verify_url(code),
    }
    return "https://www.linkedin.com/profile/add?" + urlencode(params, quote_via=quote)


def _owned(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, cert_id: str) -> Certificate:
    try:
        parsed = uuid.UUID(cert_id)
    except ValueError as exc:
        raise NotFound() from exc
    cert = (
        db.query(Certificate)
        .filter_by(id=parsed, startup_id=startup_id, user_id=user_id)
        .filter(Certificate.revoked_at.is_(None))
        .first()
    )
    if cert is None:
        raise NotFound()
    return cert


def owned_pdf(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, cert_id: str
) -> tuple[Certificate, bytes]:
    """The caller's own certificate PDF. Anyone else's id is a 404, indistinguishable from a typo."""
    cert = _owned(db, startup_id, user_id, cert_id)
    stored = db.get(CertificateFile, cert.id)
    if stored is None:
        raise CertificateNotReady()
    return cert, stored.content


def list_certificates(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID) -> list[Certificate]:
    return (
        db.query(Certificate)
        .filter_by(startup_id=startup_id, user_id=user_id)
        .filter(Certificate.revoked_at.is_(None))
        .order_by(Certificate.issued_at.desc())
        .all()
    )


def serialize_certificate(
    cert: Certificate, course_title: str | None, *, pdf_ready: bool
) -> dict[str, Any]:
    title = course_title or cert.course_id
    return {
        "id": str(cert.id),
        "course_id": cert.course_id,
        "course_title": title,
        "holder_name": cert.holder_name,
        "credential_id": cert.verify_code,
        "issuer": ISSUER,
        "issued_at": cert.issued_at.isoformat(),
        "verify_url": verify_url(cert.verify_code),
        "pdf_ready": pdf_ready,
        "pdf_url": f"/api/v1/learning/certificates/{cert.id}/pdf" if pdf_ready else None,
        "linkedin_url": linkedin_url(
            course_title=title, issued_at=cert.issued_at, code=cert.verify_code
        ),
    }


def verify_public(db: Session, raw_code: str) -> dict[str, Any]:
    """Public, unauthenticated. Unknown or malformed codes are a plain 404; revoked ones are
    ``valid: false`` with no personal data."""
    code = normalize_verify_code(raw_code)
    cert = db.query(Certificate).filter_by(verify_code=code).first() if code else None
    if cert is None:
        raise NotFound()
    if cert.revoked_at is not None:
        return {"valid": False}
    course = db.get(AcademyCourse, cert.course_id)
    return {
        "valid": True,
        "holder_name": cert.holder_name,
        "course_title": course.title if course is not None else cert.course_id,
        "issued_on": cert.issued_at.date().isoformat(),
        "issuer": ISSUER,
    }
