"""Tests for the US contest layer: page discovery, section/seat rules, rows.

All HTML here is synthetic, copied from the live Wikipedia shapes described in
the plan. Nothing touches the network (every fetch goes through
:class:`FakeFetcher`) or the live database (the ``db`` fixture builds a fresh
temporary one per test).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping
from datetime import date
from email.message import Message
from urllib.error import HTTPError

import pytest
from sqlalchemy.exc import IntegrityError

import polls.importers.us.us_wikipedia_polls as us_wikipedia_polls
from db import Database
from polls.importers.us.us_polls_common import (
    CandidateReading,
    PageTables,
    parse_poll_tables,
)
from polls.importers.us.us_wikipedia_polls import (
    HOUSE_DISTRICTS,
    HOUSE_INDEX_URL,
    HOUSE_NATIONAL,
    PRESIDENT,
    PRESIDENT_PAGE_URLS,
    SENATE_INDEX_URL,
    SENATE_RACES,
    US_CONTESTS,
    US_CONTESTS_BY_SLUG,
    NoMatchupTable,
    OversizedTable,
    UsContest,
    UsPollRow,
    apply_auto_tracked_matchups,
    build_us_import_plan,
    commit_us_import_plan,
    discover_house_pages,
    discover_senate_pages,
    fetch_pages,
    fetch_us_poll_index,
    normalise_states,
    rows_for_page,
    run_importer,
)

WIKI = "https://en.wikipedia.org/wiki"

MICHIGAN_URL = f"{WIKI}/2026_United_States_Senate_election_in_Michigan"
NEBRASKA_URL = f"{WIKI}/2026_United_States_Senate_election_in_Nebraska"
ALASKA_URL = f"{WIKI}/2026_United_States_Senate_election_in_Alaska"
DELAWARE_URL = f"{WIKI}/2026_United_States_Senate_election_in_Delaware"
FLORIDA_URL = f"{WIKI}/2026_United_States_Senate_election_in_Florida"
FLORIDA_SPECIAL_URL = f"{WIKI}/2026_United_States_Senate_special_election_in_Florida"
TEXAS_SENATE_URL = f"{WIKI}/2026_United_States_Senate_election_in_Texas"

# Other spellings of Texas's Senate page: each resolves to Texas.
TEXAS_SENATE_VARIANT_URLS = (
    f"{WIKI}/2026_United_States_Senate_election_in__Texas",
    f"{WIKI}/2026_United_States_Senate_election_in_texas",
    f"{WIKI}/2026_United_States_Senate_election_in_x/Texas",
)

CALIFORNIA_URL = f"{WIKI}/2026_United_States_House_of_Representatives_elections_in_California"
TEXAS_URL = f"{WIKI}/2026_United_States_House_of_Representatives_elections_in_Texas"
VERMONT_URL = f"{WIKI}/2026_United_States_House_of_Representatives_election_in_Vermont"

PRESIDENT_URL = PRESIDENT_PAGE_URLS[0]
PRESIDENT_STATEWIDE_URL = PRESIDENT_PAGE_URLS[1]


# ── Fixture pages ─────────────────────────────────────────────────────────────

# The 2028 nationwide presidential article: a primary section, the nationwide
# matchup tables (the pollster cell links to the poll itself), a collapsed
# hypothetical, and the Statewide › Nevada › matchup h6 path.
PRESIDENT_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="Opinion_polling">Opinion polling</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Republican_primary">Republican primary</h3></div>
<div class="mw-heading mw-heading4"><h4 id="Nationwide_primary">Nationwide</h4></div>
<table class="wikitable sortable" id="primary-nationwide">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>JD Vance<br /><small>(R)</small></th><th>Marco Rubio<br /><small>(R)</small></th></tr>
<tr><td>Emerson College</td><td>June 24–26, 2026</td><td>1,000 (RV)</td>
<td>52%</td><td>21%</td></tr>
</tbody></table>
<div class="mw-heading mw-heading3"><h3 id="General_election">General election</h3></div>
<div class="mw-heading mw-heading4"><h4 id="Nationwide">Nationwide</h4></div>
<div class="mw-heading mw-heading5"><h5 id="JD_Vance_vs._Gavin_Newsom">JD Vance vs. Gavin Newsom</h5></div>
<table class="wikitable sortable" id="vance-newsom">
<tbody>
<tr>
<th rowspan="2">Poll source</th><th rowspan="2">Date(s)<br />administered</th>
<th rowspan="2">Sample<br />size</th><th rowspan="2">Margin<br />of error</th>
<th><a href="/wiki/JD_Vance" title="JD Vance">Vance</a><br /><small>(R)</small></th>
<th><a href="/wiki/Gavin_Newsom" title="Gavin Newsom">Newsom</a><br /><small>(D)</small></th>
<th rowspan="2">Undecided</th>
</tr>
<tr><td style="background-color:#E81B23;"></td><td style="background-color:#3333FF;"></td></tr>
<tr>
<td><a rel="nofollow" class="external text" href="https://example.invalid/poll">Emerson College</a></td>
<td>June 24–26, 2026</td><td>1,000 (LV)</td><td>± 3.0%</td>
<td><b>45%</b></td><td>44%</td><td>11%</td>
</tr>
</tbody></table>
<div class="mw-heading mw-heading5"><h5 id="JD_Vance_vs._Josh_Shapiro">JD Vance vs. Josh Shapiro</h5></div>
<table class="wikitable sortable" id="vance-shapiro">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th><a href="/wiki/JD_Vance" title="JD Vance">Vance</a><br /><small>(R)</small></th>
<th><a href="/wiki/Josh_Shapiro" title="Josh Shapiro">Shapiro</a><br /><small>(D)</small></th></tr>
<tr><td>Emerson College</td><td>June 24–26, 2026</td><td>1,000 (LV)</td>
<td>46%</td><td>43%</td></tr>
</tbody></table>
<div class="mw-heading mw-heading5"><h5 id="Hypothetical_polling">Hypothetical polling</h5></div>
<div class="mw-collapsible mw-made-collapsible"><div class="mw-collapsible-content">
<table class="wikitable sortable" id="vance-harris">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Vance<br /><small>(R)</small></th><th>Harris<br /><small>(D)</small></th></tr>
<tr><td>Emerson College</td><td>June 24–26, 2026</td><td>1,000 (LV)</td>
<td>47%</td><td>42%</td></tr>
</tbody></table>
</div></div>
<div class="mw-heading mw-heading4"><h4 id="Statewide">Statewide</h4></div>
<div class="mw-heading mw-heading5"><h5 id="Nevada">Nevada</h5></div>
<div class="mw-heading mw-heading6"><h6 id="JD_Vance_vs._Gavin_Newsom_2">JD Vance vs. Gavin Newsom</h6></div>
<table class="wikitable sortable" id="nevada-vance-newsom">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Vance<br /><small>(R)</small></th><th>Newsom<br /><small>(D)</small></th></tr>
<tr><td>Noble Predictive Insights</td><td>May 28 – June 3, 2026</td><td>600 (RV)</td>
<td>44%</td><td><b>46%</b></td></tr>
</tbody></table>
<div class="mw-heading mw-heading5"><h5 id="Fremont">Fremont</h5></div>
<div class="mw-heading mw-heading6"><h6 id="JD_Vance_vs._Gavin_Newsom_3">JD Vance vs. Gavin Newsom</h6></div>
<table class="wikitable sortable" id="fremont-vance-newsom">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Vance<br /><small>(R)</small></th><th>Newsom<br /><small>(D)</small></th></tr>
<tr><td>Fremont Research</td><td>May 1–3, 2026</td><td>500 (RV)</td>
<td>44%</td><td>46%</td></tr>
</tbody></table>
</div></body></html>
"""

# Michigan's Senate race: a Predictions table, the aggregation table, the
# rowspan-heavy nominee table and a collapsed hypothetical.
MICHIGAN_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="Predictions">Predictions</h2></div>
<table class="wikitable" id="predictions">
<tbody><tr><th>Source</th><th>Ranking</th><th>As of</th></tr>
<tr><td>The Cook Political Report</td><td>Tossup</td><td>June 3, 2026</td></tr>
</tbody></table>
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<table class="wikitable sortable" id="senate-aggregation">
<tbody><tr>
<th>Source of poll<br />aggregation</th><th>Dates<br />administered</th><th>Dates<br />updated</th>
<th>Republicans</th><th>Democrats</th><th>Margin</th></tr>
<tr><td>Decision Desk HQ</td><td>January 9, 2025 – June 29, 2026</td><td>June 29, 2026</td>
<td>40.1%</td><td>44.3%</td><td>Democrats +4.2%</td></tr>
</tbody></table>
<table class="wikitable sortable" id="el-sayed-rogers">
<tbody>
<tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Margin<br />of error</th>
<th>Abdul El-Sayed<br /><small>(D)</small></th><th>Mike Rogers<br /><small>(R)</small></th>
<th>Undecided</th></tr>
<tr>
<td rowspan="2">Glengariff Group<sup class="reference">[41]</sup></td>
<td rowspan="2">June 1–4, 2026</td>
<td>600 (LV)</td><td>± 4.0%</td><td>44%</td><td><b>45%</b></td><td>8%</td></tr>
<tr><td>600 (LV) with leaners</td><td>± 4.0%</td><td>46%</td><td><b>47%</b></td><td>7%</td></tr>
<tr><td></td><td>August 18, 2026</td><td colspan="5">Primary election held</td></tr>
<tr>
<td>Marketing Resource Group (R)</td><td>September 8–11, 2026</td><td>600 (LV)</td>
<td>± 4.0%</td><td>42%</td><td><b>46%</b></td><td>8%</td></tr>
</tbody></table>
<div class="mw-collapsible mw-collapsed"><div class="mw-collapsible-content">
<table class="wikitable sortable" id="stevens-rogers">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Haley Stevens<br /><small>(D)</small></th><th>Mike Rogers<br /><small>(R)</small></th></tr>
<tr><td>Glengariff Group</td><td>June 1–4, 2026</td><td>600 (LV)</td><td>41%</td><td>44%</td></tr>
</tbody></table>
</div></div>
</div></body></html>
"""

# Nebraska: the visible lead table is Republican vs Independent; the Democratic
# pairings are collapsed, so they must not be imported or become the lead.
NEBRASKA_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<table class="wikitable sortable" id="ricketts-osborn">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Pete Ricketts<br /><small>(R)</small></th><th>Dan Osborn<br /><small>(I)</small></th></tr>
<tr><td>Change Research (D)</td><td>July 1–3, 2026</td><td>800 (LV)</td>
<td><b>48%</b></td><td>44%</td></tr>
</tbody></table>
<div class="mw-collapsible mw-collapsed"><div class="mw-collapsible-content">
<table class="wikitable sortable" id="ricketts-democrat">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Pete Ricketts<br /><small>(R)</small></th><th>Jane Kleeb<br /><small>(D)</small></th></tr>
<tr><td>Change Research (D)</td><td>July 1–3, 2026</td><td>800 (LV)</td><td>55%</td><td>38%</td></tr>
</tbody></table>
</div></div>
</div></body></html>
"""

# Alaska's top-four table: two candidates share a surname, and an unknown
# suffix rides along on a fourth column.
ALASKA_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<table class="wikitable sortable" id="alaska">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Dan S. Sullivan<br /><small>(R)</small></th><th>Mary Peltola<br /><small>(D)</small></th>
<th>Dan J. Sullivan<br /><small>(R)</small></th><th>Gerald Heikes<br /><small>(WCP)</small></th>
<th>Undecided</th></tr>
<tr>
<td rowspan="2">Alaska Survey Research</td><td rowspan="2">July 7–9, 2026</td>
<td rowspan="2">1,203 (LV)</td>
<td>40%</td><td><b>44%</b></td><td>3%</td><td>2%</td><td>11%</td></tr>
<tr><td>43%</td><td><b>47%</b></td><td>4%</td><td>2%</td><td>4%</td></tr>
</tbody></table>
</div></body></html>
"""

# Delaware has no visible general-election table at all: only a hypothetical.
DELAWARE_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<div class="mw-collapsible mw-collapsed"><div class="mw-collapsible-content">
<table class="wikitable sortable" id="delaware-hypothetical">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Chris Coons<br /><small>(D)</small></th><th>Jane Doe<br /><small>(R)</small></th></tr>
<tr><td>Emerson College</td><td>April 2–4, 2026</td><td>500 (LV)</td><td>58%</td><td>35%</td></tr>
</tbody></table>
</div></div>
</div></body></html>
"""


# Nebraska with a single-candidate general-election table: it forms no matchup,
# so none of its rows can be used by the Senate model.
NEBRASKA_SINGLE_CANDIDATE_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<table class="wikitable sortable" id="ricketts-only">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Pete Ricketts<br /><small>(R)</small></th><th>Undecided</th></tr>
<tr><td>Change Research</td><td>July 1–3, 2026</td><td>800 (LV)</td>
<td>48%</td><td>12%</td></tr>
</tbody></table>
</div></body></html>
"""

# A nationwide presidential table naming one candidate. Stored, it would be a
# national poll with neither seat nor matchup — the legacy poll shape.
PRESIDENT_SINGLE_CANDIDATE_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="Opinion_polling">Opinion polling</h2></div>
<div class="mw-heading mw-heading3"><h3 id="General_election">General election</h3></div>
<div class="mw-heading mw-heading4"><h4 id="Nationwide">Nationwide</h4></div>
<table class="wikitable sortable" id="vance-only">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Vance<br /><small>(R)</small></th><th>Undecided</th></tr>
<tr><td>Emerson College</td><td>June 24–26, 2026</td><td>1,000 (LV)</td>
<td>45%</td><td>55%</td></tr>
</tbody></table>
</div></body></html>
"""

# Delaware's only table is a hidden single-candidate hypothetical.
DELAWARE_SINGLE_CANDIDATE_PAGE = DELAWARE_PAGE.replace(
    "<th>Jane Doe<br /><small>(R)</small></th>", "<th>Undecided</th>"
)

# Michigan with a table far too wide to expand: one row of 1,000 cells, each
# spanning 64 columns.
MICHIGAN_OVERSIZED_PAGE = (
    '<html><body><div class="mw-parser-output">'
    '<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>'
    '<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>'
    "<table class='wikitable'><tr><th>Poll source</th><th>Date(s) administered</th></tr>"
    "<tr>" + "<td colspan='64'>x</td>" * 1000 + "</tr></table>"
    "</div></body></html>"
)


def _senate_state_page(candidate_r: str, candidate_d: str) -> str:
    """A minimal Senate race page with one visible general-election table."""
    return f"""
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<table class="wikitable sortable">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>{candidate_r}<br /><small>(R)</small></th><th>{candidate_d}<br /><small>(D)</small></th></tr>
<tr><td>Emerson College</td><td>June 1–3, 2026</td><td>700 (LV)</td><td>49%</td><td>45%</td></tr>
</tbody></table>
</div></body></html>
"""


# A multi-district House page: District 3's primary and general sections, plus
# CA-40's "Primary › General election" oddity.
CALIFORNIA_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="District_3">District 3</h2></div>
<table class="wikitable" id="candidate-list">
<tbody><tr><th>District</th><th>Incumbent</th><th>Candidates</th><th>Filing deadline</th></tr>
<tr><td>3rd</td><td>Jane Roe (D)</td><td>Jane Roe (D)</td><td>June 3, 2026</td></tr>
</tbody></table>
<div class="mw-heading mw-heading3"><h3 id="Democratic_primary">Democratic primary</h3></div>
<div class="mw-heading mw-heading4"><h4 id="Polling">Polling</h4></div>
<table class="wikitable sortable" id="district-3-primary">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Jane Roe<br /><small>(D)</small></th><th>Ann Lee<br /><small>(D)</small></th></tr>
<tr><td>Public Policy Polling</td><td>April 2–3, 2026</td><td>500 (LV)</td><td>52%</td><td>30%</td></tr>
</tbody></table>
<div class="mw-heading mw-heading3"><h3 id="General_election">General election</h3></div>
<div class="mw-heading mw-heading4"><h4 id="Polling_2">Polling</h4></div>
<table class="wikitable sortable" id="district-3-general">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Jane Roe<br /><small>(D)</small></th><th>John Doe<br /><small>(R)</small></th></tr>
<tr><td>Public Policy Polling</td><td>September 2–3, 2026</td><td>500 (LV)</td><td>49%</td><td>45%</td></tr>
</tbody></table>
<div class="mw-heading mw-heading2"><h2 id="District_40">District 40</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Primary">Primary</h3></div>
<div class="mw-heading mw-heading4"><h4 id="General_election_2">General election</h4></div>
<table class="wikitable sortable" id="ca-40">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Ken Calvert<br /><small>(R)</small></th><th>Young Kim<br /><small>(R)</small></th></tr>
<tr><td>co/efficient</td><td>March 10–12, 2026</td><td>400 (LV)</td><td>38%</td><td>34%</td></tr>
</tbody></table>
</div></body></html>
"""

# A multi-district page whose general-election table sits under no "District N"
# heading, and one whose every row is unparseable.
TEXAS_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<table class="wikitable sortable" id="texas-orphan">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Jane Roe<br /><small>(D)</small></th><th>John Doe<br /><small>(R)</small></th></tr>
<tr><td>Emerson College</td><td>June 1–3, 2026</td><td>700 (LV)</td><td>49%</td><td>45%</td></tr>
</tbody></table>
<div class="mw-heading mw-heading2"><h2 id="District_2">District 2</h2></div>
<div class="mw-heading mw-heading3"><h3 id="General_election_2">General election</h3></div>
<table class="wikitable sortable" id="texas-2-drifted">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Ann Lee<br /><small>(D)</small></th><th>Bob Fox<br /><small>(R)</small></th></tr>
<tr><td>Emerson College</td><td>shortly before the primary</td><td>700 (LV)</td>
<td>49%</td><td>45%</td></tr>
</tbody></table>
</div></body></html>
"""

# Vermont's at-large shape: the polling table hangs off "Predictions", and the
# page carries no "District N" heading at all.
VERMONT_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Predictions">Predictions</h3></div>
<div class="mw-heading mw-heading4"><h4 id="Polling">Polling</h4></div>
<table class="wikitable sortable" id="vermont">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Becca Balint<br /><small>(D)</small></th><th>Mark Coester<br /><small>(R)</small></th></tr>
<tr><td>UNH Survey Center</td><td>August 1–5, 2026</td><td>700 (LV)</td><td>61%</td><td>28%</td></tr>
</tbody></table>
</div></body></html>
"""

_HOUSE_INDEX_HEAD = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="Out-of-cycle_partisan_redistricting_efforts">
Out-of-cycle partisan redistricting efforts</h2></div>
<table class="wikitable" id="redistricting">
<tbody><tr><th>State</th><th>D</th><th>C</th><th>R</th><th>Signed into law</th></tr>
<tr><td>Texas</td><td>0</td><td>0</td><td>5</td><td>August 22, 2025</td></tr>
</tbody></table>
<div class="mw-heading mw-heading2"><h2 id="Generic_congressional_ballot_aggregate_polls">
Generic congressional ballot aggregate polls</h2></div>
<table class="wikitable sortable" id="generic-ballot">
<tbody><tr>
<th>Source of poll<br />aggregation</th><th>Dates<br />administered</th><th>Dates<br />updated</th>
<th>Republicans</th><th>Democrats</th><th>Other/<br />Undecided</th><th>Margin</th></tr>
<tr><td>Decision Desk HQ</td><td>January 9, 2025 – June 29, 2026</td><td>June 29, 2026</td>
<td>40.1%</td><td>44.3%</td><td>15.6%</td><td>Democrats +4.2%</td></tr>
<tr><td>The Economist</td><td>January 9, 2025 – June 28, 2026</td><td>June 28, 2026</td>
<td>41.0%</td><td>45.0%</td><td>14.0%</td><td>Democrats +4.0%</td></tr>
<tr><td><b>Average</b></td><td>January 9, 2025 – June 29, 2026</td><td>June 29, 2026</td>
<td>40.6%</td><td>44.7%</td><td>14.8%</td><td>Democrats +4.1%</td></tr>
</tbody></table>
<div class="mw-heading mw-heading2"><h2 id="By_state">By state</h2></div>
"""

_INDEX_TAIL = "</div></body></html>"


def _link(url: str, text: str) -> str:
    """Render an index link the way Wikipedia serves them: absolute href."""
    return f'<a href="{url}" title="{text}">{text}</a>\n'


def _house_index_page() -> str:
    """Build the House index: 44 multi-district states, 6 at-large, plus noise."""
    from polls.importers.us.us_geography import AT_LARGE_STATES, HOUSE_DISTRICT_COUNTS

    links: list[str] = []
    for state in HOUSE_DISTRICT_COUNTS:
        slug = state.replace(" ", "_")
        if state in AT_LARGE_STATES:
            links.append(
                _link(
                    f"{WIKI}/2026_United_States_House_of_Representatives_election_in_{slug}",
                    state,
                )
            )
        else:
            links.append(
                _link(
                    f"{WIKI}/2026_United_States_House_of_Representatives_elections_in_{slug}",
                    state,
                )
            )
    for territory in (
        "the_District_of_Columbia",
        "Guam",
        "American_Samoa",
        "Puerto_Rico",
        "the_United_States_Virgin_Islands",
        "the_Northern_Mariana_Islands",
        "the_Northern_Mariana_Islands",
    ):
        links.append(
            _link(
                f"{WIKI}/2026_United_States_House_of_Representatives_election_in_{territory}",
                territory,
            )
        )
    links.append(_link(f"{WIKI}/2024_United_States_House_of_Representatives_elections", "2024"))
    return _HOUSE_INDEX_HEAD + "".join(links) + _INDEX_TAIL


_SENATE_CLASS_2_STATES = (
    "Alabama", "Alaska", "Arkansas", "Colorado", "Delaware", "Georgia", "Idaho",
    "Illinois", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Montana", "Nebraska", "New Hampshire",
    "New Jersey", "New Mexico", "North Carolina", "Oklahoma", "Oregon",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
    "Virginia", "West Virginia", "Wyoming",
)


def _senate_index_page() -> str:
    """Build the Senate index: 33 regular races, 2 specials, plus false friends."""
    links = [
        _link(f"{WIKI}/2026_United_States_Senate_election_in_{state.replace(' ', '_')}", state)
        for state in _SENATE_CLASS_2_STATES
    ]
    links += [
        _link(f"{WIKI}/2026_United_States_Senate_special_election_in_Florida", "Florida"),
        _link(f"{WIKI}/2026_United_States_Senate_special_election_in_Ohio", "Ohio"),
        # Repeats and off-pattern links the live index also carries.
        _link(f"{WIKI}/2026_United_States_Senate_election_in_Texas", "Texas"),
        _link(f"{WIKI}/2026_United_States_Senate_election_in_Guam", "Guam"),
        _link(f"{WIKI}/2024_United_States_Senate_elections", "2024"),
    ]
    return (
        '<html><body><div class="mw-parser-output"><table class="wikitable" id="seats">'
        "<tbody><tr><th>Affiliation</th><th>Democratic</th><th>Independent</th>"
        "<th>Republican</th><th>Last change</th></tr>"
        "<tr><td>Before</td><td>45</td><td>2</td><td>53</td><td>June 3, 2026</td></tr>"
        "</tbody></table>" + "".join(links) + _INDEX_TAIL
    )


HOUSE_INDEX_PAGE = _house_index_page()
SENATE_INDEX_PAGE = _senate_index_page()


# ── Helpers ───────────────────────────────────────────────────────────────────


class FakeFetcher:
    """A dict-backed stand-in for ``fetch_html``; never touches the network.

    An unknown URL raises a 404 ``HTTPError``, which is how a page that has not
    been written yet behaves.
    """

    def __init__(
        self,
        pages: Mapping[str, str],
        *,
        errors: Mapping[str, Exception] | None = None,
    ) -> None:
        self.pages = dict(pages)
        self.errors = dict(errors or {})
        self.requested: list[str] = []

    def __call__(self, url: str) -> str:
        self.requested.append(url)
        error = self.errors.get(url)
        if error is not None:
            raise error
        try:
            return self.pages[url]
        except KeyError:
            raise _not_found(url) from None


def _not_found(url: str) -> HTTPError:
    """Build the 404 urllib raises for a page that does not exist."""
    return HTTPError(url, 404, "Not Found", Message(), None)


@pytest.fixture()
def us_db(db: Database) -> Database:
    """Seed the three US maps with the seats these tests attach polls to."""
    house = db.add_map("US House Districts 2024")
    for seat_name in ("CA-03", "CA-40", "VT-01", "TX-02"):
        db.add_seat(house.id, seat_name)
    senate = db.add_map("US Senate 2024")
    for seat_name in (
        "Michigan",
        "Nebraska",
        "Alaska",
        "Delaware",
        "Florida",
        "Ohio",
        "Texas",
    ):
        db.add_seat(senate.id, seat_name)
    president = db.add_map("US Presidential 2024")
    for seat_name in ("Nevada", "Maine CD-2"):
        db.add_seat(president.id, seat_name)
    return db


def _seat_ids(db: Database, map_name: str) -> dict[str, int]:
    """Seat name → id for a map, the shape ``rows_for_page`` takes."""
    poll_map = db.get_map_by_name(map_name)
    assert poll_map is not None
    return {seat.seat_name: seat.id for seat in db.get_seats_for_map(poll_map.id)}


def _matchups(rows: tuple[UsPollRow, ...]) -> list[str | None]:
    """The matchup of every row, in order."""
    return [row.matchup for row in rows]


# ── Contest definitions ───────────────────────────────────────────────────────


class TestContests:
    def test_four_contests_keyed_by_slug(self) -> None:
        assert [contest.slug for contest in US_CONTESTS] == [
            "house_national",
            "house_districts",
            "senate_races",
            "president",
        ]
        assert US_CONTESTS_BY_SLUG["senate_races"] is SENATE_RACES

    def test_maps_and_pollster_suffixes(self) -> None:
        assert (HOUSE_NATIONAL.map_name, HOUSE_NATIONAL.pollster_suffix) == (
            "US House Districts 2024",
            "_us_house",
        )
        assert (HOUSE_DISTRICTS.map_name, HOUSE_DISTRICTS.pollster_suffix) == (
            "US House Districts 2024",
            "_us_house",
        )
        assert (SENATE_RACES.map_name, SENATE_RACES.pollster_suffix) == (
            "US Senate 2024",
            "_us_senate",
        )
        assert (PRESIDENT.map_name, PRESIDENT.pollster_suffix) == (
            "US Presidential 2024",
            "_us_president",
        )

    def test_president_pages_are_configurable(self) -> None:
        # The Statewide article 404s today, so the tuple has to be overridable.
        assert PRESIDENT.page_urls == PRESIDENT_PAGE_URLS
        assert len(PRESIDENT_PAGE_URLS) == 2
        assert "Statewide_opinion_polling" in PRESIDENT_PAGE_URLS[1]

    def test_only_discovered_contests_are_per_state(self) -> None:
        assert SENATE_RACES.is_per_state
        assert HOUSE_DISTRICTS.is_per_state
        assert not PRESIDENT.is_per_state
        assert not HOUSE_NATIONAL.is_per_state


# ── Page discovery ────────────────────────────────────────────────────────────


class TestDiscoverSenatePages:
    def test_finds_thirty_five_races(self) -> None:
        urls = discover_senate_pages(SENATE_INDEX_PAGE).urls
        assert len(urls) == 35

    def test_includes_both_specials_and_excludes_territories(self) -> None:
        urls = discover_senate_pages(SENATE_INDEX_PAGE).urls
        assert FLORIDA_SPECIAL_URL in urls
        assert f"{WIKI}/2026_United_States_Senate_special_election_in_Ohio" in urls
        assert not any("Guam" in url for url in urls)
        assert not any("2024" in url for url in urls)

    def test_repeated_link_is_deduped(self) -> None:
        urls = discover_senate_pages(SENATE_INDEX_PAGE).urls
        assert urls.count(f"{WIKI}/2026_United_States_Senate_election_in_Texas") == 1

    def test_variant_spellings_of_one_state_are_capped_at_two_pages(self) -> None:
        html = _link(TEXAS_SENATE_URL, "Texas") + "".join(
            _link(url, "Texas") for url in TEXAS_SENATE_VARIANT_URLS
        )
        discovered = discover_senate_pages(html)
        # Two pages reach the duplicate-seat check; the rest are reported.
        assert discovered.urls == (TEXAS_SENATE_URL, TEXAS_SENATE_VARIANT_URLS[0])
        assert discovered.dropped == {
            url: "Texas already has 2 race pages on the index"
            for url in TEXAS_SENATE_VARIANT_URLS[1:]
        }

    def test_the_page_count_is_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(us_wikipedia_polls, "_MAX_DISCOVERED_PAGES", 30)
        discovered = discover_senate_pages(SENATE_INDEX_PAGE)
        assert len(discovered.urls) == 30
        assert len(discovered.dropped) == 5
        assert set(discovered.dropped.values()) == {
            "past the 30-page limit on one index's race pages"
        }

    def test_a_normal_index_drops_nothing(self) -> None:
        assert discover_senate_pages(SENATE_INDEX_PAGE).dropped == {}
        assert discover_house_pages(HOUSE_INDEX_PAGE).dropped == {}

    def test_relative_hrefs_become_absolute(self) -> None:
        html = _link("/wiki/2026_United_States_Senate_election_in_Maine", "Maine")
        assert discover_senate_pages(html).urls == (
            f"{WIKI}/2026_United_States_Senate_election_in_Maine",
        )


class TestDiscoverHousePages:
    def test_finds_fifty_state_pages(self) -> None:
        urls = discover_house_pages(HOUSE_INDEX_PAGE).urls
        assert len(urls) == 50

    def test_forty_four_plural_and_six_at_large(self) -> None:
        urls = discover_house_pages(HOUSE_INDEX_PAGE).urls
        at_large = [url for url in urls if "_election_in_" in url]
        multi = [url for url in urls if "_elections_in_" in url]
        assert len(multi) == 44
        assert len(at_large) == 6
        assert VERMONT_URL in at_large

    def test_dc_and_territories_are_excluded(self) -> None:
        urls = discover_house_pages(HOUSE_INDEX_PAGE).urls
        assert not any("District_of_Columbia" in url for url in urls)
        assert not any("Mariana" in url for url in urls)
        assert not any("Guam" in url for url in urls)


class TestNormaliseStates:
    def test_accepts_names_and_postal_codes(self) -> None:
        assert normalise_states(["tx", "Michigan", "ne"]) == (
            ["Texas", "Michigan", "Nebraska"],
            [],
        )

    def test_reports_unrecognised_values(self) -> None:
        assert normalise_states(["Texas", "Narnia"]) == (["Texas"], ["Narnia"])


# ── Fetching ──────────────────────────────────────────────────────────────────


class TestFetchPages:
    def test_returns_every_page(self) -> None:
        fetcher = FakeFetcher({"a": "<html>A</html>", "b": "<html>B</html>"})
        result = fetch_pages(["a", "b"], fetcher=fetcher)
        assert result.pages == {"a": "<html>A</html>", "b": "<html>B</html>"}
        assert result.failures == {}
        assert result.notes == ()

    def test_repeated_urls_are_fetched_once(self) -> None:
        fetcher = FakeFetcher({"a": "A"})
        result = fetch_pages(["a", "a", "a"], fetcher=fetcher)
        assert fetcher.requested == ["a"]
        assert result.pages == {"a": "A"}

    def test_404_is_a_note_not_a_failure(self) -> None:
        fetcher = FakeFetcher({"a": "A"}, errors={"b": _not_found("b")})
        result = fetch_pages(["a", "b"], fetcher=fetcher)
        assert result.failures == {}
        assert result.notes == ("b: not present yet (HTTP 404)",)
        assert result.pages == {"a": "A"}

    def test_other_errors_are_recorded_and_the_run_continues(self) -> None:
        fetcher = FakeFetcher(
            {"a": "A", "c": "C"},
            errors={"b": RuntimeError("connection reset")},
        )
        result = fetch_pages(["a", "b", "c"], fetcher=fetcher)
        assert result.pages == {"a": "A", "c": "C"}
        assert result.failures == {"b": "RuntimeError: connection reset"}

    def test_a_failed_fetch_is_logged_with_its_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fetcher = FakeFetcher({}, errors={"b": RuntimeError("connection reset")})
        with caplog.at_level(logging.WARNING, logger=us_wikipedia_polls.__name__):
            result = fetch_pages(["b"], fetcher=fetcher)
        assert result.failures == {"b": "RuntimeError: connection reset"}
        [record] = caplog.records
        assert record.levelno == logging.WARNING
        assert record.getMessage() == "Fetching b failed"
        assert record.exc_info is not None
        assert record.exc_info[0] is RuntimeError

    def test_http_error_other_than_404_is_a_failure(self) -> None:
        fetcher = FakeFetcher({}, errors={"b": HTTPError("b", 503, "Busy", Message(), None)})
        result = fetch_pages(["b"], fetcher=fetcher)
        assert result.failures == {"b": "HTTP 503: Busy"}
        assert result.notes == ()

    def test_empty_url_list_does_no_work(self) -> None:
        fetcher = FakeFetcher({})
        result = fetch_pages([], fetcher=fetcher)
        assert (result.pages, result.failures, result.notes) == ({}, {}, ())
        assert fetcher.requested == []

    def test_note_order_follows_request_order(self) -> None:
        fetcher = FakeFetcher({})
        result = fetch_pages(["z", "y", "x"], fetcher=fetcher, max_workers=3)
        assert result.notes == (
            "z: not present yet (HTTP 404)",
            "y: not present yet (HTTP 404)",
            "x: not present yet (HTTP 404)",
        )


# ── President ─────────────────────────────────────────────────────────────────


class TestPresidentRows:
    def test_nationwide_rows_have_no_seat_but_a_matchup(self, us_db: Database) -> None:
        page = rows_for_page(
            PRESIDENT,
            PRESIDENT_URL,
            PRESIDENT_PAGE,
            seat_ids=_seat_ids(us_db, PRESIDENT.map_name),
        )
        nationwide = [row for row in page.rows if "Nationwide" in row.heading_path]
        assert len(nationwide) == 2
        assert {row.seat_name for row in nationwide} == {None}
        assert {row.seat_id for row in nationwide} == {None}
        assert _matchups(tuple(nationwide)) == [
            "Vance (R) vs Newsom (D)",
            "Vance (R) vs Shapiro (D)",
        ]

    def test_statewide_nevada_row_is_attached_to_the_nevada_seat(
        self, us_db: Database
    ) -> None:
        seat_ids = _seat_ids(us_db, PRESIDENT.map_name)
        page = rows_for_page(
            PRESIDENT, PRESIDENT_URL, PRESIDENT_PAGE, seat_ids=seat_ids
        )
        nevada = [row for row in page.rows if row.seat_name == "Nevada"]
        assert len(nevada) == 1
        assert nevada[0].seat_id == seat_ids["Nevada"]
        assert nevada[0].matchup == "Vance (R) vs Newsom (D)"
        assert nevada[0].heading_path == (
            "Opinion polling › General election › Statewide › Nevada › "
            "JD Vance vs. Gavin Newsom"
        )

    def test_primary_sections_are_excluded(self, us_db: Database) -> None:
        page = rows_for_page(
            PRESIDENT,
            PRESIDENT_URL,
            PRESIDENT_PAGE,
            seat_ids=_seat_ids(us_db, PRESIDENT.map_name),
        )
        assert not any("Rubio" in (row.matchup or "") for row in page.rows)
        assert not any("primary" in row.heading_path.lower() for row in page.rows)

    def test_collapsed_hypotheticals_are_skipped_when_visible_rows_exist(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            PRESIDENT,
            PRESIDENT_URL,
            PRESIDENT_PAGE,
            seat_ids=_seat_ids(us_db, PRESIDENT.map_name),
        )
        assert not any("Harris" in (row.matchup or "") for row in page.rows)
        assert all(not row.collapsed for row in page.rows)

    def test_pollster_cell_link_becomes_the_row_source(self, us_db: Database) -> None:
        page = rows_for_page(
            PRESIDENT,
            PRESIDENT_URL,
            PRESIDENT_PAGE,
            seat_ids=_seat_ids(us_db, PRESIDENT.map_name),
        )
        assert page.rows[0].source_url == "https://example.invalid/poll"

    def test_without_a_pollster_link_the_row_points_at_its_section(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            PRESIDENT,
            PRESIDENT_URL,
            PRESIDENT_PAGE,
            seat_ids=_seat_ids(us_db, PRESIDENT.map_name),
        )
        nevada = next(row for row in page.rows if row.seat_name == "Nevada")
        assert nevada.source_url == f"{PRESIDENT_URL}#JD_Vance_vs._Gavin_Newsom_2"

    def test_a_statewide_heading_off_the_map_is_reported(self, us_db: Database) -> None:
        # "Fremont" is a heading president_seat_for_heading cannot resolve.
        page = rows_for_page(
            PRESIDENT,
            PRESIDENT_URL,
            PRESIDENT_PAGE,
            seat_ids=_seat_ids(us_db, PRESIDENT.map_name),
        )
        assert [(u.seat_name, u.reason, u.dropped_rows) for u in page.unmatched_seats] == [
            (None, "no heading names a state or district", 1)
        ]
        assert not any("Fremont Research" == row.pollster_label for row in page.rows)

    def test_row_carries_the_president_pollster_identifier_and_sample(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            PRESIDENT,
            PRESIDENT_URL,
            PRESIDENT_PAGE,
            seat_ids=_seat_ids(us_db, PRESIDENT.map_name),
        )
        row = page.rows[0]
        assert row.pollster_identifier == "emerson_college_us_president"
        assert row.sample_size_label == "1,000 (LV)"
        assert (row.sample_size, row.population) == (1000, "LV")
        assert row.fieldwork_start == date(2026, 6, 24)
        assert row.fieldwork_end == date(2026, 6, 26)
        assert row.map_name == "US Presidential 2024"
        assert row.contest == "president"
        assert [(r.party_name, r.candidate_name, r.percentage) for r in row.readings] == [
            ("Republican", "JD Vance", 45.0),
            ("Democratic", "Gavin Newsom", 44.0),
        ]


# ── Senate ────────────────────────────────────────────────────────────────────


class TestSenateRows:
    def test_aggregation_and_collapsed_tables_are_skipped(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            SENATE_RACES,
            MICHIGAN_URL,
            MICHIGAN_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert {row.matchup for row in page.rows} == {"Rogers (R) vs El-Sayed (D)"}
        assert not any("Stevens" in (row.matchup or "") for row in page.rows)
        assert all(row.seat_name == "Michigan" for row in page.rows)

    def test_rowspan_variants_are_counted_and_noted(self, us_db: Database) -> None:
        page = rows_for_page(
            SENATE_RACES,
            MICHIGAN_URL,
            MICHIGAN_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert page.variants_dropped == 1
        assert len(page.rows) == 2
        assert any("repeat row" in note for note in page.rows[0].notes)

    def test_partisan_sponsor_tag_is_split_off_the_label(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            SENATE_RACES,
            MICHIGAN_URL,
            MICHIGAN_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        tagged = page.rows[1]
        assert tagged.pollster_label == "Marketing Resource Group"
        assert tagged.pollster_tags == ("R",)
        assert tagged.pollster_identifier == "marketing_resource_group_us_senate"

    def test_nebraska_lead_is_the_visible_republican_independent_table(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            SENATE_RACES,
            NEBRASKA_URL,
            NEBRASKA_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert len(page.rows) == 1
        lead = page.rows[0]
        assert lead.is_lead
        assert lead.matchup == "Ricketts (R) vs Osborn (I)"
        assert [r.party_name for r in lead.readings] == ["Republican", "Independent"]
        assert page.collapsed_only_races == ()

    def test_alaska_four_way_keeps_both_sullivans_apart(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            SENATE_RACES,
            ALASKA_URL,
            ALASKA_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert len(page.rows) == 1
        row = page.rows[0]
        # Only the shared surname forces full names; Heikes keeps his surname,
        # and the unknown "(WCP)" suffix still sorts last.
        assert row.matchup == (
            "Dan S. Sullivan (R) vs Dan J. Sullivan (R) vs Peltola (D) "
            "vs Heikes (WCP)"
        )
        assert len(row.readings) == 4
        assert row.seat_name == "Alaska"
        assert row.is_lead

    def test_unknown_suffix_is_counted_and_noted(self, us_db: Database) -> None:
        page = rows_for_page(
            SENATE_RACES,
            ALASKA_URL,
            ALASKA_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert page.unknown_suffixes == {"WCP": 1}
        assert any("WCP" in note for note in page.rows[0].notes)

    def test_seat_comes_from_the_page_slug(self, us_db: Database) -> None:
        seat_ids = _seat_ids(us_db, SENATE_RACES.map_name)
        page = rows_for_page(
            SENATE_RACES,
            FLORIDA_SPECIAL_URL,
            _senate_state_page("Ashley Moody", "Jane Doe"),
            seat_ids=seat_ids,
        )
        assert [row.seat_name for row in page.rows] == ["Florida"]
        assert page.rows[0].seat_id == seat_ids["Florida"]

    def test_a_state_with_no_seat_on_the_map_is_unmatched(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            SENATE_RACES,
            f"{WIKI}/2026_United_States_Senate_election_in_Wyoming",
            _senate_state_page("John Barrasso", "Jane Doe"),
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert page.rows == ()
        assert len(page.unmatched_seats) == 1
        unmatched = page.unmatched_seats[0]
        assert unmatched.seat_name == "Wyoming"
        assert "no seat named 'Wyoming'" in unmatched.reason
        assert unmatched.dropped_rows == 1


class TestCollapsedOnlyFallback:
    def test_uncovered_race_is_reported_but_not_imported_by_default(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            SENATE_RACES,
            DELAWARE_URL,
            DELAWARE_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert page.rows == ()
        assert len(page.collapsed_only_races) == 1
        race = page.collapsed_only_races[0]
        assert (race.seat_name, race.available_rows, race.included) == (
            "Delaware",
            1,
            False,
        )

    def test_opt_in_imports_the_hidden_rows_flagged_and_never_lead(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            SENATE_RACES,
            DELAWARE_URL,
            DELAWARE_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
            include_collapsed_for_uncovered=True,
        )
        assert len(page.rows) == 1
        row = page.rows[0]
        assert row.collapsed
        assert not row.is_lead
        assert row.seat_name == "Delaware"
        assert "collapsed hypothetical table" in row.notes
        assert page.collapsed_only_races[0].included

    def test_a_covered_race_keeps_its_hypotheticals_out_even_under_the_opt_in(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            SENATE_RACES,
            NEBRASKA_URL,
            NEBRASKA_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
            include_collapsed_for_uncovered=True,
        )
        assert [row.matchup for row in page.rows] == ["Ricketts (R) vs Osborn (I)"]
        assert page.collapsed_only_races == ()


class TestNoMatchupTables:
    """A contest whose polls need a matchup never yields a row without one."""

    def test_a_single_candidate_senate_table_is_reported_not_imported(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            SENATE_RACES,
            NEBRASKA_URL,
            NEBRASKA_SINGLE_CANDIDATE_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert page.rows == ()
        assert page.no_matchup_tables == (
            NoMatchupTable(
                contest="senate_races",
                page_url=NEBRASKA_URL,
                heading_path="General election › Polling",
                seat_name="Nebraska",
                dropped_rows=1,
            ),
        )

    def test_a_single_candidate_national_president_table_is_reported(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            PRESIDENT,
            PRESIDENT_URL,
            PRESIDENT_SINGLE_CANDIDATE_PAGE,
            seat_ids=_seat_ids(us_db, PRESIDENT.map_name),
        )
        assert page.rows == ()
        assert [(t.seat_name, t.dropped_rows) for t in page.no_matchup_tables] == [
            (None, 1)
        ]

    def test_the_generic_ballot_needs_no_matchup(self, us_db: Database) -> None:
        page = rows_for_page(
            HOUSE_NATIONAL,
            HOUSE_INDEX_URL,
            HOUSE_INDEX_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_NATIONAL.map_name),
        )
        assert _matchups(page.rows) == [None, None]
        assert page.no_matchup_tables == ()

    def test_an_opted_in_hidden_table_without_a_matchup_is_reported(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            SENATE_RACES,
            DELAWARE_URL,
            DELAWARE_SINGLE_CANDIDATE_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
            include_collapsed_for_uncovered=True,
        )
        assert page.rows == ()
        assert page.collapsed_only_races == ()
        assert [(t.seat_name, t.dropped_rows) for t in page.no_matchup_tables] == [
            ("Delaware", 1)
        ]

    def test_the_run_reports_them(self, us_db: Database) -> None:
        index = fetch_us_poll_index(
            us_db,
            [SENATE_RACES],
            states=["Nebraska"],
            fetcher=_full_fetcher(**{NEBRASKA_URL: NEBRASKA_SINGLE_CANDIDATE_PAGE}),
        )
        assert index.rows == ()
        assert [t.page_url for t in index.no_matchup_tables] == [NEBRASKA_URL]


class TestOversizedTables:
    def test_a_table_too_large_to_read_is_reported(self, us_db: Database) -> None:
        page = rows_for_page(
            SENATE_RACES,
            MICHIGAN_URL,
            MICHIGAN_OVERSIZED_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert page.rows == ()
        assert page.oversized_tables == (
            OversizedTable(
                contest="senate_races",
                page_url=MICHIGAN_URL,
                heading_path="General election › Polling",
            ),
        )

    def test_the_run_reports_them(self, us_db: Database) -> None:
        index = fetch_us_poll_index(
            us_db,
            [SENATE_RACES],
            states=["Michigan"],
            fetcher=_full_fetcher(**{MICHIGAN_URL: MICHIGAN_OVERSIZED_PAGE}),
        )
        assert [t.page_url for t in index.oversized_tables] == [MICHIGAN_URL]

    def test_tables_skipped_past_the_page_budget_are_one_line(
        self,
        us_db: Database,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def parse_with_skips(html: str, **kwargs: bool) -> PageTables:
            page = parse_poll_tables(html, **kwargs)
            return dataclasses.replace(page, budget_skipped=5)

        monkeypatch.setattr(us_wikipedia_polls, "parse_poll_tables", parse_with_skips)
        page = rows_for_page(
            SENATE_RACES,
            MICHIGAN_URL,
            MICHIGAN_OVERSIZED_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        # The per-table line first, then one line for all the skipped tables.
        assert [t.heading_path for t in page.oversized_tables] == [
            "General election › Polling",
            "5 further table(s) not read: "
            "the page's 400,000-cell grid budget was reached",
        ]


# ── House ─────────────────────────────────────────────────────────────────────


class TestHouseDistrictRows:
    def test_district_heading_maps_to_a_seat(self, us_db: Database) -> None:
        seat_ids = _seat_ids(us_db, HOUSE_DISTRICTS.map_name)
        page = rows_for_page(
            HOUSE_DISTRICTS, CALIFORNIA_URL, CALIFORNIA_PAGE, seat_ids=seat_ids
        )
        assert [row.seat_name for row in page.rows] == ["CA-03"]
        assert page.rows[0].seat_id == seat_ids["CA-03"]
        assert page.rows[0].matchup == "Doe (R) vs Roe (D)"

    def test_ca40_primary_over_general_election_is_rejected(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            HOUSE_DISTRICTS,
            CALIFORNIA_URL,
            CALIFORNIA_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_DISTRICTS.map_name),
        )
        assert not any(row.seat_name == "CA-40" for row in page.rows)
        assert not any("Calvert" in (row.matchup or "") for row in page.rows)

    def test_primary_section_polls_are_excluded(self, us_db: Database) -> None:
        page = rows_for_page(
            HOUSE_DISTRICTS,
            CALIFORNIA_URL,
            CALIFORNIA_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_DISTRICTS.map_name),
        )
        assert not any("Lee" in (row.matchup or "") for row in page.rows)

    def test_at_large_page_maps_to_seat_01(self, us_db: Database) -> None:
        page = rows_for_page(
            HOUSE_DISTRICTS,
            VERMONT_URL,
            VERMONT_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_DISTRICTS.map_name),
        )
        assert [row.seat_name for row in page.rows] == ["VT-01"]
        assert page.rows[0].heading_path == "General election › Predictions › Polling"

    def test_general_election_table_with_no_district_heading_is_unmatched(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            HOUSE_DISTRICTS, TEXAS_URL, TEXAS_PAGE, seat_ids=_seat_ids(us_db, HOUSE_DISTRICTS.map_name)
        )
        reasons = [unmatched.reason for unmatched in page.unmatched_seats]
        assert reasons == ["no 'District N' heading above the table (Texas)"]
        assert page.rows == ()

    def test_accepted_table_that_parses_no_rows_is_reported(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            HOUSE_DISTRICTS, TEXAS_URL, TEXAS_PAGE, seat_ids=_seat_ids(us_db, HOUSE_DISTRICTS.map_name)
        )
        assert len(page.empty_tables) == 1
        empty = page.empty_tables[0]
        assert empty.heading_path == "District 2 › General election"
        assert empty.contest == "house_districts"
        assert not empty.collapsed


class TestHouseNationalRows:
    def test_generic_ballot_aggregation_table_is_accepted(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            HOUSE_NATIONAL,
            HOUSE_INDEX_URL,
            HOUSE_INDEX_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_NATIONAL.map_name),
        )
        assert len(page.rows) == 2
        assert {row.seat_name for row in page.rows} == {None}
        assert _matchups(page.rows) == [None, None]
        assert page.rows[0].pollster_identifier == "decision_desk_hq_us_house"

    def test_party_readings_carry_no_candidate_name(self, us_db: Database) -> None:
        page = rows_for_page(
            HOUSE_NATIONAL,
            HOUSE_INDEX_URL,
            HOUSE_INDEX_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_NATIONAL.map_name),
        )
        assert [
            (reading.party_name, reading.candidate_name, reading.percentage)
            for reading in page.rows[0].readings
        ] == [("Republican", "", 40.1), ("Democratic", "", 44.3)]

    def test_dates_updated_is_the_fieldwork_window(self, us_db: Database) -> None:
        page = rows_for_page(
            HOUSE_NATIONAL,
            HOUSE_INDEX_URL,
            HOUSE_INDEX_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_NATIONAL.map_name),
        )
        assert page.rows[0].fieldwork_start == date(2026, 6, 29)
        assert page.rows[0].fieldwork_end == date(2026, 6, 29)

    def test_a_matchupless_table_is_never_a_lead_table(self, us_db: Database) -> None:
        page = rows_for_page(
            HOUSE_NATIONAL,
            HOUSE_INDEX_URL,
            HOUSE_INDEX_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_NATIONAL.map_name),
        )
        assert all(not row.is_lead for row in page.rows)

    def test_the_redistricting_table_is_not_a_poll_table(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            HOUSE_NATIONAL,
            HOUSE_INDEX_URL,
            HOUSE_INDEX_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_NATIONAL.map_name),
        )
        assert page.empty_tables == ()
        assert all(row.pollster_label != "Texas" for row in page.rows)

    def test_race_page_tables_are_ignored_by_the_generic_rule(
        self, us_db: Database
    ) -> None:
        # The Michigan aggregation table is not under a "generic" h2.
        page = rows_for_page(
            HOUSE_NATIONAL,
            MICHIGAN_URL,
            MICHIGAN_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_NATIONAL.map_name),
        )
        assert page.rows == ()


class TestLeadTable:
    def test_only_the_first_accepted_table_per_seat_leads(
        self, us_db: Database
    ) -> None:
        page = rows_for_page(
            PRESIDENT,
            PRESIDENT_URL,
            PRESIDENT_PAGE,
            seat_ids=_seat_ids(us_db, PRESIDENT.map_name),
        )
        national = [row for row in page.rows if row.seat_name is None]
        assert [row.is_lead for row in national] == [True, False]

    def test_each_seat_gets_its_own_lead(self, us_db: Database) -> None:
        page = rows_for_page(
            PRESIDENT,
            PRESIDENT_URL,
            PRESIDENT_PAGE,
            seat_ids=_seat_ids(us_db, PRESIDENT.map_name),
        )
        nevada = next(row for row in page.rows if row.seat_name == "Nevada")
        assert nevada.is_lead

    def test_every_row_of_the_lead_table_is_flagged(self, us_db: Database) -> None:
        page = rows_for_page(
            SENATE_RACES,
            MICHIGAN_URL,
            MICHIGAN_PAGE,
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert [row.is_lead for row in page.rows] == [True, True]


# ── The whole run ─────────────────────────────────────────────────────────────


def _full_fetcher(**overrides: str) -> FakeFetcher:
    """A fetcher serving the index pages and the race pages the tests use."""
    pages: dict[str, str] = {
        HOUSE_INDEX_URL: HOUSE_INDEX_PAGE,
        SENATE_INDEX_URL: SENATE_INDEX_PAGE,
        PRESIDENT_URL: PRESIDENT_PAGE,
        MICHIGAN_URL: MICHIGAN_PAGE,
        NEBRASKA_URL: NEBRASKA_PAGE,
        ALASKA_URL: ALASKA_PAGE,
        DELAWARE_URL: DELAWARE_PAGE,
        CALIFORNIA_URL: CALIFORNIA_PAGE,
        VERMONT_URL: VERMONT_PAGE,
    }
    pages.update(overrides)
    return FakeFetcher(pages)


class TestFetchUsPollIndex:
    def test_senate_run_covers_every_discovered_race(self, us_db: Database) -> None:
        fetcher = _full_fetcher()
        index = fetch_us_poll_index(us_db, [SENATE_RACES], fetcher=fetcher)
        assert {row.seat_name for row in index.rows} == {
            "Michigan",
            "Nebraska",
            "Alaska",
        }
        # The index plus the four race pages the fetcher serves. The other 31
        # of the 35 discovered races 404, which is a note, not a failure.
        assert index.pages_fetched == 5
        assert index.page_failures == {}
        assert len(index.notes) == 31

    def test_statewide_president_404_is_a_note(self, us_db: Database) -> None:
        index = fetch_us_poll_index(us_db, [PRESIDENT], fetcher=_full_fetcher())
        assert index.page_failures == {}
        assert index.notes == (f"{PRESIDENT_STATEWIDE_URL}: not present yet (HTTP 404)",)
        assert len(index.rows) == 3

    def test_a_broken_page_is_recorded_and_the_rest_still_import(
        self, us_db: Database
    ) -> None:
        fetcher = _full_fetcher()
        fetcher.errors[NEBRASKA_URL] = RuntimeError("connection reset")
        index = fetch_us_poll_index(
            us_db, [SENATE_RACES], states=["Michigan", "Nebraska"], fetcher=fetcher
        )
        assert index.page_failures[NEBRASKA_URL] == "RuntimeError: connection reset"
        assert {row.seat_name for row in index.rows} == {"Michigan"}

    def test_two_pages_for_one_seat_become_a_page_failure(
        self, us_db: Database
    ) -> None:
        # Hypothetical today, but the one shape a state-named seat cannot
        # represent: one state holding a regular and a special race in the
        # same cycle. The first page wins and the second is reported.
        both_florida_races = _link(FLORIDA_URL, "Florida") + _link(
            FLORIDA_SPECIAL_URL, "Florida (special)"
        )
        fetcher = _full_fetcher(
            **{
                SENATE_INDEX_URL: both_florida_races,
                FLORIDA_URL: _senate_state_page("Rick Scott", "Jane Doe"),
                FLORIDA_SPECIAL_URL: _senate_state_page("Ashley Moody", "Ann Lee"),
            }
        )
        index = fetch_us_poll_index(
            us_db, [SENATE_RACES], states=["Florida"], fetcher=fetcher
        )
        assert [row.matchup for row in index.rows] == ["Scott (R) vs Doe (D)"]
        assert FLORIDA_SPECIAL_URL in index.page_failures
        assert "already covered by" in index.page_failures[FLORIDA_SPECIAL_URL]

    def test_a_second_page_for_one_seat_is_never_fetched(
        self, us_db: Database
    ) -> None:
        fetcher = _full_fetcher(
            **{
                SENATE_INDEX_URL: _link(FLORIDA_URL, "Florida")
                + _link(FLORIDA_SPECIAL_URL, "Florida (special)"),
                FLORIDA_URL: _senate_state_page("Rick Scott", "Jane Doe"),
                FLORIDA_SPECIAL_URL: _senate_state_page("Ashley Moody", "Ann Lee"),
            }
        )
        fetch_us_poll_index(us_db, [SENATE_RACES], fetcher=fetcher)
        assert fetcher.requested == [SENATE_INDEX_URL, FLORIDA_URL]

    def test_variant_spellings_of_one_state_cost_no_requests(
        self, us_db: Database
    ) -> None:
        index_html = _link(TEXAS_SENATE_URL, "Texas") + "".join(
            _link(url, "Texas") for url in TEXAS_SENATE_VARIANT_URLS
        )
        fetcher = _full_fetcher(
            **{
                SENATE_INDEX_URL: index_html,
                TEXAS_SENATE_URL: _senate_state_page("Ken Paxton", "James Talarico"),
            }
        )
        index = fetch_us_poll_index(us_db, [SENATE_RACES], fetcher=fetcher)
        assert fetcher.requested == [SENATE_INDEX_URL, TEXAS_SENATE_URL]
        assert set(index.page_failures) == set(TEXAS_SENATE_VARIANT_URLS)
        assert "already covered by" in index.page_failures[TEXAS_SENATE_VARIANT_URLS[0]]
        assert [row.seat_name for row in index.rows] == ["Texas"]

    def test_state_filter_limits_the_pages_fetched(self, us_db: Database) -> None:
        fetcher = _full_fetcher()
        fetch_us_poll_index(us_db, [SENATE_RACES], states=["mi"], fetcher=fetcher)
        assert fetcher.requested == [SENATE_INDEX_URL, MICHIGAN_URL]

    def test_state_filter_does_not_touch_the_president(self, us_db: Database) -> None:
        fetcher = _full_fetcher()
        index = fetch_us_poll_index(
            us_db, [PRESIDENT], states=["Michigan"], fetcher=fetcher
        )
        assert len(index.rows) == 3

    def test_unrecognised_state_filter_is_noted(self, us_db: Database) -> None:
        index = fetch_us_poll_index(
            us_db, [SENATE_RACES], states=["Narnia"], fetcher=_full_fetcher()
        )
        assert "ignored unrecognised state filter: 'Narnia'" in index.notes

    def test_house_index_is_fetched_once_for_both_house_contests(
        self, us_db: Database
    ) -> None:
        fetcher = _full_fetcher()
        index = fetch_us_poll_index(
            us_db,
            [HOUSE_NATIONAL, HOUSE_DISTRICTS],
            states=["California", "Vermont"],
            fetcher=fetcher,
        )
        assert fetcher.requested.count(HOUSE_INDEX_URL) == 1
        assert {row.contest for row in index.rows} == {
            "house_national",
            "house_districts",
        }
        assert {row.seat_name for row in index.rows} == {None, "CA-03", "VT-01"}

    def test_diagnostics_are_aggregated_across_contests(
        self, us_db: Database
    ) -> None:
        index = fetch_us_poll_index(
            us_db,
            [SENATE_RACES],
            states=["Michigan", "Alaska", "Delaware"],
            fetcher=_full_fetcher(),
        )
        assert index.variants_dropped == 2
        assert index.unknown_suffixes == {"WCP": 1}
        assert [race.seat_name for race in index.collapsed_only_races] == ["Delaware"]
        assert index.unmatched_seats == ()
        assert index.empty_tables == ()

    def test_collapsed_opt_in_adds_the_uncovered_race(self, us_db: Database) -> None:
        index = fetch_us_poll_index(
            us_db,
            [SENATE_RACES],
            states=["Delaware"],
            include_collapsed_for_uncovered=True,
            fetcher=_full_fetcher(),
        )
        assert [row.seat_name for row in index.rows] == ["Delaware"]
        assert index.rows[0].collapsed

    def test_a_missing_map_skips_the_contest_with_a_note(self, db: Database) -> None:
        index = fetch_us_poll_index(db, [SENATE_RACES], fetcher=_full_fetcher())
        assert index.rows == ()
        assert index.notes == (
            "senate_races: skipped — no map named 'US Senate 2024'",
        )

    def test_an_unreadable_index_leaves_the_contest_empty(
        self, us_db: Database
    ) -> None:
        fetcher = FakeFetcher({})
        index = fetch_us_poll_index(us_db, [SENATE_RACES], fetcher=fetcher)
        assert index.rows == ()
        assert "senate_races: no race pages — its index could not be read" in index.notes

    def test_rows_keep_contest_order(self, us_db: Database) -> None:
        index = fetch_us_poll_index(
            us_db,
            [PRESIDENT, SENATE_RACES],
            states=["Michigan"],
            fetcher=_full_fetcher(),
        )
        contests = [row.contest for row in index.rows]
        assert contests == ["president"] * 3 + ["senate_races"] * 2

    def test_no_test_reaches_the_network(self, us_db: Database) -> None:
        fetcher = _full_fetcher()
        fetch_us_poll_index(us_db, list(US_CONTESTS), fetcher=fetcher)
        assert all(url.startswith("https://en.wikipedia.org/") for url in fetcher.requested)
        assert fetcher.requested


# ── Summary rows ──────────────────────────────────────────────────────────────


class TestSummaryRows:
    """The generic-ballot table's closing "Average" row is not a pollster."""

    def test_the_average_row_is_not_imported(self, us_db: Database) -> None:
        page = rows_for_page(
            HOUSE_NATIONAL,
            HOUSE_INDEX_URL,
            HOUSE_INDEX_PAGE,
            seat_ids=_seat_ids(us_db, HOUSE_NATIONAL.map_name),
        )
        assert [row.pollster_label for row in page.rows] == [
            "Decision Desk HQ",
            "The Economist",
        ]
        assert page.summary_rows_skipped == 1

    def test_the_skip_is_case_insensitive(self, us_db: Database) -> None:
        page = rows_for_page(
            HOUSE_NATIONAL,
            HOUSE_INDEX_URL,
            HOUSE_INDEX_PAGE.replace("<b>Average</b>", "AVERAGE"),
            seat_ids=_seat_ids(us_db, HOUSE_NATIONAL.map_name),
        )
        assert page.summary_rows_skipped == 1

    def test_a_race_pollster_called_average_is_kept(self, us_db: Database) -> None:
        # Only the generic ballot declares a summary row; a Senate page naming
        # a pollster "Average" is still a poll.
        page = rows_for_page(
            SENATE_RACES,
            MICHIGAN_URL,
            MICHIGAN_PAGE.replace("Marketing Resource Group (R)", "Average"),
            seat_ids=_seat_ids(us_db, SENATE_RACES.map_name),
        )
        assert "Average" in [row.pollster_label for row in page.rows]
        assert page.summary_rows_skipped == 0

    def test_the_run_reports_the_skip(self, us_db: Database) -> None:
        index = fetch_us_poll_index(
            us_db, [HOUSE_NATIONAL], fetcher=_full_fetcher()
        )
        assert index.summary_rows_skipped == 1
        assert len(index.rows) == 2


# ── Import plans ──────────────────────────────────────────────────────────────


@pytest.fixture()
def import_db(us_db: Database) -> Database:
    """The seeded maps plus the two parties the readings resolve to."""
    us_db.add_party("Democratic")
    us_db.add_party("Republican")
    return us_db


def _page_rows(
    db: Database, contest: UsContest, url: str, html: str
) -> tuple[UsPollRow, ...]:
    """Scrape one page against the real seat ids, the way a run does."""
    return rows_for_page(
        contest, url, html, seat_ids=_seat_ids(db, contest.map_name)
    ).rows


def _michigan_row(db: Database) -> UsPollRow:
    """The Michigan lead table's first poll: Rogers (R) vs El-Sayed (D)."""
    return _page_rows(db, SENATE_RACES, MICHIGAN_URL, MICHIGAN_PAGE)[0]


def _map_id(db: Database, map_name: str) -> int:
    poll_map = db.get_map_by_name(map_name)
    assert poll_map is not None
    return poll_map.id


class TestBuildUsImportPlan:
    def test_resolves_the_map_the_seat_and_the_parties(
        self, import_db: Database
    ) -> None:
        row = _michigan_row(import_db)
        plan = build_us_import_plan(import_db, row)
        assert plan.map_id == _map_id(import_db, "US Senate 2024")
        assert plan.seat_id == _seat_ids(import_db, "US Senate 2024")["Michigan"]
        assert [
            (planned.party_name, planned.candidate_name, planned.percentage)
            for planned in plan.rows
        ] == [
            ("Democratic", "Abdul El-Sayed", 44.0),
            ("Republican", "Mike Rogers", 45.0),
        ]
        assert plan.unknown_parties == ()
        assert plan.warnings == ()

    def test_a_new_pollster_is_named_after_the_contest(
        self, import_db: Database
    ) -> None:
        plan = build_us_import_plan(import_db, _michigan_row(import_db))
        assert plan.pollster_exists is False
        assert plan.pollster_identifier == "glengariff_group_us_senate"
        assert plan.pollster_name == "Glengariff Group (US Senate)"

    def test_an_existing_pollster_keeps_its_stored_name(
        self, import_db: Database
    ) -> None:
        import_db.add_pollster(
            name="Glengariff Group (renamed)",
            identifier="glengariff_group_us_senate",
        )
        plan = build_us_import_plan(import_db, _michigan_row(import_db))
        assert plan.pollster_exists is True
        assert plan.pollster_name == "Glengariff Group (renamed)"

    def test_a_party_the_database_lacks_is_reported_not_imported(
        self, import_db: Database
    ) -> None:
        row = _page_rows(import_db, SENATE_RACES, NEBRASKA_URL, NEBRASKA_PAGE)[0]
        plan = build_us_import_plan(import_db, row)
        assert [planned.party_name for planned in plan.rows] == ["Republican"]
        assert plan.unknown_parties == ("Independent",)
        assert plan.warnings == ("party not in the database: 'Independent'",)

    def test_an_unrecognised_suffix_is_reported_by_its_candidate(
        self, import_db: Database
    ) -> None:
        row = _page_rows(import_db, SENATE_RACES, ALASKA_URL, ALASKA_PAGE)[0]
        plan = build_us_import_plan(import_db, row)
        assert plan.unknown_parties == ("Gerald Heikes",)
        assert plan.warnings == (
            "no party for Gerald Heikes — unrecognised suffix",
        )

    def test_a_national_row_plans_no_seat(self, import_db: Database) -> None:
        row = _page_rows(import_db, PRESIDENT, PRESIDENT_URL, PRESIDENT_PAGE)[0]
        plan = build_us_import_plan(import_db, row)
        assert plan.seat_id is None
        assert plan.map_id == _map_id(import_db, "US Presidential 2024")

    def test_a_party_column_plans_no_candidate_name(
        self, import_db: Database
    ) -> None:
        row = _page_rows(
            import_db, HOUSE_NATIONAL, HOUSE_INDEX_URL, HOUSE_INDEX_PAGE
        )[0]
        plan = build_us_import_plan(import_db, row)
        assert [planned.candidate_name for planned in plan.rows] == [None, None]

    def test_an_unknown_contest_is_rejected(self, import_db: Database) -> None:
        row = _michigan_row(import_db).model_copy(update={"contest": "governors"})
        with pytest.raises(ValueError, match="unknown contest"):
            build_us_import_plan(import_db, row)

    def test_a_missing_map_is_rejected(self, import_db: Database) -> None:
        row = _michigan_row(import_db).model_copy(update={"map_name": "US Senate 2030"})
        with pytest.raises(ValueError, match="no map named"):
            build_us_import_plan(import_db, row)

    def test_a_seat_that_is_not_on_the_map_is_rejected(
        self, import_db: Database
    ) -> None:
        row = _michigan_row(import_db).model_copy(update={"seat_name": "Narnia"})
        with pytest.raises(ValueError, match="no seat named"):
            build_us_import_plan(import_db, row)

    def test_a_race_row_without_a_matchup_is_rejected(
        self, import_db: Database
    ) -> None:
        row = _michigan_row(import_db).model_copy(update={"matchup": None})
        with pytest.raises(ValueError, match="no matchup"):
            build_us_import_plan(import_db, row)

    def test_a_national_president_row_without_a_matchup_is_rejected(
        self, import_db: Database
    ) -> None:
        # Stored, it would match the legacy "no seat, no matchup" poll shape.
        row = _page_rows(import_db, PRESIDENT, PRESIDENT_URL, PRESIDENT_PAGE)[0]
        assert row.seat_name is None
        with pytest.raises(ValueError, match="no matchup"):
            build_us_import_plan(import_db, row.model_copy(update={"matchup": None}))

    def test_a_row_with_no_resolvable_party_is_rejected(
        self, db: Database
    ) -> None:
        # The maps and seats exist; the parties do not.
        db.add_map("US Senate 2024")
        senate = db.get_map_by_name("US Senate 2024")
        assert senate is not None
        db.add_seat(senate.id, "Michigan")
        row = _michigan_row(db)
        with pytest.raises(ValueError, match="resolved to a party"):
            build_us_import_plan(db, row)


# ── Commit ────────────────────────────────────────────────────────────────────


def _stored_rows(
    db: Database, poll_id: int
) -> list[tuple[str | None, float, int | None]]:
    """Candidate, percentage and region of every stored row of a poll.

    ``get_rows_for_poll`` orders by percentage, so this sorts by candidate to
    keep the assertions about *what* was written independent of that.
    """
    return sorted(
        (row.candidate_name, row.percentage, row.region_id)
        for row in db.get_rows_for_poll(poll_id)
    )


def _commit(db: Database, row: UsPollRow) -> int:
    """Plan and commit one row, returning the poll id."""
    result = commit_us_import_plan(db, row, build_us_import_plan(db, row))
    return result.poll_id


class TestCommitUsImportPlan:
    def test_writes_the_seat_the_matchup_and_the_candidate_names(
        self, import_db: Database
    ) -> None:
        row = _michigan_row(import_db)
        result = commit_us_import_plan(
            import_db, row, build_us_import_plan(import_db, row)
        )
        assert result.created_poll is True
        assert result.created_pollster is True
        assert result.inserted_rows == 2
        poll = import_db.get_poll(result.poll_id)
        assert poll is not None
        assert poll.matchup == "Rogers (R) vs El-Sayed (D)"
        assert poll.seat_id == _seat_ids(import_db, "US Senate 2024")["Michigan"]
        assert poll.sample_size == 600
        assert poll.fieldwork_start == date(2026, 6, 1)
        assert _stored_rows(import_db, result.poll_id) == [
            ("Abdul El-Sayed", 44.0, None),
            ("Mike Rogers", 45.0, None),
        ]

    def test_creates_the_pollster_with_the_contest_label(
        self, import_db: Database
    ) -> None:
        _commit(import_db, _michigan_row(import_db))
        pollster = import_db.get_pollster_by_identifier("glengariff_group_us_senate")
        assert pollster is not None
        assert pollster.name == "Glengariff Group (US Senate)"

    def test_same_party_candidates_become_separate_rows(
        self, import_db: Database
    ) -> None:
        row = _page_rows(import_db, SENATE_RACES, ALASKA_URL, ALASKA_PAGE)[0]
        poll_id = _commit(import_db, row)
        republican = import_db.get_party_by_name("Republican")
        assert republican is not None
        stored = import_db.get_rows_for_poll(poll_id)
        assert sorted(
            (stored_row.candidate_name, stored_row.percentage)
            for stored_row in stored
            if stored_row.party_id == republican.id
        ) == [("Dan J. Sullivan", 3.0), ("Dan S. Sullivan", 40.0)]

    def test_the_same_poll_twice_is_skipped(self, import_db: Database) -> None:
        row = _michigan_row(import_db)
        first = commit_us_import_plan(
            import_db, row, build_us_import_plan(import_db, row)
        )
        second = commit_us_import_plan(
            import_db, row, build_us_import_plan(import_db, row)
        )
        assert second.skipped_existing_rows is True
        assert second.created_poll is False
        assert second.poll_id == first.poll_id
        assert second.inserted_rows == 0
        senate = _map_id(import_db, "US Senate 2024")
        assert len(import_db.get_polls_for_map(senate)) == 1

    def test_a_national_party_poll_is_deduped_on_its_null_scope(
        self, import_db: Database
    ) -> None:
        # The generic ballot has neither a matchup nor a seat, so the presence
        # check only matches it if its nulls compare with IS, not with "=".
        row = _page_rows(
            import_db, HOUSE_NATIONAL, HOUSE_INDEX_URL, HOUSE_INDEX_PAGE
        )[0]
        first = _commit(import_db, row)
        second = commit_us_import_plan(
            import_db, row, build_us_import_plan(import_db, row)
        )
        assert second.skipped_existing_rows is True
        assert second.poll_id == first
        house = _map_id(import_db, "US House Districts 2024")
        assert len(import_db.get_polls_for_map(house)) == 1

    def test_two_matchups_from_one_poll_become_two_polls(
        self, import_db: Database
    ) -> None:
        rows = _page_rows(import_db, PRESIDENT, PRESIDENT_URL, PRESIDENT_PAGE)
        emerson = [row for row in rows if row.pollster_label == "Emerson College"]
        assert len(emerson) == 2
        assert {row.fieldwork_end for row in emerson} == {date(2026, 6, 26)}
        poll_ids = {_commit(import_db, row) for row in emerson}
        assert len(poll_ids) == 2
        president = _map_id(import_db, "US Presidential 2024")
        assert {poll.matchup for poll in import_db.get_polls_for_map(president)} == {
            "Vance (R) vs Newsom (D)",
            "Vance (R) vs Shapiro (D)",
        }

    def test_the_same_pollster_and_dates_on_another_seat_is_not_a_duplicate(
        self, import_db: Database
    ) -> None:
        michigan = _michigan_row(import_db)
        seats = _seat_ids(import_db, "US Senate 2024")
        texas = michigan.model_copy(
            update={"seat_name": "Texas", "seat_id": seats["Texas"]}
        )
        first = _commit(import_db, michigan)
        second = _commit(import_db, texas)
        assert first != second
        stored = import_db.get_polls_for_map(_map_id(import_db, "US Senate 2024"))
        assert {poll.seat_id for poll in stored} == {
            seats["Michigan"],
            seats["Texas"],
        }

    def test_a_national_poll_and_a_seat_poll_are_told_apart(
        self, import_db: Database
    ) -> None:
        rows = _page_rows(import_db, PRESIDENT, PRESIDENT_URL, PRESIDENT_PAGE)
        national = rows[0]
        seats = _seat_ids(import_db, "US Presidential 2024")
        nevada = national.model_copy(
            update={"seat_name": "Nevada", "seat_id": seats["Nevada"]}
        )
        assert _commit(import_db, national) != _commit(import_db, nevada)

    def test_a_failed_row_takes_its_poll_back_out(
        self, import_db: Database
    ) -> None:
        row = _michigan_row(import_db)
        plan = build_us_import_plan(import_db, row)
        broken = plan.rows[0].__class__(
            party_id=9999,  # no such party: the row insert violates its foreign key
            party_name="Ghost",
            candidate_name="Nobody",
            percentage=1.0,
        )
        with pytest.raises(IntegrityError):
            commit_us_import_plan(
                import_db,
                row,
                dataclasses.replace(plan, rows=(*plan.rows, broken)),
            )
        senate = _map_id(import_db, "US Senate 2024")
        assert import_db.get_polls_for_map(senate) == []
        assert (
            import_db.get_pollster_by_identifier("glengariff_group_us_senate") is None
        )

    def test_a_stale_seat_is_rejected(self, import_db: Database) -> None:
        row = _michigan_row(import_db)
        plan = build_us_import_plan(import_db, row)
        house_seat = _seat_ids(import_db, "US House Districts 2024")["CA-03"]
        with pytest.raises(ValueError, match="not on map"):
            commit_us_import_plan(
                import_db, row, dataclasses.replace(plan, seat_id=house_seat)
            )


# ── Automatic matchup tracking ────────────────────────────────────────────────


class TestApplyAutoTrackedMatchups:
    def test_a_lead_row_with_stored_polls_sets_the_matchup(
        self, import_db: Database
    ) -> None:
        rows = _page_rows(import_db, SENATE_RACES, MICHIGAN_URL, MICHIGAN_PAGE)
        for row in rows:
            _commit(import_db, row)
        counts = apply_auto_tracked_matchups(import_db, rows)
        assert counts["created"] == 1
        senate = _map_id(import_db, "US Senate 2024")
        seat_id = _seat_ids(import_db, "US Senate 2024")["Michigan"]
        tracked = import_db.get_tracked_matchup(senate, seat_id)
        assert tracked is not None
        assert tracked.matchup == "Rogers (R) vs El-Sayed (D)"
        assert tracked.source == "auto"

    def test_a_race_with_no_stored_polls_is_not_tracked(
        self, import_db: Database
    ) -> None:
        rows = _page_rows(import_db, SENATE_RACES, MICHIGAN_URL, MICHIGAN_PAGE)
        counts = apply_auto_tracked_matchups(import_db, rows)
        assert counts == {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "kept_manual": 0,
            "no_polls": 1,
        }
        senate = _map_id(import_db, "US Senate 2024")
        assert import_db.get_tracked_matchups_for_map(senate) == []

    def test_a_manual_override_is_never_replaced(self, import_db: Database) -> None:
        rows = _page_rows(import_db, SENATE_RACES, MICHIGAN_URL, MICHIGAN_PAGE)
        for row in rows:
            _commit(import_db, row)
        senate = _map_id(import_db, "US Senate 2024")
        seat_id = _seat_ids(import_db, "US Senate 2024")["Michigan"]
        import_db.set_tracked_matchup(
            senate, seat_id, "Stevens (D) vs Rogers (R)", source="manual"
        )
        counts = apply_auto_tracked_matchups(import_db, rows)
        assert counts["kept_manual"] == 1
        tracked = import_db.get_tracked_matchup(senate, seat_id)
        assert tracked is not None
        assert tracked.matchup == "Stevens (D) vs Rogers (R)"
        assert tracked.auto_matchup == "Rogers (R) vs El-Sayed (D)"

    def test_a_second_run_is_unchanged(self, import_db: Database) -> None:
        rows = _page_rows(import_db, SENATE_RACES, MICHIGAN_URL, MICHIGAN_PAGE)
        for row in rows:
            _commit(import_db, row)
        apply_auto_tracked_matchups(import_db, rows)
        assert apply_auto_tracked_matchups(import_db, rows)["unchanged"] == 1

    def test_the_president_does_not_track_automatically(
        self, import_db: Database
    ) -> None:
        rows = _page_rows(import_db, PRESIDENT, PRESIDENT_URL, PRESIDENT_PAGE)
        for row in rows:
            _commit(import_db, row)
        counts = apply_auto_tracked_matchups(import_db, rows)
        assert set(counts.values()) == {0}
        president = _map_id(import_db, "US Presidential 2024")
        assert import_db.get_tracked_matchups_for_map(president) == []

    def test_a_non_lead_table_never_tracks(self, import_db: Database) -> None:
        rows = _page_rows(import_db, SENATE_RACES, MICHIGAN_URL, MICHIGAN_PAGE)
        followers = [row.model_copy(update={"is_lead": False}) for row in rows]
        for row in followers:
            _commit(import_db, row)
        assert set(apply_auto_tracked_matchups(import_db, followers).values()) == {0}


# ── The command line ──────────────────────────────────────────────────────────


def _db_is_empty(db: Database) -> bool:
    """True when no poll, pollster or tracked matchup has been written."""
    maps = ("US Senate 2024", "US House Districts 2024", "US Presidential 2024")
    return (
        not db.get_all_pollsters()
        and all(not db.get_polls_for_map(_map_id(db, name)) for name in maps)
        and all(not db.get_tracked_matchups_for_map(_map_id(db, name)) for name in maps)
    )


class TestRunImporter:
    def test_the_default_run_lists_and_writes_nothing(
        self, import_db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = run_importer(
            [SENATE_RACES],
            ["--state", "Michigan"],
            db=import_db,
            fetcher=_full_fetcher(),
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "Senate race polls [senate_races]: 2 poll(s) across 1 race(s)" in out
        assert "Michigan — Rogers (R) vs El-Sayed (D): 2  [lead]" in out
        assert "Dry run: 2 poll(s) found, nothing written." in out
        assert _db_is_empty(import_db)

    def test_commit_writes_the_polls_and_tracks_the_matchup(
        self, import_db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = run_importer(
            [SENATE_RACES],
            ["--state", "Michigan", "--commit"],
            db=import_db,
            fetcher=_full_fetcher(),
        )
        out = capsys.readouterr().out
        assert code == 0
        senate = _map_id(import_db, "US Senate 2024")
        assert len(import_db.get_polls_for_map(senate)) == 2
        assert "created=2 skipped=0 failed=0 rows=4" in out
        assert "Tracked matchups: created=1" in out
        seat_id = _seat_ids(import_db, "US Senate 2024")["Michigan"]
        tracked = import_db.get_tracked_matchup(senate, seat_id)
        assert tracked is not None
        assert tracked.matchup == "Rogers (R) vs El-Sayed (D)"

    def test_a_second_commit_skips_what_is_already_stored(
        self, import_db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = ["--state", "Michigan", "--commit"]
        run_importer([SENATE_RACES], argv, db=import_db, fetcher=_full_fetcher())
        capsys.readouterr()
        run_importer([SENATE_RACES], argv, db=import_db, fetcher=_full_fetcher())
        assert "created=0 skipped=2 failed=0" in capsys.readouterr().out
        senate = _map_id(import_db, "US Senate 2024")
        assert len(import_db.get_polls_for_map(senate)) == 2

    def test_the_contest_flag_narrows_the_run(
        self, import_db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_importer(
            [HOUSE_NATIONAL, HOUSE_DISTRICTS],
            ["--contest", "house_national"],
            db=import_db,
            fetcher=_full_fetcher(),
        )
        out = capsys.readouterr().out
        assert "House national generic ballot [house_national]" in out
        assert "house_districts" not in out

    def test_an_unknown_contest_is_refused(self, import_db: Database) -> None:
        with pytest.raises(SystemExit):
            run_importer(
                [SENATE_RACES],
                ["--contest", "president"],
                db=import_db,
                fetcher=_full_fetcher(),
            )

    def test_the_diagnostics_are_printed(
        self, import_db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_importer(
            [SENATE_RACES],
            ["--state", "Alaska", "--state", "Delaware"],
            db=import_db,
            fetcher=_full_fetcher(),
        )
        out = capsys.readouterr().out
        assert "unknown party suffix: (WCP) in 1 table(s)" in out
        assert "collapsed-only race: Delaware (senate_races)" in out
        assert "repeat row(s) dropped" in out

    def test_dropped_tables_are_printed(
        self, import_db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_importer(
            [SENATE_RACES],
            ["--state", "Michigan", "--state", "Nebraska"],
            db=import_db,
            fetcher=_full_fetcher(
                **{
                    MICHIGAN_URL: MICHIGAN_OVERSIZED_PAGE,
                    NEBRASKA_URL: NEBRASKA_SINGLE_CANDIDATE_PAGE,
                }
            ),
        )
        out = capsys.readouterr().out
        assert (
            f"table with no matchup: {NEBRASKA_URL} [General election › Polling] "
            "(1 row(s) dropped)"
        ) in out
        assert (
            f"table too large to read: {MICHIGAN_URL} [General election › Polling]"
        ) in out

    def test_a_page_failure_fails_the_run(
        self, import_db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fetcher = _full_fetcher()
        fetcher.errors[MICHIGAN_URL] = RuntimeError("connection reset")
        code = run_importer(
            [SENATE_RACES],
            ["--state", "Michigan"],
            db=import_db,
            fetcher=fetcher,
        )
        assert code == 1
        assert "page failed" in capsys.readouterr().out

    def test_an_unimportable_row_is_reported_and_the_rest_import(
        self, us_db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # No parties at all, so every row fails to plan.
        code = run_importer(
            [SENATE_RACES],
            ["--state", "Michigan", "--commit"],
            db=us_db,
            fetcher=_full_fetcher(),
        )
        out = capsys.readouterr().out
        assert code == 1
        assert out.count("FAILED Michigan") == 2
        assert "created=0 skipped=0 failed=2" in out

    def test_a_partly_unknown_poll_warns_and_still_imports(
        self, import_db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = run_importer(
            [SENATE_RACES],
            ["--state", "Nebraska", "--commit"],
            db=import_db,
            fetcher=_full_fetcher(),
        )
        out = capsys.readouterr().out
        assert code == 0
        assert (
            "WARNING Change Research: party not in the database: 'Independent'"
            in out
        )
        assert "created=1 skipped=0 failed=0 rows=1" in out
