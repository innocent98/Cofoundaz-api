import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from jose import JWTError, jwt
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.envelope import error_response
from app.core.errors import register_exception_handlers
from app.core.logger import log
from app.core.rate_limit import (
    install_included_router_support,
    verify_included_router_resolution,
)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time

        log.info(
            f"{request.method} {request.url.path} "
            f"completed in {process_time:.4f}s with status {response.status_code}"
        )

        return response


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
)

register_exception_handlers(app)


# Rate limiting
def _rate_limit_key(request: Request) -> str:
    """Key rate limits on the authenticated user when possible, falling back
    to remote address for unauthenticated requests. This keeps limits tied to
    the caller rather than the source IP, so users behind a shared IP (NAT,
    corporate proxy) aren't penalized by each other's traffic."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = jwt.decode(auth[7:], settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except JWTError:
            pass
    return get_remote_address(request)


limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_exceeded_handler(_: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=error_response("RATE_LIMITED", "Too many requests. Slow down a moment."),
        headers={"Retry-After": "60"},
    )


# SlowAPIMiddleware is what actually enforces `default_limits` (and any
# @limiter.limit(...) decorators) on every request; without it, app.state.limiter
# and the handler above are registered but never invoked.
#
# It also cannot see routes mounted via include_router on fastapi >= 0.137
# unless we teach it how. install_() patches slowapi's endpoint resolution;
# verify_() refuses to start the process if the patch is not working, so a
# future fastapi upgrade fails at boot instead of silently serving the whole
# /api/v1 surface unlimited. See app/core/rate_limit.py.
install_included_router_support()
verify_included_router_resolution()
app.add_middleware(SlowAPIMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging middleware
app.add_middleware(LoggingMiddleware)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} API",
        "version": settings.VERSION,
        "docs": f"{settings.API_V1_STR}/docs",
    }


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "healthy"}


def start() -> None:
    """Entry point for poetry script."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        # nosec B104 - binding all interfaces is REQUIRED inside a container:
        # the container network namespace is the isolation boundary, and
        # docker-compose.prod.yml publishes this only to 127.0.0.1 on the host,
        # with nginx in front. Binding 127.0.0.1 here would make the container
        # unreachable from outside its own namespace.
        host="0.0.0.0",  # nosec B104
        port=settings.SERVER_PORT,
        reload=True if settings.ENVIRONMENT == "development" else False,
    )


if __name__ == "__main__":
    start()
