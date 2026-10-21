"""The public subset of a stored Monte Carlo summary, for the site's JSON files.

Stored summaries (``persistence.load_uncertainty``) hold private diagnostics
beside the results: the error budget, the seed, the config and input hashes.
This module is the one whitelist of what is published. Each function copies
named fields only, validates them, and rounds them to the published precision:

* vote shares: percentage points, 1 dp (as the trend file's ``v``);
* probabilities: fractions 0-1, 4 dp (as stored; 1 in 10,000 is one
  simulation at the default count);
* seats: integers.

Anything malformed raises :class:`PublicUncertaintyError` (a ``ValueError``)
rather than publishing a guess. Numpy-free: it works on decoded JSON.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

PUBLIC_SCHEMA = "pf-uncertainty-v1"
SHARE_DIGITS = 1
PROBABILITY_DIGITS = 4

# The whitelist: the only stored fields ever published, per block. Anything
# else a stored block holds (the error budget, seed, hashes) is withheld.
PUBLIC_NATIONAL_FIELDS: tuple[str, ...] = ("simulations", "interval", "parties", "hung")
PUBLIC_NATIONAL_PARTY_FIELDS: tuple[str, ...] = ("v", "s", "pm", "pl", "hist")
PUBLIC_REGION_FIELDS: tuple[str, ...] = ("parties", "hung")
PUBLIC_REGION_PARTY_FIELDS: tuple[str, ...] = ("v", "s", "pm", "pl")
PUBLIC_SEAT_PARTY_FIELDS: tuple[str, ...] = ("v", "w")
# Regional odds (and a region's hung) were added after the first stored
# summaries, so a region copies them only when stored.
_OPTIONAL_REGION_PARTY_FIELDS = frozenset({"pm", "pl"})
# Half a unit in the last published place: the most rounding can move one
# probability.
_ROUNDING = 0.5 * 10**-PROBABILITY_DIGITS


class PublicUncertaintyError(ValueError):
    """A stored summary lacks, or garbles, a field that is published."""


@dataclass(frozen=True, slots=True)
class TrendUncertainty:
    """One run's additions to its trend-file entry.

    Attributes:
        parties: Party id -> ``{"vl", "vh", "sl", "sh"}`` (national vote
            and seat bounds) plus
            ``"pm"`` (P majority) and ``"pl"`` (P largest) when non-zero; a
            zero probability is left out to keep the file small.
        hung: P(hung parliament).
    """

    parties: dict[str, dict[str, int | float]]
    hung: float


@dataclass(frozen=True, slots=True)
class SeatBand:
    """One party's published seat uncertainty.

    Attributes:
        lo: Lower bound of its share interval, points.
        hi: Upper bound, points.
        win: Probability it wins the seat.
    """

    lo: float
    hi: float
    win: float


def _mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PublicUncertaintyError(f"{where}: expected an object")
    return value


def _number(value: object, where: str) -> float:
    # bool is an int subclass; a stored true/false is never a number here.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PublicUncertaintyError(f"{where}: expected a number")
    if not math.isfinite(value):
        raise PublicUncertaintyError(f"{where}: not finite")
    return float(value)


def _count(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicUncertaintyError(f"{where}: expected a non-negative integer")
    return value


def _probability(value: object, where: str) -> float:
    number = _number(value, where)
    if not 0.0 <= number <= 1.0:
        raise PublicUncertaintyError(f"{where}: {number} is outside 0-1")
    return round(number, PROBABILITY_DIGITS)


def _share_band(value: object, where: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise PublicUncertaintyError(f"{where}: expected [lo, hi]")
    lo, hi = (_number(item, where) for item in value)
    if not 0.0 <= lo <= hi <= 100.0:
        raise PublicUncertaintyError(f"{where}: {lo}-{hi} is not a share interval")
    return [round(lo, SHARE_DIGITS), round(hi, SHARE_DIGITS)]


def _seat_range(value: object, where: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 3:
        raise PublicUncertaintyError(f"{where}: expected [lo, median, hi]")
    lo, median, hi = (_count(item, where) for item in value)
    if not lo <= median <= hi:
        raise PublicUncertaintyError(f"{where}: {lo}/{median}/{hi} is out of order")
    return [lo, median, hi]


def _histogram(value: object, where: str, simulations: int) -> dict[str, object]:
    start, counts = _histogram_parts(value, where, simulations)
    return {"start": start, "counts": counts}


def _histogram_parts(
    value: object, where: str, simulations: int
) -> tuple[int, list[int]]:
    block = _mapping(value, where)
    start = _count(block.get("start"), f"{where}.start")
    counts = block.get("counts")
    if not isinstance(counts, list) or not counts:
        raise PublicUncertaintyError(f"{where}.counts: expected a non-empty list")
    checked = [_count(item, f"{where}.counts") for item in counts]
    # Every simulation lands in exactly one bin: a free end-to-end check.
    if sum(checked) != simulations:
        raise PublicUncertaintyError(
            f"{where}.counts: sum {sum(checked)} != {simulations} simulations"
        )
    return start, checked


def _sorted_ids(block: Mapping[str, object], where: str) -> list[str]:
    try:
        return sorted(block, key=int)
    except ValueError as err:
        raise PublicUncertaintyError(f"{where}: ids must be integers") from err


def _parties(national: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(national.get("parties"), "national.parties")


def run_size(national: Mapping[str, object]) -> tuple[int, float]:
    """Return a national block's simulation count and interval level.

    Raises:
        PublicUncertaintyError: Either is missing or out of range.
    """
    simulations = _count(national.get("simulations"), "national.simulations")
    interval = _number(national.get("interval"), "national.interval")
    if simulations == 0 or not 0.0 < interval < 1.0:
        raise PublicUncertaintyError("national: simulations or interval out of range")
    return simulations, interval


def basis_label(interval: float, simulations: int) -> str:
    """Return the one-line reading guide published with the figures.

    Args:
        interval: Interval level, e.g. 0.9.
        simulations: Simulation count.

    Returns:
        E.g. "Nowcast: if an election were held today. 90% ranges and
        probabilities from 10,000 simulations, conditional on current polls
        and the model's error assumptions."
    """
    return (
        "Nowcast: if an election were held today. "
        f"{interval * 100:g}% ranges and probabilities from {simulations:,} "
        "simulations, conditional on current polls and the model's error "
        "assumptions."
    )


def trend_uncertainty(national: Mapping[str, object]) -> TrendUncertainty:
    """Return a run's trend-file additions from its stored national block.

    Args:
        national: The run's decoded ``national_json``.

    Returns:
        Per party the 1 dp vote band, integer seat bounds and non-zero P(majority) and
        P(largest), plus P(hung).

    Raises:
        PublicUncertaintyError: A published field is missing or malformed.
    """
    parties: dict[str, dict[str, int | float]] = {}
    odds: dict[str, dict[str, object]] = {}
    block = _parties(national)
    for party_id in _sorted_ids(block, "national.parties"):
        where = f"national.parties.{party_id}"
        party = _mapping(block[party_id], where)
        lo, hi = _share_band(party.get("v"), f"{where}.v")
        seat_lo, _, seat_hi = _seat_range(party.get("s"), f"{where}.s")
        fields: dict[str, int | float] = {
            "vl": lo, "vh": hi, "sl": seat_lo, "sh": seat_hi,
        }
        odds[party_id] = {}
        for key in ("pm", "pl"):
            probability = _probability(party.get(key), f"{where}.{key}")
            odds[party_id][key] = probability
            if probability > 0:
                fields[key] = probability
        parties[party_id] = fields
    hung = _probability(national.get("hung"), "national.hung")
    _check_odds(odds, hung, "national")
    return TrendUncertainty(parties, hung)


def seat_bands(seats: Mapping[str, object]) -> dict[int, dict[int, SeatBand]]:
    """Return per seat and party the published share band and win probability.

    The stored seat block leaves out parties below its compact floor (band
    under 0.5 pt and never winning), so a party can be absent for a seat.

    Args:
        seats: The run's decoded ``seats_json``: seat id -> party id ->
            ``{"v": [lo, hi], "w": p}``.

    Returns:
        Seat id -> party id -> :class:`SeatBand`.

    Raises:
        PublicUncertaintyError: A seat or party entry is malformed.
    """
    result: dict[int, dict[int, SeatBand]] = {}
    for seat_id in _sorted_ids(seats, "seats"):
        seat = _mapping(seats[seat_id], f"seats.{seat_id}")
        bands: dict[int, SeatBand] = {}
        for party_id in _sorted_ids(seat, f"seats.{seat_id}"):
            where = f"seats.{seat_id}.{party_id}"
            party = _mapping(seat[party_id], where)
            # PUBLIC_SEAT_PARTY_FIELDS: the band v and the win probability w.
            lo, hi = _share_band(party.get("v"), f"{where}.v")
            win = _probability(party.get("w"), f"{where}.w")
            bands[int(party_id)] = SeatBand(lo, hi, win)
        result[int(seat_id)] = bands
    return result


def _public_fields(
    block: Mapping[str, object],
    where: str,
    fields: tuple[str, ...],
    *,
    simulations: int = 0,
    optional: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Copy and validate the named fields of a party block, in ``fields`` order.

    Args:
        block: The stored party block.
        where: Its path, for error messages.
        fields: The fields to publish (a ``PUBLIC_*_FIELDS`` tuple).
        simulations: The run's simulation count (checks ``hist``).
        optional: Fields copied only when stored.

    Raises:
        PublicUncertaintyError: A required field is missing, or any is
            malformed.
    """
    copied: dict[str, object] = {}
    for field in fields:
        if field in optional and field not in block:
            continue
        value, at = block.get(field), f"{where}.{field}"
        if field == "v":
            copied[field] = _share_band(value, at)
        elif field == "s":
            copied[field] = _seat_range(value, at)
        elif field == "hist":
            copied[field] = _histogram(value, at, simulations)
        else:  # pm, pl, w: probabilities
            copied[field] = _probability(value, at)
    return copied


def _check_histogram(
    party: Mapping[str, object], where: str, simulations: int
) -> None:
    """The seat range must lie inside the histogram, which spans min to max."""
    lo, _, hi = _seat_range(party.get("s"), f"{where}.s")
    start, counts = _histogram_parts(party.get("hist"), f"{where}.hist", simulations)
    end = start + len(counts) - 1
    if not start <= lo <= hi <= end or counts[0] == 0 or counts[-1] == 0:
        raise PublicUncertaintyError(
            f"{where}.hist: {start}-{end} (ends {counts[0]}, {counts[-1]}) does not "
            f"span seats {lo}-{hi} from the fewest to the most simulated"
        )


def _check_odds(
    parties: Mapping[str, Mapping[str, object]], hung: object, where: str
) -> None:
    """Majorities and hung are exclusive and exhaustive; so are largest shares.

    Checked on the published (rounded) values: each sum may be off by the
    rounding of its terms, half a unit in the last place each. Skipped for a
    sum whose terms are not all stored (older regional summaries).
    """
    majorities = [party.get("pm") for party in parties.values()]
    largest = [party.get("pl") for party in parties.values()]
    checks = (
        ("P(majority) + P(hung)", [*majorities, hung]),
        ("P(largest)", largest),
    )
    for label, terms in checks:
        if any(not isinstance(term, float) for term in terms):
            continue
        total = sum(term for term in terms if isinstance(term, float))
        if abs(total - 1.0) > len(terms) * _ROUNDING + 1e-12:
            raise PublicUncertaintyError(f"{where}: {label} sums to {total:.4f}, not 1")


def public_document(
    national: Mapping[str, object],
    regions: Mapping[str, object],
    *,
    election_name: str,
    as_of_date: str | None,
) -> dict[str, object]:
    """Return the ``pf-uncertainty-v1`` document for one run.

    Args:
        national: The run's decoded ``national_json``.
        regions: Its decoded ``regions_json``.
        election_name: The run's election name, e.g. "UNS 2026-10-12".
        as_of_date: Its as-of date, ISO format (None if its name has none).

    Returns:
        ``{schema, as_of_date, election_name, simulations, interval, basis,
        national: {parties: {pid: {v, s, pm, pl, hist}}, hung},
        regions: {rid: {parties: {pid: {v, s[, pm, pl]}}[, hung]}}}``.
        Regional ``pm``/``pl``/``hung`` (a majority of that region's seats)
        appear only where the run stored them. Parties and regions are in
        id order.

    Raises:
        PublicUncertaintyError: A published field is missing or malformed.
    """
    simulations, interval = run_size(national)
    national_parties: dict[str, dict[str, object]] = {}
    block = _parties(national)
    for party_id in _sorted_ids(block, "national.parties"):
        where = f"national.parties.{party_id}"
        stored = _mapping(block[party_id], where)
        party = _public_fields(
            stored, where, PUBLIC_NATIONAL_PARTY_FIELDS, simulations=simulations
        )
        _check_histogram(stored, where, simulations)
        national_parties[party_id] = party
    hung = _probability(national.get("hung"), "national.hung")
    _check_odds(national_parties, hung, "national")
    public_regions: dict[str, object] = {}
    for region_id in _sorted_ids(regions, "regions"):
        where = f"regions.{region_id}"
        region = _mapping(regions[region_id], where)
        region_parties = _mapping(region.get("parties"), f"{where}.parties")
        parties: dict[str, dict[str, object]] = {
            party_id: _public_fields(
                _mapping(region_parties[party_id], f"{where}.parties.{party_id}"),
                f"{where}.parties.{party_id}",
                PUBLIC_REGION_PARTY_FIELDS,
                optional=_OPTIONAL_REGION_PARTY_FIELDS,
            )
            for party_id in _sorted_ids(region_parties, f"{where}.parties")
        }
        entry: dict[str, object] = {"parties": parties}
        if "hung" in region:
            entry["hung"] = _probability(region["hung"], f"{where}.hung")
        # Below-floor parties are left out of a region, but they never win a
        # seat there, so their odds are 0 and the sums still hold.
        _check_odds(parties, entry.get("hung"), where)
        public_regions[region_id] = entry
    document: dict[str, object] = {
        "schema": PUBLIC_SCHEMA,
        "as_of_date": as_of_date,
        "election_name": election_name,
        "simulations": simulations,
        "interval": interval,
        "basis": basis_label(interval, simulations),
        "national": {"parties": national_parties, "hung": hung},
        "regions": public_regions,
    }
    return document
