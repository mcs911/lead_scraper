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
