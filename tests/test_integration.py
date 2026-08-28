"""End-to-end tests driving real Chromium against a local fixture server.

These exercise the full path — stealth patches, navigation, retries, parsing,
pagination, export — without touching the public internet.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path
from typing import Iterator

import pytest

from src.scraper.browser import StealthBrowser
from src.scraper.config import ScrapeConfig
from src.scraper.export import to_csv, to_json

playwright = pytest.importorskip("playwright", reason="playwright not installed")

FIXTURES = Path(__file__).parent / "fixtures"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Static handler that maps /stores/ to the listing fixture and stays silent."""

    def log_message(self, *args: object) -> None:  # noqa: A003 - stdlib signature
        pass

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        if self.path.rstrip("/").endswith(("alfa-elektronika", "bety-butor")):
            name = (
                "store_profile.html"
                if "alfa" in self.path
                else "store_profile_table.html"
            )
            body = (FIXTURES / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/stores/"):
            body = (FIXTURES / "stores_listing.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    """Serve the fixtures on localhost and yield the base URL."""
    handler = functools.partial(_QuietHandler, directory=str(FIXTURES))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def config(server: str) -> ScrapeConfig:
    """A fast, local-only config: no delays, no robots lookup."""
    return ScrapeConfig(
        base_url=server,
        min_delay=0.0,
        max_delay=0.0,
        respect_robots=False,
        max_pages=1,
        timeout_ms=15_000,
    )


@pytest.mark.asyncio
async def test_stealth_patches_applied(config: ScrapeConfig) -> None:
    """The fingerprint surface a detector reads should look like real Chrome."""
    async with StealthBrowser(config) as browser:
        assert browser._context is not None
        page = await browser._context.new_page()
        try:
            await page.goto(config.stores_url, wait_until="domcontentloaded")
            fingerprint = await page.evaluate("""() => ({
                    webdriver: navigator.webdriver,
                    hasWebdriverProp: 'webdriver' in navigator,
                    languages: navigator.languages,
                    plugins: navigator.plugins.length,
                    chrome: typeof window.chrome,
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                    cores: navigator.hardwareConcurrency,
                })""")
        finally:
            await page.close()

    # Genuine Chrome exposes navigator.webdriver as false, not undefined, so
    # the property must still be present and simply report a non-automated
    # browser. Under stock Playwright this would be True.
    assert fingerprint["webdriver"] is False
    assert fingerprint["hasWebdriverProp"] is True
    assert fingerprint["languages"][0] == "hu-HU"
    assert fingerprint["plugins"] > 0
    assert fingerprint["chrome"] == "object"
    assert fingerprint["timezone"] == "Europe/Budapest"
    assert fingerprint["cores"] == 8


@pytest.mark.asyncio
async def test_fetch_all_stores_end_to_end(config: ScrapeConfig) -> None:
    """A full scrape through a real browser yields parsed, deduplicated leads."""
    from src.scraper.arukereso import fetch_all_stores

    stores = await fetch_all_stores(config)

    by_id = {s.store_id: s for s in stores}
    assert set(by_id) == {"alfa-elektronika", "bety-butor"}

    alfa = by_id["alfa-elektronika"]
    assert alfa.name == "Alfa Elektronika Kft."
    assert alfa.rating == pytest.approx(4.7)
    assert alfa.review_count == 1284
    assert alfa.profile_url.endswith("/stores/alfa-elektronika/")


@pytest.mark.asyncio
async def test_pagination_stops_when_no_new_stores(server: str) -> None:
    """The fixture server returns the same page for every ?page=N.

    Without the "no new stores" guard this would loop to PAGE_LIMIT, so this
    test pins the protection against a wrong pagination parameter.
    """
    from src.scraper.arukereso import fetch_all_stores

    config = ScrapeConfig(
        base_url=server,
        min_delay=0.0,
        max_delay=0.0,
        respect_robots=False,
        max_pages=None,  # unbounded: only the guard can stop it
        timeout_ms=15_000,
    )
    stores = await fetch_all_stores(config)
    assert len(stores) == 2


@pytest.mark.asyncio
async def test_export_round_trip(config: ScrapeConfig, tmp_path: Path) -> None:
    """Exported CSV and JSON carry Hungarian accented characters intact."""
    import csv
    import json

    from src.scraper.arukereso import fetch_all_stores

    stores = await fetch_all_stores(config)

    csv_path = to_csv(stores, tmp_path / "leads.csv")
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(stores)
    assert any(row["name"] == "Béty Bútor Zrt." for row in rows)

    json_path = to_json(stores, tmp_path / "leads.json")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert {r["store_id"] for r in payload} == {s.store_id for s in stores}
    assert any(r["name"] == "Béty Bútor Zrt." for r in payload)


def test_outbound_link_excludes_own_subdomains() -> None:
    """A CDN host under the site's own domain is not the merchant's website.

    Matching on the netloc alone ("www.arukereso.hu") let "static.arukereso.hu"
    through as an outbound link.
    """
    from selectolax.parser import HTMLParser

    from src.scraper.arukereso import _find_outbound_link

    tree = HTMLParser("""<a href="https://static.arukereso.hu/a.png">asset</a>
           <a href="https://kepek.arukereso.hu/b.png">asset</a>
           <a href="https://arukereso.hu/x">self</a>
           <a href="https://alfa-elektronika.hu">shop</a>""")
    assert (
        _find_outbound_link(tree, "https://www.arukereso.hu")
        == "https://alfa-elektronika.hu"
    )


@pytest.mark.asyncio
async def test_backoff_skips_the_final_attempt(config: ScrapeConfig) -> None:
    """Backing off after the last attempt only delays the error.

    The loop exits straight into FetchError, so that sleep buys no retry. It
    measured at ~4-5s of dead wait per permanently-failing URL. Asserted by
    timing the real coroutine rather than inspecting it.
    """
    import time

    config.max_retries = 3
    browser = StealthBrowser(config)

    # A non-final attempt must actually sleep (2**1 = 2s floor).
    start = time.monotonic()
    await browser._backoff(1)
    assert time.monotonic() - start > 1.0

    # The final attempt must return effectively instantly.
    start = time.monotonic()
    await browser._backoff(3)
    assert time.monotonic() - start < 0.1


def test_discover_checks_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery hits the live site, so it owes robots.txt the same respect.

    It previously fetched unconditionally, which also made --ignore-robots
    inert on that subcommand.
    """
    import asyncio

    from src import cli

    calls: list[str] = []
    monkeypatch.setattr(cli, "check_robots", lambda url: calls.append(url) or False)

    args = cli._build_parser().parse_args(["discover", "--url", "https://example.com/"])
    rc = asyncio.run(cli._run_discover(args))

    assert rc == 2, "a robots.txt disallow must abort discovery"
    assert calls == ["https://example.com/"]


@pytest.mark.asyncio
async def test_scrape_to_csv_writes_file(config: ScrapeConfig, tmp_path: Path) -> None:
    """The one-call form scrapes and writes a usable CSV."""
    import csv

    from src.scraper.arukereso import scrape_to_csv

    out = await scrape_to_csv(tmp_path / "leads.csv", config)

    assert out.exists()
    with out.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert {r["store_id"] for r in rows} == {"alfa-elektronika", "bety-butor"}
    assert any(r["name"] == "Béty Bútor Zrt." for r in rows)


@pytest.mark.asyncio
async def test_scrape_to_csv_creates_parent_directories(
    config: ScrapeConfig, tmp_path: Path
) -> None:
    from src.scraper.arukereso import scrape_to_csv

    out = await scrape_to_csv(tmp_path / "nested" / "dir" / "leads.csv", config)
    assert out.exists()


@pytest.mark.asyncio
async def test_scrape_to_csv_writes_header_only_when_nothing_found(
    server: str, tmp_path: Path
) -> None:
    """An empty scrape must still leave a readable CSV, not a missing file.

    Silently writing nothing is the failure mode to avoid while the selectors
    are unverified, so the header lands and the caller gets a warning.
    """
    import csv

    from src.scraper.arukereso import scrape_to_csv
    from src.scraper.models import STORE_FIELDS

    # /nothing/ is served 404, so no stores are parsed.
    cfg = ScrapeConfig(
        base_url=server,
        stores_path="/nothing/",
        min_delay=0.0,
        max_delay=0.0,
        respect_robots=False,
        max_pages=1,
        max_retries=1,
        timeout_ms=8_000,
    )
    out = await scrape_to_csv(tmp_path / "empty.csv", cfg)

    assert out.exists()
    with out.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        assert tuple(next(reader)) == STORE_FIELDS
        assert next(reader, None) is None


def test_scrape_to_csv_is_exported_from_the_package() -> None:
    import src.scraper as pkg

    assert callable(pkg.scrape_to_csv)
    assert callable(pkg.scrape_to_csv_sync)


@pytest.mark.asyncio
async def test_details_enrichment_populates_company_fields(server: str) -> None:
    """--details visits each profile URL and fills in the company details."""
    from src.scraper.arukereso import fetch_all_stores

    cfg = ScrapeConfig(
        base_url=server,
        min_delay=0.0,
        max_delay=0.0,
        respect_robots=False,
        max_pages=1,
        enrich_details=True,
        timeout_ms=15_000,
    )
    stores = {s.store_id: s for s in await fetch_all_stores(cfg)}

    alfa = stores["alfa-elektronika"]
    assert alfa.legal_name == "Alfa Kereskedelmi Kft."
    assert alfa.tax_number == "12345678-1-42"
    assert alfa.phone == "+3612345678"
    # The storefront name from the listing is preserved, not overwritten.
    assert alfa.name == "Alfa Elektronika Kft."

    bety = stores["bety-butor"]
    assert bety.legal_name == "Béty Bútor Zrt."
    assert bety.tax_number == "87654321-2-13"
    assert bety.phone == "+36301234567"


@pytest.mark.asyncio
async def test_company_fields_stay_none_without_details(config: ScrapeConfig) -> None:
    """A listing-only run must not invent company details."""
    from src.scraper.arukereso import fetch_all_stores

    for store in await fetch_all_stores(config):
        assert store.legal_name is None
        assert store.tax_number is None
        assert store.phone is None


@pytest.mark.asyncio
async def test_no_email_reaches_the_exported_csv(server: str, tmp_path: Path) -> None:
    """End-to-end guard: the profile fixture carries an email; the CSV must not.

    Covers the whole path rather than the extractors alone, so a future field
    or a widened fallback cannot smuggle an address into the output.
    """
    from src.scraper.arukereso import scrape_to_csv
    from src.scraper.contact import contains_email

    cfg = ScrapeConfig(
        base_url=server,
        min_delay=0.0,
        max_delay=0.0,
        respect_robots=False,
        max_pages=1,
        enrich_details=True,
        timeout_ms=15_000,
    )
    out = await scrape_to_csv(tmp_path / "leads.csv", cfg)
    assert not contains_email(out.read_text(encoding="utf-8-sig"))
