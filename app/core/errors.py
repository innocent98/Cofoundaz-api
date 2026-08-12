from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.envelope import error_response


class AppError(Exception):
    code = "APP_ERROR"
    message = "Something went wrong."
    http_status = 400

    def __init__(self, code=None, message=None, http_status=None, field_errors=None):
        self.code = code or self.code
        self.message = message or self.message
        self.http_status = http_status or self.http_status
        self.field_errors = field_errors or []
        super().__init__(self.message)


class EmailTaken(AppError):  # noqa: N818
    code, http_status = "EMAIL_TAKEN", 409
    message = "That email already has an account — log in instead?"


class WeakPassword(AppError):  # noqa: N818
    code, http_status = "WEAK_PASSWORD", 422
    message = "Add a number and make it at least 8 characters."


class InvalidCredentials(AppError):  # noqa: N818
    code, http_status = "INVALID_CREDENTIALS", 401
    message = "That email and password don't match."


class AccountLocked(AppError):  # noqa: N818
    code, http_status = "ACCOUNT_LOCKED", 429
    message = "Too many attempts. Try again in a few minutes or reset your password."


class TokenInvalid(AppError):  # noqa: N818
    code, http_status = "TOKEN_INVALID", 400
    message = "That link is invalid or has expired."


class MfaInvalidCode(AppError):  # noqa: N818
    code, http_status = "MFA_INVALID_CODE", 401
    message = "That code isn't right. Try again."


class FeatureNotEnabled(AppError):  # noqa: N818
    code, http_status = "FEATURE_NOT_ENABLED", 501
    message = "This feature isn't available yet."


class Forbidden(AppError):  # noqa: N818
    code, http_status = "FORBIDDEN", 403
    message = "You don't have permission to do that."


class NotFound(AppError):  # noqa: N818
    code, http_status = "NOT_FOUND", 404
    message = "Not found."


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.http_status,
            content=error_response(exc.code, exc.message, exc.field_errors),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        field_errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_response(
                "VALIDATION_ERROR", "Please check the highlighted fields.", field_errors
            ),
        )
