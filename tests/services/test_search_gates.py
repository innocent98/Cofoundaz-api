"""Audit: every global-search source and page is gated like the module it opens (D-MIS-8)."""

from app.db.models.enums import MembershipRole
from app.services.search.service import PAGES, SOURCES

ALL = frozenset(MembershipRole)
CORE = frozenset({MembershipRole.founder, MembershipRole.team_member})
FINANCE = frozenset({*CORE, MembershipRole.accountant})

# Read gates, taken from the route dependencies of the module each result opens
# (roadmap/health/business: any member; documents: founder + team member, D-DOC-1; mission, assessment, validation, calendar,
# academy, AI chat, marketing: founder + team member; finance: + accountant; journal: founder).
SOURCE_GATES = {
    "milestone": ALL,
    "roadmap_task": ALL,
    "mission_task": CORE,
    "document": CORE,
    "conversation": CORE,
    "calendar_event": CORE,
    "assumption": CORE,
    "experiment": CORE,
    "survey": CORE,
    "transaction": FINANCE,
    "invoice": FINANCE,
    "campaign": CORE,
    "post": CORE,
    "keyword": CORE,
    "segment": CORE,
    "deal": CORE,
    "sales_account": CORE,
    "sales_contact": CORE,
    "sales_lead": CORE,
    "course": CORE,
    "article": CORE,
}
PAGE_GATES = {
    "/mission": CORE,
    "/assessment": CORE,
    "/validation": CORE,
    "/marketing": CORE,
    "/sales": CORE,
    "/finance": FINANCE,
    "/academy": CORE,
    "/calendar": CORE,
    "/ai": CORE,
    "/journal": frozenset({MembershipRole.founder}),
    "/dashboard": ALL,
    "/health": ALL,
    "/roadmap": ALL,
    "/business-builder": ALL,
    "/documents": CORE,
    "/notifications": ALL,
}


def test_every_search_source_is_gated_like_its_module():
    assert {s.type: s.roles for s in SOURCES} == SOURCE_GATES


def test_search_pages_are_gated_like_their_module():
    by_href = {p.href: p.roles for p in PAGES}
    for href, roles in PAGE_GATES.items():
        assert by_href[href] == roles, href


def test_sub_pages_never_open_wider_than_their_hub():
    for p in PAGES:
        hub = "/" + p.href.strip("/").split("/")[0]
        if hub in PAGE_GATES:
            assert p.roles <= PAGE_GATES[hub], p.href
