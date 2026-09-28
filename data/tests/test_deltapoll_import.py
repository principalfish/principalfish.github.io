"""Tests for the Deltapoll PDF poll importer.

``commit_import_plan`` and ``_find_existing_poll`` are already covered, across
all eleven Westminster importers, by ``test_westminster_importers_commit.py``.
This file covers everything else: the PDF-fetch chain, the PDF-text parsing
helpers, ``build_import_plan``'s region handling, ``_cli_preview`` and
``main``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from urllib.request import Request

import pytest

from db import Database
from polls.importers.westminster import deltapoll_import
from polls.importers.westminster.deltapoll_import import (
    MACRO_TO_INTERNAL_REGIONS,
    NATIONAL_KEY,
    PARTY_LABEL_TO_CANONICAL,
    ImportPlan,
    ParsedPoll,
    PlannedPollRow,
    _canonical_party_from_line,
    _cli_preview,
    _extract_lines,
    _extract_party_order_and_national,
    _extract_pdf_text,
    _fetch_bytes,
    _month_number,
    _parse_fieldwork,
    _parse_regional_from_block,
    _parse_sample_size,
    _resolve_pdf_url,
    build_import_plan,
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

_TEST_MAP_NAME = "Deltapoll Test Map"

# Every internal region name any macro in MACRO_TO_INTERNAL_REGIONS expands to.
_ALL_INTERNAL_REGIONS: tuple[str, ...] = tuple(
    region
    for regions in MACRO_TO_INTERNAL_REGIONS.values()
    for region in regions
)

_REQUIRED_PARTY_NAMES: tuple[str, ...] = tuple(sorted(set(PARTY_LABEL_TO_CANONICAL.values())))

_PARSED_START = date(2026, 3, 1)
_PARSED_END = date(2026, 3, 3)
_PARSED_SAMPLE = 1500


def _seed_parties(db: Database) -> dict[str, int]:
    """Seed the eight canonical parties ``build_import_plan`` requires."""
    return {name: db.add_party(name).id for name in _REQUIRED_PARTY_NAMES}


def _seed_map(
    db: Database, name: str, region_names: Sequence[str]
) -> tuple[int, dict[str, int]]:
    """Seed a map named ``name`` with only ``region_names``, no parties."""
    poll_map = db.add_map(name, parliament="westminster")
    region_ids = {region: db.add_region(poll_map.id, region).id for region in region_names}
    return poll_map.id, region_ids


def _parsed_poll(party_region_percentages: Mapping[str, Mapping[str, float]]) -> ParsedPoll:
    """Build a ``ParsedPoll`` with fixed fieldwork/sample-size for plan tests."""
    return ParsedPoll(
        sample_size=_PARSED_SAMPLE,
        fieldwork_start=_PARSED_START,
        fieldwork_end=_PARSED_END,
        party_region_percentages={
            party: dict(regions) for party, regions in party_region_percentages.items()
        },
    )


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a real network call impossible from this file, even by accident.

    Today every fixture URL happens to end in ``.pdf`` or is otherwise routed
    through a monkeypatched fetch point, so nothing actually reaches
    ``urlopen`` — but that is incidental, not enforced. Tests that need a
    specific ``urlopen`` behaviour monkeypatch it themselves inside the test
    body, which runs after this fixture and overrides it.
    """

    def _blocked(*_a: object, **_k: object) -> object:
        raise AssertionError("test attempted a real network call via urlopen")

    monkeypatch.setattr(deltapoll_import, "urlopen", _blocked)


# ── _month_number ────────────────────────────────────────────────────────────


class TestMonthNumber:
    """Tests for _month_number — month name → integer conversion."""

    def test_full_names(self) -> None:
        assert _month_number("January") == 1
        assert _month_number("December") == 12

    def test_abbreviated_names(self) -> None:
        assert _month_number("Jan") == 1
        assert _month_number("Sept") == 9

    def test_case_insensitive(self) -> None:
        assert _month_number("JANUARY") == 1
        assert _month_number("january") == 1

    def test_trailing_period_stripped(self) -> None:
        assert _month_number("Jan.") == 1

    def test_surrounding_whitespace_stripped(self) -> None:
        assert _month_number("  Jan  ") == 1

    def test_unknown_returns_none(self) -> None:
        assert _month_number("Fooruary") is None
        assert _month_number("") is None


# ── _fetch_bytes ─────────────────────────────────────────────────────────────


class TestFetchBytes:
    """Tests for _fetch_bytes — a raw urlopen wrapped with a browser UA."""

    def test_returns_response_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            deltapoll_import, "urlopen", lambda *_a, **_k: FakeUrlResponse(b"payload-bytes")
        )
        assert _fetch_bytes("https://example.test/file.pdf") == b"payload-bytes"

    def test_sends_browser_user_agent_url_and_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def _fake_urlopen(request: Request, *, timeout: int) -> FakeUrlResponse:
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return FakeUrlResponse(b"x")

        monkeypatch.setattr(deltapoll_import, "urlopen", _fake_urlopen)

        _fetch_bytes("https://example.test/file.pdf")

        assert captured["url"] == "https://example.test/file.pdf"
        assert captured["timeout"] == 60
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert headers.get("User-agent", "").startswith("Mozilla/5.0")


# ── _resolve_pdf_url ─────────────────────────────────────────────────────────


class TestResolvePdfUrl:
    """Tests for _resolve_pdf_url — direct PDF URL vs an HTML page with a link."""

    def test_direct_pdf_url_returned_unchanged_without_fetching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: object, **_k: object) -> bytes:
            raise AssertionError("must not fetch a URL that already ends in .pdf")

        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", _boom)
        url = "https://deltapoll.co.uk/reports/jan-2026.PDF"

        assert _resolve_pdf_url(url) == url

    def test_html_page_with_pdf_link_resolves_to_absolute_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        html = b'<html><body><a href="/wp-content/uploads/report.pdf">Report</a></body></html>'
        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", lambda _url: html)

        result = _resolve_pdf_url("https://deltapoll.co.uk/reports/jan-2026/")

        assert result == "https://deltapoll.co.uk/wp-content/uploads/report.pdf"

    def test_first_of_several_pdf_links_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = (
            b'<html><body>'
            b'<a href="https://cdn.test/first.pdf">First</a>'
            b'<a href="https://cdn.test/second.pdf">Second</a>'
            b'</body></html>'
        )
        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", lambda _url: html)

        result = _resolve_pdf_url("https://deltapoll.co.uk/reports/")

        assert result == "https://cdn.test/first.pdf"

    def test_anchor_with_blank_href_does_not_crash_or_get_picked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A blank ``href`` does not crash the scan and the real link still wins.

        This does not actually witness the ``if not href: continue`` line: a
        removed guard would fall through to ``urljoin(source_url, "")``, which
        returns ``source_url`` itself — and since ``source_url`` is the HTML
        page (never a ``.pdf`` URL, or the function would have returned before
        fetching), that fallback can never look like a PDF link either. The
        guard is therefore not observable through this function's return
        value in any reachable scenario; this test only proves the blank
        anchor doesn't crash the scan or produce a wrong result.
        """
        html = (
            b'<html><body>'
            b'<a href="   ">Blank</a>'
            b'<a href="/report.pdf">Report</a>'
            b'</body></html>'
        )
        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", lambda _url: html)

        result = _resolve_pdf_url("https://deltapoll.co.uk/reports/")

        assert result == "https://deltapoll.co.uk/report.pdf"

    def test_no_pdf_link_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b'<html><body><a href="/about">About</a></body></html>'
        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", lambda _url: html)

        with pytest.raises(ValueError, match="No PDF link found in Deltapoll HTML page"):
            _resolve_pdf_url("https://deltapoll.co.uk/reports/")


# ── _extract_pdf_text ────────────────────────────────────────────────────────


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


def _fetch_from(responses: Mapping[str, bytes | Exception]) -> Callable[[str], bytes]:
    """Return a fake ``_fetch_bytes`` serving fixed bytes or raising, by URL."""

    def _fetch(url: str) -> bytes:
        outcome = responses[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return _fetch


class TestExtractPdfText:
    """Tests for _extract_pdf_text — download, validate and extract PDF text."""

    def test_direct_url_returns_joined_page_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", lambda _url: b"%PDF-1.4 bytes")
        monkeypatch.setattr(
            deltapoll_import, "PdfReader", _fake_pdf_reader_factory(["Page one", "Page two"])
        )

        result = _extract_pdf_text("https://deltapoll.co.uk/report.pdf")

        assert result == "Page one\nPage two"

    def test_page_with_no_extracted_text_becomes_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", lambda _url: b"%PDF-1.4 bytes")
        monkeypatch.setattr(
            deltapoll_import, "PdfReader", _fake_pdf_reader_factory([None, "Page two"])
        )

        result = _extract_pdf_text("https://deltapoll.co.uk/report.pdf")

        assert result == "\nPage two"

    def test_wayback_url_falls_back_to_if_variant_on_non_pdf_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = "https://web.archive.org/web/20260105120000/https://deltapoll.co.uk/r.pdf"
        with_if = "https://web.archive.org/web/20260105120000if_/https://deltapoll.co.uk/r.pdf"
        responses: dict[str, bytes | Exception] = {
            original: b"<html>wrapper, not a pdf</html>",
            with_if: b"%PDF-1.4 real pdf bytes",
        }
        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", _fetch_from(responses))
        monkeypatch.setattr(
            deltapoll_import, "PdfReader", _fake_pdf_reader_factory(["extracted text"])
        )

        result = _extract_pdf_text(original)

        assert result == "extracted text"

    def test_wayback_url_falls_back_to_if_variant_on_fetch_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = "https://web.archive.org/web/20260105120000/https://deltapoll.co.uk/r.pdf"
        with_if = "https://web.archive.org/web/20260105120000if_/https://deltapoll.co.uk/r.pdf"
        responses: dict[str, bytes | Exception] = {
            original: RuntimeError("network error"),
            with_if: b"%PDF-1.4 real pdf bytes",
        }
        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", _fetch_from(responses))
        monkeypatch.setattr(deltapoll_import, "PdfReader", _fake_pdf_reader_factory(["text"]))

        result = _extract_pdf_text(original)

        assert result == "text"

    def test_wayback_url_with_if_flag_already_present_has_one_candidate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        url = "https://web.archive.org/web/20260105120000if_/https://deltapoll.co.uk/r.pdf"
        calls: list[str] = []

        def _fetch(candidate: str) -> bytes:
            calls.append(candidate)
            return b"%PDF-1.4 bytes"

        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", _fetch)
        monkeypatch.setattr(deltapoll_import, "PdfReader", _fake_pdf_reader_factory(["text"]))

        _extract_pdf_text(url)

        assert calls == [url]

    def test_no_candidate_returns_a_valid_pdf_payload_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(deltapoll_import, "_fetch_bytes", lambda _url: b"not a pdf payload")

        with pytest.raises(ValueError, match="Could not fetch PDF payload from URL"):
            _extract_pdf_text("https://deltapoll.co.uk/report.pdf")


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
    """Tests for _parse_fieldwork — the four Deltapoll date-range formats."""

    def test_no_fieldwork_line_raises(self) -> None:
        with pytest.raises(ValueError, match="Fieldwork line not found"):
            _parse_fieldwork(["Sample Size: 1500", "Other content"])

    def test_finds_the_fieldwork_line_among_others(self) -> None:
        lines = ["Deltapoll Report", "Fieldwork: 3-5 January 2026", "Sample Size: 1500"]
        assert _parse_fieldwork(lines) == (date(2026, 1, 3), date(2026, 1, 5))

    # -- pattern 1: cross-month, explicit year on both sides --

    def test_cross_month_explicit_years_same_year(self) -> None:
        lines = ["Fieldwork: 1 June 2026 to 5 June 2026"]
        assert _parse_fieldwork(lines) == (date(2026, 6, 1), date(2026, 6, 5))

    def test_cross_month_explicit_years_rolls_back_start_year(self) -> None:
        lines = ["Fieldwork: 30 December 2025 to 2 January 2026"]
        assert _parse_fieldwork(lines) == (date(2025, 12, 30), date(2026, 1, 2))

    def test_cross_month_explicit_years_invalid_month_raises(self) -> None:
        lines = ["Fieldwork: 30 Fooruary 2025 to 2 January 2026"]
        with pytest.raises(ValueError, match="Could not parse fieldwork months from"):
            _parse_fieldwork(lines)

    # -- pattern 2: cross-month, no year on the start side --

    def test_cross_month_no_start_year_same_year(self) -> None:
        lines = ["Fieldwork: 5 June to 10 June 2026"]
        assert _parse_fieldwork(lines) == (date(2026, 6, 5), date(2026, 6, 10))

    def test_cross_month_no_start_year_rolls_back_start_year(self) -> None:
        lines = ["Fieldwork: 30 December to 2 January 2026"]
        assert _parse_fieldwork(lines) == (date(2025, 12, 30), date(2026, 1, 2))

    def test_cross_month_no_start_year_invalid_month_raises(self) -> None:
        lines = ["Fieldwork: 30 Fooruary to 2 January 2026"]
        with pytest.raises(ValueError, match="Could not parse fieldwork months from"):
            _parse_fieldwork(lines)

    # -- pattern 3: same-month hyphen range --

    def test_same_month_hyphen_range(self) -> None:
        lines = ["Fieldwork: 3-5 January 2026"]
        assert _parse_fieldwork(lines) == (date(2026, 1, 3), date(2026, 1, 5))

    def test_same_month_en_dash_is_normalised(self) -> None:
        lines = ["Fieldwork: 3–5 January 2026"]
        assert _parse_fieldwork(lines) == (date(2026, 1, 3), date(2026, 1, 5))

    def test_same_month_ordinal_suffixes_are_stripped(self) -> None:
        lines = ["Fieldwork: 3rd-5th January 2026"]
        assert _parse_fieldwork(lines) == (date(2026, 1, 3), date(2026, 1, 5))

    def test_same_month_hyphen_invalid_month_raises(self) -> None:
        lines = ["Fieldwork: 3-5 Fooruary 2026"]
        with pytest.raises(ValueError, match="Could not parse fieldwork month from"):
            _parse_fieldwork(lines)

    # -- pattern 4: same-month "to" range --

    def test_same_month_to_range(self) -> None:
        lines = ["Fieldwork: 3 to 5 January 2026"]
        assert _parse_fieldwork(lines) == (date(2026, 1, 3), date(2026, 1, 5))

    def test_same_month_to_invalid_month_raises(self) -> None:
        lines = ["Fieldwork: 3 to 5 Fooruary 2026"]
        with pytest.raises(ValueError, match="Could not parse fieldwork month from"):
            _parse_fieldwork(lines)

    # -- none of the four patterns match --

    def test_unparseable_fieldwork_line_raises(self) -> None:
        lines = ["Fieldwork: sometime in January 2026"]
        with pytest.raises(ValueError, match="Could not parse fieldwork line"):
            _parse_fieldwork(lines)


# ── _parse_sample_size ───────────────────────────────────────────────────────


class TestParseSampleSize:
    """Tests for _parse_sample_size — digit extraction from the sample line."""

    def test_extracts_digits(self) -> None:
        assert _parse_sample_size(["Sample Size: 1500"]) == 1500

    def test_strips_thousands_separator(self) -> None:
        assert _parse_sample_size(["Sample Size: 1,500 adults"]) == 1500

    def test_case_insensitive_label(self) -> None:
        assert _parse_sample_size(["sample size: 2000"]) == 2000

    def test_missing_label_raises(self) -> None:
        with pytest.raises(ValueError, match="Sample Size line not found"):
            _parse_sample_size(["Fieldwork: 3-5 January 2026"])

    def test_no_digits_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse sample size from line"):
            _parse_sample_size(["Sample Size: unknown"])


# ── _canonical_party_from_line ───────────────────────────────────────────────


class TestCanonicalPartyFromLine:
    """Tests for _canonical_party_from_line — party-label prefix matching."""

    def test_exact_labels(self) -> None:
        assert _canonical_party_from_line("Conservative 20 20 65") == "Conservative"
        assert _canonical_party_from_line("Labour 23 23") == "Labour"
        assert _canonical_party_from_line("Green 12 11") == "Green"
        assert _canonical_party_from_line("Other 2 1") == "Other"

    def test_liberal_democrat_variants_map_to_liberal_democrats(self) -> None:
        assert _canonical_party_from_line("Liberal Democrat 11 13") == "Liberal Democrats"
        assert _canonical_party_from_line("Liberal Democrats 11 13") == "Liberal Democrats"

    def test_snp_variants_map_to_scottish_national_party(self) -> None:
        assert _canonical_party_from_line("SNP 3 3 0") == "Scottish National Party"
        assert (
            _canonical_party_from_line("Scottish National Party 3 3 0")
            == "Scottish National Party"
        )
        assert (
            _canonical_party_from_line("Scottish National Party (SNP) 3 3 0")
            == "Scottish National Party"
        )

    def test_plaid_cymru_variant_maps_to_plaid_cymru(self) -> None:
        assert _canonical_party_from_line("Plaid Cymru (PC) 1 1") == "Plaid Cymru"

    def test_collapses_internal_whitespace_before_matching(self) -> None:
        assert _canonical_party_from_line("Reform   UK 23 23") == "Reform UK"

    def test_unmatched_line_returns_none(self) -> None:
        assert _canonical_party_from_line("Your Party 0 0 0") is None
        assert _canonical_party_from_line("") is None


# ── _extract_party_order_and_national ───────────────────────────────────────

_FULL_NATIONAL_LINES = [
    "Deltapoll / Mirror Political Polling",
    "HEADLINE VOTING INTENTION",
    "Conservative 20 20 65",
    "Labour 23 23 3",
    "Liberal Democrats 11 13 4",
    "Scottish National Party 3 3 0",
    "Plaid Cymru 1 1 0",
    "Reform UK 23 23 25",
    "Green 12 11 1",
    "Other 2 1 1",
    "footer text",
]


class TestExtractPartyOrderAndNational:
    """Tests for _extract_party_order_and_national — the Q1 national table."""

    def test_parses_all_eight_parties_in_document_order(self) -> None:
        party_order, national = _extract_party_order_and_national(_FULL_NATIONAL_LINES)

        assert party_order == [
            "Conservative",
            "Labour",
            "Liberal Democrats",
            "Scottish National Party",
            "Plaid Cymru",
            "Reform UK",
            "Green",
            "Other",
        ]
        assert national == {
            "Conservative": 20.0,
            "Labour": 23.0,
            "Liberal Democrats": 11.0,
            "Scottish National Party": 3.0,
            "Plaid Cymru": 1.0,
            "Reform UK": 23.0,
            "Green": 12.0,
            "Other": 2.0,
        }

    def test_missing_voting_intention_section_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not find Q1 voting intention section"):
            _extract_party_order_and_national(["Conservative 20 20"])

    def test_missing_required_party_raises(self) -> None:
        lines = ["HEADLINE VOTING INTENTION", "Conservative 20 20", "Labour 23 23"]
        with pytest.raises(ValueError, match="Missing expected party rows"):
            _extract_party_order_and_national(lines)

    def test_other_defaults_to_zero_and_is_appended_when_absent(self) -> None:
        lines = [
            "HEADLINE VOTING INTENTION",
            "Conservative 20 20",
            "Labour 23 23",
            "Liberal Democrats 11 13",
            "Scottish National Party 3 3",
            "Plaid Cymru 1 1",
            "Reform UK 23 23",
            "Green 12 11",
        ]

        party_order, national = _extract_party_order_and_national(lines)

        assert party_order[-1] == "Other"
        assert national["Other"] == 0.0

    def test_stops_after_eight_parties_even_if_more_labelled_lines_follow(self) -> None:
        lines = [
            "HEADLINE VOTING INTENTION",
            "Conservative 20 20",
            "Labour 23 23",
            "Liberal Democrats 11 13",
            "Scottish National Party 3 3",
            "Plaid Cymru 1 1",
            "Reform UK 23 23",
            "Green 12 11",
            "Other 2 1",
            "Conservative 99 99",  # a 9th labelled line; must not be reprocessed
        ]

        party_order, national = _extract_party_order_and_national(lines)

        assert party_order.count("Conservative") == 1
        assert national["Conservative"] == 20.0

    def test_row_matching_a_party_with_no_digits_is_skipped(self) -> None:
        lines = [
            "HEADLINE VOTING INTENTION",
            "Green Party candidates stood in every seat",  # matches "Green", no digits
            "Conservative 20 20",
            "Labour 23 23",
            "Liberal Democrats 11 13",
            "Scottish National Party 3 3",
            "Plaid Cymru 1 1",
            "Reform UK 23 23",
            "Green 12 11",
        ]

        party_order, national = _extract_party_order_and_national(lines)

        assert national["Green"] == 12.0
        assert party_order.count("Green") == 1


# ── _parse_regional_from_block ──────────────────────────────────────────────

_REGIONAL_HEADER = "Total London Rest of South Midlands North Wales Scotland"


class TestParseRegionalFromBlock:
    """Tests for _parse_regional_from_block — the macro-region VI table."""

    def test_parses_rows_in_party_order(self) -> None:
        lines = [
            _REGIONAL_HEADER,
            "20 21 15 12 18 22 10 5",
            "30 31 28 26 24 20 15 33",
            "10 11 8 9 11 9 3 4",
        ]

        result = _parse_regional_from_block(
            lines, ["Conservative", "Labour", "Liberal Democrats"]
        )

        assert result["Conservative"] == {
            "London": 20.0,
            "Rest of South": 21.0,
            "Midlands": 15.0,
            "North": 12.0,
            "Wales": 18.0,
            "Scotland": 22.0,
        }
        assert result["Labour"]["London"] == 30.0
        assert result["Liberal Democrats"]["Scotland"] == 9.0

    def test_missing_header_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not find regional header block"):
            _parse_regional_from_block(["no header here"], ["Conservative"])

    def test_too_few_rows_raises(self) -> None:
        lines = [_REGIONAL_HEADER, "20 21 15 12 18 22 10 5"]

        with pytest.raises(ValueError, match="Could not parse complete regional rows"):
            _parse_regional_from_block(lines, ["Conservative", "Labour", "Liberal Democrats"])

    def test_party_without_a_matching_row_defaults_to_zero(self) -> None:
        # len(party_order) - 1 rows is the accepted minimum, so the last party
        # in party_order gets no row and every macro region defaults to 0.0.
        lines = [
            _REGIONAL_HEADER,
            "20 21 15 12 18 22 10 5",
            "30 31 28 26 24 20 15 33",
        ]

        result = _parse_regional_from_block(
            lines, ["Conservative", "Labour", "Liberal Democrats"]
        )

        assert result["Liberal Democrats"] == {
            "London": 0.0,
            "Rest of South": 0.0,
            "Midlands": 0.0,
            "North": 0.0,
            "Wales": 0.0,
            "Scotland": 0.0,
        }

    def test_rows_with_wrong_length_or_out_of_range_values_are_skipped(self) -> None:
        lines = [
            _REGIONAL_HEADER,
            "20 21 15 12 18 22 10",  # 7 values: wrong length, skipped
            "20 21 15 12 18 22 10 5 3",  # 9 values: wrong length, skipped
            "-1 21 15 12 18 22 10 5",  # a negative value is outside [0, 100], skipped
            "30 31 28 26 24 20 15 33",  # the first row actually collected
        ]

        result = _parse_regional_from_block(lines, ["Conservative"])

        assert result["Conservative"]["London"] == 30.0

    def test_rows_beyond_party_order_length_are_not_used(self) -> None:
        """Only as many rows as there are parties are ever read back out.

        This does not prove the ``if len(rows) >= len(party_order): break``
        line itself does anything: the idx loop below only ever reads
        ``rows[:len(party_order)]``, so a surplus row would be ignored by that
        read regardless of whether the break fired during collection. It only
        shows that a surplus row's values never leak into the result.
        """
        lines = [
            _REGIONAL_HEADER,
            "20 21 15 12 18 22 10 5",
            "30 31 28 26 24 20 15 33",
            "99 99 99 99 99 99 99 99",  # a third row, but only 2 parties
        ]

        result = _parse_regional_from_block(lines, ["Conservative", "Labour"])

        assert result["Labour"] == {
            "London": 30.0,
            "Rest of South": 31.0,
            "Midlands": 28.0,
            "North": 26.0,
            "Wales": 24.0,
            "Scotland": 20.0,
        }
        assert "99" not in str(result)


# ── parse_poll ───────────────────────────────────────────────────────────────
#
# Regional rows below are [London, Rest of South, Midlands, North, Wales,
# Scotland, <ignored>, <ignored>] per _parse_regional_from_block's real
# mapping. The trailing pair (95, 96 in every row) is deliberately outside
# every region value used here, so a mutant that started reading them would
# be caught. SNP (0 everywhere but Scotland=38) and Plaid (0 everywhere but
# Wales=24) mirror a real Deltapoll poll on the live DB (pollster
# "deltapoll", poll 235): SNP 38 in Scotland/0 elsewhere, Plaid 24 in
# Wales/0 elsewhere, and the regional figures reconstruct the reported
# national ones when weighted by region size (e.g. Conservative ~19.1 vs the
# 19 reported, Labour ~20.2 vs 20) — confirming columns 0-5 really are
# London..Scotland in that order, as the code maps them.

_FULL_PDF_TEXT = """\
Deltapoll / Mirror Political Polling
Fieldwork: 3-5 January 2026
Sample Size: 1503
Voting intention (Q1)
Conservative 19 19
Labour 20 20
Liberal Democrats 10 10
Scottish National Party 4 4
Plaid Cymru 1 1
Reform UK 23 23
Green 8 8
Other 2 2
Total London Rest of South Midlands North Wales Scotland
22 24 20 18 15 10 95 96
30 25 28 32 22 18 95 96
14 12 9 8 7 6 95 96
0 0 0 0 0 38 95 96
0 0 0 0 24 0 95 96
20 22 25 24 18 12 95 96
10 9 8 7 6 5 95 96
4 3 3 3 2 2 95 96
"""

_REGIONAL_BLOCK = (
    "Total London Rest of South Midlands North Wales Scotland\n"
    "22 24 20 18 15 10 95 96\n"
    "30 25 28 32 22 18 95 96\n"
    "14 12 9 8 7 6 95 96\n"
    "0 0 0 0 0 38 95 96\n"
    "0 0 0 0 24 0 95 96\n"
    "20 22 25 24 18 12 95 96\n"
    "10 9 8 7 6 5 95 96\n"
    "4 3 3 3 2 2 95 96\n"
)


class TestParsePoll:
    """Tests for parse_poll — the full PDF-text-to-ParsedPoll pipeline."""

    def test_sample_size_and_fieldwork(self) -> None:
        parsed = parse_poll(_FULL_PDF_TEXT)

        assert parsed.sample_size == 1503
        assert parsed.fieldwork_start == date(2026, 1, 3)
        assert parsed.fieldwork_end == date(2026, 1, 5)

    def test_national_percentages_stored_under_the_national_key(self) -> None:
        parsed = parse_poll(_FULL_PDF_TEXT)

        assert parsed.party_region_percentages["Labour"][NATIONAL_KEY] == 20.0
        assert parsed.party_region_percentages["Other"][NATIONAL_KEY] == 2.0

    def test_regional_columns_map_london_through_scotland_in_order(self) -> None:
        """Columns 0-5 are London..Scotland in document order; 6-7 are unused.

        _parse_regional_from_block's own docstring claims column 0 is a
        "Total" column that gets discarded — it is not: the function reads
        column 0 straight into "London". That is correct, not a bug: see the
        module comment above this fixture for the live-poll evidence. Only
        that function's docstring is wrong and is worth a one-line fix (not
        made here, since this piece is test-only).
        """
        parsed = parse_poll(_FULL_PDF_TEXT)

        percentages = parsed.party_region_percentages
        assert percentages["Conservative"] == {
            NATIONAL_KEY: 19.0,
            "London": 22.0,
            "Rest of South": 24.0,
            "Midlands": 20.0,
            "North": 18.0,
            "Wales": 15.0,
            "Scotland": 10.0,
        }
        assert percentages["Scottish National Party"] == {
            NATIONAL_KEY: 4.0,
            "London": 0.0,
            "Rest of South": 0.0,
            "Midlands": 0.0,
            "North": 0.0,
            "Wales": 0.0,
            "Scotland": 38.0,
        }
        assert percentages["Plaid Cymru"] == {
            NATIONAL_KEY: 1.0,
            "London": 0.0,
            "Rest of South": 0.0,
            "Midlands": 0.0,
            "North": 0.0,
            "Wales": 24.0,
            "Scotland": 0.0,
        }

    def test_regional_parse_failure_falls_back_to_national_only(self) -> None:
        text = _FULL_PDF_TEXT.replace(_REGIONAL_BLOCK, "")

        parsed = parse_poll(text)

        assert parsed.party_region_percentages["Conservative"] == {NATIONAL_KEY: 19.0}
        assert parsed.party_region_percentages["Other"] == {NATIONAL_KEY: 2.0}


# ── build_import_plan ────────────────────────────────────────────────────────


class TestBuildImportPlan:
    """Tests for build_import_plan's own logic (commit is tested elsewhere)."""

    def test_map_missing_raises(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
        )

        with pytest.raises(ValueError, match="Map not found"):
            build_import_plan(db, map_name="No Such Map", source_url="https://x.test/a.pdf")

    def test_missing_parties_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Can't use westminster_world: it seeds every required party, so this
        # case (no parties at all) is unreachable through it.
        _seed_map(db, _TEST_MAP_NAME, _ALL_INTERNAL_REGIONS)
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
        )

        with pytest.raises(ValueError, match="Missing parties in database"):
            build_import_plan(db, map_name=_TEST_MAP_NAME, source_url="https://x.test/a.pdf")

    def test_missing_internal_region_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Can't use westminster_world: it seeds every internal region, so a
        # missing one is unreachable through it. This is the one test in this
        # class that genuinely needs a custom region set.
        regions_without_east_midlands = tuple(
            region for region in _ALL_INTERNAL_REGIONS if region != "East Midlands"
        )
        _seed_map(db, _TEST_MAP_NAME, regions_without_east_midlands)
        _seed_parties(db)
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0, "Midlands": 25.0}}),
        )

        with pytest.raises(ValueError, match="Region 'East Midlands' not found in map"):
            build_import_plan(db, map_name=_TEST_MAP_NAME, source_url="https://x.test/a.pdf")

    def test_macro_expands_to_every_internal_region(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Green": {"North": 12.0}}),
        )

        plan = build_import_plan(
            db, map_name=world.map_name, source_url="https://x.test/a.pdf"
        )

        assert len(plan.rows) == 3
        assert {row.region_name for row in plan.rows} == {
            "North East England",
            "North West England",
            "Yorkshire and The Humber",
        }
        assert all(row.percentage == 12.0 for row in plan.rows)
        assert all(row.party_name == "Green" for row in plan.rows)

    def test_missing_macro_key_is_skipped_and_absent_national_omits_row(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll(
                {
                    # National present, only the "London" macro present — every
                    # other macro key is missing and must be silently skipped.
                    "Labour": {NATIONAL_KEY: 30.0, "London": 40.0},
                    # No national key at all: no national row for this party.
                    "Conservative": {"Scotland": 18.0},
                }
            ),
        )

        plan = build_import_plan(
            db, map_name=world.map_name, source_url="https://x.test/a.pdf"
        )

        rows_by_party: dict[str, list[PlannedPollRow]] = {}
        for row in plan.rows:
            rows_by_party.setdefault(row.party_name, []).append(row)

        labour_rows = rows_by_party["Labour"]
        assert len(labour_rows) == 2
        assert {(row.region_name, row.percentage) for row in labour_rows} == {
            ("National", 30.0),
            ("London", 40.0),
        }

        conservative_rows = rows_by_party["Conservative"]
        assert len(conservative_rows) == 1
        assert conservative_rows[0].region_name == "Scotland"
        assert conservative_rows[0].percentage == 18.0
        assert conservative_rows[0].region_id == world.region_ids["Scotland"]

    def test_pollster_absent_defaults_name_and_leaves_regions_mapping_empty(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
        )

        plan = build_import_plan(
            db, map_name=world.map_name, source_url="https://x.test/a.pdf"
        )

        assert plan.pollster_exists is False
        assert plan.pollster_id is None
        assert plan.pollster_name == "Deltapoll"
        assert plan.regions_mapping == ""
        assert plan.poll_exists is False
        assert plan.poll_id is None

    def test_existing_pollster_without_a_matching_poll_is_flagged(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Named unlike the "Deltapoll" fallback default, so a mutation that
        # always returns the default name instead of the real pollster's
        # cannot pass this assertion by coincidence.
        world = westminster_world
        pollster = db.add_pollster("Deltapoll Ltd", "deltapoll_test", weight=1.0)
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
        )

        plan = build_import_plan(
            db,
            map_name=world.map_name,
            source_url="https://x.test/a.pdf",
            pollster_identifier="deltapoll_test",
        )

        assert plan.pollster_exists is True
        assert plan.pollster_id == pollster.id
        assert plan.pollster_name == "Deltapoll Ltd"
        assert plan.poll_exists is False
        assert plan.poll_id is None

    def test_existing_poll_matching_parsed_metadata_is_flagged(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Deltapoll Ltd", "deltapoll_test", weight=1.0)
        existing = db.add_poll(
            pollster.id, world.map_id, _PARSED_START, _PARSED_END, sample_size=_PARSED_SAMPLE
        )
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
        )

        plan = build_import_plan(
            db,
            map_name=world.map_name,
            source_url="https://x.test/a.pdf",
            pollster_identifier="deltapoll_test",
        )

        assert plan.poll_exists is True
        assert plan.poll_id == existing.id


# ── _cli_preview ─────────────────────────────────────────────────────────────


def _plan_for_preview(
    *, pollster_exists: bool, poll_id: int | None, rows: Sequence[PlannedPollRow]
) -> ImportPlan:
    # poll_exists is tied to poll_id (True iff poll_id is not None), so
    # poll_exists=True with poll_id=None is never exercised. That combination
    # isn't reachable via build_import_plan either: it always sets
    # poll_exists=(existing_poll is not None) and poll_id from that same
    # existing_poll's (never-null, autoincrement) id, so the two fields are
    # always constructed in lockstep. _cli_preview's own
    # "poll_exists and poll_id is not None" check is therefore stricter than
    # any real ImportPlan can violate.
    return ImportPlan(
        pollster_identifier="deltapoll",
        pollster_name="Deltapoll",
        pollster_id=(7 if pollster_exists else None),
        pollster_exists=pollster_exists,
        regions_mapping="",
        map_id=1,
        map_name="UK Constituencies post 2022",
        source_url="https://x.test/a.pdf",
        parsed=_parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
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

        out = capsys.readouterr().out
        assert "fieldwork=2026-03-01 to 2026-03-03, sample=1500" in out
        assert "[dry-run] would create pollster: deltapoll" in out
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

        out = capsys.readouterr().out
        assert "pollster exists: deltapoll" in out
        assert "poll exists: 42" in out
        assert "would create pollster" not in out
        assert "would create poll" not in out

    def test_rows_beyond_thirty_are_not_printed(
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
            for n in range(35)
        ]
        plan = _plan_for_preview(pollster_exists=False, poll_id=None, rows=rows)

        _cli_preview(plan)

        out = capsys.readouterr().out
        assert out.count("[dry-run] would insert row:") == 30
        assert "Region 29" in out
        assert "Region 30" not in out


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
        monkeypatch.setattr(deltapoll_import, "Database", lambda: db)
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "deltapoll_import.py",
                "--source-url",
                "https://x.test/a.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "deltapoll_test",
                "--dry-run",
            ],
        )

        deltapoll_import.main()

        out = capsys.readouterr().out
        assert "Fetching source: https://x.test/a.pdf" in out
        assert "[dry-run] would create pollster: deltapoll_test" in out
        assert db.get_pollster_by_identifier("deltapoll_test") is None

    def test_commit_creates_pollster_and_poll(
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

        monkeypatch.setattr(deltapoll_import, "Database", lambda: db)
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", fake_extract_pdf_text)
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "deltapoll_import.py",
                "--source-url",
                "https://x.test/a.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "deltapoll_test",
            ],
        )

        deltapoll_import.main()

        out = capsys.readouterr().out
        assert "created pollster: deltapoll_test" in out
        assert "created poll:" in out
        assert "inserted poll rows: 1" in out
        assert db.get_pollster_by_identifier("deltapoll_test") is not None
        polls = db.get_polls_for_map(world.map_id)
        assert len(polls) == 1
        assert fetched_urls == ["https://x.test/a.pdf"]
        assert polls[0].source_url == "https://x.test/a.pdf"

    def test_commit_skips_existing_rows_without_replace_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Deltapoll Ltd", "deltapoll_test", weight=1.0)
        poll = db.add_poll(
            pollster.id, world.map_id, _PARSED_START, _PARSED_END, sample_size=_PARSED_SAMPLE
        )
        db.add_poll_row(poll.id, world.party_ids["Labour"], 25.0)
        monkeypatch.setattr(deltapoll_import, "Database", lambda: db)
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "deltapoll_import.py",
                "--source-url",
                "https://x.test/a.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "deltapoll_test",
            ],
        )

        deltapoll_import.main()

        out = capsys.readouterr().out
        assert "pollster exists: deltapoll_test" in out
        assert f"poll exists: {poll.id}" in out
        assert f"poll {poll.id} already has rows; use --replace-rows to overwrite" in out
        assert len(db.get_rows_for_poll(poll.id)) == 1

    def test_replace_rows_deletes_then_inserts(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Deltapoll Ltd", "deltapoll_test", weight=1.0)
        poll = db.add_poll(
            pollster.id, world.map_id, _PARSED_START, _PARSED_END, sample_size=_PARSED_SAMPLE
        )
        db.add_poll_row(poll.id, world.party_ids["Labour"], 25.0)
        monkeypatch.setattr(deltapoll_import, "Database", lambda: db)
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "deltapoll_import.py",
                "--source-url",
                "https://x.test/a.pdf",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "deltapoll_test",
                "--replace-rows",
            ],
        )

        deltapoll_import.main()

        out = capsys.readouterr().out
        assert "deleted existing rows: 1" in out
        assert "inserted poll rows: 1" in out
        rows = db.get_rows_for_poll(poll.id)
        assert len(rows) == 1
        assert rows[0].percentage == 30.0

    def test_defaults_pin_source_map_and_pollster_identifier(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No CLI args resolve against the module's real DEFAULT_* literals.

        The map-name default is checked against westminster_world.map_name,
        an independent fixture constant (uk_fixtures.WESTMINSTER_MAP_NAME),
        not against deltapoll_import.DEFAULT_MAP_NAME itself, so a mutation to
        either side would be caught. The source URL and pollster identifier
        are asserted against literals for the same reason: comparing against
        deltapoll_import.DEFAULT_SOURCE_URL/DEFAULT_POLLSTER_IDENTIFIER would
        just compare the code under test with itself.
        """
        assert westminster_world.map_name == "UK Constituencies post 2022"
        monkeypatch.setattr(deltapoll_import, "Database", lambda: db)
        monkeypatch.setattr(deltapoll_import, "_extract_pdf_text", lambda _url: "text")
        monkeypatch.setattr(
            deltapoll_import,
            "parse_poll",
            lambda _text: _parsed_poll({"Labour": {NATIONAL_KEY: 30.0}}),
        )
        monkeypatch.setattr("sys.argv", ["deltapoll_import.py", "--dry-run"])

        deltapoll_import.main()

        out = capsys.readouterr().out
        assert (
            "Fetching source: "
            "https://deltapoll.co.uk/wp-content/uploads/2026/01/"
            "260105_Deltapoll-Mirror-pdf.pdf" in out
        )
        assert "[dry-run] would create pollster: deltapoll" in out
