import re

from app.core.errors import PasswordTooLong, WeakPassword
from app.core.security import BCRYPT_MAX_PASSWORD_BYTES


def validate_password_strength(password: str) -> None:
    """Gate for paths that SET a password (signup, reset). Never used on login.

    The upper bound is measured in UTF-8 bytes, not characters, because that is
    what bcrypt counts: "密" * 30 is 30 characters but 90 bytes. A character
    bound would let that password through to be silently truncated, which is the
    exact behaviour this check exists to stop.

    Applying this to login instead would lock out every existing user whose
    password is already over the limit -- they must keep being able to sign in.
    """
    if len(password) < 8 or not re.search(r"\d", password):
        raise WeakPassword()
    if len(password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise PasswordTooLong()
