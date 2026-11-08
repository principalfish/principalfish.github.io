"""The public subset of a stored Monte Carlo summary, for the site's JSON files.

Stored summaries (``persistence.load_uncertainty``) hold private diagnostics
beside the results: the error budget, the seed, the config and input hashes.
This module is the one whitelist of what is published. Each function copies
named fields only, validates them, and rounds them to the published precision:

* vote shares: percentage points, 1 dp (as the trend file's ``v``);
* probabilities: fractions 0-1, 3 dp (stored at 4 dp, but at the default
  10,000 simulations a probability's Monte Carlo standard error reaches half
  a percentage point, so the fourth place is noise; the site shows whole
  percentages);
* seats: integers; mean seats 2 dp (as stored), and each block's whole seats
  ``m``, the means rounded by largest remainder so a block's whole seats add
  up to its seat total.

Anything malformed raises :class:`PublicUncertaintyError` (a ``ValueError``)
rather than publishing a guess. Numpy-free: it works on decoded JSON.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

PUBLIC_SCHEMA = "pf-uncertainty-v1"
SHARE_DIGITS = 1
PROBABILITY_DIGITS = 3
# Mean seats: published as stored, and 1 dp in the trend file (a line, not a
# table figure).
MEAN_SEAT_DIGITS = 2
TREND_MEAN_SEAT_DIGITS = 1
# Places a stored summary rounds probabilities to (monte_carlo's own rounding).
_STORED_PROBABILITY_DIGITS = 4

# The whitelist: the only stored fields ever published, per block. Anything
# else a stored block holds (the error budget, seed, hashes) is withheld. A
# party's published whole seats ``m`` are derived from ``mean``, not stored.
PUBLIC_NATIONAL_FIELDS: tuple[str, ...] = ("simulations", "interval", "parties", "hung")
PUBLIC_NATIONAL_PARTY_FIELDS: tuple[str, ...] = ("v", "s", "mean", "pm", "pl", "hist")
PUBLIC_REGION_FIELDS: tuple[str, ...] = ("parties", "hung")
PUBLIC_REGION_PARTY_FIELDS: tuple[str, ...] = ("v", "s", "mean", "pm", "pl")
PUBLIC_SEAT_PARTY_FIELDS: tuple[str, ...] = ("v", "w")
# Regional odds (and a region's hung) were added after the first stored
# summaries, so a region copies them only when stored.
_OPTIONAL_REGION_PARTY_FIELDS = frozenset({"pm", "pl"})
# The most rounding can move one published probability: half a unit in the
# last published place, after half a unit in the last stored place (the
# published value is rounded again from an already rounded stored one).
_ROUNDING = 0.5 * 10**-PROBABILITY_DIGITS + 0.5 * 10**-_STORED_PROBABILITY_DIGITS
# The largest count a float holds exactly; seat and simulation counts are far
# below it, and the means divide these counts as floats.
_MAX_COUNT = 2**53


class PublicUncertaintyError(ValueError):
    """A stored summary lacks, or garbles, a field that is published."""


@dataclass(frozen=True, slots=True)
class TrendUncertainty:
    """One run's additions to its trend-file entry.

    Attributes:
        parties: Party id -> ``{"vl", "vh", "sl", "sh", "sm"}`` (national
            vote and seat bounds, and the mean seats from the stored
            histogram, 1 dp) plus
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
    try:
        number = float(value)
    except OverflowError as err:
        # A JSON integer literal too large for a float.
        raise PublicUncertaintyError(f"{where}: out of range") from err
    if not math.isfinite(number):
        raise PublicUncertaintyError(f"{where}: not finite")
    return number


def _count(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicUncertaintyError(f"{where}: expected a non-negative integer")
    if value > _MAX_COUNT:
        raise PublicUncertaintyError(f"{where}: out of range")
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


def _seat_mean(value: object, where: str) -> float:
    if value is None:
        # Never a median stand-in: a run stored before mean seats must be rerun.
        raise PublicUncertaintyError(
            f"{where}: no stored mean seats (the run predates them); rerun the "
            "model for its date"
        )
    number = _number(value, where)
    if number < 0:
        raise PublicUncertaintyError(f"{where}: {number} is negative")
    if number > _MAX_COUNT:
        raise PublicUncertaintyError(f"{where}: out of range")
    return round(number, MEAN_SEAT_DIGITS)


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


def _histogram_seat_sum(start: int, counts: list[int]) -> int:
    """Total seats over the simulations: the histogram's mean times S, exactly."""
    return sum((start + offset) * count for offset, count in enumerate(counts))


def largest_remainder_seats(
    means: Mapping[str, float], total: int, names: Mapping[str, str]
) -> dict[str, int]:
    """Round mean seats to whole seats that add up to ``total``.

    Each mean is floored, and the seats left over go one each to the largest
    remainders. Equal remainders go to the larger mean first, then by name,
    so the result never depends on dict order. The means are worked in exact
    hundredths (they are published at 2 dp), so no float noise decides a tie.

    Args:
        means: Party id -> mean seats, 2 dp.
        total: The block's seat total.
        names: Party id -> name, for the last tie-break.

    Returns:
        Party id -> whole seats, in ``means`` order.

    Raises:
        PublicUncertaintyError: A party has no name, or the means cannot
            round to ``total`` (the leftover is negative or exceeds the
            party count).
    """
    hundredths = {party: round(mean * 100) for party, mean in means.items()}
    whole = {party: value // 100 for party, value in hundredths.items()}
    leftover = total - sum(whole.values())
    if not 0 <= leftover <= len(whole):
        raise PublicUncertaintyError(
            f"mean seats sum to {sum(means.values()):.2f}; cannot round to {total}"
        )
    missing = sorted(set(means) - set(names))
    if missing:
        raise PublicUncertaintyError(f"no party name for {', '.join(missing)}")

    def rank(party: str) -> tuple[int, int, str]:
        return -(hundredths[party] % 100), -hundredths[party], names[party]

    order = sorted(means, key=rank)
    for party in order[:leftover]:
        whole[party] += 1
    return {party: whole[party] for party in means}


def _means(parties: Mapping[str, Mapping[str, object]]) -> dict[str, float]:
    """Each published party's mean seats (already validated by ``_seat_mean``)."""
    means: dict[str, float] = {}
    for party, fields in parties.items():
        mean = fields["mean"]
        if not isinstance(mean, float):
            raise PublicUncertaintyError(f"party {party}: mean seats not validated")
        means[party] = mean
    return means


def _with_whole_seats(
    parties: Mapping[str, Mapping[str, object]],
    total: int,
    names: Mapping[str, str],
    where: str,
) -> dict[str, dict[str, object]]:
    """Return the parties with their whole seats ``m`` placed after ``mean``."""
    try:
        whole = largest_remainder_seats(_means(parties), total, names)
    except PublicUncertaintyError as err:
        raise PublicUncertaintyError(f"{where}: {err}") from err
    result: dict[str, dict[str, object]] = {}
    for party, fields in parties.items():
        result[party] = {}
        for key, value in fields.items():
            result[party][key] = value
            if key == "mean":
                result[party]["m"] = whole[party]
    return result


def _region_total(parties: Mapping[str, Mapping[str, object]], where: str) -> int:
    """A region's seat total: its parties' mean seats sum to it, up to rounding.

    A region block holds no histogram, so the total is the sum of the 2 dp
    means rounded, checked to lie within their rounding (half a hundredth
    each) of a whole number. A party left out of the region never wins a
    seat there, so its mean is 0 and the sum still holds.
    """
    total_mean = sum(_means(parties).values())
    total = round(total_mean)
    if abs(total_mean - total) > 0.5 * 10**-MEAN_SEAT_DIGITS * len(parties) + 1e-9:
        raise PublicUncertaintyError(
            f"{where}: mean seats sum to {total_mean:.2f}, not a whole seat total"
        )
    return total


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
        Per party the 1 dp vote band, integer seat bounds, 1 dp mean seats and
        non-zero P(majority) and P(largest), plus P(hung).

    Raises:
        PublicUncertaintyError: A published field is missing or malformed.
    """
    simulations, _ = run_size(national)
    parties: dict[str, dict[str, int | float]] = {}
    odds: dict[str, dict[str, object]] = {}
    block = _parties(national)
    for party_id in _sorted_ids(block, "national.parties"):
        where = f"national.parties.{party_id}"
        party = _mapping(block[party_id], where)
        lo, hi = _share_band(party.get("v"), f"{where}.v")
        seat_lo, _, seat_hi = _seat_range(party.get("s"), f"{where}.s")
        # From the histogram, which every stored national block has, so runs
        # stored before the summary's own mean get the line too.
        start, counts = _histogram_parts(
            party.get("hist"), f"{where}.hist", simulations
        )
        fields: dict[str, int | float] = {
            "vl": lo,
            "vh": hi,
            "sl": seat_lo,
            "sh": seat_hi,
            "sm": round(
                _histogram_seat_sum(start, counts) / simulations, TREND_MEAN_SEAT_DIGITS
            ),
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
        elif field == "mean":
            copied[field] = _seat_mean(value, at)
        elif field == "hist":
            copied[field] = _histogram(value, at, simulations)
        else:  # pm, pl, w: probabilities
            copied[field] = _probability(value, at)
    return copied


def _check_histogram(party: Mapping[str, object], where: str, simulations: int) -> int:
    """Check the histogram against the range and mean; return its seat sum.

    The seat range must lie inside the histogram, which spans min to max, and
    the stored 2 dp mean must be the histogram's mean, rounded.

    Returns:
        The party's seats summed over the simulations
        (:func:`_histogram_seat_sum`).
    """
    lo, _, hi = _seat_range(party.get("s"), f"{where}.s")
    start, counts = _histogram_parts(party.get("hist"), f"{where}.hist", simulations)
    end = start + len(counts) - 1
    if not start <= lo <= hi <= end or counts[0] == 0 or counts[-1] == 0:
        raise PublicUncertaintyError(
            f"{where}.hist: {start}-{end} (ends {counts[0]}, {counts[-1]}) does not "
            f"span seats {lo}-{hi} from the fewest to the most simulated"
        )
    seat_sum = _histogram_seat_sum(start, counts)
    mean = _seat_mean(party.get("mean"), f"{where}.mean")
    if abs(mean - seat_sum / simulations) > 0.5 * 10**-MEAN_SEAT_DIGITS + 1e-9:
        raise PublicUncertaintyError(
            f"{where}.mean: {mean} is not the histogram's mean "
            f"{seat_sum / simulations:.4f}"
        )
    return seat_sum


def _check_odds(
    parties: Mapping[str, Mapping[str, object]], hung: object, where: str
) -> None:
    """Majorities and hung are exclusive and exhaustive; so are largest shares.

    Checked on the published (rounded) values: each sum may be off by the
    rounding of its terms, ``_ROUNDING`` each (stored, then published).
    Skipped for a sum whose terms are not all stored (older regional
    summaries).
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
            raise PublicUncertaintyError(
                f"{where}: {label} sums to {total:.{PROBABILITY_DIGITS}f}, not 1"
            )


def public_document(
    national: Mapping[str, object],
    regions: Mapping[str, object],
    *,
    election_name: str,
    as_of_date: str | None,
    party_names: Mapping[int, str],
) -> dict[str, object]:
    """Return the ``pf-uncertainty-v1`` document for one run.

    Args:
        national: The run's decoded ``national_json``.
        regions: Its decoded ``regions_json``.
        election_name: The run's election name, e.g. "UNS 2026-10-12".
        as_of_date: Its as-of date, ISO format (None if its name has none).
        party_names: Party id -> name: the last tie-break of the whole seats.

    Returns:
        ``{schema, as_of_date, election_name, simulations, interval, basis,
        national: {parties: {pid: {v, s, mean, m, pm, pl, hist}}, hung},
        regions: {rid: {parties: {pid: {v, s, mean, m[, pm, pl]}}[, hung]}}}``.
        ``m`` is :func:`largest_remainder_seats` of the block's means, so a
        block's ``m`` add up to its seat total (nationally the histograms'
        exact total; a region's, its rounded sum of means; the regions'
        totals must add up to the national one). Regional ``pm``/``pl``/
        ``hung`` (a majority of that region's seats) appear only where the
        run stored them. Parties and regions are in id order.

    Raises:
        PublicUncertaintyError: A published field is missing or malformed,
            including a run stored without mean seats.
    """
    simulations, interval = run_size(national)
    names = {str(party_id): name for party_id, name in party_names.items()}
    national_parties: dict[str, dict[str, object]] = {}
    seat_sum = 0
    block = _parties(national)
    for party_id in _sorted_ids(block, "national.parties"):
        where = f"national.parties.{party_id}"
        stored = _mapping(block[party_id], where)
        party = _public_fields(
            stored, where, PUBLIC_NATIONAL_PARTY_FIELDS, simulations=simulations
        )
        seat_sum += _check_histogram(stored, where, simulations)
        national_parties[party_id] = party
    # Every simulation fills every seat, so the histograms' seats over all
    # parties are exactly S times the seat total.
    national_total, remainder = divmod(seat_sum, simulations)
    if remainder:
        raise PublicUncertaintyError(
            f"national: the histograms total {seat_sum} seats over {simulations} "
            "simulations, not a whole seat total"
        )
    national_parties = _with_whole_seats(
        national_parties, national_total, names, "national"
    )
    hung = _probability(national.get("hung"), "national.hung")
    _check_odds(national_parties, hung, "national")
    region_totals = 0
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
        region_total = _region_total(parties, where)
        region_totals += region_total
        parties = _with_whole_seats(parties, region_total, names, where)
        entry: dict[str, object] = {"parties": parties}
        if "hung" in region:
            entry["hung"] = _probability(region["hung"], f"{where}.hung")
        # Below-floor parties are left out of a region, but they never win a
        # seat there, so their odds are 0 and the sums still hold.
        _check_odds(parties, entry.get("hung"), where)
        public_regions[region_id] = entry
    if regions and region_totals != national_total:
        raise PublicUncertaintyError(
            f"regions: seat totals add up to {region_totals}, not the national "
            f"{national_total}"
        )
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
