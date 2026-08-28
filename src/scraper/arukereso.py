"""Store directory scraper for arukereso.hu."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from src.scraper.browser import FetchError, StealthBrowser
from src.scraper.config import ScrapeConfig
from src.scraper.models import Store
from src.scraper.parsing import (
    has_next_page,
    parse_float_hu,
    parse_int_hu,
    parse_stores,
    select_first,
    select_text,
)

logger = logging.getLogger(__name__)

#: Hard ceiling on pagination, so a misread "next page" control cannot spin
#: forever when ``max_pages`` is unset.
PAGE_LIMIT = 500


class RobotsDisallowed(RuntimeError):
    """Raised when robots.txt forbids the requested path."""


def check_robots(url: str, user_agent: str = "*", timeout: float = 10.0) -> bool:
    """Return True when ``url`` may be fetched according to robots.txt.

    A robots.txt that cannot be retrieved is treated as permissive, matching
    the convention that an absent file means no restriction. Network failure
    is logged rather than raised so a transient error does not abort a run.
    """
    robots_url = urljoin(url, "/robots.txt")
    parser = RobotFileParser()
    try:
        response = requests.get(robots_url, timeout=timeout)
        if response.status_code >= 400:
            logger.debug("robots.txt returned HTTP %s", response.status_code)
            return True
        parser.parse(response.text.splitlines())
    except requests.RequestException as exc:
        logger.warning("Could not read %s (%s); proceeding", robots_url, exc)
        return True
    return parser.can_fetch(user_agent, url)


async def fetch_all_stores(config: ScrapeConfig | None = None) -> list[Store]:
    """Scrape every store in the arukereso.hu directory.

    Walks the listing pagination, deduplicating by ``store_id``, and optionally
    visits each profile page for the fields the listing omits.

    Args:
        config: Runtime settings. Defaults to a conservative
            :class:`~src.scraper.config.ScrapeConfig`.

    Returns:
        Stores in the order first encountered.

    Raises:
        RobotsDisallowed: ``config.respect_robots`` is set and robots.txt
            disallows the store directory.
    """
    cfg = config or ScrapeConfig()

    if cfg.respect_robots and not check_robots(cfg.stores_url):
        raise RobotsDisallowed(
            f"robots.txt disallows {cfg.stores_url}. Set respect_robots=False "
            "(CLI: --ignore-robots) only if you have permission to crawl it."
        )

    stores: dict[str, Store] = {}

    async with StealthBrowser(cfg) as browser:
        page = 1
        limit = cfg.max_pages or PAGE_LIMIT

        while page <= limit:
            url = cfg.page_url(page)
            logger.info("Fetching listing page %s: %s", page, url)

            try:
                html = await browser.fetch(url)
            except FetchError as exc:
                logger.error("Listing page %s failed: %s", page, exc)
                break

            found = parse_stores(html, base_url=cfg.base_url)
            new = {s.store_id: s for s in found if s.store_id not in stores}

            logger.info(
                "Page %s: %s stores parsed, %s new (running total %s)",
                page,
                len(found),
                len(new),
                len(stores) + len(new),
            )

            # No new stores means either the end of the directory or a
            # pagination parameter the site ignores — both mean stop.
            if not new:
                logger.info("Page %s added no new stores; stopping pagination", page)
                break

            stores.update(new)

            if not has_next_page(html):
                logger.info("No next-page control on page %s; pagination done", page)
                break

            page += 1

        if cfg.enrich_details and stores:
            await _enrich_all(browser, list(stores.values()), cfg)

    logger.info("Scrape complete: %s unique stores", len(stores))
    return list(stores.values())


def fetch_all_stores_sync(config: ScrapeConfig | None = None) -> list[Store]:
    """Blocking wrapper around :func:`fetch_all_stores`."""
    return asyncio.run(fetch_all_stores(config))


def fetch_store_ids(config: ScrapeConfig | None = None) -> list[str]:
    """Return just the store IDs from the directory."""
    return [store.store_id for store in fetch_all_stores_sync(config)]


# -- detail enrichment -----------------------------------------------------


async def _enrich_all(
    browser: StealthBrowser, stores: list[Store], cfg: ScrapeConfig
) -> None:
    """Populate missing fields by visiting each store's profile page.

    Mutates ``stores`` in place. Individual failures are logged and skipped:
    one unreachable profile should not lose the whole run's listing data.
    """
    logger.info("Enriching %s stores from their profile pages", len(stores))
    results = await asyncio.gather(
        *(_enrich_one(browser, store) for store in stores),
        return_exceptions=True,
    )
    failures = sum(1 for r in results if isinstance(r, BaseException))
    if failures:
        logger.warning("%s/%s profile pages failed to enrich", failures, len(stores))


async def _enrich_one(browser: StealthBrowser, store: Store) -> None:
    """Fill in blank fields on ``store`` from its profile page."""
    try:
        html = await browser.fetch(store.profile_url)
    except FetchError as exc:
        logger.debug("Enrich failed for %s: %s", store.store_id, exc)
        raise

    # Imported locally so the listing-only path never pays for it.
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)

    if store.rating is None:
        node = select_first(tree, "rating")
        store.rating = parse_float_hu(
            (node.attributes.get("content") if node else None)
            or select_text(tree, "rating")
        )
    if store.review_count is None:
        store.review_count = parse_int_hu(select_text(tree, "review_count"))
    if store.offer_count is None:
        store.offer_count = parse_int_hu(select_text(tree, "offer_count"))
    if store.city is None:
        store.city = select_text(tree, "city")
    if store.website_url is None:
        store.website_url = _find_outbound_link(tree, browser.config.base_url)


def _find_outbound_link(tree: object, base_url: str) -> str | None:
    """Return the first link pointing off arukereso.hu — the merchant's site.

    Excludes the site's own domain *and* its subdomains. Comparing against the
    netloc alone is not enough: that is ``www.arukereso.hu``, which does not
    match a CDN host like ``static.arukereso.hu``, so image and asset links
    would be recorded as the merchant's website.
    """
    own_host = urlparse(base_url).netloc
    root = own_host[4:] if own_host.startswith("www.") else own_host

    for anchor in tree.css("a[href]"):  # type: ignore[attr-defined]
        href = anchor.attributes.get("href")
        if not href or not href.startswith("http"):
            continue
        host = urlparse(href).netloc
        if not host or host == root or host.endswith(f".{root}"):
            continue
        return href
    return None
