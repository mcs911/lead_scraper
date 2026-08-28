"""Tests for the DOM discovery reporter."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scraper.discovery import _rank_candidates, discover
from selectolax.parser import HTMLParser

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def unknown_markup() -> str:
    return (FIXTURES / "stores_unknown_markup.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def listing() -> str:
    return (FIXTURES / "stores_listing.html").read_text(encoding="utf-8")


def test_ranks_the_true_card_above_wrapping_ancestors(unknown_markup: str) -> None:
    """The repeating card must outrank the section/body that also contain links."""
    tree = HTMLParser(unknown_markup)
    ranked = _rank_candidates(tree, link_count=3)

    assert ranked, "expected at least one candidate"
    best = ranked[0]
    assert best.selector == "article.xy7-grid__cell.q3w"
    assert best.is_exact
    assert best.match_count == 3


def test_never_proposes_structural_tags(unknown_markup: str) -> None:
    """body/html wrap every link but are useless as card selectors."""
    tree = HTMLParser(unknown_markup)
    selectors = {c.selector for c in _rank_candidates(tree, link_count=3)}
    assert not selectors & {"body", "html", "-undef"}


def test_report_names_the_card_selector(unknown_markup: str) -> None:
    report = discover(unknown_markup)
    assert "Anchors matching a store-URL pattern: 3" in report
    assert "article.xy7-grid__cell.q3w" in report
    assert "one store per match" in report


def test_report_surfaces_microdata_when_present(listing: str) -> None:
    """The listing fixture has none, so the section should say so rather than
    silently omit itself."""
    report = discover(listing)
    assert "schema.org microdata" in report


def test_report_handles_page_with_no_stores() -> None:
    report = discover("<html><body><a href='/adatvedelem/'>x</a></body></html>")
    assert "No store-shaped links found" in report
    assert "Most common link path prefixes" in report
