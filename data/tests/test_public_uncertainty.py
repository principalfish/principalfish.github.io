"""The public whitelist over stored Monte Carlo summaries."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from model_support.public_uncertainty import (
    PUBLIC_SCHEMA,
    PublicUncertaintyError,
    SeatBand,
    basis_label,
    largest_remainder_seats,
    public_document,
    seat_bands,
    trend_uncertainty,
)

# Private values that must never reach a public file. Each is distinctive, so
# finding its text in the output means it leaked.
PRIVATE_VALUES = ("budget", "config_hash", "input_hash", "seed", "73990173")


# Party 2's histogram: 218..378 seats, the ends non-empty, 200 simulations;
# its mean is 298, and party 10's is 1, so the parliament has 299 seats.
HIST_2 = [100] + [0] * 159 + [100]
NAMES = {2: "Labour", 10: "Green"}


def national() -> dict[str, Any]:
    """A stored national block: published fields plus private diagnostics."""
    return {
        "budget": {"config": {"sigma_nat": 2.94}, "parties": {"2": {"x": 1}}},
        "config_hash": "730a3349d3836bc6",
        "input_hash": "2aa936b04b",
        "seed": 73990173,
        "simulations": 200,
        "interval": 0.9,
        "hung": 0.694,
        "parties": {
            "10": {
                "v": [0.37, 0.44],
                "s": [0, 1, 2],
                "mean": 1.0,
                "pm": 0.0,
                "pl": 0.0303,
                "hist": {"start": 0, "counts": [50, 100, 50]},
            },
            "2": {
                "v": [22.84, 30.56],
                "s": [220, 301, 376],
                "mean": 298.0,
                "pm": 0.30604,
                "pl": 0.9697,
                "hist": {"start": 218, "counts": HIST_2},
                "budget": {"leak": True},
            },
        },
    }


def regions() -> dict[str, Any]:
    """Region 14's means are 147.5 and 1.5: plain rounding gives 150 of its 149."""
    return {
        "14": {
            "budget": {"2": {"sd": 1.2}},
            "parties": {
                "2": {"v": [17.34, 26.39], "s": [140, 147, 155], "mean": 147.5},
                "10": {"v": [0.37, 0.44], "s": [0, 2, 3], "mean": 1.5},
            },
        },
        "13": {
            "budget": {},
            "hung": 0.9877,
            "parties": {
                "2": {
                    "v": [17.34, 26.39],
                    "s": [140, 150, 160],
                    "mean": 150.0,
                    "pm": 0.0123,
                    "pl": 1.0,
                    "budget": {},
                }
            },
        },
    }


def document() -> dict[str, object]:
    return public_document(
        national(),
        regions(),
        election_name="UNS 2026-10-12",
        as_of_date="2026-10-12",
        party_names=NAMES,
    )


def publish(
    stored: dict[str, Any], stored_regions: dict[str, Any]
) -> dict[str, object]:
    return public_document(
        stored, stored_regions, election_name="x", as_of_date=None, party_names=NAMES
    )


class TestPublicDocument:
    def test_exact_public_shape(self) -> None:
        assert document() == {
            "schema": PUBLIC_SCHEMA,
            "as_of_date": "2026-10-12",
            "election_name": "UNS 2026-10-12",
            "simulations": 200,
            "interval": 0.9,
            "basis": (
                "Nowcast: if an election were held today. 90% ranges and "
                "probabilities from 200 simulations, conditional on current polls "
                "and the model's error assumptions."
            ),
            "national": {
                "parties": {
                    "2": {
                        "v": [22.8, 30.6],
                        "s": [220, 301, 376],
                        "mean": 298.0,
                        "m": 298,
                        # Stored to 4 dp, published to 3.
                        "pm": 0.306,
                        "pl": 0.97,
                        "hist": {"start": 218, "counts": HIST_2},
                    },
                    "10": {
                        "v": [0.4, 0.4],
                        "s": [0, 1, 2],
                        "mean": 1.0,
                        "m": 1,
                        "pm": 0.0,
                        "pl": 0.03,
                        "hist": {"start": 0, "counts": [50, 100, 50]},
                    },
                },
                "hung": 0.694,
            },
            "regions": {
                # Region odds only where stored.
                "13": {
                    "parties": {
                        "2": {
                            "v": [17.3, 26.4],
                            "s": [140, 150, 160],
                            "mean": 150.0,
                            "m": 150,
                            "pm": 0.012,
                            "pl": 1.0,
                        }
                    },
                    "hung": 0.988,
                },
                # Equal remainders: the larger mean takes the last seat.
                "14": {
                    "parties": {
                        "2": {
                            "v": [17.3, 26.4],
                            "s": [140, 147, 155],
                            "mean": 147.5,
                            "m": 148,
                        },
                        "10": {"v": [0.4, 0.4], "s": [0, 2, 3], "mean": 1.5, "m": 1},
                    }
                },
            },
        }

    def test_ids_are_in_numeric_order(self) -> None:
        result: Any = document()
        assert list(result["national"]["parties"]) == ["2", "10"]
        assert list(result["regions"]) == ["13", "14"]

    def test_no_private_field_reaches_the_text(self) -> None:
        text = json.dumps(document())
        for private in PRIVATE_VALUES:
            assert private not in text

    def test_input_is_not_modified(self) -> None:
        stored, stored_regions = national(), regions()
        before = copy.deepcopy((stored, stored_regions))
        publish(stored, stored_regions)
        assert (stored, stored_regions) == before

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda n: n.pop("simulations"), "national.simulations"),
            (lambda n: n.update(interval=1.0), "out of range"),
            (lambda n: n["parties"]["2"]["hist"].update(counts=[1, 2]), "!= 200"),
            # The seat range must sit inside a histogram whose ends are filled.
            (lambda n: n["parties"]["2"]["hist"].update(start=221), "does not span"),
            (
                lambda n: n["parties"]["2"]["hist"].update(
                    counts=[0, *HIST_2[1:], 100]
                ),
                "does not span",
            ),
            (
                lambda n: n["parties"]["2"]["hist"].update(counts=[*HIST_2, 0]),
                "does not span",
            ),
            # Exclusive outcomes: rounding allows half a unit per term, no more.
            (lambda n: n.update(hung=0.696), r"P\(majority\) \+ P\(hung\) sums"),
            (lambda n: n["parties"]["10"].update(pl=0.032), r"P\(largest\) sums"),
            (lambda n: n["parties"]["2"].update(s=[301, 220, 376]), "out of order"),
            (lambda n: n["parties"]["2"].update(s=[220.0, 301, 376]), "integer"),
            (lambda n: n["parties"]["2"].update(v=[22.8]), r"\[lo, hi\]"),
            (lambda n: n["parties"]["2"].update(pm=-0.1), "outside 0-1"),
            (lambda n: n["parties"]["2"].update(pm=float("nan")), "not finite"),
            (lambda n: n.update(parties={"Labour": {}}), "integers"),
            (lambda n: n.pop("hung"), "national.hung"),
            # The mean is the histogram's, to its 2 dp.
            (lambda n: n["parties"]["2"].update(mean=298.01), "histogram's mean"),
            (lambda n: n["parties"]["2"].update(mean=-1.0), "negative"),
            (lambda n: n["parties"]["2"].update(mean="298"), "expected a number"),
        ],
    )
    def test_malformed_national_block_is_refused(
        self, change: Any, message: str
    ) -> None:
        stored = national()
        change(stored)
        with pytest.raises(PublicUncertaintyError, match=message):
            publish(stored, regions())

    def test_rounding_slack_is_accepted(self) -> None:
        stored = national()
        # Published 0.306 + 0.695 = 1.001, within three terms' 0.0015.
        stored["hung"] = 0.6951
        publish(stored, regions())

    def test_region_odds_must_add_up_when_stored(self) -> None:
        stored = regions()
        stored["13"]["parties"]["2"]["pl"] = 0.5
        with pytest.raises(PublicUncertaintyError, match=r"regions.13: P\(largest\)"):
            publish(national(), stored)

    def test_malformed_region_is_refused(self) -> None:
        stored = regions()
        stored["13"]["hung"] = "high"
        with pytest.raises(PublicUncertaintyError, match="regions.13.hung"):
            publish(national(), stored)

    @pytest.mark.parametrize(
        ("national_block", "region_block", "where"),
        [
            (lambda n: n["parties"]["10"].pop("mean"), None, "national.parties.10"),
            (None, lambda r: r["14"]["parties"]["2"].pop("mean"), "regions.14"),
        ],
    )
    def test_a_run_without_mean_seats_is_refused(
        self, national_block: Any, region_block: Any, where: str
    ) -> None:
        # No median fallback: a run stored before mean seats must be rerun.
        stored, stored_regions = national(), regions()
        for change, block in ((national_block, stored), (region_block, stored_regions)):
            if change is not None:
                change(block)
        with pytest.raises(PublicUncertaintyError, match=f"{where}.*no stored mean"):
            publish(stored, stored_regions)

    def test_whole_seats_add_up_in_every_block(self) -> None:
        result: Any = document()
        assert sum(p["m"] for p in result["national"]["parties"].values()) == 299
        totals = {
            region: sum(p["m"] for p in block["parties"].values())
            for region, block in result["regions"].items()
        }
        assert totals == {"13": 150, "14": 149}

    def test_region_means_must_sum_to_a_whole_seat_total(self) -> None:
        stored = regions()
        stored["14"]["parties"]["10"]["mean"] = 1.2
        with pytest.raises(PublicUncertaintyError, match="regions.14: mean seats sum"):
            publish(national(), stored)

    def test_region_totals_must_add_up_to_the_national_total(self) -> None:
        stored = regions()
        stored["14"]["parties"]["10"]["mean"] = 2.5
        with pytest.raises(PublicUncertaintyError, match="add up to 300, not the"):
            publish(national(), stored)

    def test_national_histograms_must_total_a_whole_seat_count(self) -> None:
        # Each histogram passes on its own (party 10's mean 1.01 is its own),
        # but 59,802 seats over 200 simulations is no whole parliament.
        stored = national()
        stored["parties"]["10"]["hist"]["counts"] = [50, 98, 52]
        stored["parties"]["10"]["mean"] = 1.01
        with pytest.raises(PublicUncertaintyError, match="not a whole seat total"):
            publish(stored, regions())

    def test_every_party_needs_a_name(self) -> None:
        with pytest.raises(PublicUncertaintyError, match="no party name for 10"):
            public_document(
                national(),
                regions(),
                election_name="x",
                as_of_date=None,
                party_names={2: "Labour"},
            )

    def test_error_is_a_value_error(self) -> None:
        assert issubclass(PublicUncertaintyError, ValueError)


@pytest.mark.parametrize(
    ("interval", "simulations", "phrase"),
    [
        (0.9, 10_000, "90% ranges and probabilities from 10,000 simulations"),
        (0.8, 500, "80% ranges and probabilities from 500 simulations"),
    ],
)
def test_basis_label_states_interval_and_count(
    interval: float, simulations: int, phrase: str
) -> None:
    label = basis_label(interval, simulations)
    assert label.startswith("Nowcast: if an election were held today. ")
    assert phrase in label


class TestLargestRemainderSeats:
    NAMES = {"a": "Alba", "b": "Brexit", "c": "Conservative"}

    @pytest.mark.parametrize(
        ("means", "total", "expected"),
        [
            # Plain rounding gives 9: floors 2, 2, 5 and the spare seat to the
            # largest remainder (equal remainders and means: by name).
            ({"a": 2.4, "b": 2.4, "c": 5.2}, 10, {"a": 3, "b": 2, "c": 5}),
            # Plain rounding (half up) gives 11.
            ({"a": 1.5, "b": 1.5, "c": 7.0}, 10, {"a": 2, "b": 1, "c": 7}),
            # 0.29 * 100 is 28.999…: worked in exact hundredths.
            ({"a": 0.29, "b": 0.71}, 1, {"a": 0, "b": 1}),
            # Whole means need no leftover.
            ({"a": 300.0, "b": 350.0}, 650, {"a": 300, "b": 350}),
            # Means stored at 2 dp may sum a hair under the total.
            ({"a": 3.33, "b": 3.33, "c": 3.33}, 10, {"a": 4, "b": 3, "c": 3}),
        ],
    )
    def test_whole_seats_sum_to_the_total(
        self, means: dict[str, float], total: int, expected: dict[str, int]
    ) -> None:
        result = largest_remainder_seats(means, total, self.NAMES)
        assert result == expected
        assert sum(result.values()) == total

    def test_equal_remainders_go_to_the_larger_mean_before_the_name(self) -> None:
        # "Alba" sorts first, but Conservative's mean is larger.
        result = largest_remainder_seats({"a": 1.5, "c": 3.5}, 5, self.NAMES)
        assert result == {"a": 1, "c": 4}

    def test_order_of_the_input_does_not_matter(self) -> None:
        forwards = largest_remainder_seats(
            {"a": 1.5, "b": 1.5, "c": 7.0}, 10, self.NAMES
        )
        backwards = largest_remainder_seats(
            {"c": 7.0, "b": 1.5, "a": 1.5}, 10, self.NAMES
        )
        assert forwards == backwards == {"a": 2, "b": 1, "c": 7}
        assert list(backwards) == ["c", "b", "a"]

    @pytest.mark.parametrize(
        ("means", "total"), [({"a": 5.0}, 7), ({"a": 5.0, "b": 2.0}, 6)]
    )
    def test_means_that_cannot_reach_the_total_are_refused(
        self, means: dict[str, float], total: int
    ) -> None:
        with pytest.raises(PublicUncertaintyError, match=f"cannot round to {total}"):
            largest_remainder_seats(means, total, self.NAMES)


class TestTrendUncertainty:
    def test_bands_rounded_zero_odds_dropped(self) -> None:
        result = trend_uncertainty(national())
        assert result.parties == {
            "2": {
                "vl": 22.8,
                "vh": 30.6,
                "sl": 220,
                "sh": 376,
                "sm": 298.0,
                "pm": 0.306,
                "pl": 0.97,
            },
            "10": {"vl": 0.4, "vh": 0.4, "sl": 0, "sh": 2, "sm": 1.0, "pl": 0.03},
        }
        assert list(result.parties) == ["2", "10"]
        assert result.hung == 0.694

    def test_mean_seats_come_from_the_histogram(self) -> None:
        # Runs stored before the summary's own mean still get the line.
        stored = national()
        for party in stored["parties"].values():
            party.pop("mean")
        stored["parties"]["10"]["hist"]["counts"] = [50, 80, 70]
        result = trend_uncertainty(stored)
        assert result.parties["10"]["sm"] == 1.1
        assert result.parties["2"]["sm"] == 298.0

    def test_mean_needs_a_valid_histogram(self) -> None:
        stored = national()
        stored["parties"]["2"]["hist"]["counts"] = [1, 2]
        with pytest.raises(PublicUncertaintyError, match="!= 200"):
            trend_uncertainty(stored)

    @pytest.mark.parametrize(
        "seats", [[2, 1, 3], [-1, 1, 3], [0, 1, 2.5], [False, 1, 2], [0, 2]]
    )
    def test_seat_bounds_require_an_ordered_integer_range(self, seats: object) -> None:
        stored = national()
        stored["parties"]["2"]["s"] = seats
        with pytest.raises(PublicUncertaintyError, match="national.parties.2.s"):
            trend_uncertainty(stored)

    def test_smallest_probability_is_kept(self) -> None:
        stored = national()
        stored["parties"]["10"]["pl"] = 0.001
        stored["parties"]["2"]["pl"] = 0.999
        assert trend_uncertainty(stored).parties["10"]["pl"] == 0.001

    def test_dust_below_the_precision_counts_as_zero(self) -> None:
        stored = national()
        stored["parties"]["10"]["pm"] = 0.0004
        assert "pm" not in trend_uncertainty(stored).parties["10"]

    @pytest.mark.parametrize(("majorities", "accepted"), [(11, True), (12, False)])
    def test_slack_covers_rounding_twice(self, majorities: int, accepted: bool) -> None:
        # 20 terms (19 parties and hung) may each be off by 0.0005 from
        # publishing at 3 dp plus 0.00005 from storing at 4 dp: 0.011 in all.
        stored = national()
        stored["parties"] = {
            str(party): {
                "v": [0.1, 0.2],
                "s": [0, 0, 1],
                "pm": 0.001 if party < majorities else 0.0,
                "pl": 1.0 if party == 0 else 0.0,
                "hist": {"start": 0, "counts": [100, 100]},
            }
            for party in range(19)
        }
        stored["hung"] = 1.0
        if accepted:
            trend_uncertainty(stored)
        else:
            with pytest.raises(PublicUncertaintyError, match="sums to 1.012"):
                trend_uncertainty(stored)

    def test_odds_must_add_up(self) -> None:
        stored = national()
        stored["hung"] = 0.5
        with pytest.raises(PublicUncertaintyError, match=r"P\(majority\) \+ P\(hung\)"):
            trend_uncertainty(stored)


class TestSeatBands:
    def test_bands_by_seat_and_party(self) -> None:
        stored = {
            "1000": {
                "1": {"v": [31.32, 49.57], "w": 0.9752},
                "15": {"v": [0.95, 10.17], "w": 0.0},
            },
            "7": {"2": {"v": [40.0, 60.0], "w": 1.0}},
        }
        assert seat_bands(stored) == {
            7: {2: SeatBand(40.0, 60.0, 1.0)},
            # Python's round, as the trend file's v: the float 0.95 is just
            # under 0.95, so it rounds down.
            1000: {1: SeatBand(31.3, 49.6, 0.975), 15: SeatBand(0.9, 10.2, 0.0)},
        }

    @pytest.mark.parametrize(
        "seat",
        [{"1": {"v": [31.0, 49.0]}}, {"1": {"v": [49.0, 31.0], "w": 0.5}}, ["x"]],
    )
    def test_malformed_seat_is_refused(self, seat: object) -> None:
        with pytest.raises(PublicUncertaintyError, match="seats.1000"):
            seat_bands({"1000": seat})
