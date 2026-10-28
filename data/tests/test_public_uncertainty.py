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
    public_document,
    seat_bands,
    trend_uncertainty,
)

# Private values that must never reach a public file. Each is distinctive, so
# finding its text in the output means it leaked.
PRIVATE_VALUES = ("budget", "config_hash", "input_hash", "seed", "73990173")


# Party 2's histogram: 218..378 seats, the ends non-empty, 200 simulations.
HIST_2 = [100] + [0] * 159 + [100]


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
                "pm": 0.0,
                "pl": 0.0303,
                "hist": {"start": 0, "counts": [50, 100, 50]},
            },
            "2": {
                "v": [22.84, 30.56],
                "s": [220, 301, 376],
                "pm": 0.30604,
                "pl": 0.9697,
                "hist": {"start": 218, "counts": HIST_2},
                "budget": {"leak": True},
            },
        },
    }


def regions() -> dict[str, Any]:
    return {
        "14": {
            "budget": {"2": {"sd": 1.2}},
            "parties": {"2": {"v": [17.34, 26.39], "s": [19, 29, 40]}},
        },
        "13": {
            "budget": {},
            "hung": 0.9877,
            "parties": {
                "2": {
                    "v": [17.34, 26.39],
                    "s": [19, 29, 40],
                    "pm": 0.0123,
                    "pl": 1.0,
                    "budget": {},
                }
            },
        },
    }


def document() -> dict[str, object]:
    return public_document(
        national(), regions(), election_name="UNS 2026-10-12", as_of_date="2026-10-12"
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
                        "pm": 0.306,
                        "pl": 0.9697,
                        "hist": {"start": 218, "counts": HIST_2},
                    },
                    "10": {
                        "v": [0.4, 0.4],
                        "s": [0, 1, 2],
                        "pm": 0.0,
                        "pl": 0.0303,
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
                            "s": [19, 29, 40],
                            "pm": 0.0123,
                            "pl": 1.0,
                        }
                    },
                    "hung": 0.9877,
                },
                "14": {"parties": {"2": {"v": [17.3, 26.4], "s": [19, 29, 40]}}},
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
        public_document(stored, stored_regions, election_name="x", as_of_date=None)
        assert (stored, stored_regions) == before

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda n: n.pop("simulations"), "national.simulations"),
            (lambda n: n.update(interval=1.0), "out of range"),
            (lambda n: n["parties"]["2"]["hist"].update(counts=[1, 2]), "!= 200"),
            # The seat range must sit inside a histogram whose ends are filled.
            (lambda n: n["parties"]["2"]["hist"].update(start=221), "does not span"),
            (lambda n: n["parties"]["2"]["hist"].update(counts=[0, *HIST_2[1:], 100]),
             "does not span"),
            (lambda n: n["parties"]["2"]["hist"].update(counts=[*HIST_2, 0]),
             "does not span"),
            # Exclusive outcomes: rounding allows half a unit per term, no more.
            (lambda n: n.update(hung=0.6942), r"P\(majority\) \+ P\(hung\) sums"),
            (lambda n: n["parties"]["10"].update(pl=0.0305), r"P\(largest\) sums"),
            (lambda n: n["parties"]["2"].update(s=[301, 220, 376]), "out of order"),
            (lambda n: n["parties"]["2"].update(s=[220.0, 301, 376]), "integer"),
            (lambda n: n["parties"]["2"].update(v=[22.8]), r"\[lo, hi\]"),
            (lambda n: n["parties"]["2"].update(pm=-0.1), "outside 0-1"),
            (lambda n: n["parties"]["2"].update(pm=float("nan")), "not finite"),
            (lambda n: n.update(parties={"Labour": {}}), "integers"),
            (lambda n: n.pop("hung"), "national.hung"),
        ],
    )
    def test_malformed_national_block_is_refused(
        self, change: Any, message: str
    ) -> None:
        stored = national()
        change(stored)
        with pytest.raises(PublicUncertaintyError, match=message):
            public_document(stored, regions(), election_name="x", as_of_date=None)

    def test_rounding_slack_is_accepted(self) -> None:
        stored = national()
        # 0.30604 + 0.6941 = 1.00014, within three terms' 0.00015.
        stored["hung"] = 0.6941
        public_document(stored, regions(), election_name="x", as_of_date=None)

    def test_region_odds_must_add_up_when_stored(self) -> None:
        stored = regions()
        stored["13"]["parties"]["2"]["pl"] = 0.5
        with pytest.raises(PublicUncertaintyError, match=r"regions.13: P\(largest\)"):
            public_document(national(), stored, election_name="x", as_of_date=None)

    def test_malformed_region_is_refused(self) -> None:
        stored = regions()
        stored["13"]["hung"] = "high"
        with pytest.raises(PublicUncertaintyError, match="regions.13.hung"):
            public_document(national(), stored, election_name="x", as_of_date=None)

    def test_error_is_a_value_error(self) -> None:
        assert issubclass(PublicUncertaintyError, ValueError)


@pytest.mark.parametrize(
    ("interval", "simulations", "phrase"),
    [(0.9, 10_000, "90% ranges and probabilities from 10,000 simulations"),
     (0.8, 500, "80% ranges and probabilities from 500 simulations")],
)
def test_basis_label_states_interval_and_count(
    interval: float, simulations: int, phrase: str
) -> None:
    label = basis_label(interval, simulations)
    assert label.startswith("Nowcast: if an election were held today. ")
    assert phrase in label


class TestTrendUncertainty:
    def test_bands_rounded_zero_odds_dropped(self) -> None:
        result = trend_uncertainty(national())
        assert result.parties == {
            "2": {"vl": 22.8, "vh": 30.6, "sl": 220, "sh": 376, "pm": 0.306, "pl": 0.9697},
            "10": {"vl": 0.4, "vh": 0.4, "sl": 0, "sh": 2, "pl": 0.0303},
        }
        assert list(result.parties) == ["2", "10"]
        assert result.hung == 0.694

    @pytest.mark.parametrize("seats", [[2, 1, 3], [-1, 1, 3], [0, 1, 2.5], [False, 1, 2], [0, 2]])
    def test_seat_bounds_require_an_ordered_integer_range(self, seats: object) -> None:
        stored = national()
        stored["parties"]["2"]["s"] = seats
        with pytest.raises(PublicUncertaintyError, match="national.parties.2.s"):
            trend_uncertainty(stored)

    def test_smallest_probability_is_kept(self) -> None:
        stored = national()
        stored["parties"]["10"]["pl"] = 0.0001
        stored["parties"]["2"]["pl"] = 0.9999
        assert trend_uncertainty(stored).parties["10"]["pl"] == 0.0001

    def test_dust_below_the_precision_counts_as_zero(self) -> None:
        stored = national()
        stored["parties"]["10"]["pm"] = 0.00004
        assert "pm" not in trend_uncertainty(stored).parties["10"]

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
            1000: {1: SeatBand(31.3, 49.6, 0.9752), 15: SeatBand(0.9, 10.2, 0.0)},
        }

    @pytest.mark.parametrize(
        "seat",
        [{"1": {"v": [31.0, 49.0]}}, {"1": {"v": [49.0, 31.0], "w": 0.5}}, ["x"]],
    )
    def test_malformed_seat_is_refused(self, seat: object) -> None:
        with pytest.raises(PublicUncertaintyError, match="seats.1000"):
            seat_bands({"1000": seat})
