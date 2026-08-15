import re

from app.core.errors import WeakPassword


def validate_password_strength(password: str) -> None:
    if len(password) < 8 or not re.search(r"\d", password):
        raise WeakPassword()
