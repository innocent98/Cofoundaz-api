"""Gap 6 (and the BE half of gap 18): PATCH /onboarding/state refuses unsafe or empty input."""

import pytest

from tests.auth_helpers import active_user, bearer

URL = "/api/v1/onboarding/state"


def _patch(client, db, email, body):
    u = active_user(db, email)
    db.commit()
    return client.patch(URL, headers=bearer(u), json={"step": 1, **body})


def _fields(r):
    return [e["field"] for e in r.json()["error"]["field_errors"]]


@pytest.mark.parametrize(
    "website",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>x</script>",
        "ftp://acme.com",
        "mailto:a@b.com",
        "https://user:pw@acme.com",
        "https://nodot",
        "http://",
        "acme .com",
        "https://" + "a" * 260 + ".com",
    ],
)
def test_website_must_be_a_plain_http_or_https_address(client, db, website):
    r = _patch(client, db, "web@x.com", {"step": 2, "website": website})
    assert r.status_code == 422, r.text
    assert "website" in _fields(r)


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        ("https://acme.com", "https://acme.com"),
        ("http://acme.com/path?q=1", "http://acme.com/path?q=1"),
        ("acme.com", "https://acme.com"),
        ("acme.com:8080/x", "https://acme.com:8080/x"),
        ("  https://acme.com  ", "https://acme.com"),
        ("", None),
    ],
)
def test_valid_websites_are_kept_and_a_bare_domain_gets_https(client, db, sent, stored):
    r = _patch(client, db, "web-ok@x.com", {"step": 2, "website": sent})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["startup"]["website"] == stored


@pytest.mark.parametrize(
    "phone", ["abc", "123", "+", "12345678901234567890", "<script>", "+234 80-ab"]
)
def test_phone_must_look_like_a_phone_number(client, db, phone):
    r = _patch(client, db, "ph@x.com", {"phone": phone})
    assert r.status_code == 422 and "phone" in _fields(r)


@pytest.mark.parametrize("phone", ["+234 801 234 5678", "(555) 010-9999", "08012345678"])
def test_real_phone_numbers_pass(client, db, phone):
    assert _patch(client, db, "ph-ok@x.com", {"phone": phone}).status_code == 200


@pytest.mark.parametrize(
    "goals",
    [["<script>x</script>"], ["Get first customers", "Take over the world"], ["Raise funding"] * 2],
)
def test_goals_must_come_from_the_fixed_list_once_each(client, db, goals):
    r = _patch(client, db, "goals@x.com", {"step": 4, "goals": goals})
    assert r.status_code == 422 and "goals" in ".".join(_fields(r))


def test_all_eight_chips_are_accepted_individually(client, db):
    from app.schemas.onboarding import ONBOARDING_GOALS

    assert len(ONBOARDING_GOALS) == 8
    u = active_user(db, "chips@x.com")
    db.commit()
    for goal in ONBOARDING_GOALS:
        r = client.patch(URL, headers=bearer(u), json={"step": 4, "goals": [goal]})
        assert r.status_code == 200, (goal, r.text)


@pytest.mark.parametrize(
    "body",
    [
        {"full_name": ""},
        {"full_name": "   "},
        {"full_name": "x" * 121},
        {"full_name": "bad\x07name"},
        {"step": 2, "name": ""},
        {"step": 2, "name": "n" * 121},
        {"step": 3, "industry": ""},
        {"role_title": ""},
        {"role_title": "r" * 81},
        {"country": "Nigeria"},
        {"country": "1"},
        {"step": 2, "description": "d" * 1001},
        {"step": 4, "notes": "n" * 2001},
    ],
)
def test_empty_or_oversized_text_is_rejected_not_stored(client, db, body):
    r = _patch(client, db, "txt@x.com", body)
    assert r.status_code == 422, r.text


def test_text_is_trimmed_and_country_is_upper_cased(client, db):
    u = active_user(db, "clean@x.com")
    db.commit()
    r = client.patch(
        URL,
        headers=bearer(u),
        json={"step": 1, "full_name": "  Ada   Obi ", "country": "ng", "role_title": " CEO "},
    )
    assert r.status_code == 200, r.text
    fp = r.json()["data"]["founder_profile"]
    assert (fp["full_name"], fp["country"], fp["role_title"]) == ("Ada Obi", "NG", "CEO")
