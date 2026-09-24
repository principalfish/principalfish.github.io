"""Helpers shared by the Wikipedia-scraping poll importers.

Wikipedia opinion-polling pages back more than one importer, and each needs the
same three primitives: fetch the page HTML, collapse the whitespace out of a
table cell, and turn a fieldwork date-range label into real dates.

The date grammar here is the **UK day-first** one (``"28 Jan - 3 Feb 2026"``).
US pages use a month-first grammar with its own parser in
``polls/importers/us/us_polls_common.py`` — the two are deliberately separate.
"""

from __future__ import annotations

import re
from datetime import date
from http.client import IncompleteRead
from urllib.request import Request, urlopen

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; poll-importer/1.0)"

# Hard ceiling on one response body. MediaWiki caps a page's expanded wikitext
# at ~2 MB (the post-expand include size), and the rendered HTML of a
# table-heavy polling article runs a small multiple of that, so no real
# Wikipedia article reaches 8 MiB. The biggest US race page (as of 2026-09) is
# ~1 MB, so this is at least 8x headroom, and it also covers the UK Westminster
# polling index (the largest article this repo reads). It turns an unbounded
# response body into a fixed per-request limit.
MAX_PAGE_BYTES = 8 * 1024 * 1024

_MONTH_MAP: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


class PageTooLargeError(ValueError):
    """Raised when a fetched page exceeds the configured byte limit."""


def fetch_html(
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = 60,
    max_bytes: int = MAX_PAGE_BYTES,
) -> str:
    """Fetch HTML content from ``url`` using a browser-like User-Agent.

    Args:
        url: Fully-qualified URL to fetch.
        user_agent: Value of the ``User-Agent`` request header. Wikipedia
            rejects requests that do not send one.
        timeout: Socket timeout in seconds, passed to ``urlopen``.
        max_bytes: Largest response body accepted, in bytes. At most
            ``max_bytes + 1`` bytes are read, so an oversized page is never
            buffered in full.

    Returns:
        UTF-8 decoded response body, with undecodable bytes replaced.

    Raises:
        urllib.error.URLError: If the request fails.
        http.client.IncompleteRead: If the connection closed before the
            ``Content-Length`` promised had arrived.
        PageTooLargeError: If the response body exceeds ``max_bytes``.
    """
    req = Request(url, headers={"User-Agent": user_agent})
    with urlopen(req, timeout=timeout) as response:
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            limit_mib = max_bytes / (1024 * 1024)
            raise PageTooLargeError(
                f"{url} returned more than {limit_mib:g} MiB; not read",
            )
        # A bounded read(n) returns a cut-off body without complaint (unlike a
        # bare read()), so check the response is not still owed bytes; else a
        # dropped connection would parse as a page with its later polls missing.
        remaining = getattr(response, "length", None)
        if remaining:
            raise IncompleteRead(raw, remaining)
        body: str = raw.decode("utf-8", errors="replace")
    return body


def clean_text(value: str) -> str:
    """Collapse runs of whitespace in ``value`` to single spaces and strip it.

    Table cells scraped from Wikipedia carry newlines and non-breaking spaces
    from the source markup; this normalises them for comparison and display.

    Args:
        value: Raw text, typically from ``Tag.get_text()``.

    Returns:
        The whitespace-normalised, stripped string.
    """
    return re.sub(r"\s+", " ", value).strip()


def parse_date_range(raw: str) -> tuple[date, date] | None:
    """Parse a fieldwork date-range string into ``(start, end)`` date objects.

    Handles three formats:
    - Same-month range: ``"1–3 Feb 2026"``
    - Cross-month range: ``"28 Jan – 3 Feb 2026"``
    - Single day: ``"3 Feb 2026"``

    En-dashes (–) and em-dashes (—) are normalised to hyphens before matching.

    Args:
        raw: Raw date string extracted from a Wikipedia table cell.

    Returns:
        ``(start, end)`` tuple of :class:`datetime.date` objects, or ``None``
        if the string cannot be parsed.
    """
    text = re.sub(r"[–—]", "-", raw).strip()
    text = re.sub(r"\s+", " ", text)

    # Cross-month: "28 Jan - 3 Feb 2026"
    cross = re.match(
        r"(\d{1,2})\s+([A-Za-z]+)\s*-\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        text,
    )
    if cross:
        d1, m1_str, d2, m2_str, yr_str = cross.groups()
        month1 = _MONTH_MAP.get(m1_str.lower())
        month2 = _MONTH_MAP.get(m2_str.lower())
        if month1 and month2:
            year = int(yr_str)
            year1 = year if month1 <= month2 else year - 1
            try:
                return date(year1, month1, int(d1)), date(year, month2, int(d2))
            except ValueError:
                return None

    # Same-month range: "1-3 Feb 2026"
    same = re.match(
        r"(\d{1,2})\s*-\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        text,
    )
    if same:
        d1, d2, mon_str, yr_str = same.groups()
        month = _MONTH_MAP.get(mon_str.lower())
        if month:
            year = int(yr_str)
            try:
                return date(year, month, int(d1)), date(year, month, int(d2))
            except ValueError:
                return None

    # Single day: "3 Feb 2026"
    single = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if single:
        d, mon_str, yr_str = single.groups()
        month = _MONTH_MAP.get(mon_str.lower())
        if month:
            try:
                d_obj = date(int(yr_str), month, int(d))
                return d_obj, d_obj
            except ValueError:
                return None

    return None
