"""Tests for the Techne PDF poll importer.

``commit_import_plan`` and ``_find_existing_poll`` are already covered, across
all eleven Westminster importers, by ``test_westminster_importers_commit.py``
(Techne is in ``VARIANT_A``). This file covers everything else: the PDF-fetch
step (including the Wayback Machine ``if_`` raw-content retry), the PDF-text
parsing helpers, ``build_import_plan``'s national-only row handling (Techne is
the one Westminster importer with no regional breakdown at all), ``_cli_preview``
and ``main``.

Every PDF-text layout below is synthetic: built to fit the shapes the parsing
functions match on, not sourced from a real Techne PDF. Two exceptions, both
in ``TestParseSampleSize``: a wrong-wave-figure test and a digit-concatenation
test, whose pinned mechanisms were independently verified against real,
currently-hosted Techne PDFs (including reinstalling an older pypdf into a
scratch environment) and against all 27 stored Techne polls in the live DB --
see each test's docstring for the verification.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import date
from urllib.request import Request

import pytest

from db import Database
from polls.importers.westminster import techne_import
from polls.importers.westminster.techne_import import (
    PARTY_NAME_MAP,
    ImportPlan,
    ParsedPoll,
    PlannedPollRow,
    _cli_preview,
    _infer_year,
    _month_number,
    _parse_fieldwork,
    _parse_party_percentages,
    _parse_sample_size,
    _party_percentage_from_line,
    build_import_plan,
    extract_pdf_text,
    parse_poll,
)
from tests.uk_fixtures import FakeUrlResponse, WestminsterWorld

# ── Local fixtures and seed helpers ─────────────────────────────────────────
#
# Most build_import_plan/main tests below take the westminster_world fixture.
# One test genuinely can't: test_missing_parties_raises (westminster_world
# seeds every required party, so it can never exhibit "parties missing"). That
# one seeds its own minimal map with _seed_map, then adds all-but-one party
# directly via db.add_party.

_TEST_MAP_NAME = "Techne Test Map"

_REQUIRED_PARTY_NAMES: tuple[str, ...] = tuple(sorted(set(PARTY_NAME_MAP.values())))

_PARSED_START = date(2026, 2, 11)
_PARSED_END = date(2026, 2, 13)
_PARSED_SAMPLE = 1636


def _seed_map(db: Database, name: str) -> int:
    """Seed a map named ``name`` with no regions and no parties.

    Techne never looks up a region, so no region needs seeding here.
    """
    return db.add_map(name, parliament="westminster").id


def _parsed_poll(party_percentages: dict[str, float] | None = None) -> ParsedPoll:
    """Build a ``ParsedPoll`` with fixed fieldwork/sample-size for plan tests."""
    return ParsedPoll(
        sample_size=_PARSED_SAMPLE,
        fieldwork_start=_PARSED_START,
        fieldwork_end=_PARSED_END,
        party_percentages=dict(party_percentages or {}),
    )


def _parse_poll_stub(_text: str, *, inferred_year: int) -> ParsedPoll:
    """A ``parse_poll`` stand-in returning a single-party national-only poll."""
    return _parsed_poll(party_percentages={"Labour": 30.0})


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a real network call impossible from this file, even by accident.

    Every fixture URL below is routed through a monkeypatched fetch point, so
    nothing actually reaches ``urlopen`` -- but that is incidental, not
    enforced. Tests that need a specific ``urlopen`` behaviour monkeypatch it
    themselves inside the test body, which runs after this fixture and
    overrides it.
    """

    def _blocked(*_a: object, **_k: object) -> object:
        raise AssertionError("test attempted a real network call via urlopen")

    monkeypatch.setattr(techne_import, "urlopen", _blocked)


# ── _month_number ────────────────────────────────────────────────────────────


class TestMonthNumber:
    """Tests for _month_number -- month name/abbreviation to integer."""

    def test_full_names(self) -> None:
        assert _month_number("January") == 1
        assert _month_number("February") == 2
        assert _month_number("March") == 3
        assert _month_number("April") == 4
        assert _month_number("May") == 5
        assert _month_number("June") == 6
        assert _month_number("July") == 7
        assert _month_number("August") == 8
        assert _month_number("September") == 9
        assert _month_number("October") == 10
        assert _month_number("November") == 11
        assert _month_number("December") == 12

    def test_abbreviations(self) -> None:
        assert _month_number("Jan") == 1
        assert _month_number("Feb") == 2
        assert _month_number("Sep") == 9
        assert _month_number("Sept") == 9
        assert _month_number("Dec") == 12

    def test_case_insensitive(self) -> None:
        assert _month_number("FEBRUARY") == 2
        assert _month_number("february") == 2
        assert _month_number("FeBrUaRy") == 2

    def test_trailing_period_stripped(self) -> None:
        assert _month_number("Feb.") == 2

    def test_unrecognized_returns_none(self) -> None:
        assert _month_number("Fooruary") is None
        assert _month_number("") is None
        assert _month_number("13") is None


# ── _infer_year ───────────────────────────────────────────────────────────────


class TestInferYear:
    """Tests for _infer_year -- four-digit year extraction from PDF URLs."""

    def test_year_as_path_segment(self) -> None:
        url = "https://www.techneuk.com/wp-content/uploads/2026/02/R162-DATA.pdf"
        assert _infer_year(url) == 2026

    def test_year_as_bare_occurrence(self) -> None:
        url = "https://cdn.example.com/techne_2025_v2.pdf"
        assert _infer_year(url) == 2025

    def test_path_segment_preferred_over_bare(self) -> None:
        url = "https://cdn.example.com/2024archive/2026/R162-DATA.pdf"
        assert _infer_year(url) == 2026

    def test_takes_first_of_several_bare_occurrences(self) -> None:
        # Neither is a /YYYY/ path segment, so the bare-occurrence branch is
        # used; it must take the first, not the last.
        url = "https://cdn.example.com/2025-report-2026-update.pdf"
        assert _infer_year(url) == 2025

    def test_fallback_when_no_year_in_url(self) -> None:
        url = "https://cdn.example.com/techne-data.pdf"
        assert _infer_year(url, fallback=2025) == 2025

    def test_no_year_no_fallback_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not infer year from PDF URL"):
            _infer_year("https://cdn.example.com/techne-data.pdf")


# ── extract_pdf_text ─────────────────────────────────────────────────────────


class _FakePdfPage:
    """A ``pypdf`` page stand-in returning fixed text (or None)."""

    def __init__(self, text: str | None) -> None:
        self._text = text

    def extract_text(self) -> str | None:
        """Return the fixed text this page was built with."""
        return self._text


class _FakePdfReader:
    """A ``pypdf.PdfReader`` stand-in exposing fixed ``pages``."""

    def __init__(self, pages_text: Sequence[str | None]) -> None:
        self.pages = [_FakePdfPage(text) for text in pages_text]


def _fake_pdf_reader_factory(
    pages_text: Sequence[str | None],
) -> Callable[..., _FakePdfReader]:
    """Return a ``PdfReader``-shaped factory ignoring its (path) argument."""

    def factory(*_a: object, **_k: object) -> _FakePdfReader:
        return _FakePdfReader(pages_text)

    return factory


class TestExtractPdfText:
    """Tests for extract_pdf_text -- fetch, validate and extract PDF text."""

    def test_returns_joined_page_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            techne_import,
            "urlopen",
            lambda *_a, **_k: FakeUrlResponse(b"%PDF-1.4 bytes"),
        )
        monkeypatch.setattr(
            techne_import, "PdfReader", _fake_pdf_reader_factory(["Page one", "Page two"])
        )

        result = extract_pdf_text("https://www.techneuk.com/report.pdf")

        assert result == "Page one\nPage two"

    def test_page_with_no_extracted_text_becomes_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            techne_import,
            "urlopen",
            lambda *_a, **_k: FakeUrlResponse(b"%PDF-1.4 bytes"),
        )
        monkeypatch.setattr(
            techne_import, "PdfReader", _fake_pdf_reader_factory([None, "Page two"])
        )

        result = extract_pdf_text("https://www.techneuk.com/report.pdf")

        assert result == "\nPage two"

    def test_sends_browser_user_agent_url_and_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def _fake_urlopen(request: Request, *, timeout: int) -> FakeUrlResponse:
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return FakeUrlResponse(b"%PDF-1.4 bytes")

        monkeypatch.setattr(techne_import, "urlopen", _fake_urlopen)
        monkeypatch.setattr(techne_import, "PdfReader", _fake_pdf_reader_factory(["text"]))

        extract_pdf_text("https://www.techneuk.com/report.pdf")

        assert captured["url"] == "https://www.techneuk.com/report.pdf"
        assert captured["timeout"] == 60
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert headers.get("User-agent", "").startswith("Mozilla/5.0")

    def test_non_wayback_url_tries_only_one_candidate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested: list[str] = []

        def _fake_urlopen(request: Request, *, timeout: int) -> FakeUrlResponse:
            requested.append(request.full_url)
            return FakeUrlResponse(b"%PDF-1.4 bytes")

        monkeypatch.setattr(techne_import, "urlopen", _fake_urlopen)
        monkeypatch.setattr(techne_import, "PdfReader", _fake_pdf_reader_factory(["text"]))

        extract_pdf_text("https://www.techneuk.com/report.pdf")

        assert requested == ["https://www.techneuk.com/report.pdf"]

    def test_wayback_url_without_if_flag_retries_with_the_if_flag_variant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pdf_url = (
            "https://web.archive.org/web/20260213120000/"
            "https://www.techneuk.com/r162.pdf"
        )
        expected_second = (
            "https://web.archive.org/web/20260213120000if_/"
            "https://www.techneuk.com/r162.pdf"
        )
        requested: list[str] = []

        def _fake_urlopen(request: Request, *, timeout: int) -> FakeUrlResponse:
            requested.append(request.full_url)
            if request.full_url == pdf_url:
                # The plain archived page: not the raw PDF payload.
                return FakeUrlResponse(b"<html>archived page</html>")
            return FakeUrlResponse(b"%PDF-1.4 bytes")

        monkeypatch.setattr(techne_import, "urlopen", _fake_urlopen)
        monkeypatch.setattr(techne_import, "PdfReader", _fake_pdf_reader_factory(["text"]))

        result = extract_pdf_text(pdf_url)

        assert requested == [pdf_url, expected_second]
        assert result == "text"

    def test_wayback_url_already_carrying_if_flag_tries_only_one_candidate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pdf_url = (
            "https://web.archive.org/web/20260213120000if_/"
            "https://www.techneuk.com/r162.pdf"
        )
        requested: list[str] = []

        def _fake_urlopen(request: Request, *, timeout: int) -> FakeUrlResponse:
            requested.append(request.full_url)
            return FakeUrlResponse(b"%PDF-1.4 bytes")

        monkeypatch.setattr(techne_import, "urlopen", _fake_urlopen)
        monkeypatch.setattr(techne_import, "PdfReader", _fake_pdf_reader_factory(["text"]))

        extract_pdf_text(pdf_url)

        assert requested == [pdf_url]

    def test_exception_during_fetch_is_caught_and_the_next_candidate_tried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pdf_url = (
            "https://web.archive.org/web/20260213120000/"
            "https://www.techneuk.com/r162.pdf"
        )
        requested: list[str] = []

        def _fake_urlopen(request: Request, *, timeout: int) -> FakeUrlResponse:
            requested.append(request.full_url)
            if request.full_url == pdf_url:
                raise OSError("connection reset")
            return FakeUrlResponse(b"%PDF-1.4 bytes")

        monkeypatch.setattr(techne_import, "urlopen", _fake_urlopen)
        monkeypatch.setattr(techne_import, "PdfReader", _fake_pdf_reader_factory(["text"]))

        result = extract_pdf_text(pdf_url)

        assert len(requested) == 2
        assert result == "text"

    def test_all_candidates_failing_raises_naming_the_original_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Also covers the non-PDF-payload case (former test_non_pdf_payload_raises).

        A Wayback URL, so two distinct candidates are tried and both fail --
        only that can distinguish the error message naming the *original*
        pdf_url from one that instead named the (different) second, "if_"
        candidate URL. The match is anchored with a trailing ``$`` so a
        message naming the wrong URL, or appending extra text, would not
        pass by loose substring matching.
        """
        pdf_url = (
            "https://web.archive.org/web/20260213120000/"
            "https://www.techneuk.com/r162.pdf"
        )
        monkeypatch.setattr(
            techne_import,
            "urlopen",
            lambda *_a, **_k: FakeUrlResponse(b"<html>not a pdf</html>"),
        )

        with pytest.raises(
            ValueError,
            match=f"^Could not fetch PDF payload from URL: {re.escape(pdf_url)}$",
        ):
            extract_pdf_text(pdf_url)


# ── _parse_fieldwork ─────────────────────────────────────────────────────────


class TestParseFieldwork:
    """Tests for _parse_fieldwork -- the Techne FIELDWORK: window line."""

    def test_same_month_with_ordinal_suffixes(self) -> None:
        text = "FIELDWORK: February 11th - February 13th"
        assert _parse_fieldwork(text, 2026) == (date(2026, 2, 11), date(2026, 2, 13))

    def test_no_ordinal_suffix(self) -> None:
        text = "FIELDWORK: February 11 - February 13"
        assert _parse_fieldwork(text, 2026) == (date(2026, 2, 11), date(2026, 2, 13))

    def test_case_insensitive_label_and_months(self) -> None:
        text = "fieldwork: march 1st - march 3rd"
        assert _parse_fieldwork(text, 2026) == (date(2026, 3, 1), date(2026, 3, 3))

    def test_cross_month_same_year(self) -> None:
        text = "FIELDWORK: January 30th - February 2nd"
        assert _parse_fieldwork(text, 2026) == (date(2026, 1, 30), date(2026, 2, 2))

    def test_cross_year_rolls_back_start_year(self) -> None:
        text = "FIELDWORK: December 30th - January 2nd"
        assert _parse_fieldwork(text, 2026) == (date(2025, 12, 30), date(2026, 1, 2))

    def test_found_among_other_text(self) -> None:
        text = "Techne UK Tracker\nFIELDWORK: April 9th - April 10th\nSample: 1636"
        assert _parse_fieldwork(text, 2026) == (date(2026, 4, 9), date(2026, 4, 10))

    def test_no_fieldwork_label_raises(self) -> None:
        with pytest.raises(ValueError, match="Fieldwork window not found in PDF"):
            _parse_fieldwork("Unweighted Sample 1636", 2026)

    def test_missing_colon_after_label_raises(self) -> None:
        # The literal ":" is required by the pattern, unlike Ipsos's fieldwork
        # line which has no colon requirement.
        with pytest.raises(ValueError, match="Fieldwork window not found in PDF"):
            _parse_fieldwork("FIELDWORK February 11th - February 13th", 2026)

    def test_unrecognized_start_month_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognized month in fieldwork window"):
            _parse_fieldwork("FIELDWORK: Fooruary 11th - February 13th", 2026)

    def test_unrecognized_end_month_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognized month in fieldwork window"):
            _parse_fieldwork("FIELDWORK: February 11th - Fooruary 13th", 2026)


# ── _parse_sample_size ───────────────────────────────────────────────────────


class TestParseSampleSize:
    """Tests for _parse_sample_size -- the Unweighted Sample figure."""

    def test_extracts_the_unweighted_sample_figure(self) -> None:
        assert _parse_sample_size("Unweighted Sample 1636") == 1636

    def test_found_among_other_text(self) -> None:
        text = "FIELDWORK: February 11th - February 13th\nUnweighted Sample 2000"
        assert _parse_sample_size(text) == 2000

    def test_three_digit_minimum(self) -> None:
        assert _parse_sample_size("Unweighted Sample 500") == 500

    def test_two_number_columns_picks_the_previous_wave_not_the_current_pins_current_behaviour(
        self,
    ) -> None:
        """Bug, confirmed real -- distinct from the concatenation bug below.

        Techne's PDF places two number columns in every "Unweighted Sample"
        row: the previous poll's wave, then the current one (the real
        header reads "UK JAN 15th | UK FEB 12th"). The regex has only one
        capture group, so ``re.search`` always returns the FIRST number --
        the previous wave, not the current one this poll is actually about.

        Confirmed against two real, currently-hosted Techne PDFs: in both,
        "Male" + "Female" (later columns on the same row) sum to the
        SECOND number, not the first (e.g. 808 + 836 = 1644 for the PDF
        this fixture is drawn from, not the 1636 the parser actually
        returns). Cross-checked against the live DB's stored dates: this
        PDF is poll 155 (fieldwork 2026-02-11/2026-02-12); the value this
        parser actually returns, 1636, is poll 156's (the *preceding*
        poll's, fieldwork 2026-01-14/2026-01-15) real current-wave figure,
        not poll 155's own. The same pattern -- Male+Female summing to the
        second column, and the first column matching the prior poll's
        second column -- holds across all 27 Techne polls stored in the
        live DB, re-verified by re-fetching and re-parsing every one of
        their still-hosted PDFs with this exact, unmodified production
        code.

        This is a distinct bug from the digit-concatenation one pinned
        below: even setting concatenation aside entirely, the plain
        4-digit figure this regex selects is itself the wrong wave's
        number.
        """
        text = (
            "UK\nJAN 15th\nUK\nFEB 12th\nMale\nFemale\n"
            "Unweighted Sample 1636 1644 808 836"
        )

        assert _parse_sample_size(text) == 1636

    def test_missing_label_raises(self) -> None:
        with pytest.raises(
            ValueError, match="Unweighted sample size not found in PDF"
        ):
            _parse_sample_size("FIELDWORK: February 11th - February 13th")

    def test_lowercase_label_does_not_match(self) -> None:
        # The regex has no re.IGNORECASE flag, unlike _parse_fieldwork's and
        # _parse_party_percentages's patterns -- asserted as current
        # behaviour. Harmless against real Techne PDFs, which always
        # capitalise "Unweighted Sample" exactly (confirmed against a
        # currently-hosted file).
        with pytest.raises(
            ValueError, match="Unweighted sample size not found in PDF"
        ):
            _parse_sample_size("unweighted sample 1636")

    def test_fewer_than_three_digits_does_not_match(self) -> None:
        with pytest.raises(
            ValueError, match="Unweighted sample size not found in PDF"
        ):
            _parse_sample_size("Unweighted Sample 99")

    def test_adjacent_second_number_glued_onto_first_when_no_separating_space_pins_current_behaviour(
        self,
    ) -> None:
        """Bug, confirmed real and version-dependent -- distinct from the wrong-wave bug above.

        The regex ``r"Unweighted Sample\\s*(\\d{3,5})"`` has no upper bound
        against a second number sitting directly after the first with no
        separating whitespace: it greedily captures up to 5 digits, so two
        adjacent numbers with no space between them become one wrong one.

        Confirmed as the actual historical import-time cause, not just a
        hypothetical mechanism: pypdf versions 6.6.0 through 6.16.1 --
        6.7.x was the current release around 2026-02-13, when this poll's
        PDF was fetched -- extract this exact PDF's "Unweighted Sample" row
        WITHOUT the spaces between numbers (``"Unweighted Sample
        163616448088364382652912563946306453691...."``); versions before
        6.6.0 and from 6.16.2 onward, including this repo's installed
        6.19.0, keep the spaces. Verified directly: reinstalling
        ``pypdf==6.7.0`` into a scratch environment and re-running the
        unmodified ``extract_pdf_text``/``_parse_sample_size`` against the
        real, still-hosted PDF for poll 155 reproduces the exact stored
        value, 16361, end to end; reinstalling ``pypdf==6.16.1`` reproduces
        it too, and ``pypdf==6.16.2`` does not (the space survives).

        Re-running the parser against the same hosted PDFs with this
        repo's installed pypdf (6.19.0, which keeps the spaces) does NOT
        return the correct value either -- it returns the wrong-wave value
        (1636, see the test above), not a "correct" one. Combined, every
        one of the 27 stored Techne ``sample_size`` values is wrong twice
        over: concatenated (via the pypdf version live at import time) AND
        selecting the previous wave instead of the current one. A fix
        would need a data migration for all 27 rows, taking each PDF's
        second (current-wave) figure. ``requirements.txt`` does not pin
        pypdf, so this could recur, or silently self-correct to a
        still-wrong value, depending on whatever version gets installed
        next.
        """
        text = "Unweighted Sample 163616448088364382652912563946306453691"

        assert _parse_sample_size(text) == 16361


# ── _party_percentage_from_line ─────────────────────────────────────────────


class TestPartyPercentageFromLine:
    """Tests for _party_percentage_from_line -- second-value extraction.

    This function is dead code in production: grep confirms the only
    reference to its name in techne_import.py is its own ``def`` line,
    nothing calls it. Its own docstring also mislabels the two numbers it
    extracts as "unweighted" and "weighted" -- on a real Techne PDF's
    voting-intention line the two numbers are actually the previous-wave
    and current-wave headline figures (the same two-column layout as the
    "Unweighted Sample" row -- see TestParseSampleSize's pinned wrong-wave
    bug). These tests exercise the function's own (unused) logic as
    written, without repeating that mislabelling.
    """

    def test_two_values_returns_the_second(self) -> None:
        assert _party_percentage_from_line("Conservative 18% 20%") == 20.0

    def test_three_values_returns_the_second_not_the_last(self) -> None:
        # Distinguishes index [1] from a mutant that took values[-1].
        assert _party_percentage_from_line("Conservative 18% 20% 22%") == 20.0

    def test_one_value_raises(self) -> None:
        line = "Conservative 18%"
        with pytest.raises(
            ValueError, match="Could not parse headline percentages from line"
        ):
            _party_percentage_from_line(line)

    def test_no_percentage_raises(self) -> None:
        with pytest.raises(
            ValueError, match="Could not parse headline percentages from line"
        ):
            _party_percentage_from_line("Conservative")


# ── _parse_party_percentages ─────────────────────────────────────────────────

_ALL_CASES_HEADER = "Which political party would you vote for? [all cases]"
_BOUNDARY = "Which political party would you vote for? [only who indicates a pol party]"


def _line(party: str, previous_wave: int, current_wave: int) -> str:
    """Build a single party voting-intention line: previous wave, then current.

    Real Techne PDFs place two number columns per row throughout every
    table -- "Unweighted Sample"/"Weighted Sample" and this voting-
    intention block alike -- the previous poll's wave, then the current
    one. Confirmed against a real PDF's voting-intention line, e.g.
    ``"Reform UK 17% 18% 18% 18% 11% ..."``, where 17%/18% are the real
    "UK JAN 15th"/"UK FEB 12th" columns. ``_parse_party_percentages``'s
    real extraction path takes the *second* value here (via
    ``matches[-1].group(2)``), which is therefore the correct,
    current-wave headline figure -- unlike ``_parse_sample_size``, which
    has only one capture group and always takes the wrong, previous-wave
    first one (see TestParseSampleSize's pinned bug).
    """
    return f"{party} {previous_wave}% {current_wave}%"


_FULL_BLOCK_TEXT = "\n".join(
    [
        _ALL_CASES_HEADER,
        _line("Conservative", 18, 20),
        _line("Labour", 28, 30),
        _line("Liberal Democrats", 8, 10),
        _line("Reform UK", 16, 18),
        _line("Green Party", 5, 6),
        _line("Scottish National Party", 3, 4),
        _line("Plaid Cymru", 1, 1),
        _line("Other party", 8, 11),
        _BOUNDARY,
        _line("Conservative", 99, 99),
    ]
)

_EXPECTED_FULL_RESULT = {
    "Conservative": 20.0,
    "Labour": 30.0,
    "Liberal Democrats": 10.0,
    "Reform UK": 18.0,
    "Green": 6.0,
    "Scottish National Party": 4.0,
    "Plaid Cymru": 1.0,
    "Other": 11.0,
}


class TestParsePartyPercentages:
    """Tests for _parse_party_percentages -- national voting-intention block."""

    def test_all_eight_parties_from_the_all_cases_block(self) -> None:
        assert _parse_party_percentages(_FULL_BLOCK_TEXT) == _EXPECTED_FULL_RESULT

    def test_boundary_marker_excludes_text_after_it(self) -> None:
        # The Conservative 99%/99% line after the boundary must never win;
        # only reachable because the block itself already has >=5 parties.
        result = _parse_party_percentages(_FULL_BLOCK_TEXT)
        assert result["Conservative"] == 20.0

    def test_missing_block_header_falls_back_to_full_text(self) -> None:
        text = "\n".join(
            [
                _line("Conservative", 18, 20),
                _line("Labour", 28, 30),
                _line("Liberal Democrats", 8, 10),
                _line("Reform UK", 16, 18),
                _line("Green Party", 5, 6),
                _line("Scottish National Party", 3, 4),
                _line("Plaid Cymru", 1, 1),
                _line("Other party", 8, 11),
            ]
        )
        assert _parse_party_percentages(text) == _EXPECTED_FULL_RESULT

    def test_block_with_fewer_than_five_parties_falls_back_to_full_text(self) -> None:
        # Only 2 parties inside the [all cases] block (below the 5 minimum),
        # but all 8 required parties are present somewhere in the full text
        # (2 inside the block, 6 after the boundary) -- the fallback pass
        # scans the whole text, unrestricted by the block boundary.
        text = "\n".join(
            [
                _ALL_CASES_HEADER,
                _line("Conservative", 18, 20),
                _line("Labour", 28, 30),
                _BOUNDARY,
                _line("Liberal Democrats", 8, 10),
                _line("Reform UK", 16, 18),
                _line("Green Party", 5, 6),
                _line("Scottish National Party", 3, 4),
                _line("Plaid Cymru", 1, 1),
                _line("Other party", 8, 11),
            ]
        )
        assert _parse_party_percentages(text) == _EXPECTED_FULL_RESULT

    def test_block_with_exactly_five_parties_uses_the_block_not_the_fallback(
        self,
    ) -> None:
        # Exactly at the ">= 5" threshold: the block itself must be used,
        # even though a differently-valued Conservative line exists after
        # the boundary -- pinning the boundary against mutants like "< 3",
        # "< 4", "<= 5" or "< 8", all of which would also pass a test that
        # only checked "well above" or "well below" the real threshold.
        text = "\n".join(
            [
                _ALL_CASES_HEADER,
                _line("Conservative", 18, 20),
                _line("Labour", 28, 30),
                _line("Liberal Democrats", 8, 10),
                _line("Reform UK", 16, 18),
                _line("Green Party", 5, 6),
                _BOUNDARY,
                _line("Conservative", 77, 78),
            ]
        )
        result = _parse_party_percentages(text)
        assert result["Conservative"] == 20.0

    def test_block_with_exactly_four_parties_falls_back(self) -> None:
        # One below the "< 5" threshold: the fallback must run, picking up
        # the four parties only present after the boundary -- which the
        # block-restricted pass never reaches on its own.
        text = "\n".join(
            [
                _ALL_CASES_HEADER,
                _line("Conservative", 18, 20),
                _line("Labour", 28, 30),
                _line("Liberal Democrats", 8, 10),
                _line("Reform UK", 16, 18),
                _BOUNDARY,
                _line("Green Party", 5, 6),
                _line("Scottish National Party", 3, 4),
                _line("Plaid Cymru", 1, 1),
                _line("Other party", 8, 11),
            ]
        )
        result = _parse_party_percentages(text)
        assert result == _EXPECTED_FULL_RESULT

    def test_optional_parties_default_to_zero_when_absent(self) -> None:
        text = "\n".join(
            [
                _ALL_CASES_HEADER,
                _line("Conservative", 18, 20),
                _line("Labour", 28, 30),
                _line("Liberal Democrats", 8, 10),
                _line("Reform UK", 16, 18),
                _line("Green Party", 5, 6),
            ]
        )
        result = _parse_party_percentages(text)
        assert result["Scottish National Party"] == 0.0
        assert result["Plaid Cymru"] == 0.0
        assert result["Other"] == 0.0

    def test_missing_required_party_raises_with_sorted_list(self) -> None:
        # Every non-optional required party missing (Conservative, Green,
        # Labour, Liberal Democrats, Reform UK) -- Scottish National
        # Party/Plaid Cymru/Other always get defaulted to 0.0 by the
        # setdefault loop before this check runs, so they can never appear
        # in the missing list; five is the maximum achievable.
        #
        # A single missing party can't prove sorted() is actually applied
        # (a one-element list is trivially "sorted" either way). This
        # module has no PYTHONHASHSEED pinned, so Python's own
        # (hash-randomised) set-iteration order is not literally guaranteed
        # to differ from alphabetical on every run -- but a coincidental
        # match across all 5! = 120 orderings is far less likely than for
        # a smaller list: an earlier draft of this test used only 3 missing
        # parties, and its "sorted() dropped" mutant survived because that
        # run's 3-element (1-in-6) set order happened to already be
        # alphabetical.
        text = _ALL_CASES_HEADER

        with pytest.raises(
            ValueError,
            match=r"Missing expected party rows in PDF: "
            r"\['Conservative', 'Green', 'Labour', 'Liberal Democrats', "
            r"'Reform UK'\]",
        ):
            _parse_party_percentages(text)

    def test_later_occurrence_of_the_same_party_wins(self) -> None:
        text = "\n".join(
            [
                _ALL_CASES_HEADER,
                _line("Conservative", 18, 20),
                _line("Labour", 28, 30),
                _line("Liberal Democrats", 8, 10),
                _line("Reform UK", 16, 18),
                _line("Green Party", 5, 6),
                _line("Scottish National Party", 3, 4),
                _line("Plaid Cymru", 1, 1),
                _line("Other party", 8, 11),
                _line("Conservative", 44, 45),  # still inside the block
            ]
        )
        result = _parse_party_percentages(text)
        assert result["Conservative"] == 45.0

    def test_case_insensitive_party_name_and_header_matching(self) -> None:
        # A lowercase header AND boundary marker, plus a differently-valued
        # Conservative line placed after the (lowercase) boundary. If
        # re.IGNORECASE were dropped from block_pattern, the lowercased
        # header would fail to match at all, block_text would stay empty,
        # and the full-text fallback would run instead -- which, being
        # unrestricted by any boundary, would pick up the after-boundary
        # Conservative line's *different* value (77/78, last-match-wins)
        # instead of the in-block one (18/20). A test that only checked
        # "the header matches case-insensitively" without this trap would
        # pass either way, because _extract()'s own party-line regex is
        # separately, unconditionally case-insensitive regardless of
        # block_pattern's flags.
        text = "\n".join(
            [
                _ALL_CASES_HEADER.lower(),
                _line("conservative", 18, 20),
                _line("labour", 28, 30),
                _line("liberal democrats", 8, 10),
                _line("reform uk", 16, 18),
                _line("green party", 5, 6),
                _line("scottish national party", 3, 4),
                _line("plaid cymru", 1, 1),
                _line("other party", 8, 11),
                _BOUNDARY.lower(),
                _line("Conservative", 77, 78),
            ]
        )
        assert _parse_party_percentages(text) == _EXPECTED_FULL_RESULT


# ── parse_poll ───────────────────────────────────────────────────────────────

_FULL_PDF_TEXT = f"""\
Techne UK Tracker
FIELDWORK: February 11th - February 13th
Unweighted Sample 1636
{_FULL_BLOCK_TEXT}
"""


class TestParsePoll:
    """Tests for parse_poll -- the full PDF-text-to-ParsedPoll pipeline."""

    def test_sample_size_and_fieldwork(self) -> None:
        parsed = parse_poll(_FULL_PDF_TEXT, inferred_year=2026)

        assert parsed.sample_size == 1636
        assert parsed.fieldwork_start == date(2026, 2, 11)
        assert parsed.fieldwork_end == date(2026, 2, 13)

    def test_party_percentages(self) -> None:
        parsed = parse_poll(_FULL_PDF_TEXT, inferred_year=2026)

        assert parsed.party_percentages == _EXPECTED_FULL_RESULT

    def test_inferred_year_affects_a_cross_year_fieldwork_window(self) -> None:
        text = f"""\
FIELDWORK: December 30th - January 2nd
Unweighted Sample 1636
{_FULL_BLOCK_TEXT}
"""
        parsed = parse_poll(text, inferred_year=2026)

        assert parsed.fieldwork_start == date(2025, 12, 30)
        assert parsed.fieldwork_end == date(2026, 1, 2)

    def test_missing_sample_size_propagates(self) -> None:
        text = f"FIELDWORK: February 11th - February 13th\n{_FULL_BLOCK_TEXT}"
        with pytest.raises(
            ValueError, match="Unweighted sample size not found in PDF"
        ):
            parse_poll(text, inferred_year=2026)

    def test_missing_fieldwork_propagates(self) -> None:
        text = f"Unweighted Sample 1636\n{_FULL_BLOCK_TEXT}"
        with pytest.raises(ValueError, match="Fieldwork window not found in PDF"):
            parse_poll(text, inferred_year=2026)


# ── build_import_plan ────────────────────────────────────────────────────────


class TestBuildImportPlan:
    """Tests for build_import_plan's own logic (commit is tested elsewhere)."""

    def test_map_missing_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)

        with pytest.raises(ValueError, match="Map not found"):
            build_import_plan(
                db, map_name="No Such Map", pdf_url="https://x.test/a.pdf"
            )

    def test_year_cannot_be_inferred_and_no_hint_raises(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        fetch_calls: list[str] = []

        def _fail_if_called(_url: str) -> str:
            fetch_calls.append(_url)
            return "text"

        monkeypatch.setattr(techne_import, "extract_pdf_text", _fail_if_called)
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)

        with pytest.raises(ValueError, match="Could not infer year from PDF URL"):
            build_import_plan(
                db, map_name=world.map_name, pdf_url="https://x.test/no-year.pdf"
            )

        # Year inference happens before the PDF is even fetched.
        assert fetch_calls == []

    def test_missing_parties_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Can't use westminster_world: it seeds every required party, so this
        # case is unreachable through it. Seeding all-but-one (rather than
        # none) proves the missing-parties list names the actual gap.
        _seed_map(db, _TEST_MAP_NAME)
        for name in _REQUIRED_PARTY_NAMES:
            if name != "Plaid Cymru":
                db.add_party(name)
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)

        with pytest.raises(
            ValueError,
            match=r"Missing parties in database \(run party importer first\): "
            r"\['Plaid Cymru'\]",
        ):
            build_import_plan(
                db, map_name=_TEST_MAP_NAME, pdf_url="https://x.test/2026/a.pdf"
            )

    def test_rows_are_national_only(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            techne_import,
            "parse_poll",
            lambda _text, *, inferred_year: _parsed_poll(
                {"Labour": 30.0, "Conservative": 20.0, "Reform UK": 18.0}
            ),
        )

        plan = build_import_plan(
            db, map_name=world.map_name, pdf_url="https://x.test/2026/a.pdf"
        )

        assert len(plan.rows) == 3
        assert all(row.region_id is None for row in plan.rows)
        assert all(row.region_name == "National" for row in plan.rows)
        assert {row.party_name: row.percentage for row in plan.rows} == {
            "Labour": 30.0,
            "Conservative": 20.0,
            "Reform UK": 18.0,
        }

    def test_regions_mapping_is_always_empty(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)

        plan = build_import_plan(
            db, map_name=world.map_name, pdf_url="https://x.test/2026/a.pdf"
        )

        assert plan.regions_mapping == ""

    def test_source_url_and_defaults_on_a_fresh_plan(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)

        plan = build_import_plan(
            db, map_name=world.map_name, pdf_url="https://x.test/2026/specific.pdf"
        )

        assert plan.source_url == "https://x.test/2026/specific.pdf"
        assert plan.pollster_name == "Techne"
        assert plan.pollster_exists is False
        assert plan.pollster_id is None
        assert plan.poll_exists is False
        assert plan.poll_id is None

    def test_existing_pollster_without_a_matching_poll_is_flagged(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Named unlike the "Techne" fallback default, so a mutation that
        # always returns the default name cannot pass this by coincidence.
        world = westminster_world
        pollster = db.add_pollster("Techne UK", "techne_test", weight=1.0)
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)

        plan = build_import_plan(
            db,
            map_name=world.map_name,
            pdf_url="https://x.test/2026/a.pdf",
            pollster_identifier="techne_test",
        )

        assert plan.pollster_exists is True
        assert plan.pollster_id == pollster.id
        assert plan.pollster_name == "Techne UK"
        assert plan.poll_exists is False
        assert plan.poll_id is None

    def test_existing_poll_matching_parsed_metadata_is_flagged(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Techne UK", "techne_test", weight=1.0)
        existing = db.add_poll(
            pollster.id,
            world.map_id,
            _PARSED_START,
            _PARSED_END,
            sample_size=_PARSED_SAMPLE,
        )
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)

        plan = build_import_plan(
            db,
            map_name=world.map_name,
            pdf_url="https://x.test/2026/a.pdf",
            pollster_identifier="techne_test",
        )

        assert plan.poll_exists is True
        assert plan.poll_id == existing.id

    def test_year_hint_is_forwarded_to_parse_poll(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Proves --year-hint's value actually reaches parse_poll.

        A URL with no year and a year_hint distinct from any module default
        (2019 is nowhere near DEFAULT_PDF_URL's 2026), so a build_import_plan
        that silently dropped the hint would fail differently (a raised
        "Could not infer year" ValueError) rather than passing this.
        """
        world = westminster_world
        captured_years: list[int] = []

        def _spy_parse_poll(_text: str, *, inferred_year: int) -> ParsedPoll:
            captured_years.append(inferred_year)
            return _parsed_poll({"Labour": 30.0})

        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _spy_parse_poll)

        build_import_plan(
            db,
            map_name=world.map_name,
            pdf_url="https://x.test/no-year.pdf",
            year_hint=2019,
        )

        assert captured_years == [2019]

    def test_full_pdf_text_through_the_real_parser(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end with the real parse_poll, not a stub ParsedPoll.

        Every other test in this class stubs out parse_poll entirely, so
        parse_poll(pdf_text) collapsing to parse_poll("") would pass them
        all. Running the real parser against _FULL_PDF_TEXT (8 parties)
        closes that gap: 8 national rows, all region_id None.
        """
        world = westminster_world
        monkeypatch.setattr(
            techne_import, "extract_pdf_text", lambda _url: _FULL_PDF_TEXT
        )

        plan = build_import_plan(
            db, map_name=world.map_name, pdf_url="https://x.test/2026/a.pdf"
        )

        assert len(plan.rows) == 8
        assert all(row.region_id is None for row in plan.rows)
        assert {row.party_name: row.percentage for row in plan.rows} == (
            _EXPECTED_FULL_RESULT
        )


# ── _cli_preview ─────────────────────────────────────────────────────────────


def _plan_for_preview(
    *, pollster_exists: bool, poll_id: int | None, rows: Sequence[PlannedPollRow]
) -> ImportPlan:
    return ImportPlan(
        pollster_identifier="techne",
        pollster_name="Techne",
        pollster_id=(7 if pollster_exists else None),
        pollster_exists=pollster_exists,
        regions_mapping="",
        map_id=1,
        map_name="UK Constituencies post 2022",
        source_url="https://x.test/2026/a.pdf",
        parsed=_parsed_poll({"Labour": 30.0}),
        poll_id=poll_id,
        poll_exists=poll_id is not None,
        rows=list(rows),
    )


class TestCliPreview:
    """Tests for _cli_preview -- the dry-run summary printed to stdout."""

    def test_prints_fieldwork_sample_and_dry_run_markers_when_new(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        plan = _plan_for_preview(
            pollster_exists=False,
            poll_id=None,
            rows=[
                PlannedPollRow(
                    party_id=2,
                    party_name="Labour",
                    region_id=None,
                    region_name="National",
                    percentage=30.0,
                )
            ],
        )

        _cli_preview(plan)

        out = capsys.readouterr().out.splitlines()
        assert "Parsed poll: fieldwork=2026-02-11 to 2026-02-13, sample=1636" in out
        assert "[dry-run] would create pollster: techne" in out
        assert "[dry-run] would create poll" in out
        assert (
            "[dry-run] would insert row: party=Labour, region=National, "
            "region_id=None, pct=30.00" in out
        )

    def test_prints_exists_markers_when_pollster_and_poll_already_present(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        plan = _plan_for_preview(pollster_exists=True, poll_id=42, rows=[])

        _cli_preview(plan)

        out = capsys.readouterr().out.splitlines()
        assert "pollster exists: techne" in out
        assert "poll exists: 42" in out
        assert not any("would create pollster" in line for line in out)
        assert not any("would create poll" in line for line in out)

    def test_poll_exists_flag_true_but_no_poll_id_still_prints_would_create(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # ImportPlan lets poll_exists and poll_id disagree; _cli_preview's
        # guard checks both, so this exercises that second condition.
        plan = ImportPlan(
            pollster_identifier="techne",
            pollster_name="Techne",
            pollster_id=None,
            pollster_exists=False,
            regions_mapping="",
            map_id=1,
            map_name="UK Constituencies post 2022",
            source_url="https://x.test/2026/a.pdf",
            parsed=_parsed_poll({"Labour": 30.0}),
            poll_id=None,
            poll_exists=True,
            rows=[],
        )

        _cli_preview(plan)

        out = capsys.readouterr().out.splitlines()
        assert "[dry-run] would create poll" in out
        assert not any(line.startswith("poll exists:") for line in out)

    def test_every_row_is_printed_with_no_truncation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rows = [
            PlannedPollRow(
                party_id=n,
                party_name=f"Party {n}",
                region_id=None,
                region_name="National",
                percentage=1.0,
            )
            for n in range(1, 41)
        ]
        plan = _plan_for_preview(pollster_exists=False, poll_id=None, rows=rows)

        _cli_preview(plan)

        out = capsys.readouterr().out
        assert out.count("[dry-run] would insert row:") == 40
        assert "Party 40" in out


# ── main ─────────────────────────────────────────────────────────────────────


class TestMain:
    """Tests for main -- argument parsing, dry-run preview and commit."""

    def test_dry_run_prints_preview_and_writes_nothing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(techne_import, "Database", lambda: db)
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr(
            "sys.argv",
            [
                "techne_import.py",
                "--pdf-url",
                "https://x.test/2026/a.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "techne_test",
                "--dry-run",
            ],
        )

        techne_import.main()

        out = capsys.readouterr().out.splitlines()
        assert "Fetching PDF: https://x.test/2026/a.pdf" in out
        assert "[dry-run] would create pollster: techne_test" in out
        assert db.get_pollster_by_identifier("techne_test") is None

    def test_map_name_is_forwarded_to_build_import_plan(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Proves --map-name is actually used, not silently dropped.

        Every other main() test passes westminster_world.map_name, which
        happens to equal DEFAULT_MAP_NAME -- so main() ignoring --map-name
        entirely would pass all of them. A wrong, non-default name that
        build_import_plan is guaranteed to reject closes that gap.
        """
        monkeypatch.setattr(techne_import, "Database", lambda: db)
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr(
            "sys.argv",
            [
                "techne_import.py",
                "--pdf-url",
                "https://x.test/2026/a.pdf",
                "--map-name",
                "No Such Map",
                "--dry-run",
            ],
        )

        with pytest.raises(ValueError, match="Map not found"):
            techne_import.main()

    def test_commit_creates_pollster_and_poll_and_forwards_the_pdf_url(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        fetched_urls: list[str] = []

        def fake_extract_pdf_text(url: str) -> str:
            fetched_urls.append(url)
            return "text"

        monkeypatch.setattr(techne_import, "Database", lambda: db)
        monkeypatch.setattr(techne_import, "extract_pdf_text", fake_extract_pdf_text)
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr(
            "sys.argv",
            [
                "techne_import.py",
                "--pdf-url",
                "https://x.test/2026/specific.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "techne_test",
            ],
        )

        techne_import.main()

        out = capsys.readouterr().out.splitlines()
        assert "created pollster: techne_test" in out
        assert any(line.startswith("created poll:") for line in out)
        assert "inserted poll rows: 1" in out
        assert db.get_pollster_by_identifier("techne_test") is not None
        polls = db.get_polls_for_map(world.map_id)
        assert len(polls) == 1
        # --pdf-url is a non-default URL, so this proves main() actually
        # forwards the flag rather than falling back to DEFAULT_PDF_URL.
        assert fetched_urls == ["https://x.test/2026/specific.pdf"]
        assert polls[0].source_url == "https://x.test/2026/specific.pdf"

    def test_commit_skips_existing_rows_without_replace_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Techne UK", "techne_test", weight=1.0)
        poll = db.add_poll(
            pollster.id,
            world.map_id,
            _PARSED_START,
            _PARSED_END,
            sample_size=_PARSED_SAMPLE,
        )
        db.add_poll_row(poll.id, world.party_ids["Labour"], 25.0)
        monkeypatch.setattr(techne_import, "Database", lambda: db)
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr(
            "sys.argv",
            [
                "techne_import.py",
                "--pdf-url",
                "https://x.test/2026/a.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "techne_test",
            ],
        )

        techne_import.main()

        out = capsys.readouterr().out.splitlines()
        assert "pollster exists: techne_test" in out
        assert f"poll exists: {poll.id}" in out
        assert (
            f"poll {poll.id} already has rows; use --replace-rows to overwrite" in out
        )
        rows = db.get_rows_for_poll(poll.id)
        assert len(rows) == 1
        # The original row's value survives untouched, not just its count.
        assert rows[0].percentage == 25.0

    def test_replace_rows_deletes_then_inserts(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Techne UK", "techne_test", weight=1.0)
        poll = db.add_poll(
            pollster.id,
            world.map_id,
            _PARSED_START,
            _PARSED_END,
            sample_size=_PARSED_SAMPLE,
        )
        db.add_poll_row(poll.id, world.party_ids["Labour"], 25.0)
        monkeypatch.setattr(techne_import, "Database", lambda: db)
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr(
            "sys.argv",
            [
                "techne_import.py",
                "--pdf-url",
                "https://x.test/2026/a.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "techne_test",
                "--replace-rows",
            ],
        )

        techne_import.main()

        out = capsys.readouterr().out.splitlines()
        assert "deleted existing rows: 1" in out
        assert "inserted poll rows: 1" in out
        rows = db.get_rows_for_poll(poll.id)
        assert len(rows) == 1
        assert rows[0].percentage == 30.0

    def test_year_hint_is_forwarded_from_the_cli(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        captured_years: list[int] = []

        def _spy_parse_poll(_text: str, *, inferred_year: int) -> ParsedPoll:
            captured_years.append(inferred_year)
            return _parsed_poll({"Labour": 30.0})

        monkeypatch.setattr(techne_import, "Database", lambda: db)
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _spy_parse_poll)
        monkeypatch.setattr(
            "sys.argv",
            [
                "techne_import.py",
                "--pdf-url",
                "https://x.test/no-year.pdf",
                "--map-name",
                world.map_name,
                "--year-hint",
                "2019",
                "--dry-run",
            ],
        )

        techne_import.main()

        assert captured_years == [2019]

    def test_defaults_pin_pdf_url_map_name_and_pollster_identifier(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No CLI args resolve against the module's real DEFAULT_* literals.

        The map-name default is checked against westminster_world.map_name,
        an independent fixture constant (uk_fixtures.WESTMINSTER_MAP_NAME),
        not against techne_import.DEFAULT_MAP_NAME itself, so a mutation to
        either side would be caught. The PDF URL and pollster identifier are
        asserted against literals for the same reason.
        """
        assert westminster_world.map_name == "UK Constituencies post 2022"
        monkeypatch.setattr(techne_import, "Database", lambda: db)
        monkeypatch.setattr(techne_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(techne_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr("sys.argv", ["techne_import.py", "--dry-run"])

        techne_import.main()

        out = capsys.readouterr().out.splitlines()
        assert (
            "Fetching PDF: https://www.techneuk.com/wp-content/uploads/"
            "2026/02/R162-UK-2026-2-13-DATA.pdf" in out
        )
        assert "[dry-run] would create pollster: techne" in out
