"""Every ReportLab PDF prints "₦" and Yoruba letters as written (analytics re-walk D1, 2026-11-18).

ReportLab's built-in Helvetica is WinAnsi only, so the naira sign printed as "?" ("?7,777.00"). Each test
renders a real file and reads its text back with pypdf, the way a reader's PDF viewer would.
"""

import io
from datetime import UTC, datetime
from types import SimpleNamespace

from pypdf import PdfReader

from app.core import pdf_fonts
from app.services.analytics import render as analytics_render
from app.services.billing.receipt import build_receipt
from app.services.documents import esign_pdf, export
from app.services.funding import watermark
from app.services.learning import certificates

YORUBA = "Adébáyọ̀ Ọlá"
WHEN = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def _text(pdf: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


def _fonts(pdf: bytes) -> set[str]:
    out = set()
    for page in PdfReader(io.BytesIO(pdf)).pages:
        for f in (page.get("/Resources") or {}).get("/Font", {}).values():
            out.add(str(f.get_object()["/BaseFont"]))
    return out


def test_the_bundled_fonts_cover_naira_and_yoruba_and_ship_with_their_licence():
    pdf_fonts.register()
    for name in (
        pdf_fonts.SANS,
        pdf_fonts.SANS_BOLD,
        pdf_fonts.SANS_OBLIQUE,
        pdf_fonts.SERIF_ITALIC,
    ):
        assert pdf_fonts.printable("₦ ọ ẹ ṣ Ọ", name) == "₦ ọ ẹ ṣ Ọ"
    assert (
        (pdf_fonts.FONT_DIR / "LICENSE-DejaVu.txt")
        .read_text()
        .startswith("Fonts are (c) Bitstream")
    )
    pdf_fonts.register()  # idempotent


def test_printable_folds_only_what_the_font_cannot_draw():
    assert pdf_fonts.printable("你 é") == "? é"
    assert pdf_fonts.printable("你", fallback="") == ""
    assert pdf_fonts.printable("line\nnext") == "line\nnext"


def _money_metric(value: int) -> dict:
    return {
        "block_id": "b1",
        "type": "metric",
        "hub": "finance",
        "title": None,
        "result": {
            "key": "finance.revenue",
            "label": "Revenue",
            "hub": "finance",
            "unit": "money",
            "currency": "NGN",
            "status": "ok",
            "value": value,
            "period": {"from": "2026-10-20", "to": "2026-11-18"},
            "compare": {"value": 100_000, "delta": value - 100_000, "delta_pct": 677.7},
            "series": [{"from": "2026-11-10", "to": "2026-11-16", "value": value}],
        },
    }


def test_analytics_report_pdf_prints_the_naira_sign():
    ctx = analytics_render.Context(
        "Ọlá Ventures", "2026-10-20", "2026-11-18", "2026-11-18 15:24 UTC", YORUBA
    )
    pdf = analytics_render.render_pdf(
        {"report": {"name": "Board pack"}, "results": [_money_metric(777_700)]}, ctx
    )
    text = _text(pdf)
    assert "₦7,777.00" in text  # headline value
    assert "?7,777" not in text and "?1,000" not in text
    assert "₦1,000.00" in text  # the comparison line
    assert YORUBA in text and "Ọlá Ventures" in text
    # ReportLab declares its canvas default (Helvetica) on every page; the text itself is set in DejaVu.
    assert {"/AAAAAA+DejaVuSans", "/AAAAAA+DejaVuSans-Bold"} <= _fonts(pdf)


def test_document_export_pdf_keeps_naira_and_yoruba():
    doc = SimpleNamespace(
        title=f"Offer for {YORUBA}",
        version=1,
        sections=[{"heading": "Pay", "body": "Salary ₦450,000 a month."}],
        kind=None,
        template_key=None,
    )
    text = _text(export.render_pdf(doc, None))
    assert "Salary ₦450,000 a month." in text and YORUBA in text


def test_esign_certificate_prints_signer_names_as_written():
    signer = esign_pdf.SignerStamp(
        name=YORUBA,
        email="ola@example.com",
        signed_name=YORUBA,
        signed_at=WHEN,
        ip="203.0.113.7",
        user_agent="Mozilla/5.0",
        viewed_at=WHEN,
    )
    cert = esign_pdf.Certificate(
        request_id="req-1",
        title="Loan of ₦2,000,000",
        filename="loan.pdf",
        sender="Ọlá Founder",
        sent_at=WHEN,
        completed_at=WHEN,
        document_hash="ab" * 32,
        signers=[signer],
    )
    text = _text(esign_pdf.render_certificate(cert))
    assert "Loan of ₦2,000,000" in text and text.count(YORUBA) >= 2 and "Ọlá Founder" in text


def test_academy_certificate_prints_the_holder_name_with_its_marks():
    pdf = certificates.render_pdf(
        holder=YORUBA, course="Pricing in ₦", issued_at=WHEN, code="ABCD-2345"
    )
    text = _text(pdf)
    assert YORUBA in text and "Pricing in ₦" in text


def test_billing_receipt_prints_a_yoruba_company_name_and_address():
    inv = SimpleNamespace(
        number="R-1",
        paid_at=WHEN,
        created_at=WHEN,
        description="Growth plan",
        status="paid",
        reference="ref_1",
        amount_minor=6_800_000,
        currency="NGN",
        refunded_minor=0,
    )
    details = SimpleNamespace(
        company_name="Ọlá Ventures", tax_id=None, address="12 Ọba Street\nIbadan"
    )
    text = _text(build_receipt(inv, details, None))
    assert "Ọlá Ventures" in text and "12 Ọba Street" in text and "NGN 68,000.00" in text


def test_data_room_watermark_keeps_a_unicode_email_readable():
    label = watermark._label("ọlá@example.com", WHEN)
    assert label.startswith("ọlá@example.com")
    assert "ọlá@example.com" in _text(watermark._overlay(595, 842, label))
