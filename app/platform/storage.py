import io
from pathlib import Path
from typing import Protocol

import cloudinary
import cloudinary.uploader

from app.core.config import settings


class Storage(Protocol):
    def save(self, key: str, content: bytes, content_type: str) -> str: ...
    def delete(self, key: str) -> None: ...


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


def get_storage() -> Storage:
    if settings.STORAGE_BACKEND == "cloudinary":
        return CloudinaryStorage()
    return LocalStorage()
