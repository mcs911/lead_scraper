"""Command-line entrypoint for the arukereso.hu lead scraper.

python -m src.cli scrape --out leads.csv --max-pages 5
python -m src.cli discover --out dom-report.txt
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from src.scraper.arukereso import RobotsDisallowed, fetch_all_stores
from src.scraper.browser import FetchError, StealthBrowser
from src.scraper.config import ScrapeConfig
from src.scraper.discovery import discover
from src.scraper.export import to_csv, to_json

logger = logging.getLogger("lead_scraper")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lead_scraper",
        description="Scrape merchant leads from the arukereso.hu store directory.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- shared browser/politeness options --
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--headed", action="store_true", help="run with a visible browser window"
    )
    common.add_argument(
        "--timeout",
        type=int,
        default=30_000,
        help="per-navigation timeout in ms (default: 30000)",
    )
    common.add_argument("--proxy", help="upstream proxy, e.g. http://host:port")
    common.add_argument("--user-agent", help="pin a specific user agent")
    common.add_argument(
        "--ignore-robots",
        action="store_true",
        help="proceed even if robots.txt disallows the path",
    )

    scrape = sub.add_parser(
        "scrape", parents=[common], help="scrape the store directory"
    )
    scrape.add_argument(
        "-o",
        "--out",
        default="stores.csv",
        help="output path; .json writes JSON (default: stores.csv)",
    )
    scrape.add_argument(
        "--max-pages", type=int, help="stop after N listing pages (default: all)"
    )
    scrape.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="concurrent page fetches (default: 2)",
    )
    scrape.add_argument(
        "--min-delay",
        type=float,
        default=1.5,
        help="minimum delay between requests in seconds",
    )
    scrape.add_argument(
        "--max-delay",
        type=float,
        default=3.5,
        help="maximum delay between requests in seconds",
    )
    scrape.add_argument(
        "--details",
        action="store_true",
        help="also visit each store profile page for extra fields",
    )

    disc = sub.add_parser(
        "discover",
        parents=[common],
        help="report the live DOM structure to derive selectors",
    )
    disc.add_argument("-o", "--out", help="write the report to a file")
    disc.add_argument("--url", help="page to inspect (default: the store directory)")
    disc.add_argument("--wait-for", help="CSS selector to await before reading the DOM")

    return parser


def _config_from_args(args: argparse.Namespace) -> ScrapeConfig:
    return ScrapeConfig(
        max_pages=getattr(args, "max_pages", None),
        enrich_details=getattr(args, "details", False),
        concurrency=getattr(args, "concurrency", 2),
        min_delay=getattr(args, "min_delay", 1.5),
        max_delay=getattr(args, "max_delay", 3.5),
        respect_robots=not args.ignore_robots,
        headless=not args.headed,
        timeout_ms=args.timeout,
        proxy=args.proxy,
        user_agent=args.user_agent,
    )


async def _run_scrape(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    try:
        stores = await fetch_all_stores(config)
    except RobotsDisallowed as exc:
        logger.error("%s", exc)
        return 2

    if not stores:
        logger.error(
            "No stores extracted. The configured selectors likely do not match the "
            "live markup — run `python -m src.cli discover` to derive the real ones."
        )
        return 1

    out = Path(args.out)
    writer = to_json if out.suffix.lower() == ".json" else to_csv
    writer(stores, out)

    complete = sum(1 for s in stores if s.rating is not None)
    print(f"\n{len(stores)} stores -> {out}")
    print(f"  with rating: {complete}")
    print(f"  with website: {sum(1 for s in stores if s.website_url)}")
    return 0


async def _run_discover(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    url = args.url or config.stores_url

    async with StealthBrowser(config) as browser:
        try:
            html = await browser.fetch(url, wait_for=args.wait_for)
        except FetchError as exc:
            logger.error("%s", exc)
            return 1

    report = discover(html)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Report written to {args.out}")
    else:
        print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    runner = _run_scrape if args.command == "scrape" else _run_discover
    try:
        return asyncio.run(runner(args))
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
