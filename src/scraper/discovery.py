"""DOM structure discovery for deriving real selectors from a live page.

The selector candidates in :mod:`src.scraper.config` were written without
access to the live arukereso.hu markup. This module closes that gap: point it
at the real page from a machine that can reach the site and it reports the
repeating card container and the class names inside it, ready to paste into
``SELECTORS``.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Iterable, NamedTuple

from selectolax.parser import HTMLParser, Node

from src.scraper.parsing import is_store_url

logger = logging.getLogger(__name__)

#: How far up from a store link to look for the repeating card container.
_MAX_ANCESTOR_DEPTH = 6

#: Signatures never worth proposing as a card selector.
_IGNORED_TAGS = frozenset({"html", "body", "head", "-undef", "-text", "_comment"})


class Candidate(NamedTuple):
    """A proposed card selector and how well it partitions the store links."""

    selector: str
    match_count: int
    links_covered: int
    one_link_each: bool

    @property
    def is_exact(self) -> bool:
        """True when each match wraps exactly one store link."""
        return self.one_link_each and self.match_count == self.links_covered


def _signature(node: Node) -> str | None:
    """Return a CSS selector for a node, or ``None`` if it is not usable."""
    tag = node.tag
    if not tag or tag in _IGNORED_TAGS:
        return None

    classes = [c for c in (node.attributes.get("class") or "").split() if c]
    if classes:
        return tag + "." + ".".join(classes[:3])
    for attr in ("data-store-id", "itemtype"):
        if attr in node.attributes:
            return f"{tag}[{attr}]"
    return None


def _ancestors(node: Node, depth: int) -> Iterable[Node]:
    """Yield up to ``depth`` ancestors of ``node``, nearest first."""
    current = node.parent
    for _ in range(depth):
        if current is None:
            return
        yield current
        current = current.parent


def _store_links(tree: HTMLParser) -> list[Node]:
    """Return every anchor whose href looks like a store profile."""
    return [
        a for a in tree.css("a[href]") if is_store_url(a.attributes.get("href") or "")
    ]


def _rank_candidates(tree: HTMLParser, link_count: int) -> list[Candidate]:
    """Score each candidate signature by running it as a real CSS selector.

    A signature that ties the true card container (``body``, a wrapping
    ``section``) is separated out here: the winning selector is the one whose
    match count equals the number of store links *and* whose every match
    contains exactly one of them.
    """
    signatures: set[str] = set()
    for link in _store_links(tree):
        for ancestor in _ancestors(link, _MAX_ANCESTOR_DEPTH):
            sig = _signature(ancestor)
            if sig:
                signatures.add(sig)

    candidates: list[Candidate] = []
    for selector in signatures:
        try:
            matches = tree.css(selector)
        except Exception:
            # A derived signature the parser cannot compile is not worth
            # aborting discovery for, but swallowing it silently would hide a
            # real defect in _signature().
            logger.debug("Skipping uncompilable candidate selector %r", selector)
            continue
        if not matches:
            continue

        per_match = [len(_store_links(m)) for m in matches]
        covered = sum(per_match)
        if covered == 0:
            continue
        candidates.append(
            Candidate(
                selector=selector,
                match_count=len(matches),
                links_covered=covered,
                one_link_each=all(n == 1 for n in per_match),
            )
        )

    # Exact partitions first, then whichever covers the most links with the
    # match count closest to the link total.
    return sorted(
        candidates,
        key=lambda c: (
            not c.is_exact,
            abs(c.match_count - link_count),
            -c.links_covered,
        ),
    )


def discover(html: str) -> str:
    """Return a human-readable report of the page's store-listing structure."""
    tree = HTMLParser(html)
    lines: list[str] = ["=" * 72, "DOM DISCOVERY REPORT", "=" * 72, ""]

    links = _store_links(tree)
    lines.append(f"Anchors matching a store-URL pattern: {len(links)}")

    if not links:
        lines += [
            "",
            "No store-shaped links found. Either the listing renders after load",
            "(raise --timeout or pass --wait-for), or the profile URL pattern",
            "differs from STORE_URL_MARKERS in config.py.",
            "",
            "Most common link path prefixes on this page:",
        ]
        prefixes = Counter(
            "/".join((a.attributes.get("href") or "").split("/")[:3])
            for a in tree.css("a[href]")
        )
        lines += [
            f"  {count:>4}x  {prefix}" for prefix, count in prefixes.most_common(15)
        ]
        return "\n".join(lines)

    ranked = _rank_candidates(tree, len(links))

    lines += ["", "Candidate card containers (best first):", ""]
    if ranked:
        for cand in ranked[:10]:
            note = "  <-- one store per match" if cand.is_exact else ""
            lines.append(
                f"  {cand.match_count:>4} matches, {cand.links_covered:>4} links  "
                f"{cand.selector}{note}"
            )
    else:
        lines.append(
            "  (no repeating container found; the heuristic fallback "
            "in parsing.py will be used)"
        )

    # Show the winning candidate's first match so field selectors can be read
    # off a real card rather than a guessed ancestor.
    card: Node | None = None
    if ranked:
        matches = tree.css(ranked[0].selector)
        card = matches[0] if matches else None
    if card is None:
        card = links[0].parent or links[0]

    lines += ["", "-" * 72, "Sample card HTML (trimmed to 2000 chars):", "-" * 72, ""]
    sample = card.html or ""
    lines.append(sample[:2000] + ("\n... [truncated]" if len(sample) > 2000 else ""))

    lines += ["", "-" * 72, "Class names inside that card:", "-" * 72, ""]
    inner: Counter[str] = Counter()
    for node in card.css("*"):
        for cls in (node.attributes.get("class") or "").split():
            inner[f"{node.tag}.{cls}"] += 1
    lines += [f"  {count:>3}x  {name}" for name, count in inner.most_common(30)] or [
        "  (none)"
    ]

    lines += [
        "",
        "-" * 72,
        "schema.org microdata in that card (usually the most stable selectors):",
        "-" * 72,
        "",
    ]
    micro = [
        f"  [itemprop='{n.attributes.get('itemprop')}']  <{n.tag}>  "
        f"{((n.attributes.get('content') or n.text(strip=True)) or '')[:60]}"
        for n in card.css("[itemprop]")
    ]
    lines += micro or ["  (none found)"]

    lines += [
        "",
        "=" * 72,
        "Paste the winning selectors into SELECTORS in src/scraper/config.py.",
        "=" * 72,
    ]
    return "\n".join(lines)
