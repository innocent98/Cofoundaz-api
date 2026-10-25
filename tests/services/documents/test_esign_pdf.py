import io
from datetime import UTC, datetime

import pytest
from pypdf import PdfReader, PdfWriter

from app.core.errors import AppError
from app.services.documents import esign_pdf
from app.services.documents.esign_pdf import Certificate, SignerStamp
from tests.pdf_helpers import make_pdf

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _cert(n=1, **over):
    signers = [
        SignerStamp(
            name=f"Signer {i}",
            email=f"s{i}@example.com",
            signed_name=f"Typed {i}",
            signed_at=NOW,
            ip="203.0.113.7",
            user_agent="Mozilla/5.0 <b>x</b> & “fancy” 中",
            viewed_at=None,
        )
        for i in range(n)
    ]
    base = {
        "request_id": "r-1",
        "title": "NDA <b>",
        "filename": "nda.pdf",
        "sender": "Ada & Co",
        "sent_at": NOW,
        "completed_at": NOW,
        "document_hash": "ab" * 32,
        "signers": signers,
    }
    return Certificate(**{**base, **over})


def test_validate_counts_pages_and_rejects_garbage():
    assert esign_pdf.validate_pdf(make_pdf()) == 1
    for bad in (b"", b"not a pdf", b"%PDF-1.4 body"):
        with pytest.raises(AppError) as e:
            esign_pdf.validate_pdf(bad)
        assert e.value.code == "PDF_UNREADABLE" and e.value.http_status == 422


def test_encrypted_pdfs_are_refused():
    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(make_pdf())))
    w.encrypt("pw")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(AppError) as e:
        esign_pdf.validate_pdf(buf.getvalue())
    assert e.value.code == "PDF_UNREADABLE"


def test_too_many_pages_are_refused(monkeypatch):
    monkeypatch.setattr(esign_pdf, "MAX_PAGES", 0)
    with pytest.raises(AppError):
        esign_pdf.validate_pdf(make_pdf())


def test_build_appends_the_certificate_and_keeps_the_source_untouched():
    src = make_pdf("Original page")
    out = esign_pdf.build_executed_pdf(src, _cert(2))
    reader = PdfReader(io.BytesIO(out))
    assert len(reader.pages) == 2
    assert reader.pages[0].extract_text().strip() == "Original page"
    text = reader.pages[1].extract_text()
    for needle in [
        "Typed 0",
        "Typed 1",
        "s0@example.com",
        "203.0.113.7",
        "ab" * 32,
        "simple electronic",
    ]:
        assert needle in text


def test_many_signers_flow_onto_extra_certificate_pages():
    out = esign_pdf.build_executed_pdf(make_pdf(), _cert(14))
    assert len(PdfReader(io.BytesIO(out)).pages) > 2


def test_hostile_text_is_rendered_as_text_not_markup():
    out = esign_pdf.build_executed_pdf(make_pdf(), _cert(1, title="</para><b>x"))
    assert "Typed 0" in PdfReader(io.BytesIO(out)).pages[-1].extract_text()


def test_build_rejects_an_unreadable_source():
    with pytest.raises(AppError):
        esign_pdf.build_executed_pdf(b"%PDF-1.4 junk", _cert())
