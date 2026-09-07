"""Tests for the Wikipedia catch-up queue: scraper, queue state machine, routes.

Three layers are covered here, bottom-up:

- ``polls/importers/westminster/wikipedia_index.py`` — parsed from the inline
  HTML fixture below, which replicates the page's current markup (per-year
  ``<section>`` elements, a ``<div class="mw-heading">`` wrapper around the
  heading, and a reference list carrying both numeric and *named* ids).
- ``console/services/wikipedia_queue.py`` — driven against the shared temp-DB
  fixture, with no Flask client.
- The five catch-up routes on the ``poll_import`` blueprint — driven through the
  test client with a stub importer registered into a copy of ``IMPORTERS``.

**Nothing here touches the network or spawns a subprocess.** ``fetch_poll_index``
is always given ``html=``; the importer is a stub; and ``_run_model_and_export``
is monkeypatched directly (the route has no ``.exists()`` guard on the model
script, so pointing the path somewhere harmless would still launch python).
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from flask import Flask

from db import Database
from polls.importers.types import PollImportResult
from polls.importers.westminster.wikipedia_index import (
    PollIndex,
    WikipediaIndexError,
    WikipediaPollRow,
    fetch_poll_index,
    normalize_pollster_name,
)

from console import create_app
from console.blueprints.poll_import import (
    WESTMINSTER_MAP_NAME,
    WIKIPEDIA_PREVIEW_TYPE,
)
from console.importers_registry import IMPORTERS, ImporterMeta
from console.services.preview import PREVIEW_CACHE, store_preview
from console.services.wikipedia_queue import (
    NO_CUTOFF,
    QUEUE_STATUSES,
    QueueItem,
    QueueState,
    QueueStatus,
    advance,
    build_queue,
    current_item,
    progress,
    summarise,
)

# ── HTML fixture ──────────────────────────────────────────────────────────────
# Mirrors the live page's shape: <section> per heading level, the heading itself
# inside div.mw-heading, one table.wikitable per year, and an <ol> of reference
# items. The reference ids deliberately mix numeric ("cite_note-31") and named
# ("cite_note-FONEC-7apr26-137") forms, and no row's visible citation number
# matches its href fragment — both are the mistakes this scraper exists to avoid.

_ASHCROFT_URL = "https://example.test/ashcroft-tables.pdf"
_FIND_OUT_NOW_URL = "https://example.test/find-out-now-tables.xlsx"
_OPINIUM_URL = "https://example.test/opinium-tables.xlsx"
_YOUGOV_URL = "https://example.test/yougov-tables.pdf"
_SURVATION_URL = "https://example.test/survation-tables.xlsx"
_MORE_IN_COMMON_URL = "https://example.test/more-in-common-tables.xlsx"
_NEWS_WRITE_UP_URL = "https://news.example.test/deltapoll-write-up"
# Referenced by nothing in the tables. They exist so that resolving a citation
# by its *visible* number rather than its href fragment returns a wrong document
# instead of merely returning nothing.
_DECOY_30_URL = "https://example.test/decoy-30.pdf"
_DECOY_33_URL = "https://example.test/decoy-33.pdf"
_DECOY_137_URL = "https://example.test/decoy-137.pdf"

_HEADER_ROW = """
<tr>
  <th>Dates conducted</th><th>Pollster</th><th>Client</th>
  <th>Area</th><th>Sample size</th><th>Lab</th>
</tr>
"""

# Two-cell event rows record by-elections and defections, not polls.
_EVENT_ROW = """
<tr><td>13 Aug</td><td>Clacton by-election</td></tr>
"""

_ROWS_2026 = (
    _HEADER_ROW
    + _EVENT_ROW
    + """
<tr>
  <td>1&ndash;3 Feb<sup class="reference"><a href="#cite_note-datenote-2">[1]</a></sup></td>
  <td>Lord Ashcroft Polls<sup class="reference"><a href="#cite_note-31">[30]</a></sup><sup class="reference"><a href="#cite_note-notea-1">[b]</a></sup></td>
  <td>Self-funded</td><td>GB</td><td>1,502</td><td>28%</td>
</tr>
<tr>
  <td>5&ndash;7 Apr</td>
  <td>Find Out Now<sup class="reference"><a href="#cite_note-FONEC-7apr26-137">[136]</a></sup></td>
  <td>Electoral Calculus</td><td>GB</td><td>2,502</td><td>22%</td>
</tr>
<tr>
  <td>1&ndash;20 May</td>
  <td>Focaldata (MRP)<sup class="reference"><a href="#cite_note-53">[33]</a></sup></td>
  <td>UnHerd</td><td>TBA</td><td>45,335</td><td>26%</td>
</tr>
<tr>
  <td>10&ndash;12 Jun</td>
  <td>Opinium<sup class="reference"><a href="#cite_note-notea-1">[a]</a></sup><sup class="reference"><a href="#cite_note-53">[33]</a></sup></td>
  <td>The Observer</td><td>GB</td><td>2,050</td><td>30%</td>
</tr>
<tr>
  <td>31 Aug &ndash; 1 Sep</td>
  <td>YouGov<sup class="reference"><a href="#cite_note-52">[51]</a></sup></td>
  <td>The Times</td><td>GB</td><td>2,113</td><td>27%</td>
</tr>
<tr>
  <td>3&ndash;4 Sep</td>
  <td>Deltapoll</td>
  <td>The Mail on Sunday<sup class="reference"><a href="#cite_note-newsdesk-99">[98]</a></sup></td>
  <td>GB</td><td>1,507</td><td>29%</td>
</tr>
<tr>
  <td>30 Dec &ndash; 2 Jan</td>
  <td>Survation<sup class="reference"><a href="#cite_note-55">[54]</a></sup></td>
  <td>Good Morning Britain</td><td>UK</td><td>1,010</td><td>31%</td>
</tr>
"""
)

_ROWS_2025 = (
    _HEADER_ROW
    + """
<tr>
  <td>20&ndash;22 Nov</td>
  <td>More in Common<sup class="reference"><a href="#cite_note-54">[53]</a></sup></td>
  <td>Sky News</td><td>GB</td><td>2,001</td><td>25%</td>
</tr>
"""
)

_REFERENCES = f"""
<ol class="references">
  <li id="cite_note-notea-1">
    <span class="reference-text">Note a: online panel; no source document.</span>
  </li>
  <li id="cite_note-30">
    <span class="reference-text"><a rel="nofollow" class="external text" href="{_DECOY_30_URL}">Decoy</a></span>
  </li>
  <li id="cite_note-31">
    <span class="reference-text"><a rel="nofollow" class="external text" href="{_ASHCROFT_URL}">Tables</a></span>
  </li>
  <li id="cite_note-33">
    <span class="reference-text"><a rel="nofollow" class="external text" href="{_DECOY_33_URL}">Decoy</a></span>
  </li>
  <li id="cite_note-137">
    <span class="reference-text"><a rel="nofollow" class="external text" href="{_DECOY_137_URL}">Decoy</a></span>
  </li>
  <li id="cite_note-FONEC-7apr26-137">
    <span class="reference-text"><a rel="nofollow" class="external text" href="{_FIND_OUT_NOW_URL}">Tables</a></span>
  </li>
  <li id="cite_note-52">
    <span class="reference-text"><a rel="nofollow" class="external text" href="{_YOUGOV_URL}">Tables</a></span>
  </li>
  <li id="cite_note-53">
    <span class="reference-text"><a rel="nofollow" class="external text" href="{_OPINIUM_URL}">Tables</a></span>
  </li>
  <li id="cite_note-54">
    <span class="reference-text"><a rel="nofollow" class="external text" href="{_MORE_IN_COMMON_URL}">Tables</a></span>
  </li>
  <li id="cite_note-55">
    <span class="reference-text"><a rel="nofollow" class="external text" href="{_SURVATION_URL}">Tables</a></span>
  </li>
  <li id="cite_note-newsdesk-99">
    <span class="reference-text"><a rel="nofollow" class="external text" href="{_NEWS_WRITE_UP_URL}">Write-up</a></span>
  </li>
</ol>
"""


def _year_section(year: int, rows_html: str) -> str:
    """Wrap table rows in the per-year ``<section>`` Wikipedia now emits."""
    return f"""
<section>
  <div class="mw-heading mw-heading3"><h3 id="{year}">{year}</h3></div>
  <table class="wikitable">{rows_html}</table>
</section>
"""


def _page(inner_sections: str, *, heading: bool = True) -> str:
    """Build a whole page around the national-results heading and its subsections.

    Args:
        inner_sections: Markup placed inside the section that holds the
            ``National_poll_results`` heading.
        heading: When False the heading keeps its text but loses its id, which
            is how a Wikipedia rename would look to the scraper.

    Returns:
        A complete HTML document.
    """
    heading_id = ' id="National_poll_results"' if heading else ""
    return f"""<!doctype html>
<html lang="en"><body><div class="mw-parser-output">
  <section>
    <div class="mw-heading mw-heading2">
      <h2{heading_id}>National poll results</h2>
    </div>
    {inner_sections}
  </section>
  {_REFERENCES}
</div></body></html>
"""


_INDEX_HTML = _page(_year_section(2026, _ROWS_2026) + _year_section(2025, _ROWS_2025))


# ── shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture()
def app() -> Flask:
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(autouse=True)
def _isolated_preview_cache() -> Generator[None, None, None]:
    """Keep the process-global preview cache from leaking tokens across tests."""
    PREVIEW_CACHE.clear()
    yield
    PREVIEW_CACHE.clear()


@pytest.fixture()
def index() -> PollIndex:
    """The parsed HTML fixture, shared by the scraper tests."""
    return fetch_poll_index(html=_INDEX_HTML)


def _by_identifier(parsed: PollIndex, identifier: str) -> WikipediaPollRow:
    """Return the single fixture row for a pollster slug."""
    matches = [row for row in parsed.rows if row.pollster_identifier == identifier]
    assert len(matches) == 1, f"expected exactly one {identifier} row"
    return matches[0]


# ── A. the scraper ────────────────────────────────────────────────────────────


class TestFetchPollIndexRows:
    """fetch_poll_index reads the GB/UK poll rows out of every year section."""

    def test_parses_every_gb_uk_row_in_document_order(self, index: PollIndex) -> None:
        # Document order, not date order: the cross-year Survation row sits last
        # in the 2026 table even though it is the second-oldest poll, and the
        # 2025 section trails the 2026 one.
        assert [row.pollster_identifier for row in index.rows] == [
            "lord_ashcroft",
            "find_out_now",
            "opinium",
            "yougov",
            "deltapoll",
            "survation",
            "more_in_common",
        ]

    def test_carries_the_display_only_columns(self, index: PollIndex) -> None:
        row = _by_identifier(index, "yougov")
        assert row.date_label == "31 Aug – 1 Sep"
        assert row.client == "The Times"
        assert row.sample_size_label == "2,113"


class TestCitationResolution:
    """Citations resolve through the reference list, from the pollster cell only."""

    def test_named_reference_resolves_to_a_url(self, index: PollIndex) -> None:
        # Regression test for the numeric-key reference map, which matched only
        # `cite_note-(\d+)` and so silently dropped every named reference —
        # 76 of 549 rows on the live page.
        row = _by_identifier(index, "find_out_now")
        assert row.citation_id == "cite_note-FONEC-7apr26-137"
        assert row.source_url == _FIND_OUT_NOW_URL

    def test_citation_id_is_the_href_not_the_visible_number(
        self, index: PollIndex
    ) -> None:
        # The Ashcroft row renders "[30]" but links to #cite_note-31.
        row = _by_identifier(index, "lord_ashcroft")
        assert row.citation_id == "cite_note-31"
        assert row.source_url == _ASHCROFT_URL

    def test_note_markers_are_skipped_not_treated_as_failure(
        self, index: PollIndex
    ) -> None:
        # The Opinium cell carries "[a]" (a footnote with no external link)
        # ahead of its real citation.
        row = _by_identifier(index, "opinium")
        assert row.citation_id == "cite_note-53"
        assert row.source_url == _OPINIUM_URL

    def test_client_column_citation_is_not_picked_up(self, index: PollIndex) -> None:
        # The tr-wide fallback was removed deliberately: the client column cites
        # news write-ups, and attaching one would feed the importer the wrong
        # document.
        row = _by_identifier(index, "deltapoll")
        assert row.source_url == ""
        assert row.citation_id == ""
        assert index.unresolved_citations == 1


class TestPollsterLabel:
    """Citation markers are stripped from the displayed pollster name."""

    def test_markers_are_stripped_from_the_label(self, index: PollIndex) -> None:
        row = _by_identifier(index, "lord_ashcroft")
        assert row.pollster_label == "Lord Ashcroft Polls"

    def test_label_drives_the_identifier(self, index: PollIndex) -> None:
        assert _by_identifier(index, "more_in_common").pollster_label == (
            "More in Common"
        )


class TestRowFiltering:
    """Non-poll rows are dropped, but only the invisible ones stay silent."""

    def test_only_gb_and_uk_rows_are_kept(self, index: PollIndex) -> None:
        assert "focaldata" not in {row.pollster_identifier for row in index.rows}
        assert _by_identifier(index, "survation").pollster_identifier == "survation"

    def test_full_width_non_gb_row_is_reported_not_silently_dropped(
        self, index: PollIndex
    ) -> None:
        # A poll-shaped row with an unrecognised area is how a Wikipedia column
        # shift would first show up, so it is counted rather than skipped.
        assert index.unrecognised_areas == {"Area": 2, "TBA": 1}
        assert index.skipped_rows == 0

    def test_narrow_event_row_is_dropped_silently(self, index: PollIndex) -> None:
        # "13 Aug | Clacton by-election" is structurally not a poll: it must not
        # register as either a skipped row or an unrecognised area.
        assert index.skipped_rows == 0
        assert "Clacton by-election" not in index.unrecognised_areas

    def test_unparseable_date_on_a_gb_row_counts_as_skipped(self) -> None:
        bad_row = """
<tr>
  <td>Ongoing</td>
  <td>YouGov<sup class="reference"><a href="#cite_note-52">[51]</a></sup></td>
  <td>The Times</td><td>GB</td><td>2,113</td><td>27%</td>
</tr>
"""
        parsed = fetch_poll_index(html=_page(_year_section(2026, bad_row)))
        assert parsed.rows == []
        assert parsed.skipped_rows == 1
        assert parsed.unrecognised_areas == {}


class TestDateParsing:
    """Fieldwork dates take their year from the enclosing section heading."""

    def test_same_month_range(self, index: PollIndex) -> None:
        row = _by_identifier(index, "find_out_now")
        assert (row.fieldwork_start, row.fieldwork_end) == (
            date(2026, 4, 5),
            date(2026, 4, 7),
        )

    def test_cross_month_range(self, index: PollIndex) -> None:
        row = _by_identifier(index, "yougov")
        assert (row.fieldwork_start, row.fieldwork_end) == (
            date(2026, 8, 31),
            date(2026, 9, 1),
        )

    def test_cross_year_range_rolls_the_start_back(self, index: PollIndex) -> None:
        # "30 Dec – 2 Jan" inside the 2026 section starts in 2025.
        row = _by_identifier(index, "survation")
        assert (row.fieldwork_start, row.fieldwork_end) == (
            date(2025, 12, 30),
            date(2026, 1, 2),
        )

    def test_year_comes_from_the_section_not_the_page(self, index: PollIndex) -> None:
        row = _by_identifier(index, "more_in_common")
        assert (row.fieldwork_start, row.fieldwork_end) == (
            date(2025, 11, 20),
            date(2025, 11, 22),
        )

    def test_footnoted_date_cell_still_parses(self, index: PollIndex) -> None:
        # "1–3 Feb[1]" would fail the anchored date regex if the marker survived.
        row = _by_identifier(index, "lord_ashcroft")
        assert row.date_label == "1–3 Feb"
        assert (row.fieldwork_start, row.fieldwork_end) == (
            date(2026, 2, 1),
            date(2026, 2, 3),
        )


class TestStructuralFailures:
    """A markup change raises rather than returning a plausible empty index."""

    def test_missing_heading_raises(self) -> None:
        html = _page(_year_section(2026, _ROWS_2026), heading=False)
        with pytest.raises(WikipediaIndexError):
            fetch_poll_index(html=html)

    def test_no_year_subsections_raises(self) -> None:
        html = _page(f'<table class="wikitable">{_ROWS_2026}</table>')
        with pytest.raises(WikipediaIndexError):
            fetch_poll_index(html=html)


class TestNormalizePollsterName:
    """The alias ladder collapses Wikipedia's labels onto importer slugs."""

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("Lord Ashcroft Polls", "lord_ashcroft"),
            ("Find Out Now/Electoral Calculus", "find_out_now_electoral_calculus"),
            ("BMG Research", "bmg_research"),
            ("YouGov[12]", "yougov"),
            ("More in Common", "more_in_common"),
            ("Focaldata (MRP)", "focaldata"),
            ("Trajectory Partnership", "trajectory_partnership"),
        ],
    )
    def test_slugs(self, label: str, expected: str) -> None:
        assert normalize_pollster_name(label) == expected


# ── B. the queue state machine ────────────────────────────────────────────────


def _seed_westminster(db: Database) -> dict[str, int]:
    """Create the Westminster map and the two pollsters the queue tests use."""
    westminster = db.add_map(WESTMINSTER_MAP_NAME)
    yougov = db.add_pollster("YouGov", "yougov")
    opinium = db.add_pollster("Opinium", "opinium")
    return {
        "map_id": westminster.id,
        "yougov_id": yougov.id,
        "opinium_id": opinium.id,
    }


def _row(
    identifier: str,
    start: date,
    end: date,
    *,
    label: str = "",
    source_url: str = "https://example.test/doc.xlsx",
    sample_size_label: str = "1,500",
) -> WikipediaPollRow:
    """Build a scraped row without going through the HTML fixture."""
    return WikipediaPollRow(
        fieldwork_start=start,
        fieldwork_end=end,
        date_label=f"{start.day}-{end.day} {end:%b}",
        pollster_label=label or identifier.replace("_", " ").title(),
        pollster_identifier=identifier,
        client="The Times",
        sample_size_label=sample_size_label,
        source_url=source_url,
        citation_id="cite_note-1",
    )


def _mark(item: QueueItem, status: QueueStatus, *, detail: str = "") -> None:
    """Put an item into a terminal status during test setup.

    Assigning ``item.status`` inline would narrow the field's literal type for
    the rest of the test, so later assertions about what a *route* changed it to
    would not type-check.
    """
    item.status = status
    item.detail = detail


def _index_of(
    rows: list[WikipediaPollRow],
    *,
    skipped_rows: int = 0,
    unrecognised_areas: dict[str, int] | None = None,
) -> PollIndex:
    """Wrap rows in a PollIndex with the drop counts a scrape would carry."""
    return PollIndex(
        rows=rows,
        skipped_rows=skipped_rows,
        unresolved_citations=sum(1 for row in rows if not row.source_url),
        unrecognised_areas=unrecognised_areas or {},
    )


class TestBuildQueueWindow:
    """The cutoff scopes the run; the database decides what is actually missing."""

    def test_cutoff_is_inclusive(self, db: Database) -> None:
        _seed_westminster(db)
        on_boundary = _row("yougov", date(2026, 7, 29), date(2026, 7, 31))
        before = _row("opinium", date(2026, 7, 26), date(2026, 7, 30))
        state = build_queue(
            db,
            _index_of([before, on_boundary]),
            map_name=WESTMINSTER_MAP_NAME,
            cutoff=date(2026, 7, 31),
        )
        assert [item.row.pollster_identifier for item in state.items] == ["yougov"]

    def test_derived_cutoff_still_sees_the_boundary_poll(self, db: Database) -> None:
        # The latest stored poll's end date is a window, not a high-water mark:
        # a *different* poll ending the same day must not hide behind it.
        seeded = _seed_westminster(db)
        db.add_poll(
            seeded["opinium_id"],
            seeded["map_id"],
            date(2026, 7, 29),
            date(2026, 7, 31),
            sample_size=2000,
        )
        row = _row("yougov", date(2026, 7, 30), date(2026, 7, 31))
        state = build_queue(db, _index_of([row]), map_name=WESTMINSTER_MAP_NAME)
        assert state.cutoff == date(2026, 7, 31)
        assert [item.row.pollster_identifier for item in state.items] == ["yougov"]

    def test_present_row_is_dropped_despite_a_different_sample_size(
        self, db: Database
    ) -> None:
        # The presence test deliberately ignores sample_size. Wikipedia's figure
        # and the published tables routinely disagree by a few respondents, and
        # including it would let the same poll be imported a second time.
        seeded = _seed_westminster(db)
        db.add_poll(
            seeded["yougov_id"],
            seeded["map_id"],
            date(2026, 8, 10),
            date(2026, 8, 12),
            sample_size=2113,
        )
        row = _row(
            "yougov",
            date(2026, 8, 10),
            date(2026, 8, 12),
            sample_size_label="2,205",
        )
        state = build_queue(
            db,
            _index_of([row]),
            map_name=WESTMINSTER_MAP_NAME,
            cutoff=date(2026, 1, 1),
        )
        assert state.items == []
        assert state.skipped_present == 1

    def test_a_poll_on_another_map_does_not_count_as_present(
        self, db: Database
    ) -> None:
        seeded = _seed_westminster(db)
        other = db.add_map("Holyrood Constituencies")
        db.add_poll(
            seeded["yougov_id"],
            other.id,
            date(2026, 8, 10),
            date(2026, 8, 12),
        )
        row = _row("yougov", date(2026, 8, 10), date(2026, 8, 12))
        state = build_queue(
            db,
            _index_of([row]),
            map_name=WESTMINSTER_MAP_NAME,
            cutoff=date(2026, 1, 1),
        )
        assert len(state.items) == 1
        assert state.skipped_present == 0

    def test_unknown_map_queues_every_row_with_no_cutoff(self, db: Database) -> None:
        rows = [
            _row("yougov", date(2020, 1, 1), date(2020, 1, 2)),
            _row("opinium", date(2026, 8, 1), date(2026, 8, 3)),
        ]
        state = build_queue(db, _index_of(rows), map_name=WESTMINSTER_MAP_NAME)
        assert state.cutoff == NO_CUTOFF
        assert len(state.items) == 2

    def test_empty_map_queues_every_row_with_no_cutoff(self, db: Database) -> None:
        _seed_westminster(db)
        rows = [_row("yougov", date(2019, 5, 1), date(2019, 5, 2))]
        state = build_queue(db, _index_of(rows), map_name=WESTMINSTER_MAP_NAME)
        assert state.cutoff == NO_CUTOFF
        assert len(state.items) == 1

    def test_scrape_drop_counts_are_carried_onto_the_state(
        self, db: Database
    ) -> None:
        _seed_westminster(db)
        parsed = _index_of(
            [_row("yougov", date(2026, 8, 1), date(2026, 8, 3))],
            skipped_rows=2,
            unrecognised_areas={"Area": 3, "14.7%": 1},
        )
        state = build_queue(db, parsed, map_name=WESTMINSTER_MAP_NAME)
        assert state.skipped_unparsed == 2
        assert state.unrecognised_areas == {"Area": 3, "14.7%": 1}


class TestBuildQueueOrderingAndMarking:
    """Oldest first, with unimportable rows pre-marked and stepped over."""

    def test_sorted_ascending_by_end_then_start_then_label(
        self, db: Database
    ) -> None:
        _seed_westminster(db)
        rows = [
            _row("opinium", date(2026, 8, 5), date(2026, 8, 7), label="Opinium"),
            _row("yougov", date(2026, 8, 1), date(2026, 8, 3), label="YouGov"),
            _row("survation", date(2026, 8, 2), date(2026, 8, 3), label="Survation"),
            _row("techne", date(2026, 8, 1), date(2026, 8, 3), label="Alpha"),
        ]
        state = build_queue(
            db,
            _index_of(rows),
            map_name=WESTMINSTER_MAP_NAME,
            cutoff=date(2026, 1, 1),
        )
        assert [item.row.pollster_label for item in state.items] == [
            "Alpha",  # 1-3 Aug, label sorts first
            "YouGov",  # 1-3 Aug
            "Survation",  # 2-3 Aug, later start
            "Opinium",  # 5-7 Aug
        ]

    def test_no_importer_is_pre_marked_with_a_distinct_reason(
        self, db: Database
    ) -> None:
        _seed_westminster(db)
        unknown = _row("jl_partners", date(2026, 8, 1), date(2026, 8, 3))
        no_document = _row(
            "yougov", date(2026, 8, 4), date(2026, 8, 6), source_url=""
        )
        state = build_queue(
            db,
            _index_of([unknown, no_document]),
            map_name=WESTMINSTER_MAP_NAME,
            cutoff=date(2026, 1, 1),
        )
        assert [item.status for item in state.items] == ["no_importer", "no_importer"]
        assert "jl_partners" in state.items[0].detail
        assert "citation" in state.items[1].detail
        assert state.items[0].detail != state.items[1].detail

    def test_cursor_opens_on_a_pending_item_behind_no_importer_rows(
        self, db: Database
    ) -> None:
        # A run whose oldest missing polls have no importer must still open on
        # something actionable — those rows never interrupt the queue.
        _seed_westminster(db)
        rows = [
            _row("jl_partners", date(2026, 8, 1), date(2026, 8, 2)),
            _row("freshwater_strategy", date(2026, 8, 3), date(2026, 8, 4)),
            _row("yougov", date(2026, 8, 5), date(2026, 8, 6)),
        ]
        state = build_queue(
            db,
            _index_of(rows),
            map_name=WESTMINSTER_MAP_NAME,
            cutoff=date(2026, 1, 1),
        )
        assert state.index == 2
        item = current_item(state)
        assert item is not None
        assert item.status == "pending"
        assert item.row.pollster_identifier == "yougov"

    def test_an_all_unimportable_queue_finishes_immediately(
        self, db: Database
    ) -> None:
        _seed_westminster(db)
        rows = [_row("jl_partners", date(2026, 8, 1), date(2026, 8, 2))]
        state = build_queue(
            db,
            _index_of(rows),
            map_name=WESTMINSTER_MAP_NAME,
            cutoff=date(2026, 1, 1),
        )
        assert state.index == 1
        assert current_item(state) is None


class TestCursor:
    """advance steps past terminal items and drops the plans they were holding."""

    def test_advance_clears_the_plan_of_items_it_steps_past(self) -> None:
        first = QueueItem(
            row=_row("yougov", date(2026, 8, 1), date(2026, 8, 2)),
            status="imported",
            plan={"stashed": True},
        )
        second = QueueItem(
            row=_row("opinium", date(2026, 8, 3), date(2026, 8, 4)),
            status="skipped",
            plan={"stashed": True},
        )
        third = QueueItem(row=_row("techne", date(2026, 8, 5), date(2026, 8, 6)))
        state = QueueState(items=[first, second, third], cutoff=NO_CUTOFF)

        advance(state)

        assert state.index == 2
        assert first.plan is None
        assert second.plan is None
        assert current_item(state) is third

    def test_advance_stops_on_a_pending_item(self) -> None:
        item = QueueItem(row=_row("yougov", date(2026, 8, 1), date(2026, 8, 2)))
        state = QueueState(items=[item], cutoff=NO_CUTOFF)
        advance(state)
        assert state.index == 0
        assert item.plan is None


class TestReporting:
    """summarise and progress feed the summary page."""

    def _mixed_state(self) -> QueueState:
        statuses: list[tuple[str, QueueStatus]] = [
            ("yougov", "imported"),
            ("opinium", "failed"),
            ("survation", "skipped"),
            ("jl_partners", "no_importer"),
            ("techne", "pending"),
        ]
        items = [
            QueueItem(
                row=_row(identifier, date(2026, 8, 1), date(2026, 8, 2)),
                status=status,
            )
            for identifier, status in statuses
        ]
        return QueueState(items=items, index=4, cutoff=NO_CUTOFF)

    def test_summarise_always_returns_every_status(self) -> None:
        grouped = summarise(self._mixed_state())
        assert set(grouped) == set(QUEUE_STATUSES)
        assert [item.row.pollster_identifier for item in grouped["imported"]] == [
            "yougov"
        ]
        assert len(grouped["pending"]) == 1

    def test_summarise_of_an_empty_queue_has_every_key(self) -> None:
        grouped = summarise(QueueState(items=[], cutoff=NO_CUTOFF))
        assert set(grouped) == set(QUEUE_STATUSES)
        assert all(items == [] for items in grouped.values())

    def test_progress_counts(self) -> None:
        counters = progress(self._mixed_state())
        assert counters == {
            "position": 5,
            "total": 5,
            "imported": 1,
            "failed": 1,
            "skipped": 1,
            "no_importer": 1,
        }

    def test_progress_of_an_empty_queue_reports_position_zero(self) -> None:
        counters = progress(QueueState(items=[], cutoff=NO_CUTOFF))
        assert counters["position"] == 0
        assert counters["total"] == 0


# ── C. the routes ─────────────────────────────────────────────────────────────


@dataclass
class _StubParsedPoll:
    """Stands in for an importer's ParsedPoll (all eleven share this shape)."""

    fieldwork_start: date
    fieldwork_end: date
    sample_size: int


@dataclass
class _StubPlannedRow:
    """Stands in for a non-YouGov PlannedPollRow (no macro_region)."""

    party_name: str
    region_name: str
    percentage: float


@dataclass
class _StubPlan:
    """Stands in for an importer's ImportPlan, duck-typed like the real eleven."""

    parsed: _StubParsedPoll
    map_id: int
    map_name: str = WESTMINSTER_MAP_NAME
    pollster_identifier: str = "yougov"
    pollster_name: str = "YouGov"
    pollster_exists: bool = True
    poll_exists: bool = False
    poll_id: int | None = None
    rows: list[_StubPlannedRow] = field(
        default_factory=lambda: [_StubPlannedRow("Labour", "London", 31.0)]
    )


class _StubImporter:
    """Minimal stand-in for an importer module, recording what the routes call.

    Registered into a *copy* of ``IMPORTERS``, so the real modules — and any
    network access they would perform — are never reached.
    """

    DEFAULT_MAP_NAME = WESTMINSTER_MAP_NAME

    def __init__(self, plan: _StubPlan, *, poll_id: int = 4242) -> None:
        self.plan = plan
        self.build_error = ""
        self.build_calls = 0
        self.commit_calls = 0
        self.build_kwargs: dict[str, Any] = {}
        self.replace_rows_seen: bool | None = None
        self.result = PollImportResult(
            created_pollster=False,
            created_poll=True,
            poll_id=poll_id,
            inserted_rows=104,
            replaced_rows=0,
            skipped_existing_rows=False,
        )

    def build_import_plan(self, db: Database, **kwargs: Any) -> _StubPlan:
        """Return the canned plan, or raise the canned failure."""
        self.build_calls += 1
        self.build_kwargs = kwargs
        if self.build_error:
            raise RuntimeError(self.build_error)
        return self.plan

    def commit_import_plan(
        self, db: Database, plan: Any, replace_rows: bool
    ) -> PollImportResult:
        """Record the commit and return the canned result."""
        self.commit_calls += 1
        self.replace_rows_seen = replace_rows
        return self.result


def _stub_plan(
    map_id: int,
    *,
    start: date = date(2026, 8, 10),
    end: date = date(2026, 8, 12),
    sample_size: int = 2113,
    poll_exists: bool = False,
    poll_id: int | None = None,
) -> _StubPlan:
    """Build a stub ImportPlan for the Westminster map."""
    return _StubPlan(
        parsed=_StubParsedPoll(
            fieldwork_start=start,
            fieldwork_end=end,
            sample_size=sample_size,
        ),
        map_id=map_id,
        poll_exists=poll_exists,
        poll_id=poll_id,
    )


def _install_stub_importer(
    monkeypatch: pytest.MonkeyPatch,
    stub: _StubImporter,
    *,
    identifier: str = "yougov",
) -> None:
    """Point the blueprint's registry at the stub, leaving the real one alone."""
    registry: dict[str, ImporterMeta] = dict(IMPORTERS)
    registry[identifier] = {
        "label": "Stub Importer",
        "module": stub,
        "url_arg": "pdf_url",
    }
    monkeypatch.setattr("console.blueprints.poll_import.IMPORTERS", registry)


def _queue_state(
    *,
    identifier: str = "yougov",
    start: date = date(2026, 8, 10),
    end: date = date(2026, 8, 12),
    sample_size_label: str = "2,113",
    extra_rows: list[WikipediaPollRow] | None = None,
) -> QueueState:
    """Build a one- or many-item pending queue without consulting the database."""
    rows = [
        _row(
            identifier,
            start,
            end,
            source_url="https://example.test/tables.pdf",
            sample_size_label=sample_size_label,
        )
    ]
    rows.extend(extra_rows or [])
    return QueueState(
        items=[QueueItem(row=row) for row in rows],
        cutoff=date(2026, 7, 31),
    )


def _store(state: QueueState) -> str:
    """Cache a queue state the way ``wikipedia_start`` does, and return its token."""
    return store_preview({"type": WIKIPEDIA_PREVIEW_TYPE, "state": state})


_TOKEN_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", ""),
    ("POST", "/confirm"),
    ("POST", "/skip"),
    ("POST", "/finish"),
)


class TestWikipediaRoutesRegistered:
    """create_app wires all five catch-up rules with the right methods."""

    def test_rules_and_methods(self, app: Flask) -> None:
        methods = {
            str(rule): rule.methods or set() for rule in app.url_map.iter_rules()
        }
        assert "POST" in methods["/import/wikipedia/start"]
        assert "GET" in methods["/import/wikipedia/<token>"]
        assert "POST" in methods["/import/wikipedia/<token>/confirm"]
        assert "POST" in methods["/import/wikipedia/<token>/skip"]
        finish = methods["/import/wikipedia/<token>/finish"]
        assert {"GET", "POST"} <= finish

    def test_endpoints_exist(self, app: Flask) -> None:
        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
        for name in ("start", "queue", "confirm", "skip", "finish"):
            suffix = "wikipedia_queue" if name == "queue" else f"wikipedia_{name}"
            assert f"poll_import.{suffix}" in endpoints


class TestTokenGuard:
    """Every token route rejects a token it did not mint."""

    @pytest.mark.parametrize(("method", "suffix"), _TOKEN_ROUTES)
    def test_unknown_token_redirects_with_a_flash(
        self, app: Flask, method: str, suffix: str
    ) -> None:
        client = app.test_client()
        path = f"/import/wikipedia/deadbeef{suffix}"
        response = client.open(path, method=method)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/import")

        body = client.open(path, method=method, follow_redirects=True).get_data(
            as_text=True
        )
        assert "Queue expired or not found" in body

    @pytest.mark.parametrize(("method", "suffix"), _TOKEN_ROUTES)
    def test_foreign_payload_type_is_rejected(
        self, app: Flask, method: str, suffix: str
    ) -> None:
        token = store_preview({"type": "by_election", "plan": object()})
        client = app.test_client()
        path = f"/import/wikipedia/{token}{suffix}"
        response = client.open(path, method=method)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/import")

        body = client.open(path, method=method, follow_redirects=True).get_data(
            as_text=True
        )
        assert "Queue expired or not found" in body


class TestQueueStep:
    """The step screen builds the plan lazily and cross-references it."""

    def test_renders_the_plan_and_calls_the_importer_once(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        stub = _StubImporter(_stub_plan(seeded["map_id"]))
        _install_stub_importer(monkeypatch, stub)
        state = _queue_state()
        token = _store(state)

        body = app.test_client().get(f"/import/wikipedia/{token}").get_data(
            as_text=True
        )

        assert stub.build_calls == 1
        assert stub.build_kwargs["pdf_url"] == "https://example.test/tables.pdf"
        assert "Wikipedia Catch-Up" in body
        assert "Labour" in body
        assert state.items[0].plan is not None

    def test_date_disagreement_raises_a_warning(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The only proof of the mis-citation check: a document whose own dates
        # do not match the Wikipedia row that cited it.
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        stub = _StubImporter(
            _stub_plan(
                seeded["map_id"],
                start=date(2026, 7, 6),
                end=date(2026, 7, 8),
            )
        )
        _install_stub_importer(monkeypatch, stub)
        token = _store(_queue_state())

        body = app.test_client().get(f"/import/wikipedia/{token}").get_data(
            as_text=True
        )

        assert (
            "Document dates (2026-07-06 to 2026-07-08) disagree with Wikipedia "
            "(2026-08-10 to 2026-08-12)." in body
        )

    def test_sample_size_disagreement_raises_a_warning(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        stub = _StubImporter(_stub_plan(seeded["map_id"], sample_size=2205))
        _install_stub_importer(monkeypatch, stub)
        token = _store(_queue_state(sample_size_label="2,113"))

        body = app.test_client().get(f"/import/wikipedia/{token}").get_data(
            as_text=True
        )

        assert "Sample size differs: document 2205 vs Wikipedia 2113." in body

    def test_replace_rows_is_hidden_unless_the_poll_already_exists(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # replace_rows deletes every existing row of the matched poll, so it must
        # not be offerable on a poll that is not there.
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        _install_stub_importer(monkeypatch, _StubImporter(_stub_plan(seeded["map_id"])))
        token = _store(_queue_state())

        body = app.test_client().get(f"/import/wikipedia/{token}").get_data(
            as_text=True
        )

        assert 'name="replace_rows"' not in body

    def test_replace_rows_is_offered_when_the_poll_already_exists(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        _install_stub_importer(
            monkeypatch,
            _StubImporter(
                _stub_plan(seeded["map_id"], poll_exists=True, poll_id=77)
            ),
        )
        token = _store(_queue_state())

        body = app.test_client().get(f"/import/wikipedia/{token}").get_data(
            as_text=True
        )

        assert 'name="replace_rows"' in body
        assert "already exists (#77)" in body

    def test_a_build_failure_marks_the_item_and_holds_the_cursor(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        stub = _StubImporter(_stub_plan(seeded["map_id"]))
        stub.build_error = "404 Not Found: tables.pdf"
        _install_stub_importer(monkeypatch, stub)
        state = _queue_state()
        token = _store(state)

        response = app.test_client().get(f"/import/wikipedia/{token}")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert state.items[0].status == "failed"
        assert state.index == 0
        assert "404 Not Found: tables.pdf" in body
        assert "Retry" in body

    def test_an_exhausted_queue_redirects_to_the_summary(self, app: Flask) -> None:
        state = _queue_state()
        _mark(state.items[0], "skipped")
        state.index = 1
        token = _store(state)
        response = app.test_client().get(f"/import/wikipedia/{token}")
        assert response.status_code == 302
        assert response.headers["Location"].endswith(
            f"/import/wikipedia/{token}/finish"
        )


class TestConfirm:
    """Confirming commits exactly what was previewed, once."""

    def test_commits_and_advances(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        stub = _StubImporter(_stub_plan(seeded["map_id"]))
        _install_stub_importer(monkeypatch, stub)
        state = _queue_state()
        state.items[0].plan = stub.plan
        token = _store(state)

        response = app.test_client().post(
            f"/import/wikipedia/{token}/confirm", data={"expected_index": "0"}
        )

        assert response.status_code == 302
        assert stub.commit_calls == 1
        assert stub.replace_rows_seen is False
        assert state.items[0].status == "imported"
        assert state.items[0].poll_id == 4242
        assert state.items[0].detail == "Poll #4242, 104 rows inserted"
        assert state.items[0].plan is None
        assert state.index == 1

    def test_stale_expected_index_neither_commits_nor_advances(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The back button and double submits both replay a decided step; acting
        # on one would advance the cursor past an unreviewed poll.
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        stub = _StubImporter(_stub_plan(seeded["map_id"]))
        _install_stub_importer(monkeypatch, stub)
        state = _queue_state(
            extra_rows=[_row("opinium", date(2026, 8, 13), date(2026, 8, 15))]
        )
        state.items[0].plan = stub.plan
        token = _store(state)

        client = app.test_client()
        response = client.post(
            f"/import/wikipedia/{token}/confirm", data={"expected_index": "1"}
        )

        assert response.status_code == 302
        assert stub.commit_calls == 0
        assert state.index == 0
        assert state.items[0].status == "pending"

        body = client.get(f"/import/wikipedia/{token}").get_data(as_text=True)
        assert "That step has already been actioned." in body

    def test_non_numeric_expected_index_is_rejected(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        stub = _StubImporter(_stub_plan(seeded["map_id"]))
        _install_stub_importer(monkeypatch, stub)
        state = _queue_state()
        state.items[0].plan = stub.plan
        token = _store(state)

        response = app.test_client().post(
            f"/import/wikipedia/{token}/confirm", data={"expected_index": "first"}
        )

        assert response.status_code == 302
        assert stub.commit_calls == 0
        assert state.index == 0

    def test_a_poll_imported_since_the_preview_is_skipped_not_committed(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        stub = _StubImporter(_stub_plan(seeded["map_id"]))
        _install_stub_importer(monkeypatch, stub)
        state = _queue_state()
        state.items[0].plan = stub.plan
        token = _store(state)

        # The poll lands in the database between the preview and the confirm —
        # a second queue, or the manual flow, importing the same row.
        db.add_poll(
            seeded["yougov_id"],
            seeded["map_id"],
            date(2026, 8, 10),
            date(2026, 8, 12),
            sample_size=999,
        )

        app.test_client().post(
            f"/import/wikipedia/{token}/confirm", data={"expected_index": "0"}
        )

        assert stub.commit_calls == 0
        assert state.items[0].status == "skipped"
        assert state.items[0].detail == "Already in the database"
        assert state.index == 1

    def test_a_commit_failure_holds_the_cursor(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        stub = _StubImporter(_stub_plan(seeded["map_id"]))
        _install_stub_importer(monkeypatch, stub)

        def _explode(db: Database, plan: Any, replace_rows: bool) -> PollImportResult:
            raise RuntimeError("constraint failed")

        monkeypatch.setattr(stub, "commit_import_plan", _explode)
        state = _queue_state()
        state.items[0].plan = stub.plan
        token = _store(state)

        app.test_client().post(
            f"/import/wikipedia/{token}/confirm", data={"expected_index": "0"}
        )

        assert state.items[0].status == "failed"
        assert state.items[0].detail == "constraint failed"
        assert state.index == 0


class TestSkipAndRetry:
    """Skip advances; retry re-arms the item without moving the cursor."""

    def test_skip_advances_and_records_a_reason(self, app: Flask) -> None:
        state = _queue_state()
        token = _store(state)
        app.test_client().post(
            f"/import/wikipedia/{token}/skip", data={"expected_index": "0"}
        )
        assert state.items[0].status == "skipped"
        assert state.items[0].detail == "Skipped"
        assert state.index == 1

    def test_skipping_a_failed_item_keeps_its_error(self, app: Flask) -> None:
        state = _queue_state()
        _mark(state.items[0], "failed", detail="404 Not Found")
        token = _store(state)
        app.test_client().post(
            f"/import/wikipedia/{token}/skip", data={"expected_index": "0"}
        )
        assert state.items[0].detail == "Skipped after failure: 404 Not Found"

    def test_retry_re_arms_the_item_in_place(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        _install_stub_importer(monkeypatch, _StubImporter(_stub_plan(seeded["map_id"])))
        state = _queue_state()
        _mark(state.items[0], "failed", detail="404 Not Found")
        state.items[0].warnings = ["stale"]
        token = _store(state)

        app.test_client().post(
            f"/import/wikipedia/{token}/skip",
            data={"expected_index": "0", "action": "retry"},
        )

        assert state.items[0].status == "pending"
        assert state.items[0].detail == ""
        assert state.items[0].plan is None
        assert state.items[0].warnings == []
        assert state.index == 0


class TestFinish:
    """The summary reports the run and triggers the model at most once."""

    def _imported_state(self) -> QueueState:
        state = _queue_state()
        _mark(state.items[0], "imported", detail="Poll #4242, 104 rows inserted")
        state.items[0].poll_id = 4242
        state.index = 1
        return state

    def test_model_runs_exactly_once_across_repeated_summary_loads(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Keeping the token alive so a refresh still shows the report means F5
        # would otherwise re-launch a half-hour subprocess every time.
        calls: list[int] = []

        def _fake_run(*, timeout: int = 0) -> list[str]:
            calls.append(timeout)
            return ["UNS model updated.", "Prediction simulation exported."]

        monkeypatch.setattr(
            "console.blueprints.poll_import._run_model_and_export", _fake_run
        )
        token = _store(self._imported_state())
        client = app.test_client()

        first = client.get(f"/import/wikipedia/{token}/finish").get_data(as_text=True)
        client.get(f"/import/wikipedia/{token}/finish")
        client.get(f"/import/wikipedia/{token}/finish")

        assert len(calls) == 1
        assert "UNS model updated." in first
        assert "Wikipedia Catch-Up Summary" in first

    def test_model_does_not_run_on_abandon(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        def _fake_run(*, timeout: int = 0) -> list[str]:
            calls.append(timeout)
            return []

        monkeypatch.setattr(
            "console.blueprints.poll_import._run_model_and_export", _fake_run
        )
        state = self._imported_state()
        token = _store(state)

        response = app.test_client().post(
            f"/import/wikipedia/{token}/finish", data={"abandon": "on"}
        )

        assert response.status_code == 200
        assert calls == []
        assert state.run_model_at_end is False

    def test_model_does_not_run_when_nothing_was_imported(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        def _fake_run(*, timeout: int = 0) -> list[str]:
            calls.append(timeout)
            return []

        monkeypatch.setattr(
            "console.blueprints.poll_import._run_model_and_export", _fake_run
        )
        state = _queue_state()
        _mark(state.items[0], "skipped", detail="Skipped")
        state.index = 1
        token = _store(state)

        body = app.test_client().get(f"/import/wikipedia/{token}/finish").get_data(
            as_text=True
        )

        assert calls == []
        assert "Nothing was imported." in body

    def test_a_model_failure_is_reported_and_not_retried(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        def _fake_run(*, timeout: int = 0) -> list[str]:
            calls.append(timeout)
            raise RuntimeError("model exited 1")

        monkeypatch.setattr(
            "console.blueprints.poll_import._run_model_and_export", _fake_run
        )
        token = _store(self._imported_state())
        client = app.test_client()

        body = client.get(f"/import/wikipedia/{token}/finish").get_data(as_text=True)
        client.get(f"/import/wikipedia/{token}/finish")

        assert len(calls) == 1
        assert "UNS model run failed: model exited 1" in body

    def test_summary_reports_the_scrape_drop_counts(self, app: Flask) -> None:
        state = self._imported_state()
        state.skipped_present = 3
        state.skipped_unparsed = 2
        state.unrecognised_areas = {"Area": 3, "TBA": 1}
        token = _store(state)

        body = app.test_client().get(f"/import/wikipedia/{token}/finish").get_data(
            as_text=True
        )

        assert "3 row(s) in the window were already in the database" in body
        assert "2 Wikipedia row(s) could not be read" in body
        assert "TBA" in body

    def test_no_cutoff_renders_as_all_polls(self, app: Flask) -> None:
        state = self._imported_state()
        state.cutoff = NO_CUTOFF
        state.run_model_at_end = False
        token = _store(state)

        body = app.test_client().get(f"/import/wikipedia/{token}/finish").get_data(
            as_text=True
        )

        assert "all polls" in body
        assert "0001-01-01" not in body


class TestStart:
    """The start route turns a scrape into a queue without touching the network."""

    def test_builds_a_queue_and_redirects_to_the_first_step(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        monkeypatch.setattr(
            "console.blueprints.poll_import.fetch_poll_index",
            lambda: fetch_poll_index(html=_INDEX_HTML),
        )

        response = app.test_client().post(
            "/import/wikipedia/start",
            data={"cutoff_date": "2026-01-01", "run_model_at_end": "on"},
        )

        assert response.status_code == 302
        location = response.headers["Location"]
        token = location.rsplit("/", 1)[-1]
        payload = PREVIEW_CACHE[token]
        assert payload["type"] == WIKIPEDIA_PREVIEW_TYPE
        state: QueueState = payload["state"]
        assert state.cutoff == date(2026, 1, 1)
        assert state.run_model_at_end is True
        # Sorted by parsed fieldwork end, so the cross-year Survation row leads
        # despite sitting last in the document. The 2025 More in Common row
        # falls outside the window; Deltapoll has no resolvable document, so it
        # is queued but pre-marked.
        assert [item.row.pollster_identifier for item in state.items] == [
            "survation",
            "lord_ashcroft",
            "find_out_now",
            "opinium",
            "yougov",
            "deltapoll",
        ]
        assert state.items[5].status == "no_importer"
        assert state.index == 0
        assert state.unrecognised_areas == {"Area": 2, "TBA": 1}

    def test_an_unticked_checkbox_turns_the_model_run_off(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An unticked HTML checkbox posts nothing at all, so the route has to
        # coerce it rather than let the form model's True default stand.
        _seed_westminster(db)
        monkeypatch.setattr("console.blueprints.poll_import.get_db", lambda: db)
        monkeypatch.setattr(
            "console.blueprints.poll_import.fetch_poll_index",
            lambda: fetch_poll_index(html=_INDEX_HTML),
        )

        response = app.test_client().post("/import/wikipedia/start", data={})

        token = response.headers["Location"].rsplit("/", 1)[-1]
        state: QueueState = PREVIEW_CACHE[token]["state"]
        assert state.run_model_at_end is False
        assert state.cutoff == NO_CUTOFF

    def test_a_bad_cutoff_date_redirects_with_a_flash(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _never_called() -> PollIndex:
            raise AssertionError("the page must not be fetched for a bad date")

        monkeypatch.setattr(
            "console.blueprints.poll_import.fetch_poll_index", _never_called
        )
        body = app.test_client().post(
            "/import/wikipedia/start",
            data={"cutoff_date": "31/08/2026"},
            follow_redirects=True,
        ).get_data(as_text=True)

        assert "is not a valid date" in body

    def test_a_scrape_failure_redirects_with_a_flash(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise() -> PollIndex:
            raise WikipediaIndexError("layout changed")

        monkeypatch.setattr("console.blueprints.poll_import.fetch_poll_index", _raise)
        body = app.test_client().post(
            "/import/wikipedia/start", data={}, follow_redirects=True
        ).get_data(as_text=True)

        assert "Wikipedia page could not be read: layout changed" in body
