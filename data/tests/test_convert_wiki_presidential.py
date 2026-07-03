"""Tests for the Wikipedia presidential results converter (data/old_data/scripts/usa).

The parser is exercised against synthetic HTML fixtures (no network) that reproduce the two
header layouts seen across 1964–1996: a two-row header naming candidates in the top row, and
a three-row header nesting candidates under "Candidates with / without electoral votes".
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

USA_DIR = Path(__file__).resolve().parents[1] / "old_data" / "scripts" / "usa"


def _load(name: str) -> ModuleType:
    """Load a usa/ script module by file path (they are run as scripts, not a package)."""
    spec = importlib.util.spec_from_file_location(name, USA_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cwp = _load("convert_wiki_presidential")

# The 50 states in the order the synthetic table lists them; Maine carries a dagger and DC is
# spelled "D. C." so the fixture stresses the footnote / alias normalisation paths.
_FIXTURE_STATES = [name if name != "Maine" else "Maine †" for name in sorted(cwp.STATE_NAMES)]


def _two_row_1968_html() -> str:
    """Build a full 51-row 1968-style table: 2 header rows, Nixon/Humphrey/Wallace + Other.

    Nixon wins every state except Mississippi, which Wallace carries — covering a third-party
    (independent) state winner. An unconfigured "Other" column folds to ``others``, and each
    state's total exceeds its columns by 10, exercising the residual-into-``others`` fold. DC
    is rendered "D. C." and Maine "Maine †" to stress the alias / footnote paths.
    """
    header = (
        "<tr><th colspan='2'></th>"
        "<th colspan='3'>Richard Nixon Republican</th>"
        "<th colspan='3'>Hubert Humphrey Democratic</th>"
        "<th colspan='3'>George Wallace American Independent</th>"
        "<th colspan='3'>Other</th>"
        "<th colspan='2'>State Total</th></tr>"
        "<tr><th>State</th><th>electoral votes</th>"
        "<th>#</th><th>%</th><th>electoral votes</th>"
        "<th>#</th><th>%</th><th>electoral votes</th>"
        "<th>#</th><th>%</th><th>electoral votes</th>"
        "<th>#</th><th>%</th><th>electoral votes</th>"
        "<th>#</th><th>%</th></tr>"
    )
    rows = []
    for state in _FIXTURE_STATES:
        display = "D. C." if state == "District of Columbia" else state
        if state == "Mississippi":
            nixon, humphrey, wallace = 100, 100, 700
        else:
            nixon, humphrey, wallace = 500, 400, 50
        other = 20
        total = nixon + humphrey + wallace + other + 10  # +10 residual to fold into "others"
        rows.append(
            f"<tr><td>{display}</td><td>0</td>"
            f"<td>{nixon:,}</td><td>0</td><td>0</td>"
            f"<td>{humphrey:,}</td><td>0</td><td>0</td>"
            f"<td>{wallace:,}</td><td>0</td><td>0</td>"
            f"<td>{other:,}</td><td>0</td><td>0</td>"
            f"<td>{total:,}</td><td>100</td></tr>"
        )
    footer = "<tr><td>TOTALS:</td><td>538</td><td colspan='14'></td></tr>"
    return f"<table class='wikitable'>{header}{''.join(rows)}{footer}</table>"


class TestConvertTwoRowHeader:
    """Full-pipeline conversion of a two-row-header table (1968 layout)."""

    def setup_method(self) -> None:
        self.result = cwp.convert(1968, html=_two_row_1968_html())

    def test_has_all_56_units_summing_to_538(self) -> None:
        assert len(self.result) == 56
        assert sum(u["seatInfo"]["electoral_votes"] for u in self.result.values()) == 538

    def test_dc_alias_and_maine_footnote_resolved(self) -> None:
        assert "District of Columbia" in self.result
        assert "Maine" in self.result  # "Maine †" normalised

    def test_me_ne_cd_units_backfilled(self) -> None:
        for unit in ("Maine CD-1", "Maine CD-2", "Nebraska CD-1", "Nebraska CD-2", "Nebraska CD-3"):
            assert unit in self.result

    def test_wallace_wins_mississippi_as_independent(self) -> None:
        ms = self.result["Mississippi"]
        assert ms["seatInfo"]["current"] == "independent"
        assert ms["partyInfo"]["independent"]["name"] == "George Wallace"
        assert ms["partyInfo"]["independent"]["total"] == 700

    def test_nixon_wins_a_typical_state_as_republican(self) -> None:
        ca = self.result["California"]
        assert ca["seatInfo"]["current"] == "republican"
        assert ca["partyInfo"]["republican"]["total"] == 500
        assert ca["partyInfo"]["democrat"]["total"] == 400

    def test_unconfigured_column_and_residual_fold_into_others(self) -> None:
        # California's "Other" column (20) plus the 10-vote residual over the state total.
        others = self.result["California"]["partyInfo"]["others"]
        assert others["total"] == 30
        assert others["name"] == "Other"

    def test_every_party_key_is_in_the_allowed_set(self) -> None:
        allowed = {"democrat", "republican", "libertarian", "usgreen", "independent", "others"}
        keys = {key for unit in self.result.values() for key in unit["partyInfo"]}
        assert keys <= allowed

    def test_electoral_votes_stamped_from_era_table(self) -> None:
        # 1968 uses the 1960 census era: California 40, Maine at-large 2, its CDs 1 each.
        assert self.result["California"]["seatInfo"]["electoral_votes"] == 40
        assert self.result["Maine"]["seatInfo"]["electoral_votes"] == 2
        assert self.result["Maine CD-1"]["seatInfo"]["electoral_votes"] == 1


class TestConvertGuards:
    """convert / _find_results_table fail loudly on malformed input."""

    def test_duplicate_state_row_raises(self) -> None:
        html = _two_row_1968_html().replace(
            "</table>", "<tr><td>California</td><td>0</td></tr></table>"
        )
        with pytest.raises(ValueError, match="Duplicate row for 'California'"):
            cwp.convert(1968, html=html)

    def test_undersized_table_raises(self) -> None:
        html = (
            "<table class='wikitable'><tr><th>State</th><th>#</th></tr>"
            "<tr><td>California</td><td>1</td></tr>"
            "<tr><td>Texas</td><td>1</td></tr></table>"
        )
        with pytest.raises(ValueError, match="No results-by-state table"):
            cwp.convert(1968, html=html)

    def test_unknown_year_raises(self) -> None:
        with pytest.raises(ValueError, match="No candidate config"):
            cwp.convert(1960, html="<table></table>")


class TestThreeRowHeaderColumns:
    """The nested three-row header (1992 layout) still finds candidate vote columns."""

    def test_candidate_columns_found_under_group_header(self) -> None:
        header_html = (
            "<table class='wikitable'>"
            "<tr><th colspan='2'></th><th colspan='6'>Candidates with electoral votes</th></tr>"
            "<tr><th colspan='2'></th>"
            "<th colspan='3'>Bill Clinton Democratic</th>"
            "<th colspan='3'>George H.W. Bush Republican</th></tr>"
            "<tr><th>State</th><th>E</th>"
            "<th>Vote</th><th>%</th><th>E</th>"
            "<th>Vote</th><th>%</th><th>E</th></tr>"
            "</table>"
        )
        table = cwp.BeautifulSoup(header_html, "html.parser").find("table")
        grid = cwp._expand_header_grid(table.find_all("tr"), ncols=8)
        key_by_col, _total = cwp._vote_columns(grid, 1992)
        # Vote columns are the first of each candidate's three-column group (indices 2 and 5).
        assert key_by_col == {2: "democrat", 5: "republican"}


class TestStateName:
    """_state_name: alias, footnote and non-state handling."""

    def test_plain_state(self) -> None:
        assert cwp._state_name("California") == "California"

    def test_dc_spellings(self) -> None:
        for spelling in ("D.C.", "D. C.", "DC", "Washington, D.C.", "District of Columbia"):
            assert cwp._state_name(spelling) == "District of Columbia"

    def test_footnote_stripped(self) -> None:
        assert cwp._state_name("Maine †") == "Maine"
        assert cwp._state_name("Nebraska[a]") == "Nebraska"

    def test_non_state_rejected(self) -> None:
        assert cwp._state_name("TOTALS:") is None
        assert cwp._state_name("ME-1 Maine's 1st congressional district") is None


class TestParseVotes:
    """_parse_votes: comma stripping and empty/dash handling."""

    def test_thousands_separators(self) -> None:
        assert cwp._parse_votes("415,349") == 415349

    def test_blank_and_dash_are_zero(self) -> None:
        assert cwp._parse_votes("—") == 0
        assert cwp._parse_votes("–") == 0
        assert cwp._parse_votes("") == 0


class TestDisplayName:
    """_display_name: strip party affiliation words from a candidate header."""

    def test_strips_party_suffix(self) -> None:
        assert cwp._display_name("Bill Clinton Democratic") == "Bill Clinton"
        assert cwp._display_name("George Wallace American Independent") == "George Wallace"
