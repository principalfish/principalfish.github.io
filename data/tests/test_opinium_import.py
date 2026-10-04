"""Tests for the Opinium XLSX poll importer.

``commit_import_plan`` and ``_find_existing_poll`` are already covered, across
all eleven Westminster importers, by ``test_westminster_importers_commit.py``
(this module is one of the four "Variant B" importers that also update an
existing pollster's ``regions_mapping`` on commit; that update path is
exercised there, not here). This file covers everything else: the parsing
helpers, ``extract_workbook``, ``build_import_plan``'s region handling,
``_cli_preview`` and ``main``.

Every workbook below is built synthetically with ``build_workbook``, shaped to
fit the columns/labels the parsing functions match on. Investigating this
module surfaced three bugs, all pinned (not fixed) below:

- ``PARTY_NAME_MAP`` has no raw label mapping to "Scottish National Party" or
  "Plaid Cymru" at all, so a HeadlineVI row for either party -- however it
  might be spelled -- can never be recognised; both always fall through to
  the post-scan zero-fill. The live DB shows exactly 0.0 for every one of
  the 455 stored Scottish National Party ``poll_rows`` and every one of the
  455 stored Plaid Cymru ``poll_rows`` for pollster "opinium" (910 rows
  total; 13 rows/poll x 35 polls, each party counted separately). This is
  not merely the party being absent from the source: summing each poll's
  stored regional shares (every party, per region) shows Scotland totals
  only 61-74% (mean 68.3%, with "Other" separately averaging just ~3.1%
  there) and Wales totals only 69-89% (mean 79%) -- both short of 100% by
  roughly what SNP/Plaid Cymru typically poll in those regions -- while
  every English region and the national total sit at ~99.7-100%. So the
  real source sheets almost certainly do carry rows for both parties, and
  this module actively drops them, rather than Opinium's HeadlineVI table
  simply never listing them.
- ``parse_poll``'s "weighted base row" search matches any label containing
  both "base:" and "weighted" (case-insensitively). Since "unweighted"
  itself contains "weighted" as a substring, an "(Unweighted)" row anywhere
  in the scan window is matched (and, via the search's ``break``, wins)
  ahead of a later "(Weighted)" row. This mechanism is confirmed by reading
  the code. It was also checked against one real, directly-downloaded
  Opinium workbook (fieldwork 2026-05-06, live DB poll 970): running the
  real ``parse_poll`` on that file returned ``sample_size=1538``, matching
  the DB's stored value for poll 970 exactly (re-checkable any time with a
  read-only query), and the file's own "(Unweighted)"/"(Weighted)" rows
  read 1538 and 1525 respectively -- two different numbers, so this isn't a
  coincidence of the rows sharing a value. That the same ordering
  (Unweighted before Weighted) holds across Opinium's other files is
  plausible -- it matched the one figure checked at investigation time --
  but the downloaded workbooks weren't kept, so treat that broader claim as
  investigation notes rather than a re-verifiable fact beyond poll 970. The
  impact is low: ``sample_size`` is display-only and one of the match keys
  ``_find_existing_poll`` uses for de-duplication -- neither UK model reads
  it -- so "wrong" here means a mismatch with the code's own stated intent
  (some sibling importers, e.g. Ipsos and Techne, deliberately store an
  unweighted sample size by design), not a scoring-affecting defect. If
  this is ever fixed, re-importing any of the 35 stored Opinium polls would
  create a duplicate, since the changed ``sample_size`` would no longer
  match ``_find_existing_poll``'s lookup -- the same hazard piece 16 noted
  for Ipsos.

A third bug, found by reading the code rather than live data (its trigger
condition -- a parties table missing "Scottish National Party" or
"Plaid Cymru" -- cannot occur against the live DB, since the real party
importer always seeds both), is pinned in ``TestBuildImportPlanRaises``.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from types import MappingProxyType

import pytest
from openpyxl import Workbook

from db import Database
from polls.importers.westminster import opinium_import as op
from tests.uk_fixtures import (
    FakeUrlResponse,
    WestminsterWorld,
    build_workbook,
    workbook_bytes,
)

# ── Synthetic workbook builders ────────────────────────────────────────────
#
# FRONT PAGE: "Field dates"/"Sample" label rows in column C, value in column
# F, read by _find_fieldwork_and_sample.
#
# HeadlineVI: a header row (blank col A, "Total" in col B, then the six
# required macro columns and the optional "Northern Ireland" column), a
# weighted-base row, then one row per party.

_FIELDWORK_TEXT = "3-5 February 2026"
_SAMPLE_TEXT = "2,015"
# Carries its own year (2026) so parse_poll's unconditional upfront
# _infer_year_from_url call succeeds; tests of year inference itself use
# their own URLs, not this one.
_SOURCE_URL = "https://example.test/2026/tables.xlsx"

_MACRO_HEADERS: tuple[str, ...] = ("North", "Mids", "London", "South", "Wales", "Scotland")

# Raw workbook party label -> (Total, North, Mids, London, South, Wales,
# Scotland, Northern Ireland). Every value is unique across the whole grid
# (2..49), so a swapped row or column can't coincidentally reproduce another
# cell's expected value. Values start at 2, not 1, because _to_percentage
# treats anything in [0.0, 1.0] as a fraction (multiplying by 100), and 1.0
# sits exactly on that boundary.
_PARTY_VALUES: Mapping[str, tuple[float, ...]] = MappingProxyType(
    {
        "Con": (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0),
        "Lab": (10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0),
        "Lib Dem": (18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0, 25.0),
        "Reform": (26.0, 27.0, 28.0, 29.0, 30.0, 31.0, 32.0, 33.0),
        "Green": (34.0, 35.0, 36.0, 37.0, 38.0, 39.0, 40.0, 41.0),
        "Other": (42.0, 43.0, 44.0, 45.0, 46.0, 47.0, 48.0, 49.0),
    }
)


def _front_page_rows(
    fieldwork_text: str | None = _FIELDWORK_TEXT,
    sample_text: str | None = _SAMPLE_TEXT,
) -> list[list[object]]:
    """A FRONT PAGE sheet: label in column C, value in column F."""
    rows: list[list[object]] = []
    if fieldwork_text is not None:
        rows.append([None, None, "Field dates", None, None, fieldwork_text])
    if sample_text is not None:
        rows.append([None, None, "Sample", None, None, sample_text])
    return rows


def _headline_sheet_rows(
    *,
    include_ni_column: bool = True,
    omit_parties: Sequence[str] = (),
    base_label: str = "Base: All Respondents (Weighted)",
    base_value: object = 2015,
    extra_rows: Sequence[Sequence[object]] = (),
) -> list[list[object]]:
    """A HeadlineVI sheet: header row, a base row, one row per party."""
    header: list[object] = [None, "Total", *_MACRO_HEADERS]
    if include_ni_column:
        header.append("Northern Ireland")
    rows: list[list[object]] = [header, [base_label, base_value]]
    for raw_label, values in _PARTY_VALUES.items():
        if raw_label in omit_parties:
            continue
        row_values = values if include_ni_column else values[:-1]
        rows.append([raw_label, *row_values])
    rows.extend(list(row) for row in extra_rows)
    return rows


def _full_workbook(
    *,
    front_page_name: str = "FRONT PAGE",
    front_page_rows: list[list[object]] | None = None,
    headline_sheet_name: str = "HeadlineVI",
    headline_rows: list[list[object]] | None = None,
) -> Workbook:
    """A FRONT PAGE sheet plus a HeadlineVI sheet."""
    sheets: dict[str, list[list[object]]] = {
        front_page_name: (
            front_page_rows if front_page_rows is not None else _front_page_rows()
        ),
        headline_sheet_name: (
            headline_rows if headline_rows is not None else _headline_sheet_rows()
        ),
    }
    return build_workbook(sheets)


def _forbid_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch extract_workbook to fail loudly if it is ever called."""

    def _extract_workbook(*_a: object, **_k: object) -> Workbook:
        raise AssertionError("build_import_plan must not fetch the workbook here")

    monkeypatch.setattr(op, "extract_workbook", _extract_workbook)


# ── PARTY_NAME_MAP ──────────────────────────────────────────────────────


class TestPartyNameMap:
    """PARTY_NAME_MAP's raw-label -> canonical mapping, spelled literally."""

    def test_maps_every_known_alias(self) -> None:
        assert op.PARTY_NAME_MAP == {
            "Con": "Conservative",
            "Conservative": "Conservative",
            "Lab": "Labour",
            "Labour": "Labour",
            "Lib Dem": "Liberal Democrats",
            "Liberal Democrat": "Liberal Democrats",
            "Liberal Democrats": "Liberal Democrats",
            "Reform": "Reform UK",
            "Reform UK": "Reform UK",
            "Green": "Green",
            "Other": "Other",
        }

    def test_snp_and_plaid_cymru_have_no_raw_label_mapping_to_them(self) -> None:
        # Foundational fact behind the pinned bug in
        # TestParsePollOptionalPartyRecognition below: no raw label maps to
        # either party, so a HeadlineVI row for either can never be
        # recognised, however it's spelled.
        assert "Scottish National Party" not in op.PARTY_NAME_MAP.values()
        assert "Plaid Cymru" not in op.PARTY_NAME_MAP.values()


# ── _month_number ─────────────────────────────────────────────────────────


class TestMonthNumber:
    """Tests for _month_number -- full names and abbreviations, case-insensitive."""

    def test_full_names(self) -> None:
        assert op._month_number("January") == 1
        assert op._month_number("February") == 2
        assert op._month_number("March") == 3
        assert op._month_number("April") == 4
        assert op._month_number("May") == 5
        assert op._month_number("June") == 6
        assert op._month_number("July") == 7
        assert op._month_number("August") == 8
        assert op._month_number("September") == 9
        assert op._month_number("October") == 10
        assert op._month_number("November") == 11
        assert op._month_number("December") == 12

    def test_abbreviations(self) -> None:
        assert op._month_number("Jan") == 1
        assert op._month_number("Feb") == 2
        assert op._month_number("Mar") == 3
        assert op._month_number("Apr") == 4
        assert op._month_number("Jun") == 6
        assert op._month_number("Jul") == 7
        assert op._month_number("Aug") == 8
        assert op._month_number("Sep") == 9
        assert op._month_number("Sept") == 9
        assert op._month_number("Oct") == 10
        assert op._month_number("Nov") == 11
        assert op._month_number("Dec") == 12

    def test_trailing_period_stripped(self) -> None:
        assert op._month_number("Feb.") == 2
        assert op._month_number("Sept.") == 9

    def test_case_insensitive(self) -> None:
        assert op._month_number("JANUARY") == 1
        assert op._month_number("january") == 1
        assert op._month_number("jAnUaRy") == 1

    def test_unknown_returns_none(self) -> None:
        assert op._month_number("Octember") is None
        assert op._month_number("") is None


# ── _parse_fieldwork ──────────────────────────────────────────────────────


class TestParseFieldwork:
    """Tests for _parse_fieldwork -- same-month day ranges, with/without year.

    Unlike some sibling importers, Opinium's regexes only match a
    "<day>-<day> <Month> [<year>]" same-month range: there is no cross-month
    pattern and no single-day pattern.
    """

    def test_same_month_with_year(self) -> None:
        start, end = op._parse_fieldwork("3-5 February 2026")
        assert start == date(2026, 2, 3)
        assert end == date(2026, 2, 5)

    def test_same_month_no_year_uses_default_year(self) -> None:
        start, end = op._parse_fieldwork("3-5 Feb", default_year=2026)
        assert start == date(2026, 2, 3)
        assert end == date(2026, 2, 5)

    def test_same_month_no_year_without_default_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            op._parse_fieldwork("3-5 February")

    def test_explicit_year_takes_priority_over_default_year(self) -> None:
        # The with-year pattern is tried first and returns immediately; a
        # default_year passed alongside an explicit year must be ignored,
        # not silently override it.
        start, end = op._parse_fieldwork("3-5 February 2026", default_year=2099)
        assert start == date(2026, 2, 3)
        assert end == date(2026, 2, 5)

    def test_unknown_month_with_year_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            op._parse_fieldwork("3-5 Blah 2026")

    def test_unknown_month_no_year_with_default_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            op._parse_fieldwork("3-5 Blah", default_year=2026)

    def test_en_dash_normalised(self) -> None:
        start, end = op._parse_fieldwork("3–5 February 2026")
        assert start == date(2026, 2, 3)
        assert end == date(2026, 2, 5)

    def test_em_dash_normalised(self) -> None:
        start, end = op._parse_fieldwork("3—5 February 2026")
        assert start == date(2026, 2, 3)
        assert end == date(2026, 2, 5)

    def test_ordinal_suffixes_tolerated(self) -> None:
        start, end = op._parse_fieldwork("3rd-5th February 2026")
        assert start == date(2026, 2, 3)
        assert end == date(2026, 2, 5)

    def test_single_day_is_not_supported_and_raises(self) -> None:
        # Both patterns require a "<day>-<day>" range; a lone day never
        # matches either, even with a year present.
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            op._parse_fieldwork("5 February 2026")

    def test_no_pattern_matches_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            op._parse_fieldwork("not a date at all")


# ── _infer_year_from_url ────────────────────────────────────────────────


class TestInferYearFromUrl:
    """Tests for _infer_year_from_url -- path segment, then bare digits, then fallback."""

    def test_path_segment_match(self) -> None:
        # A literal URL, not op.DEFAULT_XLSX_URL -- otherwise this test
        # would silently start testing something else if the default URL's
        # year or path shape ever changed for unrelated reasons.
        url = "https://example.test/wp-content/uploads/2026/02/tables.xlsx"
        assert op._infer_year_from_url(url) == 2026

    def test_bare_digits_first_occurrence_wins(self) -> None:
        # No "/YYYY/" path segment anywhere; the findall fallback picks the
        # first bare "20XX" run, not the last.
        url = "https://example.test/archive-2020-then-2026/file.xlsx"
        assert op._infer_year_from_url(url) == 2020

    def test_no_year_with_fallback_returns_fallback(self) -> None:
        assert op._infer_year_from_url("https://example.test/tables.xlsx", fallback=2025) == 2025

    def test_no_year_without_fallback_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not infer year from URL"):
            op._infer_year_from_url("https://example.test/tables.xlsx")


# ── extract_workbook ────────────────────────────────────────────────────


class TestExtractWorkbook:
    """Tests for extract_workbook -- fetch and load an XLSX from a URL."""

    def test_downloads_and_loads_the_workbook(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = workbook_bytes(build_workbook({"Sheet1": [["ok"]]}))
        calls: list[tuple[str, float | None]] = []

        def fake_urlopen(url: str, timeout: float | None = None) -> FakeUrlResponse:
            calls.append((url, timeout))
            return FakeUrlResponse(payload)

        monkeypatch.setattr(op, "urlopen", fake_urlopen)

        workbook = op.extract_workbook("https://example.test/tables.xlsx")

        assert workbook.sheetnames == ["Sheet1"]
        assert calls == [("https://example.test/tables.xlsx", 60)]


# ── _cell_text ─────────────────────────────────────────────────────────


class TestCellText:
    """Tests for _cell_text -- openpyxl cell value -> stripped string."""

    def test_none_returns_empty_string(self) -> None:
        assert op._cell_text(None) == ""

    def test_string_stripped(self) -> None:
        assert op._cell_text("  hello  ") == "hello"

    def test_integer_converted(self) -> None:
        assert op._cell_text(42) == "42"

    def test_float_converted(self) -> None:
        assert op._cell_text(3.5) == "3.5"

    def test_empty_string(self) -> None:
        assert op._cell_text("") == ""


# ── _to_percentage ───────────────────────────────────────────────────────


class TestToPercentage:
    """Tests for _to_percentage -- raw cell value -> rounded percentage float."""

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="Encountered empty percentage cell"):
            op._to_percentage(None)

    def test_decimal_fraction_multiplied(self) -> None:
        assert op._to_percentage(0.42) == pytest.approx(42.0)

    def test_one_boundary_treated_as_100_percent(self) -> None:
        assert op._to_percentage(1.0) == pytest.approx(100.0)

    def test_zero_boundary(self) -> None:
        assert op._to_percentage(0.0) == pytest.approx(0.0)

    def test_value_above_one_left_unchanged(self) -> None:
        assert op._to_percentage(42) == pytest.approx(42.0)

    def test_rounds_to_nearest_integer(self) -> None:
        assert op._to_percentage(34.6) == pytest.approx(35.0)
        assert op._to_percentage(34.4) == pytest.approx(34.0)

    def test_returns_a_float(self) -> None:
        assert type(op._to_percentage(42)) is float


# ── _find_fieldwork_and_sample ────────────────────────────────────────────


class TestFindFieldworkAndSample:
    """Tests for _find_fieldwork_and_sample -- FRONT PAGE scan, sample None."""

    def test_finds_fieldwork_and_sample_on_the_front_page(self) -> None:
        workbook = build_workbook({"FRONT PAGE": _front_page_rows()})
        start, end, sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert start == date(2026, 2, 3)
        assert end == date(2026, 2, 5)
        assert sample == 2015

    def test_front_page_used_even_when_not_the_first_sheet(self) -> None:
        # "Decoy" is first in sheet order and carries its own, wrong values
        # -- so a mutant that picked workbook.sheetnames[0] regardless of
        # name would pick these up, not merely fail to find anything.
        workbook = build_workbook(
            {
                "Decoy": [[None, None, "Field dates", None, None, "1-2 January 2020"]],
                "FRONT PAGE": _front_page_rows(),
            }
        )
        start, _end, _sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert start == date(2026, 2, 3)

    def test_uses_first_sheet_when_front_page_is_absent(self) -> None:
        workbook = build_workbook(
            {"Data": _front_page_rows(), "Other": [["irrelevant"]]}
        )
        start, _end, sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert start == date(2026, 2, 3)
        assert sample == 2015

    def test_field_dates_label_matches_via_field_date_substring(self) -> None:
        workbook = build_workbook(
            {
                "FRONT PAGE": [
                    [None, None, "PROVISIONAL FIELD DATES", None, None, _FIELDWORK_TEXT]
                ]
            }
        )
        start, _end, _sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert start == date(2026, 2, 3)

    def test_field_date_singular_without_trailing_s_also_matches(self) -> None:
        # A label reading exactly "Field Date" (singular, no trailing "s")
        # satisfies "field date" in label but not "field dates" in label
        # (that clause needs the "s" the label doesn't have). This isolates
        # the real `or`: a mutant that required both clauses (`and`) would
        # reject this label even though the singular clause alone is
        # supposed to be enough.
        workbook = build_workbook(
            {"FRONT PAGE": [[None, None, "Field Date", None, None, _FIELDWORK_TEXT]]}
        )
        start, _end, _sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert start == date(2026, 2, 3)

    def test_sample_label_matches_via_substring(self) -> None:
        workbook = build_workbook(
            {
                "FRONT PAGE": [
                    [None, None, "Field dates", None, None, _FIELDWORK_TEXT],
                    [None, None, "SAMPLE SIZE (WEIGHTED)", None, None, "1,234"],
                ]
            }
        )
        _start, _end, sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert sample == 1234

    def test_last_matching_field_date_row_wins(self) -> None:
        # Nothing breaks the scan on a match; a later matching row
        # overwrites an earlier one rather than the first one winning.
        workbook = build_workbook(
            {
                "FRONT PAGE": [
                    [None, None, "Field dates", None, None, "1-2 January 2020"],
                    [None, None, "Field dates", None, None, "3-5 February 2026"],
                ]
            }
        )
        start, end, _sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert start == date(2026, 2, 3)
        assert end == date(2026, 2, 5)

    def test_last_matching_sample_row_wins(self) -> None:
        workbook = build_workbook(
            {
                "FRONT PAGE": [
                    [None, None, "Field dates", None, None, _FIELDWORK_TEXT],
                    [None, None, "Sample", None, None, "1,000"],
                    [None, None, "Sample", None, None, "2,015"],
                ]
            }
        )
        _start, _end, sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert sample == 2015

    def test_fieldwork_not_found_raises(self) -> None:
        workbook = build_workbook(
            {"FRONT PAGE": [[None, None, "Sample", None, None, "2,015"]]}
        )
        with pytest.raises(ValueError, match="Fieldwork date not found in workbook"):
            op._find_fieldwork_and_sample(workbook, default_year=2026)

    def test_sample_label_not_found_returns_none(self) -> None:
        workbook = build_workbook(
            {"FRONT PAGE": _front_page_rows(sample_text=None)}
        )
        _start, _end, sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert sample is None

    def test_sample_value_with_no_digits_returns_none(self) -> None:
        workbook = build_workbook(
            {"FRONT PAGE": _front_page_rows(sample_text="undisclosed")}
        )
        _start, _end, sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert sample is None

    def test_label_at_row_49_is_found(self) -> None:
        blank_rows: list[list[object]] = [[] for _ in range(48)]
        target: list[object] = [None, None, "Field dates", None, None, _FIELDWORK_TEXT]
        workbook = build_workbook({"FRONT PAGE": [*blank_rows, target]})
        start, _end, _sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert start == date(2026, 2, 3)

    def test_label_at_row_50_is_never_scanned_pins_current_behaviour(self) -> None:
        """The scan covers rows 1-49 only; row 50 is silently unreachable.

        ``for row in range(1, 50)`` yields 1..49, one short of the
        docstring's "up to the first 50 rows". Real Opinium front pages
        place their FIELD DATES / SAMPLE labels in the first 20 rows
        (confirmed against four real downloaded workbooks spanning
        2025-11 to 2026-09), so this boundary has not been observed to
        matter in practice -- pinned as current behaviour, not reported
        as a confirmed production bug.
        """
        blank_rows: list[list[object]] = [[] for _ in range(49)]
        target: list[object] = [None, None, "Field dates", None, None, _FIELDWORK_TEXT]
        workbook = build_workbook({"FRONT PAGE": [*blank_rows, target]})
        with pytest.raises(ValueError, match="Fieldwork date not found in workbook"):
            op._find_fieldwork_and_sample(workbook, default_year=2026)


class TestFindFieldworkAndSampleDigitConcatenation:
    """A latent digit-concatenation defect in the sample-size fallback."""

    def test_multi_number_sample_text_concatenates_into_garbage_pins_current_behaviour(
        self,
    ) -> None:
        """Latent: the mechanism is real, but not observed determining a
        stored sample_size on any of the four real files checked, since
        HeadlineVI's (buggy but always-numeric) base row always supplies
        digits first -- see TestParsePollBaseRowSelection below.

        Real Opinium front pages word the sample line as e.g. "2,054 UK
        adults (18+)" (confirmed against the 2026-02-04 workbook's real
        FRONT PAGE sheet). ``re.sub(r"[^0-9]", "", ...)`` strips every
        non-digit character from the *whole* string, so the sample count
        and the trailing age bracket get concatenated into one number
        instead of only the leading digits being read.
        """
        workbook = build_workbook(
            {
                "FRONT PAGE": [
                    [None, None, "Field dates", None, None, _FIELDWORK_TEXT],
                    [None, None, "Sample", None, None, "2,054 UK adults (18+)"],
                ]
            }
        )
        _start, _end, sample = op._find_fieldwork_and_sample(workbook, default_year=2026)
        assert sample == 205418


# ── _find_headline_sheet ───────────────────────────────────────────────


class TestFindHeadlineSheet:
    """Tests for _find_headline_sheet -- exact name or 'headline' substring."""

    def test_exact_name_case_insensitive(self) -> None:
        workbook = build_workbook({"HEADLINEVI": [["ok"]]})
        assert op._find_headline_sheet(workbook).title == "HEADLINEVI"

    def test_substring_match(self) -> None:
        workbook = build_workbook({"VI Headline Table": [["ok"]]})
        assert op._find_headline_sheet(workbook).title == "VI Headline Table"

    def test_first_match_in_sheet_order_wins(self) -> None:
        workbook = build_workbook(
            {"Headline A": [["a"]], "Headline B": [["b"]]}
        )
        assert op._find_headline_sheet(workbook).title == "Headline A"

    def test_no_match_raises(self) -> None:
        workbook = build_workbook({"Data": [["ok"]], "Other": [["ok"]]})
        with pytest.raises(ValueError, match="Could not find HeadlineVI sheet"):
            op._find_headline_sheet(workbook)


# ── parse_poll: header row ──────────────────────────────────────────────


class TestParsePollHeaderRow:
    """parse_poll's header-row search and its header-content raises."""

    def test_header_row_not_found_raises(self) -> None:
        workbook = _full_workbook(headline_rows=[["nothing", "here"]])
        with pytest.raises(ValueError, match="Could not locate headline header row"):
            op.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_header_row_found_at_row_19_boundary(self) -> None:
        blank_rows: list[list[object]] = [[] for _ in range(18)]
        header: list[object] = [None, "Total", "North", "Mids", "London", "South", "Wales", "Scotland"]
        workbook = _full_workbook(headline_rows=[*blank_rows, header])
        # No base row anywhere: a *different*, later failure proves the
        # header row itself was found.
        with pytest.raises(
            ValueError, match="Could not locate weighted base row in HeadlineVI sheet"
        ):
            op.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_header_row_at_row_20_is_never_scanned_pins_current_behaviour(self) -> None:
        blank_rows: list[list[object]] = [[] for _ in range(19)]
        header: list[object] = [None, "Total", "North", "Mids", "London", "South", "Wales", "Scotland"]
        workbook = _full_workbook(headline_rows=[*blank_rows, header])
        with pytest.raises(ValueError, match="Could not locate headline header row"):
            op.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_missing_regional_header_raises(self) -> None:
        header: list[object] = [None, "Total", "North", "London", "South", "Wales", "Scotland"]
        rows = [header, ["Base: All Respondents (Weighted)", 2015]]
        workbook = _full_workbook(headline_rows=rows)
        expected = "Missing expected regional header 'Mids' in HeadlineVI sheet"
        with pytest.raises(ValueError, match=re.escape(expected)):
            op.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_total_header_spelled_differently_from_detection_still_raises(self) -> None:
        """Header-row detection lowercases "Total" for its match, but the
        later ``"Total" not in header_labels`` check is case-sensitive, so a
        sheet whose column B literally reads "TOTAL" passes detection and
        then fails this check. Real Opinium files always spell it "Total"
        exactly (confirmed across four real downloaded workbooks), so this
        is a documented inconsistency, not an observed production issue.
        """
        header: list[object] = [None, "TOTAL", "North", "Mids", "London", "South", "Wales", "Scotland"]
        rows = [header, ["Base: All Respondents (Weighted)", 2015]]
        workbook = _full_workbook(headline_rows=rows)
        expected = "Missing expected 'Total' header in HeadlineVI sheet"
        with pytest.raises(ValueError, match=re.escape(expected)):
            op.parse_poll(workbook, source_url=_SOURCE_URL)


# ── parse_poll: base row and sample size ──────────────────────────────


class TestParsePollBaseRowAndSample:
    """parse_poll's weighted-base-row search and sample-size resolution."""

    def test_base_row_not_found_raises(self) -> None:
        header: list[object] = [None, "Total", "North", "Mids", "London", "South", "Wales", "Scotland"]
        workbook = _full_workbook(headline_rows=[header, ["Not a base row", 2015]])
        with pytest.raises(
            ValueError, match="Could not locate weighted base row in HeadlineVI sheet"
        ):
            op.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_a_weighted_only_row_is_not_mistaken_for_the_base_row(self) -> None:
        # The search requires BOTH "base:" and "weighted"; a row with only
        # one of the two (here "weighted", no "base:") must be skipped, not
        # treated as an `or` match. The decoy's own number (9999) is
        # distinct from the real base row's (2015), so picking the wrong
        # row would be observable, not a coincidence.
        header: list[object] = [None, "Total", *_MACRO_HEADERS, "Northern Ireland"]
        rows: list[list[object]] = [
            header,
            ["Sample weighted to census targets", 9999],
            ["Base: All Respondents (Weighted)", 2015],
            *_headline_sheet_rows()[2:],
        ]
        workbook = _full_workbook(headline_rows=rows)

        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)

        assert parsed.sample_size == 2015

    def test_a_base_only_row_without_weighted_is_not_mistaken_for_the_base_row(
        self,
    ) -> None:
        # The mirror image of the test above: a "Base:" row that never says
        # "weighted" must not satisfy the search on its own either. This
        # catches a mutant that drops the "weighted" half of the check
        # entirely (keeping only "base:" in label), which the
        # weighted-only decoy above can't catch, since that decoy lacks
        # "base:" too and so wouldn't match under such a mutant regardless.
        header: list[object] = [None, "Total", *_MACRO_HEADERS, "Northern Ireland"]
        rows: list[list[object]] = [
            header,
            ["Base: All respondents", 9999],
            ["Base: All Respondents (Weighted)", 2015],
            *_headline_sheet_rows()[2:],
        ]
        workbook = _full_workbook(headline_rows=rows)

        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)

        assert parsed.sample_size == 2015

    def test_base_row_found_at_header_plus_7_boundary(self) -> None:
        header: list[object] = [None, "Total", "North", "Mids", "London", "South", "Wales", "Scotland"]
        blank_rows: list[list[object]] = [[] for _ in range(6)]
        base: list[object] = ["Base: All Respondents (Weighted)", 2015]
        workbook = _full_workbook(headline_rows=[header, *blank_rows, base])
        # No party rows at all: a different, later failure proves the base
        # row itself was found.
        with pytest.raises(
            ValueError, match=re.escape("Missing expected party rows in workbook:")
        ):
            op.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_base_row_one_past_the_boundary_is_never_scanned_pins_current_behaviour(
        self,
    ) -> None:
        header: list[object] = [None, "Total", "North", "Mids", "London", "South", "Wales", "Scotland"]
        blank_rows: list[list[object]] = [[] for _ in range(7)]
        base: list[object] = ["Base: All Respondents (Weighted)", 2015]
        workbook = _full_workbook(headline_rows=[header, *blank_rows, base])
        with pytest.raises(
            ValueError, match="Could not locate weighted base row in HeadlineVI sheet"
        ):
            op.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_sample_size_prefers_headline_weighted_base_over_front_page(self) -> None:
        front = _front_page_rows(sample_text="9,999")
        headline = _headline_sheet_rows(base_value=2015)
        workbook = _full_workbook(front_page_rows=front, headline_rows=headline)
        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.sample_size == 2015

    def test_sample_size_falls_back_to_front_page_when_base_cell_has_no_digits(
        self,
    ) -> None:
        front = _front_page_rows(sample_text="1,801")
        headline = _headline_sheet_rows(base_value="TBC")
        workbook = _full_workbook(front_page_rows=front, headline_rows=headline)
        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.sample_size == 1801

    def test_sample_size_undetermined_raises(self) -> None:
        front = _front_page_rows(sample_text=None)
        headline = _headline_sheet_rows(base_value="TBC")
        workbook = _full_workbook(front_page_rows=front, headline_rows=headline)
        with pytest.raises(ValueError, match="Could not determine sample size"):
            op.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_unweighted_row_matches_before_the_real_weighted_row_pins_current_behaviour(
        self,
    ) -> None:
        """The mechanism is confirmed by reading the code: the search
        requires "base:" and "weighted" as substrings, and "unweighted"
        contains "weighted", so an "(Unweighted)" row anywhere in the scan
        window is matched ahead of a later "(Weighted)" row -- see this
        module's docstring for the full write-up, including why the
        confidence here is scoped to poll 970 rather than claimed for
        every Opinium file.

        The values here (1538 vs 1525) are the real 2026-05-06 workbook's
        actual Unweighted/Weighted base counts (live DB poll 970, whose
        stored sample_size is 1538 -- re-checkable any time with a
        read-only query), not synthetic round numbers, so this reproduces
        the checked instance precisely rather than an arbitrary pair that
        happens to differ.
        """
        header: list[object] = [
            None, "Total", *_MACRO_HEADERS, "Northern Ireland",
        ]
        rows: list[list[object]] = [
            header,
            ["Base: All giving voting intention (Unweighted)", 1538],
            ["Base: All giving voting intention (Weighted)", 1525],
            *_headline_sheet_rows()[2:],
        ]
        workbook = _full_workbook(headline_rows=rows)

        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)

        assert parsed.sample_size == 1538


# ── parse_poll: party rows ─────────────────────────────────────────────


class TestParsePollPartyRows:
    """parse_poll's party-row scan: extraction, skips, the stop label, raises."""

    def test_full_extraction_maps_every_alias_and_macro(self) -> None:
        workbook = _full_workbook()
        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)

        assert len(parsed.party_macro_percentages) == 8
        assert parsed.party_macro_percentages["Conservative"] == {
            op.NATIONAL_KEY: 2.0,
            "North": 3.0,
            "Mids": 4.0,
            "London": 5.0,
            "South": 6.0,
            "Wales": 7.0,
            "Scotland": 8.0,
            "Northern Ireland": 9.0,
        }
        assert parsed.party_macro_percentages["Liberal Democrats"][op.NATIONAL_KEY] == 18.0
        assert parsed.party_macro_percentages["Reform UK"]["South"] == 30.0
        assert parsed.party_macro_percentages["Other"]["Scotland"] == 48.0
        assert parsed.sample_size == 2015
        assert parsed.fieldwork_start == date(2026, 2, 3)
        assert parsed.fieldwork_end == date(2026, 2, 5)

    def test_alternate_party_spellings_map_to_the_same_canonical_name(self) -> None:
        headline = _headline_sheet_rows(
            omit_parties=("Con", "Lab", "Lib Dem", "Reform"),
            extra_rows=[
                ("Conservative", 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0),
                ("Labour", 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0),
                ("Liberal Democrat", 18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0, 25.0),
                ("Reform UK", 26.0, 27.0, 28.0, 29.0, 30.0, 31.0, 32.0, 33.0),
            ],
        )
        workbook = _full_workbook(headline_rows=headline)
        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.party_macro_percentages["Conservative"][op.NATIONAL_KEY] == 2.0
        assert parsed.party_macro_percentages["Labour"][op.NATIONAL_KEY] == 10.0
        assert parsed.party_macro_percentages["Liberal Democrats"][op.NATIONAL_KEY] == 18.0
        assert parsed.party_macro_percentages["Reform UK"][op.NATIONAL_KEY] == 26.0

    def test_blank_row_is_harmless(self) -> None:
        blank: list[object] = [None] * 9
        headline = _headline_sheet_rows(extra_rows=[blank])
        workbook = _full_workbook(headline_rows=headline)
        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)
        assert len(parsed.party_macro_percentages) == 8

    def test_unmapped_party_row_is_skipped(self) -> None:
        headline = _headline_sheet_rows(
            extra_rows=[("Don't Know", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)]
        )
        workbook = _full_workbook(headline_rows=headline)
        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)
        assert "Don't Know" not in parsed.party_macro_percentages
        assert len(parsed.party_macro_percentages) == 8

    def test_stop_label_breaks_the_scan_before_later_parties(self) -> None:
        rows = _headline_sheet_rows()
        header, base, *party_rows = rows
        stop_row: list[object] = ["Return to index"]
        rows = [header, base, party_rows[0], party_rows[1], stop_row, *party_rows[2:]]
        workbook = _full_workbook(headline_rows=rows)
        expected = "Missing expected party rows in workbook: ['Green', 'Liberal Democrats', 'Reform UK']"
        with pytest.raises(ValueError, match=re.escape(expected)):
            op.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_missing_required_party_raises(self) -> None:
        headline = _headline_sheet_rows(omit_parties=("Reform",))
        workbook = _full_workbook(headline_rows=headline)
        expected = "Missing expected party rows in workbook: ['Reform UK']"
        with pytest.raises(ValueError, match=re.escape(expected)):
            op.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_party_row_found_at_base_plus_29_boundary(self) -> None:
        header: list[object] = [None, "Total", *_MACRO_HEADERS, "Northern Ireland"]
        base: list[object] = ["Base: All Respondents (Weighted)", 2015]
        early_parties = [
            [raw, *values] for raw, values in _PARTY_VALUES.items() if raw != "Green"
        ]
        blank_rows: list[list[object]] = [[] for _ in range(23)]
        green_row: list[object] = ["Green", *_PARTY_VALUES["Green"]]
        rows = [header, base, *early_parties, *blank_rows, green_row]
        workbook = _full_workbook(headline_rows=rows)

        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)

        assert parsed.party_macro_percentages["Green"][op.NATIONAL_KEY] == 34.0

    def test_party_row_one_past_the_boundary_is_never_scanned_pins_current_behaviour(
        self,
    ) -> None:
        header: list[object] = [None, "Total", *_MACRO_HEADERS, "Northern Ireland"]
        base: list[object] = ["Base: All Respondents (Weighted)", 2015]
        early_parties = [
            [raw, *values] for raw, values in _PARTY_VALUES.items() if raw != "Green"
        ]
        blank_rows: list[list[object]] = [[] for _ in range(24)]
        green_row: list[object] = ["Green", *_PARTY_VALUES["Green"]]
        rows = [header, base, *early_parties, *blank_rows, green_row]
        workbook = _full_workbook(headline_rows=rows)

        expected = "Missing expected party rows in workbook: ['Green']"
        with pytest.raises(ValueError, match=re.escape(expected)):
            op.parse_poll(workbook, source_url=_SOURCE_URL)


class TestParsePollNorthernIreland:
    """The optional Northern Ireland macro column: present vs absent."""

    def test_absent_from_the_sheet_defaults_every_party_to_zero(self) -> None:
        headline = _headline_sheet_rows(include_ni_column=False)
        workbook = _full_workbook(headline_rows=headline)
        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)
        for macro_values in parsed.party_macro_percentages.values():
            assert macro_values["Northern Ireland"] == 0.0

    def test_present_in_the_sheet_uses_the_real_value(self) -> None:
        workbook = _full_workbook()  # include_ni_column=True by default
        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.party_macro_percentages["Conservative"]["Northern Ireland"] == 9.0


class TestParsePollOptionalPartyRecognition:
    """SNP and Plaid Cymru rows, even if present, can never be recognised.

    Confirmed real by live-DB evidence (see this module's docstring): every
    one of the 455 stored Scottish National Party poll_rows, and every one
    of the 455 stored Plaid Cymru poll_rows (910 rows total), for pollster
    "opinium" is exactly 0.0 -- and each poll's stored regional shares show
    Scotland and Wales falling well short of 100%, by roughly what those
    parties typically poll there, consistent with the real source sheets
    carrying rows for both that this module actively drops. These tests
    force the row into a synthetic sheet to prove the mechanism directly:
    PARTY_NAME_MAP has no entry for either party under any spelling, so a
    HeadlineVI row for either is silently skipped regardless of what the
    real sheet carries.
    """

    @pytest.mark.parametrize(
        "raw_label", ["SNP", "Plaid Cymru", "Scottish National Party"]
    )
    def test_a_present_row_is_still_silently_skipped_pins_current_behaviour(
        self, raw_label: str
    ) -> None:
        assert raw_label not in op.PARTY_NAME_MAP  # precondition of the bug
        headline = _headline_sheet_rows(
            extra_rows=[(raw_label, 77.0, 78.0, 79.0, 80.0, 81.0, 82.0, 83.0, 84.0)]
        )
        workbook = _full_workbook(headline_rows=headline)

        parsed = op.parse_poll(workbook, source_url=_SOURCE_URL)

        assert all(
            v == 0.0 for v in parsed.party_macro_percentages["Scottish National Party"].values()
        )
        assert all(
            v == 0.0 for v in parsed.party_macro_percentages["Plaid Cymru"].values()
        )


# ── parse_poll: fieldwork year inference ──────────────────────────────


class TestParsePollFieldworkYear:
    """parse_poll's year resolution: URL, then fieldwork_year_hint."""

    def test_year_inferred_from_source_url(self) -> None:
        # A literal URL with its own year, not op.DEFAULT_XLSX_URL -- see
        # TestInferYearFromUrl.test_path_segment_match for why.
        workbook = _full_workbook(
            front_page_rows=_front_page_rows(fieldwork_text="3-5 Feb")
        )
        parsed = op.parse_poll(
            workbook, source_url="https://example.test/2026/tables.xlsx"
        )
        assert parsed.fieldwork_start == date(2026, 2, 3)

    def test_fieldwork_year_hint_used_when_url_has_no_year(self) -> None:
        workbook = _full_workbook(
            front_page_rows=_front_page_rows(fieldwork_text="3-5 Feb")
        )
        parsed = op.parse_poll(
            workbook,
            source_url="https://example.test/tables.xlsx",
            fieldwork_year_hint=2027,
        )
        assert parsed.fieldwork_start == date(2027, 2, 3)

    def test_no_year_anywhere_raises(self) -> None:
        workbook = _full_workbook(
            front_page_rows=_front_page_rows(fieldwork_text="3-5 Feb")
        )
        with pytest.raises(ValueError, match="Could not infer year from URL"):
            op.parse_poll(workbook, source_url="https://example.test/tables.xlsx")


# ── build_import_plan ─────────────────────────────────────────────────


def _expected_regions_mapping(region_ids: Mapping[str, int]) -> str:
    """The seven "macro:id[,id...]" lines build_import_plan produces.

    Spelled literally (not read from MACRO_TO_INTERNAL_REGIONS) so a
    mutated production mapping is caught here rather than mirrored.
    """
    return "\n".join(
        [
            "North:{},{},{}".format(
                region_ids["North East England"],
                region_ids["North West England"],
                region_ids["Yorkshire and The Humber"],
            ),
            "Mids:{},{}".format(
                region_ids["East Midlands"], region_ids["West Midlands"]
            ),
            f"London:{region_ids['London']}",
            "South:{},{},{}".format(
                region_ids["East of England"],
                region_ids["South East England"],
                region_ids["South West England"],
            ),
            f"Wales:{region_ids['Wales']}",
            f"Scotland:{region_ids['Scotland']}",
            f"Northern Ireland:{region_ids['Northern Ireland']}",
        ]
    )


class TestBuildImportPlanRaises:
    """build_import_plan's raises for a missing map, region or party."""

    def test_missing_map_raises_before_fetching(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _forbid_fetch(monkeypatch)
        with pytest.raises(ValueError, match=re.escape("Map not found: 'Nope'")):
            op.build_import_plan(db, map_name="Nope")

    def test_missing_internal_region_raises_before_fetching(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom_map = db.add_map("Partial Map", parliament="westminster")
        missing = "Scotland"
        for names in op.MACRO_TO_INTERNAL_REGIONS.values():
            for name in names:
                if name != missing:
                    db.add_region(custom_map.id, name)
        _forbid_fetch(monkeypatch)

        expected = f"Region {missing!r} not found in map {custom_map.name!r}"
        with pytest.raises(ValueError, match=re.escape(expected)):
            op.build_import_plan(db, map_name=custom_map.name)

    def test_missing_party_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom_map = db.add_map("Full Regions Map", parliament="westminster")
        for names in op.MACRO_TO_INTERNAL_REGIONS.values():
            for name in names:
                db.add_region(custom_map.id, name)
        for name in sorted(set(op.PARTY_NAME_MAP.values()) - {"Green"}):
            db.add_party(name)
        # Also seed the two parties the missing_parties check doesn't look
        # for (see the pinned KeyError bug below), so this test exercises
        # only the "Green" gap and stays correct if that bug is ever fixed.
        db.add_party("Scottish National Party")
        db.add_party("Plaid Cymru")

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(op, "extract_workbook", fake_extract_workbook)

        expected = "Missing parties in database (run party importer first): ['Green']"
        with pytest.raises(ValueError, match=re.escape(expected)):
            op.build_import_plan(db, map_name=custom_map.name)

    def test_missing_snp_or_plaid_cymru_bypasses_the_check_and_raises_keyerror_pins_current_behaviour(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confirmed real by direct code reading, not by live data: the real
        party importer always seeds "Scottish National Party" and
        "Plaid Cymru" (they're in WESTMINSTER_PARTY_NAMES too), so this
        precondition can't be checked against the live DB -- pinned as a
        mechanism, not claimed to have fired in production.

        ``missing_parties`` only checks ``set(PARTY_NAME_MAP.values())`` --
        six names, not the eight parties ``parse_poll`` actually guarantees
        in ``parsed.party_macro_percentages`` (which always includes
        "Scottish National Party" and "Plaid Cymru" via its own zero-fill).
        A database missing either of those two passes this check silently,
        then crashes with an unhandled ``KeyError`` -- not the intended
        ``ValueError`` -- when the row-building loop below indexes
        ``party_by_name[party_name]`` for it.
        """
        custom_map = db.add_map("Full Regions Map", parliament="westminster")
        for names in op.MACRO_TO_INTERNAL_REGIONS.values():
            for name in names:
                db.add_region(custom_map.id, name)
        for name in sorted(set(op.PARTY_NAME_MAP.values())):
            db.add_party(name)
        # Deliberately omit "Scottish National Party" and "Plaid Cymru".

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(op, "extract_workbook", fake_extract_workbook)

        with pytest.raises(KeyError) as excinfo:
            op.build_import_plan(db, map_name=custom_map.name)
        assert excinfo.value.args[0] == "Scottish National Party"


class TestBuildImportPlanFullBuild:
    """build_import_plan over the seeded Westminster world: mapping and rows."""

    def test_regions_mapping_and_row_count(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(op, "extract_workbook", fake_extract_workbook)

        plan = op.build_import_plan(
            db, xlsx_url=op.DEFAULT_XLSX_URL, map_name=world.map_name
        )

        assert plan.map_id == world.map_id
        assert plan.map_name == world.map_name
        assert plan.regions_mapping == _expected_regions_mapping(world.region_ids)
        assert plan.pollster_identifier == op.DEFAULT_POLLSTER_IDENTIFIER
        assert plan.pollster_exists is False
        assert plan.pollster_name == "Opinium"
        assert plan.pollster_id is None
        assert plan.poll_exists is False
        assert plan.poll_id is None
        assert plan.source_url == op.DEFAULT_XLSX_URL

        # 8 required parties x (1 national + 12 DB regions).
        assert len(plan.rows) == 8 * 13
        national_rows = [row for row in plan.rows if row.region_id is None]
        assert len(national_rows) == 8

        conservative_national = next(
            row
            for row in plan.rows
            if row.party_name == "Conservative" and row.region_id is None
        )
        assert conservative_national.percentage == 2.0
        assert conservative_national.region_name == "National"
        assert conservative_national.party_id == world.party_ids["Conservative"]

        # South spans three internal regions; each must carry Reform UK's
        # own South macro value, not some other macro's.
        reform_south_regions = {
            world.region_ids["East of England"],
            world.region_ids["South East England"],
            world.region_ids["South West England"],
        }
        reform_south_rows = [
            row
            for row in plan.rows
            if row.party_name == "Reform UK" and row.region_id in reform_south_regions
        ]
        assert len(reform_south_rows) == 3
        assert {row.percentage for row in reform_south_rows} == {30.0}

        reform_ni = next(
            row
            for row in plan.rows
            if row.party_name == "Reform UK"
            and row.region_id == world.region_ids["Northern Ireland"]
        )
        assert reform_ni.percentage == 33.0

    def test_pollster_and_poll_existing_are_detected(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Opinium Research", "opinium", weight=1.0)
        poll = db.add_poll(
            pollster.id,
            world.map_id,
            date(2026, 2, 3),
            date(2026, 2, 5),
            sample_size=2015,
        )

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(op, "extract_workbook", fake_extract_workbook)

        plan = op.build_import_plan(db, map_name=world.map_name)

        assert plan.pollster_exists is True
        assert plan.pollster_id == pollster.id
        assert plan.pollster_name == "Opinium Research"
        assert plan.poll_exists is True
        assert plan.poll_id == poll.id

    def test_pollster_existing_without_a_matching_poll_leaves_poll_absent(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        db.add_pollster("Opinium Research", "opinium", weight=1.0)

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(op, "extract_workbook", fake_extract_workbook)

        plan = op.build_import_plan(db, map_name=world.map_name)

        assert plan.pollster_exists is True
        assert plan.poll_exists is False
        assert plan.poll_id is None


class TestBuildImportPlanNationalAndMacroDefaults:
    """A party whose parsed data lacks NATIONAL_KEY or a macro key still gets
    a row for it, defaulted to 0.0 -- build_import_plan always emits a
    National row and a row per DB region for every party in
    parsed.party_macro_percentages (unlike some sibling importers, it never
    skips the row outright).

    parse_poll always includes NATIONAL_KEY (and every macro key) for every
    party in real workbooks, so this monkeypatches parse_poll directly to
    exercise build_import_plan's own defensive .get(..., 0.0) fallbacks.
    """

    def test_missing_national_and_macro_keys_default_to_zero_but_the_row_is_still_created(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world

        def fake_extract_workbook(*_a: object, **_k: object) -> object:
            return object()

        def fake_parse_poll(
            _workbook: object,
            *,
            source_url: str,
            fieldwork_year_hint: int | None = None,
        ) -> op.ParsedPoll:
            return op.ParsedPoll(
                sample_size=1000,
                fieldwork_start=date(2026, 1, 1),
                fieldwork_end=date(2026, 1, 2),
                party_macro_percentages={"Labour": {"London": 55.0}},
            )

        monkeypatch.setattr(op, "extract_workbook", fake_extract_workbook)
        monkeypatch.setattr(op, "parse_poll", fake_parse_poll)

        plan = op.build_import_plan(db, map_name=world.map_name)

        labour_rows = [row for row in plan.rows if row.party_name == "Labour"]
        # 1 national row + 1 row per DB region, even though the fake parse
        # only supplied a single macro's figure.
        assert len(labour_rows) == 1 + len(world.region_ids)

        labour_national = next(row for row in labour_rows if row.region_id is None)
        assert labour_national.percentage == 0.0
        assert labour_national.region_name == "National"

        labour_london = next(
            row for row in labour_rows if row.region_id == world.region_ids["London"]
        )
        assert labour_london.percentage == 55.0
        # A macro other than London's got no figure at all -- the defensive
        # macro-level .get(..., 0.0) fallback, not just the national one.
        labour_wales = next(
            row for row in labour_rows if row.region_id == world.region_ids["Wales"]
        )
        assert labour_wales.percentage == 0.0


class TestBuildImportPlanFieldworkYearHint:
    """build_import_plan forwards fieldwork_year_hint through to parse_poll."""

    def test_year_hint_used_when_fieldwork_text_has_no_year(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        workbook = _full_workbook(
            front_page_rows=_front_page_rows(fieldwork_text="3-5 Feb")
        )

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return workbook

        monkeypatch.setattr(op, "extract_workbook", fake_extract_workbook)

        # The URL carries no year of its own, so a dropped hint would make
        # default_year None and raise, rather than silently falling back to
        # a URL-inferred year that happens to match.
        plan = op.build_import_plan(
            db,
            xlsx_url="https://example.test/tables.xlsx",
            map_name=world.map_name,
            fieldwork_year_hint=2027,
        )

        assert plan.parsed.fieldwork_start == date(2027, 2, 3)
        assert plan.parsed.fieldwork_end == date(2027, 2, 5)


# ── _cli_preview ──────────────────────────────────────────────────────


def _preview_plan(**overrides: object) -> op.ImportPlan:
    """Build an ImportPlan for _cli_preview tests: one Labour/Wales row."""
    parsed = op.ParsedPoll(
        sample_size=2015,
        fieldwork_start=date(2026, 2, 3),
        fieldwork_end=date(2026, 2, 5),
        party_macro_percentages={"Labour": {op.NATIONAL_KEY: 32.0, "Wales": 22.0}},
    )
    row = op.PlannedPollRow(
        party_id=2,
        party_name="Labour",
        region_id=10,
        region_name="Wales",
        percentage=22.0,
    )
    defaults: dict[str, object] = {
        "pollster_identifier": "opinium",
        "pollster_name": "Opinium",
        "pollster_id": None,
        "pollster_exists": False,
        "regions_mapping": "Wales:10",
        "map_id": 1,
        "map_name": "UK Constituencies post 2022",
        "source_url": op.DEFAULT_XLSX_URL,
        "parsed": parsed,
        "poll_id": None,
        "poll_exists": False,
        "rows": [row],
    }
    defaults.update(overrides)
    return op.ImportPlan.model_validate(defaults)


class TestCliPreview:
    """_cli_preview's dry-run summary, all four pollster/poll existence combos."""

    def test_new_pollster_and_new_poll(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        op._cli_preview(_preview_plan())

        lines = capsys.readouterr().out.splitlines()
        assert "Parsed poll: fieldwork=2026-02-03 to 2026-02-05, sample=2015" in lines
        assert "[dry-run] would create pollster: opinium" in lines
        assert "[dry-run] would create poll" in lines
        assert (
            "[dry-run] would insert row: party=Labour, region=Wales, "
            "region_id=10, pct=22.00"
        ) in lines

    def test_existing_pollster_and_existing_poll(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        op._cli_preview(
            _preview_plan(pollster_exists=True, poll_exists=True, poll_id=7)
        )

        lines = capsys.readouterr().out.splitlines()
        assert "pollster exists: opinium" in lines
        assert "poll exists: 7" in lines
        assert "[dry-run] would create pollster: opinium" not in lines
        assert "[dry-run] would create poll" not in lines

    def test_poll_exists_true_with_no_id_still_previews_creation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        op._cli_preview(_preview_plan(poll_exists=True, poll_id=None))

        lines = capsys.readouterr().out.splitlines()
        assert "poll exists: None" not in lines
        assert "[dry-run] would create poll" in lines

    def test_poll_id_set_but_poll_exists_false_still_previews_creation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        op._cli_preview(_preview_plan(poll_exists=False, poll_id=42))

        lines = capsys.readouterr().out.splitlines()
        assert "poll exists: 42" not in lines
        assert "[dry-run] would create poll" in lines

    def test_no_rows_prints_no_row_lines(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        op._cli_preview(_preview_plan(rows=[]))

        out = capsys.readouterr().out
        assert "[dry-run] would insert row" not in out


# ── main ──────────────────────────────────────────────────────────────


class TestMain:
    """main's dry-run/commit branches, sys.argv and Database patched to db."""

    def _run(
        self,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        workbook: Workbook,
        *argv: str,
        fetched_urls: list[str] | None = None,
    ) -> None:
        """Run main() with Database and extract_workbook faked, via sys.argv.

        If ``fetched_urls`` is given, every URL ``extract_workbook`` is
        called with is recorded, so a test can prove ``--xlsx-url``
        actually reaches the fetch rather than the CLI default silently
        being used.
        """

        def fake_database(*_a: object, **_k: object) -> Database:
            return db

        def fake_extract_workbook(xlsx_url: str) -> Workbook:
            if fetched_urls is not None:
                fetched_urls.append(xlsx_url)
            return workbook

        monkeypatch.setattr(op, "Database", fake_database)
        monkeypatch.setattr(op, "extract_workbook", fake_extract_workbook)
        monkeypatch.setattr(sys, "argv", ["opinium_import.py", *argv])
        op.main()

    def test_cli_defaults_pin(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        assert op.DEFAULT_MAP_NAME == world.map_name
        assert op.DEFAULT_POLLSTER_IDENTIFIER == "opinium"

        self._run(db, monkeypatch, _full_workbook(), "--dry-run")

        out = capsys.readouterr().out
        assert f"Fetching XLSX: {op.DEFAULT_XLSX_URL}" in out
        assert "[dry-run] would create pollster: opinium" in out.splitlines()

    def test_dry_run_previews_without_writing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        self._run(
            db, monkeypatch, _full_workbook(), "--map-name", world.map_name, "--dry-run"
        )

        out = capsys.readouterr().out
        assert "[dry-run] would create pollster: opinium" in out.splitlines()
        assert len(db.get_all_pollsters()) == 0

    def test_commit_with_non_default_arguments_forwards_them(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--xlsx-url, --map-name and --pollster-identifier are all forwarded.

        A second, non-default map proves ``--map-name`` was read rather than
        falling back to ``DEFAULT_MAP_NAME`` (which equals the seeded
        world's map name, so the other tests can't tell forwarding from a
        default).
        """
        second_map = db.add_map("Second Opinium Map", parliament="westminster")
        # A literal tuple, not op.MACRO_TO_INTERNAL_REGIONS.values(), so a
        # wrong production mapping can't quietly go unnoticed here too.
        second_map_regions = (
            "East Midlands",
            "East of England",
            "London",
            "North East England",
            "North West England",
            "Northern Ireland",
            "Scotland",
            "South East England",
            "South West England",
            "Wales",
            "West Midlands",
            "Yorkshire and The Humber",
        )
        for name in second_map_regions:
            db.add_region(second_map.id, name)
        custom_url = "https://example.test/2026/custom-opinium.xlsx"
        fetched_urls: list[str] = []

        self._run(
            db,
            monkeypatch,
            _full_workbook(),
            "--map-name",
            second_map.name,
            "--xlsx-url",
            custom_url,
            "--pollster-identifier",
            "opinium_custom",
            fetched_urls=fetched_urls,
        )

        assert fetched_urls == [custom_url]
        assert db.get_pollster_by_identifier(op.DEFAULT_POLLSTER_IDENTIFIER) is None
        pollster = db.get_pollster_by_identifier("opinium_custom")
        assert pollster is not None
        polls = db.get_polls_by_pollster(pollster.id)
        assert len(polls) == 1
        assert polls[0].map_id == second_map.id
        assert polls[0].source_url == custom_url

    def test_fieldwork_year_hint_forwarded(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        workbook = _full_workbook(
            front_page_rows=_front_page_rows(fieldwork_text="3-5 Feb")
        )

        self._run(
            db,
            monkeypatch,
            workbook,
            "--map-name",
            world.map_name,
            # No year in this URL, and DEFAULT_XLSX_URL (which the CLI would
            # otherwise fall back to) has one -- so this proves the hint
            # itself was forwarded, not that a URL-inferred year happened
            # to match.
            "--xlsx-url",
            "https://example.test/tables.xlsx",
            "--fieldwork-year-hint",
            "2027",
            "--dry-run",
        )

        out = capsys.readouterr().out
        assert "Parsed poll: fieldwork=2027-02-03 to 2027-02-05" in out

    def test_commit_creates_pollster_poll_and_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        self._run(db, monkeypatch, _full_workbook(), "--map-name", world.map_name)

        lines = capsys.readouterr().out.splitlines()
        assert "created pollster: opinium" in lines
        assert "inserted poll rows: 104" in lines
        assert "deleted existing rows" not in "\n".join(lines)
        pollster = db.get_pollster_by_identifier("opinium")
        assert pollster is not None
        assert pollster.weight == 1.0
        polls = db.get_polls_by_pollster(pollster.id)
        assert len(polls) == 1
        assert f"created poll: {polls[0].id}" in lines
        assert len(db.get_rows_for_poll(polls[0].id)) == 104

    def test_rerun_skips_then_replace_rows_overwrites(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        argv = ("--map-name", world.map_name)

        self._run(db, monkeypatch, _full_workbook(), *argv)
        capsys.readouterr()  # drain, so the next read can't see this run's output
        pollster = db.get_pollster_by_identifier("opinium")
        assert pollster is not None
        poll_id = db.get_polls_by_pollster(pollster.id)[0].id
        assert len(db.get_rows_for_poll(poll_id)) == 104

        self._run(db, monkeypatch, _full_workbook(), *argv)
        lines = capsys.readouterr().out.splitlines()
        assert "pollster exists: opinium" in lines
        assert f"poll exists: {poll_id}" in lines
        assert (
            f"poll {poll_id} already has rows; use --replace-rows to overwrite"
        ) in lines
        assert len(db.get_rows_for_poll(poll_id)) == 104

        self._run(db, monkeypatch, _full_workbook(), *argv, "--replace-rows")
        lines = capsys.readouterr().out.splitlines()
        assert "deleted existing rows: 104" in lines
        assert "inserted poll rows: 104" in lines
        assert len(db.get_rows_for_poll(poll_id)) == 104
