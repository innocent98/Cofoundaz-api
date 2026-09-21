import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.errors import JournalContentUnreadable, JournalNotConfigured
from app.services.journal.encryption import decrypt_content, encrypt_content

# The plan also lists three per-workspace tests: the same plaintext differing
# across workspaces, one workspace failing to read another's token, and
# KEY_VERSION == 1. They are deliberately absent here because they test the
# HKDF derivation in Task 1, which is gated on senior sign-off and not built.
# Add them when that gate clears.


def test_round_trip_returns_the_original_text():
    text = "shipped the thing today"
    token = encrypt_content(text)
    assert token != text
    assert decrypt_content(token) == text


def test_unicode_and_emoji_survive():
    # e-acute, coffee cup, CJK, party popper -- written as escapes so the
    # terminal encoding cannot mangle the source file.
    text = "caf\u00e9 \u2615 \u4eca\u65e5 \U0001f389"
    assert decrypt_content(encrypt_content(text)) == text


def test_empty_content_round_trips():
    assert decrypt_content(encrypt_content("")) == ""


def test_corrupt_token_raises_unreadable():
    with pytest.raises(JournalContentUnreadable):
        decrypt_content("not-a-fernet-token")


def test_token_from_a_different_key_is_unreadable(monkeypatch):
    token = encrypt_content("private words")
    monkeypatch.setattr(settings, "JOURNAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(JournalContentUnreadable):
        decrypt_content(token)


def test_missing_key_raises_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "JOURNAL_ENCRYPTION_KEY", None)
    with pytest.raises(JournalNotConfigured):
        encrypt_content("anything")


def test_malformed_key_raises_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "JOURNAL_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    with pytest.raises(JournalNotConfigured):
        encrypt_content("anything")


def test_non_string_input_is_rejected():
    with pytest.raises(TypeError):
        encrypt_content(123)
    with pytest.raises(TypeError):
        decrypt_content(123)
