"""Unit tests for the 2026 Holyrood results scraper.

All tests use synthetic HTML — no network access or database required.

The constituency table's real shape is ``[seat] + 7 x (swatch, data)``, so a
full data row has 15 cells. One real row (``Glasgow Central``) has 14: its
trailing "Other" *data* cell is absent while the swatch remains. The helpers
below build both shapes, and a third where a data cell is missing *mid*-row —
which must never be parsed positionally.
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup, Tag

from scripts.scrape_holyrood_2026 import (
    MapIndex,
    build_list_payload,
    cell_text,
    dhondt_allocate,
    has_background_style,
    is_swatch_cell,
    parse_constituency_row,
    parse_header_party_keys,
    parse_int,
    parse_other_cell,
    parse_party_cell,
    parse_region_table,
)

# ── Synthetic HTML helpers ────────────────────────────────────────────────────

# Party keys in the order both tables present them.
PARTY_KEYS = ["snp", "labour", "conservative", "green", "libdems", "reform", "others"]

_CONSTITUENCY_HEADER = """
<tr>
  <th>Seat</th>
  <th colspan="2">SNP</th>
  <th colspan="2">Labour</th>
  <th colspan="2">Conservative</th>
  <th colspan="2">Greens</th>
  <th colspan="2">Lib Dem</th>
  <th colspan="2">Reform UK</th>
  <th colspan="2">Other</th>
</tr>
"""

# The regional list-vote table spells two headers differently and adds a
# trailing "Total" column that must be skipped.
_REGION_HEADER = """
<tr>
  <th>Seat</th>
  <th>SNP</th>
  <th>Labour</th>
  <th>Conservative</th>
  <th>Greens</th>
  <th>Lib Dems</th>
  <th>Reform UK</th>
  <th>Other</th>
  <th>Total</th>
</tr>
"""

_SWATCH = '<td width="1" bgcolor="#FFF9C4"></td>'

# Real Central Scotland and Lothians West regional list votes.
CSLW_VOTES = {
    "snp": 86809,
    "labour": 57103,
    "conservative": 19450,
    "green": 34415,
    "libdems": 12830,
    "reform": 58334,
}


def _as_tag(html: str) -> Tag:
    """Parse an HTML fragment and return its first element.

    Args:
        html: A single ``<tr>`` or ``<table>`` element as raw HTML.

    Returns:
        The parsed Tag.
    """
    soup = BeautifulSoup(f"<table>{html}</table>", "lxml")
    row = soup.find("tr")
    assert isinstance(row, Tag)
    return row


def _as_table(rows: str) -> Tag:
    """Wrap row HTML in a table and return the ``<table>`` Tag.

    Args:
        rows: One or more ``<tr>`` elements as raw HTML.

    Returns:
        The parsed ``<table>`` Tag.
    """
    soup = BeautifulSoup(f'<table class="wikitable">{rows}</table>', "lxml")
    table = soup.find("table")
    assert isinstance(table, Tag)
    return table


def _data_cell(
    content: str,
    *,
    winner: bool = False,
    background: str = "#FEFDDE",
) -> str:
    """Build one party data cell.

    Args:
        content: Inner HTML of the cell.
        winner: When True, attach the inline ``background`` style that marks
            the winning cell.
        background: Background colour to use when *winner* is set.

    Returns:
        The ``<td>`` element as raw HTML.
    """
    if winner:
        return f'<td style="background:{background};">{content}</td>'
    return f"<td>{content}</td>"


def _make_row(seat: str, cells: list[str]) -> Tag:
    """Build a constituency row from a seat name and per-party data cells.

    Each entry in *cells* is preceded by a colour swatch, mirroring the real
    table. Passing fewer than seven entries reproduces a short row; a trailing
    swatch is still emitted per missing column so the row matches the real
    ``Glasgow Central`` shape.

    Args:
        seat: Constituency name for the first cell.
        cells: Party data cells as raw HTML, in column order.

    Returns:
        The assembled ``<tr>`` Tag.
    """
    parts = [f"<td>{seat}</td>"]
    for cell in cells:
        parts.append(_SWATCH)
        parts.append(cell)
    parts.extend(_SWATCH for _ in range(len(PARTY_KEYS) - len(cells)))
    return _as_tag(f"<tr>{''.join(parts)}</tr>")


def _full_cells(winner_index: int = 0) -> list[str]:
    """Seven ordinary party cells with the winner at *winner_index*.

    Args:
        winner_index: Column index carrying the winning background style.

    Returns:
        Seven ``<td>`` elements as raw HTML.
    """
    votes = [12000, 9000, 1000, 2000, 800, 4000]
    cells = [
        _data_cell(f"Cand {i}<br/>{v:,}", winner=(i == winner_index))
        for i, v in enumerate(votes)
    ]
    cells.append(_data_cell(""))
    return cells


def _region_row(name: str, votes: dict[str, int], others: int) -> str:
    """Build one region row, with the Total column summing the parties.

    Args:
        name: Region name for the first cell.
        votes: Named-party votes in ``PARTY_KEYS`` order.
        others: Value for the aggregate "Other" column.

    Returns:
        The ``<tr>`` element as raw HTML.
    """
    values = [votes[k] for k in PARTY_KEYS[:-1]] + [others]
    body = "".join(f"<td>{v:,}</td>" for v in values)
    return f"<tr><th>{name}</th>{body}<td>{sum(values):,}</td></tr>"


def _region_table(rows: int = 8, others: int = 13814) -> Tag:
    """Build a full region table with *rows* identical-shaped regions.

    Args:
        rows: Number of region rows to emit.
        others: Value for each row's aggregate "Other" column.

    Returns:
        The assembled ``<table>`` Tag.
    """
    body = "".join(
        _region_row(f"Region {i}", CSLW_VOTES, others) for i in range(rows)
    )
    return _as_table(_REGION_HEADER + body)


# ── Text extraction ───────────────────────────────────────────────────────────


class TestCellText:
    """Cell text extraction: <br/> becomes a space, footnotes disappear."""

    def test_br_becomes_a_separator(self) -> None:
        cell = _as_tag("<tr><td>Jack Middleton<br/>11,974</td></tr>").find("td")
        assert isinstance(cell, Tag)
        assert cell_text(cell) == "Jack Middleton 11,974"

    def test_footnote_after_the_number_is_stripped(self) -> None:
        # Regression guard for the real Dundee City West Reform cell: the
        # footnote sits *after* the vote figure, so it must be removed before
        # the trailing number is split off.
        cell = _as_tag(
            '<tr><td>Arthur Keith<br/>3,315<sup class="reference">[a]</sup></td></tr>'
        ).find("td")
        assert isinstance(cell, Tag)
        assert cell_text(cell) == "Arthur Keith 3,315"

    def test_footnote_after_name_is_stripped(self) -> None:
        cell = _as_tag("<tr><td>Jane Doe[a]<br/>1,234</td></tr>").find("td")
        assert isinstance(cell, Tag)
        assert cell_text(cell) == "Jane Doe 1,234"


class TestParseInt:
    """Whole-field integer parsing."""

    def test_comma_formatted(self) -> None:
        assert parse_int("86,809") == 86809

    @pytest.mark.parametrize(
        "value",
        ["86,809 32.4 %", "86,809 (32.4%)", "86,809 votes", "", "n/a", "—"],
    )
    def test_trailing_or_missing_content_raises(self, value: str) -> None:
        # Stripping non-digits instead would fuse "86,809 32.4 %" into
        # 86809324 — a plausible-looking figure with no error.
        with pytest.raises(ValueError, match="Expected an integer"):
            parse_int(value)


# ── Cell classification ───────────────────────────────────────────────────────


class TestCellClassification:
    """Swatch and winner detection are attribute-based."""

    def test_swatch_needs_both_width_and_bgcolor(self) -> None:
        both = _as_tag(f"<tr>{_SWATCH}</tr>").find("td")
        width_only = _as_tag('<tr><td width="1"></td></tr>').find("td")
        bgcolor_only = _as_tag('<tr><td bgcolor="#FFF9C4"></td></tr>').find("td")
        assert isinstance(both, Tag)
        assert isinstance(width_only, Tag)
        assert isinstance(bgcolor_only, Tag)

        assert is_swatch_cell(both) is True
        # Both conjuncts are load-bearing: a data cell could carry either alone.
        assert is_swatch_cell(width_only) is False
        assert is_swatch_cell(bgcolor_only) is False

    def test_background_style_is_colour_agnostic(self) -> None:
        for colour in ("#FEFDDE", "#FFCCD9", "#ccffc6", "#FDE6C1", "#CCEBFF"):
            html = f'<tr><td style="background:{colour};">x</td></tr>'
            cell = _as_tag(html).find("td")
            assert isinstance(cell, Tag)
            assert has_background_style(cell) is True

    def test_plain_cell_has_no_background(self) -> None:
        cell = _as_tag("<tr><td>x</td></tr>").find("td")
        assert isinstance(cell, Tag)
        assert has_background_style(cell) is False


# ── Header parsing ────────────────────────────────────────────────────────────


class TestParseHeaderPartyKeys:
    """Header row → ordered party keys."""

    def test_constituency_header(self) -> None:
        assert parse_header_party_keys(_as_tag(_CONSTITUENCY_HEADER)) == PARTY_KEYS

    def test_region_header_accepts_variant_spellings_and_skips_total(self) -> None:
        # "Lib Dems" (not "Lib Dem") and a trailing "Total" column.
        assert parse_header_party_keys(_as_tag(_REGION_HEADER)) == PARTY_KEYS

    def test_unrecognised_party_column_raises(self) -> None:
        drifted = _CONSTITUENCY_HEADER.replace(
            '<th colspan="2">Labour</th>', '<th colspan="2">Fictional Party</th>'
        )
        with pytest.raises(ValueError, match="Unrecognised party column header"):
            parse_header_party_keys(_as_tag(drifted))

    def test_extra_recognised_column_raises_on_count(self) -> None:
        # A *recognised* duplicate reaches the count guard rather than tripping
        # the unknown-header check first.
        drifted = _CONSTITUENCY_HEADER.replace(
            '<th colspan="2">Other</th>',
            '<th colspan="2">Other</th><th colspan="2">Other</th>',
        )
        with pytest.raises(ValueError, match="Expected 7 party columns"):
            parse_header_party_keys(_as_tag(drifted))

    def test_dropped_column_raises_on_count(self) -> None:
        drifted = _CONSTITUENCY_HEADER.replace('<th colspan="2">Other</th>', "")
        with pytest.raises(ValueError, match="Expected 7 party columns"):
            parse_header_party_keys(_as_tag(drifted))


# ── Candidate cell parsing ────────────────────────────────────────────────────


class TestParsePartyCell:
    """Named-party data cells: trailing number is the vote total."""

    def test_name_and_votes(self) -> None:
        result = parse_party_cell("Jack Middleton 11,974", "Aberdeen Central", "snp")
        assert result.candidate_name == "Jack Middleton"
        assert result.votes == 11974

    def test_multi_word_name_keeps_only_trailing_number(self) -> None:
        result = parse_party_cell(
            "Yi-pei Chou Turvey 2,563", "Aberdeen Central", "libdems"
        )
        assert result.candidate_name == "Yi-pei Chou Turvey"
        assert result.votes == 2563

    def test_missing_vote_number_raises(self) -> None:
        with pytest.raises(ValueError, match="No vote total found"):
            parse_party_cell("Nameless Candidate", "Someshire", "labour")


class TestParseOtherCell:
    """The Other column aggregates minor parties into one bucket."""

    def test_single_candidate(self) -> None:
        result, complete = parse_other_cell("Chris Sermanni (TUSC, 467)")
        assert result is not None
        assert result.key == "others"
        assert result.votes == 467
        assert result.candidate_name == "Chris Sermanni"
        assert complete is True

    def test_multiple_candidates_are_summed(self) -> None:
        result, complete = parse_other_cell("Ann Adams (Ind, 505) Ben Bell (ASP, 441)")
        assert result is not None
        assert result.votes == 946
        # First listed candidate represents the bucket, matching the 2021 files.
        assert result.candidate_name == "Ann Adams"
        assert complete is True

    def test_candidate_without_vote_figure_returns_none(self) -> None:
        # Regression guard: Renfrewshire North and Cardonald / Stirling list an
        # Other candidate with no number. This must not abort the scrape.
        result, complete = parse_other_cell("Jim Halfpenny (TUSC)")
        assert result is None
        assert complete is False

    def test_mixed_cell_is_flagged_incomplete(self) -> None:
        # One candidate has a figure, one does not: the total under-counts and
        # the caller must be told rather than silently losing votes.
        result, complete = parse_other_cell("Ann Adams (Ind, 505) Jim Halfpenny (TUSC)")
        assert result is not None
        assert result.votes == 505
        assert complete is False


# ── Row parsing ───────────────────────────────────────────────────────────────


class TestParseConstituencyRow:
    """Winner detection and per-party extraction."""

    def test_winner_is_background_styled_cell_not_bold(self) -> None:
        # The Glasgow Southside trap: stray bold sits on the SNP *number* while
        # the Green cell carries the winning background.
        cells = [
            _data_cell("Kaukab Stewart<br/><b>10,947</b>"),
            _data_cell("Zubir Ahmed<br/>9,294"),
            _data_cell("Tori Miller<br/>1,328"),
            _data_cell("Holly Bruce<br/>14,048", winner=True, background="#ccffc6"),
            _data_cell("Fay Lib<br/>1,090"),
            _data_cell("Ray Ref<br/>5,315"),
            _data_cell(""),
        ]
        row = parse_constituency_row(_make_row("Glasgow Southside", cells), PARTY_KEYS)
        assert row.winner_key == "green"
        assert row.others_missing is False

    def test_split_bold_still_yields_name_and_votes(self) -> None:
        # Rutherglen splits the bold across the name and the number.
        cells = [
            _data_cell("<b>Clare Haughey</b><br/><b>14,969</b>", winner=True),
            _data_cell("Lab Person<br/>11,375"),
            _data_cell("Con Person<br/>1,680"),
            _data_cell(""),
            _data_cell("Lib Person<br/>1,548"),
            _data_cell("Ref Person<br/>5,380"),
            _data_cell(""),
        ]
        row = parse_constituency_row(
            _make_row("Rutherglen and Cambuslang", cells), PARTY_KEYS
        )
        assert row.winner_key == "snp"
        snp = next(p for p in row.parties if p.key == "snp")
        assert snp.candidate_name == "Clare Haughey"
        assert snp.votes == 14969

    def test_non_snp_winner_background_is_detected(self) -> None:
        cells = [
            _data_cell("Snp Person<br/>12,000"),
            _data_cell("Daniel Johnson<br/>16,963", winner=True, background="#FFCCD9"),
            _data_cell("Con Person<br/>2,659"),
            _data_cell("Grn Person<br/>15"),
            _data_cell("Lib Person<br/>2,681"),
            _data_cell("Ref Person<br/>5,243"),
            _data_cell(""),
        ]
        row = parse_constituency_row(_make_row("Edinburgh Southern", cells), PARTY_KEYS)
        assert row.winner_key == "labour"

    def test_short_row_with_absent_trailing_other_cell(self) -> None:
        # Glasgow Central: 14 cells — the Other data cell is missing, its
        # swatch is not.
        cells = _full_cells()[:-1]
        source = _make_row("Glasgow Central", cells)
        assert len(source.find_all("td")) == 14

        row = parse_constituency_row(source, PARTY_KEYS)
        assert row.winner_key == "snp"
        assert len(row.parties) == 6
        assert "others" not in {p.key for p in row.parties}

    def test_mid_row_gap_does_not_shift_columns(self) -> None:
        # A data cell absent *mid*-row, its swatch still present. Mapping cells
        # to parties by position in a swatch-filtered list would file Lib Dem
        # votes under Green and drop Reform entirely, silently.
        parts = [
            "<td>Gapshire</td>",
            _SWATCH,
            _data_cell("Snp Person<br/>12,000", winner=True),
            _SWATCH,
            _data_cell("Lab Person<br/>9,000"),
            _SWATCH,
            _data_cell("Con Person<br/>1,000"),
            _SWATCH,  # Green swatch with no data cell
            _SWATCH,
            _data_cell("Lib Person<br/>800"),
            _SWATCH,
            _data_cell("Ref Person<br/>4,000"),
            _SWATCH,
            _data_cell(""),
        ]
        row = parse_constituency_row(_as_tag(f"<tr>{''.join(parts)}</tr>"), PARTY_KEYS)

        by_key = {p.key: p for p in row.parties}
        assert "green" not in by_key
        assert by_key["libdems"].votes == 800
        assert by_key["reform"].votes == 4000
        assert by_key["libdems"].candidate_name == "Lib Person"

    def test_wrong_swatch_count_raises(self) -> None:
        parts = ["<td>Someshire</td>", _SWATCH, _data_cell("A<br/>1", winner=True)]
        with pytest.raises(ValueError, match="party columns"):
            parse_constituency_row(_as_tag(f"<tr>{''.join(parts)}</tr>"), PARTY_KEYS)

    def test_empty_party_cell_is_omitted(self) -> None:
        cells = [
            _data_cell("Snp Person<br/>12,000", winner=True),
            _data_cell("Lab Person<br/>9,000"),
            _data_cell(""),  # Conservative did not stand
            _data_cell(""),
            _data_cell(""),
            _data_cell("Ref Person<br/>4,000"),
            _data_cell(""),
        ]
        row = parse_constituency_row(_make_row("Someshire", cells), PARTY_KEYS)
        assert {p.key for p in row.parties} == {"snp", "labour", "reform"}

    def test_footnote_after_number_in_full_row(self) -> None:
        cells = _full_cells()
        cells[5] = _data_cell(
            'Arthur Keith<br/>3,315<sup class="reference">[a]</sup>'
        )
        row = parse_constituency_row(_make_row("Dundee City West", cells), PARTY_KEYS)
        reform = next(p for p in row.parties if p.key == "reform")
        assert reform.candidate_name == "Arthur Keith"
        assert reform.votes == 3315

    def test_other_candidate_without_votes_is_dropped_not_raised(self) -> None:
        cells = _full_cells()
        cells[6] = _data_cell("Jim Halfpenny (TUSC)")
        row = parse_constituency_row(
            _make_row("Renfrewshire North and Cardonald", cells), PARTY_KEYS
        )
        assert row.others_missing is True
        assert "others" not in {p.key for p in row.parties}

    def test_partial_other_cell_is_flagged(self) -> None:
        cells = _full_cells()
        cells[6] = _data_cell("Ann Adams (Ind, 505)<br/>Jim Halfpenny (TUSC)")
        row = parse_constituency_row(_make_row("Someshire", cells), PARTY_KEYS)
        assert row.others_partial is True

    def test_multi_candidate_other_is_summed(self) -> None:
        cells = _full_cells()
        cells[6] = _data_cell("Ann Adams (Ind, 505)<br/>Ben Bell (ASP, 441)")
        row = parse_constituency_row(_make_row("Airdrie", cells), PARTY_KEYS)
        assert row.others_missing is False
        assert row.others_partial is False
        others = next(p for p in row.parties if p.key == "others")
        assert others.votes == 946

    def test_winner_must_also_lead_the_vote(self) -> None:
        # Under FPTP these cannot disagree, so a mismatch means the row was
        # misread — the exact failure this scraper exists to correct.
        cells = _full_cells(winner_index=1)
        with pytest.raises(ValueError, match="polled most"):
            parse_constituency_row(_make_row("Someshire", cells), PARTY_KEYS)

    def test_two_background_styled_cells_raises(self) -> None:
        cells = _full_cells()
        cells[1] = _data_cell("Lab Person<br/>9,000", winner=True)
        with pytest.raises(ValueError, match="background-styled"):
            parse_constituency_row(_make_row("Someshire", cells), PARTY_KEYS)

    def test_no_background_styled_cell_raises(self) -> None:
        cells = [_data_cell(c) for c in ("A<br/>1", "B<br/>2")] + [
            _data_cell("") for _ in range(5)
        ]
        with pytest.raises(ValueError, match="background-styled"):
            parse_constituency_row(_make_row("Someshire", cells), PARTY_KEYS)


# ── Region table ──────────────────────────────────────────────────────────────


class TestParseRegionTable:
    """Regional list votes, with the Total column used as a checksum."""

    def test_parses_eight_regions(self) -> None:
        regions = parse_region_table(_region_table())
        assert len(regions) == 8
        assert regions[0].votes == {**CSLW_VOTES, "others": 13814}

    def test_wrong_region_count_raises(self) -> None:
        with pytest.raises(ValueError, match="Expected 8 region rows"):
            parse_region_table(_region_table(rows=7))

    def test_short_row_raises_rather_than_shifting_columns(self) -> None:
        # Dropping a cell would slide the Total figure into the Others column.
        short = _region_row("Region 0", CSLW_VOTES, 13814).replace(
            "<td>13,814</td>", "", 1
        )
        table = _as_table(_REGION_HEADER + short * 8)
        with pytest.raises(ValueError, match="to match the header"):
            parse_region_table(table)

    def test_large_total_mismatch_raises(self) -> None:
        # A column shift misplaces a whole column, so the gap is large.
        row = _region_row("Region 0", CSLW_VOTES, 13814)
        broken = row.rsplit("<td>", 1)[0] + "<td>999,999</td></tr>"
        with pytest.raises(ValueError, match="probably misaligned"):
            parse_region_table(_as_table(_REGION_HEADER + broken * 8))

    def test_small_total_mismatch_is_recorded_not_raised(self) -> None:
        # Edinburgh and Lothians East really is out by 10 votes on Wikipedia.
        # That is upstream arithmetic, not a parse failure.
        row = _region_row("Region 0", CSLW_VOTES, 13814)
        stated = sum(CSLW_VOTES.values()) + 13814
        nudged = row.rsplit("<td>", 1)[0] + f"<td>{stated + 10:,}</td></tr>"
        regions = parse_region_table(_as_table(_REGION_HEADER + nudged * 8))
        assert regions[0].total_discrepancy == 10
        assert regions[0].votes == {**CSLW_VOTES, "others": 13814}


# ── Seat allocation ───────────────────────────────────────────────────────────


class TestDhondtAllocate:
    """d'Hondt list allocation, and why "others" must be excluded."""

    def test_reproduces_central_scotland_and_lothians_west(self) -> None:
        allocation = dhondt_allocate(CSLW_VOTES, {"snp": 13}, 7)
        assert allocation == {"labour": 2, "conservative": 1, "green": 1, "reform": 3}
        assert sum(allocation.values()) == 7

    def test_constituency_wins_suppress_list_seats(self) -> None:
        # With no constituency seats the SNP's large vote wins list seats; with
        # 13 already banked its quotient collapses and it wins none.
        assert dhondt_allocate(CSLW_VOTES, {}, 7).get("snp", 0) > 0
        assert dhondt_allocate(CSLW_VOTES, {"snp": 13}, 7).get("snp", 0) == 0

    def test_including_others_can_steal_a_seat(self) -> None:
        # "others" aggregates several unrelated minor parties. Treated as one
        # party it competes in d'Hondt and can take a seat that belongs to a
        # real party — which is why build_list_payload strips it first.
        named = {"alpha": 100, "beta": 40}
        assert dhondt_allocate(named, {}, 2) == {"alpha": 2}
        assert dhondt_allocate({**named, "others": 90}, {}, 2) == {
            "alpha": 1,
            "others": 1,
        }


class TestBuildListPayload:
    """The payload builder must strip "others" before allocating."""

    INDEX = MapIndex(
        region_by_key={"regionone": "Region One"},
        region_for_seat={},
    )

    def _payload(self, others: int) -> dict[str, object]:
        from scripts.scrape_holyrood_2026 import RegionRow

        regions = [RegionRow("Region One", {**CSLW_VOTES, "others": others})]
        built = build_list_payload(regions, {"Region One": {"snp": 13}}, self.INDEX)
        return built["Region One"]

    def test_others_excluded_from_allocation_but_kept_in_votes(self) -> None:
        block = self._payload(13814)
        assert block["seats"] == {
            "labour": 2,
            "conservative": 1,
            "green": 1,
            "reform": 3,
        }
        # The raw votes still report others — only the allocation ignores it.
        votes = block["regionVotes"]
        assert isinstance(votes, dict)
        assert votes["others"] == 13814

    def test_large_others_bucket_never_wins_a_seat(self) -> None:
        # Guards the strip: with others included this bucket would top the poll
        # and take multiple seats.
        block = self._payload(500000)
        seats = block["seats"]
        assert isinstance(seats, dict)
        assert "others" not in seats
        assert sum(seats.values()) == 7
