"""Tests for polls/importers/sanity.py."""

from __future__ import annotations

import pytest

from polls.importers.sanity import regional_alignment_problem, regional_basis_problem


class TestRegionalAlignmentProblem:
    def test_home_nation_shares_are_fine(self) -> None:
        shares = {
            "Scotland": {"Scottish National Party": 39.0, "Plaid Cymru": 0.0},
            "Wales": {"Plaid Cymru": 27.0, "Scottish National Party": 1.0},
            "London": {"Scottish National Party": 3.0, "Plaid Cymru": 0.0},
        }
        assert regional_alignment_problem(shares) is None

    @pytest.mark.parametrize(
        ("region", "party", "share"),
        [
            ("South East England", "Scottish National Party", 39.0),
            ("London", "Plaid Cymru", 27.0),
            ("Wales", "Scottish National Party", 3.5),
            ("Scotland", "Plaid Cymru", 4.0),
        ],
    )
    def test_misplaced_share_is_reported(
        self, region: str, party: str, share: float
    ) -> None:
        shares: dict[str, dict[str, float]] = {
            "Scotland": {"Scottish National Party": 30.0}
        }
        shares.setdefault(region, {})[party] = share

        assert regional_alignment_problem(shares) == (
            f"{party} {share:g}% in {region}: regional columns look misaligned"
        )

    def test_worst_misplacement_is_named(self) -> None:
        shares = {
            "London": {"Plaid Cymru": 27.0},
            "South East England": {"Scottish National Party": 39.0},
        }
        problem = regional_alignment_problem(shares)
        assert problem is not None
        assert problem.startswith("Scottish National Party 39% in South East England")

    @pytest.mark.parametrize(
        ("party", "home", "share"),
        [("Scottish National Party", "Scotland", 2.0), ("Plaid Cymru", "Wales", 1.0)],
    )
    def test_near_zero_share_at_home_is_reported(
        self, party: str, home: str, share: float
    ) -> None:
        shares = {home: {party: share}, "London": {party: 3.0}}

        assert regional_alignment_problem(shares) == (
            f"{party} only {share:g}% in {home}: regional columns look misaligned"
        )

    def test_home_floor_boundaries_are_accepted(self) -> None:
        shares = {
            "Scotland": {"Scottish National Party": 10.0},
            "Wales": {"Plaid Cymru": 5.0},
        }
        assert regional_alignment_problem(shares) is None

    def test_absent_home_region_or_party_is_not_judged(self) -> None:
        assert regional_alignment_problem({"Scotland": {"Labour": 30.0}}) is None
        assert regional_alignment_problem({"London": {"Labour": 30.0}}) is None


class TestRegionalBasisProblem:
    _NATIONAL = {"Conservative": 13.0, "Labour": 14.0, "Reform UK": 17.0}

    def test_regions_on_the_national_basis_are_fine(self) -> None:
        # Mean gap -1.0: regional don't-know rates vary around the national.
        regional = {
            "London": {"Conservative": 10.0, "Labour": 19.0, "Reform UK": 12.0},
            "Wales": {"Conservative": 12.0, "Labour": 11.0, "Reform UK": 18.0},
        }
        assert regional_basis_problem(regional, self._NATIONAL) is None

    def test_counts_over_an_all_adult_base_are_reported(self) -> None:
        regional = {
            "London": {"Conservative": 2.0, "Labour": 3.0, "Reform UK": 2.0},
            "Wales": {"Conservative": 3.0, "Labour": 8.0, "Reform UK": 7.0},
        }
        assert regional_basis_problem(regional, self._NATIONAL) == (
            "regional shares average -31.5 points against the national total:"
            " regional basis looks wrong"
        )

    @pytest.mark.parametrize(("gap", "expected"), [(5.0, None), (5.5, "+5.5")])
    def test_threshold_boundary(self, gap: float, expected: str | None) -> None:
        regional = {"London": {"Conservative": 13.0 + gap}}
        problem = regional_basis_problem(regional, {"Conservative": 13.0})
        if expected is None:
            assert problem is None
        else:
            assert problem is not None and problem.startswith(
                f"regional shares average {expected}"
            )

    def test_only_shared_parties_are_compared(self) -> None:
        regional = {"London": {"Conservative": 14.0, "Green": 40.0}}
        assert regional_basis_problem(regional, {"Conservative": 13.0}) is None

    def test_nothing_to_compare_is_not_a_problem(self) -> None:
        assert regional_basis_problem({}, self._NATIONAL) is None
        assert regional_basis_problem({"London": {"Green": 9.0}}, {}) is None
