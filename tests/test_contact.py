"""Tests for Hungarian company-detail extraction from profile pages."""

from __future__ import annotations

from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from src.scraper.contact import (
    contains_email,
    extract_legal_name,
    extract_phone,
    extract_tax_number,
    find_labelled_value,
    normalise_phone,
    normalise_tax_number,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def profile() -> HTMLParser:
    return HTMLParser((FIXTURES / "store_profile.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def profile_table() -> HTMLParser:
    return HTMLParser(
        (FIXTURES / "store_profile_table.html").read_text(encoding="utf-8")
    )


# -- adószám ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12345678-1-42", "12345678-1-42"),
        ("Adószám: 12345678-1-42", "12345678-1-42"),
        ("12345678 1 42", "12345678-1-42"),  # space-separated
        ("12345678142", "12345678-1-42"),  # unseparated
        ("HU12345678", "12345678"),  # EU VAT carries only the core
        ("HU 12345678", "12345678"),
        ("nincs megadva", None),
        ("", None),
        (None, None),
    ],
)
def test_normalise_tax_number(raw: str | None, expected: str | None) -> None:
    assert normalise_tax_number(raw) == expected


def test_extract_tax_number_from_definition_list(profile: HTMLParser) -> None:
    assert extract_tax_number(profile) == "12345678-1-42"


def test_extract_tax_number_from_table(profile_table: HTMLParser) -> None:
    assert extract_tax_number(profile_table) == "87654321-2-13"


# -- phone -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+36 1 234 5678", "+3612345678"),  # Budapest landline
        ("06 1 234 5678", "+3612345678"),  # domestic trunk prefix
        ("+36 30/123-4567", "+36301234567"),  # mobile
        ("06-30-123-4567", "+36301234567"),
        ("0036 20 123 4567", "+36201234567"),  # international prefix
        ("(+36) 70 123 4567", "+36701234567"),
        ("12345678901234", None),  # an order number, not a phone
        ("123", None),  # too short
        ("nincs", None),
        (None, None),
    ],
)
def test_normalise_phone(raw: str | None, expected: str | None) -> None:
    assert normalise_phone(raw) == expected


def test_extract_phone_prefers_tel_link(profile: HTMLParser) -> None:
    assert extract_phone(profile) == "+3612345678"


def test_extract_phone_falls_back_to_labelled_text(profile_table: HTMLParser) -> None:
    """No tel: link in this fixture, so the label is the only route."""
    assert extract_phone(profile_table) == "+36301234567"


def test_phone_extraction_ignores_a_long_order_number(
    profile_table: HTMLParser,
) -> None:
    """The fixture also carries a 14-digit 'Rendelésszám' that must not match."""
    assert extract_phone(profile_table) != "+3612345678901234"


# -- legal name ------------------------------------------------------------


def test_extract_legal_name_differs_from_storefront_name(profile: HTMLParser) -> None:
    """The shop trades as "AlfaShop" but invoices as "Alfa Kereskedelmi Kft."."""
    assert extract_legal_name(profile) == "Alfa Kereskedelmi Kft."


def test_extract_legal_name_from_table(profile_table: HTMLParser) -> None:
    assert extract_legal_name(profile_table) == "Béty Bútor Zrt."


def test_extract_legal_name_finds_suffix_without_a_label() -> None:
    tree = HTMLParser("<p>Az üzletet a Gamma Otthon Bt. üzemelteti.</p>")
    assert extract_legal_name(tree) == "Gamma Otthon Bt."


def test_extract_legal_name_returns_none_when_absent() -> None:
    assert extract_legal_name(HTMLParser("<p>Nincs cégadat.</p>")) is None


# -- label lookup ----------------------------------------------------------


def test_find_labelled_value_handles_dt_dd(profile: HTMLParser) -> None:
    assert find_labelled_value(profile, ["cím"]) == "1052 Budapest, Példa utca 1."


def test_find_labelled_value_returns_none_for_unknown_label(
    profile: HTMLParser,
) -> None:
    assert find_labelled_value(profile, ["bankszámlaszám"]) is None


# -- the email exclusion ---------------------------------------------------


def test_no_email_is_ever_collected(profile: HTMLParser) -> None:
    """Merchant email is hidden behind an interaction, so it is not collected.

    The fixture deliberately contains a mailto link and a plain-text address.
    Every extractor must come back clean, so a future selector or a widened
    free-text fallback cannot quietly start harvesting email.
    """
    for extracted in (
        extract_legal_name(profile),
        extract_tax_number(profile),
        extract_phone(profile),
        find_labelled_value(profile, ["kapcsolat"]),
    ):
        assert not contains_email(extracted), f"email leaked into {extracted!r}"


def test_store_model_has_no_email_field() -> None:
    """Absence of the field is the structural guarantee behind the rule."""
    import dataclasses

    from src.scraper.models import STORE_FIELDS, Store

    names = {f.name for f in dataclasses.fields(Store)}
    assert not any("email" in n or "mail" in n for n in names)
    assert not any("email" in f or "mail" in f for f in STORE_FIELDS)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("info@alfa.hu", True),
        ("Kapcsolat: info@alfa-kereskedelmi.hu", True),
        ("Alfa Kereskedelmi Kft.", False),
        ("+3612345678", False),
        (None, False),
    ],
)
def test_contains_email(value: str | None, expected: bool) -> None:
    assert contains_email(value) is expected


# -- regressions -----------------------------------------------------------


@pytest.mark.parametrize(
    "html",
    [
        "<p>Telefon: 06301234567 </p>",
        '<a href="tel:06301234567">06301234567</a>',
        "<p>Hívjon: 36301234567.</p>",
    ],
)
def test_phone_is_not_harvested_as_a_tax_number(html: str) -> None:
    """An unseparated adószám and a Hungarian phone are both 11 digits.

    The unlabelled whole-page fallback therefore demands the punctuated form;
    without that, a tel: link was read as the tax number 06301234-5-67.
    """
    assert extract_tax_number(HTMLParser(html)) is None


def test_unlabelled_tax_number_still_found_when_punctuated() -> None:
    assert extract_tax_number(HTMLParser("<p>12345678-1-42</p>")) == "12345678-1-42"


def test_labelled_tax_number_still_accepts_the_run_together_form() -> None:
    """A label disambiguates the digits, so the unseparated form stays valid."""
    tree = HTMLParser("<dl><dt>Adószám</dt><dd>12345678142</dd></dl>")
    assert extract_tax_number(tree) == "12345678-1-42"


def test_settlement_row_does_not_shadow_the_phone_row() -> None:
    """ "Település" starts with the "tel" label.

    A bare startswith matched it first, answered the phone lookup with
    "Budapest", and the caller discarded that as unparseable — losing the
    phone entirely on any page listing a settlement.
    """
    tree = HTMLParser(
        "<dl><dt>Település</dt><dd>Budapest</dd>"
        "<dt>Telefon</dt><dd>+36 1 234 5678</dd></dl>"
    )
    assert extract_phone(tree) == "+3612345678"


@pytest.mark.parametrize(
    "html",
    [
        "<p>Telefon: 06 1 234 5678</p>",
        "<p>Tel.: 06 1 234 5678</p>",
        "<p>Tel: 06 1 234 5678</p>",
        "<dl><dt>Telefonszám</dt><dd>06 1 234 5678</dd></dl>",
    ],
)
def test_genuine_phone_labels_still_match(html: str) -> None:
    """The boundary rule must not cost the labels that should match."""
    assert extract_phone(HTMLParser(html)) == "+3612345678"


def test_settlement_label_is_not_confused_for_a_phone_label() -> None:
    from src.scraper.contact import PHONE_LABELS, find_labelled_value

    tree = HTMLParser("<dl><dt>Település</dt><dd>Budapest</dd></dl>")
    assert find_labelled_value(tree, PHONE_LABELS) is None
