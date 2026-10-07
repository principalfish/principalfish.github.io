"""Tests for the Lord Ashcroft XLS/XLSX poll importer.

``commit_import_plan`` and ``_find_existing_poll`` are already covered, across
all eleven Westminster importers, by ``test_westminster_importers_commit.py``.
This file covers everything else: the XLS-fetch chain, the fake-``xlrd``
parsing helpers, ``build_import_plan``'s region handling, ``_cli_preview`` and
``main``.

``_find_sample_size`` has a latent (never yet observed live) bug: a genuine
xlrd numeric cell in a "Weighted sample" row loses its decimal point.
``xlrd`` returns every ``XL_CELL_NUMBER`` cell as a Python ``float`` (never
an ``int`` or a string), including for whole numbers, so a cell holding
``2000`` comes back as ``2000.0``. If that row's value were read through
``_as_int`` (which converts via ``str(value)`` and strips non-digit
characters), ``2000.0`` would become the digit string ``"20000"``, not
``"2000"``. **This is latent, not live**: ``_find_sample_size`` returns on
its *first* match, and in every real Lord Ashcroft workbook checked (Jun
2025, Jan 2026, Feb 2026 — downloaded and run through the real
``parse_poll_from_xls_url`` during review), a "Sample Size: N adults..."
text row appears earlier in the scan window than "Weighted Sample", so the
vulnerable float-cell branch is never reached; the stored DB sample sizes
match the workbooks' published figures exactly. See ``TestFindSampleSize``
and ``TestAsInt`` below for the pinned mechanism and the test proving the
real-world layout is safe.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
import xlrd
from openpyxl import Workbook

from db import Database
from polls.importers.westminster import lord_ashcroft_import
from polls.importers.westminster.lord_ashcroft_import import (
    PARTY_NAME_MAP,
    SOURCE_REGION_TO_INTERNAL,
    ImportPlan,
    ParsedPoll,
    PlannedPollRow,
    _as_int,
    _as_text,
    _canonical_party,
    _cli_preview,
    _fetch_bytes,
    _find_fieldwork,
    _find_region_columns,
    _find_sample_size,
    _find_vi_block_start,
    _find_weighted_sample_row,
    _parse_party_percentages,
    _parse_party_region_percentages,
    _resolve_xls_url,
    _to_float,
    build_import_plan,
    parse_poll_from_xls_url,
)
from tests.uk_fixtures import (
    FakeUrlResponse,
    FakeXlrdBook,
    FakeXlrdSheet,
    WestminsterWorld,
)

# ── Local fixtures and seed helpers ─────────────────────────────────────────
#
# Most build_import_plan/main tests below take the westminster_world fixture.
# Two tests genuinely can't: test_missing_parties_raises (westminster_world
# seeds every required party, so it can never exhibit "parties missing") and
# test_missing_region_raises (westminster_world seeds every internal region
# lord_ashcroft maps to). Those two seed their own minimal map with
# _seed_map/_seed_parties.

_TEST_MAP_NAME = "Lord Ashcroft Test Map"

_REQUIRED_PARTY_NAMES: tuple[str, ...] = tuple(sorted(set(PARTY_NAME_MAP.values())))
_ALL_INTERNAL_REGIONS: tuple[str, ...] = tuple(
    sorted(set(SOURCE_REGION_TO_INTERNAL.values()))
)

_PARSED_START = date(2026, 1, 3)
_PARSED_END = date(2026, 1, 5)
_PARSED_SAMPLE = 1503


def _seed_parties(db: Database) -> dict[str, int]:
    """Seed the eight canonical parties ``build_import_plan`` requires."""
    return {name: db.add_party(name).id for name in _REQUIRED_PARTY_NAMES}


def _seed_map(db: Database, name: str, region_names: tuple[str, ...]) -> int:
    """Seed a map named ``name`` with only ``region_names``, no parties."""
    poll_map = db.add_map(name, parliament="westminster")
    for region in region_names:
        db.add_region(poll_map.id, region)
    return poll_map.id


def _parsed_poll(
    *,
    party_percentages: dict[str, float] | None = None,
    party_region_percentages: dict[str, dict[str, float]] | None = None,
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


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a real network call impossible from this file, even by accident.

    Tests that need a specific ``urlopen`` behaviour monkeypatch it themselves
    inside the test body, which runs after this fixture and overrides it.
    """

    def _blocked(*_a: object, **_k: object) -> object:
        raise AssertionError("test attempted a real network call via urlopen")

    monkeypatch.setattr(lord_ashcroft_import, "urlopen", _blocked)


# ── _fetch_bytes ─────────────────────────────────────────────────────────────


class TestFetchBytes:
    """Tests for _fetch_bytes — a raw urlopen wrapped with a browser UA."""

    def test_returns_response_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            lord_ashcroft_import,
            "urlopen",
            lambda *_a, **_k: FakeUrlResponse(b"payload-bytes"),
        )
        assert _fetch_bytes("https://example.test/file.xls") == b"payload-bytes"

    def test_sends_browser_user_agent_url_and_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def _fake_urlopen(request: object, *, timeout: int) -> FakeUrlResponse:
            captured["url"] = request.full_url  # type: ignore[attr-defined]
            headers = request.header_items()  # type: ignore[attr-defined]
            captured["headers"] = dict(headers)
            captured["timeout"] = timeout
            return FakeUrlResponse(b"x")

        monkeypatch.setattr(lord_ashcroft_import, "urlopen", _fake_urlopen)

        _fetch_bytes("https://example.test/file.xls")

        assert captured["url"] == "https://example.test/file.xls"
        assert captured["timeout"] == 60
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert headers.get("User-agent", "").startswith("Mozilla/5.0")


# ── _resolve_xls_url ─────────────────────────────────────────────────────────


class TestResolveXlsUrl:
    """Tests for _resolve_xls_url — direct XLS/XLSX URL vs an HTML page link."""

    def test_direct_xls_url_returned_unchanged_without_fetching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: object, **_k: object) -> bytes:
            raise AssertionError("must not fetch a URL that already ends in .xls")

        monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", _boom)
        url = "https://lordashcroftpolls.com/wp-content/uploads/2026/01/poll.XLS"

        assert _resolve_xls_url(url) == url

    def test_direct_xlsx_url_returned_unchanged_without_fetching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: object, **_k: object) -> bytes:
            raise AssertionError("must not fetch a URL that already ends in .xlsx")

        monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", _boom)
        url = "https://lordashcroftpolls.com/wp-content/uploads/2026/01/poll.xlsx"

        assert _resolve_xls_url(url) == url

    def test_html_page_with_xls_link_resolves_to_absolute_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        html = (
            b'<html><body><a href="/wp-content/uploads/tables.xls">Tables</a>'
            b"</body></html>"
        )
        monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", lambda _url: html)

        result = _resolve_xls_url("https://lordashcroftpolls.com/2026/01/some-poll/")

        assert result == "https://lordashcroftpolls.com/wp-content/uploads/tables.xls"

    def test_first_of_several_links_is_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        html = (
            b"<html><body>"
            b'<a href="https://cdn.test/first.xlsx">First</a>'
            b'<a href="https://cdn.test/second.xlsx">Second</a>'
            b"</body></html>"
        )
        monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", lambda _url: html)

        result = _resolve_xls_url("https://lordashcroftpolls.com/2026/01/some-poll/")

        assert result == "https://cdn.test/first.xlsx"

    def test_blank_href_does_not_crash_or_get_picked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A blank ``href`` does not crash the scan and the real link still wins.

        This does not actually witness the ``if not href: continue`` guard:
        a removed guard would fall through to ``urljoin(source_url, "")``,
        which returns ``source_url`` itself — and since ``source_url`` is
        the HTML page (never a ``.xls``/``.xlsx`` URL, or the function
        would have returned before fetching), that fallback can never look
        like an XLS/XLSX link either. The guard is therefore not observable
        through this function's return value in any reachable scenario;
        this test only proves the blank anchor doesn't crash the scan or
        produce a wrong result — accept it as coverage-only, not a guard
        witness.
        """
        html = (
            b"<html><body>"
            b'<a href="   ">Blank</a>'
            b'<a href="/tables.xls">Tables</a>'
            b"</body></html>"
        )
        monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", lambda _url: html)

        result = _resolve_xls_url("https://lordashcroftpolls.com/2026/01/some-poll/")

        assert result == "https://lordashcroftpolls.com/tables.xls"

    def test_no_xls_link_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        html = b'<html><body><a href="/about">About</a></body></html>'
        monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", lambda _url: html)

        with pytest.raises(ValueError, match="No XLS/XLSX link found"):
            _resolve_xls_url("https://lordashcroftpolls.com/2026/01/some-poll/")


# ── _as_text ─────────────────────────────────────────────────────────────────


class TestAsText:
    """Tests for _as_text — stripped string, empty for None."""

    def test_none_returns_empty_string(self) -> None:
        assert _as_text(None) == ""

    def test_string_is_stripped(self) -> None:
        assert _as_text("  Conservative  ") == "Conservative"

    def test_float_is_stringified(self) -> None:
        assert _as_text(2000.0) == "2000.0"

    def test_int_is_stringified(self) -> None:
        assert _as_text(42) == "42"


# ── _as_int ──────────────────────────────────────────────────────────────────


class TestAsInt:
    """Tests for _as_int — digit extraction from a cell's string form."""

    def test_extracts_digits_from_plain_string(self) -> None:
        assert _as_int("1503") == 1503

    def test_strips_thousands_separator(self) -> None:
        assert _as_int("1,503") == 1503

    def test_extracts_digits_from_a_labelled_string(self) -> None:
        assert _as_int("Sample size: 1503") == 1503

    def test_no_digits_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse integer value from"):
            _as_int("no digits here")

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse integer value from None"):
            _as_int(None)

    def test_real_xlrd_float_cell_loses_its_decimal_point_pins_current_behaviour(
        self,
    ) -> None:
        """A genuine xlrd numeric cell's decimal point is silently deleted.

        xlrd returns every XL_CELL_NUMBER cell as a Python float — 2000
        stored in a spreadsheet cell comes back as ``2000.0``, not the string
        ``"2000"`` and not the int ``2000``. ``_as_int`` converts via
        ``str(value)`` then strips non-digit characters, so the "." in
        "2000.0" is simply removed rather than treated as a value boundary,
        leaving the digits "20000" — ten times the real number, not 2000.

        This is a **latent** bug in ``_as_int`` itself, pinned here rather
        than fixed. See ``TestFindSampleSize`` for why the specific call
        site that could trigger it (a "Weighted sample" row read through
        ``_find_sample_size``) has never actually been observed to: it only
        fires if no "Sample size" text row appears earlier in the scan
        window, which no real workbook checked during review does.
        """
        assert _as_int(2000.0) == 20000

    def test_real_xlrd_float_with_genuine_decimal_also_corrupted(self) -> None:
        """A non-whole float value is corrupted by the same mechanism.

        557.6 becomes the digit string "5576" — the decimal point vanishes
        instead of the value being rounded or truncated. This demonstrates
        the same latent mechanism as the whole-number case above; it makes
        no claim about what any real weighted-sample value actually is.
        """
        assert _as_int(557.6) == 5576


# ── _find_fieldwork ──────────────────────────────────────────────────────────


class TestFindFieldwork:
    """Tests for _find_fieldwork — the fieldwork date-range line in column A."""

    def test_finds_fieldwork_with_ordinal_suffixes(self) -> None:
        sheet = FakeXlrdSheet(
            "Sheet1",
            [[""], ["Fieldwork: 3rd-5th January 2026"], ["Sample size: 1503"]],
        )
        assert _find_fieldwork(sheet) == (date(2026, 1, 3), date(2026, 1, 5))

    def test_finds_fieldwork_without_ordinal_suffixes(self) -> None:
        sheet = FakeXlrdSheet("Sheet1", [["Fieldwork: 3-5 January 2026"]])
        assert _find_fieldwork(sheet) == (date(2026, 1, 3), date(2026, 1, 5))

    def test_blank_rows_before_the_line_are_skipped(self) -> None:
        sheet = FakeXlrdSheet(
            "Sheet1", [[""], [""], ["Fieldwork: 3-5 January 2026"]]
        )
        assert _find_fieldwork(sheet) == (date(2026, 1, 3), date(2026, 1, 5))

    def test_rows_with_no_match_are_skipped(self) -> None:
        sheet = FakeXlrdSheet(
            "Sheet1",
            [["Lord Ashcroft Polls"], ["Fieldwork: 3-5 January 2026"]],
        )
        assert _find_fieldwork(sheet) == (date(2026, 1, 3), date(2026, 1, 5))

    def test_invalid_month_raises(self) -> None:
        sheet = FakeXlrdSheet("Sheet1", [["Fieldwork: 3-5 Fooruary 2026"]])
        with pytest.raises(ValueError, match="Could not parse month in fieldwork line"):
            _find_fieldwork(sheet)

    @pytest.mark.parametrize(
        "line, expected_start, expected_end",
        [
            (
                "Fieldwork: 27th August - 1st September 2026",
                date(2026, 8, 27),
                date(2026, 9, 1),
            ),
            (
                "Fieldwork: 30th July - 3rd August 2026",
                date(2026, 7, 30),
                date(2026, 8, 3),
            ),
            ("FIELDWORK: 27 Aug.–1 Sep. 2026", date(2026, 8, 27), date(2026, 9, 1)),
            (
                "Fieldwork: 29th December — 2nd January 2026",
                date(2025, 12, 29),
                date(2026, 1, 2),
            ),
            (
                "Fieldwork: 3 January - 5 January 2026",
                date(2026, 1, 3),
                date(2026, 1, 5),
            ),
        ],
    )
    def test_explicit_month_ranges(
        self, line: str, expected_start: date, expected_end: date
    ) -> None:
        sheet = FakeXlrdSheet("Sheet1", [[line]])
        assert _find_fieldwork(sheet) == (expected_start, expected_end)

    @pytest.mark.parametrize(
        "line",
        [
            "Fieldwork: 30 February - 2 March 2026",
            "Fieldwork: 5-3 January 2026",
            "Fieldwork: 27 Fooruary - 1 September 2026",
            "Fieldwork: 27 August - 1 Fooruary 2026",
        ],
    )
    def test_invalid_explicit_ranges_raise(self, line: str) -> None:
        with pytest.raises(ValueError):
            _find_fieldwork(FakeXlrdSheet("Sheet1", [[line]]))

    def test_not_found_raises(self) -> None:
        sheet = FakeXlrdSheet("Sheet1", [["No fieldwork line here"]])
        with pytest.raises(ValueError, match="Fieldwork line not found"):
            _find_fieldwork(sheet)


# ── _find_sample_size ────────────────────────────────────────────────────────


class TestFindSampleSize:
    """Tests for _find_sample_size — the two label formats in column A/B."""

    def test_sample_size_label_reads_column_a(self) -> None:
        sheet = FakeXlrdSheet("Sheet1", [["Sample size: 1503"]])
        assert _find_sample_size(sheet) == 1503

    def test_sample_size_label_is_case_insensitive(self) -> None:
        sheet = FakeXlrdSheet("Sheet1", [["SAMPLE SIZE: 2001"]])
        assert _find_sample_size(sheet) == 2001

    def test_weighted_sample_label_reads_column_b(self) -> None:
        sheet = FakeXlrdSheet("Sheet1", [["Weighted sample", "1503"]])
        assert _find_sample_size(sheet) == 1503

    def test_not_found_raises(self) -> None:
        sheet = FakeXlrdSheet("Sheet1", [["Nothing relevant here"]])
        with pytest.raises(ValueError, match="Sample size not found"):
            _find_sample_size(sheet)

    def test_weighted_sample_as_real_xlrd_float_is_corrupted_pins_current_behaviour(
        self,
    ) -> None:
        """A genuine xlrd numeric cell in the "Weighted sample" row is corrupted
        *if that row is reached at all*.

        If a workbook's "Weighted sample" row is the first (and only) match
        in the scan window, ``_find_sample_size`` reads its column-B float
        through ``_as_int`` and returns a corrupted value — here 20000, not
        2000: the "." is deleted by ``_as_int``'s digit-only regex rather
        than acting as a boundary.

        **Verdict: latent, not live.** ``_find_sample_size`` returns on its
        *first* match, checking "sample size" before "weighted sample" on
        every row. In every real Lord Ashcroft workbook checked during
        review (Jun 2025, Jan 2026, Feb 2026 — downloaded and run through
        the real ``parse_poll_from_xls_url``), a "Sample Size: N adults..."
        text row precedes "Weighted Sample" in the scan window, so that
        text row's own ``_as_int`` call (on a string cell, not a float one)
        returns first with the correct value, and the vulnerable branch
        below is never reached. The stored DB sample sizes for every real
        lord_ashcroft poll match the workbooks' published figures exactly.
        This isolated test constructs a sheet with *only* the vulnerable
        row, to pin the mechanism in ``_as_int``/``_find_sample_size``
        without claiming it has ever fired on real data — see
        ``test_sample_size_text_row_before_weighted_sample_returns_the_correct_value``
        below for the real-world-safe layout.
        """
        sheet = FakeXlrdSheet("Sheet1", [["Weighted sample", 2000.0]])

        assert _find_sample_size(sheet) == 20000

    def test_sample_size_text_row_before_weighted_sample_returns_the_correct_value(
        self,
    ) -> None:
        """The real-world row layout never reaches the vulnerable branch.

        Mirrors the layout of every real Lord Ashcroft workbook checked
        during review: a "Sample Size: N adults..." text row (column A) is
        followed later by "Unweighted Sample" and "Weighted Sample" rows
        whose column-B values are genuine xlrd floats (5576.0). Because
        ``_find_sample_size`` returns on its first match, the text row wins
        and the correct 5576 is returned — the float-corruption mechanism
        pinned above never fires here.
        """
        sheet = FakeXlrdSheet(
            "Sheet1",
            [
                ["Lord Ashcroft Polls National Survey"],
                ["Fieldwork: 3rd-5th January 2026"],
                ["Sample Size: 5576 adults in GB"],
                ["Unweighted Sample", 5576.0],
                ["Weighted Sample", 5576.0],
            ],
        )

        assert _find_sample_size(sheet) == 5576


# ── _find_vi_block_start ─────────────────────────────────────────────────────


class TestFindViBlockStart:
    """Tests for _find_vi_block_start — the VI header row, whole sheet scanned."""

    def test_finds_header_case_insensitively(self) -> None:
        sheet = FakeXlrdSheet(
            "Sheet1",
            [
                ["Some preamble"],
                ["current westminster voting intention"],
                ["Conservative", 30],
            ],
        )
        assert _find_vi_block_start(sheet) == 1

    def test_not_found_raises(self) -> None:
        sheet = FakeXlrdSheet("Sheet1", [["No header here"]])
        with pytest.raises(ValueError, match="CURRENT WESTMINSTER VOTING INTENTION"):
            _find_vi_block_start(sheet)


# ── _canonical_party ─────────────────────────────────────────────────────────


class TestCanonicalParty:
    """Tests for _canonical_party — substring matching against PARTY_NAME_MAP."""

    def test_exact_labels(self) -> None:
        assert _canonical_party("Conservative") == "Conservative"
        assert _canonical_party("Labour") == "Labour"
        assert _canonical_party("Green") == "Green"

    def test_lib_dem_variants_map_to_liberal_democrats(self) -> None:
        assert _canonical_party("Lib Dem") == "Liberal Democrats"
        assert _canonical_party("Liberal Democrat") == "Liberal Democrats"
        assert _canonical_party("Liberal Democrats") == "Liberal Democrats"

    def test_snp_and_plaid_variants(self) -> None:
        assert _canonical_party("SNP") == "Scottish National Party"
        assert _canonical_party("Scottish National Party") == "Scottish National Party"
        assert _canonical_party("Plaid") == "Plaid Cymru"
        assert _canonical_party("Plaid Cymru") == "Plaid Cymru"

    def test_another_party_maps_to_other(self) -> None:
        assert _canonical_party("Another party") == "Other"
        assert _canonical_party("Other") == "Other"

    def test_case_insensitive_and_whitespace_collapsed(self) -> None:
        assert _canonical_party("  green   party  ") == "Green"

    def test_marker_matches_as_a_substring_of_a_longer_label(self) -> None:
        # "Reform UK candidates" is not equal to any PARTY_NAME_MAP key, but
        # contains "Reform UK" as a substring; the match must still succeed
        # (proving this is genuinely substring matching, not equality).
        assert _canonical_party("Reform UK candidates") == "Reform UK"

    def test_unmatched_label_returns_none(self) -> None:
        assert _canonical_party("Your Party") is None
        assert _canonical_party("") is None


# ── _parse_party_percentages ─────────────────────────────────────────────────

_FULL_VI_ROWS: list[list[object]] = [
    ["CURRENT WESTMINSTER VOTING INTENTION"],
    ["Conservative", 19.4],
    ["Labour", 20.6],
    ["Lib Dem", 10.0],
    ["SNP", 3.5],
    ["Plaid", 1.4],
    ["Reform UK", 23.0],
    ["Green", 8.0],
    ["Another party", 2.1],
]


class TestParsePartyPercentages:
    """Tests for _parse_party_percentages — the 8-party national VI block."""

    def test_parses_and_rounds_all_eight_parties(self) -> None:
        sheet = FakeXlrdSheet("Sheet1", _FULL_VI_ROWS)

        parsed = _parse_party_percentages(sheet)

        assert parsed == {
            "Conservative": 19.0,
            "Labour": 21.0,
            "Liberal Democrats": 10.0,
            "Scottish National Party": 4.0,
            "Plaid Cymru": 1.0,
            "Reform UK": 23.0,
            "Green": 8.0,
            "Other": 2.0,
        }

    def test_missing_required_party_raises(self) -> None:
        rows: list[list[object]] = [
            ["CURRENT WESTMINSTER VOTING INTENTION"],
            ["Conservative", 30],
            ["Labour", 40],
        ]
        sheet = FakeXlrdSheet("Sheet1", rows)

        with pytest.raises(
            ValueError, match="Missing party rows in Lord Ashcroft workbook"
        ):
            _parse_party_percentages(sheet)

    def test_unmapped_label_row_is_skipped(self) -> None:
        rows = list(_FULL_VI_ROWS)
        rows.insert(1, ["Undecided", 5])
        sheet = FakeXlrdSheet("Sheet1", rows)

        parsed = _parse_party_percentages(sheet)

        assert parsed == {
            "Conservative": 19.0,
            "Labour": 21.0,
            "Liberal Democrats": 10.0,
            "Scottish National Party": 4.0,
            "Plaid Cymru": 1.0,
            "Reform UK": 23.0,
            "Green": 8.0,
            "Other": 2.0,
        }

    def test_blank_label_row_is_skipped(self) -> None:
        rows = list(_FULL_VI_ROWS)
        rows.insert(1, [""])
        sheet = FakeXlrdSheet("Sheet1", rows)

        parsed = _parse_party_percentages(sheet)

        assert parsed["Conservative"] == 19.0

    def test_blank_value_cell_is_skipped_but_a_later_row_still_counts(self) -> None:
        rows: list[list[object]] = [
            ["CURRENT WESTMINSTER VOTING INTENTION"],
            ["Conservative", ""],  # blank value: skipped, not stored as 0
            ["Conservative", 19.0],  # real value arrives on a later row
            ["Labour", 20.0],
            ["Lib Dem", 10.0],
            ["SNP", 3.0],
            ["Plaid", 1.0],
            ["Reform UK", 23.0],
            ["Green", 8.0],
            ["Another party", 2.0],
        ]
        sheet = FakeXlrdSheet("Sheet1", rows)

        parsed = _parse_party_percentages(sheet)

        assert parsed["Conservative"] == 19.0

    def test_stops_after_eight_parties_even_if_more_labelled_lines_follow(self) -> None:
        rows = [*_FULL_VI_ROWS, ["Conservative", 99]]
        sheet = FakeXlrdSheet("Sheet1", rows)

        parsed = _parse_party_percentages(sheet)

        # If the len(parsed) == 8 break were dropped, this later row would
        # overwrite Conservative with 99.0 instead of leaving it at 19.0.
        assert parsed["Conservative"] == 19.0


# ── _find_region_columns ─────────────────────────────────────────────────────

_REGION_HEADER_8: list[object] = [
    "Region",
    "North East",
    "North West",
    "Yorkshire and the Humber",
    "East Midlands",
    "West Midlands",
    "East of England",
    "London",
    "South East",
]


class TestFindRegionColumns:
    """Tests for _find_region_columns — a header row with >= 8 known regions."""

    def test_finds_eight_recognised_region_columns(self) -> None:
        sheet = FakeXlrdSheet("Values", [_REGION_HEADER_8])

        columns = _find_region_columns(sheet)

        assert len(columns) == 8
        assert columns[1] == "North East England"
        assert columns[8] == "South East England"

    def test_unrecognised_and_blank_headers_are_not_counted(self) -> None:
        row = [*_REGION_HEADER_8, "Not A Region", ""]
        sheet = FakeXlrdSheet("Values", [row])

        columns = _find_region_columns(sheet)

        assert len(columns) == 8
        assert 9 not in columns
        assert 10 not in columns

    def test_row_with_fewer_than_eight_is_skipped_a_later_row_is_used(self) -> None:
        short_row = ["Region", "North East", "North West", "London"]
        sheet = FakeXlrdSheet("Values", [short_row, _REGION_HEADER_8])

        columns = _find_region_columns(sheet)

        assert len(columns) == 8

    def test_fewer_than_eight_anywhere_returns_empty_dict(self) -> None:
        sheet = FakeXlrdSheet("Values", [["Region", "North East", "London"]])

        assert _find_region_columns(sheet) == {}


# ── _find_weighted_sample_row ────────────────────────────────────────────────


class TestFindWeightedSampleRow:
    """Tests for _find_weighted_sample_row — exact "weighted sample" match."""

    def test_finds_exact_case_insensitive_match(self) -> None:
        sheet = FakeXlrdSheet("Values", [["Region", "NE"], ["Weighted Sample", 100]])
        assert _find_weighted_sample_row(sheet) == 1

    def test_not_found_raises(self) -> None:
        sheet = FakeXlrdSheet("Values", [["Region", "NE"]])
        with pytest.raises(ValueError, match="Weighted Sample row not found"):
            _find_weighted_sample_row(sheet)

    def test_match_is_exact_not_substring(self) -> None:
        # Real Lord Ashcroft workbooks list both an "Unweighted sample" and a
        # "Weighted sample" row. "unweighted sample" contains "weighted" as a
        # substring, so a match that merely tested "in" the label (instead of
        # "==") would wrongly return the unweighted row here.
        sheet = FakeXlrdSheet(
            "Values",
            [["Region", "NE"], ["Unweighted sample", 250], ["Weighted Sample", 100]],
        )
        assert _find_weighted_sample_row(sheet) == 2


# ── _to_float ────────────────────────────────────────────────────────────────


class TestToFloat:
    """Tests for _to_float — blank-to-zero conversion for xlrd cell values."""

    def test_none_returns_zero(self) -> None:
        assert _to_float(None) == 0.0

    def test_empty_string_returns_zero(self) -> None:
        assert _to_float("") == 0.0

    def test_whitespace_only_string_returns_zero(self) -> None:
        assert _to_float("   ") == 0.0

    def test_int_is_converted(self) -> None:
        assert _to_float(5) == 5.0

    def test_float_is_passed_through(self) -> None:
        assert _to_float(12.5) == 12.5

    def test_numeric_string_is_parsed(self) -> None:
        assert _to_float("45.6") == 45.6

    def test_non_numeric_string_raises(self) -> None:
        with pytest.raises(ValueError):
            _to_float("not-a-number")


# ── _parse_party_region_percentages ──────────────────────────────────────────


def _values_sheet_rows(
    *, denominators: list[object], party_rows: list[list[object]]
) -> list[list[object]]:
    return [
        _REGION_HEADER_8,
        ["Weighted Sample", *denominators],
        ["CURRENT WESTMINSTER VOTING INTENTION"],
        *party_rows,
    ]


class TestParsePartyRegionPercentages:
    """Tests for _parse_party_region_percentages — the regional VI block."""

    def test_no_region_columns_returns_empty_dict(self) -> None:
        sheet = FakeXlrdSheet(
            "Values",
            [
                ["Region", "North East"],
                ["Weighted Sample", 100],
                ["CURRENT WESTMINSTER VOTING INTENTION"],
                ["Conservative", 30],
            ],
        )
        assert _parse_party_region_percentages(sheet) == {}

    def test_all_zero_denominators_produces_no_regional_rows(self) -> None:
        """All-zero weighted-sample denominators leave every party unparsed.

        This does not by itself witness the early
        ``if not denominators: return {}`` guard: with every denominator
        filtered out, the per-column ``if not denominator: continue`` inside
        the row loop already skips every column for every party row, so
        ``region_values`` stays empty and no party is ever added to
        ``parsed`` — the same ``{}`` this guard would return directly.
        Removing the guard is an equivalent mutant for this input (the VI
        block header is present, so no earlier lookup would raise either).
        This only shows the observable output is correct for this case, not
        that the guard line itself exists — accept it as coverage-only.
        """
        rows = _values_sheet_rows(
            denominators=[0, 0, 0, 0, 0, 0, 0, 0],
            party_rows=[["Conservative", 30, 25, 28, 26, 24, 27, 22, 29]],
        )
        sheet = FakeXlrdSheet("Values", rows)
        assert _parse_party_region_percentages(sheet) == {}

    def test_percentages_computed_from_numerator_over_denominator(self) -> None:
        rows = _values_sheet_rows(
            denominators=[100, 100, 100, 100, 100, 100, 100, 100],
            party_rows=[["Conservative", 22, 20, 25, 23, 21, 24, 18, 27]],
        )
        sheet = FakeXlrdSheet("Values", rows)

        result = _parse_party_region_percentages(sheet)

        assert result["Conservative"]["North East England"] == 22.0
        assert result["Conservative"]["South East England"] == 27.0

    def test_unmapped_label_row_is_skipped(self) -> None:
        rows = _values_sheet_rows(
            denominators=[100, 100, 100, 100, 100, 100, 100, 100],
            party_rows=[
                ["Undecided", 5, 5, 5, 5, 5, 5, 5, 5],
                ["Conservative", 22, 20, 25, 23, 21, 24, 18, 27],
            ],
        )
        sheet = FakeXlrdSheet("Values", rows)

        result = _parse_party_region_percentages(sheet)

        assert set(result) == {"Conservative"}

    def test_blank_label_row_is_skipped(self) -> None:
        rows = _values_sheet_rows(
            denominators=[100, 100, 100, 100, 100, 100, 100, 100],
            party_rows=[
                [""],
                ["Conservative", 22, 20, 25, 23, 21, 24, 18, 27],
            ],
        )
        sheet = FakeXlrdSheet("Values", rows)

        result = _parse_party_region_percentages(sheet)

        assert result["Conservative"]["North East England"] == 22.0

    def test_column_with_zero_denominator_is_skipped_for_every_party(self) -> None:
        # East Midlands (index 4) has a zero weighted sample; every other
        # region has a normal one, so Conservative's row must be missing
        # exactly that one region while keeping the rest.
        rows = _values_sheet_rows(
            denominators=[100, 100, 100, 0, 100, 100, 100, 100],
            party_rows=[["Conservative", 22, 20, 25, 23, 21, 24, 18, 27]],
        )
        sheet = FakeXlrdSheet("Values", rows)

        result = _parse_party_region_percentages(sheet)

        assert "East Midlands" not in result["Conservative"]
        assert result["Conservative"]["North East England"] == 22.0
        assert result["Conservative"]["South East England"] == 27.0

    def test_percentages_are_rounded_to_nearest_integer(self) -> None:
        # 22.6 rounds to 23; truncating (int()) instead would give 22 — using
        # a value whose round() and int() disagree proves this is genuinely
        # rounding, not truncation.
        rows = _values_sheet_rows(
            denominators=[100, 100, 100, 100, 100, 100, 100, 100],
            party_rows=[["Conservative", 22.6, 20, 25, 23, 21, 24, 18, 27]],
        )
        sheet = FakeXlrdSheet("Values", rows)

        result = _parse_party_region_percentages(sheet)

        assert result["Conservative"]["North East England"] == 23.0

    @pytest.mark.parametrize("later_cells", [[""] * 8, [95] * 8])
    def test_later_party_rows_do_not_overwrite_first_results(
        self, later_cells: list[object]
    ) -> None:
        rows = _values_sheet_rows(
            denominators=[100] * 8,
            party_rows=[
                ["Labour", 22, 20, 25, 23, 21, 24, 18, 27],
                ["Labour", *later_cells],
            ],
        )
        result = _parse_party_region_percentages(FakeXlrdSheet("Values", rows))
        assert result["Labour"] == {
            "North East England": 22.0,
            "North West England": 20.0,
            "Yorkshire and The Humber": 25.0,
            "East Midlands": 23.0,
            "West Midlands": 21.0,
            "East of England": 24.0,
            "London": 18.0,
            "South East England": 27.0,
        }

    @pytest.mark.parametrize("blank", [None, ""])
    def test_blank_party_heading_before_data_is_skipped(self, blank: object) -> None:
        rows = _values_sheet_rows(
            denominators=[100] * 8,
            party_rows=[
                ["Conservative-Reform Alliance", *([blank] * 8)],
                ["Conservative", *([22] * 8)],
            ],
        )
        result = _parse_party_region_percentages(FakeXlrdSheet("Values", rows))
        assert set(result) == {"Conservative"}
        assert set(result["Conservative"].values()) == {22.0}

    def test_numeric_zero_is_a_valid_first_result(self) -> None:
        rows = _values_sheet_rows(
            denominators=[100] * 8,
            party_rows=[
                ["Plaid", *([0] * 8)],
                ["Plaid", *([60] * 8)],
            ],
        )
        result = _parse_party_region_percentages(FakeXlrdSheet("Values", rows))
        assert set(result["Plaid Cymru"].values()) == {0.0}

    def test_completed_party_block_stops_before_later_questions(self) -> None:
        rows = _values_sheet_rows(
            denominators=[100] * 8,
            party_rows=[
                *[[row[0], *([row[1]] * 8)] for row in _FULL_VI_ROWS[1:]],
                ["Conservative-Reform Alliance", *(["not a count"] * 8)],
            ],
        )
        result = _parse_party_region_percentages(FakeXlrdSheet("Values", rows))
        assert len(result) == 8
        assert set(result["Conservative"].values()) == {19.0}


# ── parse_poll_from_xls_url ──────────────────────────────────────────────────

_NATIONAL_SHEET_ROWS: list[list[object]] = [
    ["Lord Ashcroft Polls - National Survey"],
    ["Fieldwork: 3rd-5th January 2026"],
    ["Sample size: 1503"],
    [""],
    *_FULL_VI_ROWS,
]

_VALUES_SHEET_ROWS: list[list[object]] = _values_sheet_rows(
    denominators=[100, 100, 100, 100, 100, 100, 100, 100],
    party_rows=[["Conservative", 22, 20, 25, 23, 21, 24, 18, 27]],
)


class TestParsePollFromXlsUrl:
    """Tests for parse_poll_from_xls_url — the full fetch-and-parse pipeline."""

    def test_full_parse_with_a_named_values_sheet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        book = FakeXlrdBook(
            [
                FakeXlrdSheet("National", _NATIONAL_SHEET_ROWS),
                FakeXlrdSheet("Regional Values", _VALUES_SHEET_ROWS),
            ]
        )
        monkeypatch.setattr(
            lord_ashcroft_import, "_fetch_bytes", lambda _url: b"xls-bytes"
        )
        monkeypatch.setattr(
            xlrd, "open_workbook", lambda **_k: book
        )

        parsed = parse_poll_from_xls_url("https://x.test/poll.xls")

        assert parsed.sample_size == 1503
        assert parsed.fieldwork_start == date(2026, 1, 3)
        assert parsed.fieldwork_end == date(2026, 1, 5)
        assert parsed.party_percentages["Conservative"] == 19.0
        assert parsed.party_percentages["Other"] == 2.0
        regional = parsed.party_region_percentages["Conservative"]
        assert regional["London"] == 18.0
        assert regional["South East England"] == 27.0

    def test_values_sheet_name_match_is_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        book = FakeXlrdBook(
            [
                FakeXlrdSheet("National", _NATIONAL_SHEET_ROWS),
                FakeXlrdSheet("REGIONAL VALUES TABLE", _VALUES_SHEET_ROWS),
            ]
        )
        monkeypatch.setattr(
            lord_ashcroft_import, "_fetch_bytes", lambda _url: b"xls-bytes"
        )
        monkeypatch.setattr(
            xlrd, "open_workbook", lambda **_k: book
        )

        parsed = parse_poll_from_xls_url("https://x.test/poll.xls")

        assert parsed.party_region_percentages["Conservative"]["London"] == 18.0

    @pytest.mark.parametrize(
        "count_name",
        ["Regional Counts", "COUNTS", "Demographics - n", "Demographics - N"],
    )
    def test_count_sheet_aliases_are_used_for_regions(
        self, monkeypatch: pytest.MonkeyPatch, count_name: str
    ) -> None:
        book = FakeXlrdBook(
            [
                FakeXlrdSheet("National", _NATIONAL_SHEET_ROWS),
                FakeXlrdSheet(count_name, _VALUES_SHEET_ROWS),
            ]
        )
        monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", lambda _url: b"xls")
        monkeypatch.setattr(xlrd, "open_workbook", lambda **_k: book)
        parsed = parse_poll_from_xls_url("https://x.test/poll.xls")
        assert parsed.party_region_percentages["Conservative"]["London"] == 18.0

    def test_values_sheet_has_priority_over_an_earlier_counts_sheet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        counts_rows = _values_sheet_rows(
            denominators=[100] * 8,
            party_rows=[["Conservative", *([99] * 8)]],
        )
        book = FakeXlrdBook(
            [
                FakeXlrdSheet("National", _NATIONAL_SHEET_ROWS),
                FakeXlrdSheet("Counts", counts_rows),
                FakeXlrdSheet("Values", _VALUES_SHEET_ROWS),
            ]
        )
        monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", lambda _url: b"xls")
        monkeypatch.setattr(xlrd, "open_workbook", lambda **_k: book)
        parsed = parse_poll_from_xls_url("https://x.test/poll.xls")
        assert parsed.party_region_percentages["Conservative"]["London"] == 18.0

    def test_nonterminal_n_sheet_name_does_not_select_a_count_sheet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        book = FakeXlrdBook(
            [
                FakeXlrdSheet("National", _NATIONAL_SHEET_ROWS),
                FakeXlrdSheet("Demographics - notes", _VALUES_SHEET_ROWS),
            ]
        )
        monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", lambda _url: b"xls")
        monkeypatch.setattr(xlrd, "open_workbook", lambda **_k: book)
        parsed = parse_poll_from_xls_url("https://x.test/poll.xls")
        assert parsed.party_region_percentages == {}

    @pytest.mark.parametrize("url_suffix", ["xls", "xlsx"])
    def test_real_xlsx_payload_with_percent_and_count_sheets(
        self, monkeypatch: pytest.MonkeyPatch, url_suffix: str
    ) -> None:
        workbook = Workbook()
        national = workbook.active
        assert national is not None
        national.title = "Demographics - %"
        national.append(["Fieldwork: 27th August - 1st September 2026"])
        national.append(["Sample Size: 1503 adults"])
        for row in _FULL_VI_ROWS:
            national.append(row)
        counts = workbook.create_sheet("Demographics - n")
        rows = _values_sheet_rows(
            denominators=[200] * 8,
            party_rows=[
                ["Conservative-Reform Alliance", *([None] * 8)],
                *[
                    [row[0], *([float(str(row[1])) * 2] * 8)]
                    for row in _FULL_VI_ROWS[1:]
                ],
                ["Conservative-Reform Alliance", *([None] * 8)],
            ],
        )
        for row in rows:
            counts.append(row)
        payload = BytesIO()
        workbook.save(payload)
        workbook.close()
        monkeypatch.setattr(
            lord_ashcroft_import, "_fetch_bytes", lambda _url: payload.getvalue()
        )

        def reject_legacy_reader(**_kwargs: object) -> None:
            raise AssertionError("OOXML content must use the modern reader")

        monkeypatch.setattr(xlrd, "open_workbook", reject_legacy_reader)
        parsed = parse_poll_from_xls_url(f"https://x.test/poll.{url_suffix}")
        assert parsed.sample_size == 1503
        assert (parsed.fieldwork_start, parsed.fieldwork_end) == (
            date(2026, 8, 27), date(2026, 9, 1)
        )
        assert parsed.party_percentages == {
            "Conservative": 19.0,
            "Labour": 21.0,
            "Liberal Democrats": 10.0,
            "Scottish National Party": 4.0,
            "Plaid Cymru": 1.0,
            "Reform UK": 23.0,
            "Green": 8.0,
            "Other": 2.0,
        }
        assert parsed.party_region_percentages == {
            party: dict.fromkeys(
                [
                    SOURCE_REGION_TO_INTERNAL[str(region)]
                    for region in _REGION_HEADER_8[1:]
                ],
                percentage,
            )
            for party, percentage in parsed.party_percentages.items()
        }

    def test_no_values_sheet_falls_back_to_sheet_zero_for_regions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # This fixture's sheet 0 happens to have neither region headers nor
        # a weighted-sample row, so the fallback here only reaches an empty
        # dict — it does NOT prove the fallback is safe in general. Per
        # review: the real Jun 2025 single-sheet workbook (poll 234, no
        # separate "values" sheet) DOES have region headers and a Weighted
        # Sample row on sheet 0 itself, and there the fallback ends up
        # dividing percentages by the wrong counts, producing garbage
        # regional values. That real single-sheet layout isn't reproduced
        # or otherwise pinned by this file — flagged here rather than left
        # to look like this test shows the fallback is harmless.
        book = FakeXlrdBook([FakeXlrdSheet("National", _NATIONAL_SHEET_ROWS)])
        monkeypatch.setattr(
            lord_ashcroft_import, "_fetch_bytes", lambda _url: b"xls-bytes"
        )
        monkeypatch.setattr(
            xlrd, "open_workbook", lambda **_k: book
        )

        parsed = parse_poll_from_xls_url("https://x.test/poll.xls")

        assert parsed.party_region_percentages == {}
        assert parsed.party_percentages["Conservative"] == 19.0

    def test_no_sheets_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        book = FakeXlrdBook([])
        monkeypatch.setattr(
            lord_ashcroft_import, "_fetch_bytes", lambda _url: b"xls-bytes"
        )
        monkeypatch.setattr(
            xlrd, "open_workbook", lambda **_k: book
        )

        with pytest.raises(ValueError, match="Lord Ashcroft workbook has no sheets"):
            parse_poll_from_xls_url("https://x.test/poll.xls")

    def test_fetch_bytes_receives_the_xls_url_and_open_workbook_the_fetched_bytes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fetched: list[str] = []
        opened_with: list[bytes] = []

        def _fake_fetch(url: str) -> bytes:
            fetched.append(url)
            return b"specific-xls-payload"

        def _fake_open_workbook(*, file_contents: bytes) -> FakeXlrdBook:
            opened_with.append(file_contents)
            return book

        book = FakeXlrdBook([FakeXlrdSheet("National", _NATIONAL_SHEET_ROWS)])
        monkeypatch.setattr(lord_ashcroft_import, "_fetch_bytes", _fake_fetch)
        monkeypatch.setattr(xlrd, "open_workbook", _fake_open_workbook)

        parse_poll_from_xls_url("https://x.test/specific-poll.xls")

        assert fetched == ["https://x.test/specific-poll.xls"]
        assert opened_with == [b"specific-xls-payload"]


# ── build_import_plan ────────────────────────────────────────────────────────


class TestBuildImportPlan:
    """Tests for build_import_plan's own logic (commit is tested elsewhere)."""

    def test_map_missing_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )

        with pytest.raises(ValueError, match="Map not found"):
            build_import_plan(
                db, map_name="No Such Map", source_url="https://x.test/a.xls"
            )

    def test_missing_parties_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Can't use westminster_world: it seeds every required party, so this
        # case (no parties at all) is unreachable through it.
        _seed_map(db, _TEST_MAP_NAME, _ALL_INTERNAL_REGIONS)
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )

        with pytest.raises(ValueError, match="Missing parties in database"):
            build_import_plan(
                db, map_name=_TEST_MAP_NAME, source_url="https://x.test/a.xls"
            )

    def test_missing_region_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Can't use westminster_world: it seeds every internal region
        # SOURCE_REGION_TO_INTERNAL maps to, so a missing one is unreachable
        # through it. This is the one test in this class that genuinely
        # needs a custom region set.
        regions_without_london = tuple(
            region for region in _ALL_INTERNAL_REGIONS if region != "London"
        )
        _seed_map(db, _TEST_MAP_NAME, regions_without_london)
        _seed_parties(db)
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(
                party_percentages={"Labour": 30.0},
                party_region_percentages={"Labour": {"London": 40.0}},
            ),
        )

        with pytest.raises(ValueError, match="Missing region in database: 'London'"):
            build_import_plan(
                db, map_name=_TEST_MAP_NAME, source_url="https://x.test/a.xls"
            )

    def test_national_and_regional_rows_are_built(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(
                party_percentages={"Labour": 30.0, "Conservative": 25.0},
                party_region_percentages={"Labour": {"London": 40.0, "Scotland": 35.0}},
            ),
        )

        plan = build_import_plan(
            db, map_name=world.map_name, source_url="https://x.test/a.xls"
        )

        national_rows = [row for row in plan.rows if row.region_id is None]
        regional_rows = [row for row in plan.rows if row.region_id is not None]
        assert {(row.party_name, row.percentage) for row in national_rows} == {
            ("Labour", 30.0),
            ("Conservative", 25.0),
        }
        assert all(row.region_name == "National" for row in national_rows)
        assert len(regional_rows) == 2
        assert {(row.region_name, row.percentage) for row in regional_rows} == {
            ("London", 40.0),
            ("Scotland", 35.0),
        }
        for row in regional_rows:
            assert row.region_id == world.region_ids[row.region_name]

    def test_pollster_absent_defaults_name_and_leaves_regions_mapping_empty(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )

        plan = build_import_plan(
            db, map_name=world.map_name, source_url="https://x.test/a.xls"
        )

        assert plan.pollster_exists is False
        assert plan.pollster_id is None
        assert plan.pollster_name == "Lord Ashcroft Polls"
        assert plan.regions_mapping == ""
        assert plan.poll_exists is False
        assert plan.poll_id is None

    def test_existing_pollster_without_a_matching_poll_is_flagged(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Named unlike the "Lord Ashcroft Polls" fallback default, so a
        # mutation that always returns the default name instead of the real
        # pollster's cannot pass this assertion by coincidence.
        world = westminster_world
        pollster = db.add_pollster("Ashcroft Ltd", "lord_ashcroft_test", weight=1.0)
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )

        plan = build_import_plan(
            db,
            map_name=world.map_name,
            source_url="https://x.test/a.xls",
            pollster_identifier="lord_ashcroft_test",
        )

        assert plan.pollster_exists is True
        assert plan.pollster_id == pollster.id
        assert plan.pollster_name == "Ashcroft Ltd"
        assert plan.poll_exists is False
        assert plan.poll_id is None

    def test_existing_poll_matching_parsed_metadata_is_flagged(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Ashcroft Ltd", "lord_ashcroft_test", weight=1.0)
        existing = db.add_poll(
            pollster.id,
            world.map_id,
            _PARSED_START,
            _PARSED_END,
            sample_size=_PARSED_SAMPLE,
        )
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )

        plan = build_import_plan(
            db,
            map_name=world.map_name,
            source_url="https://x.test/a.xls",
            pollster_identifier="lord_ashcroft_test",
        )

        assert plan.poll_exists is True
        assert plan.poll_id == existing.id

    def test_resolve_xls_url_receives_the_cli_source_url_and_parse_the_resolved_url(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        original_url = "https://distinct-non-default.test/page/"
        resolved_url = "https://x.test/resolved.xls"
        resolved_with: list[str] = []
        parsed_with: list[str] = []

        def _fake_resolve(url: str) -> str:
            resolved_with.append(url)
            return resolved_url

        def _fake_parse(url: str) -> ParsedPoll:
            parsed_with.append(url)
            return _parsed_poll(party_percentages={"Labour": 30.0})

        monkeypatch.setattr(lord_ashcroft_import, "_resolve_xls_url", _fake_resolve)
        monkeypatch.setattr(
            lord_ashcroft_import, "parse_poll_from_xls_url", _fake_parse
        )

        plan = build_import_plan(db, map_name=world.map_name, source_url=original_url)

        # _resolve_xls_url gets the original CLI URL; parse_poll_from_xls_url
        # gets what _resolve_xls_url returned, not the original — the two
        # differ here so a fake that ignored its argument, or a swap between
        # the two URLs, would be caught.
        assert resolved_with == [original_url]
        assert parsed_with == [resolved_url]
        # The plan itself records the original URL (what a user gave), not
        # the resolved XLS download URL.
        assert plan.source_url == original_url


# ── _cli_preview ─────────────────────────────────────────────────────────────


def _plan_for_preview(
    *, pollster_exists: bool, poll_id: int | None, rows: list[PlannedPollRow]
) -> ImportPlan:
    return ImportPlan(
        pollster_identifier="lord_ashcroft",
        pollster_name="Lord Ashcroft Polls",
        pollster_id=(7 if pollster_exists else None),
        pollster_exists=pollster_exists,
        regions_mapping="",
        map_id=1,
        map_name="UK Constituencies post 2022",
        source_url="https://x.test/a.xls",
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

        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "fieldwork=2026-01-03 to 2026-01-05, sample=1503" in out
        # Exact-line checks: "would create poll" is a literal prefix of the
        # "would create pollster: ..." line above it, so a plain substring
        # check on either line could pass on the other line's text alone.
        assert "[dry-run] would create pollster: lord_ashcroft" in lines
        assert "[dry-run] would create poll" in lines
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
        assert "pollster exists: lord_ashcroft" in out
        assert "poll exists: 42" in out
        assert "would create pollster" not in out
        assert "would create poll" not in out

    def test_prints_one_line_per_row(self, capsys: pytest.CaptureFixture[str]) -> None:
        rows = [
            PlannedPollRow(
                party_id=1,
                party_name="Labour",
                region_id=n,
                region_name=f"Region {n}",
                percentage=1.0,
            )
            for n in range(3)
        ]
        plan = _plan_for_preview(pollster_exists=False, poll_id=None, rows=rows)

        _cli_preview(plan)

        out = capsys.readouterr().out
        assert out.count("[dry-run] would insert row:") == 3
        assert "Region 0" in out
        assert "Region 2" in out


# ── main ─────────────────────────────────────────────────────────────────────


class TestMain:
    """Tests for main — argument parsing, dry-run preview and commit."""

    def test_map_name_argument_is_forwarded_not_silently_defaulted(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--map-name really reaches build_import_plan, not just the CLI.

        westminster_world's only seeded map is named exactly
        DEFAULT_MAP_NAME, so every other TestMain test below passes
        ``--map-name world.map_name`` — a value equal to the module's own
        fallback default. Those tests can't tell "the CLI value was
        forwarded" from "main() silently ignored it and fell back to its
        own default". Passing a distinct, nonexistent name here and
        asserting the resulting "Map not found" error proves the CLI value
        genuinely reaches build_import_plan instead.
        """
        assert westminster_world.map_name != "Some Distinct Nonexistent Map"
        monkeypatch.setattr(lord_ashcroft_import, "Database", lambda: db)
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "lord_ashcroft_import.py",
                "--source-url",
                "https://x.test/a.xls",
                "--map-name",
                "Some Distinct Nonexistent Map",
                "--dry-run",
            ],
        )

        with pytest.raises(
            ValueError, match="Map not found: 'Some Distinct Nonexistent Map'"
        ):
            lord_ashcroft_import.main()

    def test_dry_run_prints_preview_and_writes_nothing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        monkeypatch.setattr(lord_ashcroft_import, "Database", lambda: db)
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "lord_ashcroft_import.py",
                "--source-url",
                "https://x.test/a.xls",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "lord_ashcroft_test",
                "--dry-run",
            ],
        )

        lord_ashcroft_import.main()

        out = capsys.readouterr().out
        assert "Fetching source: https://x.test/a.xls" in out
        assert "[dry-run] would create pollster: lord_ashcroft_test" in out
        assert db.get_pollster_by_identifier("lord_ashcroft_test") is None

    def test_commit_creates_pollster_and_poll_and_forwards_a_non_default_url(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        resolved_with: list[str] = []
        non_default_url = "https://distinct-non-default.test/a.xls"
        # Deliberately distinct from non_default_url (not returned
        # unchanged): build_import_plan stores the ORIGINAL source_url on
        # the ImportPlan/Poll, not the resolved XLS URL, and if
        # _fake_resolve returned its input unchanged the two values would
        # be indistinguishable, so a mutation that stored the resolved URL
        # instead of the original one would pass undetected.
        resolved_url = "https://cdn.test/resolved-different-from-source.xls"

        def _fake_resolve(url: str) -> str:
            resolved_with.append(url)
            return resolved_url

        monkeypatch.setattr(lord_ashcroft_import, "Database", lambda: db)
        monkeypatch.setattr(lord_ashcroft_import, "_resolve_xls_url", _fake_resolve)
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "lord_ashcroft_import.py",
                "--source-url",
                non_default_url,
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "lord_ashcroft_test",
            ],
        )

        lord_ashcroft_import.main()

        out = capsys.readouterr().out
        assert db.get_pollster_by_identifier("lord_ashcroft_test") is not None
        polls = db.get_polls_for_map(world.map_id)
        assert len(polls) == 1
        lines = out.splitlines()
        assert "created pollster: lord_ashcroft_test" in lines
        assert f"created poll: {polls[0].id}" in lines
        assert "inserted poll rows: 1" in lines
        assert resolved_with == [non_default_url]
        # The stored source_url is the original CLI URL, not the resolved
        # XLS URL (which real Lord Ashcroft polls never store — see the
        # module's build_import_plan, which passes source_url through
        # unchanged and only uses resolved_xls_url to fetch/parse).
        assert polls[0].source_url == non_default_url
        assert polls[0].source_url != resolved_url

    def test_commit_skips_existing_rows_without_replace_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Ashcroft Ltd", "lord_ashcroft_test", weight=1.0)
        poll = db.add_poll(
            pollster.id,
            world.map_id,
            _PARSED_START,
            _PARSED_END,
            sample_size=_PARSED_SAMPLE,
        )
        db.add_poll_row(poll.id, world.party_ids["Labour"], 25.0)
        monkeypatch.setattr(lord_ashcroft_import, "Database", lambda: db)
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "lord_ashcroft_import.py",
                "--source-url",
                "https://x.test/a.xls",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "lord_ashcroft_test",
            ],
        )

        lord_ashcroft_import.main()

        out = capsys.readouterr().out
        assert "pollster exists: lord_ashcroft_test" in out
        assert f"poll exists: {poll.id}" in out
        assert (
            f"poll {poll.id} already has rows; use --replace-rows to overwrite" in out
        )
        assert len(db.get_rows_for_poll(poll.id)) == 1

    def test_replace_rows_deletes_then_inserts(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("Ashcroft Ltd", "lord_ashcroft_test", weight=1.0)
        poll = db.add_poll(
            pollster.id,
            world.map_id,
            _PARSED_START,
            _PARSED_END,
            sample_size=_PARSED_SAMPLE,
        )
        db.add_poll_row(poll.id, world.party_ids["Labour"], 25.0)
        monkeypatch.setattr(lord_ashcroft_import, "Database", lambda: db)
        monkeypatch.setattr(
            lord_ashcroft_import, "_resolve_xls_url", lambda url: "https://x.test/a.xls"
        )
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "lord_ashcroft_import.py",
                "--source-url",
                "https://x.test/a.xls",
                "--map-name",
                world.map_name,
                "--pollster-identifier",
                "lord_ashcroft_test",
                "--replace-rows",
            ],
        )

        lord_ashcroft_import.main()

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
        not against lord_ashcroft_import.DEFAULT_MAP_NAME itself, so a
        mutation to either side would be caught. The source URL and pollster
        identifier are asserted against literals for the same reason:
        comparing against lord_ashcroft_import.DEFAULT_SOURCE_URL /
        DEFAULT_POLLSTER_IDENTIFIER would just compare the code under test
        with itself. _resolve_xls_url is faked (its real behaviour is
        covered by TestResolveXlsUrl) so this test can assert the exact
        source URL it receives without a network call.
        """
        assert westminster_world.map_name == "UK Constituencies post 2022"
        resolved_with: list[str] = []

        def _fake_resolve(url: str) -> str:
            resolved_with.append(url)
            return "https://x.test/a.xls"

        monkeypatch.setattr(lord_ashcroft_import, "Database", lambda: db)
        monkeypatch.setattr(lord_ashcroft_import, "_resolve_xls_url", _fake_resolve)
        monkeypatch.setattr(
            lord_ashcroft_import,
            "parse_poll_from_xls_url",
            lambda _url: _parsed_poll(party_percentages={"Labour": 30.0}),
        )
        monkeypatch.setattr("sys.argv", ["lord_ashcroft_import.py", "--dry-run"])

        lord_ashcroft_import.main()

        out = capsys.readouterr().out
        assert (
            "Fetching source: https://lordashcroftpolls.com/2026/01/"
            "kemis-been-tough-and-jenricks-a-bit-of-a-bounder-say-tory-voters"
            "-but-plenty-agree-with-him-that-britain-is-broken/" in out
        )
        assert resolved_with == [
            "https://lordashcroftpolls.com/2026/01/kemis-been-tough-and-jenricks"
            "-a-bit-of-a-bounder-say-tory-voters-but-plenty-agree-with-him-that"
            "-britain-is-broken/"
        ]
        # Exact-line check: "would create poll" (printed on its own line
        # just below) is a literal prefix of this pollster line's text.
        assert "[dry-run] would create pollster: lord_ashcroft" in out.splitlines()
