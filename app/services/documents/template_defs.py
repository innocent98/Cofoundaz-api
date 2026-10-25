import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.errors import NotFound
from app.db.models.enums import DocumentKind

# D-DOC-4: the legal drafts ship with this banner. The API returns it (so the FE can show it
# above the editor and the form) and the export prints it, so it travels with the document.
DISCLAIMER = (
    "This document is a draft template provided for convenience and is not legal advice. "
    "Cofoundaz is not a law firm. Have a qualified lawyer review it before you sign or rely on it."
)

CATEGORIES = ("Legal", "Financial", "Pitch", "Operational")

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class FieldDef:
    """An input the founder fills once and the template text reuses (`{{key}}` in a body).

    `source` is an auto-fill spec (see `generator.resolve_spec`); `default` is a fixed starting
    value the founder can overwrite.
    """

    key: str
    label: str
    type: str = "text"  # text | longtext | date
    required: bool = False
    source: str | None = None
    default: str | None = None


@dataclass(frozen=True)
class SectionDef:
    """One section. `body` is fixed text with `{{field}}` placeholders (legal drafts). `autofill`
    lists spec strings joined into the body when there is no fixed text. `ai` means an AI draft
    may fill the section when it is still empty."""

    key: str
    heading: str
    body: str = ""
    autofill: tuple[str, ...] = ()
    ai: bool = False


@dataclass(frozen=True)
class DocumentTemplate:
    key: str
    name: str
    description: str
    kind: DocumentKind
    category: str
    sections: tuple[SectionDef, ...]
    fields: tuple[FieldDef, ...] = ()
    disclaimer: str | None = None


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")


def _s(heading: str, body: str = "", *, fill: tuple[str, ...] = (), ai: bool = False) -> SectionDef:
    return SectionDef(key=_slug(heading), heading=heading, body=body, autofill=fill, ai=ai)


def _t(
    key: str,
    name: str,
    description: str,
    kind: DocumentKind,
    category: str,
    sections: tuple[SectionDef, ...],
    *,
    fields: tuple[FieldDef, ...] = (),
    disclaimer: str | None = None,
) -> DocumentTemplate:
    assert category in CATEGORIES  # nosec B101 - import-time invariant
    assert len({s.key for s in sections}) == len(sections)  # nosec B101
    return DocumentTemplate(key, name, description, kind, category, sections, fields, disclaimer)


_EFFECTIVE = FieldDef("effective_date", "Effective date", "date", source="today")
_LAW = FieldDef("governing_law", "Governing law", required=True)

_SIGNATURES_TWO = (
    "Signed for {{party_a}}:\n\nName: ______________________\nTitle: ______________________\n"
    "Signature: ___________________\nDate: ______________________\n\n"
    "Signed for {{party_b}}:\n\nName: ______________________\nTitle: ______________________\n"
    "Signature: ___________________\nDate: ______________________"
)


def _signatures(a: str, b: str) -> str:
    return _SIGNATURES_TWO.replace("party_a", a).replace("party_b", b)


_NDA = _t(
    "nda",
    "Non-Disclosure Agreement",
    "A one-way NDA for sharing confidential information with a partner, investor or contractor.",
    DocumentKind.nda,
    "Legal",
    (
        _s(
            "Parties",
            "This Non-Disclosure Agreement is made on {{effective_date}} between {{disclosing_party}} "
            '(the "Disclosing Party") and {{receiving_party}} (the "Receiving Party").',
        ),
        _s(
            "Purpose",
            "The Disclosing Party will share confidential information with the Receiving Party "
            "solely for the following purpose: {{purpose}}. The Receiving Party may not use the "
            "information for any other purpose.",
        ),
        _s(
            "Confidential Information",
            '"Confidential Information" means any non-public information the Disclosing Party '
            "shares in any form, including business plans, financials, customer and supplier "
            "details, product designs, source code and know-how, whether or not it is marked "
            "confidential.",
        ),
        _s(
            "Obligations of the Receiving Party",
            "The Receiving Party will: keep the Confidential Information secret using at least the "
            "care it uses for its own confidential material; share it only with people who need to "
            "know it for the purpose above and who are bound by equivalent duties; and not copy it "
            "except as the purpose requires.",
        ),
        _s(
            "Exclusions",
            "These duties do not apply to information that: is or becomes public through no fault "
            "of the Receiving Party; was already known to the Receiving Party without a duty of "
            "confidence; is received lawfully from a third party; or must be disclosed by law, in "
            "which case the Receiving Party will give the Disclosing Party prompt notice where "
            "allowed.",
        ),
        _s(
            "Term",
            "This agreement covers information shared from the effective date. The duties of "
            "confidentiality continue for {{term_years}} years after the effective date.",
        ),
        _s(
            "Return or Destruction",
            "On request, the Receiving Party will promptly return or destroy all Confidential "
            "Information and confirm in writing that it has done so.",
        ),
        _s(
            "Remedies",
            "The Receiving Party acknowledges that a breach may cause harm that money alone cannot "
            "repair, and that the Disclosing Party may seek an injunction in addition to any other "
            "remedy.",
        ),
        _s(
            "Governing Law",
            "This agreement is governed by the laws of {{governing_law}}, and its courts have "
            "jurisdiction over any dispute.",
        ),
        _s("Signatures", _signatures("disclosing_party", "receiving_party")),
    ),
    fields=(
        FieldDef("disclosing_party", "Disclosing party", required=True, source="profile.name"),
        FieldDef("receiving_party", "Receiving party", required=True),
        _EFFECTIVE,
        FieldDef("purpose", "Purpose of sharing", "longtext", required=True),
        FieldDef("term_years", "Confidentiality period (years)", default="2"),
        _LAW,
    ),
    disclaimer=DISCLAIMER,
)

_CONTRACTOR = _t(
    "contractor_agreement",
    "Contractor Agreement",
    "An independent contractor agreement for freelancers and short engagements.",
    DocumentKind.contractor_agreement,
    "Legal",
    (
        _s(
            "Parties",
            "This Contractor Agreement is made on {{effective_date}} between {{company_name}} "
            '(the "Company") and {{contractor_name}} (the "Contractor").',
        ),
        _s(
            "Services",
            "The Contractor will provide the following services to the Company: {{services}}. "
            "The Contractor decides how and when the work is done, within any deadlines both "
            "parties agree in writing.",
        ),
        _s(
            "Fees and Payment",
            "The Company will pay the Contractor {{fee}}. The Contractor will invoice the Company "
            "and the Company will pay {{payment_terms}}. The Contractor is responsible for their "
            "own taxes and expenses unless agreed otherwise in writing.",
        ),
        _s(
            "Independent Contractor",
            "The Contractor is an independent contractor, not an employee, partner or agent of the "
            "Company. The Contractor has no authority to bind the Company and is not entitled to "
            "employee benefits.",
        ),
        _s(
            "Intellectual Property",
            "All work product created for the Company under this agreement belongs to the Company "
            "once the Contractor is paid for it. The Contractor assigns to the Company all rights "
            "in that work product and will sign any document reasonably needed to confirm this. "
            "The Contractor keeps rights in tools and know-how they owned before this agreement.",
        ),
        _s(
            "Confidentiality",
            "The Contractor will keep the Company's non-public information confidential and use it "
            "only to do the services. This duty continues after the agreement ends.",
        ),
        _s(
            "Term and Termination",
            "This agreement starts on the effective date and continues until the services are "
            "complete. Either party may end it with 14 days' written notice. The Company will pay "
            "for services performed up to the end date.",
        ),
        _s(
            "Liability",
            "Neither party is liable for indirect or consequential losses. The Contractor's total "
            "liability under this agreement is limited to the fees paid in the 12 months before the "
            "claim, except for breach of confidentiality or fraud.",
        ),
        _s(
            "Governing Law",
            "This agreement is governed by the laws of {{governing_law}}, and its courts have "
            "jurisdiction over any dispute.",
        ),
        _s("Signatures", _signatures("company_name", "contractor_name")),
    ),
    fields=(
        FieldDef("company_name", "Company name", required=True, source="profile.name"),
        FieldDef("contractor_name", "Contractor name", required=True),
        _EFFECTIVE,
        FieldDef("services", "Services", "longtext", required=True),
        FieldDef("fee", "Fee (amount and currency)", required=True),
        FieldDef(
            "payment_terms", "Payment terms", default="within 30 days of receiving an invoice"
        ),
        _LAW,
    ),
    disclaimer=DISCLAIMER,
)

_FOUNDER = _t(
    "founder_agreement",
    "Founder Agreement",
    "Roles, equity, vesting and decision rules between two co-founders.",
    DocumentKind.founder_agreement,
    "Legal",
    (
        _s(
            "Parties",
            "This Founder Agreement is made on {{effective_date}} between {{founder_one}} and "
            '{{founder_two}} (the "Founders") about {{company_name}} (the "Company").',
        ),
        _s(
            "Roles and Commitment",
            "{{founder_one}} and {{founder_two}} will each work on the Company full time unless "
            "they agree otherwise in writing. Each Founder's role and responsibilities are set out "
            "in a schedule the Founders keep up to date together.",
        ),
        _s(
            "Equity",
            "The Founders will hold the Company's shares in the following split: {{equity_split}}. "
            "Any change to this split needs the written agreement of both Founders.",
        ),
        _s(
            "Vesting",
            "Each Founder's shares vest monthly over {{vesting_years}} years, with nothing vesting "
            "until a cliff of {{cliff_months}} months has passed. If a Founder leaves before "
            "their shares have vested, the Company may buy back the unvested shares at their "
            "original price.",
        ),
        _s(
            "Intellectual Property",
            "Each Founder assigns to the Company all intellectual property they create for the "
            "Company's business, now or in future, and will sign any document needed to confirm "
            "this.",
        ),
        _s(
            "Decision Making",
            "Day to day decisions in a Founder's own area are theirs to make. Decisions about "
            "raising money, issuing shares, selling the Company, taking on debt above an agreed "
            "limit, or changing this agreement need both Founders' consent.",
        ),
        _s(
            "Leaving the Company",
            "A Founder who wants to leave will give at least 60 days' written notice and help hand "
            "over their work. A Founder who is removed or leaves keeps only the shares that have "
            "vested by the date they leave.",
        ),
        _s(
            "Confidentiality",
            "Each Founder will keep the Company's non-public information confidential, during and "
            "after their time with the Company, and will not compete with the Company's business "
            "while they are a Founder.",
        ),
        _s(
            "Disputes",
            "The Founders will first try to settle any dispute by talking it through, and then by "
            "mediation, before starting court proceedings.",
        ),
        _s(
            "Governing Law",
            "This agreement is governed by the laws of {{governing_law}}.",
        ),
        _s("Signatures", _signatures("founder_one", "founder_two")),
    ),
    fields=(
        FieldDef("company_name", "Company name", required=True, source="profile.name"),
        FieldDef("founder_one", "First founder", required=True, source="user.full_name"),
        FieldDef("founder_two", "Second founder", required=True),
        _EFFECTIVE,
        FieldDef("equity_split", "Equity split (for example 50% / 50%)", required=True),
        FieldDef("vesting_years", "Vesting period (years)", default="4"),
        FieldDef("cliff_months", "Cliff (months)", default="12"),
        _LAW,
    ),
    disclaimer=DISCLAIMER,
)

_SAFE = _t(
    "safe",
    "SAFE (Simple Agreement for Future Equity)",
    "A plain-language SAFE-style agreement for an early investment.",
    DocumentKind.safe,
    "Legal",
    (
        _s(
            "Parties and Investment",
            'On {{effective_date}}, {{investor_name}} (the "Investor") pays {{purchase_amount}} '
            '(the "Purchase Amount") to {{company_name}} (the "Company"). In return the Company '
            "grants the Investor the right to receive shares of the Company's capital stock on the "
            "terms below.",
        ),
        _s(
            "Equity Financing",
            "If the Company sells shares at a fixed price in a financing round led by new "
            "investors, this SAFE converts into shares of the same type sold in that round. The "
            "number of shares is the Purchase Amount divided by the conversion price, which is the "
            "lower of the round price and the price implied by the valuation cap below, reduced "
            "by any discount.",
        ),
        _s(
            "Valuation Cap and Discount",
            "Valuation cap: {{valuation_cap}}. Discount: {{discount_rate}}. The cap and the "
            "discount are alternatives; the Investor receives whichever gives the lower "
            "conversion price.",
        ),
        _s(
            "Liquidity Event",
            "If the Company is sold or merged before this SAFE converts, the Investor can choose "
            "to receive either the Purchase Amount back or the shares it would have converted "
            "into, as determined by the valuation cap, whichever is greater.",
        ),
        _s(
            "Dissolution",
            "If the Company winds up before this SAFE converts, the Investor is paid the Purchase "
            "Amount out of the Company's remaining assets after creditors are paid and before "
            "holders of common stock.",
        ),
        _s(
            "Representations",
            "The Company confirms that it is validly organised, that it has the authority to enter "
            "this agreement, and that doing so does not break any other agreement it is party to. "
            "The Investor confirms that it is investing for its own account and understands that "
            "the investment is risky and may be lost in full.",
        ),
        _s(
            "No Shareholder Rights",
            "This SAFE is not a share. Until it converts, the Investor has no voting rights and no "
            "right to dividends or to information beyond what the law requires.",
        ),
        _s(
            "Termination",
            "This agreement ends when this SAFE converts to shares, when the Investor is paid "
            "under the Liquidity Event or Dissolution terms, or when both parties agree in "
            "writing.",
        ),
        _s(
            "Governing Law",
            "This agreement is governed by the laws of {{governing_law}}.",
        ),
        _s("Signatures", _signatures("company_name", "investor_name")),
    ),
    fields=(
        FieldDef("company_name", "Company name", required=True, source="profile.name"),
        FieldDef("investor_name", "Investor name", required=True),
        _EFFECTIVE,
        FieldDef("purchase_amount", "Purchase amount (with currency)", required=True),
        FieldDef("valuation_cap", "Valuation cap (with currency)", required=True),
        FieldDef("discount_rate", "Discount", default="none"),
        _LAW,
    ),
    disclaimer=DISCLAIMER,
)

_LEAN = "canvas.lean"

DOCUMENT_TEMPLATES: dict[str, DocumentTemplate] = {
    t.key: t
    for t in (
        _t(
            "business_plan",
            "Business Plan",
            "A full business plan.",
            DocumentKind.business_plan,
            "Pitch",
            (
                _s("Executive Summary", fill=("profile.description",), ai=True),
                _s("Problem", fill=(f"{_LEAN}.problem",), ai=True),
                _s("Solution", fill=(f"{_LEAN}.solution",), ai=True),
                _s(
                    "Market & Customer",
                    fill=(f"{_LEAN}.customer_segments", "record.persona"),
                    ai=True,
                ),
                _s(
                    "Business Model",
                    fill=(f"{_LEAN}.revenue_streams", "record.revenue_stream", "record.pricing"),
                    ai=True,
                ),
                _s("Go-to-Market", fill=(f"{_LEAN}.channels",), ai=True),
                _s("Team"),
                _s("Financials", fill=(f"{_LEAN}.cost_structure",), ai=True),
                _s("The Ask"),
            ),
        ),
        _t(
            "pitch_deck",
            "Pitch Deck",
            "An investor pitch narrative.",
            DocumentKind.pitch_deck,
            "Pitch",
            (
                _s("Hook", fill=("profile.description",), ai=True),
                _s("Problem", fill=(f"{_LEAN}.problem",), ai=True),
                _s("Solution", fill=(f"{_LEAN}.solution",), ai=True),
                _s("Why Now", ai=True),
                _s("Market", fill=(f"{_LEAN}.customer_segments", "record.persona"), ai=True),
                _s("Product", fill=(f"{_LEAN}.unique_value_proposition",), ai=True),
                _s("Team"),
                _s("The Ask"),
            ),
        ),
        _t(
            "financial_model",
            "Financial Model",
            "A lightweight financial narrative.",
            DocumentKind.financial_model,
            "Financial",
            (
                _s("Assumptions", ai=True),
                _s("Revenue", fill=("record.revenue_stream", f"{_LEAN}.revenue_streams"), ai=True),
                _s("Costs", fill=(f"{_LEAN}.cost_structure",), ai=True),
                _s("Runway"),
                _s("Projections"),
            ),
        ),
        _t(
            "meeting_notes",
            "Meeting Notes",
            "Structured meeting notes.",
            DocumentKind.meeting_notes,
            "Operational",
            (
                _s("Attendees"),
                _s("Agenda"),
                _s("Discussion"),
                _s("Decisions"),
                _s("Action Items"),
            ),
        ),
        _t(
            "one_pager",
            "One-Pager",
            "A concise one-page overview.",
            DocumentKind.one_pager,
            "Pitch",
            (
                _s("Overview", fill=("profile.description",), ai=True),
                _s("Problem", fill=(f"{_LEAN}.problem",), ai=True),
                _s("Solution", fill=(f"{_LEAN}.solution",), ai=True),
                _s("Traction"),
                _s("The Ask"),
            ),
        ),
        _NDA,
        _CONTRACTOR,
        _FOUNDER,
        _SAFE,
    )
}

_LEGAL_KINDS = {t.kind for t in DOCUMENT_TEMPLATES.values() if t.disclaimer}


def get_template(template_key: str) -> DocumentTemplate:
    tmpl = DOCUMENT_TEMPLATES.get(template_key)
    if tmpl is None:
        raise NotFound()
    return tmpl


def disclaimer_for(template_key: str | None, kind: DocumentKind | None = None) -> str | None:
    """The banner a document must carry: from its template, or from its (legal) kind."""
    tmpl = DOCUMENT_TEMPLATES.get(template_key or "")
    if tmpl is not None:
        return tmpl.disclaimer
    return DISCLAIMER if kind in _LEGAL_KINDS else None


def render_body(tmpl: DocumentTemplate, body: str, values: dict[str, str]) -> str:
    """Fill `{{field}}` placeholders in one pass (so a value that itself looks like a
    placeholder is inserted literally). A field with no value becomes a visible `[Label]`."""
    labels = {f.key: f.label[:1].upper() + f.label[1:] for f in tmpl.fields}

    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        return values.get(key) or f"[{labels.get(key, key)}]"

    return _PLACEHOLDER.sub(sub, body)


def instantiate(
    template_key: str, values: dict[str, str] | None = None
) -> tuple[DocumentKind, str, list[dict[str, Any]]]:
    """Blank sections for a template (fixed legal text rendered with `values`, bracketed where a
    value is missing). Auto-fill from the workspace is `generator.build_form`."""
    tmpl = get_template(template_key)
    vals = {f.key: f.default for f in tmpl.fields if f.default}
    vals.update(values or {})
    sections = [
        {"id": str(uuid.uuid4()), "heading": s.heading, "body": render_body(tmpl, s.body, vals)}
        for s in tmpl.sections
    ]
    return tmpl.kind, tmpl.name, sections


def _view(tmpl: DocumentTemplate) -> dict[str, Any]:
    return {
        "key": tmpl.key,
        "name": tmpl.name,
        "description": tmpl.description,
        "kind": tmpl.kind.value,
        "category": tmpl.category,
        "sections": [s.heading for s in tmpl.sections],
        "disclaimer": tmpl.disclaimer,
    }


def catalog() -> list[dict[str, Any]]:
    return [_view(t) for t in DOCUMENT_TEMPLATES.values()]


def template_view(template_key: str) -> dict[str, Any]:
    return _view(get_template(template_key))
