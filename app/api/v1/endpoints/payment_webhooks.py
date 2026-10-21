from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.core.errors import AppError
from app.db.session import get_db
from app.services.finance.payments import webhooks

router = APIRouter()


def _too_large() -> AppError:
    return AppError("PAYLOAD_TOO_LARGE", "The request body is too large.", 413)


async def _read_capped(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > webhooks.MAX_BODY_BYTES:
        raise _too_large()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > webhooks.MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


# Public on purpose: no auth dependency. Authenticity comes from the provider signature, checked
# against the connection's own stored secret. Rate limiting still applies (default limiter).
@router.post("/payments/{provider}/{connection_public_id}", response_model=dict[str, Any])
async def receive_payment_webhook(
    provider: str,
    connection_public_id: str,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    raw_body = await _read_capped(request)  # raw bytes: the signature is over exactly these
    headers = dict(request.headers)

    def handle() -> dict[str, bool]:
        out = webhooks.process(
            db,
            provider=provider,
            public_id=connection_public_id,
            raw_body=raw_body,
            headers=headers,
        )
        db.commit()
        return out

    return success_response(await run_in_threadpool(handle))
