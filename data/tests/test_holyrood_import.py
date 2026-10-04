"""Unit tests for the Holyrood Wikipedia poll importer.

Parsing tests use synthetic HTML only. ``commit_polls``, ``_ensure_pollster``,
``_pollster_identifier``, ``main`` and ``fetch_html`` additionally use the ``db``
fixture and a monkeypatched ``fetch_html``/``urlopen`` — still no network access
or the live database.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from urllib.request import Request

import pytest

from db import Database
from polls.importers import wikipedia_common
from polls.importers.holyrood import holyrood_wikipedia_import as hw_import
from polls.importers.holyrood.holyrood_wikipedia_import import (
    BALLOT_CONSTITUENCY,
    BALLOT_LIST,
    ParsedScottishPoll,
    _ensure_pollster,
    _pollster_identifier,
    commit_polls,
    fetch_html,
    identify_party_columns,
    main,
    parse_polls as parse_constituency_polls,
    parse_date_range,
)
from tests.uk_fixtures import HolyroodWorld, add_poll_with_rows, seed_holyrood_world

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


# ── Election reference rows ───────────────────────────────────────────────────


_ANALYSIS_HEADER = """
<tr>
  <th>Dates conducted</th>
  <th>Polling firm</th>
  <th>Pollsters Analysis</th>
  <th>Sample<br/>size</th>
  <th>Con</th>
  <th>Lab</th>
  <th>LD</th>
  <th>SNP</th>
  <th>Green</th>
  <th>Alba</th>
  <th>Lead</th>
</tr>
"""

# A real poll on the "next election" page: 11 cells against the 11-cell header.
_ANALYSIS_POLL_ROW = """
<tr>
  <td>13–24 Aug 2026</td>
  <td>Survation</td>
  <td>Diffley</td>
  <td>2,036</td>
  <td>11</td>
  <td>17</td>
  <td>10</td>
  <td>37</td>
  <td>5</td>
  <td>1</td>
  <td>SNP 20</td>
</tr>
"""

# The last-election reference row omits the analysis cell, so every party
# column shifts left by one and the parsed figures are garbage.
_ANALYSIS_ELECTION_ROW = """
<tr>
  <td>7 May 2026</td>
  <td>2026 Scottish Parliament election</td>
  <td>—</td>
  <td>19.2</td>
  <td>19.0</td>
  <td>11.5</td>
  <td>38.4</td>
  <td>2.3</td>
  <td>0.8</td>
  <td>SNP 19</td>
</tr>
"""


class TestElectionReferenceRows:
    """The last-election row is not a poll and must never be imported."""

    def test_election_row_is_skipped_and_the_real_poll_is_kept(self) -> None:
        html = _make_html(_ANALYSIS_HEADER + _ANALYSIS_POLL_ROW + _ANALYSIS_ELECTION_ROW)
        polls = parse_constituency_polls(html)
        assert [p.pollster_name for p in polls] == ["Survation"]

    def test_election_row_alone_yields_nothing(self) -> None:
        html = _make_html(_ANALYSIS_HEADER + _ANALYSIS_ELECTION_ROW)
        assert parse_constituency_polls(html) == []

    def test_historic_election_label_also_skipped(self) -> None:
        html = _make_html(_TABLE_HEADER + _TABLE_ROW_BASIC + _TABLE_ROW_ELECTION)
        polls = parse_constituency_polls(html)
        assert [p.pollster_name for p in polls] == ["Savanta"]

    def test_a_row_one_cell_short_of_the_header_is_still_imported(self) -> None:
        # Legitimate poll rows are routinely short (16 per table on the 2026
        # page), so the guard must key off the pollster name, not cell count.
        short = """
        <tr>
          <td>1–3 Feb 2026</td>
          <td>Norstat</td>
          <td>1,020</td>
          <td>18</td>
          <td>27</td>
          <td>8</td>
          <td>31</td>
          <td>10</td>
          <td>2</td>
        </tr>
        """
        html = _make_html(_TABLE_HEADER + short)
        polls = parse_constituency_polls(html)
        assert [p.pollster_name for p in polls] == ["Norstat"]

    def test_pollsters_analysis_layout_maps_party_columns(self) -> None:
        html = _make_html(_ANALYSIS_HEADER + _ANALYSIS_POLL_ROW)
        polls = parse_constituency_polls(html)
        assert polls[0].party_percentages == {
            "Conservative": 11.0,
            "Labour": 17.0,
            "Liberal Democrats": 10.0,
            "Scottish National Party": 37.0,
            "Scottish Greens": 5.0,
            "Alba Party": 1.0,
        }

    def test_samplesize_header_is_recognised(self) -> None:
        # The header renders as "Sample<br/>size", which collapses to
        # "Samplesize" — previously unmatched, so sizes were never imported.
        html = _make_html(_ANALYSIS_HEADER + _ANALYSIS_POLL_ROW)
        assert parse_constituency_polls(html)[0].sample_size == 2036


# ── fetch_html ─────────────────────────────────────────────────────────────────


class _FakeWikiResponse:
    """A minimal ``urlopen`` stand-in for ``wikipedia_common.fetch_html``.

    Unlike :class:`tests.uk_fixtures.FakeUrlResponse`, ``read`` accepts the
    bounded ``max_bytes + 1`` argument ``wikipedia_common.fetch_html`` passes.
    """

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeWikiResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self, _n: int = -1) -> bytes:
        return self._body


class TestFetchHtml:
    """Tests for fetch_html — the Holyrood importer's User-Agent wrapper."""

    def test_decodes_the_body_from_a_monkeypatched_urlopen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[str, int]] = []

        def fake_urlopen(req: Request, timeout: int) -> _FakeWikiResponse:
            seen.append((req.get_header("User-agent") or "", timeout))
            return _FakeWikiResponse("<html>hello</html>".encode())

        monkeypatch.setattr(wikipedia_common, "urlopen", fake_urlopen)

        result = fetch_html("https://en.wikipedia.org/wiki/Example")

        assert result == "<html>hello</html>"
        # Spelled out as a literal (not compared against the module's own
        # HOLYROOD_USER_AGENT constant) so a change to that constant itself
        # — including a wrong one — cannot pass silently.
        assert seen == [
            ("Mozilla/5.0 (compatible; holyrood-poll-importer/1.0)", 30)
        ]
        # And not wikipedia_common's own default User-Agent.
        assert seen[0][0] != wikipedia_common.DEFAULT_USER_AGENT


# ── _pollster_identifier ──────────────────────────────────────────────────────


class TestPollsterIdentifier:
    """Tests for _pollster_identifier — slug plus ballot-specific suffix."""

    @pytest.mark.parametrize(
        ("name", "ballot", "expected"),
        [
            ("Survation", BALLOT_CONSTITUENCY, "survation_holyrood"),
            ("Survation", BALLOT_LIST, "survation_holyrood_list"),
            ("Ipsos MORI", BALLOT_CONSTITUENCY, "ipsos_mori_holyrood"),
            ("  Yonder!  ", BALLOT_CONSTITUENCY, "yonder_holyrood"),
            (
                "Panelbase / Sunday Times",
                BALLOT_CONSTITUENCY,
                "panelbase_sunday_times_holyrood",
            ),
        ],
    )
    def test_slug_and_suffix(self, name: str, ballot: str, expected: str) -> None:
        assert _pollster_identifier(name, ballot) == expected

    def test_default_ballot_is_constituency(self) -> None:
        assert _pollster_identifier("Survation") == "survation_holyrood"


# ── _ensure_pollster ──────────────────────────────────────────────────────────


class TestEnsurePollster:
    """Tests for _ensure_pollster — create-or-reuse a Pollster by identifier."""

    def test_creates_a_pollster_when_absent(self, db: Database) -> None:
        seed_holyrood_world(db)

        pollster = _ensure_pollster(db, "survation_holyrood", "Survation (Holyrood)")

        assert pollster.identifier == "survation_holyrood"
        assert pollster.name == "Survation (Holyrood)"
        assert len(db.get_all_pollsters()) == 1

    def test_reuses_an_existing_pollster_by_identifier(self, db: Database) -> None:
        seed_holyrood_world(db)
        first = _ensure_pollster(db, "survation_holyrood", "Survation (Holyrood)")

        # A different display name is passed the second time; reuse must keep
        # the original row rather than creating (or renaming into) a new one.
        second = _ensure_pollster(db, "survation_holyrood", "A Different Name")

        assert second.id == first.id
        assert second.name == "Survation (Holyrood)"
        assert len(db.get_all_pollsters()) == 1


# ── commit_polls ─────────────────────────────────────────────────────────────


def _poll(
    *,
    pollster_name: str = "Survation",
    fieldwork_start: date = date(2026, 3, 1),
    fieldwork_end: date = date(2026, 3, 3),
    sample_size: int | None = 1000,
    party_percentages: dict[str, float] | None = None,
) -> ParsedScottishPoll:
    """Build a ParsedScottishPoll for commit_polls tests, bypassing HTML parsing."""
    return ParsedScottishPoll(
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        pollster_name=pollster_name,
        sample_size=sample_size,
        party_percentages=dict(
            party_percentages if party_percentages is not None else {"Labour": 30.0}
        ),
    )


class TestCommitPolls:
    """Tests for commit_polls — inserting parsed polls into the database."""

    def test_map_missing_raises(self, db: Database) -> None:
        seed_holyrood_world(db)

        with pytest.raises(ValueError, match=re.escape("Map not found: 'No Such Map'")):
            commit_polls(db, [_poll()], map_name="No Such Map")

    @pytest.mark.parametrize(
        ("ballot", "identifier_suffix", "label"),
        [
            (BALLOT_CONSTITUENCY, "_holyrood", "Holyrood"),
            (BALLOT_LIST, "_holyrood_list", "Holyrood list"),
        ],
    )
    def test_pollster_created_once_per_identifier(
        self, db: Database, ballot: str, identifier_suffix: str, label: str
    ) -> None:
        world: HolyroodWorld = seed_holyrood_world(db)
        polls = [
            _poll(
                fieldwork_start=date(2026, 3, 1),
                fieldwork_end=date(2026, 3, 3),
                party_percentages={"Labour": 30.0},
            ),
            _poll(
                fieldwork_start=date(2026, 3, 8),
                fieldwork_end=date(2026, 3, 10),
                party_percentages={"Labour": 31.0},
            ),
        ]

        counts = commit_polls(db, polls, map_name=world.map_name, ballot=ballot)

        assert counts == {"created": 2, "skipped": 0, "unknown_parties": 0}
        pollsters = db.get_all_pollsters()
        assert len(pollsters) == 1
        assert pollsters[0].identifier == f"survation{identifier_suffix}"
        assert pollsters[0].name == f"Survation ({label})"

    def test_existing_poll_same_pollster_map_and_dates_is_skipped(
        self, db: Database
    ) -> None:
        world: HolyroodWorld = seed_holyrood_world(db)
        seeded = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="survation_holyrood",
            pollster_name="Survation (Holyrood)",
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 3),
            national={world.party_ids["Labour"]: 29.0},
        )
        poll = _poll(
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 3),
            party_percentages={"Labour": 30.0},
        )

        counts = commit_polls(db, [poll], map_name=world.map_name)

        assert counts == {"created": 0, "skipped": 1, "unknown_parties": 0}
        polls_for_map = db.get_polls_for_map(world.map_id)
        assert len(polls_for_map) == 1
        assert polls_for_map[0].id == seeded.id

    @pytest.mark.parametrize(
        ("fieldwork_start", "fieldwork_end"),
        [
            (date(2026, 3, 2), date(2026, 3, 3)),  # start shifted, end matches
            (date(2026, 3, 1), date(2026, 3, 4)),  # start matches, end shifted
        ],
        ids=["shifted_start", "shifted_end"],
    )
    def test_different_fieldwork_dates_are_not_treated_as_existing(
        self, db: Database, fieldwork_start: date, fieldwork_end: date
    ) -> None:
        # The existing-poll match requires all four keys (pollster, map, start,
        # end); moving only one date by one day must still create a new poll,
        # proving both the start- and end-date columns are part of the filter
        # (not just one of them).
        world: HolyroodWorld = seed_holyrood_world(db)
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="survation_holyrood",
            pollster_name="Survation (Holyrood)",
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 3),
            national={world.party_ids["Labour"]: 29.0},
        )
        poll = _poll(
            fieldwork_start=fieldwork_start,
            fieldwork_end=fieldwork_end,
            party_percentages={"Labour": 30.0},
        )

        counts = commit_polls(db, [poll], map_name=world.map_name)

        assert counts == {"created": 1, "skipped": 0, "unknown_parties": 0}
        assert len(db.get_polls_for_map(world.map_id)) == 2

    def test_matching_poll_on_a_different_map_is_not_treated_as_existing(
        self, db: Database
    ) -> None:
        # The existing-poll match also filters on map_id; a matching pollster
        # with matching dates but seeded on a *different* map must not be
        # treated as existing when committing into world.map_name.
        world: HolyroodWorld = seed_holyrood_world(db)
        other_map = db.add_map(
            "Scottish Parliament Constituencies 2021", parliament="holyrood"
        )
        add_poll_with_rows(
            db,
            map_id=other_map.id,
            pollster_identifier="survation_holyrood",
            pollster_name="Survation (Holyrood)",
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 3),
            national={world.party_ids["Labour"]: 29.0},
        )
        poll = _poll(
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 3),
            party_percentages={"Labour": 30.0},
        )

        counts = commit_polls(db, [poll], map_name=world.map_name)

        assert counts == {"created": 1, "skipped": 0, "unknown_parties": 0}
        assert len(db.get_polls_for_map(world.map_id)) == 1
        assert len(db.get_polls_for_map(other_map.id)) == 1

    def test_same_dates_but_a_different_pollster_is_not_treated_as_existing(
        self, db: Database
    ) -> None:
        world: HolyroodWorld = seed_holyrood_world(db)
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="survation_holyrood",
            pollster_name="Survation (Holyrood)",
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 3),
            national={world.party_ids["Labour"]: 29.0},
        )
        poll = _poll(
            pollster_name="Panelbase",
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 3),
            party_percentages={"Labour": 30.0},
        )

        counts = commit_polls(db, [poll], map_name=world.map_name)

        assert counts == {"created": 1, "skipped": 0, "unknown_parties": 0}
        assert len(db.get_polls_for_map(world.map_id)) == 2
        assert db.get_pollster_by_identifier("panelbase_holyrood") is not None

    def test_unknown_party_is_counted_and_warned(
        self, db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        world: HolyroodWorld = seed_holyrood_world(db)
        poll = _poll(party_percentages={"Labour": 30.0, "Workers Party": 4.0})

        counts = commit_polls(db, [poll], map_name=world.map_name)

        assert counts == {"created": 1, "skipped": 0, "unknown_parties": 1}
        out = capsys.readouterr().out.splitlines()
        assert "  WARNING: party not found in DB: 'Workers Party'" in out
        rows = db.get_rows_for_poll(db.get_polls_for_map(world.map_id)[0].id)
        assert len(rows) == 1
        assert rows[0].party_id == world.party_ids["Labour"]

    def test_poll_with_only_unknown_parties_is_not_inserted(
        self, db: Database
    ) -> None:
        world: HolyroodWorld = seed_holyrood_world(db)
        poll = _poll(party_percentages={"Workers Party": 4.0, "TUSC": 1.0})

        counts = commit_polls(db, [poll], map_name=world.map_name)

        assert counts == {"created": 0, "skipped": 0, "unknown_parties": 2}
        assert db.get_polls_for_map(world.map_id) == []
        # Documented current behaviour, not pinned as a bug: _ensure_pollster
        # runs before the per-party loop discovers every party is unknown, so
        # an all-unknown poll still leaves a pollster row with no polls.
        assert db.get_pollster_by_identifier("survation_holyrood") is not None

    def test_dry_run_counts_created_without_writing(
        self, db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        world: HolyroodWorld = seed_holyrood_world(db)
        poll = _poll(
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 3),
            sample_size=1500,
            party_percentages={"Labour": 30.0},
        )

        counts = commit_polls(db, [poll], map_name=world.map_name, dry_run=True)

        assert counts == {"created": 1, "skipped": 0, "unknown_parties": 0}
        assert db.get_all_pollsters() == []
        assert db.get_polls_for_map(world.map_id) == []
        out = capsys.readouterr().out.splitlines()
        assert (
            "  [dry-run] 2026-03-01–2026-03-03 'Survation' (survation_holyrood) "
            "n=1500 parties=['Labour']" in out
        )

    def test_dry_run_does_not_check_for_existing_polls_pins_current_behaviour(
        self, db: Database
    ) -> None:
        """dry_run skips the whole pollster/existing-poll block, so a dry run
        reports "created" for a poll a real run would skip as a duplicate — the
        docstring's "already exist ... are skipped" promise does not hold
        under --dry-run.
        """
        world: HolyroodWorld = seed_holyrood_world(db)
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="survation_holyrood",
            pollster_name="Survation (Holyrood)",
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 3),
            national={world.party_ids["Labour"]: 29.0},
        )
        poll = _poll(
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 3),
            party_percentages={"Labour": 30.0},
        )

        counts = commit_polls(db, [poll], map_name=world.map_name, dry_run=True)

        assert counts == {"created": 1, "skipped": 0, "unknown_parties": 0}


# ── main ─────────────────────────────────────────────────────────────────────


_LIST_TABLE_ROW = """
<tr>
  <td>1–3 Feb 2026</td>
  <td>Panelbase</td>
  <td>1,050</td>
  <td>19</td>
  <td>26</td>
  <td>7</td>
  <td>32</td>
  <td>11</td>
  <td>2</td>
  <td>SNP 6</td>
</tr>
"""


def _make_list_html(rows: str) -> str:
    """Wrap rows in a full regional (list) vote page structure."""
    return f"""
    <html><body>
    <div class="mw-heading mw-heading2">
      <h2 id="Regional_vote">Regional vote</h2>
    </div>
    <table class="wikitable">
      {rows}
    </table>
    </body></html>
    """


class TestMain:
    """Tests for main — the CLI entry point."""

    def test_no_polls_found_returns_early(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        html = """
        <html><body><h2>Some other section</h2><p>no table here</p></body></html>
        """
        monkeypatch.setattr(hw_import, "fetch_html", lambda url: html)

        def _no_database(*args: object, **kwargs: object) -> Database:
            raise AssertionError("Database should not be constructed")

        monkeypatch.setattr(hw_import, "Database", _no_database)
        monkeypatch.setattr(sys, "argv", ["holyrood_wikipedia_import.py"])

        main()

        out = capsys.readouterr().out.splitlines()
        assert "No polls found — check the page structure or URL" in out

    def test_list_ballot_commits_with_the_list_suffix_and_forwards_the_url(
        self,
        db: Database,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world: HolyroodWorld = seed_holyrood_world(db)
        requested: list[str] = []
        list_html = _make_list_html(_TABLE_HEADER + _LIST_TABLE_ROW)

        def fake_fetch_html(url: str) -> str:
            requested.append(url)
            return list_html

        monkeypatch.setattr(hw_import, "fetch_html", fake_fetch_html)
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        custom_url = "https://en.wikipedia.org/wiki/Custom_Holyrood_Page"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "holyrood_wikipedia_import.py",
                "--ballot",
                "list",
                "--map-name",
                world.map_name,
                "--url",
                custom_url,
            ],
        )

        main()

        # --url reached fetch_html rather than main() silently falling back to
        # the module's own WIKI_URL default.
        assert requested == [custom_url]
        out = capsys.readouterr().out.splitlines()
        assert "Parsed 1 list VI polls from Wikipedia table" in out
        assert "Done: created=1 skipped=0 unknown_parties=0" in out
        # A non-dry-run commit must not print the dry-run-only message.
        assert "Dry-run: no data written" not in out
        pollster = db.get_pollster_by_identifier("panelbase_holyrood_list")
        assert pollster is not None
        assert pollster.name == "Panelbase (Holyrood list)"
        polls = db.get_polls_by_pollster(pollster.id)
        assert len(polls) == 1
        # --url also reaches commit_polls' source_url, not just fetch_html.
        assert polls[0].source_url == custom_url

    def test_dry_run_message(
        self,
        db: Database,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world: HolyroodWorld = seed_holyrood_world(db)
        html = _make_html(_TABLE_HEADER + _TABLE_ROW_BASIC)
        monkeypatch.setattr(hw_import, "fetch_html", lambda url: html)
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        monkeypatch.setattr(
            sys,
            "argv",
            ["holyrood_wikipedia_import.py", "--dry-run", "--map-name", world.map_name],
        )

        main()

        out = capsys.readouterr().out.splitlines()
        assert "Dry-run: no data written" in out
        assert db.get_all_pollsters() == []

    def test_map_name_flag_reaches_commit_polls(
        self,
        db: Database,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seed_holyrood_world(db)
        html = _make_html(_TABLE_HEADER + _TABLE_ROW_BASIC)
        monkeypatch.setattr(hw_import, "fetch_html", lambda url: html)
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        monkeypatch.setattr(
            sys,
            "argv",
            ["holyrood_wikipedia_import.py", "--map-name", "Not The Seeded Map"],
        )

        with pytest.raises(
            ValueError, match=re.escape("Map not found: 'Not The Seeded Map'")
        ):
            main()
