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


class SignerInput(BaseModel):
    email: str
    name: str | None = None


class SignatureRequestCreate(BaseModel):
    signers: list[SignerInput] = Field(default_factory=list)
    title: str | None = None
    expires_in_days: int | None = 14


class SignAction(BaseModel):
    typed_name: str
