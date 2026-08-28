"""Scraper configuration and the DOM selector layer.

Every CSS selector the scraper depends on lives in this module, so adapting to
a markup change means editing one file and nothing else.

IMPORTANT — selectors are unverified
------------------------------------
The selector candidates below were NOT confirmed against the live
arukereso.hu markup: the environment this scraper was written in has
www.arukereso.hu blocked at the network egress proxy, so the real DOM could
not be inspected. They are ordered guesses covering common directory-listing
patterns, backed by a heuristic fallback in ``parsing.py`` that finds store
links by URL shape rather than by class name.

Before trusting the output, run the discovery mode from a machine that can
reach the site and lock in the real selectors:

    python -m src.cli discover --out dom-report.txt

That prints the repeated card structure and the candidate attributes it found,
which you can paste straight into ``SELECTORS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BASE_URL = "https://www.arukereso.hu"
STORES_PATH = "/stores/"

#: Path fragment that marks a URL as a store profile. Used by the heuristic
#: fallback to recognise store links regardless of their CSS classes.
STORE_URL_MARKERS: tuple[str, ...] = ("/stores/", "/shop/", "/aruhaz/", "/bolt/")

#: Pagination query template appended to the directory URL. ``{page}`` is the
#: 1-based index. Verify the real parameter name with the discovery mode
#: before a full run.
PAGE_QUERY_TEMPLATE = "?page={page}"

#: Candidate selectors per field, tried in order until one matches.
#: The first list ("card") selects the repeated container for a single store;
#: every other list is evaluated *within* a matched card.
SELECTORS: dict[str, list[str]] = {
    "card": [
        "[data-store-id]",
        ".store-list__item",
        ".shop-list__item",
        "li.store-item",
        "div.store-item",
        "article.store",
        ".store-card",
        ".shop-card",
    ],
    "name": [
        "[itemprop='name']",
        ".store-name",
        ".shop-name",
        "h2 a",
        "h3 a",
        "h2",
        "h3",
        "a[title]",
    ],
    "link": [
        "a[href]",
    ],
    "rating": [
        "[itemprop='ratingValue']",
        ".rating-value",
        ".store-rating",
        ".rating__value",
        "[data-rating]",
    ],
    "review_count": [
        "[itemprop='reviewCount']",
        "[itemprop='ratingCount']",
        ".review-count",
        ".rating-count",
        "[data-review-count]",
    ],
    "offer_count": [
        ".offer-count",
        ".product-count",
        "[data-offer-count]",
    ],
    "logo": [
        "img[src]",
    ],
    "city": [
        "[itemprop='addressLocality']",
        ".store-city",
        ".city",
    ],
    "website": [
        "a[rel~='nofollow'][href^='http']",
        ".store-website a[href]",
        "a.website[href]",
    ],
    # Used only by the "next page" detection in the pagination loop.
    "next_page": [
        "a[rel='next']",
        ".pagination__next",
        ".pager-next a",
        "a.next",
    ],
}

#: Desktop Chrome user agents rotated across browser contexts.
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
)

#: Chromium binary shipped with this sandbox image. ``None`` lets Playwright
#: resolve its own managed download instead.
DEFAULT_EXECUTABLE_PATH: str | None = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
)


@dataclass(slots=True)
class ScrapeConfig:
    """Runtime knobs for a scrape run.

    Defaults are deliberately conservative: two concurrent pages and a
    randomised 1.5-3.5s gap between navigations keeps the crawl to roughly one
    request per second, which a directory of this size absorbs without strain.
    """

    # Scope
    max_pages: int | None = None
    """Stop after this many listing pages. ``None`` means follow pagination to
    the end."""

    enrich_details: bool = False
    """Visit each store's profile page for fields absent from the listing."""

    # Politeness
    concurrency: int = 2
    min_delay: float = 1.5
    max_delay: float = 3.5
    max_retries: int = 3
    respect_robots: bool = True
    """Abort when robots.txt disallows the target path. Override only for a
    site you have permission to crawl."""

    # Browser
    headless: bool = True
    timeout_ms: int = 30_000
    locale: str = "hu-HU"
    timezone_id: str = "Europe/Budapest"
    viewport: tuple[int, int] = (1920, 1080)
    executable_path: str | None = DEFAULT_EXECUTABLE_PATH
    user_agent: str | None = None
    """Pin a single UA. ``None`` picks one from :data:`USER_AGENTS` at random."""

    proxy: str | None = None
    """Upstream proxy, e.g. ``http://user:pass@host:port``."""

    extra_headers: dict[str, str] = field(default_factory=dict)

    # Target. Overridable so the scraper can be pointed at a local fixture
    # server in tests, or at a mirror.
    base_url: str = BASE_URL
    stores_path: str = STORES_PATH

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if self.min_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must be non-negative")
        if self.min_delay > self.max_delay:
            raise ValueError("min_delay must be <= max_delay")
        if self.max_pages is not None and self.max_pages < 1:
            raise ValueError("max_pages must be >= 1 when set")

    @property
    def stores_url(self) -> str:
        """Absolute URL of the store directory index."""
        return f"{self.base_url.rstrip('/')}{self.stores_path}"

    def page_url(self, page: int) -> str:
        """Absolute URL for the given 1-based listing page."""
        if page <= 1:
            return self.stores_url
        return self.stores_url + PAGE_QUERY_TEMPLATE.format(page=page)
