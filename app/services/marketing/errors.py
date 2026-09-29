"""Shared marketing validation helper.

Lives in its own module (no dependency on the marketing services) so both `service.py` and
`analytics.py` can import it without forming an import cycle. `analytics.py` is imported by
`service.overview()`, so if `analytics.py` also imported from `service.py` the two modules would
depend on each other cyclically (flagged by CodeQL). Keeping `_validation` here breaks that.
"""

from app.core.errors import AppError


def _validation(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR", message, 422, field_errors=[{"field": field, "message": message}]
    )
