import uuid

from pydantic import BaseModel, Field

from app.db.models.enums import DocumentKind, DocumentStatus, ShareAccess


class DocumentCreate(BaseModel):
    kind: DocumentKind | None = None
    title: str | None = None
    folder: str | None = None
    template_key: str | None = None
    sections: list[dict] | None = None
    tags: list[str] | None = None


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
    typed_name: str = Field(max_length=200)


class DocumentPatch(BaseModel):
    """Rename / move / tag / set status. Only the fields sent are changed; `folder: null` moves
    the document out of its folder."""

    title: str | None = None
    folder: str | None = None
    tags: list[str] | None = None
    status: DocumentStatus | None = None


class FilePatch(BaseModel):
    filename: str | None = None
    folder: str | None = None
    tags: list[str] | None = None


class FolderCreate(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None


class FolderRename(BaseModel):
    name: str


class GrantCreate(BaseModel):
    email: str | None = None
    user_id: uuid.UUID | None = None
    permission: ShareAccess = ShareAccess.view


class GrantUpdate(BaseModel):
    permission: ShareAccess


class DocumentCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000, pattern=r"\S")
