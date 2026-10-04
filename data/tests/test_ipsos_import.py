"""Tests for the Ipsos PDF poll importer.

``commit_import_plan`` and ``_find_existing_poll`` are already covered, across
all eleven Westminster importers, by ``test_westminster_importers_commit.py``.
This file covers everything else: the PDF-fetch step, the PDF-text parsing
helpers, ``build_import_plan``'s region handling, ``_cli_preview`` and
``main``.

Every PDF-text layout below (fieldwork lines, the national and regional
voting-intention blocks) is synthetic: built to fit the shapes the parsing
functions match on, not sourced from a real Ipsos PDF. Layouts were checked
against the real module functions while writing them, but that only proves
internal consistency between the fixture and the code under test, not that
the fixture matches a real document -- running a function against its own
hand-built input is not independent verification. Where a real signal was
available and checked (the cross-month fieldwork bug in ``_parse_fieldwork``,
verified against poll 225 in the live DB), that is called out explicitly in
the relevant test instead.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from urllib.request import Request

import pytest

from db import Database
from polls.importers.westminster import ipsos_import
from polls.importers.westminster.ipsos_import import (
    MACRO_REGION_TO_INTERNAL,
    PARTY_LINE_MAP,
    ImportPlan,
    ParsedPoll,
    PlannedPollRow,
    _cli_preview,
    _extract_lines,
    _extract_region_values,
    _has_eight_consecutive_nones,
    _line_percentage,
    _parse_fieldwork,
    _parse_party_percentages,
    _parse_party_region_percentages,
    _parse_percentage_tokens,
    _parse_sample_size,
    build_import_plan,
    extract_pdf_text,
    parse_poll,
)
from tests.uk_fixtures import FakeUrlResponse, WestminsterWorld

# ── Local fixtures and seed helpers ─────────────────────────────────────────
#
# Most build_import_plan/main tests below take the westminster_world fixture.
# Two tests genuinely can't: test_missing_parties_raises (westminster_world
# seeds every required party, so it can never exhibit "parties missing") and
# test_missing_internal_region_raises (westminster_world seeds every internal
# region). Those two seed their own minimal map with _seed_map/_seed_parties.

_TEST_MAP_NAME = "Ipsos Test Map"

# Every internal region name any macro in MACRO_REGION_TO_INTERNAL expands to.
_ALL_INTERNAL_REGIONS: tuple[str, ...] = tuple(
    region for regions in MACRO_REGION_TO_INTERNAL.values() for region in regions
)

_REQUIRED_PARTY_NAMES: tuple[str, ...] = tuple(sorted(set(PARTY_LINE_MAP.values())))

_PARSED_START = date(2026, 1, 27)
_PARSED_END = date(2026, 1, 29)
_PARSED_SAMPLE = 1084


def _seed_parties(db: Database) -> dict[str, int]:
    """Seed the eight canonical parties ``build_import_plan`` requires."""
    return {name: db.add_party(name).id for name in _REQUIRED_PARTY_NAMES}


def _seed_map(
    db: Database, name: str, region_names: Sequence[str]
) -> tuple[int, dict[str, int]]:
    """Seed a map named ``name`` with only ``region_names``, no parties."""
    poll_map = db.add_map(name, parliament="westminster")
    region_ids = {
        region: db.add_region(poll_map.id, region).id for region in region_names
    }
    return poll_map.id, region_ids


def _parsed_poll(
    *,
    party_percentages: Mapping[str, float] | None = None,
    party_region_percentages: Mapping[str, Mapping[str, float]] | None = None,
) -> ParsedPoll:
    """Build a ``ParsedPoll`` with fixed fieldwork/sample-size for plan tests."""
    return ParsedPoll(
        sample_size=_PARSED_SAMPLE,
        fieldwork_start=_PARSED_START,
        fieldwork_end=_PARSED_END,
        party_percentages=dict(party_percentages or {}),
        party_region_percentages={
            party: dict(regions)
            for party, regions in (party_region_percentages or {}).items()
        },
    )


def _parse_poll_stub(_text: str) -> ParsedPoll:
    """A ``parse_poll`` stand-in returning a single-party national-only poll."""
    return _parsed_poll(party_percentages={"Labour": 30.0})


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a real network call impossible from this file, even by accident.

    Every fixture URL below is routed through a monkeypatched fetch point, so
    nothing actually reaches ``urlopen`` — but that is incidental, not
    enforced. Tests that need a specific ``urlopen`` behaviour monkeypatch it
    themselves inside the test body, which runs after this fixture and
    overrides it.
    """

    def _blocked(*_a: object, **_k: object) -> object:
        raise AssertionError("test attempted a real network call via urlopen")

    monkeypatch.setattr(ipsos_import, "urlopen", _blocked)


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
    """Return a ``PdfReader``-shaped factory ignoring its (BytesIO) argument."""

    def factory(*_a: object, **_k: object) -> _FakePdfReader:
        return _FakePdfReader(pages_text)

    return factory


class TestExtractPdfText:
    """Tests for extract_pdf_text — fetch, validate and extract PDF text."""

    def test_returns_joined_page_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ipsos_import,
            "urlopen",
            lambda *_a, **_k: FakeUrlResponse(b"%PDF-1.4 bytes"),
        )
        monkeypatch.setattr(
            ipsos_import,
            "PdfReader",
            _fake_pdf_reader_factory(["Page one", "Page two"]),
        )

        result = extract_pdf_text("https://www.ipsos.com/report.pdf")

        assert result == "Page one\nPage two"

    def test_page_with_no_extracted_text_becomes_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ipsos_import,
            "urlopen",
            lambda *_a, **_k: FakeUrlResponse(b"%PDF-1.4 bytes"),
        )
        monkeypatch.setattr(
            ipsos_import, "PdfReader", _fake_pdf_reader_factory([None, "Page two"])
        )

        result = extract_pdf_text("https://www.ipsos.com/report.pdf")

        assert result == "\nPage two"

    def test_non_pdf_payload_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ipsos_import,
            "urlopen",
            lambda *_a, **_k: FakeUrlResponse(b"<html>not a pdf</html>"),
        )

        with pytest.raises(ValueError, match="Could not fetch PDF payload from URL"):
            extract_pdf_text("https://www.ipsos.com/report.pdf")

    def test_sends_browser_user_agent_url_and_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def _fake_urlopen(request: Request, *, timeout: int) -> FakeUrlResponse:
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return FakeUrlResponse(b"%PDF-1.4 bytes")

        monkeypatch.setattr(ipsos_import, "urlopen", _fake_urlopen)
        monkeypatch.setattr(
            ipsos_import, "PdfReader", _fake_pdf_reader_factory(["text"])
        )

        extract_pdf_text("https://www.ipsos.com/report.pdf")

        assert captured["url"] == "https://www.ipsos.com/report.pdf"
        assert captured["timeout"] == 60
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert headers.get("User-agent", "").startswith("Mozilla/5.0")


# ── _extract_lines ───────────────────────────────────────────────────────────


class TestExtractLines:
    """Tests for _extract_lines — split, strip and drop blank lines."""

    def test_strips_and_drops_blank_lines(self) -> None:
        text = "  Line one  \n\n\t\nLine two\n   \nLine three"
        assert _extract_lines(text) == ["Line one", "Line two", "Line three"]

    def test_empty_text_returns_empty_list(self) -> None:
        assert _extract_lines("") == []


# ── _parse_fieldwork ─────────────────────────────────────────────────────────


class TestParseFieldwork:
    """Tests for _parse_fieldwork — the Ipsos fieldwork-dates line."""

    def test_no_fieldwork_line_raises(self) -> None:
        with pytest.raises(ValueError, match="Fieldwork line not found"):
            _parse_fieldwork(["Unweighted total: 1084", "Other content"])

    def test_finds_the_fieldwork_line_among_others(self) -> None:
        lines = [
            "Ipsos Political Monitor",
            "Fieldwork dates: Monday 27th January to Wednesday 29th January 2026",
            "Unweighted total: 1084",
        ]
        assert _parse_fieldwork(lines) == (date(2026, 1, 27), date(2026, 1, 29))

    def test_case_insensitive_search_for_the_fieldwork_label(self) -> None:
        lines = ["fieldwork dates - monday 3rd march to wednesday 5th march 2026"]
        assert _parse_fieldwork(lines) == (date(2026, 3, 3), date(2026, 3, 5))

    def test_same_month_with_month_named_once_before_to(self) -> None:
        # No month repeated between the start day and "to" (group 2 is None,
        # so month_start falls back to month_end via the "or" in the code).
        lines = ["Fieldwork dates: Monday 27th to Wednesday 29th January 2026"]
        assert _parse_fieldwork(lines) == (date(2026, 1, 27), date(2026, 1, 29))

    def test_same_month_with_month_repeated_before_to(self) -> None:
        lines = ["Fieldwork dates: Monday 27th January to Wednesday 29th January 2026"]
        assert _parse_fieldwork(lines) == (date(2026, 1, 27), date(2026, 1, 29))

    def test_cross_month_no_year_wrap(self) -> None:
        lines = ["Fieldwork dates - Monday 30th January to Wednesday 2nd February 2026"]
        assert _parse_fieldwork(lines) == (date(2026, 1, 30), date(2026, 2, 2))

    def test_cross_month_rolls_back_start_year(self) -> None:
        lines = ["Fieldwork dates: Monday 30th December to Wednesday 2nd January 2026"]
        assert _parse_fieldwork(lines) == (date(2025, 12, 30), date(2026, 1, 2))

    def test_parenthetical_week_annotation_before_year_is_tolerated(self) -> None:
        lines = [
            "Fieldwork dates: Monday 27th January to Wednesday 29th "
            "January (week 4) 2026"
        ]
        assert _parse_fieldwork(lines) == (date(2026, 1, 27), date(2026, 1, 29))

    def test_unparseable_fieldwork_line_raises(self) -> None:
        lines = ["Fieldwork dates: sometime in January 2026"]
        with pytest.raises(ValueError, match="Could not parse fieldwork line"):
            _parse_fieldwork(lines)

    def test_unknown_month_name_raises(self) -> None:
        lines = ["Fieldwork dates: Monday 27th Fooruary to Wednesday 29th January 2026"]
        with pytest.raises(
            ValueError, match="Could not parse month names in fieldwork line"
        ):
            _parse_fieldwork(lines)

    def test_cross_month_start_month_omitted_pins_current_behaviour(self) -> None:
        """Bug, pinned not fixed (real, confirmed against the live DB).

        When a cross-month range omits the start month before "to" (only the
        end month is named), ``month_start_text = match.group(2) or
        match.group(4)`` falls back to the *end* month instead of signalling
        "unknown start month". The start day is then paired with the end
        month, silently inverting the range when the start day is later in
        the month than the end day.

        Confirmed live: poll id 225 (pollster "ipsos") is stored with
        ``fieldwork_start=2025-11-30`` and ``fieldwork_end=2025-11-05`` --
        exactly this inversion, from a PDF fieldwork line of this shape
        ("... 30th to ... 5th November 2025"). A re-import after any future
        fix would also create a duplicate poll rather than matching this one,
        since ``_find_existing_poll`` matches on the (wrong) stored dates.
        """
        lines = ["Fieldwork dates: Thursday 30th to Wednesday 5th November 2025"]

        result = _parse_fieldwork(lines)

        assert result == (date(2025, 11, 30), date(2025, 11, 5))
        assert result[0] > result[1]

    def test_cross_month_omitted_start_month_invalid_day_pins_current_behaviour(
        self,
    ) -> None:
        """Same root cause, worse symptom: a raw, un-wrapped date error.

        If the start day doesn't exist in the (wrongly assumed) end month --
        e.g. day 31 paired with November, which has 30 days -- the function
        raises Python's own ``ValueError`` from ``date(...)`` construction
        instead of one of its own "Could not parse ..." messages.
        """
        lines = ["Fieldwork dates: Friday 31st to Tuesday 4th November 2025"]

        with pytest.raises(
            ValueError,
            match=r"day (?:31 must be in range|is out of range for month)",
        ):
            _parse_fieldwork(lines)


# ── _parse_sample_size ───────────────────────────────────────────────────────


class TestParseSampleSize:
    """Tests for _parse_sample_size — digit extraction from the sample line."""

    def test_extracts_digits_from_unweighted_total_line(self) -> None:
        assert _parse_sample_size(["Unweighted total: 1084 adults"]) == 1084

    def test_extracts_digits_from_unweighted_sample_line(self) -> None:
        assert _parse_sample_size(["Unweighted sample: 2000"]) == 2000

    def test_case_insensitive_label(self) -> None:
        assert _parse_sample_size(["UNWEIGHTED TOTAL: 1500"]) == 1500

    def test_takes_first_of_several_numbers_and_ignores_short_ones(self) -> None:
        # "12" is only 2 digits, below the {3,6} minimum, so it is skipped.
        # Two other numbers follow, distinct from each other, so a mutation
        # that took the *last* match instead of the first cannot pass by
        # coincidence.
        line = "Unweighted total: 12 respondents of 1084 (weighted 1200)"
        assert _parse_sample_size([line]) == 1084

    def test_missing_label_raises(self) -> None:
        with pytest.raises(
            ValueError, match="Could not find unweighted sample line in PDF"
        ):
            _parse_sample_size(
                ["Fieldwork dates: Monday 1st to Wednesday 3rd January 2026"]
            )

    def test_no_digits_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse sample size from line"):
            _parse_sample_size(["Unweighted total: unknown"])


# ── _line_percentage ─────────────────────────────────────────────────────────


class TestLinePercentage:
    """Tests for _line_percentage — first percentage value on a line."""

    def test_extracts_percentage_immediately_before_sign(self) -> None:
        assert _line_percentage("Conservative 20%") == 20.0

    def test_tolerates_whitespace_before_percent_sign(self) -> None:
        assert _line_percentage("Conservative 20 %") == 20.0

    def test_no_percentage_returns_none(self) -> None:
        assert _line_percentage("no percentage here") is None

    def test_first_of_several_percentages_is_used(self) -> None:
        assert _line_percentage("Conservative 20% (down 2% on last week)") == 20.0


# ── _parse_party_percentages ─────────────────────────────────────────────────


class TestParsePartyPercentages:
    """Tests for _parse_party_percentages — the national voting-intention block."""

    def test_all_eight_parties_parsed_when_pct_on_the_party_line(self) -> None:
        lines = [
            "Combined voting intention - all",
            "Conservative 20%",
            "Labour 30%",
            "Reform UK 18%",
            "Liberal Democrats 10%",
            "Green Party 6%",
            "Scottish National Party 4%",
            "Plaid Cymru 1%",
            "Other 11%",
        ]
        assert _parse_party_percentages(lines) == {
            "Conservative": 20.0,
            "Labour": 30.0,
            "Reform UK": 18.0,
            "Liberal Democrats": 10.0,
            "Green": 6.0,
            "Scottish National Party": 4.0,
            "Plaid Cymru": 1.0,
            "Other": 11.0,
        }

    def test_pct_read_from_the_following_line_when_absent_from_the_party_line(
        self,
    ) -> None:
        lines = [
            "Combined voting intention - all",
            "Conservative",
            "20%",
            "Labour",
            "30%",
            "Reform UK",
            "18%",
            "Liberal Democrats",
            "10%",
            "Green Party",
            "6%",
            "Scottish National Party",
            "4%",
            "Plaid Cymru",
            "1%",
            "Other",
            "11%",
        ]
        assert _parse_party_percentages(lines) == {
            "Conservative": 20.0,
            "Labour": 30.0,
            "Reform UK": 18.0,
            "Liberal Democrats": 10.0,
            "Green": 6.0,
            "Scottish National Party": 4.0,
            "Plaid Cymru": 1.0,
            "Other": 11.0,
        }

    def test_missing_section_header_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not find Ipsos combined voting"):
            _parse_party_percentages(["Conservative 20%"])

    def test_missing_party_percentage_raises_with_sorted_missing_list(self) -> None:
        lines = ["Combined voting intention - all", "Conservative 20%"]
        expected = (
            "Missing party percentages in Ipsos PDF: "
            "['Green', 'Labour', 'Liberal Democrats', 'Other', 'Plaid Cymru', "
            "'Reform UK', 'Scottish National Party']"
        )
        with pytest.raises(ValueError, match=re.escape(expected)):
            _parse_party_percentages(lines)

    def test_stops_scanning_once_all_eight_parties_are_found(self) -> None:
        # A ninth, later "Conservative" line must never be reached, so it
        # cannot overwrite the party's first recorded value.
        lines = [
            "Combined voting intention - all",
            "Conservative 20%",
            "Labour 30%",
            "Reform UK 18%",
            "Liberal Democrats 10%",
            "Green Party 6%",
            "Scottish National Party 4%",
            "Plaid Cymru 1%",
            "Other 11%",
            "Conservative 99%",
        ]
        assert _parse_party_percentages(lines)["Conservative"] == 20.0

    def test_party_line_at_end_of_document_with_no_following_line_is_dropped(
        self,
    ) -> None:
        lines = [
            "Combined voting intention - all",
            "Conservative 20%",
            "Labour 30%",
            "Reform UK 18%",
            "Liberal Democrats 10%",
            "Green Party 6%",
            "Scottish National Party 4%",
            "Plaid Cymru 1%",
            "Other",  # no percentage on this line and no line after it
        ]
        with pytest.raises(ValueError, match=r"\['Other'\]"):
            _parse_party_percentages(lines)


# ── _parse_percentage_tokens ─────────────────────────────────────────────────


class TestParsePercentageTokens:
    """Tests for _parse_percentage_tokens — tokenise a compacted cross-tab row."""

    def test_percentages_and_a_bare_dash(self) -> None:
        assert _parse_percentage_tokens("20% 15% -") == [20.0, 15.0, None]

    def test_dash_with_trailing_s_is_also_missing(self) -> None:
        assert _parse_percentage_tokens("-s -s") == [None, None]

    def test_three_digit_percentage_supported(self) -> None:
        assert _parse_percentage_tokens("100%") == [100.0]

    def test_unrecognised_characters_are_skipped_not_appended(self) -> None:
        assert _parse_percentage_tokens("20%,15%") == [20.0, 15.0]

    def test_whitespace_between_tokens_is_irrelevant(self) -> None:
        assert _parse_percentage_tokens("  20%   15%  ") == [20.0, 15.0]

    def test_empty_line_returns_empty_list(self) -> None:
        assert _parse_percentage_tokens("") == []


# ── _has_eight_consecutive_nones ─────────────────────────────────────────────


class TestHasEightConsecutiveNones:
    """Tests for _has_eight_consecutive_nones — the extended-layout signature."""

    def test_exactly_eight_consecutive_nones_is_true(self) -> None:
        tokens: list[float | None] = [1.0] + [None] * 8 + [2.0]
        assert _has_eight_consecutive_nones(tokens) is True

    def test_seven_consecutive_nones_is_false(self) -> None:
        tokens: list[float | None] = [1.0] + [None] * 7 + [2.0]
        assert _has_eight_consecutive_nones(tokens) is False

    def test_eight_nones_split_by_a_value_is_false(self) -> None:
        tokens: list[float | None] = [None] * 4 + [1.0] + [None] * 4
        assert _has_eight_consecutive_nones(tokens) is False

    def test_more_than_eight_consecutive_nones_is_true(self) -> None:
        tokens: list[float | None] = [None] * 10
        assert _has_eight_consecutive_nones(tokens) is True

    def test_empty_list_is_false(self) -> None:
        assert _has_eight_consecutive_nones([]) is False


# ── _extract_region_values ───────────────────────────────────────────────────


class TestExtractRegionValues:
    """Tests for _extract_region_values — standard vs extended column layouts."""

    def test_standard_layout_returns_the_six_region_values(self) -> None:
        tokens: list[float | None] = [
            22.0, 24.0, 20.0, 18.0, 15.0, 10.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0
        ]
        assert _extract_region_values(tokens) == [22.0, 24.0, 20.0, 18.0, 15.0, 10.0]

    def test_standard_layout_with_a_suppressed_cell_returns_none(self) -> None:
        # Only one None, not eight consecutive, so the extended path never
        # triggers; the standard slice then fails its "no None" check.
        tokens: list[float | None] = [22.0, None, 20.0, 18.0, 15.0, 10.0] + [1.0] * 8
        assert _extract_region_values(tokens) is None

    def test_too_short_for_the_standard_slice_returns_none(self) -> None:
        tokens: list[float | None] = [1.0, 2.0, 3.0]
        assert _extract_region_values(tokens) is None

    def test_extended_layout_skips_the_greater_england_column(self) -> None:
        tokens: list[float | None] = (
            [20.0]
            + [None] * 8
            + [25.0, 30.0, None, 15.0, 10.0, 5.0, 2.0, 99.0, 98.0]
        )
        assert _extract_region_values(tokens) == [25.0, 30.0, 15.0, 10.0, 5.0, 2.0]

    def test_extended_layout_suppressed_region_cell_becomes_zero(self) -> None:
        tokens: list[float | None] = (
            [20.0]
            + [None] * 8
            + [25.0, None, None, 15.0, 10.0, 5.0, 2.0, 99.0, 98.0]
        )
        assert _extract_region_values(tokens) == [25.0, 0.0, 15.0, 10.0, 5.0, 2.0]


# ── _parse_party_region_percentages ──────────────────────────────────────────

_LIKELY_HEADER = "Combined voting intention - likely to vote"


def _region_line(six: Sequence[float]) -> str:
    """Build a 16-token percentage row whose middle 6 tokens are ``six``.

    Two distinct leading tokens (40, 41) precede ``six``, and 8 trailing
    tokens follow, so ``tokens[-14:-8]`` (the standard layout's negative-index
    region slice) lands on ``six`` while ``tokens[:6]`` would not -- proving
    the code really reads the slice from the end of the row, not the start.
    """
    values = [40.0, 41.0] + list(six) + [99.0] * 8
    return " ".join(f"{int(value)}%" for value in values)


class TestParsePartyRegionPercentages:
    """Tests for _parse_party_region_percentages — the regional cross-tab block."""

    def test_missing_section_header_returns_empty_dict(self) -> None:
        assert _parse_party_region_percentages(["no header here"]) == {}

    def test_fewer_than_five_matches_returns_empty_dict(self) -> None:
        lines = [
            _LIKELY_HEADER,
            "Conservative",
            _region_line([22, 24, 20, 18, 15, 10]),
            "Labour",
            _region_line([30, 25, 28, 32, 22, 18]),
        ]
        assert _parse_party_region_percentages(lines) == {}

    def test_five_matches_maps_regions_in_order_and_defaults_the_rest(self) -> None:
        lines = [
            _LIKELY_HEADER,
            "Conservative",
            _region_line([22, 24, 20, 18, 15, 10]),
            "Labour",
            _region_line([30, 25, 28, 32, 22, 18]),
            "Reform UK",
            _region_line([14, 12, 9, 8, 7, 6]),
            "Liberal Democrats",
            _region_line([10, 9, 8, 7, 6, 5]),
            "Green Party",
            _region_line([4, 3, 3, 3, 2, 2]),
        ]

        result = _parse_party_region_percentages(lines)

        assert set(result.keys()) == {
            "Conservative",
            "Labour",
            "Reform UK",
            "Liberal Democrats",
            "Green",
            "Scottish National Party",
            "Plaid Cymru",
            "Other",
        }
        assert result["Conservative"] == {
            "Wales": 22.0,
            "Scotland": 24.0,
            "London": 20.0,
            "South excl London": 18.0,
            "Midlands incl East of England": 15.0,
            "North excl Scotland": 10.0,
        }
        # The three optional parties, absent from the source lines, default
        # to 0% in every region rather than being omitted.
        for optional_party in ("Scottish National Party", "Plaid Cymru", "Other"):
            assert result[optional_party] == {
                "Wales": 0.0,
                "Scotland": 0.0,
                "London": 0.0,
                "South excl London": 0.0,
                "Midlands incl East of England": 0.0,
                "North excl Scotland": 0.0,
            }

    def test_an_optional_party_that_was_actually_parsed_is_not_overwritten(
        self,
    ) -> None:
        """The 0.0-default loop uses ``setdefault``, not a plain assignment.

        If it used ``parsed[optional_party] = {...zeros...}`` instead, a real
        SNP/Plaid/Other row that WAS parsed above would be silently zeroed
        out here -- which would zero SNP's Scotland share on every real Ipsos
        import, since live polls carry all 8 parties regionally.
        """
        lines = [
            _LIKELY_HEADER,
            "Conservative",
            _region_line([22, 24, 20, 18, 15, 10]),
            "Labour",
            _region_line([30, 25, 28, 32, 22, 18]),
            "Reform UK",
            _region_line([14, 12, 9, 8, 7, 6]),
            "Liberal Democrats",
            _region_line([10, 9, 8, 7, 6, 5]),
            "Green Party",
            _region_line([4, 3, 3, 3, 2, 2]),
            "Scottish National Party",
            _region_line([0, 30, 0, 0, 0, 0]),
        ]

        result = _parse_party_region_percentages(lines)

        assert result["Scottish National Party"] == {
            "Wales": 0.0,
            "Scotland": 30.0,
            "London": 0.0,
            "South excl London": 0.0,
            "Midlands incl East of England": 0.0,
            "North excl Scotland": 0.0,
        }
        # Plaid Cymru and Other were still absent, so they still default.
        assert result["Plaid Cymru"]["Wales"] == 0.0
        assert result["Other"]["London"] == 0.0

    def test_only_the_first_match_per_party_is_kept(self) -> None:
        lines = [
            _LIKELY_HEADER,
            "Conservative",
            _region_line([22, 24, 20, 18, 15, 10]),
            "Labour",
            _region_line([30, 25, 28, 32, 22, 18]),
            "Reform UK",
            _region_line([14, 12, 9, 8, 7, 6]),
            "Liberal Democrats",
            _region_line([10, 9, 8, 7, 6, 5]),
            "Green Party",
            _region_line([4, 3, 3, 3, 2, 2]),
            "Conservative",  # a second cross-tab; must not overwrite the first
            _region_line([99, 99, 99, 99, 99, 99]),
        ]

        result = _parse_party_region_percentages(lines)

        assert result["Conservative"]["Wales"] == 22.0

    def test_row_without_a_percent_sign_is_skipped_not_treated_as_all_zero(
        self,
    ) -> None:
        # A row of bare dashes tokenises as 14 Nones, which would itself
        # satisfy the extended-layout signature (>=8 consecutive Nones) and
        # produce a spurious all-zero match if the "%" not in next_line guard
        # were removed. Placed *before* the real cross-tab and combined with
        # the once-only guard, a removed check would make this bad row "win"
        # and the real values below would then be skipped as a duplicate.
        dashes_no_percent = " ".join(["-"] * 14)
        lines = [
            _LIKELY_HEADER,
            "Conservative",
            dashes_no_percent,
            "Conservative",
            _region_line([22, 24, 20, 18, 15, 10]),
            "Labour",
            _region_line([30, 25, 28, 32, 22, 18]),
            "Reform UK",
            _region_line([14, 12, 9, 8, 7, 6]),
            "Liberal Democrats",
            _region_line([10, 9, 8, 7, 6, 5]),
            "Green Party",
            _region_line([4, 3, 3, 3, 2, 2]),
        ]

        result = _parse_party_region_percentages(lines)

        assert result["Conservative"] == {
            "Wales": 22.0,
            "Scotland": 24.0,
            "London": 20.0,
            "South excl London": 18.0,
            "Midlands incl East of England": 15.0,
            "North excl Scotland": 10.0,
        }

    def test_row_with_too_few_tokens_is_skipped(self) -> None:
        # 8 dashes then one "5%" tokenises to 9 tokens: 8 consecutive Nones
        # followed by a value. That is short of the 14-token minimum, but if
        # the "len(tokens) < 14" guard were removed it would still slip past
        # _extract_region_values: 8 consecutive Nones satisfies the
        # extended-layout signature, and with only 9 tokens every one of its
        # six selected positions falls inside the None run, producing a
        # "successful" (if spurious) all-zero extraction. Placing this row
        # *before* the real Conservative row makes that distinction
        # observable: with the guard, this row is skipped and the real row
        # below is parsed; without it, this row would be recorded first and
        # the real row would then be dropped by the once-only duplicate
        # guard, leaving Conservative wrongly all-zero.
        short_row = " ".join(["-"] * 8) + " 5%"
        lines = [
            _LIKELY_HEADER,
            "Conservative",
            short_row,
            "Conservative",
            _region_line([22, 24, 20, 18, 15, 10]),
            "Labour",
            _region_line([30, 25, 28, 32, 22, 18]),
            "Reform UK",
            _region_line([14, 12, 9, 8, 7, 6]),
            "Liberal Democrats",
            _region_line([10, 9, 8, 7, 6, 5]),
            "Green Party",
            _region_line([4, 3, 3, 3, 2, 2]),
        ]

        result = _parse_party_region_percentages(lines)

        assert result["Conservative"] == {
            "Wales": 22.0,
            "Scotland": 24.0,
            "London": 20.0,
            "South excl London": 18.0,
            "Midlands incl East of England": 15.0,
            "North excl Scotland": 10.0,
        }

    def test_unextractable_region_values_drop_the_party_below_the_threshold(
        self,
    ) -> None:
        # A single stray dash inside the standard 6-value slice (not 8
        # consecutive) makes _extract_region_values return None, so this
        # otherwise well-formed 14-token row is discarded via the
        # "region_values is None" guard. That leaves only 4 non-optional
        # parties parsed (below the 5-match threshold), so the whole section
        # is treated as absent -- distinguishing this from the "too few
        # tokens" skip above, which still reaches the 5-match threshold.
        bad_row = "22% -  20% 18% 15% 10% 30% 25% 28% 32% 22% 18% 40% 41%"
        lines = [
            _LIKELY_HEADER,
            "Conservative",
            _region_line([22, 24, 20, 18, 15, 10]),
            "Labour",
            _region_line([30, 25, 28, 32, 22, 18]),
            "Reform UK",
            _region_line([14, 12, 9, 8, 7, 6]),
            "Green Party",
            _region_line([4, 3, 3, 3, 2, 2]),
            "Liberal Democrats",
            bad_row,
        ]

        assert _parse_party_region_percentages(lines) == {}

    def test_unmatched_marker_line_is_skipped(self) -> None:
        lines = [
            _LIKELY_HEADER,
            "Your Party",  # matches no PARTY_LINE_MAP marker
            _region_line([1, 1, 1, 1, 1, 1]),
            "Conservative",
            _region_line([22, 24, 20, 18, 15, 10]),
            "Labour",
            _region_line([30, 25, 28, 32, 22, 18]),
            "Reform UK",
            _region_line([14, 12, 9, 8, 7, 6]),
            "Liberal Democrats",
            _region_line([10, 9, 8, 7, 6, 5]),
            "Green Party",
            _region_line([4, 3, 3, 3, 2, 2]),
        ]

        result = _parse_party_region_percentages(lines)

        # Exact key set (not just "Your Party" absent): if the "canonical_party
        # is None: continue" guard were dropped, the unmatched line would be
        # recorded under the key None instead of being skipped entirely.
        assert set(result.keys()) == {
            "Conservative",
            "Labour",
            "Reform UK",
            "Liberal Democrats",
            "Green",
            "Scottish National Party",
            "Plaid Cymru",
            "Other",
        }
        assert result["Conservative"]["Wales"] == 22.0


# ── parse_poll ───────────────────────────────────────────────────────────────

_FULL_PDF_TEXT = f"""\
Ipsos Political Monitor
Fieldwork dates: Monday 27th January to Wednesday 29th January 2026
Unweighted total: 1084 adults
Combined voting intention - all
Conservative 20%
Labour 30%
Reform UK 18%
Liberal Democrats 10%
Green Party 6%
Scottish National Party 4%
Plaid Cymru 1%
Other 11%
{_LIKELY_HEADER}
Conservative
{_region_line([22, 24, 20, 18, 15, 10])}
Labour
{_region_line([30, 25, 28, 32, 22, 18])}
Reform UK
{_region_line([14, 12, 9, 8, 7, 6])}
Liberal Democrats
{_region_line([10, 9, 8, 7, 6, 5])}
Green Party
{_region_line([4, 3, 3, 3, 2, 2])}
Scottish National Party
{_region_line([0, 30, 0, 0, 0, 0])}
"""

_NATIONAL_ONLY_PDF_TEXT = """\
Ipsos Political Monitor
Fieldwork dates: Monday 27th January to Wednesday 29th January 2026
Unweighted total: 1084 adults
Combined voting intention - all
Conservative 20%
Labour 30%
Reform UK 18%
Liberal Democrats 10%
Green Party 6%
Scottish National Party 4%
Plaid Cymru 1%
Other 11%
"""


class TestParsePoll:
    """Tests for parse_poll — the full PDF-text-to-ParsedPoll pipeline."""

    def test_sample_size_and_fieldwork(self) -> None:
        parsed = parse_poll(_FULL_PDF_TEXT)

        assert parsed.sample_size == 1084
        assert parsed.fieldwork_start == date(2026, 1, 27)
        assert parsed.fieldwork_end == date(2026, 1, 29)

    def test_national_percentages(self) -> None:
        parsed = parse_poll(_FULL_PDF_TEXT)

        assert parsed.party_percentages == {
            "Conservative": 20.0,
            "Labour": 30.0,
            "Reform UK": 18.0,
            "Liberal Democrats": 10.0,
            "Green": 6.0,
            "Scottish National Party": 4.0,
            "Plaid Cymru": 1.0,
            "Other": 11.0,
        }

    def test_regional_percentages(self) -> None:
        parsed = parse_poll(_FULL_PDF_TEXT)

        assert parsed.party_region_percentages["Conservative"] == {
            "Wales": 22.0,
            "Scotland": 24.0,
            "London": 20.0,
            "South excl London": 18.0,
            "Midlands incl East of England": 15.0,
            "North excl Scotland": 10.0,
        }
        # SNP was actually parsed (Scotland 30%): it must survive the
        # optional-party defaulting step, not be zeroed by it.
        assert parsed.party_region_percentages["Scottish National Party"] == {
            "Wales": 0.0,
            "Scotland": 30.0,
            "London": 0.0,
            "South excl London": 0.0,
            "Midlands incl East of England": 0.0,
            "North excl Scotland": 0.0,
        }
        # Plaid Cymru is genuinely absent from the PDF text, so it defaults.
        assert parsed.party_region_percentages["Plaid Cymru"] == {
            "Wales": 0.0,
            "Scotland": 0.0,
            "London": 0.0,
            "South excl London": 0.0,
            "Midlands incl East of England": 0.0,
            "North excl Scotland": 0.0,
        }

    def test_no_regional_section_leaves_party_region_percentages_empty(self) -> None:
        parsed = parse_poll(_NATIONAL_ONLY_PDF_TEXT)

        assert parsed.party_region_percentages == {}
        assert parsed.party_percentages["Labour"] == 30.0


# ── build_import_plan ────────────────────────────────────────────────────────


class TestBuildImportPlan:
    """Tests for build_import_plan's own logic (commit is tested elsewhere)."""

    def test_map_missing_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)

        with pytest.raises(ValueError, match="Map not found"):
            build_import_plan(
                db, map_name="No Such Map", pdf_url="https://x.test/a.pdf"
            )

    def test_missing_parties_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Can't use westminster_world: it seeds every required party, so this
        # case is unreachable through it. Seeding all-but-one (rather than
        # none) proves the missing-parties list names the actual gap, not
        # just that the party table happens to be empty.
        _seed_map(db, _TEST_MAP_NAME, _ALL_INTERNAL_REGIONS)
        for name in _REQUIRED_PARTY_NAMES:
            if name != "Plaid Cymru":
                db.add_party(name)
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)

        with pytest.raises(
            ValueError,
            match=r"Missing parties in database \(run party importer first\): "
            r"\['Plaid Cymru'\]",
        ):
            build_import_plan(
                db, map_name=_TEST_MAP_NAME, pdf_url="https://x.test/a.pdf"
            )

    def test_missing_internal_region_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Can't use westminster_world: it seeds every internal region, so a
        # missing one is unreachable through it. Wales maps to itself
        # (one internal region), which keeps the failure attributable to a
        # single, unambiguous region name.
        regions_without_wales = tuple(
            region for region in _ALL_INTERNAL_REGIONS if region != "Wales"
        )
        _seed_map(db, _TEST_MAP_NAME, regions_without_wales)
        _seed_parties(db)
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            ipsos_import,
            "parse_poll",
            lambda _text: _parsed_poll(
                party_region_percentages={"Labour": {"Wales": 30.0}}
            ),
        )

        with pytest.raises(ValueError, match="Missing region in database: 'Wales'"):
            build_import_plan(
                db, map_name=_TEST_MAP_NAME, pdf_url="https://x.test/a.pdf"
            )

    def test_national_only_rows_when_regional_dict_is_empty(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            ipsos_import,
            "parse_poll",
            lambda _text: _parsed_poll(
                party_percentages={"Labour": 30.0, "Conservative": 20.0}
            ),
        )

        plan = build_import_plan(
            db, map_name=world.map_name, pdf_url="https://x.test/a.pdf"
        )

        assert len(plan.rows) == 2
        assert all(row.region_id is None for row in plan.rows)
        assert all(row.region_name == "National" for row in plan.rows)
        assert {row.party_name: row.percentage for row in plan.rows} == {
            "Labour": 30.0,
            "Conservative": 20.0,
        }

    def test_regional_row_added_for_a_one_to_one_macro(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            ipsos_import,
            "parse_poll",
            lambda _text: _parsed_poll(
                party_region_percentages={"Labour": {"Wales": 30.0}}
            ),
        )

        plan = build_import_plan(
            db, map_name=world.map_name, pdf_url="https://x.test/a.pdf"
        )

        assert len(plan.rows) == 1
        row = plan.rows[0]
        assert row.party_name == "Labour"
        assert row.region_name == "Wales"
        assert row.region_id == world.region_ids["Wales"]
        assert row.percentage == 30.0

    def test_regional_macro_expands_to_every_internal_region(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            ipsos_import,
            "parse_poll",
            lambda _text: _parsed_poll(
                party_region_percentages={"Green": {"South excl London": 12.0}}
            ),
        )

        plan = build_import_plan(
            db, map_name=world.map_name, pdf_url="https://x.test/a.pdf"
        )

        assert len(plan.rows) == 2
        assert {row.region_name for row in plan.rows} == {
            "South East England",
            "South West England",
        }
        assert all(row.percentage == 12.0 for row in plan.rows)
        assert all(row.party_name == "Green" for row in plan.rows)

    def test_national_and_regional_rows_coexist(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            ipsos_import,
            "parse_poll",
            lambda _text: _parsed_poll(
                party_percentages={"Labour": 30.0},
                party_region_percentages={"Labour": {"Wales": 40.0}},
            ),
        )

        plan = build_import_plan(
            db, map_name=world.map_name, pdf_url="https://x.test/a.pdf"
        )

        assert len(plan.rows) == 2
        national_row = next(row for row in plan.rows if row.region_id is None)
        regional_row = next(row for row in plan.rows if row.region_id is not None)
        assert national_row.region_name == "National"
        assert national_row.percentage == 30.0
        assert regional_row.region_name == "Wales"
        assert regional_row.region_id == world.region_ids["Wales"]
        assert regional_row.percentage == 40.0

    def test_source_url_and_regions_mapping_on_a_fresh_plan(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)

        plan = build_import_plan(
            db, map_name=world.map_name, pdf_url="https://x.test/specific.pdf"
        )

        assert plan.source_url == "https://x.test/specific.pdf"
        assert plan.regions_mapping == ""
        assert plan.pollster_name == "Ipsos"
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
        # Named unlike the "Ipsos" fallback default, so a mutation that
        # always returns the default name cannot pass this by coincidence.
        world = westminster_world
        pollster = db.add_pollster("Ipsos MORI", "ipsos_test", weight=1.0)
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)

        plan = build_import_plan(
            db,
            map_name=world.map_name,
            pdf_url="https://x.test/a.pdf",
            pollster_identifier="ipsos_test",
        )

        assert plan.pollster_exists is True
        assert plan.pollster_id == pollster.id
        assert plan.pollster_name == "Ipsos MORI"
        assert plan.poll_exists is False
        assert plan.poll_id is None

    def test_existing_poll_matching_parsed_metadata_is_flagged(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Ipsos MORI", "ipsos_test", weight=1.0)
        existing = db.add_poll(
            pollster.id,
            world.map_id,
            _PARSED_START,
            _PARSED_END,
            sample_size=_PARSED_SAMPLE,
        )
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)

        plan = build_import_plan(
            db,
            map_name=world.map_name,
            pdf_url="https://x.test/a.pdf",
            pollster_identifier="ipsos_test",
        )

        assert plan.poll_exists is True
        assert plan.poll_id == existing.id

    def test_full_pdf_text_through_the_real_parser_hits_every_region(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end with the real parse_poll, not a stub ParsedPoll.

        Every other test in this class stubs out parse_poll entirely, so
        parse_poll(pdf_text) collapsing to parse_poll("") would pass them
        all, and only 2 of the 11 internal regions ever get looked up against
        a real map. Running the real parser against _FULL_PDF_TEXT (8
        national parties, 5 explicitly parsed regionally, 3 defaulted) closes
        both gaps: 8 national rows plus 8 parties x 11 internal regions each
        = 96 rows in total, spanning every internal region the six macros
        expand to (Northern Ireland is not one of them -- Ipsos has no macro
        for it).
        """
        world = westminster_world
        monkeypatch.setattr(
            ipsos_import, "extract_pdf_text", lambda _url: _FULL_PDF_TEXT
        )

        plan = build_import_plan(
            db, map_name=world.map_name, pdf_url="https://x.test/a.pdf"
        )

        assert len(plan.rows) == 96
        national_rows = [row for row in plan.rows if row.region_id is None]
        regional_rows = [row for row in plan.rows if row.region_id is not None]
        assert len(national_rows) == 8
        assert len(regional_rows) == 88
        assert {row.region_name for row in regional_rows} == {
            "Wales",
            "Scotland",
            "London",
            "South East England",
            "South West England",
            "East Midlands",
            "West Midlands",
            "East of England",
            "North East England",
            "North West England",
            "Yorkshire and The Humber",
        }
        assert {row.party_name for row in plan.rows} == {
            "Conservative",
            "Labour",
            "Reform UK",
            "Liberal Democrats",
            "Green",
            "Scottish National Party",
            "Plaid Cymru",
            "Other",
        }


# ── _cli_preview ─────────────────────────────────────────────────────────────


def _plan_for_preview(
    *, pollster_exists: bool, poll_id: int | None, rows: Sequence[PlannedPollRow]
) -> ImportPlan:
    return ImportPlan(
        pollster_identifier="ipsos",
        pollster_name="Ipsos",
        pollster_id=(7 if pollster_exists else None),
        pollster_exists=pollster_exists,
        regions_mapping="",
        map_id=1,
        map_name="UK Constituencies post 2022",
        source_url="https://x.test/a.pdf",
        parsed=_parsed_poll(party_percentages={"Labour": 30.0}),
        poll_id=poll_id,
        poll_exists=poll_id is not None,
        rows=list(rows),
    )


class TestCliPreview:
    """Tests for _cli_preview — the dry-run summary printed to stdout."""

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
        assert "Parsed poll: fieldwork=2026-01-27 to 2026-01-29, sample=1084" in out
        assert "[dry-run] would create pollster: ipsos" in out
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
        assert "pollster exists: ipsos" in out
        assert "poll exists: 42" in out
        assert not any("would create pollster" in line for line in out)
        assert not any("would create poll" in line for line in out)

    def test_every_row_is_printed_with_no_truncation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rows = [
            PlannedPollRow(
                party_id=1,
                party_name="Labour",
                region_id=n,
                region_name=f"Region {n}",
                percentage=1.0,
            )
            for n in range(40)
        ]
        plan = _plan_for_preview(pollster_exists=False, poll_id=None, rows=rows)

        _cli_preview(plan)

        out = capsys.readouterr().out
        assert out.count("[dry-run] would insert row:") == 40
        assert "Region 39" in out


# ── main ─────────────────────────────────────────────────────────────────────


class TestMain:
    """Tests for main — argument parsing, dry-run preview and commit."""

    def test_dry_run_prints_preview_and_writes_nothing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(ipsos_import, "Database", lambda: db)
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr(
            "sys.argv",
            [
                "ipsos_import.py",
                "--pdf-url",
                "https://x.test/a.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "ipsos_test",
                "--dry-run",
            ],
        )

        ipsos_import.main()

        out = capsys.readouterr().out.splitlines()
        assert "Fetching PDF: https://x.test/a.pdf" in out
        assert "[dry-run] would create pollster: ipsos_test" in out
        assert db.get_pollster_by_identifier("ipsos_test") is None

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
        monkeypatch.setattr(ipsos_import, "Database", lambda: db)
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr(
            "sys.argv",
            [
                "ipsos_import.py",
                "--pdf-url",
                "https://x.test/a.pdf",
                "--map-name",
                "No Such Map",
                "--dry-run",
            ],
        )

        with pytest.raises(ValueError, match="Map not found"):
            ipsos_import.main()

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

        monkeypatch.setattr(ipsos_import, "Database", lambda: db)
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", fake_extract_pdf_text)
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr(
            "sys.argv",
            [
                "ipsos_import.py",
                "--pdf-url",
                "https://x.test/specific.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "ipsos_test",
            ],
        )

        ipsos_import.main()

        out = capsys.readouterr().out.splitlines()
        assert "created pollster: ipsos_test" in out
        assert any(line.startswith("created poll:") for line in out)
        assert "inserted poll rows: 1" in out
        assert db.get_pollster_by_identifier("ipsos_test") is not None
        polls = db.get_polls_for_map(world.map_id)
        assert len(polls) == 1
        # --pdf-url is a non-default URL, so this proves main() actually
        # forwards the flag rather than falling back to DEFAULT_PDF_URL.
        assert fetched_urls == ["https://x.test/specific.pdf"]
        assert polls[0].source_url == "https://x.test/specific.pdf"

    def test_commit_skips_existing_rows_without_replace_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Ipsos MORI", "ipsos_test", weight=1.0)
        poll = db.add_poll(
            pollster.id,
            world.map_id,
            _PARSED_START,
            _PARSED_END,
            sample_size=_PARSED_SAMPLE,
        )
        db.add_poll_row(poll.id, world.party_ids["Labour"], 25.0)
        monkeypatch.setattr(ipsos_import, "Database", lambda: db)
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr(
            "sys.argv",
            [
                "ipsos_import.py",
                "--pdf-url",
                "https://x.test/a.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "ipsos_test",
            ],
        )

        ipsos_import.main()

        out = capsys.readouterr().out.splitlines()
        assert "pollster exists: ipsos_test" in out
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
        pollster = db.add_pollster("Ipsos MORI", "ipsos_test", weight=1.0)
        poll = db.add_poll(
            pollster.id,
            world.map_id,
            _PARSED_START,
            _PARSED_END,
            sample_size=_PARSED_SAMPLE,
        )
        db.add_poll_row(poll.id, world.party_ids["Labour"], 25.0)
        monkeypatch.setattr(ipsos_import, "Database", lambda: db)
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr(
            "sys.argv",
            [
                "ipsos_import.py",
                "--pdf-url",
                "https://x.test/a.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "ipsos_test",
                "--replace-rows",
            ],
        )

        ipsos_import.main()

        out = capsys.readouterr().out.splitlines()
        assert "deleted existing rows: 1" in out
        assert "inserted poll rows: 1" in out
        rows = db.get_rows_for_poll(poll.id)
        assert len(rows) == 1
        assert rows[0].percentage == 30.0

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
        not against ipsos_import.DEFAULT_MAP_NAME itself, so a mutation to
        either side would be caught. The PDF URL and pollster identifier are
        asserted against literals for the same reason: comparing against
        ipsos_import.DEFAULT_PDF_URL/DEFAULT_POLLSTER_IDENTIFIER would just
        compare the code under test with itself.
        """
        assert westminster_world.map_name == "UK Constituencies post 2022"
        monkeypatch.setattr(ipsos_import, "Database", lambda: db)
        monkeypatch.setattr(ipsos_import, "extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(ipsos_import, "parse_poll", _parse_poll_stub)
        monkeypatch.setattr("sys.argv", ["ipsos_import.py", "--dry-run"])

        ipsos_import.main()

        out = capsys.readouterr().out.splitlines()
        assert (
            "Fetching PDF: https://www.ipsos.com/sites/default/files/ct/news/"
            "documents/2026-01/politmkp_w1jan2026web1.pdf" in out
        )
        assert "[dry-run] would create pollster: ipsos" in out
