from pydantic import BaseModel, Field

from app.db.models.enums import DocumentKind, DocumentStatus


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
