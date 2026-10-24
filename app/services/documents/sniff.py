"""Content sniffing for Documents uploads (gap-list row 5).

The client's `Content-Type` is a claim, not evidence: a 2-byte `MZ` executable sent as
`application/pdf` used to be stored and could then be mailed to signers. `sniff_content_type`
decides what the BYTES are and the upload is accepted only when that matches what the client
declared. The stored `content_type` is the canonical sniffed type, never the raw header.

Recognised families: PDF, OOXML (docx / xlsx / pptx, told apart by the zip's directory names),
PNG, JPEG, and text (plain / CSV / Markdown). Text has no magic number, so it must be NUL-free
UTF-8 and the declared type (or the `.md` extension, since browsers often send Markdown as
`application/octet-stream`) picks which text type it is.
"""

import io
import zipfile

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PNG = "image/png"
JPEG = "image/jpeg"
TEXT = "text/plain"
CSV = "text/csv"
MARKDOWN = "text/markdown"

_OOXML_DIR = {"word/": DOCX, "xl/": XLSX, "ppt/": PPTX}
_TEXT_TYPES = {TEXT, CSV, MARKDOWN}
# Browsers and OSes disagree on the Markdown MIME type; all of these mean `text/markdown`.
_MARKDOWN_ALIASES = {"text/markdown", "text/x-markdown", "text/x-web-markdown"}
_MARKDOWN_EXTS = (".md", ".markdown")


def declared_type(content_type: str | None, filename: str) -> str | None:
    """The upload's declared type, normalised; `None` when it is not an allowed one."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _MARKDOWN_ALIASES:
        return MARKDOWN
    if ct == "application/octet-stream" and filename.lower().endswith(_MARKDOWN_EXTS):
        return MARKDOWN
    return ct if ct in ALLOWED_TYPES else None


ALLOWED_TYPES = frozenset({PDF, DOCX, XLSX, PPTX, PNG, JPEG, TEXT, CSV, MARKDOWN})


def _ooxml_type(content: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()  # central directory only: nothing is decompressed
    except zipfile.BadZipFile:
        return None
    if "[Content_Types].xml" not in names:
        return None
    for prefix, ctype in _OOXML_DIR.items():
        if any(n.startswith(prefix) for n in names):
            return ctype
    return None


def _is_text(content: bytes) -> bool:
    if b"\x00" in content:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def sniff_content_type(content: bytes, declared: str) -> str | None:
    """The canonical content type of `content` if it matches `declared`, else `None`."""
    if not content:
        return None
    if declared == PDF:
        return PDF if content.startswith(b"%PDF-") else None
    if declared == PNG:
        return PNG if content.startswith(b"\x89PNG\r\n\x1a\n") else None
    if declared == JPEG:
        return JPEG if content.startswith(b"\xff\xd8\xff") else None
    if declared in (DOCX, XLSX, PPTX):
        return (
            declared
            if content.startswith(b"PK\x03\x04") and _ooxml_type(content) == declared
            else None
        )
    if declared in _TEXT_TYPES:
        return declared if _is_text(content) else None
    return None
