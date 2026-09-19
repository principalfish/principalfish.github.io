"""Unit tests for the US national poll importers' shared Wikipedia parser.

All tests use synthetic HTML — no network access or database required.
"""

from __future__ import annotations

from datetime import date

import pytest
from bs4 import BeautifulSoup, Tag

from polls.importers.us.us_polls_common import (
    PARTY_SUFFIXES,
    CandidateColumn,
    Cell,
    ParsedPollRow,
    candidate_columns,
    classify_table,
    clean_pollster_label,
    detect_columns,
    expand_table_grid,
    heading_path,
    is_collapsed,
    matchup_label,
    parse_date_range,
    parse_poll_tables,
    parse_table_rows,
    pollster_identifier,
    surname,
)


class TestParseDateRange:
    def test_single_day(self) -> None:
        assert parse_date_range("June 3, 2026") == (date(2026, 6, 3), date(2026, 6, 3))

    def test_same_month_range(self) -> None:
        assert parse_date_range("June 1–3, 2026") == (date(2026, 6, 1), date(2026, 6, 3))

    def test_cross_month_range_with_spaces(self) -> None:
        assert parse_date_range("May 28 – June 3, 2026") == (date(2026, 5, 28), date(2026, 6, 3))

    def test_cross_month_range_no_spaces(self) -> None:
        assert parse_date_range("May 28–June 3, 2026") == (date(2026, 5, 28), date(2026, 6, 3))

    def test_cross_year_range(self) -> None:
        # December → January rolls the start year back one.
        assert parse_date_range("December 30 – January 2, 2026") == (date(2025, 12, 30), date(2026, 1, 2))

    def test_fully_explicit_range_with_both_years(self) -> None:
        # The aggregation tables' "Dates administered" format.
        assert parse_date_range("January 9, 2025 – June 29, 2026") == (date(2025, 1, 9), date(2026, 6, 29))

    def test_unparseable_returns_none(self) -> None:
        assert parse_date_range("sometime last week") is None


class TestPollsterIdentifier:
    def test_slug_with_suffix(self) -> None:
        assert pollster_identifier("YouGov", "_us_house") == "yougov_us_house"

    def test_non_alphanumeric_collapses_to_underscores(self) -> None:
        assert pollster_identifier("Data for Progress", "_us_senate") == "data_for_progress_us_senate"


# ── Live-markup fixtures ──────────────────────────────────────────────────────
#
# The HTML below copies the shapes verified on Wikipedia on 2026-09-17, not a
# tidied-up version of them: `<section>` wrappers, `div.mw-heading` wrappers
# around every heading, `<br>` inside header cells, `<sup class="reference">`
# footnotes, `<b>` on the leading candidate, rowspan groups, colspan event rows
# and the second header row of empty colour cells. Anything the parser gets
# wrong on real pages has to be reproducible here.

PRESIDENT_PAGE = """
<html><body><div class="mw-parser-output">
<section data-mw-section-id="1">
<div class="mw-heading mw-heading2"><h2 id="Opinion_polling">Opinion polling</h2>
<span class="mw-editsection"><a href="/w/index.php?section=1">edit</a></span></div>

<section data-mw-section-id="2">
<div class="mw-heading mw-heading3"><h3 id="Republican_primary">Republican primary</h3></div>
<section data-mw-section-id="3">
<div class="mw-heading mw-heading4"><h4 id="Nationwide_primary">Nationwide</h4></div>
<table class="wikitable sortable" id="primary-nationwide">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>JD Vance<br /><small>(R)</small></th><th>Marco Rubio<br /><small>(R)</small></th>
<th>Undecided</th></tr>
<tr><td>Emerson College<sup class="reference">[12]</sup></td><td>June 24–26, 2026</td>
<td>1,000 (RV)</td><td><b>52%</b></td><td>21%</td><td>27%</td></tr>
</tbody></table>
</section>
</section>

<section data-mw-section-id="4">
<div class="mw-heading mw-heading3"><h3 id="General_election">General election</h3></div>
<section data-mw-section-id="5">
<div class="mw-heading mw-heading4"><h4 id="Nationwide">Nationwide</h4></div>
<section data-mw-section-id="6">
<div class="mw-heading mw-heading5"><h5 id="JD_Vance_vs._Gavin_Newsom">JD Vance vs. Gavin Newsom</h5></div>
<table class="wikitable sortable" id="vance-newsom">
<tbody>
<tr>
<th rowspan="2">Poll source</th><th rowspan="2">Date(s)<br />administered</th>
<th rowspan="2">Sample<br />size</th><th rowspan="2">Margin<br />of error</th>
<th><a href="/wiki/JD_Vance" title="JD Vance">Vance</a><br /><small>(R)</small></th>
<th><a href="/wiki/Gavin_Newsom" title="Gavin Newsom">Newsom</a><br /><small>(D)</small></th>
<th rowspan="2">Other</th><th rowspan="2">Undecided</th>
</tr>
<tr>
<td style="background-color:#E81B23;"></td><td style="background-color:#3333FF;"></td>
</tr>
<tr>
<td><a rel="nofollow" class="external text" href="https://example.invalid/poll">Emerson College</a></td>
<td>June 24–26, 2026</td><td>1,000 (LV)</td><td>± 3.0%</td>
<td><b>45%</b></td><td>44%</td><td>4%</td><td>7%</td>
</tr>
</tbody></table>
</section>
<section data-mw-section-id="7">
<div class="mw-heading mw-heading5"><h5 id="Hypothetical_polling">Hypothetical polling</h5></div>
<div class="mw-collapsible mw-made-collapsible">
<div class="mw-collapsible-content">
<table class="wikitable sortable" id="vance-harris">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Vance<br /><small>(R)</small></th><th>Harris<br /><small>(D)</small></th><th>Undecided</th></tr>
<tr><td>Emerson College</td><td>June 24–26, 2026</td><td>1,000 (LV)</td>
<td><b>47%</b></td><td>42%</td><td>11%</td></tr>
</tbody></table>
</div></div>
</section>
</section>

<section data-mw-section-id="8">
<div class="mw-heading mw-heading4"><h4 id="Statewide">Statewide</h4></div>
<section data-mw-section-id="9">
<div class="mw-heading mw-heading5"><h5 id="Nevada">Nevada</h5></div>
<section data-mw-section-id="10">
<div class="mw-heading mw-heading6"><h6 id="JD_Vance_vs._Gavin_Newsom_2">JD Vance vs. Gavin Newsom</h6></div>
<table class="wikitable sortable" id="nevada-vance-newsom">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Vance<br /><small>(R)</small></th><th>Newsom<br /><small>(D)</small></th><th>Undecided</th></tr>
<tr><td>Noble Predictive Insights</td><td>May 28 – June 3, 2026</td><td>600 (RV)</td>
<td>44%</td><td><b>46%</b></td><td>10%</td></tr>
</tbody></table>
</section>
</section>
</section>
</section>
</section>
</div></body></html>
"""

# The same heading path written the way pages without Parsoid section wrappers
# serve it: flat `div.mw-heading` siblings, plus one legacy-markup heading whose
# id lives on an inner `span.mw-headline`.
PRESIDENT_PAGE_NO_SECTIONS = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="Opinion_polling">Opinion polling</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Republican_primary">Republican primary</h3></div>
<table class="wikitable" id="primary-table">
<tbody><tr><th>Poll source</th><th>Date(s) administered</th></tr></tbody></table>
<div class="mw-heading mw-heading3"><h3 id="General_election">General election</h3></div>
<div class="mw-heading mw-heading4"><h4 id="Nationwide">Nationwide</h4></div>
<h5><span class="mw-headline" id="JD_Vance_vs._Gavin_Newsom">JD Vance vs. Gavin Newsom</span>
<span class="mw-editsection">[edit]</span></h5>
<table class="wikitable" id="vance-newsom">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Vance<br /><small>(R)</small></th><th>Newsom<br /><small>(D)</small></th></tr>
<tr><td>Emerson College</td><td>June 24–26, 2026</td><td>1,000 (LV)</td><td>45%</td><td>44%</td></tr>
</tbody></table>
</div></body></html>
"""

# A Senate race page: "General election" › "Polling", with the aggregation table
# first, the nominee table (Michigan's rowspan-heavy shape), a collapsed
# hypothetical, and the "Predictions" table that must be rejected.
SENATE_RACE_PAGE = """
<html><body><div class="mw-parser-output">
<section data-mw-section-id="1">
<div class="mw-heading mw-heading2"><h2 id="Predictions">Predictions</h2></div>
<table class="wikitable" id="predictions">
<tbody><tr><th>Source</th><th>Ranking</th><th>As of</th></tr>
<tr><td>The Cook Political Report</td><td>Tossup</td><td>June 3, 2026</td></tr>
<tr><td>Sabato's Crystal Ball</td><td>Lean D</td><td>June 10, 2026</td></tr>
</tbody></table>
</section>
<section data-mw-section-id="2">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<section data-mw-section-id="3">
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<table class="wikitable sortable" id="senate-aggregation">
<tbody><tr>
<th>Source of poll<br />aggregation</th><th>Dates<br />administered</th><th>Dates<br />updated</th>
<th>Republicans</th><th>Democrats</th><th>Other/<br />Undecided<sup class="reference">[e]</sup></th>
<th>Margin</th></tr>
<tr><td>Decision Desk HQ<sup class="reference">[69]</sup></td>
<td>January 9, 2025 – June 29, 2026</td><td>June 29, 2026</td>
<td>40.1%</td><td>44.3%</td><td>15.6%</td><td>Democrats +4.2%</td></tr>
</tbody></table>
<table class="wikitable sortable" id="el-sayed-rogers">
<tbody>
<tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Margin<br />of error</th>
<th>Abdul El-Sayed<br /><small>(D)</small></th><th>Mike Rogers<br /><small>(R)</small></th>
<th>Other</th><th>Undecided</th></tr>
<tr>
<td rowspan="2">Glengariff Group<sup class="reference">[41]</sup></td>
<td rowspan="2">June 1–4, 2026</td>
<td>600 (LV)</td><td>± 4.0%</td><td>44%</td><td><b>45%</b></td><td>3%</td><td>8%</td></tr>
<tr>
<td>600 (LV) with leaners</td><td>± 4.0%</td><td>46%</td><td><b>47%</b></td><td>—</td><td>7%</td></tr>
<tr><td></td><td>August 18, 2026</td><td colspan="6">Primary election held</td></tr>
<tr>
<td>Marketing Resource Group (R)</td><td>September 8–11, 2026</td><td>600 (LV)</td>
<td>± 4.0%</td><td>42%</td><td><b>46%</b></td><td>4%</td><td>8%</td></tr>
</tbody></table>
<div class="mw-collapsible mw-collapsed" id="hypothetical-wrapper">
<div class="mw-collapsible-content">
<table class="wikitable sortable" id="hypothetical">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Haley Stevens<br /><small>(D)</small></th><th>Mike Rogers<br /><small>(R)</small></th></tr>
<tr><td>Glengariff Group</td><td>June 1–4, 2026</td><td>600 (LV)</td><td>41%</td><td>44%</td></tr>
</tbody></table>
</div></div>
</section>
</section>
</div></body></html>
"""

# Alaska's top-four table: four candidates, two of them sharing a surname, with
# the pollster, dates, sample and margin all shared by a rowspan group.
ALASKA_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<table class="wikitable sortable" id="alaska">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Margin<br />of error</th>
<th>Dan S. Sullivan<br /><small>(R)</small></th><th>Mary Peltola<br /><small>(D)</small></th>
<th>Dan J. Sullivan<br /><small>(R)</small></th><th>Gerald Heikes<br /><small>(R)</small></th>
<th>Undecided</th></tr>
<tr>
<td rowspan="2">Alaska Survey Research</td><td rowspan="2">July 7–9, 2026</td>
<td rowspan="2">1,203 (LV)</td><td rowspan="2">± 2.8%</td>
<td>40%</td><td><b>44%</b></td><td>3%</td><td>2%</td><td>11%</td></tr>
<tr><td>43%</td><td><b>47%</b></td><td>4%</td><td>2%</td><td>4%</td></tr>
</tbody></table>
</div></body></html>
"""

# A multi-district House page: "District N" › "General election" › "Polling",
# the CA-40 oddity ("Primary" › "General election"), and the candidate-list
# table that must be rejected despite holding a parseable date.
HOUSE_STATE_PAGE = """
<html><body><div class="mw-parser-output">
<section data-mw-section-id="1">
<div class="mw-heading mw-heading2"><h2 id="District_3">District 3</h2></div>
<table class="wikitable" id="candidate-list">
<tbody><tr><th>District</th><th>Incumbent</th><th>Candidates</th><th>Filing deadline</th></tr>
<tr><td>3rd</td><td>Jane Roe (D)</td><td>Jane Roe (D)<br />John Doe (R)</td><td>June 3, 2026</td></tr>
</tbody></table>
<section data-mw-section-id="2">
<div class="mw-heading mw-heading3"><h3 id="Democratic_primary">Democratic primary</h3></div>
<section data-mw-section-id="3">
<div class="mw-heading mw-heading4"><h4 id="Polling">Polling</h4></div>
<table class="wikitable sortable" id="district-3-primary">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Jane Roe<br /><small>(D)</small></th><th>Ann Lee<br /><small>(D)</small></th></tr>
<tr><td>Public Policy Polling</td><td>April 2–3, 2026</td><td>500 (LV)</td><td>52%</td><td>30%</td></tr>
</tbody></table>
</section>
</section>
<section data-mw-section-id="4">
<div class="mw-heading mw-heading3"><h3 id="General_election">General election</h3></div>
<section data-mw-section-id="5">
<div class="mw-heading mw-heading4"><h4 id="Polling_2">Polling</h4></div>
<table class="wikitable sortable" id="district-3-general">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Jane Roe<br /><small>(D)</small></th><th>John Doe<br /><small>(R)</small></th></tr>
<tr><td>Public Policy Polling</td><td>September 2–3, 2026</td><td>500 (LV)</td><td>49%</td><td>45%</td></tr>
</tbody></table>
</section>
</section>
</section>
<section data-mw-section-id="6">
<div class="mw-heading mw-heading2"><h2 id="District_40">District 40</h2></div>
<section data-mw-section-id="7">
<div class="mw-heading mw-heading3"><h3 id="Primary">Primary</h3></div>
<section data-mw-section-id="8">
<div class="mw-heading mw-heading4"><h4 id="General_election_2">General election</h4></div>
<table class="wikitable sortable" id="ca-40">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Ken Calvert<br /><small>(R)</small></th><th>Young Kim<br /><small>(R)</small></th></tr>
<tr><td>co/efficient</td><td>March 10–12, 2026</td><td>400 (LV)</td><td>38%</td><td>34%</td></tr>
</tbody></table>
</section>
</section>
</section>
</div></body></html>
"""

# An at-large House page in Vermont's shape: the polling table hangs off
# "Predictions", not off "General election" directly.
HOUSE_AT_LARGE_PAGE = """
<html><body><div class="mw-parser-output">
<section data-mw-section-id="1">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<section data-mw-section-id="2">
<div class="mw-heading mw-heading3"><h3 id="Predictions">Predictions</h3></div>
<section data-mw-section-id="3">
<div class="mw-heading mw-heading4"><h4 id="Polling">Polling</h4></div>
<table class="wikitable sortable" id="vermont">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Becca Balint<br /><small>(D)</small></th><th>Mark Coester<br /><small>(R)</small></th></tr>
<tr><td>UNH Survey Center</td><td>August 1–5, 2026</td><td>700 (LV)</td><td>61%</td><td>28%</td></tr>
</tbody></table>
</section>
</section>
</section>
</div></body></html>
"""

# The House index page: the generic-ballot aggregation table (the only real one)
# and the redistricting table it sits next to.
HOUSE_INDEX_PAGE = """
<html><body><div class="mw-parser-output">
<section data-mw-section-id="1">
<div class="mw-heading mw-heading2"><h2 id="Out-of-cycle_partisan_redistricting_efforts">
Out-of-cycle partisan redistricting efforts</h2></div>
<table class="wikitable" id="redistricting">
<tbody><tr><th>State</th><th>D</th><th>C</th><th>R</th><th>Signed into law</th></tr>
<tr><td>Texas</td><td>0</td><td>0</td><td>5</td><td>August 22, 2025</td></tr>
</tbody></table>
</section>
<section data-mw-section-id="2">
<div class="mw-heading mw-heading2"><h2 id="Generic_congressional_ballot_aggregate_polls">
Generic congressional ballot aggregate polls</h2></div>
<table class="wikitable sortable" id="generic-ballot">
<tbody><tr>
<th>Source of poll<br />aggregation</th><th>Dates<br />administered</th><th>Dates<br />updated</th>
<th>Republicans</th><th>Democrats</th><th>Other/<br />Undecided</th><th>Margin</th></tr>
<tr><td>Decision Desk HQ</td><td>January 9, 2025 – June 29, 2026</td><td>June 29, 2026</td>
<td>40.1%</td><td>44.3%</td><td>15.6%</td><td>Democrats +4.2%</td></tr>
</tbody></table>
</section>
</div></body></html>
"""

# The Senate index page's seat-count table. The "Last change" date is here so the
# rejection can be shown to be structural rather than date-driven.
SENATE_SEATS_TABLE = """
<html><body><table class="wikitable" id="seats">
<tbody><tr><th>Affiliation</th><th>Democratic</th><th>Independent</th><th>Republican</th>
<th>Total</th><th>Last change</th></tr>
<tr><td>Before the election</td><td>45</td><td>2</td><td>53</td><td>100</td>
<td>June 3, 2026</td></tr>
</tbody></table></body></html>
"""


def _table(html: str, table_id: str) -> Tag:
    """Return the table with ``table_id`` from a fixture page."""
    table = BeautifulSoup(html, "lxml").find(id=table_id)
    assert isinstance(table, Tag)
    return table


def _texts(cells: list[Cell]) -> list[str]:
    """Return one grid row's cell texts."""
    return [cell.text for cell in cells]


def _heading_texts(table: Tag) -> list[str]:
    """Return a table's heading path as plain text, outermost first."""
    return [heading.text for heading in heading_path(table)]


def _too_wide_table(table_id: str) -> str:
    """A poll-shaped table whose second row spans 1,000 cells × colspan 64."""
    return (
        f"<table id='{table_id}'><tr><th>Poll source</th><th>Date(s) administered</th></tr>"
        "<tr>" + "<td colspan='64'>x</td>" * 1000 + "</tr></table>"
    )


class TestExpandTableGrid:
    def test_rowspan_value_repeats_into_every_row_it_covers(self) -> None:
        # Michigan's shape: one pollster and one date cell serve the LV row and
        # the "with leaners" row below it. Read positionally, the second row's
        # first cell is the sample size.
        grid = expand_table_grid(_table(SENATE_RACE_PAGE, "el-sayed-rogers"))
        assert _texts(grid[1])[:4] == [
            "Glengariff Group [41]",
            "June 1–4, 2026",
            "600 (LV)",
            "± 4.0%",
        ]
        assert _texts(grid[2])[:4] == [
            "Glengariff Group [41]",
            "June 1–4, 2026",
            "600 (LV) with leaners",
            "± 4.0%",
        ]

    def test_rowspan_copies_are_flagged_as_spanned(self) -> None:
        grid = expand_table_grid(_table(SENATE_RACE_PAGE, "el-sayed-rogers"))
        assert [cell.is_spanned for cell in grid[1][:3]] == [False, False, False]
        assert [cell.is_spanned for cell in grid[2][:3]] == [True, True, False]

    def test_colspan_value_repeats_into_every_column_it_covers(self) -> None:
        # The primary-election event row: an empty pollster cell, a parseable
        # date, and one colspan cell filling the rest of the row.
        grid = expand_table_grid(_table(SENATE_RACE_PAGE, "el-sayed-rogers"))
        event_row = _texts(grid[3])
        assert event_row[:2] == ["", "August 18, 2026"]
        assert event_row[2:] == ["Primary election held"] * 6
        assert [cell.is_spanned for cell in grid[3][2:]] == [False, True, True, True, True, True]

    def test_event_row_date_cell_still_parses(self) -> None:
        info = classify_table(_table(SENATE_RACE_PAGE, "el-sayed-rogers"))
        assert info is not None
        event_row = info.grid[3]
        assert parse_date_range(event_row[info.columns.date].text) == (
            date(2026, 8, 18),
            date(2026, 8, 18),
        )

    def test_rowspan_group_covers_all_shared_columns(self) -> None:
        # Alaska shares pollster, dates, sample and margin across both rows.
        grid = expand_table_grid(_table(ALASKA_PAGE, "alaska"))
        assert _texts(grid[1])[:4] == _texts(grid[2])[:4]
        assert _texts(grid[2])[:4] == [
            "Alaska Survey Research",
            "July 7–9, 2026",
            "1,203 (LV)",
            "± 2.8%",
        ]
        assert _texts(grid[2])[4:] == ["43%", "47%", "4%", "2%", "4%"]

    def test_header_rowspan_fills_the_colour_row(self) -> None:
        # The presidential tables' second header row holds only empty colour
        # cells; every other column is the first row's th spilling down.
        grid = expand_table_grid(_table(PRESIDENT_PAGE, "vance-newsom"))
        assert _texts(grid[1]) == [
            "Poll source",
            "Date(s) administered",
            "Sample size",
            "Margin of error",
            "",
            "",
            "Other",
            "Undecided",
        ]
        assert [cell.is_header for cell in grid[1]] == [
            True,
            True,
            True,
            True,
            False,
            False,
            True,
            True,
        ]

    def test_every_row_has_the_same_width(self) -> None:
        grid = expand_table_grid(_table(SENATE_RACE_PAGE, "el-sayed-rogers"))
        assert {len(row) for row in grid} == {8}

    def test_padding_cells_carry_no_tag(self) -> None:
        # A short row is padded rather than truncating the grid.
        table = _table(
            "<table id='t'><tr><th>Poll source</th><th>Dates</th><th>Sample</th></tr>"
            "<tr><td>Emerson</td></tr></table>",
            "t",
        )
        grid = expand_table_grid(table)
        assert _texts(grid[1]) == ["Emerson", "", ""]
        assert [cell.tag is None for cell in grid[1]] == [False, True, True]

    def test_cell_text_collapses_markup(self) -> None:
        grid = expand_table_grid(_table(PRESIDENT_PAGE, "vance-newsom"))
        assert grid[0][1].text == "Date(s) administered"
        assert _texts(grid[2]) == [
            "Emerson College",
            "June 24–26, 2026",
            "1,000 (LV)",
            "± 3.0%",
            "45%",
            "44%",
            "4%",
            "7%",
        ]

    def test_header_cells_keep_their_tag_for_later_stages(self) -> None:
        # candidate_columns reads the candidate's full name off the <a title>.
        grid = expand_table_grid(_table(PRESIDENT_PAGE, "vance-newsom"))
        candidate = grid[0][4]
        assert candidate.tag is not None
        link = candidate.tag.find("a")
        assert isinstance(link, Tag)
        assert link.get("title") == "JD Vance"

    def test_rowspan_zero_is_capped_like_any_other_span(self) -> None:
        # rowspan="0" means "to the end of the table", but the 64-row cap
        # still applies: row 64 gets its own first cell back.
        body = "".join(f"<tr><td>row {index}</td></tr>" for index in range(1, 100))
        table = _table(
            f"<table id='t'><tr><td rowspan='0'>spans</td><td>a</td></tr>{body}</table>",
            "t",
        )
        grid = expand_table_grid(table)
        assert len(grid) == 100
        assert grid[63][0].text == "spans"
        assert grid[64][0].text == "row 64"

    def test_a_row_past_the_width_bound_empties_the_grid(self) -> None:
        # 1,000 cells of colspan 64 would expand to 64,000 columns.
        assert expand_table_grid(_table(_too_wide_table("t"), "t")) == []

    def test_a_grid_past_the_cell_bound_is_empty(self) -> None:
        # 600 rows × 200 columns is 120,000 cells, although no row is too wide.
        rows = ("<tr>" + "<td colspan='50'>x</td>" * 4 + "</tr>") * 600
        assert expand_table_grid(_table(f"<table id='t'>{rows}</table>", "t")) == []

    def test_a_row_exactly_at_the_width_bound_is_kept(self) -> None:
        row = "<td colspan='64'>x</td>" * 3 + "<td colspan='8'>y</td>"
        grid = expand_table_grid(_table(f"<table id='t'><tr>{row}</tr></table>", "t"))
        assert len(grid[0]) == 200

    def test_nested_table_rows_are_ignored(self) -> None:
        table = _table(
            "<table id='t'><tr><th>Poll source</th><th>Dates</th></tr>"
            "<tr><td>Emerson<table><tr><td>inner</td><td>rows</td></tr></table></td>"
            "<td>June 3, 2026</td></tr></table>",
            "t",
        )
        grid = expand_table_grid(table)
        assert len(grid) == 2
        assert _texts(grid[1]) == ["Emerson inner rows", "June 3, 2026"]


class TestHeadingPath:
    def test_nationwide_president_table(self) -> None:
        assert _heading_texts(_table(PRESIDENT_PAGE, "vance-newsom")) == [
            "Opinion polling",
            "General election",
            "Nationwide",
            "JD Vance vs. Gavin Newsom",
        ]

    def test_statewide_president_table_reaches_h6(self) -> None:
        assert _heading_texts(_table(PRESIDENT_PAGE, "nevada-vance-newsom")) == [
            "Opinion polling",
            "General election",
            "Statewide",
            "Nevada",
            "JD Vance vs. Gavin Newsom",
        ]

    def test_primary_section_is_visible_in_the_path(self) -> None:
        # accepts_table rejects any path containing a heading starting "primar".
        assert _heading_texts(_table(PRESIDENT_PAGE, "primary-nationwide")) == [
            "Opinion polling",
            "Republican primary",
            "Nationwide",
        ]

    def test_works_without_section_wrappers(self) -> None:
        assert _heading_texts(_table(PRESIDENT_PAGE_NO_SECTIONS, "vance-newsom")) == [
            "Opinion polling",
            "General election",
            "Nationwide",
            "JD Vance vs. Gavin Newsom",
        ]

    def test_anchors_are_captured(self) -> None:
        path = heading_path(_table(PRESIDENT_PAGE, "nevada-vance-newsom"))
        assert [heading.anchor for heading in path] == [
            "Opinion_polling",
            "General_election",
            "Statewide",
            "Nevada",
            "JD_Vance_vs._Gavin_Newsom_2",
        ]

    def test_levels_are_recorded(self) -> None:
        path = heading_path(_table(PRESIDENT_PAGE, "nevada-vance-newsom"))
        assert [heading.level for heading in path] == [2, 3, 4, 5, 6]

    def test_legacy_headline_span_supplies_the_anchor(self) -> None:
        path = heading_path(_table(PRESIDENT_PAGE_NO_SECTIONS, "vance-newsom"))
        assert path[-1].anchor == "JD_Vance_vs._Gavin_Newsom"
        assert path[-1].text == "JD Vance vs. Gavin Newsom"

    def test_stops_after_the_first_h2(self) -> None:
        # The generic-ballot table's own h2 ends the walk: the redistricting h2
        # above it is not part of the path.
        assert _heading_texts(_table(HOUSE_INDEX_PAGE, "generic-ballot")) == [
            "Generic congressional ballot aggregate polls",
        ]

    def test_house_district_path(self) -> None:
        assert _heading_texts(_table(HOUSE_STATE_PAGE, "district-3-general")) == [
            "District 3",
            "General election",
            "Polling",
        ]

    def test_house_district_primary_path(self) -> None:
        assert _heading_texts(_table(HOUSE_STATE_PAGE, "district-3-primary")) == [
            "District 3",
            "Democratic primary",
            "Polling",
        ]

    def test_ca_40_primary_wraps_the_general_election(self) -> None:
        assert _heading_texts(_table(HOUSE_STATE_PAGE, "ca-40")) == [
            "District 40",
            "Primary",
            "General election",
        ]

    def test_at_large_polling_under_predictions(self) -> None:
        assert _heading_texts(_table(HOUSE_AT_LARGE_PAGE, "vermont")) == [
            "General election",
            "Predictions",
            "Polling",
        ]

    def test_senate_race_polling_path(self) -> None:
        assert _heading_texts(_table(SENATE_RACE_PAGE, "el-sayed-rogers")) == [
            "General election",
            "Polling",
        ]

    def test_table_above_every_heading_has_an_empty_path(self) -> None:
        assert heading_path(_table(SENATE_SEATS_TABLE, "seats")) == []


class TestIsCollapsed:
    def test_visible_table(self) -> None:
        assert is_collapsed(_table(SENATE_RACE_PAGE, "el-sayed-rogers")) is False

    def test_ancestor_with_collapsible_content(self) -> None:
        assert is_collapsed(_table(SENATE_RACE_PAGE, "hypothetical")) is True

    def test_president_hypothetical_matchup(self) -> None:
        assert is_collapsed(_table(PRESIDENT_PAGE, "vance-harris")) is True

    def test_table_collapsed_by_its_own_class(self) -> None:
        table = _table(
            "<table id='t' class='wikitable mw-collapsible mw-collapsed'>"
            "<tr><th>Poll source</th><th>Dates</th></tr></table>",
            "t",
        )
        assert is_collapsed(table) is True


class TestDetectColumns:
    @staticmethod
    def _header(html: str, table_id: str) -> list[Cell]:
        return expand_table_grid(_table(html, table_id))[0]

    def test_senate_nominee_table(self) -> None:
        columns = detect_columns(self._header(SENATE_RACE_PAGE, "el-sayed-rogers"))
        assert columns is not None
        assert (columns.pollster, columns.date, columns.sample) == (0, 1, 2)
        assert columns.is_aggregation is False
        assert columns.header_row == 0

    def test_aggregation_table_prefers_the_updated_date(self) -> None:
        columns = detect_columns(self._header(HOUSE_INDEX_PAGE, "generic-ballot"))
        assert columns is not None
        # "Dates administered" is column 1 and spans the whole cycle; the
        # snapshot date in column 2 is the one the row reports.
        assert (columns.pollster, columns.date, columns.sample) == (0, 2, None)
        assert columns.is_aggregation is True

    def test_header_row_index_is_recorded(self) -> None:
        header = self._header(SENATE_RACE_PAGE, "el-sayed-rogers")
        columns = detect_columns(header, header_row=2)
        assert columns is not None
        assert columns.header_row == 2

    def test_pollster_and_firm_labels(self) -> None:
        labels = (
            "Pollster",
            "Polling firm",
            "Poll source",
            "Source of poll aggregation",
        )
        for label in labels:
            header = self._header(
                f"<table id='t'><tr><th>{label}</th><th>Dates administered</th></tr></table>",
                "t",
            )
            assert detect_columns(header) is not None, label

    def test_sample_column_labels(self) -> None:
        header = self._header(
            "<table id='t'><tr><th>Pollster</th><th>Dates</th><th>N</th></tr></table>", "t"
        )
        columns = detect_columns(header)
        assert columns is not None
        assert columns.sample == 2

    def test_no_positional_fallback_without_labels(self) -> None:
        # The old detect_layout() assumed date=0, pollster=1 whenever the header
        # named neither. That fallback is what let non-poll tables through.
        header = self._header(
            "<table id='t'><tr><th>A</th><th>B</th><th>C</th></tr>"
            "<tr><td>June 3, 2026</td><td>Emerson</td><td>45%</td></tr></table>",
            "t",
        )
        assert detect_columns(header) is None

    def test_date_column_without_a_pollster_is_rejected(self) -> None:
        header = self._header(
            "<table id='t'><tr><th>State</th><th>Dates administered</th></tr></table>", "t"
        )
        assert detect_columns(header) is None

    def test_pollster_column_without_a_date_is_rejected(self) -> None:
        header = self._header(
            "<table id='t'><tr><th>Poll source</th><th>Result</th></tr></table>", "t"
        )
        assert detect_columns(header) is None

    def test_bare_source_column_is_not_a_pollster_column(self) -> None:
        # "Source" alone is the Predictions tables' first column.
        header = self._header(
            "<table id='t'><tr><th>Source</th><th>Ranking</th><th>Dates administered</th></tr></table>",
            "t",
        )
        assert detect_columns(header) is None


class TestClassifyTable:
    def test_president_nationwide_table(self) -> None:
        info = classify_table(_table(PRESIDENT_PAGE, "vance-newsom"))
        assert info is not None
        assert (info.columns.pollster, info.columns.date) == (0, 1)
        assert info.columns.sample == 2
        assert info.columns.header_row == 0
        assert info.collapsed is False
        assert info.headings[-1].text == "JD Vance vs. Gavin Newsom"
        assert len(info.grid) == 3

    def test_data_rows_start_below_the_header(self) -> None:
        info = classify_table(_table(SENATE_RACE_PAGE, "el-sayed-rogers"))
        assert info is not None
        assert len(info.data_rows) == 4
        assert info.data_rows[0][0].text == "Glengariff Group [41]"

    def test_collapsed_hypothetical_table(self) -> None:
        info = classify_table(_table(SENATE_RACE_PAGE, "hypothetical"))
        assert info is not None
        assert info.collapsed is True
        assert [item.text for item in info.headings] == ["General election", "Polling"]

    def test_aggregation_table(self) -> None:
        info = classify_table(_table(HOUSE_INDEX_PAGE, "generic-ballot"))
        assert info is not None
        assert info.columns.is_aggregation is True
        assert info.data_rows[0][info.columns.date].text == "June 29, 2026"

    def test_alaska_four_way_table(self) -> None:
        info = classify_table(_table(ALASKA_PAGE, "alaska"))
        assert info is not None
        assert len(info.data_rows) == 2
        assert info.data_rows[1][info.columns.pollster].text == "Alaska Survey Research"

    def test_ca_40_table_classifies_and_keeps_its_primary_heading(self) -> None:
        info = classify_table(_table(HOUSE_STATE_PAGE, "ca-40"))
        assert info is not None
        assert [heading.text for heading in info.headings] == [
            "District 40",
            "Primary",
            "General election",
        ]

    def test_header_below_a_caption_row_is_still_found(self) -> None:
        info = classify_table(
            _table(
                "<table id='t'><tr><td colspan='3'>2026 Senate election in Michigan</td></tr>"
                "<tr><th>Poll source</th><th>Date(s) administered</th><th>Sample size</th></tr>"
                "<tr><td>Emerson</td><td>June 3, 2026</td><td>800 (LV)</td></tr></table>",
                "t",
            )
        )
        assert info is not None
        assert info.columns.header_row == 1
        assert len(info.data_rows) == 1

    def test_oversized_table_is_rejected(self) -> None:
        assert classify_table(_table(_too_wide_table("t"), "t")) is None

    def test_empty_table_is_rejected(self) -> None:
        assert classify_table(_table("<table id='t'></table>", "t")) is None


class TestClassifyTableRejections:
    """Non-poll tables must be rejected on structure, not on their dates.

    Every fixture here holds at least one cell that ``parse_date_range`` reads
    happily; it is the missing pollster or date *column* that rejects the table.
    """

    def test_predictions_table(self) -> None:
        table = _table(SENATE_RACE_PAGE, "predictions")
        assert parse_date_range(expand_table_grid(table)[1][2].text) is not None
        assert classify_table(table) is None

    def test_house_redistricting_table(self) -> None:
        table = _table(HOUSE_INDEX_PAGE, "redistricting")
        assert parse_date_range(expand_table_grid(table)[1][4].text) is not None
        assert classify_table(table) is None

    def test_senate_seats_table(self) -> None:
        table = _table(SENATE_SEATS_TABLE, "seats")
        assert parse_date_range(expand_table_grid(table)[1][5].text) is not None
        assert classify_table(table) is None

    def test_candidate_list_table(self) -> None:
        table = _table(HOUSE_STATE_PAGE, "candidate-list")
        assert parse_date_range(expand_table_grid(table)[1][3].text) is not None
        assert classify_table(table) is None


# ── Candidate / matchup / row fixtures ────────────────────────────────────────

# Texas: the Senate header shape where each candidate name is a link and the
# party suffix is a second link inside <small>, plus the pollster-cell variants
# seen live (a joint partisan sponsor, a footnoted percentage, an em-dash cell).
TEXAS_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<table class="wikitable sortable" id="paxton-talarico">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Margin<br />of error</th>
<th><a href="/wiki/Ken_Paxton" title="Ken Paxton">Ken Paxton</a><br />
<small>(<a href="/wiki/Republican_Party_(United_States)"
title="Republican Party (United States)">R</a>)</small></th>
<th><a href="/wiki/James_Talarico" title="James Talarico">James Talarico</a><br />
<small>(<a href="/wiki/Democratic_Party_(United_States)"
title="Democratic Party (United States)">D</a>)</small></th>
<th>Other</th><th>Undecided</th></tr>
<tr><td>Fabrizio Ward (R)/ Impact Research (D)<sup class="reference">[ai]</sup></td>
<td>September 2–4, 2026</td><td>1,000 (LV)</td><td>± 3.1%</td>
<td><b>49%</b></td><td>41% [k]</td><td>—</td><td>10%</td></tr>
<tr><td>Rasmussen Reports (R)</td><td>August 10, 2026</td><td>900 (LV)</td><td>± 3.3%</td>
<td><b>50%</b></td><td>40%</td><td>2%</td><td>8%</td></tr>
<tr><td>GQR (D)</td><td>July 6–9, 2026</td><td>800 (RV)</td><td>± 3.5%</td>
<td>44%</td><td><b>45%</b></td><td>3%</td><td>8%</td></tr>
</tbody></table>
</div></body></html>
"""

# Nebraska: a Republican against an independent, with no Democrat on the ballot,
# plus the "result" row that names a source and a date but holds no numbers.
NEBRASKA_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<table class="wikitable sortable" id="ricketts-osborn">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Pete Ricketts<br /><small>(R)</small></th><th>Dan Osborn<br /><small>(I)</small></th>
<th>Undecided</th></tr>
<tr><td>Change Research</td><td>August 20–23, 2026</td><td>1,100 (LV)</td>
<td><b>48%</b></td><td>44%</td><td>8%</td></tr>
<tr><td>2024 election result</td><td>November 5, 2024</td><td>—</td><td>—</td><td>—</td>
<td>—</td></tr>
</tbody></table>
</div></body></html>
"""

# The state-party and unknown suffixes: Minnesota's DFL, North Dakota's D-NPL,
# and a third-party label this parser has no party for.
MINOR_SUFFIX_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<table class="wikitable sortable" id="minnesota">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Peggy Flanagan<br /><small>(DFL)</small></th>
<th>Michele Tafoya<br /><small>(R)</small></th></tr>
<tr><td>SurveyUSA</td><td>July 1–3, 2026</td><td>900 (LV)</td><td>49%</td><td>42%</td></tr>
</tbody></table>
<table class="wikitable sortable" id="north-dakota">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Katrina Christiansen<br /><small>(D-NPL)</small></th>
<th>Kevin Cramer<br /><small>(R)</small></th></tr>
<tr><td>DFM Research</td><td>July 8–10, 2026</td><td>600 (LV)</td><td>38%</td><td>52%</td></tr>
</tbody></table>
<table class="wikitable sortable" id="unknown-suffix">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Dan Cox<br /><small>(R)</small></th><th>Jane Doe<br /><small>(WCP)</small></th></tr>
<tr><td>Emerson College</td><td>July 8–10, 2026</td><td>600 (LV)</td><td>47%</td><td>9%</td></tr>
</tbody></table>
</div></body></html>
"""


def _candidates(
    html: str, table_id: str, *, allow_party_labels: bool = False
) -> list[CandidateColumn]:
    """Return the candidate columns of a fixture table."""
    info = classify_table(_table(html, table_id))
    assert info is not None
    return candidate_columns(
        info.grid[info.columns.header_row], allow_party_labels=allow_party_labels
    )


def _label(html: str, table_id: str) -> str | None:
    """Return the matchup label of a fixture table."""
    return matchup_label(_candidates(html, table_id))


def _rows(
    html: str, table_id: str, *, allow_party_labels: bool = False
) -> tuple[list[ParsedPollRow], int]:
    """Return ``(rows, variants_dropped)`` for a fixture table."""
    info = classify_table(_table(html, table_id))
    assert info is not None
    return parse_table_rows(
        info,
        candidates=candidate_columns(
            info.grid[info.columns.header_row], allow_party_labels=allow_party_labels
        ),
    )


class TestPartySuffixes:
    def test_canonical_db_party_names(self) -> None:
        assert PARTY_SUFFIXES["R"] == "Republican"
        assert PARTY_SUFFIXES["D"] == "Democratic"
        assert PARTY_SUFFIXES["I"] == "Independent"
        assert PARTY_SUFFIXES["L"] == "Libertarian"
        assert PARTY_SUFFIXES["G"] == "US Green"

    def test_state_party_labels_are_democratic(self) -> None:
        # Minnesota's DFL and North Dakota's D-NPL are the state Democratic
        # parties, and their candidates are Democrats in the DB.
        assert PARTY_SUFFIXES["DFL"] == "Democratic"
        assert PARTY_SUFFIXES["D-NPL"] == "Democratic"

    def test_unknown_suffixes_are_absent(self) -> None:
        assert "IA" not in PARTY_SUFFIXES
        assert "WCP" not in PARTY_SUFFIXES


class TestSurname:
    def test_last_token(self) -> None:
        assert surname("Gavin Newsom") == "Newsom"

    def test_middle_initial(self) -> None:
        assert surname("Dan S. Sullivan") == "Sullivan"

    def test_apostrophe_name(self) -> None:
        assert surname("Beto O'Rourke") == "O'Rourke"

    def test_generational_suffixes_are_dropped(self) -> None:
        assert surname("Hal Rogers Jr.") == "Rogers"
        assert surname("Robert Kennedy Sr") == "Kennedy"
        assert surname("Hank Williams III") == "Williams"
        assert surname("John Doe IV") == "Doe"

    def test_comma_suffix(self) -> None:
        assert surname("Doe, Jr.") == "Doe"

    def test_footnotes_are_stripped(self) -> None:
        assert surname("Mary Peltola[3]") == "Peltola"

    def test_single_token_is_its_own_surname(self) -> None:
        assert surname("Osborn") == "Osborn"

    def test_empty(self) -> None:
        assert surname("") == ""


class TestCandidateColumns:
    def test_senate_header_text_is_the_full_name(self) -> None:
        # Senate and House headers are plain text split by <br>, so the header
        # itself carries the full name.
        columns = _candidates(SENATE_RACE_PAGE, "el-sayed-rogers")
        assert [(c.index, c.full_name, c.letter, c.party_name) for c in columns] == [
            (4, "Abdul El-Sayed", "D", "Democratic"),
            (5, "Mike Rogers", "R", "Republican"),
        ]
        assert [c.surname for c in columns] == ["El-Sayed", "Rogers"]

    def test_president_link_title_supplies_the_full_name(self) -> None:
        # The presidential page shows surnames only; the full name is in the
        # header link's title attribute.
        columns = _candidates(PRESIDENT_PAGE, "vance-newsom")
        assert [(c.full_name, c.surname, c.letter) for c in columns] == [
            ("JD Vance", "Vance", "R"),
            ("Gavin Newsom", "Newsom", "D"),
        ]

    def test_surname_only_header_without_a_link_stays_as_written(self) -> None:
        columns = _candidates(PRESIDENT_PAGE, "nevada-vance-newsom")
        assert [c.full_name for c in columns] == ["Vance", "Newsom"]

    def test_party_suffix_link_is_not_read_as_the_name(self) -> None:
        # Texas links both the candidate and the "(R)" to articles; only the
        # candidate's link names the candidate.
        columns = _candidates(TEXAS_PAGE, "paxton-talarico")
        assert [c.full_name for c in columns] == ["Ken Paxton", "James Talarico"]
        assert [c.party_name for c in columns] == ["Republican", "Democratic"]

    def test_non_candidate_headers_are_skipped(self) -> None:
        columns = _candidates(SENATE_RACE_PAGE, "el-sayed-rogers")
        assert [c.index for c in columns] == [4, 5]

    def test_state_party_suffixes(self) -> None:
        assert [
            (c.letter, c.party_name) for c in _candidates(MINOR_SUFFIX_PAGE, "minnesota")
        ] == [("DFL", "Democratic"), ("R", "Republican")]
        assert [
            (c.letter, c.party_name) for c in _candidates(MINOR_SUFFIX_PAGE, "north-dakota")
        ] == [("D-NPL", "Democratic"), ("R", "Republican")]

    def test_unknown_suffix_keeps_its_letter_and_has_no_party(self) -> None:
        columns = _candidates(MINOR_SUFFIX_PAGE, "unknown-suffix")
        assert columns[1].letter == "WCP"
        assert columns[1].party_name is None
        assert columns[1].full_name == "Jane Doe"

    def test_party_labels_need_the_flag(self) -> None:
        assert _candidates(HOUSE_INDEX_PAGE, "generic-ballot") == []

    def test_party_labels_name_no_candidate(self) -> None:
        columns = _candidates(HOUSE_INDEX_PAGE, "generic-ballot", allow_party_labels=True)
        assert [(c.index, c.letter, c.party_name) for c in columns] == [
            (3, "R", "Republican"),
            (4, "D", "Democratic"),
        ]
        assert [(c.full_name, c.surname) for c in columns] == [("", ""), ("", "")]

    def test_party_label_flag_does_not_disturb_candidate_headers(self) -> None:
        with_flag = _candidates(TEXAS_PAGE, "paxton-talarico", allow_party_labels=True)
        assert with_flag == _candidates(TEXAS_PAGE, "paxton-talarico")


class TestMatchupLabel:
    def test_president_nationwide(self) -> None:
        assert _label(PRESIDENT_PAGE, "vance-newsom") == "Vance (R) vs Newsom (D)"

    def test_senate_race(self) -> None:
        assert _label(TEXAS_PAGE, "paxton-talarico") == "Paxton (R) vs Talarico (D)"

    def test_republican_against_an_independent(self) -> None:
        assert _label(NEBRASKA_PAGE, "ricketts-osborn") == "Ricketts (R) vs Osborn (I)"

    def test_same_party_top_two(self) -> None:
        assert _label(HOUSE_STATE_PAGE, "ca-40") == "Calvert (R) vs Kim (R)"

    def test_alaska_shared_surname_uses_full_names(self) -> None:
        # Both Dan Sullivans fall back to their full header name; the other two
        # candidates keep their surname.
        assert _label(ALASKA_PAGE, "alaska") == (
            "Dan S. Sullivan (R) vs Dan J. Sullivan (R) vs Heikes (R) vs Peltola (D)"
        )

    def test_democrat_first_gives_the_same_label(self) -> None:
        # Michigan lists the Democrat first; the label must still lead with R.
        assert _label(SENATE_RACE_PAGE, "el-sayed-rogers") == "Rogers (R) vs El-Sayed (D)"
        reversed_columns = list(reversed(_candidates(SENATE_RACE_PAGE, "el-sayed-rogers")))
        assert matchup_label(reversed_columns) == "Rogers (R) vs El-Sayed (D)"

    def test_state_party_label_keeps_its_letter(self) -> None:
        assert _label(MINOR_SUFFIX_PAGE, "minnesota") == "Tafoya (R) vs Flanagan (DFL)"

    def test_unknown_suffix_sorts_last_and_keeps_its_letter(self) -> None:
        assert _label(MINOR_SUFFIX_PAGE, "unknown-suffix") == "Cox (R) vs Doe (WCP)"

    def test_party_order(self) -> None:
        columns = [
            CandidateColumn(0, "US Green", "G", "Green", "Gina Green"),
            CandidateColumn(1, None, "IA", "Other", "Olive Other"),
            CandidateColumn(2, "Libertarian", "L", "Lark", "Lee Lark"),
            CandidateColumn(3, "Independent", "I", "Ives", "Ida Ives"),
            CandidateColumn(4, "Democratic", "D", "Dean", "Dana Dean"),
            CandidateColumn(5, "Republican", "R", "Ruiz", "Rosa Ruiz"),
        ]
        assert matchup_label(columns) == (
            "Ruiz (R) vs Dean (D) vs Ives (I) vs Lark (L) vs Green (G) vs Other (IA)"
        )

    def test_single_candidate_has_no_matchup(self) -> None:
        assert matchup_label(_candidates(PRESIDENT_PAGE, "vance-newsom")[:1]) is None

    def test_no_candidates_has_no_matchup(self) -> None:
        assert matchup_label([]) is None

    def test_generic_ballot_party_columns_have_no_matchup(self) -> None:
        assert (
            matchup_label(
                _candidates(HOUSE_INDEX_PAGE, "generic-ballot", allow_party_labels=True)
            )
            is None
        )


class TestCleanPollsterLabel:
    def test_plain_label(self) -> None:
        assert clean_pollster_label("Emerson College") == ("Emerson College", ())

    def test_partisan_tag_is_stripped_and_returned(self) -> None:
        label, tags = clean_pollster_label("Rasmussen Reports (R)")
        assert (label, tags) == ("Rasmussen Reports", ("R",))
        assert clean_pollster_label("GQR (D)") == ("GQR", ("D",))

    def test_joint_sponsors_keep_both_names(self) -> None:
        assert clean_pollster_label("Fabrizio Ward (R)/ Impact Research (D)") == (
            "Fabrizio Ward/ Impact Research",
            ("R", "D"),
        )

    def test_footnote_markers_are_stripped(self) -> None:
        assert clean_pollster_label("Glengariff Group [41]") == ("Glengariff Group", ())

    def test_bracket_reference_is_stripped(self) -> None:
        assert clean_pollster_label("Emerson College [ai]") == ("Emerson College", ())

    def test_slash_inside_a_name_is_left_alone(self) -> None:
        assert clean_pollster_label("co/efficient") == ("co/efficient", ())

    def test_empty_cell(self) -> None:
        assert clean_pollster_label("") == ("", ())


class TestParseTableRows:
    def test_percentage_formats(self) -> None:
        # <b>49%</b> and "41% [k]" both read as numbers.
        rows, _ = _rows(TEXAS_PAGE, "paxton-talarico")
        assert [(r.candidate_name, r.percentage) for r in rows[0].readings] == [
            ("Ken Paxton", 49.0),
            ("James Talarico", 41.0),
        ]

    def test_missing_reading_is_absent_not_zero(self) -> None:
        # The "with leaners" variant shows "—" for Other; drop the dedupe by
        # parsing that row's table and checking a candidate cell directly.
        info = classify_table(
            _table(
                "<table id='t'><tr><th>Poll source</th><th>Date(s) administered</th>"
                "<th>Jane Roe<br /><small>(D)</small></th>"
                "<th>John Doe<br /><small>(R)</small></th></tr>"
                "<tr><td>Emerson</td><td>June 3, 2026</td><td>47%</td><td>—</td></tr></table>",
                "t",
            )
        )
        assert info is not None
        rows, _ = parse_table_rows(info)
        assert [(r.candidate_name, r.percentage) for r in rows[0].readings] == [
            ("Jane Roe", 47.0),
        ]

    @pytest.mark.parametrize("cell", ["inf", "-inf", "nan", "1e400", "150%", "-5%"])
    def test_a_reading_must_be_a_finite_percentage(self, cell: str) -> None:
        info = classify_table(
            _table(
                "<table id='t'><tr><th>Poll source</th><th>Date(s) administered</th>"
                "<th>Jane Roe<br /><small>(D)</small></th>"
                "<th>John Doe<br /><small>(R)</small></th></tr>"
                f"<tr><td>Emerson</td><td>June 3, 2026</td><td>47%</td><td>{cell}</td></tr>"
                "</table>",
                "t",
            )
        )
        assert info is not None
        rows, _ = parse_table_rows(info)
        assert [(r.candidate_name, r.percentage) for r in rows[0].readings] == [
            ("Jane Roe", 47.0),
        ]

    def test_readings_at_the_percentage_bounds_are_kept(self) -> None:
        info = classify_table(
            _table(
                "<table id='t'><tr><th>Poll source</th><th>Date(s) administered</th>"
                "<th>Jane Roe<br /><small>(D)</small></th>"
                "<th>John Doe<br /><small>(R)</small></th></tr>"
                "<tr><td>Emerson</td><td>June 3, 2026</td><td>100%</td><td>0%</td></tr>"
                "</table>",
                "t",
            )
        )
        assert info is not None
        rows, _ = parse_table_rows(info)
        assert [r.percentage for r in rows[0].readings] == [100.0, 0.0]

    def test_sample_size_and_population(self) -> None:
        rows, _ = _rows(PRESIDENT_PAGE, "vance-newsom")
        assert (rows[0].sample_size, rows[0].population) == (1000, "LV")

    def test_sample_population_of_a_registered_voter_poll(self) -> None:
        rows, _ = _rows(PRESIDENT_PAGE, "nevada-vance-newsom")
        assert (rows[0].sample_size, rows[0].population) == (600, "RV")

    def test_partisan_tags_are_kept_on_the_row(self) -> None:
        rows, _ = _rows(TEXAS_PAGE, "paxton-talarico")
        assert [(r.pollster_label, r.pollster_tags) for r in rows] == [
            ("Fabrizio Ward/ Impact Research", ("R", "D")),
            ("Rasmussen Reports", ("R",)),
            ("GQR", ("D",)),
        ]

    def test_dates_and_raw_label(self) -> None:
        rows, _ = _rows(TEXAS_PAGE, "paxton-talarico")
        assert rows[0].fieldwork_start == date(2026, 9, 2)
        assert rows[0].fieldwork_end == date(2026, 9, 4)
        assert rows[0].date_label == "September 2–4, 2026"

    def test_michigan_rowspan_variant_is_dropped(self) -> None:
        # Two rows share the rowspanned pollster and dates (LV, then "with
        # leaners"); the first wins and the second is counted.
        rows, dropped = _rows(SENATE_RACE_PAGE, "el-sayed-rogers")
        assert dropped == 1
        assert [(r.pollster_label, r.fieldwork_start) for r in rows] == [
            ("Glengariff Group", date(2026, 6, 1)),
            ("Marketing Resource Group", date(2026, 9, 8)),
        ]
        assert [reading.percentage for reading in rows[0].readings] == [44.0, 45.0]

    def test_colspan_event_row_is_skipped(self) -> None:
        # '' | August 18, 2026 | Primary election held — a parseable date, but
        # no pollster and no numbers.
        rows, _ = _rows(SENATE_RACE_PAGE, "el-sayed-rogers")
        assert all(r.date_label != "August 18, 2026" for r in rows)

    def test_row_without_any_reading_is_skipped(self) -> None:
        rows, dropped = _rows(NEBRASKA_PAGE, "ricketts-osborn")
        assert dropped == 0
        assert [r.pollster_label for r in rows] == ["Change Research"]

    def test_alaska_variant_dedupe(self) -> None:
        rows, dropped = _rows(ALASKA_PAGE, "alaska")
        assert dropped == 1
        assert len(rows) == 1
        percentages = [reading.percentage for reading in rows[0].readings]
        assert percentages == [40.0, 44.0, 3.0, 2.0]

    def test_external_pollster_link_becomes_the_source_url(self) -> None:
        rows, _ = _rows(PRESIDENT_PAGE, "vance-newsom")
        assert rows[0].source_url == "https://example.invalid/poll"

    def test_wiki_internal_pollster_link_is_not_a_source(self) -> None:
        rows, _ = _rows(SENATE_RACE_PAGE, "el-sayed-rogers")
        assert rows[0].source_url is None

    def test_generic_ballot_rows_carry_parties_without_candidates(self) -> None:
        rows, dropped = _rows(HOUSE_INDEX_PAGE, "generic-ballot", allow_party_labels=True)
        assert dropped == 0
        assert rows[0].pollster_label == "Decision Desk HQ"
        # 'Dates updated' is the snapshot the aggregator row reports.
        assert rows[0].fieldwork_end == date(2026, 6, 29)
        readings = [(r.party_name, r.candidate_name, r.percentage) for r in rows[0].readings]
        assert readings == [
            ("Republican", "", 40.1),
            ("Democratic", "", 44.3),
        ]
        assert rows[0].sample_size is None

    def test_candidates_default_to_the_header_row(self) -> None:
        info = classify_table(_table(TEXAS_PAGE, "paxton-talarico"))
        assert info is not None
        rows, _ = parse_table_rows(info)
        assert len(rows[0].readings) == 2

    def test_unknown_suffix_reading_has_no_party(self) -> None:
        rows, _ = _rows(MINOR_SUFFIX_PAGE, "unknown-suffix")
        assert [(r.party_name, r.candidate_name) for r in rows[0].readings] == [
            ("Republican", "Dan Cox"),
            (None, "Jane Doe"),
        ]


class TestParsePollTables:
    def test_president_page_tables_in_document_order(self) -> None:
        tables = parse_poll_tables(PRESIDENT_PAGE).tables
        assert [table.matchup for table in tables] == [
            "Vance (R) vs Rubio (R)",
            "Vance (R) vs Newsom (D)",
            "Vance (R) vs Harris (D)",
            "Vance (R) vs Newsom (D)",
        ]

    def test_contest_rules_are_not_applied_here(self) -> None:
        # The primary table, the collapsed hypothetical and the statewide table
        # all come back; accepts_table decides which of them a contest wants.
        tables = parse_poll_tables(PRESIDENT_PAGE).tables
        assert [table.info.collapsed for table in tables] == [False, False, True, False]
        assert [table.headings[-1].text for table in tables] == [
            "Nationwide",
            "JD Vance vs. Gavin Newsom",
            "Hypothetical polling",
            "JD Vance vs. Gavin Newsom",
        ]
        assert [heading.text for heading in tables[3].headings] == [
            "Opinion polling",
            "General election",
            "Statewide",
            "Nevada",
            "JD Vance vs. Gavin Newsom",
        ]

    def test_senate_race_page(self) -> None:
        tables = parse_poll_tables(SENATE_RACE_PAGE).tables
        # The Predictions table is rejected, and the aggregation table names
        # parties rather than candidates, so it yields no rows without the flag.
        assert [table.matchup for table in tables] == [
            "Rogers (R) vs El-Sayed (D)",
            "Rogers (R) vs Stevens (D)",
        ]
        assert tables[0].variants_dropped == 1
        assert tables[0].info.collapsed is False
        assert tables[1].info.collapsed is True

    def test_generic_ballot_aggregation_table(self) -> None:
        tables = parse_poll_tables(HOUSE_INDEX_PAGE, allow_party_labels=True).tables
        assert len(tables) == 1
        assert tables[0].matchup is None
        assert tables[0].info.columns.is_aggregation is True
        assert [column.party_name for column in tables[0].candidates] == [
            "Republican",
            "Democratic",
        ]

    def test_unknown_suffixes_are_surfaced(self) -> None:
        tables = parse_poll_tables(MINOR_SUFFIX_PAGE).tables
        assert [table.unknown_suffixes for table in tables] == [(), (), ("WCP",)]
        assert [table.matchup for table in tables] == [
            "Tafoya (R) vs Flanagan (DFL)",
            "Cramer (R) vs Christiansen (D-NPL)",
            "Cox (R) vs Doe (WCP)",
        ]

    def test_headings_are_lifted_onto_the_parsed_table(self) -> None:
        tables = parse_poll_tables(ALASKA_PAGE).tables
        assert tables[0].headings == tables[0].info.headings
        assert [heading.text for heading in tables[0].headings] == [
            "General election",
            "Polling",
        ]

    def test_tables_without_a_parseable_row_are_dropped(self) -> None:
        # That page's primary table is a header with no data rows under it.
        tables = parse_poll_tables(PRESIDENT_PAGE_NO_SECTIONS).tables
        assert len(tables) == 1
        assert tables[0].matchup == "Vance (R) vs Newsom (D)"

    def test_keep_empty_returns_the_dropped_tables(self) -> None:
        # The contest layer reports a classified table that yielded no rows —
        # it is how Wikipedia markup drift becomes visible.
        tables = parse_poll_tables(PRESIDENT_PAGE_NO_SECTIONS, keep_empty=True).tables
        assert len(tables) == 2
        empty = next(table for table in tables if not table.rows)
        assert empty.headings[-1].text == "Republican primary"

    def test_page_without_polling_tables(self) -> None:
        assert parse_poll_tables(SENATE_SEATS_TABLE).tables == ()

    def test_an_oversized_table_is_reported_by_its_heading_path(self) -> None:
        html = (
            "<h2 id='General_election'>General election</h2>"
            "<h3 id='Polling'>Polling</h3>" + _too_wide_table("t")
        )
        page = parse_poll_tables(html)
        assert page.tables == ()
        assert [[heading.text for heading in path] for path in page.oversized] == [
            ["General election", "Polling"],
        ]

    def test_an_empty_table_is_not_oversized(self) -> None:
        page = parse_poll_tables("<table id='t'></table>")
        assert (page.tables, page.oversized) == ((), ())
