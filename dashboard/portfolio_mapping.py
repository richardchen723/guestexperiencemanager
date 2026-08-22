"""Canonical portfolio mapping shared by dashboard operations."""

from __future__ import annotations

TAG_PORTFOLIOS = (
    {
        "name": "Enchanted Havens",
        "aliases": ("enchanted havens",),
    },
    {
        "name": "Luminary Resorts",
        "aliases": ("luminary resorts", "luminary resort"),
    },
    {
        "name": "Smoky Cabins",
        "aliases": ("smoky cabins",),
    },
    {
        "name": "Urban Stays",
        "aliases": ("pt300", "urban stays", "urbans stays"),
    },
    {
        "name": "Crockett’s Run",
        "aliases": (
            "crockett's run",
            "crockett’s run",
            "crocketts run",
        ),
    },
    {
        "name": "Middlefork",
        "aliases": ("middlefork", "middlefork ridge"),
    },
    {
        "name": "crestwood",
        "aliases": ("crestwood",),
    },
    {
        "name": "LA St Gabe",
        "aliases": ("la st gabe", "st gabe", "st gabe units", "san gabriel units"),
    },
)

TAG_PORTFOLIO_NAMES = tuple(spec["name"] for spec in TAG_PORTFOLIOS)
EXPLICIT_LISTING_PORTFOLIOS = {
    558675: "crestwood",
    558676: "crestwood",
    558677: "crestwood",
    558678: "crestwood",
}


def normalize_tag_name(name: str | None) -> str:
    """Normalize Hostaway listing tags for portfolio matching."""
    return " ".join((name or "").strip().lower().split())


def portfolio_name_for_tags(tag_names: tuple[str, ...] | list[str]) -> str | None:
    """Return the canonical portfolio for a collection of listing tags."""
    normalized = {normalize_tag_name(tag_name) for tag_name in tag_names}
    for spec in TAG_PORTFOLIOS:
        if normalized.intersection(spec["aliases"]):
            return spec["name"]
    return None


def portfolio_name_for_listing(
    listing_id: int,
    tag_names: tuple[str, ...] | list[str],
) -> str | None:
    """Return the canonical portfolio from explicit listing rules or tags."""
    return EXPLICIT_LISTING_PORTFOLIOS.get(int(listing_id)) or portfolio_name_for_tags(tag_names)
