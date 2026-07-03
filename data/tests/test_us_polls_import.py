"""Unit tests for the US national poll importers' shared Wikipedia parser.

All tests use synthetic HTML — no network access or database required.
"""

from __future__ import annotations

from datetime import date

from polls.importers.us.us_polls_common import (
    ParsedUsPoll,
    merge_polls_by_fieldwork,
    parse_date_range,
    parse_polls,
    pollster_identifier,
)


def _page(*rows: str) -> str:
    """Wrap table rows in a minimal HTML document with a single wikitable."""
    return (
        "<html><body><table class=\"wikitable\">"
        "<tr><th>Dates</th><th>Pollster</th><th>Sample</th>"
        "<th>Democratic</th><th>Republican</th><th>Lead</th></tr>"
        + "".join(rows)
        + "</table></body></html>"
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


class TestParsePolls:
    def test_parses_two_party_rows(self) -> None:
        html = _page(
            "<tr><td>May 28 – June 3, 2026</td><td>YouGov</td><td>1,500</td>"
            "<td>47%</td><td>45%</td><td>D+2</td></tr>",
            "<tr><td>June 1–3, 2026</td><td>Marquette</td><td>900</td>"
            "<td>44%</td><td>48%</td><td>R+4</td></tr>",
        )
        polls = parse_polls(html)
        assert len(polls) == 2
        first = polls[0]
        assert first.pollster_name == "YouGov"
        assert first.sample_size == 1500
        assert first.party_percentages == {"Democratic": 47.0, "Republican": 45.0}
        assert first.fieldwork_start == date(2026, 5, 28)
        assert first.fieldwork_end == date(2026, 6, 3)

    def test_skips_rows_without_a_date(self) -> None:
        html = _page(
            "<tr><td>2024 election result</td><td>—</td><td></td><td>—</td><td>—</td><td></td></tr>",
            "<tr><td>June 3, 2026</td><td>Ipsos</td><td>1,000</td><td>46%</td><td>46%</td><td>Tie</td></tr>",
        )
        polls = parse_polls(html)
        assert len(polls) == 1
        assert polls[0].pollster_name == "Ipsos"

    def test_strips_footnotes_from_pollster_and_percentages(self) -> None:
        html = _page(
            "<tr><td>June 3, 2026</td><td>Emerson[1]</td><td>1,200</td>"
            "<td>48%[a]</td><td>47%</td><td>D+1</td></tr>",
        )
        polls = parse_polls(html)
        assert polls[0].pollster_name == "Emerson"
        assert polls[0].party_percentages == {"Democratic": 48.0, "Republican": 47.0}

    def test_returns_empty_when_no_dem_rep_table(self) -> None:
        html = (
            "<html><body><table class=\"wikitable\">"
            "<tr><th>State</th><th>Winner</th></tr>"
            "<tr><td>Ohio</td><td>Republican</td></tr>"
            "</table></body></html>"
        )
        assert parse_polls(html) == []


class TestParseAggregationTable:
    """The House/Senate pages' poll-aggregation shape: aggregator rows, plural
    party headers, pollster in column 0, snapshot date in 'Dates updated'."""

    HTML = (
        "<html><body><table class=\"wikitable sortable\">"
        "<tr><th>Source of pollaggregation</th><th>Datesadministered</th>"
        "<th>Datesupdated</th><th>Republicans</th><th>Democrats</th>"
        "<th>Other/Undecided[e]</th><th>Margin</th></tr>"
        "<tr><td>Decision Desk HQ[69]</td><td>January 9, 2025 – June 29, 2026</td>"
        "<td>June 29, 2026</td><td>40.1%</td><td>44.3%</td><td>15.6%</td>"
        "<td>Democrats +4.2%</td></tr>"
        "<tr><td>FiftyPlusOne[70]</td><td>January 9, 2025 – June 28, 2026</td>"
        "<td>June 28, 2026</td><td>43.6%</td><td>49.1%</td><td>7.3%</td>"
        "<td>Democrats +5.5%</td></tr>"
        "</table></body></html>"
    )

    def test_parses_aggregator_rows_with_updated_date(self) -> None:
        polls = parse_polls(self.HTML)
        assert len(polls) == 2
        first = polls[0]
        assert first.pollster_name == "Decision Desk HQ"
        # 'Dates updated' (the snapshot date) wins over the cycle-long 'administered' range.
        assert first.fieldwork_start == date(2026, 6, 29)
        assert first.fieldwork_end == date(2026, 6, 29)
        assert first.party_percentages == {"Republican": 40.1, "Democratic": 44.3}

    def test_margin_and_undecided_columns_ignored(self) -> None:
        polls = parse_polls(self.HTML)
        for poll in polls:
            assert set(poll.party_percentages) == {"Democratic", "Republican"}


class TestParseMatchupTables:
    """The presidential page's shape: one wikitable per head-to-head, candidate
    headers suffixed (R)/(D), pollster in 'Poll source' column 0."""

    @staticmethod
    def _matchup(rep: str, dem: str, rows: str) -> str:
        return (
            "<table class=\"wikitable\">"
            f"<tr><th>Poll source</th><th>Date(s)administered</th>"
            f"<th>Samplesize[k]</th><th>{rep}(R)</th><th>{dem}(D)</th>"
            f"<th>Undecided</th></tr>"
            + rows
            + "</table>"
        )

    def test_parses_candidate_suffix_headers(self) -> None:
        html = "<html><body>" + self._matchup(
            "JD Vance",
            "Kamala Harris",
            "<tr><td>Emerson[1]</td><td>June 24–26, 2026</td><td>1,000</td>"
            "<td>44%</td><td>46%</td><td>10%</td></tr>",
        ) + "</body></html>"
        polls = parse_polls(html)
        assert len(polls) == 1
        assert polls[0].pollster_name == "Emerson"
        assert polls[0].sample_size == 1000
        assert polls[0].party_percentages == {"Republican": 44.0, "Democratic": 46.0}

    def test_concatenates_all_matchup_tables(self) -> None:
        html = "<html><body>" + self._matchup(
            "JD Vance", "Kamala Harris",
            "<tr><td>Emerson</td><td>June 24–26, 2026</td><td>1,000</td>"
            "<td>44%</td><td>46%</td><td>10%</td></tr>",
        ) + self._matchup(
            "JD Vance", "Gavin Newsom",
            "<tr><td>Emerson</td><td>June 24–26, 2026</td><td>1,000</td>"
            "<td>45%</td><td>44%</td><td>11%</td></tr>",
        ) + "</body></html>"
        polls = parse_polls(html)
        assert len(polls) == 2

    def test_primary_matchup_table_is_skipped(self) -> None:
        # Two (D) candidates — no two-party reading, so the table must not parse.
        html = (
            "<html><body><table class=\"wikitable\">"
            "<tr><th>Poll source</th><th>Date(s)administered</th><th>Samplesize</th>"
            "<th>Kamala Harris(D)</th><th>Gavin Newsom(D)</th><th>Undecided</th></tr>"
            "<tr><td>Emerson</td><td>June 24–26, 2026</td><td>1,000</td>"
            "<td>30%</td><td>28%</td><td>42%</td></tr>"
            "</table></body></html>"
        )
        assert parse_polls(html) == []


class TestMergePollsByFieldwork:
    def test_averages_same_pollster_and_dates(self) -> None:
        polls = [
            ParsedUsPoll(date(2026, 6, 24), date(2026, 6, 26), "Emerson", 1000,
                         {"Republican": 44.0, "Democratic": 46.0}),
            ParsedUsPoll(date(2026, 6, 24), date(2026, 6, 26), "Emerson", 1000,
                         {"Republican": 46.0, "Democratic": 44.0}),
        ]
        merged = merge_polls_by_fieldwork(polls)
        assert len(merged) == 1
        assert merged[0].party_percentages == {"Republican": 45.0, "Democratic": 45.0}

    def test_distinct_polls_pass_through(self) -> None:
        polls = [
            ParsedUsPoll(date(2026, 6, 24), date(2026, 6, 26), "Emerson", 1000, {"Democratic": 46.0}),
            ParsedUsPoll(date(2026, 6, 20), date(2026, 6, 22), "YouGov", 1500, {"Democratic": 47.0}),
        ]
        merged = merge_polls_by_fieldwork(polls)
        assert len(merged) == 2
        assert [p.pollster_name for p in merged] == ["Emerson", "YouGov"]


class TestPollsterIdentifier:
    def test_slug_with_suffix(self) -> None:
        assert pollster_identifier("YouGov", "_us_house") == "yougov_us_house"

    def test_non_alphanumeric_collapses_to_underscores(self) -> None:
        assert pollster_identifier("Data for Progress", "_us_senate") == "data_for_progress_us_senate"


def test_parsed_us_poll_defaults() -> None:
    poll = ParsedUsPoll(
        fieldwork_start=date(2026, 6, 1),
        fieldwork_end=date(2026, 6, 3),
        pollster_name="Test",
        sample_size=None,
    )
    assert poll.party_percentages == {}
