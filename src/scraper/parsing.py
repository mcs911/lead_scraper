"""HTML parsing: selector resolution, Hungarian number handling, store extraction.

Kept free of any Playwright import so the whole extraction path is testable
against saved HTML fixtures without launching a browser.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser, Node

from src.scraper.config import BASE_URL, SELECTORS, STORE_URL_MARKERS
from src.scraper.models import Store

logger = logging.getLogger(__name__)

_DIGITS = re.compile(r"\d")
#: Matches a number written in either Hungarian or English convention,
#: e.g. "1 234", "1.234", "4,5", "4.5".
_NUMBER = re.compile(r"\d[\d\s., ]*")


# -- number parsing --------------------------------------------------------


def parse_int_hu(text: str | None) -> int | None:
    """Parse an integer from Hungarian-formatted text.

    Hungarian groups thousands with a space or a period, so ``"1 234"`` and
    ``"1.234"`` are both 1234. Surrounding prose is ignored, which lets this
    read strings like ``"(1 234 értékelés)"``.

    >>> parse_int_hu("1 234 értékelés")
    1234
    >>> parse_int_hu("1.234")
    1234
    """
    if not text:
        return None
    match = _NUMBER.search(text)
    if not match:
        return None
    # Drop every grouping separator; an integer has no fractional part to keep.
    cleaned = re.sub(r"[\s., ]", "", match.group())
    return int(cleaned) if cleaned.isdigit() else None


def parse_float_hu(text: str | None) -> float | None:
    """Parse a decimal from Hungarian-formatted text.

    The comma is Hungary's decimal mark, so ``"4,5"`` is 4.5. When both a
    period and a comma appear the period is the thousands separator
    (``"1.234,5"`` -> 1234.5).

    >>> parse_float_hu("4,5")
    4.5
    >>> parse_float_hu("4.5 / 5")
    4.5
    """
    if not text:
        return None
    match = _NUMBER.search(text)
    if not match:
        return None
    raw = match.group().strip().replace(" ", "").replace(" ", "")

    if "." in raw and "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    # A lone period is left as the decimal point.

    try:
        return float(raw)
    except ValueError:
        return None


# -- selector engine -------------------------------------------------------


def select_first(node: Node | HTMLParser, field: str) -> Node | None:
    """Return the first node matching any candidate selector for ``field``.

    Candidates are tried in the order declared in
    :data:`~src.scraper.config.SELECTORS`. A selector the parser cannot compile
    is skipped rather than raising, so one bad entry never breaks a run.
    """
    for selector in SELECTORS.get(field, []):
        try:
            found = node.css_first(selector)
        except Exception:  # selectolax raises bare exceptions on bad selectors
            logger.debug("Unsupported selector %r for field %r", selector, field)
            continue
        if found is not None:
            return found
    return None


def select_text(node: Node | HTMLParser, field: str) -> str | None:
    """Return the stripped text of the first match for ``field``."""
    found = select_first(node, field)
    if found is None:
        return None
    text = found.text(strip=True)
    return text or None


def _attr(node: Node | None, name: str) -> str | None:
    """Return a node attribute, or ``None`` when absent or empty."""
    if node is None:
        return None
    value = node.attributes.get(name)
    return value.strip() if value else None


# -- store extraction ------------------------------------------------------


def is_store_url(href: str) -> bool:
    """True when ``href`` looks like a link to a store profile."""
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return False
    path = urlparse(href).path
    if not any(marker in path for marker in STORE_URL_MARKERS):
        return False
    # The directory index itself is not a store profile.
    return path.rstrip("/") not in {marker.rstrip("/") for marker in STORE_URL_MARKERS}


def extract_store_id(url: str) -> str:
    """Derive a stable identifier from a store profile URL.

    Uses the final path segment, which is the store's slug and the most stable
    handle the site exposes. Returns an empty string for a URL with no usable
    path.

    >>> extract_store_id("https://www.arukereso.hu/stores/example-kft/")
    'example-kft'
    """
    path = urlparse(url).path.rstrip("/")
    if not path:
        return ""
    return path.rsplit("/", 1)[-1]


def _store_from_card(card: Node, base_url: str) -> Store | None:
    """Build a :class:`Store` from one listing card, or ``None`` if unusable."""
    # The profile link anchors the record: without it there is no id.
    link_node = None
    for anchor in card.css("a[href]"):
        href = _attr(anchor, "href")
        if href and is_store_url(href):
            link_node = anchor
            break
    if link_node is None:
        link_node = select_first(card, "link")

    href = _attr(link_node, "href")
    if not href:
        return None

    profile_url = urljoin(base_url, href)
    store_id = _attr(card, "data-store-id") or extract_store_id(profile_url)
    if not store_id:
        return None

    name = (
        select_text(card, "name")
        or _attr(link_node, "title")
        or (link_node.text(strip=True) if link_node else None)
    )
    if not name:
        return None

    logo = _attr(select_first(card, "logo"), "src")

    # An outbound absolute link to another host is the merchant's own site.
    website = None
    for anchor in card.css("a[href]"):
        candidate = _attr(anchor, "href")
        if not candidate or not candidate.startswith("http"):
            continue
        if urlparse(candidate).netloc and not is_store_url(candidate):
            if urlparse(candidate).netloc != urlparse(base_url).netloc:
                website = candidate
                break

    return Store(
        store_id=store_id,
        name=name,
        profile_url=profile_url,
        website_url=website,
        rating=parse_float_hu(
            _attr(select_first(card, "rating"), "content")
            or _attr(card, "data-rating")
            or select_text(card, "rating")
        ),
        review_count=parse_int_hu(
            _attr(select_first(card, "review_count"), "content")
            or select_text(card, "review_count")
        ),
        offer_count=parse_int_hu(select_text(card, "offer_count")),
        logo_url=urljoin(base_url, logo) if logo else None,
        city=select_text(card, "city"),
    )


def _heuristic_stores(tree: HTMLParser, base_url: str) -> list[Store]:
    """Fallback extraction that ignores class names entirely.

    Collects every anchor whose href looks like a store profile and treats the
    anchor's nearest meaningful ancestor as its card. This is what keeps the
    scraper producing output when the configured selectors do not match the
    live markup.
    """
    stores: dict[str, Store] = {}

    for anchor in tree.css("a[href]"):
        href = _attr(anchor, "href")
        if not href or not is_store_url(href):
            continue

        profile_url = urljoin(base_url, href)
        store_id = extract_store_id(profile_url)
        if not store_id or store_id in stores:
            continue

        name = anchor.text(strip=True) or _attr(anchor, "title")
        if not name:
            # An image-only link: fall back to the logo's alt text.
            img = anchor.css_first("img[alt]")
            name = _attr(img, "alt")
        if not name:
            continue

        # Walk up a few levels to find a container holding the rating/counts
        # that sit beside the link rather than inside it.
        card: Node = anchor
        for _ in range(3):
            if card.parent is None:
                break
            card = card.parent

        stores[store_id] = Store(
            store_id=store_id,
            name=name,
            profile_url=profile_url,
            rating=parse_float_hu(select_text(card, "rating")),
            review_count=parse_int_hu(select_text(card, "review_count")),
            offer_count=parse_int_hu(select_text(card, "offer_count")),
            logo_url=(
                urljoin(base_url, _attr(anchor.css_first("img[src]"), "src") or "")
                or None
            ),
            city=select_text(card, "city"),
        )

    return list(stores.values())


def parse_stores(html: str, base_url: str = BASE_URL) -> list[Store]:
    """Extract every store on a listing page.

    Tries the configured card selectors first and falls back to
    :func:`_heuristic_stores` when they yield nothing, so unverified selectors
    degrade to reduced field coverage rather than an empty result.
    """
    tree = HTMLParser(html)

    cards: list[Node] = []
    for selector in SELECTORS["card"]:
        try:
            cards = tree.css(selector)
        except Exception:
            logger.debug("Unsupported card selector %r", selector)
            continue
        if cards:
            logger.debug("Matched %s cards with %r", len(cards), selector)
            break

    stores: dict[str, Store] = {}
    for card in cards:
        store = _store_from_card(card, base_url)
        if store is not None and store.is_complete:
            stores.setdefault(store.store_id, store)

    if not stores:
        logger.info(
            "No cards matched the configured selectors; using heuristic extraction. "
            "Run `python -m src.cli discover` to lock in the real selectors."
        )
        for store in _heuristic_stores(tree, base_url):
            if store.is_complete:
                stores.setdefault(store.store_id, store)

    return list(stores.values())


def has_next_page(html: str) -> bool:
    """True when the page exposes a 'next page' pagination control."""
    tree = HTMLParser(html)
    return select_first(tree, "next_page") is not None
