"""Unicode fonts for every ReportLab PDF the API renders.

ReportLab's built-in Type 1 fonts (Helvetica, Times, Courier) are WinAnsi (cp1252) only. Anything
outside that, notably the naira sign "₦" (U+20A6) and the dotted Yoruba vowels (ẹ, ọ, ṣ), printed as "?"
or was folded to a bare letter. DejaVu (Bitstream Vera licence, `app/assets/fonts/LICENSE-DejaVu.txt`)
covers both; ReportLab embeds only the glyphs a document uses, so files stay small.

Call `register()` before building a document; it is idempotent and cheap after the first call.
"""

import unicodedata
from pathlib import Path

from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

SANS = "DejaVuSans"
SANS_BOLD = "DejaVuSans-Bold"
SANS_OBLIQUE = "DejaVuSans-Oblique"
SERIF_ITALIC = "DejaVuSerif-Italic"

_FILES = {
    SANS: "DejaVuSans.ttf",
    SANS_BOLD: "DejaVuSans-Bold.ttf",
    SANS_OBLIQUE: "DejaVuSans-Oblique.ttf",
    SERIF_ITALIC: "DejaVuSerif-Italic.ttf",
}


def register() -> None:
    registered = set(pdfmetrics.getRegisteredFontNames())
    for name, file in _FILES.items():
        if name not in registered:
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
    # <b> and <i> inside Paragraph markup resolve through the family mapping.
    addMapping(SANS, 0, 0, SANS)
    addMapping(SANS, 1, 0, SANS_BOLD)
    addMapping(SANS, 0, 1, SANS_OBLIQUE)
    addMapping(SANS, 1, 1, SANS_BOLD)


_COVERAGE: dict[str, frozenset[int]] = {}


def _covered(font: str) -> frozenset[int]:
    if font not in _COVERAGE:
        register()
        _COVERAGE[font] = frozenset(pdfmetrics.getFont(font).face.charToGlyph)
    return _COVERAGE[font]


def printable(text: str, font: str = SANS, fallback: str = "?") -> str:
    """Keep every character `font` can draw (₦, ẹ, ọ, ṣ, curly quotes...).

    A character outside the font (an emoji, CJK) is reduced to its base letter when it has one, else "?",
    rather than printed as an empty box. `fallback` is what stands in for a character with no base letter.
    """
    have = _covered(font)
    out = []
    for ch in text:
        if ord(ch) in have or ch in "\n\t":
            out.append(ch)
            continue
        base = "".join(
            c
            for c in unicodedata.normalize("NFKD", ch)
            if ord(c) in have and not unicodedata.combining(c)
        )
        out.append(base or fallback)
    return "".join(out)
