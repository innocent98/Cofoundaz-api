import re
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.errors import NotFound
from app.db.models.learning import Certificate, CertificateFile
from app.services.learning import certificates as certs
from app.services.learning import service as svc
from tests.factories import create_user
from tests.learning_helpers import founder_and_startup

COURSE = "build-scope-the-mvp"
LESSONS = [f"{COURSE}-{n}" for n in (1, 2, 3)]


@pytest.fixture(autouse=True)
def _content(academy):
    return academy


def _issue(db, name="Ada Founder"):
    user, startup = founder_and_startup(db, name=name)
    cert = None
    for lesson in LESSONS:
        _e, got, _p = svc.update_lesson_progress(
            db, startup.id, user.id, lesson, completed=True, position_s=None
        )
        cert = got or cert
    assert cert is not None
    return user, startup, cert


def test_codes_are_short_unique_and_unambiguous():
    codes = {certs.new_verify_code() for _ in range(500)}
    assert len(codes) == 500
    assert all(
        re.fullmatch(r"CFZ-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}", c) for c in codes
    )
    assert not any(ch in "".join(codes) for ch in "01OI")


def test_normalize_accepts_only_credential_shaped_input():
    assert certs.normalize_verify_code("  cfz-abcd-ef23-gh45 ") == "CFZ-ABCD-EF23-GH45"
    for bad in (
        "",
        "CFZ-ABCD-EF2",
        "CFZ-ABCD-EF23-GH450",
        "CFZ-0BCD-EF23-GH45",
        "abc",
        "' OR 1=1 --",
        "../x",
    ):
        assert certs.normalize_verify_code(bad) is None


def test_issue_freezes_the_holder_name_and_falls_back_when_there_is_none(db):
    _u, _s, cert = _issue(db, name="Ada Founder")
    assert cert.holder_name == "Ada Founder"
    nameless = create_user(db)
    assert certs.holder_name_for(db, nameless.id) == "Cofoundaz member"


def test_issue_retries_a_code_clash(db, monkeypatch):
    _u, startup, first = _issue(db)
    other = create_user(db)
    other.profile.full_name = "Other Person"
    codes = iter([first.verify_code, first.verify_code, "CFZ-ZZZZ-2222-3333"])
    monkeypatch.setattr(certs, "new_verify_code", lambda: next(codes))
    cert = certs.issue_certificate(db, startup.id, other.id, "idea-shape-the-problem")
    assert cert.verify_code == "CFZ-ZZZZ-2222-3333"


def test_issue_gives_up_after_repeated_clashes(db, monkeypatch):
    _u, startup, first = _issue(db)
    other = create_user(db)
    monkeypatch.setattr(certs, "new_verify_code", lambda: first.verify_code)
    with pytest.raises(RuntimeError, match="unique credential ID"):
        certs.issue_certificate(db, startup.id, other.id, "idea-shape-the-problem")


def test_the_database_enforces_unique_codes(db):
    _u, _s, first = _issue(db)
    dup = Certificate(
        startup_id=first.startup_id,
        user_id=first.user_id,
        course_id="other-course",
        credential_code="x",
        verify_code=first.verify_code,
        holder_name="n",
        issued_at=datetime.now(UTC),
    )
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.flush()


def test_the_pdf_is_a_one_page_document_carrying_the_certificate(db):
    _u, _s, cert = _issue(db)
    assert certs.generate_pdf_for(db, cert) is True
    assert certs.generate_pdf_for(db, cert) is False  # idempotent
    pdf = db.get(CertificateFile, cert.id).content
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 1
    assert db.query(CertificateFile).count() == 1
    again = certs.render_pdf(
        holder=cert.holder_name,
        course="Scope the MVP",
        issued_at=cert.issued_at,
        code=cert.verify_code,
    )
    assert again == certs.render_pdf(
        holder=cert.holder_name,
        course="Scope the MVP",
        issued_at=cert.issued_at,
        code=cert.verify_code,
    )  # deterministic for the same input


def test_names_the_font_cannot_draw_are_folded_not_dropped_to_boxes():
    assert certs._printable("Adébáyọ̀ Ọlá") == "Adébáyo Olá"
    assert certs._printable("Chukwuemeka") == "Chukwuemeka"
    assert certs._printable("   ") == "Cofoundaz member"
    assert certs._printable("中文") == "Cofoundaz member"  # nothing drawable left


def test_a_200_character_name_is_truncated_with_an_ellipsis_inside_the_border():
    import io

    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(io.BytesIO(), pagesize=landscape(A4))
    limit = landscape(A4)[0] - 140
    text, size = certs._fit(c, "Wolfeschlegelstein " * 11, "Helvetica-Bold", 36, limit)
    assert size == 14 and text.endswith("...")
    assert c.stringWidth(text, "Helvetica-Bold", size) <= limit
    short, short_size = certs._fit(c, "Ada Founder", "Helvetica-Bold", 36, limit)
    assert (short, short_size) == ("Ada Founder", 36)
    pdf = certs.render_pdf(
        holder="W" * 200, course="C" * 200, issued_at=datetime.now(UTC), code="CFZ-ABCD-EF23-GH45"
    )
    assert pdf.startswith(b"%PDF")


def test_issuing_twice_returns_the_existing_certificate_not_an_error(db):
    user, startup, cert = _issue(db)
    again = certs.issue_certificate(db, startup.id, user.id, cert.course_id)
    assert again.id == cert.id


def test_printed_text_is_capped_before_any_measuring():
    assert len(certs._printable("W" * 100_000)) == 120


def test_a_long_name_is_shrunk_to_fit():
    pdf = certs.render_pdf(
        holder="A" * 140,
        course="B" * 140,
        issued_at=datetime.now(UTC),
        code="CFZ-ABCD-EF23-GH45",
    )
    assert pdf.startswith(b"%PDF")


def test_linkedin_url_carries_every_field_and_uses_the_org_name_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ACADEMY_LINKEDIN_ORG_ID", "")
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.cofoundaz.com/")
    issued = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    url = certs.linkedin_url(
        course_title="Price & test: it", issued_at=issued, code="CFZ-ABCD-EF23-GH45"
    )
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "www.linkedin.com",
        "/profile/add",
    )
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert q == {
        "startTask": "CERTIFICATION_NAME",
        "name": "Price & test: it",
        "organizationName": "Cofoundaz",
        "issueYear": "2026",
        "issueMonth": "10",
        "certId": "CFZ-ABCD-EF23-GH45",
        "certUrl": "https://app.cofoundaz.com/verify/CFZ-ABCD-EF23-GH45",
    }


def test_linkedin_url_switches_to_the_org_id_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "ACADEMY_LINKEDIN_ORG_ID", "123456")
    q = parse_qs(
        urlparse(certs.linkedin_url(course_title="T", issued_at=datetime.now(UTC), code="C")).query
    )
    assert q["organizationId"] == ["123456"] and "organizationName" not in q


def test_only_the_owner_gets_the_pdf(db):
    owner, startup, cert = _issue(db)
    certs.generate_pdf_for(db, cert)
    _c, pdf = certs.owned_pdf(db, startup.id, owner.id, str(cert.id))
    assert pdf.startswith(b"%PDF")
    stranger = create_user(db)
    _o2, other_startup = founder_and_startup(db)
    for who, where in (
        (stranger.id, startup.id),
        (owner.id, other_startup.id),
        (stranger.id, other_startup.id),
    ):
        with pytest.raises(NotFound):
            certs.owned_pdf(db, where, who, str(cert.id))
    for bad in ("not-a-uuid", str(uuid.uuid4())):
        with pytest.raises(NotFound):
            certs.owned_pdf(db, startup.id, owner.id, bad)


def test_pdf_before_it_is_generated_is_a_409_not_a_404(db):
    owner, startup, cert = _issue(db)
    with pytest.raises(certs.CertificateNotReady) as exc:
        certs.owned_pdf(db, startup.id, owner.id, str(cert.id))
    assert exc.value.http_status == 409 and exc.value.code == "CERTIFICATE_NOT_READY"


def test_a_revoked_certificate_is_gone_for_the_owner_and_invalid_publicly(db):
    owner, startup, cert = _issue(db)
    certs.generate_pdf_for(db, cert)
    cert.revoked_at = datetime.now(UTC)
    db.flush()
    with pytest.raises(NotFound):
        certs.owned_pdf(db, startup.id, owner.id, str(cert.id))
    assert certs.list_certificates(db, startup.id, owner.id) == []
    assert certs.verify_public(db, cert.verify_code) == {"valid": False}


def test_public_verify_returns_the_minimum_and_nothing_else(db):
    _u, _s, cert = _issue(db, name="Ada Founder")
    out = certs.verify_public(db, cert.verify_code.lower())
    assert out == {
        "valid": True,
        "holder_name": "Ada Founder",
        "course_title": "Scope the MVP",
        "issued_on": cert.issued_at.date().isoformat(),
        "issuer": "Cofoundaz",
    }
    for leak in (str(cert.id), str(cert.user_id), str(cert.startup_id), cert.credential_code):
        assert leak not in str(out)


@pytest.mark.parametrize(
    "code", ["CFZ-ABCD-EF23-GH45", "nonsense", "", "CFZ-ABCD-EF23-GH4%", str(uuid.uuid4())]
)
def test_public_verify_404s_unknown_and_malformed_codes(db, code):
    with pytest.raises(NotFound):
        certs.verify_public(db, code)


def test_serialize_reflects_pdf_readiness(db):
    _u, startup, cert = _issue(db)
    before = svc.certificate_views(db, cert.startup_id, cert.user_id)[0]
    assert before["pdf_ready"] is False and before["pdf_url"] is None
    certs.generate_pdf_for(db, cert)
    after = svc.certificate_views(db, cert.startup_id, cert.user_id)[0]
    assert after["pdf_url"] == f"/api/v1/learning/certificates/{cert.id}/pdf"
    assert after["credential_id"] == cert.verify_code and after["issuer"] == "Cofoundaz"
    assert after["course_title"] == "Scope the MVP" and "credential_code" not in after
    assert svc.certificate_views(db, startup.id, create_user(db).id) == []


def test_linkedin_org_id_defaults_to_the_cofoundaz_company_page():
    # Public id of linkedin.com/company/146743971, so certificates link the page in every
    # environment without an env change (an env value still overrides it).
    assert type(settings).model_fields["ACADEMY_LINKEDIN_ORG_ID"].default == "146743971"
