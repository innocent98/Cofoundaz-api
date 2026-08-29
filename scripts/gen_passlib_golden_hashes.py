"""Generate golden bcrypt hashes using the PRE-MIGRATION passlib implementation.

This script produced ``tests/fixtures/passlib_golden_hashes.json``. It is kept in
the tree as provenance for that fixture: it documents exactly how every hash in
it was made, so the fixture is re-derivable rather than a set of magic strings.

It CANNOT be run against the current dependency set -- passlib was removed in the
same change that added the fixture (see docs/sop/). To re-run it, install
``passlib[bcrypt]==1.7.4`` and ``bcrypt==4.3.0`` in a throwaway virtualenv:

    python -m venv /tmp/passlib-golden
    /tmp/passlib-golden/bin/pip install 'passlib[bcrypt]==1.7.4' 'bcrypt==4.3.0'
    /tmp/passlib-golden/bin/python scripts/gen_passlib_golden_hashes.py \
        > tests/fixtures/passlib_golden_hashes.json

The hashes are salted, so a re-run produces different (equally valid) digests.
Regenerating is only useful to widen coverage, never to "fix" a failing test:
a golden-hash test that starts failing means the verification path changed
behaviour, which is exactly the regression the fixture exists to catch.
"""

import json
from datetime import UTC, datetime
from importlib.metadata import version as metadata_version

from passlib.context import CryptContext
from passlib.hash import bcrypt as passlib_bcrypt

# EXACTLY the context the production code used before the migration
# (app/core/security.py at commit 203813d).
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# (label, password) -- ASCII, unicode, boundary and over-limit cases.
PASSWORDS = [
    ("ascii_simple", "password1"),
    ("ascii_typical", "Str0ng!Passw0rd#2024"),
    ("ascii_71_bytes", "a" * 71),
    ("ascii_72_bytes", "a" * 72),
    ("ascii_73_bytes", "a" * 73),
    ("ascii_100_bytes", "a" * 100),
    ("ascii_200_bytes", "b" * 200),
    ("unicode_cjk_short", "密码密码"),
    # 30 CJK chars = 90 UTF-8 bytes: looks short, crosses the byte limit.
    ("unicode_cjk_90_bytes", "密" * 30),
    # 24 emoji = 96 UTF-8 bytes (4 bytes each); a 72-byte cut lands exactly on
    # an emoji boundary (72 / 4 = 18).
    ("unicode_emoji_96_bytes", "\U0001f510" * 24),
    # Leading ASCII char shifts the 72-byte cut INTO a codepoint rather than
    # onto its boundary -- the case that breaks naive str-slicing truncation.
    ("unicode_emoji_split_codepoint", "x" + "\U0001f510" * 25),
    ("unicode_accented", "p\xe4sswort-\xfcber-caf\xe9"),
    ("unicode_mixed_74_bytes", "caf\xe9" + "z" * 69),
    ("whitespace_and_symbols", "  spaced \t pass \n word  "),
]


def main() -> None:
    records = []

    for label, pw in PASSWORDS:
        entry: dict = {
            "label": label,
            "password": pw,
            "password_utf8_bytes": len(pw.encode("utf-8")),
            "password_chars": len(pw),
            "hashes": {},
        }
        # 1. The production CryptContext default -- what real user rows contain.
        entry["hashes"]["crypt_context_default"] = pwd_context.hash(pw)
        # 2. Explicit cost factors, spanning cheap legacy rows to expensive ones.
        for rounds in (4, 8, 10, 12, 13):
            entry["hashes"][f"2b_rounds_{rounds}"] = passlib_bcrypt.using(
                rounds=rounds, ident="2b"
            ).hash(pw)
        # 3. Legacy ident variants other tooling may have written into the column.
        for ident in ("2a", "2y", "2b"):
            entry["hashes"][f"ident_{ident}"] = passlib_bcrypt.using(rounds=10, ident=ident).hash(
                pw
            )
        records.append(entry)

    # Self-check: passlib must verify everything it just produced, and reject a
    # wrong password against it. A fixture that does not satisfy this is junk.
    #
    # The wrong password must differ within the FIRST 72 BYTES. Appending a
    # suffix is not a valid negative control: for any password already at or
    # over the limit, bcrypt truncates the suffix away and the "wrong" password
    # hashes identically. Prefixing guarantees a difference inside the window.
    for entry in records:
        wrong = "Z" + entry["password"]
        for name, digest in entry["hashes"].items():
            assert pwd_context.verify(entry["password"], digest), f"{entry['label']}/{name}"
            assert not pwd_context.verify(wrong, digest), f"{entry['label']}/{name}"

    doc = {
        "_provenance": {
            "generated_at": datetime.now(UTC).isoformat(),
            "generated_by": "scripts/gen_passlib_golden_hashes.py",
            # Read from installed metadata rather than a `__version__`
            # attribute: bcrypt 5 is typed and does not export one.
            "passlib_version": metadata_version("passlib"),
            "bcrypt_version": metadata_version("bcrypt"),
            "production_context": 'CryptContext(schemes=["bcrypt"], deprecated="auto")',
            "note": (
                "Hashes produced by the PRE-MIGRATION passlib implementation. "
                "The post-migration implementation must verify every one of "
                "these unchanged, or existing users are locked out."
            ),
        },
        "cases": records,
    }

    print(json.dumps(doc, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
