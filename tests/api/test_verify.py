import pytest

from app.core.rate_limit import limiter
from app.db.models.enums import MembershipRole
from app.main import app
from tests.api.test_learning import BASE, _complete, _member

pytestmark = pytest.mark.usefixtures("academy")


def _earn(client, h):
    for n in (1, 2, 3):
        out = _complete(client, h, f"build-scope-the-mvp-{n}").json()["data"]
    return out["certificate"]


def test_a_real_credential_verifies_with_no_login_and_the_minimum_fields(client, db):
    u, _s, h = _member(db)
    u.profile.full_name = "Ada Founder"
    db.flush()
    cert = _earn(client, h)
    r = client.get(f"/api/v1/verify/{cert['credential_id']}")  # no Authorization header
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {
        "valid": True,
        "holder_name": "Ada Founder",
        "course_title": "Scope the MVP",
        "issued_on": cert["issued_at"][:10],
        "issuer": "Cofoundaz",
    }
    body = r.text
    for leak in (cert["id"], str(u.id), u.email, "startup", "credential_code"):
        assert leak not in body


def test_codes_are_case_insensitive_unknown_ones_404(client, db):
    _u, _s, h = _member(db)
    cert = _earn(client, h)
    assert client.get(f"/api/v1/verify/{cert['credential_id'].lower()}").status_code == 200
    for code in ("CFZ-ABCD-EF23", "nope", "CFZ-ABCD", cert["id"], "%27%20OR%201%3D1"):
        r = client.get(f"/api/v1/verify/{code}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", code


def test_a_revoked_credential_is_invalid_and_discloses_nothing(client, db):
    from datetime import UTC, datetime

    from app.db.models.learning import Certificate

    _u, _s, h = _member(db)
    cert = _earn(client, h)
    db.query(Certificate).update({"revoked_at": datetime.now(UTC)})
    db.flush()
    r = client.get(f"/api/v1/verify/{cert['credential_id']}")
    assert r.status_code == 200 and r.json()["data"] == {"valid": False}


def test_the_public_route_is_rate_limited(client, db):
    """30 per minute per caller; the 31st is a 429. Unlike the other tests, the limiter is on."""
    app.state.limiter.enabled = True
    limiter.reset()
    try:
        codes = [client.get("/api/v1/verify/CFZ-ABCD-EF23").status_code for _ in range(31)]
    finally:
        app.state.limiter.enabled = False
        limiter.reset()
    assert codes[:30] == [404] * 30
    assert codes[30] == 429


def test_every_learning_route_still_requires_login(client):
    assert client.get(f"{BASE}/certificates").status_code == 401
    assert (
        client.get(f"{BASE}/certificates/00000000-0000-0000-0000-000000000000/pdf").status_code
        == 401
    )


def test_other_roles_cannot_use_the_pdf_route_but_can_verify_a_public_code(client, db):
    _u, startup, h = _member(db)
    cert = _earn(client, h)
    mentor = _member(db, role=MembershipRole.mentor, startup=startup)[2]
    assert client.get(f"{BASE}/certificates/{cert['id']}/pdf", headers=mentor).status_code == 403
    # verification is public by design: it does not depend on who asks
    assert client.get(f"/api/v1/verify/{cert['credential_id']}", headers=mentor).status_code == 200
