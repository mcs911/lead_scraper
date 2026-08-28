"""Hungarian company-detail extraction from a store profile page.

Covers the three fields the profile page carries beyond the listing:
adószám (tax number), the operating company's registered name, and a phone
number.

Email is deliberately **not** extracted. Árukereső hides merchant email
behind an interaction, so anything an automated pass could reach is either a
generic support alias or a value the site chose not to publish. See
:func:`contains_email`, which every value returned from this module passes
through, and the test that pins it — so the rule is a checked property
rather than a comment someone can quietly drop.

Extraction is label- and format-driven rather than selector-driven. The
adószám has an unmistakable shape, phone numbers arrive in ``tel:`` links, and
company details sit next to fixed Hungarian labels — all of which survive
markup this scraper has never seen, which matters while the CSS selectors in
``config.py`` remain unverified.
"""

from __future__ import annotations

import re
from typing import Iterable

from selectolax.parser import HTMLParser, Node

# -- the email exclusion ---------------------------------------------------
# Declared first because it guards every extractor below.

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def contains_email(value: object) -> bool:
    """True when ``value`` looks like it carries an email address.

    Merchant email on Árukereső is hidden behind an interaction, so this
    scraper must never end up carrying one. Every value returned from this
    module passes through here, which makes the rule a property of the
    mechanism rather than of which labels happen to be configured — a label
    added later that sits beside an address still cannot harvest it.
    """
    return bool(value) and bool(_EMAIL.search(str(value)))


# -- adószám ---------------------------------------------------------------

#: Hungarian tax number: 8-digit core, 1-digit VAT code, 2-digit county code.
#: Written as 12345678-1-42, occasionally unseparated.
_ADOSZAM = re.compile(r"\b(\d{8})[-\s]?(\d)[-\s]?(\d{2})\b")

#: The same number but requiring the separators. An adószám and an unseparated
#: Hungarian phone number are both 11 digits, so only the punctuated form is
#: safe to trust from unlabelled free text.
_ADOSZAM_SEPARATED = re.compile(r"\b(\d{8})[-\s](\d)[-\s](\d{2})\b")

#: EU VAT form, which carries only the 8-digit core (HU12345678).
_EU_VAT = re.compile(r"\bHU\s?(\d{8})\b", re.IGNORECASE)

#: Labels that introduce a tax number.
TAX_LABELS: tuple[str, ...] = (
    "adószám",
    "adoszam",
    "adóazonosító szám",
    "közösségi adószám",
    "vat",
)

# -- company name ----------------------------------------------------------

#: Labels that introduce the operating company's registered name.
NAME_LABELS: tuple[str, ...] = (
    "cégnév",
    "cég neve",
    "cegnev",
    "üzemeltető",
    "uzemelteto",
    "szolgáltató",
    "vállalkozás",
    "cégadatok",
)

#: A company name in free text: one to five Title-Case words followed by a
#: Hungarian legal form. Requiring each preceding word to be capitalised is
#: what stops the match running back through the surrounding sentence — in
#: "Az üzletet a Gamma Otthon Bt. üzemelteti" the lowercase "a" ends the run,
#: leaving "Gamma Otthon Bt." rather than the whole clause.
_LEGAL_FORM_RE = re.compile(
    r"(?:[A-ZÁÉÍÓÖŐÚÜŰ][\wÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9&.'’-]*\s+){1,5}"
    r"(?:Kft|Kkt|Bt|Zrt|Nyrt|Kht|E\.?V)\b\.?",
    re.UNICODE,
)

# -- phone -----------------------------------------------------------------

#: Labels that introduce a phone number.
PHONE_LABELS: tuple[str, ...] = (
    "telefon",
    "telefonszám",
    "tel.",
    "tel",
    "mobil",
    "ügyfélszolgálat",
)

#: Hungarian subscriber-number lengths after the +36 country code: 8 for
#: landlines (area code 1 plus 7 digits, or a 2-digit area code plus 6), 9 for
#: mobiles (20/30/31/50/70 plus 7).
_VALID_NATIONAL_LENGTHS = frozenset({8, 9})


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def normalise_phone(raw: str | None) -> str | None:
    """Normalise a Hungarian phone number to E.164, or ``None`` if implausible.

    Accepts the forms merchants actually publish — ``+36 1 234 5678``,
    ``06-20-123-4567``, ``(+36) 30/123-4567`` — and rejects anything whose
    digit count does not match a real Hungarian number, which keeps order
    numbers and tax IDs out of the phone column.

    >>> normalise_phone("06 1 234 5678")
    '+3612345678'
    >>> normalise_phone("+36 30/123-4567")
    '+36301234567'
    """
    if not raw:
        return None

    digits = _digits(raw)
    if not digits:
        return None

    # Strip the international or domestic trunk prefix down to the national
    # number: 0036... , 36... , 06... , or a bare national number.
    if digits.startswith("0036"):
        national = digits[4:]
    elif digits.startswith("36"):
        national = digits[2:]
    elif digits.startswith("06"):
        national = digits[2:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        national = digits

    if len(national) not in _VALID_NATIONAL_LENGTHS:
        return None
    return f"+36{national}"


def normalise_tax_number(
    raw: str | None, *, require_separators: bool = False
) -> str | None:
    """Normalise an adószám to canonical ``12345678-1-42`` form.

    Also accepts the EU VAT form (``HU12345678``), which carries only the
    8-digit core; that is returned as-is since the VAT and county digits are
    genuinely absent rather than guessable.

    Args:
        raw: Text to read the number out of.
        require_separators: Only accept the punctuated ``12345678-1-42`` form.
            Set this when scanning unlabelled text: an adószám and an
            unseparated Hungarian phone number are both 11 digits, so
            ``06301234567`` would otherwise be read as ``06301234-5-67``. A
            label makes the number unambiguous, so labelled lookups leave this
            off and still accept the run-together form.

    >>> normalise_tax_number("Adószám: 12345678-1-42")
    '12345678-1-42'
    >>> normalise_tax_number("HU12345678")
    '12345678'
    >>> normalise_tax_number("06301234567", require_separators=True) is None
    True
    """
    if not raw:
        return None

    pattern = _ADOSZAM_SEPARATED if require_separators else _ADOSZAM
    match = pattern.search(raw)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    eu = _EU_VAT.search(raw)
    if eu:
        return eu.group(1)
    return None


# -- label-driven lookup ---------------------------------------------------


def _next_element(node: Node) -> Node | None:
    """Return the next sibling that is an element, skipping text nodes."""
    sibling = node.next
    while sibling is not None:
        if sibling.tag and not sibling.tag.startswith("-"):
            return sibling
        sibling = sibling.next
    return None


def _label_prefix_len(text: str, label: str) -> int | None:
    """Length of ``label`` where it prefixes ``text`` as a whole word, else None.

    A bare ``startswith`` is wrong here: "Település" starts with the "tel"
    label, so a settlement row would be answered as the phone row and shadow
    the real one — the first match wins, and the caller then discards
    "Budapest" as an unparseable number, losing the phone entirely. Requiring
    the next character to be non-alphabetic keeps "Telefon", "Telefon:" and
    "Tel." matching while rejecting "Település".
    """
    if not text.startswith(label):
        return None
    rest = text[len(label) :]
    if rest and rest[0].isalpha():
        return None
    return len(label)


def _matches_label(text: str, labels: Iterable[str]) -> bool:
    lowered = text.strip().lower().rstrip(":").strip()
    return any(_label_prefix_len(lowered, lab) is not None for lab in labels)


def find_labelled_value(tree: HTMLParser, labels: Iterable[str]) -> str | None:
    """Return the value sitting next to one of ``labels``.

    Handles the three shapes company details are published in: a definition
    list (``dt``/``dd``), a table row (``th``/``td``), and a single node
    reading ``"Label: value"``.
    """
    labels = tuple(lab.lower() for lab in labels)

    # dt/dd and th/td pairs.
    for tag in ("dt", "th"):
        for node in tree.css(tag):
            if _matches_label(node.text(strip=True), labels):
                value = _next_element(node)
                if value is not None:
                    text = value.text(strip=True)
                    if text and not contains_email(text):
                        return text

    # "Label: value" inside one node. Scan leaf-ish nodes so the match is the
    # tightest container, not a wrapper holding the whole page.
    for node in tree.css("dd, td, li, p, span, div"):
        text = node.text(strip=True)
        if not text or len(text) > 200:
            continue
        for label in labels:
            width = _label_prefix_len(text.lower(), label)
            if width is not None:
                remainder = text[width:].lstrip(" : \t")
                if remainder and not contains_email(remainder):
                    return remainder
    return None


# -- field extractors ------------------------------------------------------


def extract_tax_number(tree: HTMLParser) -> str | None:
    """Return the store's adószám, or ``None`` when the page has none.

    Tries the labelled value first, where the label disambiguates the number
    and the run-together form is safe to accept. The whole-page fallback then
    demands the punctuated form, because an unseparated adószám is
    indistinguishable from a Hungarian phone number — both are 11 digits, and
    a ``tel:`` link would otherwise be harvested as a tax number.
    """
    labelled = find_labelled_value(tree, TAX_LABELS)
    if labelled:
        normalised = normalise_tax_number(labelled)
        if normalised:
            return normalised

    return normalise_tax_number(tree.text(), require_separators=True)


def extract_phone(tree: HTMLParser) -> str | None:
    """Return the store's phone number in E.164, or ``None``.

    A ``tel:`` link is authoritative when present; otherwise fall back to the
    labelled value.
    """
    for anchor in tree.css("a[href^='tel:']"):
        href = anchor.attributes.get("href") or ""
        phone = normalise_phone(href.split(":", 1)[-1])
        if phone:
            return phone

    return normalise_phone(find_labelled_value(tree, PHONE_LABELS))


def extract_legal_name(tree: HTMLParser) -> str | None:
    """Return the operating company's registered name, or ``None``.

    This is the entity behind the shop, which routinely differs from the
    storefront name already captured from the listing — the storefront may be
    "AlfaShop" while the invoice says "Alfa Kereskedelmi Kft.".
    """
    labelled = find_labelled_value(tree, NAME_LABELS)
    if labelled:
        match = _LEGAL_FORM_RE.search(labelled)
        return (match.group(0) if match else labelled).strip(" ,")

    match = _LEGAL_FORM_RE.search(tree.text())
    return match.group(0).strip(" ,") if match else None
