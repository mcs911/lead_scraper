"""Lead scraping package for arukereso.hu store listings.

Exports are resolved lazily (PEP 562) so that importing the pure-Python
parsing helpers does not pull in Playwright. This keeps the parsing test
suite runnable without a browser installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.scraper.arukereso import (
        fetch_all_stores,
        fetch_all_stores_sync,
        fetch_store_ids,
    )
    from src.scraper.config import ScrapeConfig
    from src.scraper.models import Store

__all__ = [
    "ScrapeConfig",
    "Store",
    "fetch_all_stores",
    "fetch_all_stores_sync",
    "fetch_store_ids",
]

_LAZY: dict[str, str] = {
    "ScrapeConfig": "src.scraper.config",
    "Store": "src.scraper.models",
    "fetch_all_stores": "src.scraper.arukereso",
    "fetch_all_stores_sync": "src.scraper.arukereso",
    "fetch_store_ids": "src.scraper.arukereso",
}


def __getattr__(name: str) -> Any:
    """Import an export on first access."""
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted(__all__)
