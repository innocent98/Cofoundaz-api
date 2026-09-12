from pydantic import BaseModel, Field

from app.db.models.enums import DocumentKind, DocumentStatus, ShareAccess


class DocumentCreate(BaseModel):
    kind: DocumentKind | None = None
    title: str | None = None
    folder: str | None = None
    template_key: str | None = None
    sections: list[dict] | None = None


class DocumentSave(BaseModel):
    title: str = ""
    sections: list[dict] = Field(default_factory=list)
    status: DocumentStatus = DocumentStatus.draft
    folder: str | None = None
    version: int


class ShareCreate(BaseModel):
    email: str
    access_level: ShareAccess = ShareAccess.view
    expires_in_days: int | None = 30
