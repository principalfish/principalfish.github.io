"""Tests for the YouGov PDF poll importer.

Covers pure parsing helpers that require no network access or database.
"""

from __future__ import annotations

from datetime import date

import pytest

from polls.importers.westminster.yougov_import import (
    _normalize_percentage,
    normalize_name,
    parse_fieldwork,
    parse_headline_vi_table,
    parse_poll,
)

# Trimmed pypdf extractions of real YouGov PDFs. Each layout keeps the page-1
# column header, the MRP headline rows and the unlabelled region table, whose
# rows line up with the headline rows — Your Party and Restore Britain included.

# 13-14 Sep 2026: lower-case "Sample size", no country columns on page 1, and a
# seven-column region table led by England, Wales and Scotland.
SEPT_2026_TEXT = """\
YouGov Survey Results
Sample size: 2149 adults in GB
Fieldwork: 13th - 14th September 2026
Total Con Lab Lib
Dem
Reform
UK Green Remain Leave Male Female 18-24 25-49 50-64 65+ Higher Intermediate Routine
% % % % % % % % % % % % % % % % %
HEADLINE VOTING INTENTION
Westminster Voting Intention
[Headline voting intention from constituency vote
projected by YouGov MRP model]
Con 20 20 65 5 8 9 5 18 27 19 21 7 12 24 27 22 20 21
Lab 23 23 3 61 10 0 16 33 11 24 23 31 30 23 16 28 20 20
Lib Dem 11 13 4 8 66 1 8 19 5 10 16 19 13 13 11 14 16 9
SNP 3 3 0 1 0 0 3 4 2 4 3 1 4 3 2 1 4 5
Plaid Cymru 1 1 0 2 1 0 0 2 1 1 2 1 1 2 1 1 2 1
Reform UK 23 23 25 9 4 77 6 6 42 28 19 5 18 23 31 19 22 31
Green 12 11 1 13 8 1 63 15 4 10 13 33 16 8 5 11 11 7
Your Party 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
Restore Britain 4 3 1 2 1 11 0 2 5 4 2 1 4 3 3 2 3 5
Other 2 1 1 0 3 1 0 1 2 1 2 2 1 1 2 1 2 1
If there were a general election held tomorrow,
which party would you vote for?
Conservative 14 14 55 4 7 9 2 13 22 14 14 7 7 18 24 17 14 12
Refused 4 3 3 1 2 1 0 2 2 3 4 5 5 1 1 3 3 4
1 © 2026 YouGov plc. All Rights Reserved www.yougov.com
YouGov Survey Results
Sample size: 2149 adults in GB
Fieldwork: 13th - 14th September 2026
HEADLINE VOTING INTENTION
Westminster Voting Intention
Con 20 20
Other 2 1
If there were a general election held tomorrow,
which party would you vote for?
Refused 4 3
England Wales Scotland North Midlands London Rest of
South
1859 103 187 509 352 260 737
1865 121 163 500 360 246 759
% % % % % % %
22 10 9 17 25 22 24
24 18 18 32 23 32 17
14 5 7 8 8 17 20
0 0 35 0 0 0 0
0 26 0 0 0 0 0
23 27 19 24 28 12 24
12 7 8 13 12 14 11
0 0 0 0 0 0 0
3 7 3 3 4 1 3
2 0 1 2 1 2 1
15 8 7 10 16 15 18
Country Region in England
2 © 2026 YouGov plc. All Rights Reserved www.yougov.com
Now, thinking specifically about your own
constituency, if there were a general election
"""

# 23-24 Aug 2026: page 1 ends in the country columns, and the region table holds
# the four England regions only.
AUG_2026_TEXT = """\
YouGov Survey Results
Sample Size: 2380 GB Adults
Fieldwork: 23rd - 24th August 2026
Total Con Lab Lib
Dem
Reform
UK Green Remain Leave Male Female 18-24 25-49 50-64 65+ Higher Intermediate Routine England Wales Scotland
% % % % % % % % % % % % % % % % % % % %
HEADLINE VOTING INTENTION
Westminster Voting Intention
[Headline voting intention from constituency vote
projected by YouGov MRP model]
Con 19 20 63 6 7 8 6 17 25 19 20 5 13 19 30 23 19 19 21 10 7
Lab 22 22 4 57 12 2 15 37 9 22 23 25 26 21 19 28 24 14 23 18 20
Lib Dem 12 13 4 11 63 2 3 18 8 12 14 15 15 13 12 15 13 11 14 5 6
SNP 3 3 0 1 0 0 0 4 1 3 3 4 2 3 2 2 2 4 0 0 29
Plaid Cymru 1 1 1 1 0 0 1 2 1 2 1 4 2 1 1 1 2 1 0 30 0
Reform UK 24 23 25 9 5 74 3 5 44 27 19 4 15 28 29 18 22 32 22 28 21
Green 13 13 2 14 11 1 68 15 6 11 15 41 21 9 5 9 12 13 14 7 11
Your Party 0 0 0 1 0 0 0 1 0 0 0 0 0 0 0 1 0 0 0 0 0
Restore Britain 4 3 1 0 1 11 2 1 4 3 2 0 4 3 1 1 2 5 3 2 5
Other 2 2 1 0 1 2 2 1 2 2 2 1 2 2 1 2 2 1 2 0 1
If there were a general election held tomorrow,
which party would you vote for?
Conservative 12 14 55 5 7 9 1 14 20 14 14 6 10 14 25 19 14 12 15 10 9
Refused 3 4 2 3 4 1 4 3 2 4 4 8 5 3 1 2 3 5 4 3 5
1 © 2026 YouGov plc. All Rights Reserved www.yougov.com
YouGov Survey Results
Sample Size: 2380 GB Adults
Fieldwork: 23rd - 24th August 2026
HEADLINE VOTING INTENTION
Westminster Voting Intention
Con 19 20
Other 2 2
If there were a general election held tomorrow,
which party would you vote for?
Refused 3 4
North Midlands London Rest of
South
564 390 288 816
545 404 253 828
% % % %
17 24 20 24
31 25 31 15
7 11 14 21
0 0 0 0
0 0 0 0
26 23 14 22
13 14 17 13
1 0 0 0
3 2 1 3
2 1 3 1
10 17 14 16
Region in England
2 © 2026 YouGov plc. All Rights Reserved www.yougov.com
Now, thinking specifically about your own
constituency, if there were a general election
"""

# 6-7 Apr 2026: a single cross-tab whose last six columns are the regions.
OLD_FORMAT_TEXT = """\
YouGov Survey Results
Sample Size: 2320 GB adults
Fieldwork: 6th - 7th April 2026
Total Con Lab Lib
Dem
Reform
UK Green Remain Leave Male Female 18-24 25-49 50-64 65+ Higher Intermediate Routine England Wales Scotland North Midlands London Rest of
South
HEADLINE VOTING INTENTION
Westminster Voting Intention
[Headline voting intention projected by YouGov MRP model]
Con 19 61 6 9 5 5 16 24 16 21 9 14 16 33 22 20 18 20 16 11 17 22 16 22
Lab 16 2 44 5 1 4 25 6 17 16 17 19 16 11 19 15 13 17 14 14 18 17 29 12
Lib Dem 13 4 15 63 2 3 19 8 13 14 14 15 10 14 16 13 13 14 8 9 10 9 12 20
SNP 3 0 1 1 0 0 4 1 3 2 2 3 4 3 3 3 3 0 0 33 0 0 0 0
Plaid Cymru 1 1 1 2 0 1 2 1 1 2 1 2 1 0 1 2 1 0 27 0 0 0 0 0
Reform UK 24 28 9 4 75 9 8 45 29 19 7 18 34 28 17 26 34 25 17 15 29 27 16 25
Green 16 1 20 13 0 74 20 6 12 20 44 19 12 7 16 14 9 17 12 11 18 20 20 14
Your Party 1 0 0 2 0 1 1 0 1 1 0 0 2 1 1 1 0 1 0 1 1 1 3 0
Restore Britain 4 1 2 1 15 2 2 6 5 4 2 7 4 2 2 4 7 4 5 2 5 3 2 5
Other 2 1 3 1 2 2 3 2 3 1 3 2 3 1 1 3 2 2 1 4 2 2 2 2
If there were a general election held tomorrow, which party
would you vote for?
Now, thinking specifically about your own
constituency, if there were a general election
"""


# ── normalize_name ────────────────────────────────────────────────────────────


class TestNormalizeName:
    """Tests for normalize_name — region/party name normalisation."""

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert normalize_name("  Scotland  ") == "scotland"

    def test_collapses_internal_whitespace(self) -> None:
        assert normalize_name("North  West  England") == "north west england"

    def test_lowercases(self) -> None:
        assert normalize_name("SCOTLAND") == "scotland"

    def test_tabs_treated_as_whitespace(self) -> None:
        assert normalize_name("North\tWest") == "north west"

    def test_already_normalised_unchanged(self) -> None:
        assert normalize_name("london") == "london"

    def test_empty_string(self) -> None:
        assert normalize_name("") == ""


# ── parse_fieldwork ───────────────────────────────────────────────────────────


class TestParseFieldwork:
    """Tests for parse_fieldwork — YouGov PDF fieldwork date string parsing."""

    def test_same_month(self) -> None:
        start, end = parse_fieldwork("3-7 February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_cross_month(self) -> None:
        start, end = parse_fieldwork("28 January - 3 February 2025")
        assert start == date(2025, 1, 28)
        assert end == date(2025, 2, 3)

    def test_cross_year(self) -> None:
        start, end = parse_fieldwork("30 December - 2 January 2026")
        assert start == date(2025, 12, 30)
        assert end == date(2026, 1, 2)

    def test_en_dash_normalised(self) -> None:
        start, end = parse_fieldwork("3–7 February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_fieldwork_prefix_present(self) -> None:
        # As extracted from PDF: "Fieldwork: 3-7 February 2025"
        start, end = parse_fieldwork("Fieldwork: 3-7 February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_ordinal_suffix_present(self) -> None:
        start, end = parse_fieldwork("3rd-7th February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            parse_fieldwork("not a date at all")


# ── _normalize_percentage ─────────────────────────────────────────────────────


class TestNormalizePercentage:
    """Tests for _normalize_percentage — round to nearest integer as float."""

    def test_rounds_up(self) -> None:
        assert _normalize_percentage(34.6) == pytest.approx(35.0)

    def test_rounds_down(self) -> None:
        assert _normalize_percentage(34.4) == pytest.approx(34.0)

    def test_exact_integer(self) -> None:
        assert _normalize_percentage(42.0) == pytest.approx(42.0)

    def test_returns_float(self) -> None:
        result = _normalize_percentage(30.0)
        assert isinstance(result, float)

    def test_rounds_half_to_even(self) -> None:
        # Python's built-in round() uses banker's rounding: 34.5 → 34 (nearest even)
        assert _normalize_percentage(34.5) == pytest.approx(34.0)


# ── parse_poll ────────────────────────────────────────────────────────────────


class TestParsePoll:
    """Tests for parse_poll — sample size and fieldwork from the PDF header."""

    def test_lower_case_sample_size_label(self) -> None:
        parsed = parse_poll(SEPT_2026_TEXT)
        assert parsed.sample_size == 2149
        assert parsed.fieldwork_start == date(2026, 9, 13)
        assert parsed.fieldwork_end == date(2026, 9, 14)

    def test_title_case_sample_size_label(self) -> None:
        parsed = parse_poll(AUG_2026_TEXT)
        assert parsed.sample_size == 2380
        assert parsed.fieldwork_end == date(2026, 8, 24)

    def test_missing_sample_size_raises(self) -> None:
        text = SEPT_2026_TEXT.replace("Sample size: 2149", "Respondents: 2149")
        with pytest.raises(ValueError, match="Sample size not found"):
            parse_poll(text)


# ── parse_headline_vi_table ───────────────────────────────────────────────────


class TestParseHeadlineViTableSeptember2026:
    """Seven-column region table: every region comes from the table."""

    def test_every_region_read_from_table(self) -> None:
        # Page 1 now ends in Intermediate/Routine; reading its last two values
        # as Wales/Scotland would give Con 20/21.
        result = parse_headline_vi_table(SEPT_2026_TEXT)
        assert result["Conservative"] == {
            "Wales": 10.0,
            "Scotland": 9.0,
            "North": 17.0,
            "Midlands": 25.0,
            "London": 22.0,
            "Rest of South": 24.0,
        }

    def test_country_parties(self) -> None:
        result = parse_headline_vi_table(SEPT_2026_TEXT)
        assert result["Scottish National Party"]["Scotland"] == 35.0
        assert result["Plaid Cymru"]["Wales"] == 26.0

    def test_other_is_the_other_row_not_your_party(self) -> None:
        result = parse_headline_vi_table(SEPT_2026_TEXT)
        assert result["Other"] == {
            "Wales": 0.0,
            "Scotland": 1.0,
            "North": 2.0,
            "Midlands": 1.0,
            "London": 2.0,
            "Rest of South": 1.0,
        }

    def test_unmapped_parties_are_dropped(self) -> None:
        result = parse_headline_vi_table(SEPT_2026_TEXT)
        assert "Your Party" not in result
        assert "Restore Britain" not in result
        assert len(result) == 8

    def test_percent_row_disagreeing_with_header_raises(self) -> None:
        text = SEPT_2026_TEXT.replace("% % % % % % %\n", "% % % %\n")
        with pytest.raises(ValueError, match="Regions table has 4 columns"):
            parse_headline_vi_table(text)

    def test_table_cut_short_raises(self) -> None:
        text = SEPT_2026_TEXT.replace(
            "2 0 1 2 1 2 1\n15 8 7 10 16 15 18\nCountry Region in England",
            "Country Region in England",
        )
        with pytest.raises(ValueError, match="Unexpected line in regions table"):
            parse_headline_vi_table(text)


class TestParseHeadlineViTableMarchToAugust2026:
    """Four-column region table: Wales and Scotland come from page 1."""

    def test_england_regions_read_from_table(self) -> None:
        result = parse_headline_vi_table(AUG_2026_TEXT)
        assert result["Conservative"] == {
            "Wales": 10.0,
            "Scotland": 7.0,
            "North": 17.0,
            "Midlands": 24.0,
            "London": 20.0,
            "Rest of South": 24.0,
        }

    def test_country_parties_from_page_one(self) -> None:
        result = parse_headline_vi_table(AUG_2026_TEXT)
        assert result["Scottish National Party"]["Scotland"] == 29.0
        assert result["Plaid Cymru"]["Wales"] == 30.0

    def test_other_skips_your_party_and_restore_britain_rows(self) -> None:
        # Regression: table rows used to be paired with mapped parties only, so
        # the Your Party row (1 0 0 0) was stored as Other.
        result = parse_headline_vi_table(AUG_2026_TEXT)
        assert result["Other"] == {
            "Wales": 0.0,
            "Scotland": 1.0,
            "North": 2.0,
            "Midlands": 1.0,
            "London": 3.0,
            "Rest of South": 1.0,
        }

    def test_page_one_without_country_columns_raises(self) -> None:
        text = AUG_2026_TEXT.replace(" England Wales Scotland\n", "\n")
        with pytest.raises(ValueError, match="Wales and Scotland columns found in neither"):
            parse_headline_vi_table(text)

    def test_missing_second_question_raises(self) -> None:
        text = AUG_2026_TEXT.replace("If there were a general election", "Were there an election")
        with pytest.raises(ValueError, match="end of the MRP headline rows"):
            parse_headline_vi_table(text)


class TestParseHeadlineViTableOldFormat:
    """Single cross-tab: the last six values of each row are the regions."""

    def test_regions_are_the_last_six_values(self) -> None:
        result = parse_headline_vi_table(OLD_FORMAT_TEXT)
        assert result["Conservative"] == {
            "Wales": 16.0,
            "Scotland": 11.0,
            "North": 17.0,
            "Midlands": 22.0,
            "London": 16.0,
            "Rest of South": 22.0,
        }

    def test_other_row_matched_by_label(self) -> None:
        result = parse_headline_vi_table(OLD_FORMAT_TEXT)
        assert result["Other"] == {
            "Wales": 1.0,
            "Scotland": 4.0,
            "North": 2.0,
            "Midlands": 2.0,
            "London": 2.0,
            "Rest of South": 2.0,
        }
