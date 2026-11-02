import uuid

from app.core.config import settings
from app.services.documents import links


def test_derived_tokens_are_stable_unique_and_versioned():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert links.derive_token("sign", a) == links.derive_token("sign", a)
    assert links.derive_token("sign", a) != links.derive_token("sign", b)
    assert links.derive_token("sign", a) != links.derive_token("share", a)
    assert links.derive_token("sign", a, 0) != links.derive_token("sign", a, 1)
    assert len(links.derive_token("sign", a)) == 43 and links.plausible(
        links.derive_token("sign", a)
    )


def test_a_different_secret_gives_a_different_token(monkeypatch):
    a = uuid.uuid4()
    before = links.derive_token("sign", a)
    monkeypatch.setattr(settings, "SECRET_KEY", "another-secret")
    assert links.derive_token("sign", a) != before


def test_matches_is_exact_and_plausible_bounds_the_input():
    raw = links.derive_token("sign", uuid.uuid4())
    assert links.matches(links.token_hash(raw), raw)
    assert not links.matches(links.token_hash(raw), raw + "x")
    assert not links.plausible("") and not links.plausible("x" * 200) and not links.plausible("é")
