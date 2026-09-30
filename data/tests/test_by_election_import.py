"""Unit tests for the by-election importer (``scripts/by_election_import.py``).

Parsing tests use synthetic Wikipedia HTML only. ``fetch_wikipedia_html``,
``build_import_plan``, ``commit_import_plan`` and ``main`` additionally use the
``db`` fixture and a monkeypatched ``urlopen`` — still no network access or the
live database. ``build_import_plan``/``main`` tests mostly use the
``westminster_world`` fixture: its map name ("UK Constituencies post 2022") and
baseline election name ("2024 General Election") are exactly this module's own
``DEFAULT_MAP_NAME``/``DEFAULT_PARENT_ELECTION_NAME``, so the seeded world
doubles as the parent-election/map lookup target with no extra setup. One test
(party resolution when a candidate's party is absent from the database) seeds
a smaller, purpose-built map locally instead, since ``westminster_world``
seeds every party any importer maps to and so can't produce that gap. A
couple of CLI-plumbing tests add an extra map or election alongside
``westminster_world`` (not in place of it), per the file's own needs.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest
from bs4 import BeautifulSoup, Tag

from db import Database
from models import ElectionType
from scripts import by_election_import
from scripts.by_election_import import (
    ByElectionImportPlan,
    DEFAULT_MAP_NAME,
    DEFAULT_PARENT_ELECTION_NAME,
    ParsedCandidate,
    _extract_party_from_row,
    _find_column,
    _map_party_name,
    _parse_date_text,
    build_import_plan,
    commit_import_plan,
    fetch_wikipedia_html,
    main,
    normalize_name,
    parse_constituency_name,
    parse_election_date,
    parse_election_name_from_title,
    parse_results_table,
)
from tests.uk_fixtures import FakeUrlResponse, WestminsterWorld

# ── Synthetic HTML helpers ────────────────────────────────────────────────────

_RESULTS_HEADER = "<tr><th>Party</th><th>Candidate</th><th>Votes</th><th>%</th></tr>"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _cells(row_html: str) -> list[Any]:
    """Return the ``<td>``/``<th>`` cells of a single synthetic table row."""
    soup = _soup(f"<table><tr>{row_html}</tr></table>")
    row = soup.find("tr")
    assert isinstance(row, Tag)
    return row.find_all(["td", "th"])


def _swatch_row(
    href: str, party: str, candidate: str, votes: str, pct: str, *, bold: bool = False
) -> str:
    """A results-table row with a leading colour-swatch cell, as real pages use."""
    candidate_html = f"<b>{candidate}</b>" if bold else candidate
    return (
        '<tr><td style="background-color:#000"></td>'
        f'<td><a href="{href}">{party}</a></td>'
        f"<td>{candidate_html}</td><td>{votes}</td><td>{pct}</td></tr>"
    )


# Labour (Jane Doe, bold => elected) beats Conservative (John Smith) 20,000 to
# 15,000 — matches the "Hexham" seat's real (Conservative-won) 2024 baseline in
# uk_fixtures, so the by-election flips it.
_HEXHAM_TABLE = _swatch_row(
    "/wiki/Labour_Party", "Labour", "Jane Doe", "20,000", "50.0", bold=True
) + _swatch_row("/wiki/Conservative_Party", "Conservative", "John Smith", "15,000", "37.5")


def _by_election_page(
    *,
    title: str = "2025 Hexham by-election - Wikipedia",
    infobox_date: str | None = "5 June 2025",
    table_rows: str = _HEXHAM_TABLE,
) -> str:
    """A full synthetic by-election page: title, infobox date, results wikitable."""
    infobox = ""
    if infobox_date is not None:
        infobox = (
            '<table class="infobox">'
            f"<tr><th>Date</th><td>{infobox_date}</td></tr>"
            "</table>"
        )
    return (
        f"<html><head><title>{title}</title></head><body>"
        f"{infobox}"
        f'<table class="wikitable">{_RESULTS_HEADER}{table_rows}</table>'
        "</body></html>"
    )


def _serve(monkeypatch: pytest.MonkeyPatch, html: str) -> None:
    """Monkeypatch this module's ``urlopen`` to serve ``html`` to any request."""
    monkeypatch.setattr(
        by_election_import,
        "urlopen",
        lambda *_a, **_k: FakeUrlResponse(html.encode("utf-8")),
    )


def _serve_recording(monkeypatch: pytest.MonkeyPatch, html: str) -> list[str]:
    """Like :func:`_serve`, but records each requested URL for CLI-plumbing checks."""
    requested: list[str] = []

    def fake_urlopen(req: Request, timeout: int = 15) -> FakeUrlResponse:
        requested.append(req.full_url)
        return FakeUrlResponse(html.encode("utf-8"))

    monkeypatch.setattr(by_election_import, "urlopen", fake_urlopen)
    return requested


def _seed_minimal_map(db: Database) -> tuple[int, int, int]:
    """Seed the default map with one 'Hexham' seat and only the Labour party.

    Used where a test needs a candidate's resolved party ('Conservative') to be
    genuinely absent from the database — the full ``westminster_world`` seeds
    every party any importer maps to, so it can't produce that gap.
    """
    map_row = db.add_map(DEFAULT_MAP_NAME, parliament="westminster")
    seat = db.add_seat(map_row.id, "Hexham")
    labour = db.add_party("Labour")
    return map_row.id, seat.id, labour.id


def _plan(
    *,
    seat_id: int | None,
    candidates: list[ParsedCandidate],
    election_name: str = "2025 Hexham by-election",
    constituency_name: str = "Hexham",
    election_date: date | None = date(2025, 6, 5),
    parent_election_id: int | None = None,
    parent_election_name: str = DEFAULT_PARENT_ELECTION_NAME,
    map_id: int = 1,
    seat_name_matched: str | None = "Hexham",
    party_id_by_name: dict[str, int] | None = None,
    url: str = "https://en.wikipedia.org/wiki/2025_Hexham_by-election",
) -> ByElectionImportPlan:
    """Build a ByElectionImportPlan directly, bypassing HTML parsing entirely.

    Used by the commit_import_plan tests, which exercise the write path in
    isolation from parsing (mirroring test_holyrood_import.py's ``_poll()``).
    """
    return ByElectionImportPlan(
        url=url,
        constituency_name=constituency_name,
        election_date=election_date,
        election_name=election_name,
        candidates=candidates,
        parent_election_id=parent_election_id,
        parent_election_name=parent_election_name,
        map_id=map_id,
        seat_id=seat_id,
        seat_name_matched=seat_name_matched,
        party_id_by_name=party_id_by_name or {},
    )


# ── normalize_name ────────────────────────────────────────────────────────────


class TestNormalizeName:
    """Tests for normalize_name — lowercase, &→and, strip non-alphanumerics."""

    def test_lowercases_and_strips_whitespace(self) -> None:
        assert normalize_name("Holborn and St Pancras") == "holbornandstpancras"

    def test_ampersand_becomes_and(self) -> None:
        assert normalize_name("Cynon Valley & Rhymney") == "cynonvalleyandrhymney"

    def test_strips_periods(self) -> None:
        assert normalize_name("St. Pancras") == "stpancras"

    def test_ampersand_and_plain_and_normalise_equal(self) -> None:
        assert normalize_name("Holborn & St. Pancras") == normalize_name(
            "Holborn and St Pancras"
        )

    def test_empty_string(self) -> None:
        assert normalize_name("") == ""


# ── fetch_wikipedia_html ───────────────────────────────────────────────────────


class TestFetchWikipediaHtml:
    """Tests for fetch_wikipedia_html — bot User-Agent header, UTF-8 decoding."""

    def test_sends_bot_user_agent_and_decodes_utf8(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[str, int]] = []

        def fake_urlopen(req: Request, timeout: int) -> FakeUrlResponse:
            seen.append((req.get_header("User-agent") or "", timeout))
            return FakeUrlResponse("<html>café result</html>".encode("utf-8"))

        monkeypatch.setattr(by_election_import, "urlopen", fake_urlopen)

        result = fetch_wikipedia_html("https://en.wikipedia.org/wiki/Example")

        assert result == "<html>café result</html>"
        # Spelled out as a literal: fetch_wikipedia_html writes this header
        # value inline (there is no module-level User-Agent constant), so a
        # typo there is caught directly rather than compared against itself.
        assert seen == [("ElectionMapsBot/1.0", 15)]


# ── _parse_date_text ───────────────────────────────────────────────────────────


class TestParseDateText:
    """Tests for _parse_date_text — UK format first, then US format, then None."""

    def test_uk_format(self) -> None:
        assert _parse_date_text("6 March 2025") == date(2025, 3, 6)

    def test_us_format_with_comma(self) -> None:
        assert _parse_date_text("March 6, 2025") == date(2025, 3, 6)

    def test_us_format_without_comma(self) -> None:
        assert _parse_date_text("March 6 2025") == date(2025, 3, 6)

    def test_uk_format_embedded_in_a_sentence(self) -> None:
        result = _parse_date_text("By-election held on 6 March 2025 (Thursday)")
        assert result == date(2025, 3, 6)

    def test_no_date_returns_none(self) -> None:
        assert _parse_date_text("Nonsense text") is None

    def test_unrecognised_month_name_returns_none(self) -> None:
        assert _parse_date_text("6 Blorpuary 2025") is None

    def test_us_format_with_unrecognised_month_returns_none(self) -> None:
        # The UK-format regex doesn't match here (no letters between the two
        # digit groups), so this reaches the US-format branch's own
        # `if month:` guard — unlike the UK-format case above, which fails
        # before ever reaching a month lookup in the US branch.
        assert _parse_date_text("Blorpuary 6, 2025") is None


# ── parse_election_date ─────────────────────────────────────────────────────────


class TestParseElectionDate:
    """Tests for parse_election_date — labelled th row, then bold-text fallback."""

    def test_labelled_date_row(self) -> None:
        soup = _soup(
            '<table class="infobox">'
            "<tr><th>Country</th><td>United Kingdom</td></tr>"
            "<tr><th>Date</th><td>1 May 2025</td></tr>"
            "</table>"
        )
        assert parse_election_date(soup) == date(2025, 5, 1)

    def test_bold_subheader_fallback_when_no_labelled_row(self) -> None:
        soup = _soup(
            '<table class="infobox">'
            '<tr class="infobox-subheader"><td colspan="2"><b>1 May 2025</b></td></tr>'
            "</table>"
        )
        assert parse_election_date(soup) == date(2025, 5, 1)

    def test_unparseable_labelled_row_falls_back_to_bold_text(self) -> None:
        soup = _soup(
            '<table class="infobox">'
            "<tr><th>Date</th><td>TBC</td></tr>"
            "<tr><td><b>1 May 2025</b></td></tr>"
            "</table>"
        )
        assert parse_election_date(soup) == date(2025, 5, 1)

    def test_labelled_date_row_without_a_td_falls_through_to_a_later_date_row(self) -> None:
        # The first "Date" th has no sibling td at all, and the SECOND row is
        # also a genuine labelled "Date" row with a real value — so a `break`
        # (or an early return) after the first row would also yield None here,
        # same as skipping it correctly would with no further rows to check.
        # Only a true `continue` reaches the second row and finds the date.
        soup = _soup(
            '<table class="infobox">'
            "<tr><th>Date</th></tr>"
            "<tr><th>Date</th><td>1 May 2025</td></tr>"
            "</table>"
        )
        assert parse_election_date(soup) == date(2025, 5, 1)

    def test_bold_text_that_fails_to_parse_falls_through_to_the_next_bold_tag(
        self,
    ) -> None:
        soup = _soup(
            '<table class="infobox">'
            "<tr><td><b>Not A Date</b></td></tr>"
            "<tr><td><b>1 May 2025</b></td></tr>"
            "</table>"
        )
        assert parse_election_date(soup) == date(2025, 5, 1)

    def test_no_infobox_returns_none(self) -> None:
        assert parse_election_date(_soup("<p>No infobox here.</p>")) is None

    def test_infobox_with_no_date_anywhere_returns_none(self) -> None:
        soup = _soup('<table class="infobox"><tr><th>Country</th><td>UK</td></tr></table>')
        assert parse_election_date(soup) is None


# ── parse_constituency_name ────────────────────────────────────────────────────


class TestParseConstituencyName:
    """Tests for parse_constituency_name — strips the year and by-election suffix."""

    def test_strips_year_and_suffix(self) -> None:
        soup = _soup("<title>2025 Runcorn and Helsby by-election - Wikipedia</title>")
        assert parse_constituency_name(soup) == "Runcorn and Helsby"

    def test_en_dash_wikipedia_suffix(self) -> None:
        soup = _soup("<title>2025 Hexham by-election – Wikipedia</title>")
        assert parse_constituency_name(soup) == "Hexham"

    def test_title_without_by_election_pattern_is_returned_whole(self) -> None:
        soup = _soup(
            "<title>Runcorn and Helsby (UK Parliament constituency) - Wikipedia</title>"
        )
        assert (
            parse_constituency_name(soup)
            == "Runcorn and Helsby (UK Parliament constituency)"
        )

    def test_no_title_tag_returns_unknown(self) -> None:
        assert parse_constituency_name(_soup("<p>No title.</p>")) == "Unknown"


# ── parse_election_name_from_title ────────────────────────────────────────────


class TestParseElectionNameFromTitle:
    """Tests for parse_election_name_from_title — full title minus the suffix."""

    def test_strips_wikipedia_suffix(self) -> None:
        soup = _soup("<title>2025 Runcorn and Helsby by-election - Wikipedia</title>")
        assert (
            parse_election_name_from_title(soup) == "2025 Runcorn and Helsby by-election"
        )

    def test_no_title_tag_returns_unknown_by_election(self) -> None:
        result = parse_election_name_from_title(_soup("<p>No title.</p>"))
        assert result == "Unknown by-election"


# ── _find_column ───────────────────────────────────────────────────────────────


class TestFindColumn:
    """Tests for _find_column — first matching HEADER wins, not first keyword."""

    def test_finds_first_matching_header_by_header_order_not_keyword_order(self) -> None:
        # "votes" is the 2nd keyword, but it matches the header at index 1; a
        # keyword-first mutant (try "candidate" everywhere, then "votes") would
        # instead return 2, where "candidate" matches. Index 1 proves the loop
        # iterates headers in order, not keywords.
        headers = ["dates", "votes cast", "candidate name"]
        assert _find_column(headers, ["candidate", "votes"]) == 1

    def test_no_match_returns_none(self) -> None:
        assert _find_column(["dates", "pollster"], ["party"]) is None

    def test_empty_headers_returns_none(self) -> None:
        assert _find_column([], ["party"]) is None


# ── _extract_party_from_row ────────────────────────────────────────────────────


class TestExtractPartyFromRow:
    """Tests for _extract_party_from_row — swatch fallback, link priority, n/a."""

    def test_party_col_none_returns_none(self) -> None:
        assert _extract_party_from_row(_cells("<td>Labour</td>"), None, ["party"]) is None

    def test_party_col_out_of_range_returns_none(self) -> None:
        cells = _cells("<td>Labour</td>")
        assert _extract_party_from_row(cells, 2, ["a", "b", "party"]) is None

    def test_plain_text_cell(self) -> None:
        cells = _cells("<td>Labour</td><td>Jane Doe</td>")
        assert _extract_party_from_row(cells, 0, ["party", "candidate"]) == "Labour"

    def test_short_swatch_cell_falls_back_to_the_next_cell(self) -> None:
        cells = _cells('<td style="background:red"></td><td>Labour</td>')
        assert _extract_party_from_row(cells, 0, ["party", "candidate"]) == "Labour"

    def test_one_character_non_empty_cell_also_falls_back(self) -> None:
        # Distinguishes the real guard (`len(text) <= 1`) from a narrower
        # `== 0` mutant: "X" has length 1, not 0, so only "<= 1" triggers the
        # fallback to the next cell here.
        cells = _cells("<td>X</td><td>Labour</td>")
        assert _extract_party_from_row(cells, 0, ["party", "candidate"]) == "Labour"

    def test_short_cell_with_no_next_cell_returns_the_short_text_verbatim(self) -> None:
        # Only one cell in the row, so the "party_col + 1 < len(cells)" fallback
        # guard can't fire; the 1-character text is returned as-is.
        assert _extract_party_from_row(_cells("<td>R</td>"), 0, ["party"]) == "R"

    def test_link_text_overrides_the_cells_own_text(self) -> None:
        cells = _cells('<td>see <a href="/wiki/Labour_Party">Labour</a></td>')
        assert _extract_party_from_row(cells, 0, ["party"]) == "Labour"

    def test_n_a_cell_returns_none(self) -> None:
        assert _extract_party_from_row(_cells("<td>N/A</td>"), 0, ["party"]) is None

    def test_empty_cell_with_empty_fallback_returns_none(self) -> None:
        cells = _cells("<td></td><td></td>")
        assert _extract_party_from_row(cells, 0, ["party", "candidate"]) is None


# ── _map_party_name ────────────────────────────────────────────────────────────


class TestMapPartyName:
    """Tests for _map_party_name — exact, then partial (either direction), then Others."""

    def test_exact_match_case_insensitive(self) -> None:
        assert _map_party_name("LABOUR") == "Labour"

    def test_exact_match_alias(self) -> None:
        assert _map_party_name("Reform") == "Reform UK"

    def test_partial_match_key_is_substring_of_the_raw_name(self) -> None:
        assert _map_party_name("Labour and Co-operative Party") == "Labour"

    def test_partial_match_raw_name_is_substring_of_a_key(self) -> None:
        assert _map_party_name("liberal dem") == "Liberal Democrats"

    def test_unrecognised_name_falls_back_to_others(self) -> None:
        assert _map_party_name("Yorkshire Party") == "Others"

    def test_independent_maps_to_others(self) -> None:
        assert _map_party_name("Independent") == "Others"


# ── parse_results_table ────────────────────────────────────────────────────────


class TestParseResultsTable:
    """Tests for parse_results_table — column detection, party source, row skips."""

    def test_swatch_column_offset_and_bold_marks_elected(self) -> None:
        soup = _soup(f'<table class="wikitable">{_RESULTS_HEADER}{_HEXHAM_TABLE}</table>')
        candidates = parse_results_table(soup)
        assert [c.party_name for c in candidates] == ["Labour", "Conservative"]
        assert candidates[0].candidate_name == "Jane Doe"
        assert candidates[0].votes == 20000
        assert candidates[0].elected is True
        assert candidates[1].elected is False

    def test_footer_row_with_fewer_cells_than_headers_is_skipped(self) -> None:
        # 3 cells against 4 headers gives row_offset = 3 - 4 = -1, so without
        # the guard eff_candidate_col becomes 0 and eff_votes_col becomes 1 —
        # both still valid (non-negative) indices, just pointing at the wrong
        # columns: cells[0] "Rejected ballots" read as the candidate name and
        # cells[1] "125" (a genuine digit string) read as the vote total,
        # producing a bogus third candidate. This isn't caught by the separate
        # "no digits in votes" skip, unlike a summary row whose only cells are
        # non-numeric text (e.g. "Majority" / "Turnout").
        footer = "<tr><td>Rejected ballots</td><td>125</td><td>0.3%</td></tr>"
        table = _HEXHAM_TABLE + footer
        soup = _soup(f'<table class="wikitable">{_RESULTS_HEADER}{table}</table>')
        candidates = parse_results_table(soup)
        assert [c.candidate_name for c in candidates] == ["Jane Doe", "John Smith"]

    def test_bold_candidate_cell_marks_elected_even_on_the_lower_vote_row(self) -> None:
        # Bold is on Jane Doe (20,000, higher), while the elected marker is on
        # John Smith (15,000, lower) — the max-votes fallback alone couldn't
        # produce this result, so only the bold-detection itself can.
        table = (
            "<tr><td>Labour</td><td>Jane Doe</td><td>20,000</td><td>50.0</td></tr>"
            "<tr><td>Conservative</td><td><b>John Smith</b></td><td>15,000</td><td>37.0</td></tr>"
        )
        soup = _soup(f'<table class="wikitable">{_RESULTS_HEADER}{table}</table>')
        candidates = parse_results_table(soup)
        elected = [c for c in candidates if c.elected]
        assert [c.candidate_name for c in elected] == ["John Smith"]

    @pytest.mark.parametrize("marker", ["✓", "✔", "Yes"], ids=["tick", "heavy_tick", "yes"])
    def test_marker_in_row_text_marks_elected_even_on_the_lower_vote_row(
        self, marker: str
    ) -> None:
        # Each marker is embedded in an EXISTING cell (the pct column for the
        # lower-vote row), not an extra cell, so the row/header cell counts
        # stay aligned and only the marker itself is under test.
        table = (
            "<tr><td>Labour</td><td>Jane Doe</td><td>20,000</td><td>50.0</td></tr>"
            f"<tr><td>Conservative</td><td>John Smith</td><td>15,000</td><td>{marker}</td></tr>"
        )
        soup = _soup(f'<table class="wikitable">{_RESULTS_HEADER}{table}</table>')
        candidates = parse_results_table(soup)
        elected = [c for c in candidates if c.elected]
        assert [c.candidate_name for c in elected] == ["John Smith"]

    def test_bold_on_the_party_cell_alone_does_not_mark_that_row_elected(self) -> None:
        # Bold is on the PARTY cell of the lower-vote row, not its candidate
        # cell. ``elected`` requires ``row.find("b")`` (bold anywhere in the
        # row) AND the candidate cell itself containing a "<b>" tag — a mutant
        # that dropped the second, narrower check would instead mark
        # Conservative (bold present somewhere in its row) elected here.
        table = (
            "<tr><td>Labour</td><td>Jane Doe</td><td>20,000</td><td>50.0</td></tr>"
            "<tr><td><b>Conservative</b></td><td>John Smith</td><td>15,000</td><td>37.0</td></tr>"
        )
        soup = _soup(f'<table class="wikitable">{_RESULTS_HEADER}{table}</table>')
        candidates = parse_results_table(soup)
        # Neither row's own bold-detection fires, so the max-votes fallback
        # applies instead, landing on Jane Doe (20,000) — not Conservative.
        elected = [c for c in candidates if c.elected]
        assert [c.candidate_name for c in elected] == ["Jane Doe"]

    def test_no_candidate_marked_elected_picks_the_highest_vote_total(self) -> None:
        # Conservative is listed first with fewer votes, Labour second with
        # more — the winner is neither the first row nor pre-marked, so only
        # the max-votes rule (not row order) can produce this result.
        table = (
            '<tr><td style="background:blue"></td><td>Conservative</td>'
            "<td>John Smith</td><td>15,000</td><td>33.0</td></tr>"
            '<tr><td style="background:red"></td><td>Labour</td>'
            "<td>Jane Doe</td><td>20,000</td><td>50.0</td></tr>"
        )
        soup = _soup(f'<table class="wikitable">{_RESULTS_HEADER}{table}</table>')
        candidates = parse_results_table(soup)
        elected = [c for c in candidates if c.elected]
        assert [c.candidate_name for c in elected] == ["Jane Doe"]

    def test_row_with_both_party_cell_and_fallback_cell_empty_is_skipped(self) -> None:
        table = (
            "<tr><td></td><td></td><td>20,000</td><td>50.0</td></tr>"
            "<tr><td>Labour</td><td>Jane Doe</td><td>15,000</td><td>33.0</td></tr>"
        )
        soup = _soup(f'<table class="wikitable">{_RESULTS_HEADER}{table}</table>')
        candidates = parse_results_table(soup)
        assert [c.candidate_name for c in candidates] == ["Jane Doe"]

    def test_row_with_no_digits_in_the_votes_cell_is_skipped(self) -> None:
        table = (
            "<tr><td>Labour</td><td>Jane Doe</td><td>n/a</td><td>0.0</td></tr>"
            "<tr><td>Conservative</td><td>John Smith</td><td>15,000</td><td>33.0</td></tr>"
        )
        soup = _soup(f'<table class="wikitable">{_RESULTS_HEADER}{table}</table>')
        candidates = parse_results_table(soup)
        assert [c.candidate_name for c in candidates] == ["John Smith"]

    def test_first_table_with_neither_candidate_nor_votes_header_is_skipped(self) -> None:
        first_table = (
            '<table class="wikitable"><tr><th>Year</th><th>Turnout</th></tr>'
            "<tr><td>2024</td><td>60%</td></tr></table>"
        )
        second_table = (
            f'<table class="wikitable">{_RESULTS_HEADER}'
            "<tr><td>Labour</td><td>Jane Doe</td><td>20,000</td><td>50.0</td></tr></table>"
        )
        soup = _soup(f"<body>{first_table}{second_table}</body>")
        candidates = parse_results_table(soup)
        assert [c.candidate_name for c in candidates] == ["Jane Doe"]

    @pytest.mark.parametrize(
        "decoy_header",
        [
            "<tr><th>Party</th><th>Votes</th><th>%</th></tr>",
            "<tr><th>Party</th><th>Candidate</th><th>Notes</th></tr>",
        ],
        ids=["has_party_missing_candidate", "has_party_missing_votes"],
    )
    def test_near_miss_table_with_a_party_column_but_missing_one_required_header_is_skipped(
        self, decoy_header: str
    ) -> None:
        # Unlike the "neither header" decoy above, each of these HAS a Party
        # column (so it isn't rejected by that alone) but is still missing
        # either "candidate" or "votes" — proving has_candidate and has_votes
        # are each independently required, not just "has_votes or has_candidate".
        decoy = (
            f'<table class="wikitable">{decoy_header}'
            "<tr><td>Green</td><td>9,999</td><td>1</td></tr></table>"
        )
        real = (
            f'<table class="wikitable">{_RESULTS_HEADER}'
            "<tr><td>Labour</td><td>Jane Doe</td><td>20,000</td><td>50.0</td></tr></table>"
        )
        soup = _soup(f"<body>{decoy}{real}</body>")
        candidates = parse_results_table(soup)
        assert [c.candidate_name for c in candidates] == ["Jane Doe"]

    def test_second_full_results_table_on_the_page_is_ignored(self) -> None:
        # Real by-election pages routinely carry a second, fully-formed
        # results table further down (the previous general election's) — the
        # 2014 Clacton by-election page is a real example: its FIRST wikitable
        # is the 2010 general election result (Carswell/Conservative/22,867),
        # confirmed by fetching the live page, so re-running this importer on
        # it today would silently return that wrong result if the `if
        # candidates: break` guard here were ever dropped. Both tables below
        # have full Party/Candidate/Votes headers, so only that guard (not a
        # missing-header check) can make the first one win.
        first_table = (
            f'<table class="wikitable">{_RESULTS_HEADER}'
            "<tr><td>Labour</td><td>Jane Doe</td><td>20,000</td><td>50.0</td></tr></table>"
        )
        second_table = (
            f'<table class="wikitable">{_RESULTS_HEADER}'
            "<tr><td>Conservative</td><td>Old MP</td><td>30,000</td><td>60.0</td></tr></table>"
        )
        soup = _soup(f"<body>{first_table}{second_table}</body>")
        candidates = parse_results_table(soup)
        assert [c.candidate_name for c in candidates] == ["Jane Doe"]

    def test_no_wikitable_matches_returns_empty_list(self) -> None:
        soup = _soup(
            '<table class="wikitable"><tr><th>Foo</th></tr><tr><td>Bar</td></tr></table>'
        )
        assert parse_results_table(soup) == []

    def test_no_party_header_at_all_yields_no_candidates(self) -> None:
        table = (
            '<table class="wikitable"><tr><th>Candidate</th><th>Votes</th></tr>'
            "<tr><td>Jane Doe</td><td>20,000</td></tr></table>"
        )
        assert parse_results_table(_soup(table)) == []

    def test_singular_vote_header_is_not_recognised_as_the_votes_column_pins_current_behaviour(
        self,
    ) -> None:
        """``_find_column`` only searches for "votes" (plural), while the table
        selection filter accepts "vote" as a substring too (``has_votes``). A
        table headed "Party | Candidate | Vote" (singular) is selected, but the
        votes column is then never found, so every row's vote total silently
        becomes 0 instead of being skipped or raising. Not seen on a real page
        (a live Wikipedia by-election page checked separately uses "Votes",
        plural) — pinned as a latent mechanism, not a confirmed live bug.
        """
        table = (
            '<table class="wikitable">'
            "<tr><th>Party</th><th>Candidate</th><th>Vote</th></tr>"
            "<tr><td>Labour</td><td>Jane Doe</td><td>20,000</td></tr></table>"
        )
        candidates = parse_results_table(_soup(table))
        assert len(candidates) == 1
        assert candidates[0].votes == 0


# ── build_import_plan ──────────────────────────────────────────────────────────


class TestBuildImportPlan:
    """Tests for build_import_plan — fetch/parse orchestration and DB resolution."""

    def test_full_plan_against_the_seeded_westminster_world(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = westminster_world
        _serve(monkeypatch, _by_election_page())

        plan = build_import_plan(
            db, url="https://en.wikipedia.org/wiki/2025_Hexham_by-election"
        )

        assert plan.constituency_name == "Hexham"
        assert plan.election_date == date(2025, 6, 5)
        assert plan.election_name == "2025 Hexham by-election"
        assert plan.parent_election_id == world.baseline_election_id
        assert plan.map_id == world.map_id
        assert plan.seat_id == world.seat_ids["Hexham"]
        assert plan.seat_name_matched == "Hexham"
        assert plan.party_id_by_name == {
            "Labour": world.party_ids["Labour"],
            "Conservative": world.party_ids["Conservative"],
        }

    def test_seat_matched_by_normalised_name_despite_punctuation(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = westminster_world
        page = _by_election_page(
            title="2025 Holborn & St. Pancras by-election - Wikipedia",
            table_rows=_swatch_row(
                "/wiki/Labour_Party", "Labour", "Jane Doe", "20,000", "50.0", bold=True
            ),
        )
        _serve(monkeypatch, page)

        plan = build_import_plan(db, url="https://en.wikipedia.org/wiki/x")

        # The page title's raw text differs from the seat's DB spelling only
        # in punctuation (an ampersand for "and", a period after "St") — only
        # normalize_name matching, not an exact string compare, resolves this.
        assert plan.constituency_name == "Holborn & St. Pancras"
        assert plan.seat_id == world.seat_ids["Holborn and St Pancras"]
        assert plan.seat_name_matched == "Holborn and St Pancras"

    def test_constituency_not_matching_any_seat_leaves_seat_id_none(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = westminster_world
        page = _by_election_page(title="2025 Nonexistent Constituency by-election - Wikipedia")
        _serve(monkeypatch, page)

        plan = build_import_plan(db, url="https://en.wikipedia.org/wiki/x")

        assert plan.constituency_name == "Nonexistent Constituency"
        assert plan.seat_id is None
        assert plan.seat_name_matched is None
        # The map itself was found, unlike the map-missing case below.
        assert plan.map_id == world.map_id

    def test_map_missing_leaves_map_id_zero_and_no_seat_matched(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing map does not raise here: map_id degrades to 0 and the
        seat-matching loop is skipped entirely (it only runs when the map row
        is found), so seat_id and seat_name_matched stay None. The eventual
        ValueError instead comes from commit_import_plan's "no seat matched"
        guard, whose message says nothing about the map.
        """
        _serve(monkeypatch, _by_election_page())

        plan = build_import_plan(
            db,
            url="https://en.wikipedia.org/wiki/2025_Hexham_by-election",
            map_name="No Such Map",
        )

        assert plan.map_id == 0
        assert plan.seat_id is None
        assert plan.seat_name_matched is None

    def test_parent_election_missing_leaves_parent_election_id_none(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing parent election does not raise either: parent_election_id
        simply stays None, the same "degrade rather than raise" shape as the
        missing-map case above.
        """
        _serve(monkeypatch, _by_election_page())

        plan = build_import_plan(
            db,
            url="https://en.wikipedia.org/wiki/2025_Hexham_by-election",
            parent_election_name="No Such Parent Election",
        )

        assert plan.parent_election_id is None

    def test_party_resolution_only_includes_parties_present_in_the_database(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        map_id, seat_id, labour_id = _seed_minimal_map(db)
        # Only Labour is seeded; Conservative is a real candidate but absent
        # from this DB, so it must be left out of party_id_by_name (not raise,
        # not default to some other id).
        _serve(monkeypatch, _by_election_page())

        plan = build_import_plan(
            db, url="https://en.wikipedia.org/wiki/2025_Hexham_by-election"
        )

        assert plan.seat_id == seat_id
        assert plan.map_id == map_id
        assert plan.party_id_by_name == {"Labour": labour_id}
        assert "Conservative" not in plan.party_id_by_name

    def test_url_is_recorded_on_the_plan_and_reaches_the_fetch(
        self, db: Database, westminster_world: WestminsterWorld, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target_url = "https://en.wikipedia.org/wiki/2025_Hexham_by-election_custom"
        requested = _serve_recording(monkeypatch, _by_election_page())

        plan = build_import_plan(db, url=target_url)

        assert requested == [target_url]
        assert plan.url == target_url


# ── commit_import_plan ─────────────────────────────────────────────────────────


class TestCommitImportPlan:
    """Tests for commit_import_plan — the by-election write path."""

    def test_no_seat_matched_raises(self, db: Database) -> None:
        plan = _plan(seat_id=None, candidates=[], seat_name_matched=None)
        with pytest.raises(
            ValueError, match=re.escape("No seat matched for constituency 'Hexham'")
        ):
            commit_import_plan(db, plan)

    def test_no_candidates_raises(self, db: Database, westminster_world: WestminsterWorld) -> None:
        world = westminster_world
        plan = _plan(seat_id=world.seat_ids["Hexham"], map_id=world.map_id, candidates=[])

        with pytest.raises(ValueError, match="No candidates parsed from Wikipedia page"):
            commit_import_plan(db, plan)

    def test_existing_election_without_refresh_raises(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        candidates = [
            ParsedCandidate(
                party_name="Labour", candidate_name="Jane Doe", votes=20000, elected=True
            ),
        ]
        plan = _plan(
            seat_id=world.seat_ids["Hexham"],
            map_id=world.map_id,
            candidates=candidates,
            party_id_by_name={"Labour": world.party_ids["Labour"]},
        )
        first = commit_import_plan(db, plan)

        with pytest.raises(
            ValueError,
            match=re.escape(
                f"Election '2025 Hexham by-election' already exists (id={first.election_id})"
            ),
        ):
            commit_import_plan(db, plan, refresh=False)

    def test_refresh_clears_votes_and_keeps_the_election_id(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        first_candidates = [
            ParsedCandidate(
                party_name="Labour", candidate_name="Jane Doe", votes=20000, elected=True
            ),
            ParsedCandidate(
                party_name="Conservative", candidate_name="John Smith", votes=15000, elected=False
            ),
        ]
        party_ids = {
            "Labour": world.party_ids["Labour"],
            "Conservative": world.party_ids["Conservative"],
        }
        plan = _plan(
            seat_id=world.seat_ids["Hexham"],
            map_id=world.map_id,
            candidates=first_candidates,
            party_id_by_name=party_ids,
        )
        first = commit_import_plan(db, plan)

        refreshed_candidates = [
            ParsedCandidate(
                party_name="Labour", candidate_name="Jane Doe", votes=21000, elected=True
            ),
            ParsedCandidate(
                party_name="Reform UK", candidate_name="Alex Jones", votes=1000, elected=False
            ),
        ]
        refresh_plan = _plan(
            seat_id=world.seat_ids["Hexham"],
            map_id=world.map_id,
            candidates=refreshed_candidates,
            party_id_by_name={
                "Labour": world.party_ids["Labour"],
                "Reform UK": world.party_ids["Reform UK"],
            },
        )

        second = commit_import_plan(db, refresh_plan, refresh=True)

        assert second.election_id == first.election_id
        assert second.votes_inserted == 2
        out = capsys.readouterr().out.splitlines()
        assert (
            "Refreshing '2025 Hexham by-election': cleared 2 existing votes." in out
        )
        votes = db.get_votes_for_election(second.election_id)
        # Full payload, not just candidate names: proves seat_id, party_id and
        # vote_total on the refreshed rows, and their order (descending by
        # vote_total within the one seat, per get_votes_for_election).
        assert [(v.seat_id, v.party_id, v.vote_total, v.candidate_name) for v in votes] == [
            (world.seat_ids["Hexham"], world.party_ids["Labour"], 21000.0, "Jane Doe"),
            (world.seat_ids["Hexham"], world.party_ids["Reform UK"], 1000.0, "Alex Jones"),
        ]

    def test_new_election_has_the_by_election_type_and_the_winner_is_elected(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        candidates = [
            # The elected candidate has FEWER votes than the other one, proving
            # commit_import_plan passes ``elected`` through unchanged rather
            # than recomputing a winner from vote_total itself.
            ParsedCandidate(
                party_name="Labour", candidate_name="Jane Doe", votes=9000, elected=True
            ),
            ParsedCandidate(
                party_name="Conservative", candidate_name="John Smith", votes=15000, elected=False
            ),
        ]
        # A non-2025 date: _plan's own default (and the fallback-year test
        # below) both land on 2025, so a mutant that hardcoded year=2025
        # instead of reading plan.election_date.year would pass either of
        # those alone. 2023 disambiguates the two.
        plan = _plan(
            seat_id=world.seat_ids["Hexham"],
            map_id=world.map_id,
            candidates=candidates,
            election_date=date(2023, 7, 20),
            parent_election_id=world.baseline_election_id,
            party_id_by_name={
                "Labour": world.party_ids["Labour"],
                "Conservative": world.party_ids["Conservative"],
            },
        )

        result = commit_import_plan(db, plan)

        assert result.seat_name == "Hexham"
        assert result.votes_inserted == 2
        election = db.get_election(result.election_id)
        assert election is not None
        assert election.type == ElectionType.by_election
        assert election.map_id == world.map_id
        assert election.parent_election_id == world.baseline_election_id
        assert election.year == 2023
        assert election.election_date == date(2023, 7, 20)
        votes = db.get_votes_for_election(result.election_id)
        # Full payload (seat_id, party_id, vote_total, candidate_name), not
        # just which candidate ended up elected: proves each row was written
        # with the right seat/party/total, not e.g. a null party_id or a
        # zeroed vote_total. Order is descending by vote_total within the one
        # seat, per get_votes_for_election's own ordering.
        assert [(v.seat_id, v.party_id, v.vote_total, v.candidate_name) for v in votes] == [
            (world.seat_ids["Hexham"], world.party_ids["Conservative"], 15000.0, "John Smith"),
            (world.seat_ids["Hexham"], world.party_ids["Labour"], 9000.0, "Jane Doe"),
        ]
        elected_votes = [v for v in votes if v.elected]
        assert [v.candidate_name for v in elected_votes] == ["Jane Doe"]

    def test_no_election_date_falls_back_to_year_2025_pins_current_behaviour(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        """Not a rare edge case: a real check of pre-2026 Wikipedia by-election
        pages found parse_election_date returns None on roughly half of them
        (infobox date text glued together with no whitespace, e.g.
        "2May2024(2024-05-02)", which neither UK- nor US-format regex
        recognises), so this fallback is hit often on real imports and can
        silently store the wrong year.
        """
        world = westminster_world
        plan = _plan(
            seat_id=world.seat_ids["Hexham"],
            map_id=world.map_id,
            candidates=[
                ParsedCandidate(
                    party_name="Labour", candidate_name="Jane Doe", votes=100, elected=True
                )
            ],
            election_name="No Date By-election",
            election_date=None,
            party_id_by_name={"Labour": world.party_ids["Labour"]},
        )

        result = commit_import_plan(db, plan)

        election = db.get_election(result.election_id)
        assert election is not None
        assert election.year == 2025
        assert election.election_date is None

    def test_candidate_with_an_unresolved_party_gets_a_null_party_id(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        plan = _plan(
            seat_id=world.seat_ids["Hexham"],
            map_id=world.map_id,
            candidates=[
                ParsedCandidate(
                    party_name="Workers Party", candidate_name="X", votes=100, elected=False
                )
            ],
            election_name="Unmapped Party By-election",
            party_id_by_name={},
        )

        result = commit_import_plan(db, plan)

        votes = db.get_votes_for_election(result.election_id)
        assert len(votes) == 1
        assert votes[0].party_id is None
        assert votes[0].candidate_name == "X"


# ── main ─────────────────────────────────────────────────────────────────────


class TestMain:
    """Tests for main — the CLI entry point."""

    def test_dry_run_preview_prints_the_plan_and_writes_nothing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        _serve(monkeypatch, _by_election_page())
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "by_election_import.py",
                "--url",
                "https://en.wikipedia.org/wiki/2025_Hexham_by-election",
                "--dry-run",
            ],
        )

        main()

        out = capsys.readouterr().out.splitlines()
        assert "Constituency: Hexham" in out
        assert "Election: 2025 Hexham by-election" in out
        assert "Date: 2025-06-05" in out
        assert "Seat match: Hexham" in out
        assert f"Parent election ID: {world.baseline_election_id}" in out
        assert "Candidates: 2" in out
        assert (
            f"  Labour (id={world.party_ids['Labour']}): Jane Doe - 20,000 *" in out
        )
        assert (
            f"  Conservative (id={world.party_ids['Conservative']}): John Smith - 15,000"
            in out
        )
        assert "Dry run — no database writes." in out
        assert db.get_election_by_name("2025 Hexham by-election") is None

    def test_commit_creates_the_election_and_prints_the_summary(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _serve(monkeypatch, _by_election_page())
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "by_election_import.py",
                "--url",
                "https://en.wikipedia.org/wiki/2025_Hexham_by-election",
            ],
        )

        main()

        election = db.get_election_by_name("2025 Hexham by-election")
        assert election is not None
        out = capsys.readouterr().out.splitlines()
        assert f"Imported: election #{election.id}, 2 votes" in out
        assert "Dry run — no database writes." not in out

    def test_refresh_flag_reuses_the_election_id_and_prints_the_refresh_line(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _serve(monkeypatch, _by_election_page())
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        argv = [
            "by_election_import.py",
            "--url",
            "https://en.wikipedia.org/wiki/2025_Hexham_by-election",
        ]
        monkeypatch.setattr(sys, "argv", argv)
        main()
        first = db.get_election_by_name("2025 Hexham by-election")
        assert first is not None
        capsys.readouterr()

        monkeypatch.setattr(sys, "argv", [*argv, "--refresh"])
        main()

        second = db.get_election_by_name("2025 Hexham by-election")
        assert second is not None
        assert second.id == first.id
        out = capsys.readouterr().out.splitlines()
        assert (
            "Refreshing '2025 Hexham by-election': cleared 2 existing votes." in out
        )

    def test_rerun_without_refresh_still_raises(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Companion to the --refresh test above: proves main() only suppresses
        # the "already exists" raise when --refresh is actually passed, not
        # that it always passes refresh=True to commit_import_plan.
        _serve(monkeypatch, _by_election_page())
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        argv = [
            "by_election_import.py",
            "--url",
            "https://en.wikipedia.org/wiki/2025_Hexham_by-election",
        ]
        monkeypatch.setattr(sys, "argv", argv)
        main()

        with pytest.raises(
            ValueError,
            match=re.escape("Election '2025 Hexham by-election' already exists"),
        ):
            main()

    def test_unmatched_constituency_raises_and_writes_nothing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = _by_election_page(title="2025 Nonexistent Constituency by-election - Wikipedia")
        _serve(monkeypatch, page)
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        monkeypatch.setattr(
            sys,
            "argv",
            ["by_election_import.py", "--url", "https://en.wikipedia.org/wiki/x"],
        )

        with pytest.raises(
            ValueError,
            match=re.escape("No seat matched for constituency 'Nonexistent Constituency'"),
        ):
            main()
        assert db.get_election_by_name("2025 Nonexistent Constituency by-election") is None

    def test_parent_election_flag_is_forwarded_not_the_default(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        # A second election, distinct from world.baseline_election_id (which
        # equals this module's own DEFAULT_PARENT_ELECTION_NAME) — passing its
        # name explicitly must resolve to IT, not silently fall back to the
        # default parent, which would give the wrong (but still non-null) id.
        alt_parent = db.add_election(
            world.map_id, 2019, "2019 General Election", ElectionType.uk_general
        )
        _serve(monkeypatch, _by_election_page())
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "by_election_import.py",
                "--url",
                "https://en.wikipedia.org/wiki/2025_Hexham_by-election",
                "--parent-election",
                "2019 General Election",
            ],
        )

        main()

        election = db.get_election_by_name("2025 Hexham by-election")
        assert election is not None
        assert election.parent_election_id == alt_parent.id
        assert election.parent_election_id != world.baseline_election_id

    def test_map_name_flag_is_forwarded_not_the_default(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # A seat that exists ONLY on this alt map, not on the seeded world's
        # (default) map. If --map-name were silently dropped in favour of the
        # default map, the seat lookup would fail ("NOT FOUND"); it succeeding
        # proves the flag reached build_import_plan.
        alt_map = db.add_map("Alt Map", parliament="westminster")
        db.add_seat(alt_map.id, "Nonexistent Constituency")
        page = _by_election_page(title="2025 Nonexistent Constituency by-election - Wikipedia")
        _serve(monkeypatch, page)
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "by_election_import.py",
                "--url",
                "https://en.wikipedia.org/wiki/x",
                "--map-name",
                "Alt Map",
                "--dry-run",
            ],
        )

        main()

        out = capsys.readouterr().out.splitlines()
        assert "Seat match: Nonexistent Constituency" in out
        assert "Seat match: NOT FOUND" not in out

    def test_url_flag_reaches_the_fetch_not_a_default(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target_url = "https://en.wikipedia.org/wiki/2025_Hexham_by-election_custom"
        requested = _serve_recording(monkeypatch, _by_election_page())
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        monkeypatch.setattr(
            sys, "argv", ["by_election_import.py", "--url", target_url, "--dry-run"]
        )

        main()

        assert requested == [target_url]
