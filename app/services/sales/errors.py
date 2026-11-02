"""Shared sales validation helper.

Its own module (no dependency on other sales services) so every sales service can import it
without forming an import cycle.
"""

from app.core.errors import AppError


def _validation(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR", message, 422, field_errors=[{"field": field, "message": message}]
    )
