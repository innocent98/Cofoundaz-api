"""Password hashing: passlib-compatibility and the bcrypt 72-byte boundary.

The load-bearing test in this file is `test_verifies_every_passlib_golden_hash`.
Hashing and verifying within one implementation proves nothing about a migration
-- any self-consistent implementation passes that. These hashes were produced by
the REAL pre-migration passlib 1.7.4 + bcrypt 4.3.0 (see
scripts/gen_passlib_golden_hashes.py) and committed as a fixture, so this suite
fails if the new implementation stops verifying what production already stores.

A failure here means every existing user is locked out. It is never correct to
regenerate the fixture to make it pass.
"""

import json
from pathlib import Path

import bcrypt
import pytest

from app.core.errors import PasswordTooLong, WeakPassword
from app.core.security import (
    BCRYPT_MAX_PASSWORD_BYTES,
    get_password_hash,
    verify_password,
)
from app.services.auth.password import validate_password_strength

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "passlib_golden_hashes.json"


def _load_golden() -> dict:
    with _FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


GOLDEN = _load_golden()

# (case_label, hash_label, password, digest) for every hash in the fixture.
GOLDEN_HASHES = [
    (case["label"], hash_label, case["password"], digest)
    for case in GOLDEN["cases"]
    for hash_label, digest in case["hashes"].items()
]


def test_fixture_provenance_is_the_pre_migration_stack():
    """Guards against the fixture being silently regenerated post-migration."""
    prov = GOLDEN["_provenance"]
    assert prov["passlib_version"] == "1.7.4"
    assert prov["bcrypt_version"].startswith("4.")
    assert prov["production_context"] == 'CryptContext(schemes=["bcrypt"], deprecated="auto")'
    assert len(GOLDEN_HASHES) == 126


# ---------------------------------------------------------------------------
# Cross-implementation compatibility: the reason this file exists.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case_label", "hash_label", "password", "digest"),
    GOLDEN_HASHES,
    ids=[f"{c}-{h}" for c, h, _, _ in GOLDEN_HASHES],
)
def test_verifies_every_passlib_golden_hash(case_label, hash_label, password, digest):
    """Every hash passlib ever wrote must still verify. No user gets locked out."""
    assert verify_password(password, digest) is True


@pytest.mark.parametrize(
    ("case_label", "hash_label", "password", "digest"),
    GOLDEN_HASHES,
    ids=[f"{c}-{h}" for c, h, _, _ in GOLDEN_HASHES],
)
def test_rejects_wrong_password_against_passlib_golden_hash(
    case_label, hash_label, password, digest
):
    """The negative control: compatibility must not mean "verifies anything".

    The wrong password differs in its FIRST byte. A trailing difference is not a
    valid control -- for any password at or over 72 bytes, truncation discards
    the suffix and the "wrong" password is genuinely the same secret.
    """
    assert verify_password("Z" + password, digest) is False


def test_golden_fixture_covers_every_bcrypt_ident():
    """$2a$/$2y$ rows can exist from other tooling; all three must be covered."""
    idents = {digest[:4] for _, _, _, digest in GOLDEN_HASHES}
    assert {"$2a$", "$2b$", "$2y$"} <= idents


def test_golden_fixture_covers_a_spread_of_cost_factors():
    costs = {digest[4:6] for _, _, _, digest in GOLDEN_HASHES}
    assert {"04", "08", "10", "12", "13"} <= costs


# ---------------------------------------------------------------------------
# The 72-byte limit.
# ---------------------------------------------------------------------------


def test_new_hashes_use_the_preserved_cost_factor_and_ident():
    """Cost 12 / $2b$ is what passlib wrote; changing it silently would be wrong."""
    assert get_password_hash("password1").startswith("$2b$12$")


def test_hash_and_verify_roundtrip():
    digest = get_password_hash("password1")
    assert verify_password("password1", digest) is True
    assert verify_password("password2", digest) is False


@pytest.mark.parametrize(
    "password",
    [
        "a" * 73,
        "a" * 200,
        "\U0001f510" * 24,  # 96 bytes, cut lands on a codepoint boundary
        "x" + "\U0001f510" * 25,  # 101 bytes, cut lands MID-codepoint
        "密" * 30,  # 30 chars but 90 bytes
    ],
    ids=["ascii-73", "ascii-200", "emoji-96", "emoji-split-codepoint", "cjk-90-bytes"],
)
def test_over_limit_passwords_do_not_raise(password):
    """bcrypt 5 raises over 72 bytes; we truncate first, so these must not."""
    digest = get_password_hash(password)
    assert verify_password(password, digest) is True


def test_truncation_is_on_bytes_not_characters():
    """The trap: "密" * 30 is 30 chars but 90 bytes.

    A character-based `password[:72]` leaves 90 bytes and bcrypt 5 raises. This
    asserts the multi-byte password is genuinely over the byte limit while under
    the character limit, then that it hashes anyway.
    """
    password = "密" * 30
    assert len(password) < BCRYPT_MAX_PASSWORD_BYTES
    assert len(password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES
    assert verify_password(password, get_password_hash(password)) is True


def test_passwords_sharing_a_72_byte_prefix_are_equivalent():
    """Documents the truncation semantics we inherited, rather than hiding them.

    This is bcrypt's behaviour and passlib's before it. It is exactly why
    validate_password_strength now rejects over-length passwords at signup.
    """
    digest = get_password_hash("a" * 72 + "SUFFIX_ONE")
    assert verify_password("a" * 72 + "SUFFIX_TWO", digest) is True
    assert verify_password("a" * 71 + "b", digest) is False


def test_exactly_72_bytes_is_not_truncated():
    """Boundary: 72 must be inclusive, 73 is where truncation starts."""
    digest = get_password_hash("a" * 72)
    assert verify_password("a" * 72, digest) is True
    assert verify_password("a" * 71, digest) is False


def test_our_hash_of_a_long_password_matches_bcrypt_on_the_first_72_bytes():
    """Proves the truncation point is exactly 72 bytes, against raw bcrypt."""
    password = "a" * 100
    digest = get_password_hash(password)
    salt = digest[:29].encode("ascii")
    expected = bcrypt.hashpw(password.encode("utf-8")[:72], salt).decode("ascii")
    assert digest == expected


# ---------------------------------------------------------------------------
# Malformed stored hashes must not 500 the login path.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_hash",
    ["", "not-a-hash", "$2b$12$too-short", "$9z$12$" + "a" * 53, "plaintext-password1"],
)
def test_malformed_stored_hash_returns_false_instead_of_raising(bad_hash):
    assert verify_password("password1", bad_hash) is False


# ---------------------------------------------------------------------------
# The boundary validator (set-password paths only).
# ---------------------------------------------------------------------------


def test_validator_accepts_a_password_at_exactly_the_limit():
    validate_password_strength("a" * 71 + "1")  # 72 bytes


@pytest.mark.parametrize(
    "password",
    ["a" * 72 + "1", "1" + "密" * 30, "1" + "\U0001f510" * 24],
    ids=["ascii-73", "cjk-91-bytes", "emoji-97-bytes"],
)
def test_validator_rejects_over_limit_passwords(password):
    with pytest.raises(PasswordTooLong):
        validate_password_strength(password)


def test_validator_still_rejects_weak_passwords_first():
    """Order matters: a short password reports WEAK_PASSWORD, not the new code."""
    with pytest.raises(WeakPassword):
        validate_password_strength("short1")


def test_password_too_long_uses_a_422_and_a_stable_code():
    exc = PasswordTooLong()
    assert exc.code == "PASSWORD_TOO_LONG"
    assert exc.http_status == 422
