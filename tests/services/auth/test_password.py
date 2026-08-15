import pytest

from app.core.errors import WeakPassword
from app.services.auth.password import validate_password_strength


def test_accepts_strong():
    validate_password_strength("password1")  # no raise


@pytest.mark.parametrize("bad", ["short1", "nodigitspassword", "1234567"])
def test_rejects_weak(bad):
    with pytest.raises(WeakPassword):
        validate_password_strength(bad)
