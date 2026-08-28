"""Tests for the parsing layer, run against saved HTML fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scraper.parsing import (
    extract_store_id,
    has_next_page,
    is_store_url,
    parse_float_hu,
    parse_int_hu,
    parse_stores,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def listing_html() -> str:
    return (FIXTURES / "stores_listing.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def unknown_markup_html() -> str:
    return (FIXTURES / "stores_unknown_markup.html").read_text(encoding="utf-8")


# -- Hungarian number parsing ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 234 értékelés", 1234),  # space-grouped thousands
        ("1.234", 1234),  # period-grouped thousands (Hungarian)
        ("12.480 termék", 12480),
        ("57 értékelés", 57),
        ("0", 0),
        ("nincs adat", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_int_hu(text: str | None, expected: int | None) -> None:
    assert parse_int_hu(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4,7", 4.7),  # comma is the Hungarian decimal mark
        ("4.7", 4.7),  # a lone period stays decimal
        ("1.234,5", 1234.5),  # period groups, comma decimates
        ("4,2 / 5", 4.2),
        ("5,0", 5.0),
        ("nincs", None),
        (None, None),
    ],
)
def test_parse_float_hu(text: str | None, expected: float | None) -> None:
    result = parse_float_hu(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# -- URL helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.arukereso.hu/stores/alfa-kft/", "alfa-kft"),
        ("/stores/bety-butor/", "bety-butor"),
        ("/stores/gamma?ref=x", "gamma"),
        ("https://www.arukereso.hu/", ""),
    ],
)
def test_extract_store_id(url: str, expected: str) -> None:
    assert extract_store_id(url) == expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/stores/alfa-kft/", True),
        ("https://www.arukereso.hu/stores/alfa-kft/", True),
        ("/shop/valami/", True),
        ("/stores/", False),  # the directory index is not a store
        ("/adatvedelem/", False),
        ("#top", False),
        ("mailto:info@example.hu", False),
        ("", False),
    ],
)
def test_is_store_url(href: str, expected: bool) -> None:
    assert is_store_url(href) is expected


# -- listing extraction ----------------------------------------------------


def test_parse_stores_extracts_all_fields(listing_html: str) -> None:
    stores = {s.store_id: s for s in parse_stores(listing_html)}

    assert set(stores) == {"alfa-elektronika", "bety-butor"}

    alfa = stores["alfa-elektronika"]
    assert alfa.name == "Alfa Elektronika Kft."
    assert alfa.profile_url == "https://www.arukereso.hu/stores/alfa-elektronika/"
    assert alfa.rating == pytest.approx(4.7)
    assert alfa.review_count == 1284
    assert alfa.offer_count == 12480
    assert alfa.city == "Budapest"
    assert alfa.website_url == "https://alfa-elektronika.hu"
    assert alfa.logo_url == "https://www.arukereso.hu/img/logo/alfa.png"


def test_parse_stores_deduplicates_by_store_id(listing_html: str) -> None:
    """The fixture lists alfa-elektronika twice; only one record should survive."""
    stores = parse_stores(listing_html)
    ids = [s.store_id for s in stores]
    assert len(ids) == len(set(ids))


def test_parse_stores_tolerates_missing_optional_fields(listing_html: str) -> None:
    bety = next(s for s in parse_stores(listing_html) if s.store_id == "bety-butor")
    assert bety.name == "Béty Bútor Zrt."
    assert bety.rating == pytest.approx(3.9)
    assert bety.offer_count is None  # absent from the fixture
    assert bety.website_url is None


def test_heuristic_fallback_handles_unknown_markup(unknown_markup_html: str) -> None:
    """Selectors are unverified, so the class-name-agnostic fallback must work."""
    stores = {s.store_id: s for s in parse_stores(unknown_markup_html)}

    assert set(stores) == {"gamma-otthon", "delta-sport", "epszilon-kert"}
    assert stores["gamma-otthon"].name == "Gamma Otthon Bt."
    assert (
        stores["delta-sport"].profile_url
        == "https://www.arukereso.hu/stores/delta-sport/"
    )


def test_heuristic_fallback_excludes_non_store_links(unknown_markup_html: str) -> None:
    """The footer's index, policy, anchor, and mailto links must not become leads."""
    ids = {s.store_id for s in parse_stores(unknown_markup_html)}
    assert not ids & {"", "adatvedelem", "top", "stores"}


def test_parse_stores_returns_empty_for_pages_without_stores() -> None:
    assert parse_stores("<html><body><p>Nincs találat</p></body></html>") == []


# -- pagination ------------------------------------------------------------


def test_has_next_page_detects_control(listing_html: str) -> None:
    assert has_next_page(listing_html) is True


def test_has_next_page_false_on_last_page(unknown_markup_html: str) -> None:
    assert has_next_page(unknown_markup_html) is False


# -- regressions -----------------------------------------------------------


def test_logo_url_is_none_when_card_has_no_image() -> None:
    """urljoin(base, "") returns the base URL, not "".

    Without an explicit guard every logo-less store was exported with
    logo_url set to the site root. This hits the heuristic path, which is the
    one that actually runs while the selectors are unverified.
    """
    html = (FIXTURES / "stores_no_logo.html").read_text(encoding="utf-8")
    stores = {s.store_id: s for s in parse_stores(html)}

    assert set(stores) == {"zeta-muszaki", "omega-halozat"}
    for store in stores.values():
        assert store.logo_url is None, f"{store.store_id} got {store.logo_url!r}"
