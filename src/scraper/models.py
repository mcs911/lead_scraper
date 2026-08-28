"""Data models for scraped entities."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Store:
    """A single merchant listed in the arukereso.hu store directory.

    ``store_id`` is the stable slug taken from the store's profile URL. It is
    the field to deduplicate and join on; every other field is best-effort and
    may be ``None`` when the listing does not expose it.
    """

    store_id: str
    name: str
    profile_url: str
    website_url: str | None = None
    rating: float | None = None
    review_count: int | None = None
    offer_count: int | None = None
    logo_url: str | None = None
    city: str | None = None

    # Company details, available only from the profile page. Populated when
    # ``ScrapeConfig.enrich_details`` is set (CLI: ``--details``); ``None`` on
    # a listing-only run.
    legal_name: str | None = None
    """Registered company behind the storefront, e.g. "Alfa Kereskedelmi Kft."
    — routinely different from ``name``."""

    tax_number: str | None = None
    """Hungarian adószám in canonical ``12345678-1-42`` form, or the 8-digit
    core when only the EU VAT number (``HU12345678``) is published."""

    phone: str | None = None
    """Phone number normalised to E.164, e.g. ``+3612345678``."""

    # Email is deliberately absent: Árukereső hides merchant email behind an
    # interaction, so this scraper does not collect it. See
    # src/scraper/contact.py and test_no_email_is_ever_collected.

    scraped_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        """Return a flat dict suitable for CSV/JSON export."""
        return asdict(self)

    @property
    def is_complete(self) -> bool:
        """True when the core lead fields are populated."""
        return bool(self.store_id and self.name and self.profile_url)


# Column order used by the CSV exporter. Declared here so the model stays the
# single source of truth for the output schema.
STORE_FIELDS: tuple[str, ...] = (
    "store_id",
    "name",
    "profile_url",
    "website_url",
    "rating",
    "review_count",
    "offer_count",
    "logo_url",
    "city",
    "legal_name",
    "tax_number",
    "phone",
    "scraped_at",
)
