"""Tests for the YouGov PDF poll importer.

Covers the pure parsing helpers, ``extract_pdf_text``'s tempfile handling,
``build_import_plan``, ``_cli_preview`` and ``main``. ``commit_import_plan`` and
``_find_existing_poll`` are covered for every Westminster importer, this one
included, by ``test_westminster_importers_commit.py``.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from db import Database
from polls.importers.westminster import yougov_import
from polls.importers.westminster.yougov_import import (
    DEFAULT_MAP_NAME,
    DEFAULT_PDF_URL,
    DEFAULT_POLLSTER_IDENTIFIER,
    MACRO_TO_INTERNAL_REGIONS,
    PARTY_NAME_MAP,
    ImportPlan,
    ParsedPoll,
    PlannedPollRow,
    _cli_preview,
    _normalize_percentage,
    _parse_new_format,
    _parse_old_format_rows,
    build_import_plan,
    extract_pdf_text,
    main,
    normalize_name,
    parse_fieldwork,
    parse_headline_vi_table,
    parse_poll,
)
from tests.uk_fixtures import FakeUrlResponse, WestminsterWorld, add_poll_with_rows

# ── Local fakes and helpers ───────────────────────────────────────────────────


@dataclass(slots=True)
class _FakePdfPage:
    """A pypdf page stand-in returning a fixed ``extract_text()`` result."""

    text: str | None

    def extract_text(self) -> str | None:
        """Return the fixed page text (``None`` mimics an unreadable page)."""
        return self.text


class _FakePdfReader:
    """A pypdf ``PdfReader`` stand-in serving fixed page texts."""

    def __init__(self, page_texts: list[str | None]) -> None:
        self.pages = [_FakePdfPage(text) for text in page_texts]


def _patch_pdf_fetch(
    monkeypatch: pytest.MonkeyPatch,
    page_texts: list[str | None],
    payload: bytes = b"pdf-bytes",
) -> list[tuple[str, float | None]]:
    """Monkeypatch the module's ``urlopen`` and ``PdfReader`` to serve fixed text.

    Returns the list of ``(url, timeout)`` pairs every ``urlopen`` call is
    recorded into, so a caller that cares which URL was fetched can assert on it.
    """
    calls: list[tuple[str, float | None]] = []

    def _urlopen(
        url: str, timeout: float | None = None, **_kwargs: object
    ) -> FakeUrlResponse:
        calls.append((url, timeout))
        return FakeUrlResponse(payload)

    monkeypatch.setattr(yougov_import, "urlopen", _urlopen)
    monkeypatch.setattr(
        yougov_import, "PdfReader", lambda _path: _FakePdfReader(page_texts)
    )
    return calls


def _forbid_pdf_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch ``urlopen`` to fail loudly if the PDF fetch is ever reached.

    Used by tests whose expected raise should fire before any fetch, so the
    test stays a true regression guard if the code is later reordered.
    """

    def _urlopen(*_a: object, **_k: object) -> FakeUrlResponse:
        raise AssertionError("build_import_plan should not fetch the PDF here")

    monkeypatch.setattr(yougov_import, "urlopen", _urlopen)


def _expected_regions_mapping(region_ids: Mapping[str, int]) -> str:
    """Build the expected ``regions_mapping`` string from named region ids.

    Spells out the six macro -> internal-region associations by hand (matching
    ``MACRO_TO_INTERNAL_REGIONS``'s documented shape) instead of reading that
    dict, so a mutated production mapping is caught here rather than mirrored.
    """
    return "\n".join(
        [
            "North:{},{},{}".format(
                region_ids["North East England"],
                region_ids["North West England"],
                region_ids["Yorkshire and The Humber"],
            ),
            "Midlands:{},{}".format(
                region_ids["East Midlands"], region_ids["West Midlands"]
            ),
            f"London:{region_ids['London']}",
            "Rest of South:{},{},{}".format(
                region_ids["East of England"],
                region_ids["South East England"],
                region_ids["South West England"],
            ),
            f"Wales:{region_ids['Wales']}",
            f"Scotland:{region_ids['Scotland']}",
        ]
    )


# 8 canonical parties (PARTY_NAME_MAP's unique values: Conservative, Labour,
# Liberal Democrats, Scottish National Party, Plaid Cymru, Reform UK, Green,
# Other) x 11 internal regions (3 North + 2 Midlands + 1 London + 3 Rest of
# South + 1 Wales + 1 Scotland).
_EXPECTED_ROW_COUNT = 88

# Trimmed pypdf extractions of real YouGov PDFs. Each layout keeps the page-1
# column header, the MRP headline rows and the unlabelled region table, whose
# rows line up with the headline rows — Your Party and Restore Britain included.

# 13-14 Sep 2026: lower-case "Sample size", no country columns on page 1, and a
# seven-column region table led by England, Wales and Scotland.
SEPT_2026_TEXT = """\
YouGov Survey Results
Sample size: 2149 adults in GB
Fieldwork: 13th - 14th September 2026
Total Con Lab Lib
Dem
Reform
UK Green Remain Leave Male Female 18-24 25-49 50-64 65+ Higher Intermediate Routine
% % % % % % % % % % % % % % % % %
HEADLINE VOTING INTENTION
Westminster Voting Intention
[Headline voting intention from constituency vote
projected by YouGov MRP model]
Con 20 20 65 5 8 9 5 18 27 19 21 7 12 24 27 22 20 21
Lab 23 23 3 61 10 0 16 33 11 24 23 31 30 23 16 28 20 20
Lib Dem 11 13 4 8 66 1 8 19 5 10 16 19 13 13 11 14 16 9
SNP 3 3 0 1 0 0 3 4 2 4 3 1 4 3 2 1 4 5
Plaid Cymru 1 1 0 2 1 0 0 2 1 1 2 1 1 2 1 1 2 1
Reform UK 23 23 25 9 4 77 6 6 42 28 19 5 18 23 31 19 22 31
Green 12 11 1 13 8 1 63 15 4 10 13 33 16 8 5 11 11 7
Your Party 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
Restore Britain 4 3 1 2 1 11 0 2 5 4 2 1 4 3 3 2 3 5
Other 2 1 1 0 3 1 0 1 2 1 2 2 1 1 2 1 2 1
If there were a general election held tomorrow,
which party would you vote for?
Conservative 14 14 55 4 7 9 2 13 22 14 14 7 7 18 24 17 14 12
Refused 4 3 3 1 2 1 0 2 2 3 4 5 5 1 1 3 3 4
1 © 2026 YouGov plc. All Rights Reserved www.yougov.com
YouGov Survey Results
Sample size: 2149 adults in GB
Fieldwork: 13th - 14th September 2026
HEADLINE VOTING INTENTION
Westminster Voting Intention
Con 20 20
Other 2 1
If there were a general election held tomorrow,
which party would you vote for?
Refused 4 3
England Wales Scotland North Midlands London Rest of
South
1859 103 187 509 352 260 737
1865 121 163 500 360 246 759
% % % % % % %
22 10 9 17 25 22 24
24 18 18 32 23 32 17
14 5 7 8 8 17 20
0 0 35 0 0 0 0
0 26 0 0 0 0 0
23 27 19 24 28 12 24
12 7 8 13 12 14 11
0 0 0 0 0 0 0
3 7 3 3 4 1 3
2 0 1 2 1 2 1
15 8 7 10 16 15 18
Country Region in England
2 © 2026 YouGov plc. All Rights Reserved www.yougov.com
Now, thinking specifically about your own
constituency, if there were a general election
"""

# 23-24 Aug 2026: page 1 ends in the country columns, and the region table holds
# the four England regions only.
AUG_2026_TEXT = """\
YouGov Survey Results
Sample Size: 2380 GB Adults
Fieldwork: 23rd - 24th August 2026
Total Con Lab Lib
Dem
Reform
UK Green Remain Leave Male Female 18-24 25-49 50-64 65+ Higher Intermediate Routine England Wales Scotland
% % % % % % % % % % % % % % % % % % % %
HEADLINE VOTING INTENTION
Westminster Voting Intention
[Headline voting intention from constituency vote
projected by YouGov MRP model]
Con 19 20 63 6 7 8 6 17 25 19 20 5 13 19 30 23 19 19 21 10 7
Lab 22 22 4 57 12 2 15 37 9 22 23 25 26 21 19 28 24 14 23 18 20
Lib Dem 12 13 4 11 63 2 3 18 8 12 14 15 15 13 12 15 13 11 14 5 6
SNP 3 3 0 1 0 0 0 4 1 3 3 4 2 3 2 2 2 4 0 0 29
Plaid Cymru 1 1 1 1 0 0 1 2 1 2 1 4 2 1 1 1 2 1 0 30 0
Reform UK 24 23 25 9 5 74 3 5 44 27 19 4 15 28 29 18 22 32 22 28 21
Green 13 13 2 14 11 1 68 15 6 11 15 41 21 9 5 9 12 13 14 7 11
Your Party 0 0 0 1 0 0 0 1 0 0 0 0 0 0 0 1 0 0 0 0 0
Restore Britain 4 3 1 0 1 11 2 1 4 3 2 0 4 3 1 1 2 5 3 2 5
Other 2 2 1 0 1 2 2 1 2 2 2 1 2 2 1 2 2 1 2 0 1
If there were a general election held tomorrow,
which party would you vote for?
Conservative 12 14 55 5 7 9 1 14 20 14 14 6 10 14 25 19 14 12 15 10 9
Refused 3 4 2 3 4 1 4 3 2 4 4 8 5 3 1 2 3 5 4 3 5
1 © 2026 YouGov plc. All Rights Reserved www.yougov.com
YouGov Survey Results
Sample Size: 2380 GB Adults
Fieldwork: 23rd - 24th August 2026
HEADLINE VOTING INTENTION
Westminster Voting Intention
Con 19 20
Other 2 2
If there were a general election held tomorrow,
which party would you vote for?
Refused 3 4
North Midlands London Rest of
South
564 390 288 816
545 404 253 828
% % % %
17 24 20 24
31 25 31 15
7 11 14 21
0 0 0 0
0 0 0 0
26 23 14 22
13 14 17 13
1 0 0 0
3 2 1 3
2 1 3 1
10 17 14 16
Region in England
2 © 2026 YouGov plc. All Rights Reserved www.yougov.com
Now, thinking specifically about your own
constituency, if there were a general election
"""

# 6-7 Apr 2026: a single cross-tab whose last six columns are the regions.
OLD_FORMAT_TEXT = """\
YouGov Survey Results
Sample Size: 2320 GB adults
Fieldwork: 6th - 7th April 2026
Total Con Lab Lib
Dem
Reform
UK Green Remain Leave Male Female 18-24 25-49 50-64 65+ Higher Intermediate Routine England Wales Scotland North Midlands London Rest of
South
HEADLINE VOTING INTENTION
Westminster Voting Intention
[Headline voting intention projected by YouGov MRP model]
Con 19 61 6 9 5 5 16 24 16 21 9 14 16 33 22 20 18 20 16 11 17 22 16 22
Lab 16 2 44 5 1 4 25 6 17 16 17 19 16 11 19 15 13 17 14 14 18 17 29 12
Lib Dem 13 4 15 63 2 3 19 8 13 14 14 15 10 14 16 13 13 14 8 9 10 9 12 20
SNP 3 0 1 1 0 0 4 1 3 2 2 3 4 3 3 3 3 0 0 33 0 0 0 0
Plaid Cymru 1 1 1 2 0 1 2 1 1 2 1 2 1 0 1 2 1 0 27 0 0 0 0 0
Reform UK 24 28 9 4 75 9 8 45 29 19 7 18 34 28 17 26 34 25 17 15 29 27 16 25
Green 16 1 20 13 0 74 20 6 12 20 44 19 12 7 16 14 9 17 12 11 18 20 20 14
Your Party 1 0 0 2 0 1 1 0 1 1 0 0 2 1 1 1 0 1 0 1 1 1 3 0
Restore Britain 4 1 2 1 15 2 2 6 5 4 2 7 4 2 2 4 7 4 5 2 5 3 2 5
Other 2 1 3 1 2 2 3 2 3 1 3 2 3 1 1 3 2 2 1 4 2 2 2 2
If there were a general election held tomorrow, which party
would you vote for?
Now, thinking specifically about your own
constituency, if there were a general election
"""


# 4-5 Oct 2026: one combined cross-tab, including previous-poll totals,
# later direct-question answers and a repeated full header on page 2.
OCT_2026_TEXT = """\
YouGov Survey Results
Sample size: 2312 adults in GB
Fieldwork: 4th - 5th October 2026
Total Con Lab Lib
Dem
Reform
UK Green Remain Leave Male Female 18-24 25-49 50-64 65+ Higher Intermediate Routine England Wales Scotland North Midlands London Rest of
South
Weighted Sample 2312 416 594 213 250 131 786 832 1117 1195 243 953 571 546 784 518 691 2000 111 201 548 379 280 793
Unweighted Sample 2312 340 636 223 253 133 944 747 1014 1298 179 860 657 616 958 504 522 1970 136 206 553 377 218 822
% % % % % % % % % % % % % % % % % % % % % % % %
27 - 28
Sep
4 - 5
Oct
HEADLINE VOTING INTENTION
Westminster Voting Intention
[Headline voting intention from
constituency vote projected by YouGov
MRP model]
Con 19 20 57 5 13 10 5 18 25 18 21 4 15 21 26 24 19 17 21 12 11 17 25 21 21
Lab 24 27 5 66 13 1 21 40 13 28 26 40 31 26 22 32 29 19 28 19 22 38 27 34 20
Lib Dem 12 11 4 7 60 1 6 17 5 7 15 11 11 12 11 13 15 7 12 4 11 6 6 11 19
SNP 2 3 0 0 0 0 0 3 2 3 2 2 1 2 4 2 2 5 0 0 28 0 0 0 0
Plaid Cymru 1 1 0 1 0 0 1 2 1 2 1 2 1 1 1 2 1 1 0 25 0 0 0 0 0
Reform UK 25 22 29 7 3 73 3 6 41 27 18 4 16 24 29 15 25 32 22 26 15 22 26 11 25
Green 11 11 1 11 10 0 61 11 4 9 12 36 16 7 4 9 7 9 11 10 8 11 8 20 10
Your Party 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
Restore Britain 3 3 2 2 0 13 0 1 6 4 3 1 4 6 1 2 1 7 3 5 3 3 3 1 4
Other 2 2 1 1 0 2 4 1 3 3 1 0 2 2 2 2 1 3 2 0 1 3 4 2 0
If there were a general election held
tomorrow, which party would you
vote for?
Conservative 14 13 48 3 8 10 5 13 19 13 13 2 9 17 20 18 13 10 14 10 7 12 16 13 14
Labour 21 24 5 61 17 1 15 38 13 26 21 31 24 23 21 29 24 20 24 24 21 28 25 32 18
Liberal Democrat 7 6 1 4 36 0 6 10 3 5 7 8 5 5 7 7 7 4 6 4 6 3 4 6 9
Scottish National Party (SNP) 2 2 0 0 0 0 0 3 1 2 2 1 1 2 4 1 1 4 0 0 23 0 0 0 0
Plaid Cymru 1 1 0 1 0 0 0 1 1 1 0 1 1 1 1 1 1 1 0 14 0 0 0 0 0
Reform UK 16 15 23 4 3 64 3 4 32 19 12 2 11 19 22 10 19 21 16 17 8 16 16 9 18
Green 10 8 1 8 9 0 48 9 3 7 9 23 9 4 2 7 5 7 8 5 6 8 6 15 7
Some other party 3 4 1 2 0 12 2 2 6 5 3 2 5 4 2 2 3 6 4 4 3 4 4 2 4
Would not vote 10 10 3 3 4 1 3 4 6 9 12 12 16 6 4 8 10 10 10 11 10 12 11 7 10
Don't know 13 14 16 12 17 10 15 14 15 10 17 7 13 16 16 14 15 13 14 10 14 12 14 12 16
Refused 4 4 1 3 5 1 3 3 2 3 5 9 6 2 1 3 3 5 4 2 2 4 5 4 4
Country Region in EnglandVote in 2024 GE EU Ref 2016 Gender Age Socio-economic classification (NS-
1 © 2026 YouGov plc. All Rights Reserved www.yougov.com
Sample size: 2312 adults in GB
Fieldwork: 4th - 5th October 2026
Total Con Lab Lib
Dem
Reform
UK Green Remain Leave Male Female 18-24 25-49 50-64 65+ Higher Intermediate Routine England Wales Scotland North Midlands London Rest of
South
Weighted Sample 2312 416 594 213 250 131 786 832 1117 1195 243 953 571 546 784 518 691 2000 111 201 548 379 280 793
Unweighted Sample 2312 340 636 223 253 133 944 747 1014 1298 179 860 657 616 958 504 522 1970 136 206 553 377 218 822
% % % % % % % % % % % % % % % % % % % % % % % %
Country Region in EnglandVote in 2024 GE EU Ref 2016 Gender Age Socio-economic classification (NS-
Now, thinking specifically about
"""


OCT_2026_EXPECTED_SHARES = {
    "Conservative": {
        "Wales": 12.0,
        "Scotland": 11.0,
        "North": 17.0,
        "Midlands": 25.0,
        "London": 21.0,
        "Rest of South": 21.0,
    },
    "Labour": {
        "Wales": 19.0,
        "Scotland": 22.0,
        "North": 38.0,
        "Midlands": 27.0,
        "London": 34.0,
        "Rest of South": 20.0,
    },
    "Liberal Democrats": {
        "Wales": 4.0,
        "Scotland": 11.0,
        "North": 6.0,
        "Midlands": 6.0,
        "London": 11.0,
        "Rest of South": 19.0,
    },
    "Scottish National Party": {
        "Wales": 0.0,
        "Scotland": 28.0,
        "North": 0.0,
        "Midlands": 0.0,
        "London": 0.0,
        "Rest of South": 0.0,
    },
    "Plaid Cymru": {
        "Wales": 25.0,
        "Scotland": 0.0,
        "North": 0.0,
        "Midlands": 0.0,
        "London": 0.0,
        "Rest of South": 0.0,
    },
    "Reform UK": {
        "Wales": 26.0,
        "Scotland": 15.0,
        "North": 22.0,
        "Midlands": 26.0,
        "London": 11.0,
        "Rest of South": 25.0,
    },
    "Green": {
        "Wales": 10.0,
        "Scotland": 8.0,
        "North": 11.0,
        "Midlands": 8.0,
        "London": 20.0,
        "Rest of South": 10.0,
    },
    "Other": {
        "Wales": 0.0,
        "Scotland": 1.0,
        "North": 3.0,
        "Midlands": 4.0,
        "London": 2.0,
        "Rest of South": 0.0,
    },
}


# ── normalize_name ────────────────────────────────────────────────────────────


class TestNormalizeName:
    """Tests for normalize_name — region/party name normalisation."""

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert normalize_name("  Scotland  ") == "scotland"

    def test_collapses_internal_whitespace(self) -> None:
        assert normalize_name("North  West  England") == "north west england"

    def test_lowercases(self) -> None:
        assert normalize_name("SCOTLAND") == "scotland"

    def test_tabs_treated_as_whitespace(self) -> None:
        assert normalize_name("North\tWest") == "north west"

    def test_already_normalised_unchanged(self) -> None:
        assert normalize_name("london") == "london"

    def test_empty_string(self) -> None:
        assert normalize_name("") == ""


# ── parse_fieldwork ───────────────────────────────────────────────────────────


class TestParseFieldwork:
    """Tests for parse_fieldwork — YouGov PDF fieldwork date string parsing."""

    def test_same_month(self) -> None:
        start, end = parse_fieldwork("3-7 February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_cross_month(self) -> None:
        start, end = parse_fieldwork("28 January - 3 February 2025")
        assert start == date(2025, 1, 28)
        assert end == date(2025, 2, 3)

    def test_cross_year(self) -> None:
        start, end = parse_fieldwork("30 December - 2 January 2026")
        assert start == date(2025, 12, 30)
        assert end == date(2026, 1, 2)

    def test_en_dash_normalised(self) -> None:
        start, end = parse_fieldwork("3–7 February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_fieldwork_prefix_present(self) -> None:
        # As extracted from PDF: "Fieldwork: 3-7 February 2025"
        start, end = parse_fieldwork("Fieldwork: 3-7 February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_ordinal_suffix_present(self) -> None:
        start, end = parse_fieldwork("3rd-7th February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            parse_fieldwork("not a date at all")

    def test_unknown_end_month_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown month name"):
            parse_fieldwork("3-7 Notamonth 2025")

    def test_unknown_start_month_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown month name"):
            parse_fieldwork("28 Notamonth - 3 February 2025")


# ── _normalize_percentage ─────────────────────────────────────────────────────


class TestNormalizePercentage:
    """Tests for _normalize_percentage — round to nearest integer as float."""

    def test_rounds_up(self) -> None:
        assert _normalize_percentage(34.6) == pytest.approx(35.0)

    def test_rounds_down(self) -> None:
        assert _normalize_percentage(34.4) == pytest.approx(34.0)

    def test_exact_integer(self) -> None:
        assert _normalize_percentage(42.0) == pytest.approx(42.0)

    def test_returns_float(self) -> None:
        result = _normalize_percentage(30.0)
        assert isinstance(result, float)

    def test_rounds_half_to_even(self) -> None:
        # Python's built-in round() uses banker's rounding: 34.5 → 34 (nearest even)
        assert _normalize_percentage(34.5) == pytest.approx(34.0)


# ── parse_poll ────────────────────────────────────────────────────────────────


class TestParsePoll:
    """Tests for parse_poll — sample size and fieldwork from the PDF header."""

    def test_lower_case_sample_size_label(self) -> None:
        parsed = parse_poll(SEPT_2026_TEXT)
        assert parsed.sample_size == 2149
        assert parsed.fieldwork_start == date(2026, 9, 13)
        assert parsed.fieldwork_end == date(2026, 9, 14)

    def test_title_case_sample_size_label(self) -> None:
        parsed = parse_poll(AUG_2026_TEXT)
        assert parsed.sample_size == 2380
        assert parsed.fieldwork_end == date(2026, 8, 24)

    def test_missing_sample_size_raises(self) -> None:
        text = SEPT_2026_TEXT.replace("Sample size: 2149", "Respondents: 2149")
        with pytest.raises(ValueError, match="Sample size not found"):
            parse_poll(text)

    def test_missing_fieldwork_raises(self) -> None:
        text = SEPT_2026_TEXT.replace("Fieldwork: 13th - 14th September 2026", "")
        with pytest.raises(ValueError, match="Fieldwork window not found"):
            parse_poll(text)


# ── parse_headline_vi_table ───────────────────────────────────────────────────


class TestParseHeadlineViTableOctober2026:
    """Combined headline cross-tabs may also contain region grouping labels."""

    def test_exact_metadata_and_all_headline_shares(self) -> None:
        parsed = parse_poll(OCT_2026_TEXT)

        assert parsed.sample_size == 2312
        assert parsed.fieldwork_start == date(2026, 10, 4)
        assert parsed.fieldwork_end == date(2026, 10, 5)
        assert parsed.party_macro_percentages == OCT_2026_EXPECTED_SHARES
        assert "Your Party" not in parsed.party_macro_percentages
        assert "Restore Britain" not in parsed.party_macro_percentages

    def test_later_direct_question_answers_cannot_replace_headline(self) -> None:
        result = parse_headline_vi_table(OCT_2026_TEXT)

        # The later Conservative answer has Wales 10 and Scotland 7.
        assert result["Conservative"]["Wales"] == 12.0
        assert result["Conservative"]["Scotland"] == 11.0

    def test_missing_outer_question_boundary_raises(self) -> None:
        text = OCT_2026_TEXT.replace("Now, thinking specifically", "Thinking about")

        with pytest.raises(ValueError, match="Could not isolate Westminster headline"):
            parse_headline_vi_table(text)

    @pytest.mark.parametrize("label", ["Con", "Your Party", "Restore Britain"])
    @pytest.mark.parametrize("extra_value", [False, True])
    def test_inconsistent_row_width_including_unmapped_parties_raises(
        self,
        label: str,
        extra_value: bool,
    ) -> None:
        row = next(
            line
            for line in OCT_2026_TEXT.splitlines()
            if line.startswith(f"{label} ")
        )
        replacement = f"{row} 99" if extra_value else row.rsplit(" ", 1)[0]
        text = OCT_2026_TEXT.replace(row, replacement, 1)

        with pytest.raises(ValueError, match="inconsistent widths"):
            parse_headline_vi_table(text)

    def test_rows_without_previous_poll_total_are_accepted(self) -> None:
        headline, later_questions = OCT_2026_TEXT.split(
            "If there were a general election",
            1,
        )
        headline = re.sub(r"(?m)^(\D+?) \d+ ", r"\1 ", headline)
        text = f"{headline}If there were a general election{later_questions}"

        assert parse_headline_vi_table(text) == OCT_2026_EXPECTED_SHARES

    def test_consistent_rows_still_must_agree_with_percentage_header(self) -> None:
        headline, later_questions = OCT_2026_TEXT.split(
            "If there were a general election",
            1,
        )
        headline = re.sub(r"(?m)^(\D+?) \d+ \d+ ", r"\1 ", headline)
        text = f"{headline}If there were a general election{later_questions}"

        with pytest.raises(
            ValueError,
            match="23 columns, but percentage header has 24",
        ):
            parse_headline_vi_table(text)

    def test_rows_must_contain_the_complete_seven_column_suffix(self) -> None:
        headline, later_questions = OCT_2026_TEXT.split(
            "If there were a general election",
            1,
        )
        headline = re.sub(
            r"(?m)^(\D+?) (?:\d+ ){19}",
            r"\1 ",
            headline,
        )
        text = f"{headline}If there were a general election{later_questions}"

        with pytest.raises(ValueError, match="headline rows have only 6 columns"):
            parse_headline_vi_table(text)

    def test_percentage_header_must_contain_all_seven_regions(self) -> None:
        headline, later_questions = OCT_2026_TEXT.split(
            "If there were a general election",
            1,
        )
        headline = re.sub(r"(?m)^%(?: %)+$", "% % % % % %", headline)
        text = f"{headline}If there were a general election{later_questions}"

        with pytest.raises(ValueError, match="percentage header has only 6 columns"):
            parse_headline_vi_table(text)

    def test_combined_header_allows_wrapped_region_names(self) -> None:
        text = OCT_2026_TEXT.replace(
            "England Wales Scotland North Midlands London Rest of\nSouth",
            "England\nWales\tScotland\nNorth\tMidlands London Rest of\nSouth",
            1,
        )

        assert parse_headline_vi_table(text) == OCT_2026_EXPECTED_SHARES


class TestParseHeadlineViTableRepeatedPages:
    """Real extracted pages retain metadata, repeated labels and later answers."""

    @pytest.mark.parametrize(
        ("filename", "sample_size", "start", "end", "shares"),
        [
            (
                "20260928", 2310, date(2026, 9, 27), date(2026, 9, 28),
                {
                    "Conservative": [13, 13, 18, 20, 19, 22],
                    "Labour": [18, 17, 31, 27, 32, 18],
                    "Liberal Democrats": [1, 14, 8, 8, 14, 18],
                    "Scottish National Party": [0, 26, 0, 0, 0, 0],
                    "Plaid Cymru": [26, 0, 0, 0, 0, 0],
                    "Reform UK": [32, 19, 27, 30, 14, 24],
                    "Green": [5, 9, 12, 10, 16, 12],
                    "Other": [1, 2, 2, 1, 2, 2],
                },
            ),
            (
                "20260921", 2286, date(2026, 9, 20), date(2026, 9, 21),
                {
                    "Conservative": [12, 12, 18, 21, 24, 25],
                    "Labour": [24, 15, 33, 27, 27, 16],
                    "Liberal Democrats": [7, 11, 7, 6, 14, 18],
                    "Scottish National Party": [0, 28, 0, 0, 0, 0],
                    "Plaid Cymru": [26, 0, 0, 0, 0, 0],
                    "Reform UK": [22, 15, 25, 26, 9, 20],
                    "Green": [7, 13, 13, 13, 22, 14],
                    "Other": [2, 2, 1, 3, 2, 0],
                },
            ),
            (
                "20260209", 2466, date(2026, 2, 8), date(2026, 2, 9),
                {
                    "Conservative": [10, 5, 16, 23, 16, 23],
                    "Labour": [19, 21, 22, 16, 30, 14],
                    "Liberal Democrats": [6, 8, 10, 11, 14, 20],
                    "Scottish National Party": [0, 33, 0, 0, 0, 0],
                    "Plaid Cymru": [33, 0, 0, 0, 0, 0],
                    "Reform UK": [22, 22, 32, 30, 17, 28],
                    "Green": [10, 11, 18, 15, 22, 14],
                    "Other": [1, 0, 2, 5, 1, 1],
                },
            ),
        ],
    )
    def test_exact_metadata_and_all_regional_shares(
        self,
        filename: str,
        sample_size: int,
        start: date,
        end: date,
        shares: dict[str, list[int]],
    ) -> None:
        text = self._source_text(filename)
        regions = ["Wales", "Scotland", "North", "Midlands", "London", "Rest of South"]

        parsed = parse_poll(text)

        assert parsed.sample_size == sample_size
        assert parsed.fieldwork_start == start
        assert parsed.fieldwork_end == end
        assert parsed.party_macro_percentages == {
            party: dict(zip(regions, values)) for party, values in shares.items()
        }

    @staticmethod
    def _source_text(filename: str = "20260928") -> str:
        path = Path(__file__).parent / "fixtures" / "yougov"
        return (path / f"headline-{filename}.txt").read_text()

    @pytest.mark.parametrize("block_index", [0, 1])
    @pytest.mark.parametrize("label", ["Con", "Your Party", "Restore Britain"])
    def test_duplicate_label_inside_each_block_is_rejected(
        self,
        block_index: int,
        label: str,
    ) -> None:
        sections = self._source_text().split("Westminster Voting Intention")
        block = sections[block_index + 1]
        row = next(line for line in block.splitlines() if line.startswith(f"{label} "))
        sections[block_index + 1] = block.replace(row, f"{row}\n{row}", 1)

        with pytest.raises(ValueError, match="Duplicate party labels"):
            parse_headline_vi_table("Westminster Voting Intention".join(sections))

    @pytest.mark.parametrize("change", ["rename", "reorder", "missing", "extra"])
    def test_repeated_block_must_keep_complete_party_order(self, change: str) -> None:
        prefix, repeated = self._source_text().rsplit("Westminster Voting Intention", 1)
        if change == "rename":
            repeated = repeated.replace("Your Party 0 0", "Unmapped Party 0 0", 1)
        elif change == "reorder":
            repeated = repeated.replace("Con 21 19\nLab 23 24", "Lab 23 24\nCon 21 19")
        elif change == "missing":
            repeated = repeated.replace("Your Party 0 0\n", "", 1)
        else:
            repeated = repeated.replace("Other 1 2\n", "Other 1 2\nExtra Party 0 0\n", 1)

        with pytest.raises(ValueError, match="different party order"):
            parse_headline_vi_table(f"{prefix}Westminster Voting Intention{repeated}")

    @pytest.mark.parametrize("block_index", [0, 1])
    def test_unmapped_party_width_must_match_its_own_block(self, block_index: int) -> None:
        sections = self._source_text().split("Westminster Voting Intention")
        block = sections[block_index + 1]
        row = next(line for line in block.splitlines() if line.startswith("Your Party "))
        sections[block_index + 1] = block.replace(row, f"{row} 99", 1)

        with pytest.raises(ValueError, match="inconsistent row widths"):
            parse_headline_vi_table("Westminster Voting Intention".join(sections))

    def test_routine_header_must_agree_with_percentage_width(self) -> None:
        text = self._source_text("20260921").replace(
            "% % % % % % % %\n", "% % % % % % %\n",
        )

        with pytest.raises(ValueError, match="Regions table has 7 columns"):
            parse_headline_vi_table(text)

    def test_anonymous_extra_column_is_rejected(self) -> None:
        text = self._source_text("20260921").replace(
            "Routine England Wales Scotland", "Unknown England Wales Scotland",
        )

        with pytest.raises(ValueError, match="Regions table has 8 columns"):
            parse_headline_vi_table(text)

    def test_truncated_routine_row_is_rejected(self) -> None:
        text = self._source_text("20260921").replace(
            "18 22 12 12 18 21 24 25\n", "22 12 12 18 21 24 25\n",
        )

        with pytest.raises(ValueError, match="Unexpected line in regions table"):
            parse_headline_vi_table(text)


class TestParseHeadlineViTableSeptember2026:
    """Seven-column region table: every region comes from the table."""

    def test_every_region_read_from_table(self) -> None:
        # Page 1 now ends in Intermediate/Routine; reading its last two values
        # as Wales/Scotland would give Con 20/21.
        result = parse_headline_vi_table(SEPT_2026_TEXT)
        assert result["Conservative"] == {
            "Wales": 10.0,
            "Scotland": 9.0,
            "North": 17.0,
            "Midlands": 25.0,
            "London": 22.0,
            "Rest of South": 24.0,
        }

    def test_country_parties(self) -> None:
        result = parse_headline_vi_table(SEPT_2026_TEXT)
        assert result["Scottish National Party"]["Scotland"] == 35.0
        assert result["Plaid Cymru"]["Wales"] == 26.0

    def test_other_is_the_other_row_not_your_party(self) -> None:
        result = parse_headline_vi_table(SEPT_2026_TEXT)
        assert result["Other"] == {
            "Wales": 0.0,
            "Scotland": 1.0,
            "North": 2.0,
            "Midlands": 1.0,
            "London": 2.0,
            "Rest of South": 1.0,
        }

    def test_unmapped_parties_are_dropped(self) -> None:
        result = parse_headline_vi_table(SEPT_2026_TEXT)
        assert "Your Party" not in result
        assert "Restore Britain" not in result
        assert len(result) == 8

    def test_percent_row_disagreeing_with_header_raises(self) -> None:
        text = SEPT_2026_TEXT.replace("% % % % % % %\n", "% % % %\n")
        with pytest.raises(ValueError, match="Regions table has 4 columns"):
            parse_headline_vi_table(text)

    def test_table_cut_short_raises(self) -> None:
        text = SEPT_2026_TEXT.replace(
            "2 0 1 2 1 2 1\n15 8 7 10 16 15 18\nCountry Region in England",
            "Country Region in England",
        )
        with pytest.raises(ValueError, match="Unexpected line in regions table"):
            parse_headline_vi_table(text)


class TestParseHeadlineViTableMarchToAugust2026:
    """Four-column region table: Wales and Scotland come from page 1."""

    def test_england_regions_read_from_table(self) -> None:
        result = parse_headline_vi_table(AUG_2026_TEXT)
        assert result["Conservative"] == {
            "Wales": 10.0,
            "Scotland": 7.0,
            "North": 17.0,
            "Midlands": 24.0,
            "London": 20.0,
            "Rest of South": 24.0,
        }

    def test_country_parties_from_page_one(self) -> None:
        result = parse_headline_vi_table(AUG_2026_TEXT)
        assert result["Scottish National Party"]["Scotland"] == 29.0
        assert result["Plaid Cymru"]["Wales"] == 30.0

    def test_other_skips_your_party_and_restore_britain_rows(self) -> None:
        # Regression: table rows used to be paired with mapped parties only, so
        # the Your Party row (1 0 0 0) was stored as Other.
        result = parse_headline_vi_table(AUG_2026_TEXT)
        assert result["Other"] == {
            "Wales": 0.0,
            "Scotland": 1.0,
            "North": 2.0,
            "Midlands": 1.0,
            "London": 3.0,
            "Rest of South": 1.0,
        }

    def test_page_one_without_country_columns_raises(self) -> None:
        text = AUG_2026_TEXT.replace(" England Wales Scotland\n", "\n")
        with pytest.raises(ValueError, match="Wales and Scotland columns found in neither"):
            parse_headline_vi_table(text)

    def test_missing_second_question_raises(self) -> None:
        text = AUG_2026_TEXT.replace("If there were a general election", "Were there an election")
        with pytest.raises(ValueError, match="end of the MRP headline rows"):
            parse_headline_vi_table(text)


class TestParseHeadlineViTableOldFormat:
    """Single cross-tab: the last six values of each row are the regions."""

    def test_regions_are_the_last_six_values(self) -> None:
        result = parse_headline_vi_table(OLD_FORMAT_TEXT)
        assert result["Conservative"] == {
            "Wales": 16.0,
            "Scotland": 11.0,
            "North": 17.0,
            "Midlands": 22.0,
            "London": 16.0,
            "Rest of South": 22.0,
        }

    def test_other_row_matched_by_label(self) -> None:
        result = parse_headline_vi_table(OLD_FORMAT_TEXT)
        assert result["Other"] == {
            "Wales": 1.0,
            "Scotland": 4.0,
            "North": 2.0,
            "Midlands": 2.0,
            "London": 2.0,
            "Rest of South": 2.0,
        }

    @pytest.mark.parametrize("keep_header", [False, True])
    def test_later_answers_are_ignored_with_or_without_structural_header(
        self,
        keep_header: bool,
    ) -> None:
        text = OLD_FORMAT_TEXT
        if not keep_header:
            text = text[text.index("Westminster Voting Intention"):]
        text = text.replace(
            "Now, thinking specifically",
            "Conservative 1 2 3 4 5 6\nNow, thinking specifically",
        )

        assert parse_headline_vi_table(text) == parse_headline_vi_table(OLD_FORMAT_TEXT)


class TestParseHeadlineViTableRaises:
    """parse_headline_vi_table's own raises, outside the old/new-format dispatch."""

    def test_missing_start_marker_raises(self) -> None:
        text = OLD_FORMAT_TEXT.replace("Westminster Voting Intention", "")
        with pytest.raises(ValueError, match="Could not isolate Westminster headline"):
            parse_headline_vi_table(text)

    def test_missing_end_marker_raises(self) -> None:
        end_marker = (
            "Now, thinking specifically about your own\n"
            "constituency, if there were a general election\n"
        )
        text = OLD_FORMAT_TEXT.replace(end_marker, "")
        with pytest.raises(ValueError, match="Could not isolate Westminster headline"):
            parse_headline_vi_table(text)

    def test_end_marker_before_start_marker_raises(self) -> None:
        """``end_index <= start_index`` is its own branch, distinct from -1."""
        text = (
            "Now, thinking specifically about your own\n"
            "constituency, if there were a general election\n"
            "Westminster Voting Intention\n"
            "Con 1 2 3 4 5 6\n"
        )
        with pytest.raises(ValueError, match="Could not isolate Westminster headline"):
            parse_headline_vi_table(text)

    def test_party_missing_from_old_format_rows_raises(self) -> None:
        # Every PARTY_NAME_MAP row present with six figures except Green, which
        # is left out entirely, so the post-parse completeness check fires.
        text = (
            "Westminster Voting Intention\n"
            "Con 1 2 3 4 5 6\n"
            "Lab 1 2 3 4 5 6\n"
            "Lib Dem 1 2 3 4 5 6\n"
            "SNP 1 2 3 4 5 6\n"
            "Plaid Cymru 1 2 3 4 5 6\n"
            "Reform UK 1 2 3 4 5 6\n"
            "Other 1 2 3 4 5 6\n"
            "Now, thinking specifically\n"
        )
        expected = "Missing party rows in headline table: ['Green']"
        with pytest.raises(ValueError, match=re.escape(expected)):
            parse_headline_vi_table(text)


# ── _parse_old_format_rows (direct) ────────────────────────────────────────────


class TestParseOldFormatRowsDirect:
    """Branches of _parse_old_format_rows not reached through a full PDF text."""

    def test_row_with_fewer_than_six_values_is_skipped(self) -> None:
        lines = ["Con 1 2 3 4 5", "Lab 10 20 30 40 50 60"]

        result = _parse_old_format_rows(lines, ["Con", "Lab"])

        assert "Conservative" not in result
        assert result["Labour"] == {
            "Wales": 10.0,
            "Scotland": 20.0,
            "North": 30.0,
            "Midlands": 40.0,
            "London": 50.0,
            "Rest of South": 60.0,
        }

    def test_line_matching_no_label_is_ignored(self) -> None:
        lines = ["Unrelated header text", "Lab 10 20 30 40 50 60"]

        result = _parse_old_format_rows(lines, ["Con", "Lab"])

        assert list(result) == ["Labour"]

    def test_no_matching_lines_returns_empty_dict(self) -> None:
        result = _parse_old_format_rows(
            ["Nothing here", "Still nothing"], ["Con", "Lab"]
        )

        assert result == {}


# ── _parse_new_format (direct) ─────────────────────────────────────────────────


class TestParseNewFormatDirect:
    """Raise branches of _parse_new_format not reached through a full PDF text."""

    def test_no_headline_rows_before_second_question_raises(self) -> None:
        section = "Random text\nIf there were a general election\n"
        lines = [line.strip() for line in section.splitlines() if line.strip()]

        with pytest.raises(ValueError, match="No MRP headline rows found"):
            _parse_new_format(section, lines, "")

    def test_missing_region_table_header_raises(self) -> None:
        section = "Con 20 20\nIf there were a general election\n"
        lines = [line.strip() for line in section.splitlines() if line.strip()]

        with pytest.raises(ValueError, match="Could not find regions table"):
            _parse_new_format(section, lines, "")

    def test_missing_percent_header_row_raises(self) -> None:
        section = (
            "Con 20 20\n"
            "If there were a general election\n"
            "North Midlands London Rest of\nSouth\n"
        )
        lines = [line.strip() for line in section.splitlines() if line.strip()]

        with pytest.raises(ValueError, match="Could not find '%' header row"):
            _parse_new_format(section, lines, "")

    def test_incomplete_table_raises(self) -> None:
        section = (
            "Con 1 2 3 4\n"
            "Lab 1 2 3 4\n"
            "If there were a general election\n"
            "North Midlands London Rest of\nSouth\n"
            "% % % %\n"
            "10 20 30 40\n"
        )
        lines = [line.strip() for line in section.splitlines() if line.strip()]

        expected = "Regions table incomplete: expected 2 rows, got 1"
        with pytest.raises(ValueError, match=re.escape(expected)):
            _parse_new_format(section, lines, "stuff Wales Scotland more")


# ── extract_pdf_text ────────────────────────────────────────────────────────────


class TestExtractPdfText:
    """extract_pdf_text: download, tempfile write, page join, tempfile cleanup."""

    def test_downloads_and_joins_pages_with_newlines(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_pdf_fetch(monkeypatch, ["Page one", "Page two"])

        result = extract_pdf_text("https://example.test/poll.pdf")

        assert result == "Page one\nPage two"

    def test_page_with_no_extractable_text_becomes_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_pdf_fetch(monkeypatch, ["A", None, "B"])

        result = extract_pdf_text("https://example.test/poll.pdf")

        assert result == "A\n\nB"

    def test_payload_is_flushed_to_the_tempfile_before_pdfreader_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = b"%PDF-1.4 fake payload bytes"
        monkeypatch.setattr(
            yougov_import, "urlopen", lambda *_a, **_k: FakeUrlResponse(payload)
        )
        captured_bytes: list[bytes] = []

        def _reading_reader(path: str) -> _FakePdfReader:
            with open(path, "rb") as handle:
                captured_bytes.append(handle.read())
            return _FakePdfReader(["text"])

        monkeypatch.setattr(yougov_import, "PdfReader", _reading_reader)

        extract_pdf_text("https://example.test/poll.pdf")

        assert captured_bytes == [payload]

    def test_tempfile_exists_during_the_call_and_is_deleted_after(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            yougov_import, "urlopen", lambda *_a, **_k: FakeUrlResponse(b"pdf-bytes")
        )
        captured_paths: list[str] = []

        def _recording_reader(path: str) -> _FakePdfReader:
            captured_paths.append(path)
            assert Path(path).exists()
            assert path.endswith(".pdf")
            return _FakePdfReader(["text"])

        monkeypatch.setattr(yougov_import, "PdfReader", _recording_reader)

        extract_pdf_text("https://example.test/poll.pdf")

        assert len(captured_paths) == 1
        assert not Path(captured_paths[0]).exists()


# ── build_import_plan ────────────────────────────────────────────────────────


class TestBuildImportPlanRaises:
    """build_import_plan's raises for a missing map, region or party."""

    def test_missing_map_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The map check must run before the PDF fetch; urlopen is forbidden."""
        _forbid_pdf_fetch(monkeypatch)

        with pytest.raises(ValueError, match=re.escape("Map not found: 'Nope'")):
            build_import_plan(db, map_name="Nope")

    def test_missing_internal_region_raises_before_fetching_the_pdf(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The region check must run before the PDF fetch; urlopen is forbidden."""
        custom_map = db.add_map("Partial Map", parliament="westminster")
        missing = "Wales"
        for names in MACRO_TO_INTERNAL_REGIONS.values():
            for name in names:
                if name != missing:
                    db.add_region(custom_map.id, name)
        _forbid_pdf_fetch(monkeypatch)

        expected = f"Region {missing!r} not found in map {custom_map.name!r}"
        with pytest.raises(ValueError, match=re.escape(expected)):
            build_import_plan(db, map_name=custom_map.name)

    def test_missing_party_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom_map = db.add_map("Full Regions Map", parliament="westminster")
        for names in MACRO_TO_INTERNAL_REGIONS.values():
            for name in names:
                db.add_region(custom_map.id, name)
        for name in sorted(set(PARTY_NAME_MAP.values()) - {"Green"}):
            db.add_party(name)
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])

        expected = "Missing parties in database (run party importer first): ['Green']"
        with pytest.raises(ValueError, match=re.escape(expected)):
            build_import_plan(db, map_name=custom_map.name)


class TestBuildImportPlanRegions:
    """Region resolution goes through normalize_name, macro by macro."""

    def test_region_names_matched_case_and_whitespace_insensitively(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The seeded parties are reused; the map and its regions are our own."""
        custom_map = db.add_map("Custom YouGov Map", parliament="westminster")
        custom_region_ids: dict[str, int] = {}
        for names in MACRO_TO_INTERNAL_REGIONS.values():
            for name in names:
                odd_name = f"  {name.upper()}  "
                custom_region_ids[name] = db.add_region(custom_map.id, odd_name).id
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])

        plan = build_import_plan(db, map_name=custom_map.name)

        assert plan.map_id == custom_map.id
        assert plan.regions_mapping == _expected_regions_mapping(custom_region_ids)
        assert {row.region_id for row in plan.rows} == set(custom_region_ids.values())


class TestBuildImportPlanFullBuild:
    """A full build over the seeded Westminster world: rows, mapping, pollster."""

    def test_october_combined_cross_tab_builds_88_regional_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        source_url = (
            "https://ygo-assets-websites-editorial-emea.yougov.net/documents/"
            "VotingIntention_Results_261005_w.pdf"
        )
        calls = _patch_pdf_fetch(monkeypatch, [OCT_2026_TEXT])

        plan = build_import_plan(db, pdf_url=source_url, map_name=world.map_name)

        assert calls[0][0] == source_url
        assert plan.source_url == source_url
        assert plan.map_id == world.map_id
        assert plan.regions_mapping == _expected_regions_mapping(world.region_ids)
        assert plan.pollster_name == "YouGov"
        assert plan.parsed.sample_size == 2312
        assert plan.parsed.fieldwork_start == date(2026, 10, 4)
        assert plan.parsed.fieldwork_end == date(2026, 10, 5)
        assert plan.parsed.party_macro_percentages == OCT_2026_EXPECTED_SHARES
        assert len(plan.rows) == 88
        assert len({(row.party_id, row.region_id) for row in plan.rows}) == 88
        assert {row.party_name for row in plan.rows} == set(OCT_2026_EXPECTED_SHARES)
        assert {row.region_id for row in plan.rows} == {
            region_id
            for name, region_id in world.region_ids.items()
            if name != "Northern Ireland"
        }
        assert all(
            row.percentage == OCT_2026_EXPECTED_SHARES[row.party_name][row.macro_region]
            for row in plan.rows
        )
        assert {
            row.region_name: row.percentage
            for row in plan.rows
            if row.party_name == "Conservative"
        } == {
            "North East England": 17.0,
            "North West England": 17.0,
            "Yorkshire and The Humber": 17.0,
            "East Midlands": 25.0,
            "West Midlands": 25.0,
            "London": 21.0,
            "East of England": 21.0,
            "South East England": 21.0,
            "South West England": 21.0,
            "Wales": 12.0,
            "Scotland": 11.0,
        }

    def test_regions_mapping_and_rows_cover_every_macro_region(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])

        plan = build_import_plan(db, map_name=world.map_name)

        assert plan.map_id == world.map_id
        assert plan.map_name == world.map_name
        assert plan.regions_mapping == _expected_regions_mapping(world.region_ids)
        assert plan.pollster_identifier == DEFAULT_POLLSTER_IDENTIFIER
        assert plan.pollster_exists is False
        assert plan.pollster_name == "YouGov"
        assert plan.pollster_id is None
        assert plan.poll_exists is False
        assert plan.poll_id is None
        assert len(plan.rows) == _EXPECTED_ROW_COUNT

        # Spot-check every multi-region macro: every internal region in it
        # gets the same macro-level share. Values are Conservative's from
        # parse_headline_vi_table's own September-2026 test.
        def _region_shares(macro: str) -> dict[int, float]:
            return {
                r.region_id: r.percentage
                for r in plan.rows
                if r.party_name == "Conservative" and r.macro_region == macro
            }

        assert _region_shares("North") == {
            world.region_ids["North East England"]: 17.0,
            world.region_ids["North West England"]: 17.0,
            world.region_ids["Yorkshire and The Humber"]: 17.0,
        }
        assert _region_shares("Midlands") == {
            world.region_ids["East Midlands"]: 25.0,
            world.region_ids["West Midlands"]: 25.0,
        }
        assert _region_shares("Rest of South") == {
            world.region_ids["East of England"]: 24.0,
            world.region_ids["South East England"]: 24.0,
            world.region_ids["South West England"]: 24.0,
        }

        row = next(
            r
            for r in plan.rows
            if r.party_name == "Conservative" and r.macro_region == "Wales"
        )
        assert row.region_id == world.region_ids["Wales"]
        assert row.region_name == "Wales"
        assert row.party_id == world.party_ids["Conservative"]
        assert row.percentage == 10.0

    def test_pollster_and_poll_existing_are_detected(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="yougov",
            pollster_name="YouGov Existing",
            fieldwork_start=date(2026, 9, 13),
            fieldwork_end=date(2026, 9, 14),
            national={world.party_ids["Labour"]: 1.0},
            sample_size=2149,
        )
        pollster = db.get_pollster_by_identifier("yougov")
        assert pollster is not None
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])

        plan = build_import_plan(db, map_name=world.map_name)

        assert plan.pollster_exists is True
        assert plan.pollster_id == pollster.id
        assert plan.pollster_name == "YouGov Existing"
        assert plan.poll_exists is True
        assert plan.poll_id == poll.id

    def test_pollster_existing_without_a_matching_poll_leaves_poll_absent(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        db.add_pollster("YouGov Existing", "yougov", weight=1.0)
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])

        plan = build_import_plan(db, map_name=world.map_name)

        assert plan.pollster_exists is True
        assert plan.poll_exists is False
        assert plan.poll_id is None


# ── _cli_preview ─────────────────────────────────────────────────────────────


def _preview_plan(**overrides: object) -> ImportPlan:
    """Build an ImportPlan for _cli_preview tests, one Labour/Wales row by default."""
    parsed = ParsedPoll(
        sample_size=1500,
        fieldwork_start=date(2026, 3, 1),
        fieldwork_end=date(2026, 3, 3),
        party_macro_percentages={"Labour": {"Wales": 31.5}},
    )
    row = PlannedPollRow(
        party_id=2,
        party_name="Labour",
        macro_region="Wales",
        region_id=10,
        region_name="Wales",
        percentage=31.5,
    )
    defaults: dict[str, object] = {
        "pollster_identifier": "yougov",
        "pollster_name": "YouGov",
        "pollster_id": None,
        "pollster_exists": False,
        "regions_mapping": "Wales:10",
        "map_id": 1,
        "map_name": "UK Constituencies post 2022",
        "source_url": "https://example.test/poll.pdf",
        "parsed": parsed,
        "poll_id": None,
        "poll_exists": False,
        "rows": [row],
    }
    defaults.update(overrides)
    return ImportPlan.model_validate(defaults)


class TestCliPreview:
    """_cli_preview's dry-run summary, all four pollster/poll existence combos."""

    def test_new_pollster_and_new_poll(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _cli_preview(_preview_plan())

        lines = capsys.readouterr().out.splitlines()
        assert "Parsed poll: fieldwork=2026-03-01 to 2026-03-03, sample=1500" in lines
        assert "[dry-run] would create pollster: yougov" in lines
        assert "[dry-run] would create poll" in lines
        assert (
            "[dry-run] would insert row: party=Labour, macro=Wales, "
            "region_id=10, pct=31.5"
        ) in lines

    def test_existing_pollster_and_existing_poll(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _cli_preview(_preview_plan(pollster_exists=True, poll_exists=True, poll_id=42))

        lines = capsys.readouterr().out.splitlines()
        assert "pollster exists: yougov" in lines
        assert "poll exists: 42" in lines
        assert "[dry-run] would create pollster: yougov" not in lines
        assert "[dry-run] would create poll" not in lines

    def test_poll_exists_true_with_no_id_still_previews_creation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """poll_exists and poll_id are both checked; either falsy means 'would
        create'."""
        _cli_preview(_preview_plan(poll_exists=True, poll_id=None))

        lines = capsys.readouterr().out.splitlines()
        assert "poll exists: None" not in lines
        assert "[dry-run] would create poll" in lines

    def test_poll_id_set_but_poll_exists_false_still_previews_creation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The other half of the AND: a stray poll_id without poll_exists still
        creates."""
        _cli_preview(_preview_plan(poll_exists=False, poll_id=42))

        lines = capsys.readouterr().out.splitlines()
        assert "poll exists: 42" not in lines
        assert "[dry-run] would create poll" in lines

    def test_no_rows_prints_no_row_lines(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _cli_preview(_preview_plan(rows=[]))

        out = capsys.readouterr().out
        assert "[dry-run] would insert row" not in out


# ── main ──────────────────────────────────────────────────────────────────────


class TestMain:
    """main's dry-run/commit branches, sys.argv and DATABASE_PATH pointed at db."""

    def test_defaults_use_the_module_constants(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No CLI args: the world's map is named DEFAULT_MAP_NAME, so this still
        runs."""
        world = westminster_world
        assert world.map_name == DEFAULT_MAP_NAME
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])
        monkeypatch.setattr(sys, "argv", ["yougov_import.py", "--dry-run"])

        main()

        out = capsys.readouterr().out
        assert f"Fetching PDF: {DEFAULT_PDF_URL}" in out
        assert f"[dry-run] would create pollster: {DEFAULT_POLLSTER_IDENTIFIER}" in out

    def test_commit_with_non_default_arguments_forwards_them(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
    ) -> None:
        """--pdf-url, --map-name and --pollster-identifier are all forwarded.

        A second, non-default map proves ``--map-name`` was read rather than
        falling back to ``DEFAULT_MAP_NAME`` (which equals the seeded world's
        map name, so the other tests can't tell forwarding from a default).
        """
        second_map = db.add_map("Second YouGov Map", parliament="westminster")
        for names in MACRO_TO_INTERNAL_REGIONS.values():
            for name in names:
                db.add_region(second_map.id, name)
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        custom_url = "https://example.test/custom-poll.pdf"
        calls = _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "yougov_import.py",
                "--map-name",
                second_map.name,
                "--pdf-url",
                custom_url,
                "--pollster-identifier",
                "yougov_custom",
            ],
        )

        main()

        assert calls == [(custom_url, 60)]
        assert db.get_pollster_by_identifier(DEFAULT_POLLSTER_IDENTIFIER) is None
        pollster = db.get_pollster_by_identifier("yougov_custom")
        assert pollster is not None
        polls = db.get_polls_by_pollster(pollster.id)
        assert len(polls) == 1
        assert polls[0].map_id == second_map.id
        assert polls[0].source_url == custom_url

    def test_dry_run_prints_preview_and_writes_nothing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])
        monkeypatch.setattr(
            sys, "argv", ["yougov_import.py", "--map-name", world.map_name, "--dry-run"]
        )

        main()

        out = capsys.readouterr().out
        assert "[dry-run] would create pollster: yougov" in out
        assert db.get_pollster_by_identifier(DEFAULT_POLLSTER_IDENTIFIER) is None

    def test_commit_creates_pollster_poll_and_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])
        monkeypatch.setattr(
            sys, "argv", ["yougov_import.py", "--map-name", world.map_name]
        )

        main()

        out = capsys.readouterr().out
        assert "created pollster: yougov" in out
        assert "created poll:" in out
        assert f"inserted poll rows: {_EXPECTED_ROW_COUNT}" in out
        assert "deleted existing rows" not in out
        pollster = db.get_pollster_by_identifier("yougov")
        assert pollster is not None
        assert pollster.weight == 1.0
        polls = db.get_polls_by_pollster(pollster.id)
        assert len(polls) == 1
        assert len(db.get_rows_for_poll(polls[0].id)) == _EXPECTED_ROW_COUNT

    def test_commit_with_replace_rows_but_nothing_existing_prints_no_delete_line(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])
        monkeypatch.setattr(
            sys,
            "argv",
            ["yougov_import.py", "--map-name", world.map_name, "--replace-rows"],
        )

        main()

        out = capsys.readouterr().out
        assert "created poll:" in out
        assert "deleted existing rows" not in out
        assert f"inserted poll rows: {_EXPECTED_ROW_COUNT}" in out

    def test_commit_without_replace_rows_skips_existing_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        existing = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="yougov",
            pollster_name="YouGov",
            fieldwork_start=date(2026, 9, 13),
            fieldwork_end=date(2026, 9, 14),
            national={world.party_ids["Labour"]: 1.0},
            sample_size=2149,
        )
        rows_before = len(db.get_rows_for_poll(existing.id))
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])
        monkeypatch.setattr(
            sys, "argv", ["yougov_import.py", "--map-name", world.map_name]
        )

        main()

        out = capsys.readouterr().out
        assert "pollster exists: yougov" in out
        assert f"poll exists: {existing.id}" in out
        assert "already has rows; use --replace-rows to overwrite" in out
        assert len(db.get_rows_for_poll(existing.id)) == rows_before

    def test_commit_with_replace_rows_replaces_existing_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        only_the_test_database: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        existing = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="yougov",
            pollster_name="YouGov",
            fieldwork_start=date(2026, 9, 13),
            fieldwork_end=date(2026, 9, 14),
            national={world.party_ids["Labour"]: 1.0},
            sample_size=2149,
        )
        rows_before = len(db.get_rows_for_poll(existing.id))
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        _patch_pdf_fetch(monkeypatch, [SEPT_2026_TEXT])
        monkeypatch.setattr(
            sys,
            "argv",
            ["yougov_import.py", "--map-name", world.map_name, "--replace-rows"],
        )

        main()

        out = capsys.readouterr().out
        assert f"deleted existing rows: {rows_before}" in out
        assert f"inserted poll rows: {_EXPECTED_ROW_COUNT}" in out
        assert len(db.get_rows_for_poll(existing.id)) == _EXPECTED_ROW_COUNT
