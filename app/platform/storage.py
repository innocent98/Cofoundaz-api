from pathlib import Path
from typing import Protocol

from app.core.config import settings


class Storage(Protocol):
    def save(self, key: str, content: bytes, content_type: str) -> str: ...


class LocalStorage:
    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = base_dir or settings.LOCAL_STORAGE_DIR

    def save(self, key: str, content: bytes, content_type: str) -> str:
        path = Path(self.base_dir) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)


def get_storage() -> Storage:
    return LocalStorage()
