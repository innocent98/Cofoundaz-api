"""Dependency-free .xlsx writer (the same trade-off as invoice_pdf.py: no new package, no new
supply-chain surface in the image).

An .xlsx is a zip of XML parts. This writes the smallest valid workbook: sheets of rows whose
cells are text or numbers, a bold header row and sensible column widths. Text is always written
as an inline string and never as a formula, so a cell that starts with `=`, `+`, `-` or `@` (a
vendor or description a user controls) is displayed, not evaluated.
"""

import io
import re
import zipfile
from collections.abc import Sequence
from decimal import Decimal
from html import escape

Cell = str | int | float | Decimal | None
Sheet = tuple[str, Sequence[Sequence[Cell]]]

_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
# XML 1.0 forbids most control characters; one of them in a cell would corrupt the whole file.
_BAD_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_MAX_CELL_CHARS = 32767

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    "{sheets}</Types>"
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/></Relationships>'
)
# cellXfs: 0 = default, 1 = bold (header), 2 = number with thousands separator and 2 decimals.
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    "</cellXfs></styleSheet>"
)


def _col(n: int) -> str:
    name = ""
    n += 1
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def sheet_name(raw: str, taken: set[str]) -> str:
    """Excel limits: 1-31 characters, none of []:*?/\\, unique case-insensitively."""
    base = _BAD_SHEET_CHARS.sub(" ", raw).strip().strip("'")[:31] or "Sheet"
    name, i = base, 2
    while name.casefold() in taken:
        suffix = f" ({i})"
        name, i = base[: 31 - len(suffix)] + suffix, i + 1
    taken.add(name.casefold())
    return name


def _cell(ref: str, value: Cell, *, header: bool) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):  # pragma: no cover - callers never pass bools
        value = int(value)
    if isinstance(value, (int, float, Decimal)):
        return f'<c r="{ref}" s="2"><v>{value}</v></c>'
    text = _BAD_XML_CHARS.sub("", value)[:_MAX_CELL_CHARS]
    style = ' s="1"' if header else ""
    return (
        f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{escape(text)}</t></is></c>'
    )


def _sheet_xml(rows: Sequence[Sequence[Cell]]) -> str:
    widths: dict[int, int] = {}
    body: list[str] = []
    for r, row in enumerate(rows):
        cells = []
        for c, value in enumerate(row):
            cells.append(_cell(f"{_col(c)}{r + 1}", value, header=r == 0))
            if value is not None:
                widths[c] = max(widths.get(c, 0), min(len(str(value)), 60))
        body.append(f'<row r="{r + 1}">{"".join(cells)}</row>')
    cols = "".join(
        f'<col min="{c + 1}" max="{c + 1}" width="{max(10, w + 2)}" customWidth="1"/>'
        for c, w in sorted(widths.items())
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{f'<cols>{cols}</cols>' if cols else ''}<sheetData>{''.join(body)}</sheetData></worksheet>"
    )


def build_xlsx(sheets: Sequence[Sheet]) -> bytes:
    """Workbook bytes for `sheets` = [(name, rows)]. Row 0 of each sheet is its bold header."""
    if not sheets:
        sheets = [("Sheet", [])]
    taken: set[str] = set()
    names = [sheet_name(n, taken) for n, _ in sheets]
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        + "".join(
            f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, n in enumerate(names, start=1)
        )
        + "</sheets></workbook>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/'
            f'2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES.format(sheets=overrides))
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/styles.xml", _STYLES)
        for i, (_, rows) in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))
    return buf.getvalue()
