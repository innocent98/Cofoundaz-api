import io
import os
import time
from pathlib import Path
from typing import Protocol

import cloudinary
import cloudinary.exceptions
import cloudinary.uploader
import cloudinary.utils
import httpx

from app.core.config import settings


class Storage(Protocol):
    def save(self, key: str, content: bytes, content_type: str) -> str: ...
    def delete(self, key: str) -> None: ...

    # Private files (receipts): never addressable by a stable public URL. The caller keeps only
    # the key and asks for a short-lived link when an authorised user wants to read the file.
    def save_private(self, key: str, content: bytes, content_type: str) -> None:
        """Store `content` as an authenticated asset; no URL is ever returned."""

    def delete_private(self, key: str) -> None:
        """Remove a private asset (idempotent)."""

    def private_url(self, key: str, ttl_seconds: int) -> str | None:
        """A provider-signed, expiring URL, or None when we must serve (and sign) it ourselves."""

    def make_private(self, key: str) -> None:
        """Move a legacy publicly-reachable asset behind authentication (idempotent)."""

    def read_private(self, key: str) -> bytes | None:
        """The bytes of a private asset, or None when it is missing. For server-side processing
        only (stamping an executed PDF); readers get `private_url` or a signed local link."""


class LocalStorage:
    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = base_dir or settings.LOCAL_STORAGE_DIR

    def save(self, key: str, content: bytes, content_type: str) -> str:
        path = Path(self.base_dir) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)

    def delete(self, key: str) -> None:
        Path(self.base_dir).joinpath(key).unlink(missing_ok=True)

    # Local files are never mounted or served statically, so every local file is already private.
    def save_private(self, key: str, content: bytes, content_type: str) -> None:
        self.save(key, content, content_type)

    def delete_private(self, key: str) -> None:
        self.delete(key)

    def private_url(self, key: str, ttl_seconds: int) -> str | None:
        return None

    def make_private(self, key: str) -> None:
        return None

    def read_private(self, key: str) -> bytes | None:
        path = self.open_private(key)
        return path.read_bytes() if path is not None else None

    def open_private(self, key: str) -> Path | None:
        """The file for `key`, or None when it is missing or escapes the storage root."""
        root = os.path.realpath(self.base_dir)
        full = os.path.realpath(os.path.join(root, key))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return None
        return Path(full)


class CloudinaryStorage:
    """Stores every asset as Cloudinary `resource_type="raw"` (deterministic:
    the public_id is exactly our key, so delete is exact regardless of file
    type). Raw assets are served verbatim at their secure_url, which is all we
    need (no transforms in v1)."""

    def __init__(self) -> None:
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )

    def save(self, key: str, content: bytes, content_type: str) -> str:
        # Wrap in BytesIO — the SDK accepts a file-like stream across versions
        # (raw bytes support is version-dependent).
        result = cloudinary.uploader.upload(
            io.BytesIO(content), public_id=key, resource_type="raw", overwrite=True
        )
        return str(result["secure_url"])

    def delete(self, key: str) -> None:
        cloudinary.uploader.destroy(key, resource_type="raw")

    # Private assets use delivery type "authenticated": there is no public URL at all, and the
    # only way to read one is an API-signed download link that expires.
    def save_private(self, key: str, content: bytes, content_type: str) -> None:
        cloudinary.uploader.upload(
            io.BytesIO(content),
            public_id=key,
            resource_type="raw",
            type="authenticated",
            overwrite=True,
        )

    def delete_private(self, key: str) -> None:
        cloudinary.uploader.destroy(key, resource_type="raw", type="authenticated")

    def private_url(self, key: str, ttl_seconds: int) -> str | None:
        return str(
            cloudinary.utils.private_download_url(
                key,
                "",
                resource_type="raw",
                type="authenticated",
                expires_at=int(time.time()) + ttl_seconds,
            )
        )

    def read_private(self, key: str) -> bytes | None:
        url = self.private_url(key, 60)
        if url is None:
            return None
        resp = httpx.get(url, timeout=30.0, follow_redirects=False)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.content

    def make_private(self, key: str) -> None:
        # Legacy receipts were uploaded with the default delivery type ("upload"), which is
        # public to anyone holding the URL. Renaming to the same id with `to_type` moves the
        # asset behind authentication, so the old public URL stops working.
        try:
            cloudinary.uploader.rename(
                key,
                key,
                resource_type="raw",
                type="upload",
                to_type="authenticated",
                overwrite=True,
            )
        except cloudinary.exceptions.NotFound:
            # No `upload` asset under this id: an earlier attempt already moved it (the rename
            # succeeded but our commit did not). Treat it as already authenticated so the retry
            # succeeds instead of failing forever.
            return


def get_storage() -> Storage:
    if settings.STORAGE_BACKEND == "cloudinary":
        return CloudinaryStorage()
    return LocalStorage()
