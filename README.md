# lead_scraper

Scrapes merchant leads from the [arukereso.hu](https://www.arukereso.hu/stores/)
store directory using Playwright with anti-fingerprinting patches.

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

## Usage

```bash
# Scrape the whole directory to CSV
python -m src.cli scrape --out leads.csv

# First 5 pages, with profile-page enrichment, as JSON
python -m src.cli scrape --out leads.json --max-pages 5 --details

# Watch it run in a real window
python -m src.cli scrape --headed --max-pages 1 -v
```

From Python:

```python
from src.scraper import ScrapeConfig, scrape_to_csv_sync

# Scrape and write the CSV in one call
path = scrape_to_csv_sync("leads.csv", ScrapeConfig(max_pages=3))
print(f"wrote {path}")
```

Or keep the `Store` objects and write the file separately:

```python
from src.scraper import ScrapeConfig, fetch_all_stores_sync, to_csv

stores = fetch_all_stores_sync(ScrapeConfig(max_pages=3))
for store in stores:
    print(store.store_id, store.name, store.rating, store.review_count)
to_csv(stores, "leads.csv")
```

| Function | Returns |
|---|---|
| `scrape_to_csv(path, config)` | Scrapes, writes the CSV, returns its `Path` |
| `scrape_to_csv_sync(path, config)` | Blocking form of the above |
| `fetch_all_stores(config)` | `list[Store]`, async |
| `fetch_all_stores_sync(config)` | `list[Store]`, blocking |
| `fetch_store_ids(config)` | `list[str]` of store IDs |
| `to_csv(stores, path)` / `to_json(stores, path)` | Writes an existing list, returns its `Path` |

A run that finds nothing still writes a header-only CSV and logs a warning
rather than raising, so downstream tooling does not break on a missing file.
The CLI treats an empty scrape as an error (exit code 1) instead.

### Output fields

| Field | Notes |
|---|---|
| `store_id` | Slug from the profile URL — the key to dedupe and join on |
| `name` | Merchant name |
| `profile_url` | Absolute arukereso.hu profile URL |
| `website_url` | The merchant's own domain, when linked |
| `rating` | Float; Hungarian `4,7` is parsed as `4.7` |
| `review_count`, `offer_count` | Ints; `1 234` and `1.234` both parse as `1234` |
| `city`, `logo_url`, `scraped_at` | Best-effort |

CSV is written UTF-8 **with BOM** so Excel renders `á`/`ő`/`ű` correctly.

## The selectors are unverified — read this before a real run

This scraper was written in an environment where `www.arukereso.hu` is blocked
by a network egress proxy, so **the CSS selectors in `src/scraper/config.py`
were never checked against the live page**. They are ordered guesses.

Two things make that recoverable:

1. **A heuristic fallback.** When no configured selector matches, `parsing.py`
   finds stores by URL shape instead of class name. You still get
   `store_id`, `name`, and `profile_url` — the fields that matter for a lead
   list — just with thinner coverage of rating/city.
2. **A discovery mode** that derives the real selectors for you:

   ```bash
   python -m src.cli discover --out dom-report.txt
   ```

   It reports the repeating card container (validated by running each
   candidate as a real CSS selector), a sample card's HTML, the class names
   inside it, and any schema.org microdata. Paste the winners into
   `SELECTORS` in `src/scraper/config.py`.

Also verify `PAGE_QUERY_TEMPLATE` — the `?page=` parameter is a guess. If it is
wrong the site returns page 1 repeatedly; the pagination loop detects that (a
page yielding no new store IDs stops the crawl) so you get one page of results
rather than an infinite loop, but you would be silently under-collecting.

## Politeness and legal

Defaults are conservative: 2 concurrent pages and a randomised 1.5–3.5s gap,
roughly one request per second. `robots.txt` is checked before every run and a
disallow aborts it; `--ignore-robots` overrides that, which is yours to use
only where you have permission to crawl. Check Árukereső's terms before
running this at volume — the stealth patches make the crawler harder to
detect, they do not make it authorised.

## Development

```bash
pip install -r requirements-dev.txt
pytest tests/ --ignore=tests/test_client.py   # test_client.py needs live internet
ruff check src/ tests/ && black --check src/ tests/
```

Tests run fully offline: parsing is exercised against saved fixtures, and the
integration tests drive real Chromium against a local fixture server.
