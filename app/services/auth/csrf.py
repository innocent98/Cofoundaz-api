"""CSRF defence for requests whose only credential is the HttpOnly refresh cookie.

The browser attaches the cookie on its own, so a request carrying only the cookie is "ambient":
a hostile page could make the browser send it. Two endpoints take that kind of request,
`POST /auth/refresh` and `POST /auth/logout` (everything else needs the bearer access token,
which a hostile page cannot read). For those, when the refresh token arrives ONLY in the cookie
(none in the body), we require all of:

1. `X-Auth-Mode: cookie` on the request. A custom header cannot be sent by a plain HTML form,
   and a cross-origin `fetch` that sets it triggers a CORS preflight that only the origins in
   `BACKEND_CORS_ORIGINS` pass.
2. An `Origin` header, when the browser sent one, that is one of those origins. (Browsers send
   Origin on every cross-origin request and on same-origin POSTs; a request without one is not
   a browser acting on a victim's cookie.)
3. The cookie itself is `SameSite=Lax` by default (REFRESH_COOKIE_SAMESITE), so a cross-site
   sub-request does not carry it in the first place; this is defence in depth.

A refresh token supplied in the request body is not ambient (an attacker cannot know it), so the
legacy header-auth client that still keeps its token in localStorage passes through unchanged.
"""

from fastapi import Request

from app.core.config import settings
from app.core.errors import CsrfRejected

AUTH_MODE_HEADER = "X-Auth-Mode"
COOKIE_MODE = "cookie"


def is_cookie_mode(request: Request) -> bool:
    """The caller says it keeps the refresh token only in the cookie (so the body omits it)."""
    return request.headers.get(AUTH_MODE_HEADER, "").strip().lower() == COOKIE_MODE


def require_cookie_csrf_guard(request: Request, *, body_token: str | None) -> None:
    """Raise CSRF_REJECTED for a cookie-only request that fails the checks above."""
    if body_token:
        return
    if not request.cookies.get(settings.REFRESH_COOKIE_NAME):
        return  # no ambient credential, nothing to forge
    if not is_cookie_mode(request):
        raise CsrfRejected()
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.BACKEND_CORS_ORIGINS:
        raise CsrfRejected()
