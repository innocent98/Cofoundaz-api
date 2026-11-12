from app.core.errors import AppError


def validation(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR", message, 422, field_errors=[{"field": field, "message": message}]
    )


def not_found(message: str = "Not found.") -> AppError:
    return AppError("NOT_FOUND", message, 404)
