"""Unit tests for the Holyrood Wikipedia poll importer.

All tests use synthetic HTML — no network access or database required.
"""

from __future__ import annotations

from datetime import date

import pytest

from polls.importers.holyrood.holyrood_wikipedia_import import (
    ParsedScottishPoll,
    identify_party_columns,
    parse_polls as parse_constituency_polls,
    parse_date_range,
)

# ── Synthetic HTML helpers ────────────────────────────────────────────────────

_TABLE_HEADER = """
<tr>
  <th>Dates conducted</th>
  <th>Polling firm</th>
  <th>Sample size</th>
  <th>Con</th>
  <th>Lab</th>
  <th>LD</th>
  <th>SNP</th>
  <th>Green</th>
  <th>Alba</th>
  <th>Lead</th>
</tr>
"""

_TABLE_ROW_BASIC = """
<tr>
  <td>1–3 Feb 2026</td>
  <td>Savanta</td>
  <td>1,054</td>
  <td>18</td>
  <td>27</td>
  <td>8</td>
  <td>31</td>
  <td>10</td>
  <td>2</td>
  <td>SNP 4</td>
</tr>
"""

_TABLE_ROW_ELECTION = """
<tr>
  <td>2021 Scottish Parliament election</td>
  <td>—</td>
  <td>—</td>
  <td>22</td>
  <td>22</td>
  <td>5</td>
  <td>40</td>
  <td>7</td>
  <td>2</td>
  <td>SNP 18</td>
</tr>
"""


def _make_html(rows: str) -> str:
    """Wrap rows in a full constituency-vote page structure."""
    return f"""
    <html><body>
    <div class="mw-heading mw-heading2">
      <h2 id="Constituency_vote">Constituency vote</h2>
    </div>
    <table class="wikitable">
      {rows}
    </table>
    </body></html>
    """


# ── parse_date_range ──────────────────────────────────────────────────────────


class TestParseDateRange:
    """Tests for parse_date_range — date string parsing."""

    def test_same_month_en_dash(self) -> None:
        result = parse_date_range("1–3 Feb 2026")
        assert result == (date(2026, 2, 1), date(2026, 2, 3))

    def test_same_month_hyphen(self) -> None:
        result = parse_date_range("1-3 Feb 2026")
        assert result == (date(2026, 2, 1), date(2026, 2, 3))

    def test_cross_month(self) -> None:
        result = parse_date_range("28 Jan – 3 Feb 2026")
        assert result == (date(2026, 1, 28), date(2026, 2, 3))

    def test_cross_year(self) -> None:
        result = parse_date_range("29 Dec – 2 Jan 2026")
        assert result == (date(2025, 12, 29), date(2026, 1, 2))

    def test_single_day(self) -> None:
        result = parse_date_range("3 Feb 2026")
        assert result == (date(2026, 2, 3), date(2026, 2, 3))

    def test_full_month_name(self) -> None:
        result = parse_date_range("15-18 January 2026")
        assert result == (date(2026, 1, 15), date(2026, 1, 18))

    def test_election_label_returns_none(self) -> None:
        assert parse_date_range("2021 Scottish Parliament election") is None

    def test_empty_returns_none(self) -> None:
        assert parse_date_range("") is None

    def test_hyphen_only_returns_none(self) -> None:
        assert parse_date_range("—") is None


# ── identify_party_columns ────────────────────────────────────────────────────


class TestIdentifyPartyColumns:
    """Tests for identify_party_columns — header → party mapping."""

    def test_standard_headers(self) -> None:
        headers = ["dates", "pollster", "n", "con", "lab", "ld", "snp", "green", "alba", "lead"]
        result = identify_party_columns(headers)
        assert result == {
            3: "Conservative",
            4: "Labour",
            5: "Liberal Democrats",
            6: "Scottish National Party",
            7: "Scottish Greens",
            8: "Alba Party",
        }

    def test_partial_headers(self) -> None:
        headers = ["dates", "pollster", "snp", "lab", "con"]
        result = identify_party_columns(headers)
        assert result == {
            2: "Scottish National Party",
            3: "Labour",
            4: "Conservative",
        }

    def test_case_insensitive(self) -> None:
        headers = ["Dates", "Pollster", "SNP", "Lab", "Con"]
        result = identify_party_columns(headers)
        assert 2 in result
        assert result[2] == "Scottish National Party"

    def test_no_party_columns(self) -> None:
        headers = ["dates", "pollster", "sample", "lead"]
        assert identify_party_columns(headers) == {}

    def test_empty_headers(self) -> None:
        assert identify_party_columns([]) == {}


# ── parse_constituency_polls ──────────────────────────────────────────────────


class TestParseConstituencyPolls:
    """Tests for parse_constituency_polls — end-to-end HTML parsing."""

    def test_basic_row(self) -> None:
        html = _make_html(_TABLE_HEADER + _TABLE_ROW_BASIC)
        polls = parse_constituency_polls(html)
        assert len(polls) == 1
        p = polls[0]
        assert p.fieldwork_start == date(2026, 2, 1)
        assert p.fieldwork_end == date(2026, 2, 3)
        assert p.pollster_name == "Savanta"
        assert p.sample_size == 1054
        assert p.party_percentages["Scottish National Party"] == pytest.approx(31.0)
        assert p.party_percentages["Labour"] == pytest.approx(27.0)
        assert p.party_percentages["Conservative"] == pytest.approx(18.0)
        assert p.party_percentages["Scottish Greens"] == pytest.approx(10.0)
        assert p.party_percentages["Alba Party"] == pytest.approx(2.0)
        assert p.party_percentages["Liberal Democrats"] == pytest.approx(8.0)

    def test_election_row_skipped(self) -> None:
        html = _make_html(_TABLE_HEADER + _TABLE_ROW_ELECTION + _TABLE_ROW_BASIC)
        polls = parse_constituency_polls(html)
        # Only the real poll row should be returned, not the election baseline
        assert len(polls) == 1
        assert polls[0].pollster_name == "Savanta"

    def test_multiple_rows(self) -> None:
        row2 = """
        <tr>
          <td>10–12 Mar 2026</td>
          <td>Survation</td>
          <td>1,200</td>
          <td>17</td>
          <td>30</td>
          <td>7</td>
          <td>32</td>
          <td>9</td>
          <td>3</td>
          <td>SNP 2</td>
        </tr>
        """
        html = _make_html(_TABLE_HEADER + _TABLE_ROW_BASIC + row2)
        polls = parse_constituency_polls(html)
        assert len(polls) == 2
        pollster_names = {p.pollster_name for p in polls}
        assert pollster_names == {"Savanta", "Survation"}

    def test_no_constituency_heading_returns_empty(self) -> None:
        html = """
        <html><body>
        <h2>Regional vote</h2>
        <table class="wikitable">
          <tr><th>Dates</th><th>Pollster</th><th>SNP</th></tr>
          <tr><td>1–3 Feb 2026</td><td>Savanta</td><td>30</td></tr>
        </table>
        </body></html>
        """
        assert parse_constituency_polls(html) == []

    def test_no_table_returns_empty(self) -> None:
        html = """
        <html><body>
        <h2>Constituency vote</h2>
        <p>No table here.</p>
        </body></html>
        """
        assert parse_constituency_polls(html) == []

    def test_footnote_brackets_stripped_from_percentages(self) -> None:
        row = """
        <tr>
          <td>1–3 Feb 2026</td>
          <td>Savanta</td>
          <td>1,054</td>
          <td>18[a]</td>
          <td>27</td>
          <td>8</td>
          <td>31</td>
          <td>10</td>
          <td>2</td>
          <td>SNP 4</td>
        </tr>
        """
        html = _make_html(_TABLE_HEADER + row)
        polls = parse_constituency_polls(html)
        assert len(polls) == 1
        assert polls[0].party_percentages["Conservative"] == pytest.approx(18.0)

    def test_missing_sample_size(self) -> None:
        row = """
        <tr>
          <td>1–3 Feb 2026</td>
          <td>Savanta</td>
          <td>–</td>
          <td>18</td>
          <td>27</td>
          <td>8</td>
          <td>31</td>
          <td>10</td>
          <td>2</td>
          <td>SNP 4</td>
        </tr>
        """
        html = _make_html(_TABLE_HEADER + row)
        polls = parse_constituency_polls(html)
        assert len(polls) == 1
        assert polls[0].sample_size is None

    def test_row_without_party_data_skipped(self) -> None:
        row = """
        <tr>
          <td>1–3 Feb 2026</td>
          <td>Savanta</td>
          <td></td>
          <td></td>
          <td></td>
        </tr>
        """
        html = _make_html(_TABLE_HEADER + row)
        polls = parse_constituency_polls(html)
        assert polls == []
