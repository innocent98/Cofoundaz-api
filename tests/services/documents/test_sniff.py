import io
import zipfile

import pytest

from app.services.documents import sniff
from app.services.documents.sniff import declared_type, sniff_content_type


def _zip(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(n, "x")
    return buf.getvalue()


PNG = b"\x89PNG\r\n\x1a\n" + b"rest"
JPG = b"\xff\xd8\xff\xe0rest"


@pytest.mark.parametrize(
    ("content", "declared"),
    [
        (b"%PDF-1.7 rest", sniff.PDF),
        (PNG, sniff.PNG),
        (JPG, sniff.JPEG),
        (_zip("[Content_Types].xml", "word/document.xml"), sniff.DOCX),
        (_zip("[Content_Types].xml", "xl/workbook.xml"), sniff.XLSX),
        (_zip("[Content_Types].xml", "ppt/presentation.xml"), sniff.PPTX),
        ("héllo\n".encode(), sniff.TEXT),
        (b"a,b\n1,2\n", sniff.CSV),
        (b"# Title\n\ntext", sniff.MARKDOWN),
    ],
)
def test_matching_bytes_are_accepted_and_canonical(content, declared):
    assert sniff_content_type(content, declared) == declared


@pytest.mark.parametrize(
    ("content", "declared"),
    [
        (b"MZ", sniff.PDF),  # the gap-list case: an executable posing as a PDF
        (b"MZ\x90\x00", sniff.TEXT),  # NUL byte: not text
        (b"\xff\xfe\x00bad", sniff.CSV),
        (b"%PDF-1.7", sniff.PNG),
        (PNG, sniff.JPEG),
        (JPG, sniff.PDF),
        (b"", sniff.PDF),  # empty
        (b"", sniff.TEXT),
        (b"PK\x03\x04not really a zip", sniff.DOCX),
        (_zip("[Content_Types].xml", "word/document.xml"), sniff.XLSX),  # docx posing as xlsx
        (_zip("random.txt"), sniff.DOCX),  # a plain zip is not OOXML
        (_zip("word/document.xml"), sniff.DOCX),  # no [Content_Types].xml
        (b"\xc3\x28", sniff.MARKDOWN),  # invalid UTF-8
        (b"anything", "application/x-msdownload"),
    ],
)
def test_mismatched_bytes_are_refused(content, declared):
    assert sniff_content_type(content, declared) is None


@pytest.mark.parametrize(
    ("ct", "name", "want"),
    [
        ("application/pdf", "a.pdf", sniff.PDF),
        ("Application/PDF; charset=x", "a.pdf", sniff.PDF),
        ("text/markdown", "a.md", sniff.MARKDOWN),
        ("text/x-markdown", "a.md", sniff.MARKDOWN),
        ("application/octet-stream", "notes.MD", sniff.MARKDOWN),
        ("application/octet-stream", "notes.markdown", sniff.MARKDOWN),
        ("application/octet-stream", "tool.exe", None),
        ("application/x-msdownload", "a.exe", None),
        (None, "a.pdf", None),
    ],
)
def test_declared_type_normalisation(ct, name, want):
    assert declared_type(ct, name) == want
