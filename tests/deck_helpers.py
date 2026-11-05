"""Real multi-page PDFs and a canned LLM for the deck review tests."""

import io
import json

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from app.db.models.document import DocumentFile
from app.db.models.enums import ScanStatus
from app.platform.storage import get_storage


def deck_pdf(slides: list[str], *, images: bool = True) -> bytes:
    buf = io.BytesIO()
    canvas = Canvas(buf)
    pic = Image.new("RGB", (40, 40), (20, 80, 200))
    for text in slides:
        y = 760
        for line in text.split("\n"):
            canvas.drawString(60, y, line)
            y -= 16
        if images:
            canvas.drawImage(ImageReader(pic), 60, 300, width=120, height=120)
        canvas.showPage()
    canvas.save()
    return buf.getvalue()


GOOD_SLIDES = [
    "Acme Pay\nSend money across Africa in seconds",
    "Problem\nSMEs lose 8% of every cross border payment to fees and delays",
    "Solution\nInstant settlement over local rails with one API",
    "Market\nTAM 12bn USD, SAM 2bn USD, SOM 150m USD (source: placeholder report)",
    "Traction\nMRR 1.6m NGN, 45 paying merchants, 12% month on month growth",
    "Business model\n1.2% per transaction, 82% gross margin",
    "Team\nTwo founders, ex payments engineers",
    "The ask\nRaising 90m NGN to hire 3 engineers and open Ghana",
]


def store_deck(db, startup, user, slides=None, *, name="deck.pdf", content=None, clean=True):
    """A clean PDF DocumentFile with real bytes in storage."""
    content = content if content is not None else deck_pdf(slides or GOOD_SLIDES)
    key = f"documents/{startup.id}/deck-{abs(hash((name, len(content)))) }.pdf"
    get_storage().save_private(key, content, "application/pdf")
    f = DocumentFile(
        startup_id=startup.id,
        uploaded_by_id=user.id,
        folder="Pitch Deck",
        filename=name,
        content_type="application/pdf",
        size_bytes=len(content),
        storage_key=key,
        is_private=True,
        scan_status=ScanStatus.clean if clean else ScanStatus.pending,
    )
    db.add(f)
    db.flush()
    return f, content


class CannedLLM:
    """Stands in for `get_llm_client()`: returns a fixed review and records the prompt."""

    last_usage_tokens = 4200

    def __init__(self, payload: dict | None = None, *, boom: Exception | None = None):
        self.payload = payload or {
            "narrative": 7,
            "data_clarity": 5,
            "top_fixes": [
                "Slide 4: cite the source for the market size.",
                "Slide 5: show cumulative revenue, not monthly.",
                "Slide 8: break the ask down by use of funds.",
                "Slide 2: quantify the problem for one customer.",
                "Slide 7: add advisors.",
                "A sixth fix that must be cut",
            ],
            "slides": [
                {"n": 1, "title": "Title", "notes": "Strong hook."},
                {"n": 4, "title": "Market", "notes": "SAM versus SOM is unclear."},
            ],
        }
        self.boom = boom
        self.calls: list[list] = []

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls.append(messages)
        if self.boom:
            raise self.boom
        return json.loads(json.dumps(self.payload))
