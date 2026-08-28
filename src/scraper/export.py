"""CSV and JSON writers for scraped stores."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Sequence

from src.scraper.models import STORE_FIELDS, Store

logger = logging.getLogger(__name__)


def to_csv(stores: Sequence[Store], path: str | Path) -> Path:
    """Write ``stores`` to a UTF-8 CSV with a BOM.

    The BOM is what makes Excel open the file with Hungarian accented
    characters intact; without it ``á``/``ő``/``ű`` are mangled on Windows.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(STORE_FIELDS))
        writer.writeheader()
        for store in stores:
            writer.writerow(store.to_dict())

    logger.info("Wrote %s stores to %s", len(stores), target)
    return target


def to_json(stores: Sequence[Store], path: str | Path) -> Path:
    """Write ``stores`` to a UTF-8 JSON array, preserving non-ASCII text."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = [store.to_dict() for store in stores]
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info("Wrote %s stores to %s", len(stores), target)
    return target
