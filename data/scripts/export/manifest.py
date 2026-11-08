"""map-modes.json manifest construction for the election export."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
import math
import re
from typing import Any, Sequence

from models import Election, ElectionType, Party, Region
from scripts.export.legacy import SUPPLEMENTAL_LEGACY_ELECTIONS
from scripts.export.naming import party_key_for_party

# Election types that compare with the election of the same type exactly N years earlier
# rather than the preceding one. The US Senate's staggered classes contest a different
# third of the states each cycle, so only the cycle six years back contests the same class
# of seats; when that election is not in the list the entry has no comparison. See
# ``assign_comparison_elections``.
FIXED_GAP_COMPARISON_YEARS: dict[str, int] = {ElectionType.us_senate.value: 6}

# Election types that chain to the preceding election of the same type and parliament even
# across a boundary change (a different mapId). US House districts are redrawn each census
# and mid-decade, but every cycle contests the same district keys (``ST-NN``), so a seat is
# compared with the same-numbered district of the previous House election — knowingly
# comparing a redrawn district against its old namesake. See ``assign_comparison_elections``.
CROSS_MAP_COMPARISON_TYPES: frozenset[str] = frozenset({ElectionType.us_house.value})

# Each US forecast entry compares against the same baseline its model swings from, so
# gains / comparison columns read against the result the projection actually moved. These
# are the baselines of the archived 2026 forecasts, which stay listed after the model
# runners (``models/us/run_us_*_model.py``) roll on to 2028. The Senate forecast projected
# the 2026 Class-2 field, last contested in 2020 — hence 2020, not the latest Senate cycle.
US_MODEL_COMPARISON_IDS: dict[str, str] = {
    ElectionType.us_house_model.value: "2024-us-house",
    ElectionType.us_presidential_model.value: "2024-us-president",
    ElectionType.us_senate_model.value: "2020-us-senate",
}

# ``parliamentFeatures`` key whose ``nextElectionYear`` decides which Senate specials are
# live. The Senate model (``models/us/run_us_senate_model.py``) reads the same key from the
# same shell, so the export and the model agree on the contested field.
SENATE_PARLIAMENT_KEY = "us_senate"


def build_manifest_party_settings(parties: Sequence[Party]) -> list[dict[str, Any]]:
    """Build the top-level ``parties`` list for the election-map manifest.

    Each entry contains the DB ``id``, resolved ``key``, display ``name``,
    and ``colour`` for one party, sorted alphabetically by name.

    Args:
        parties: All Party ORM rows to include.

    Returns:
        List of dicts with keys ``id``, ``key``, ``name``, ``colour``,
        sorted by lowercased party name.
    """
    entries: list[dict[str, Any]] = []

    for party in sorted(parties, key=lambda row: row.name.lower()):
        key = party_key_for_party(party)
        entries.append(
            {
                "id": party.id,
                "key": key,
                "name": party.name,
                "colour": party.colour,
            }
        )

    return entries


def build_manifest_regions_by_map_id(regions: Sequence[Region]) -> dict[str, list[dict[str, Any]]]:
    """Group region records by map ID for ``mapModes[mapId].regions``.

    Groups regions by their ``map_id``, sorted within each group by name
    then by primary key.  Keys are string map IDs so the output is valid
    JSON.

    Args:
        regions: All Region ORM rows to include.

    Returns:
        Dict mapping string map ID to a list of ``{"id": int, "name": str}``
        dicts, sorted by ``(map_id, name, id)``.
    """
    regions_by_map_id: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for region in sorted(regions, key=lambda row: (row.map_id, row.name.lower(), row.id)):
        regions_by_map_id[str(region.map_id)].append(
            {
                "id": region.id,
                "name": region.name,
            }
        )

    return dict(regions_by_map_id)


def build_map_modes_with_regions(
    map_modes: dict[str, Any],
    regions_by_map_id: dict[str, list[dict[str, Any]]],
    current_year: int | None = None,
    *,
    parliament_features: dict[str, Any],
) -> dict[str, Any]:
    """Build the ``mapModes`` block, attaching DB-derived regions to each map.

    Regions are always generated from the database (uniform for every map), so the
    shell holds only structural mapMode config — no region lists. A shell mapMode may
    carry an optional ``regionNameOverride`` (a ``{db_name: display_name}`` map) for the
    few regions whose display label is deliberately curated away from the canonical DB
    name (e.g. the shortened Holyrood-2026 labels); it is applied here and stripped from
    the output. A mapMode that still lists ``regions`` keeps them (back-compat).

    Args:
        map_modes: Per-map config keyed by string map id.
        regions_by_map_id: Region lists keyed by string map id, as built by
            :func:`build_manifest_regions_by_map_id`.
        current_year: The export year, which expires past ``senateSpecialElections`` entries
            and floors the Senate ``senateClassCycle`` "next up" years; defaults to the
            current calendar year. Injectable so exports (and tests) can pin a year rather
            than depend on the wall clock.
        parliament_features: The page's ``parliamentFeatures`` block. Its
            ``us_senate.nextElectionYear`` picks the ``senateSpecialElections`` entries
            that are live (see :func:`_live_senate_specials`) and, when later than
            ``current_year``, floors each class's next-up year so a class that has just
            voted rolls on to its following term.

    Returns:
        A new dict keyed by string map id, each value being the mapMode config with a
        ``regions`` list (DB-derived, display names overridden where configured).
    """
    if current_year is None:
        current_year = date.today().year
    special_year = senate_next_election_year(parliament_features)
    # No class is next up before the Senate's own next election: a class that voted in the
    # cycle already held (2026's Class 2, exported late in 2026) must roll on to its following
    # term, not read as "up this year". The floor is the shell's ``nextElectionYear`` (or the
    # export year, if later, so a stale shell still rolls forward).
    cycle_floor = current_year if special_year is None else max(current_year, special_year)
    merged: dict[str, Any] = {}
    for map_id_str, mode in map_modes.items():
        entry = dict(mode)
        overrides: dict[str, str] = entry.pop("regionNameOverride", None) or {}
        if "regions" not in entry:
            entry["regions"] = [
                {"id": region["id"], "name": overrides.get(region["name"], region["name"])}
                for region in regions_by_map_id.get(map_id_str, [])
            ]
        # Resolve the durable Senate-class cycle ({base year per class, period}) into concrete
        # "next up" years as of this export, so the front-end filter stays a simple class→year
        # lookup and the cycle rolls forward on its own without editing the shell.
        cycle = entry.pop("senateClassCycle", None)
        if cycle:
            entry["senateClassNextElection"] = _senate_class_next_election(cycle, cycle_floor)
        # Senate special elections (an off-cycle vacancy contested alongside the regular class,
        # e.g. Ohio and Florida's Class-3 seats in 2026) are filtered here, once, to the ones the
        # next Senate election holds; the front end then uses the list as-is.
        if "senateSpecialElections" in entry:
            entry["senateSpecialElections"] = _live_senate_specials(
                entry["senateSpecialElections"], special_year, current_year
            )
        # Per-seat Senate baseline overrides (a seat that swings from a different election
        # than the rest of the field, e.g. a seat filled at an off-cycle special since the
        # regular class last voted) are valid whatever the cycle, so they are only cleaned.
        if "senateBaselineOverrides" in entry:
            entry["senateBaselineOverrides"] = senate_baseline_overrides(
                entry["senateBaselineOverrides"]
            )
        merged[map_id_str] = entry
    return merged


def _is_int(value: object) -> bool:
    """True for a JSON integer (``bool`` is an ``int`` subclass, so it is excluded)."""
    return isinstance(value, int) and not isinstance(value, bool)


def senate_next_election_year(parliament_features: object) -> int | None:
    """Return ``parliamentFeatures.us_senate.nextElectionYear``, or ``None`` when unset.

    Shared with the Senate model (``models/us/run_us_senate_model.py``), so both read
    the cycle the same way — a non-integer (``2026.0``, ``true``) counts as unset.
    """
    if not isinstance(parliament_features, dict):
        return None
    senate = parliament_features.get(SENATE_PARLIAMENT_KEY)
    if not isinstance(senate, dict):
        return None
    year = senate.get("nextElectionYear")
    return year if _is_int(year) else None


def senate_specials_for_year(entries: object, year: int) -> list[dict[str, Any]]:
    """Keep the well-formed ``senateSpecialElections`` entries held in ``year``.

    The one parser for the shell's specials, shared by the export and the Senate
    model so they cannot disagree about which entries count. An entry is skipped,
    rather than taking its caller down, when it is not an object, or has a
    non-integer ``year`` (``2026.0`` included) or ``class``, or no non-blank string
    ``seat`` or ``baselineElectionId`` — the front end needs all four to find the
    member the special replaces and to load its baseline, and the model needs the
    seat and baseline to project it.

    Args:
        entries: The shell's ``senateSpecialElections`` value; anything but a list
            keeps nothing.
        year: The cycle to keep.

    Returns:
        Copies of the matching entries, in shell order.
    """
    if not isinstance(entries, list):
        return []
    kept: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_year = entry.get("year")
        if not _is_int(entry_year) or entry_year != year or not _is_int(entry.get("class")):
            continue
        if not all(
            isinstance(entry.get(key), str) and entry[key].strip()
            for key in ("seat", "baselineElectionId")
        ):
            continue
        kept.append(dict(entry))
    return kept


def senate_baseline_overrides(entries: object) -> list[dict[str, Any]]:
    """Keep the well-formed ``senateBaselineOverrides`` entries.

    An override names a seat of one Senate ``class`` whose last contest was a different
    election from the rest of its class (``baselineElectionId``): it was filled at an
    off-cycle special, so the class's next cycle swings from that result rather than from
    the class's regular one. The class matters because a state holds seats of more than
    one class, and the override only applies when that class is the one being contested.
    Unlike ``senateSpecialElections`` it carries no ``year`` and is never expired. The
    shared parser for the export and the Senate model.

    An entry is skipped when it is not an object, lacks a non-blank string ``seat`` or
    ``baselineElectionId``, or lacks an integer ``class`` of 1 to 3; a later entry for an
    already-named (seat, class) is skipped too, so a seat has one baseline per class.

    Args:
        entries: The shell's ``senateBaselineOverrides`` value; anything but a list
            keeps nothing.

    Returns:
        Copies of the valid entries, in shell order.
    """
    if not isinstance(entries, list):
        return []
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not all(
            isinstance(entry.get(key), str) and entry[key].strip()
            for key in ("seat", "baselineElectionId")
        ):
            continue
        seat_class: Any = entry.get("class")
        if not _is_int(seat_class) or not 1 <= seat_class <= 3:
            continue
        key: tuple[str, int] = (entry["seat"], int(seat_class))
        if key in seen:
            continue
        seen.add(key)
        kept.append(dict(entry))
    return kept


def senate_class_up_in(cycle: object, year: int) -> int | None:
    """The Senate class a regular election in ``year`` contests, or ``None``.

    Resolves the shell's durable ``senateClassCycle`` (``{"base": {class: year}, "period": n}``)
    with :func:`_senate_class_next_election`, so the model and the export read the cycle
    through one resolver. ``None`` when ``cycle`` is malformed or no class (or more than
    one) is up in exactly ``year``.
    """
    if not isinstance(cycle, dict) or not isinstance(cycle.get("base"), dict):
        return None
    period = cycle.get("period", 6)
    if not _is_int(period) or period <= 0:
        return None
    base = cycle["base"]
    if not all(_is_int(value) and str(key).isdigit() for key, value in base.items()):
        return None
    up = [
        int(seat_class)
        for seat_class, next_year in _senate_class_next_election(cycle, year).items()
        if next_year == year
    ]
    return up[0] if len(up) == 1 else None


def _live_senate_specials(
    entries: object, next_year: int | None, current_year: int
) -> list[dict[str, Any]]:
    """Keep the special elections the next Senate election holds.

    This is the one rule for which specials are live: an entry's ``year`` must equal
    ``parliamentFeatures.us_senate.nextElectionYear``, the same test the Senate model
    applies, so a 2028 special added during the 2026 cycle is neither projected by the
    model nor by front-end Predict. A next-election year that has already passed keeps
    nothing, so a stale shell cannot resurrect a finished cycle.

    Entries are parsed defensively by :func:`senate_specials_for_year`, so a malformed
    one is skipped rather than taking the export down.

    Args:
        entries: The shell's ``senateSpecialElections`` value.
        next_year: The Senate's next election year, or ``None`` when the shell has none.
        current_year: The export year; a ``next_year`` before it keeps nothing.

    Returns:
        Copies of the live entries, in shell order.
    """
    if next_year is None or next_year < current_year:
        return []
    return senate_specials_for_year(entries, next_year)


def _senate_class_next_election(cycle: dict[str, Any], current_year: int) -> dict[str, int]:
    """Resolve each Senate class's next election year from a durable cycle definition.

    Args:
        cycle: ``{"base": {class: base_election_year}, "period": years}`` — each class's fixed
            6-year cycle anchored at a base year (Class 1 = 2018, 2 = 2020, 3 = 2022).
        current_year: The year to resolve "next up" relative to (the export year).

    Returns:
        ``{class: next_election_year}`` (string class keys), where each year is the first
        election year on that class's cycle that is ``>= current_year``.
    """
    base = cycle.get("base", {})
    period = int(cycle.get("period", 6)) or 6
    result: dict[str, int] = {}
    for cls, base_year in base.items():
        base_year = int(base_year)
        steps = max(0, math.ceil((current_year - base_year) / period))
        result[str(cls)] = base_year + steps * period
    return result


def assign_comparison_elections(manifest_entries: list[dict[str, Any]]) -> None:
    """Populate ``comparisonElectionId`` for each manifest entry in-place.

    Rules applied in order:
    - Entries that already have ``comparisonElectionId`` set are skipped.
    - ``model_uns`` entries compare against the most recent UK general
      election in the list.
    - US forecast entries (``us_*_model``) compare against their model's
      baseline election via ``US_MODEL_COMPARISON_IDS`` (when present in the
      list).
    - All other entries compare against the next entry in the list with the
      same ``parliament``, ``mapId`` **and** ``type`` (i.e. the chronologically
      preceding election of the same kind on the same boundaries).  Matching
      ``mapId`` keeps a boundary-changed election comparing against the
      same-boundary baseline (e.g. the 2026 Holyrood election against
      ``2021-holyrood-2026``, not the old-boundary ``2021-holyrood``); matching
      ``type`` stops a general election from comparing against a same-map
      election of another kind (e.g. the EU referendum); and the ``parliament``
      check prevents cross-parliament comparisons.
    - Types in ``CROSS_MAP_COMPARISON_TYPES`` (US House) drop the ``mapId`` match:
      each House election compares against the previous House election whatever its
      district map (2026 on map 24 against 2024 on map 21, 2020 on map 25 against
      2018), seat by seat through the shared district keys. The match must be from
      an earlier year (read from the id), whatever the list order.
    - The last entry of each (parliament, mapId, type) receives no comparison (for
      US House, the oldest House election).
    - Types in ``FIXED_GAP_COMPARISON_YEARS`` (US Senate) compare with the election of
      the same parliament and type exactly that many years earlier (six): the staggered
      classes contest a different third of the states each cycle, so only the same class,
      six years back, is like for like (2026 against 2020, 2024 against 2018). The earlier
      election's year is read from its id; when none is in the list (for example 2022, 2020 and
      2018 while only the latest cycles are imported) the entry gets no comparison, and a later import of the earlier cycle is
      picked up automatically. Entries without a year in their id (``current-senate``)
      get none.

    Idempotent and safe to call more than once: entries that already have a
    comparison are skipped, so a second pass only fills entries left unresolved
    when their same-boundary baseline was not yet present (e.g. a preserved
    boundary-changed election added after the first pass).

    Args:
        manifest_entries: List of manifest election dicts, ordered newest
            first.  Modified in-place.
    """
    latest_general_id = next(
        (entry["id"] for entry in manifest_entries if entry.get("type") == ElectionType.uk_general.value),
        None,
    )
    present_ids = {entry.get("id") for entry in manifest_entries}

    for index, entry in enumerate(manifest_entries):
        # Skip entries that already have a comparison set (e.g. Current Parliament)
        if entry.get("comparisonElectionId"):
            continue

        comparison_id: str | None = None

        if entry.get("type") in FIXED_GAP_COMPARISON_YEARS:
            # Staggered-class chambers (US Senate): the same class last voted a fixed gap
            # earlier. Auto-chaining to the previous cycle would compare unrelated seats.
            entry_year = _entry_year(entry)
            if entry_year is not None:
                earlier_year = entry_year - FIXED_GAP_COMPARISON_YEARS[str(entry.get("type"))]
                candidates = [
                    other
                    for other in manifest_entries
                    if other.get("parliament") == entry.get("parliament")
                    and other.get("type") == entry.get("type")
                    and _entry_year(other) == earlier_year
                ]
                # Two entries can share a year; the plain "<year>-<type slug>" id wins,
                # otherwise the first in list order.
                plain_id = f"{earlier_year}-{str(entry.get('type')).replace('_', '-')}"
                preferred = [other for other in candidates if other.get("id") == plain_id]
                if preferred or candidates:
                    comparison_id = (preferred or candidates)[0]["id"]
        elif entry.get("type") == ElectionType.model_uns.value:
            comparison_id = latest_general_id
        elif entry.get("type") in US_MODEL_COMPARISON_IDS:
            # US forecasts compare against their model's baseline; only when that
            # baseline is actually exported, so no dangling reference is written.
            candidate = US_MODEL_COMPARISON_IDS[str(entry.get("type"))]
            comparison_id = candidate if candidate in present_ids else None
        else:
            parliament = entry.get("parliament")
            map_id = entry.get("mapId")
            entry_type = entry.get("type")
            any_map = entry_type in CROSS_MAP_COMPARISON_TYPES
            entry_year = _entry_year(entry)
            for later_entry in manifest_entries[index + 1:]:
                if (
                    later_entry.get("parliament") == parliament
                    and later_entry.get("type") == entry_type
                    and (
                        later_entry.get("mapId") == map_id
                        if not any_map
                        # Across maps nothing else ties the pair, so only an earlier
                        # year may serve: a hand-reordered manifest must never give
                        # the oldest House election a newer one.
                        else _is_earlier(_entry_year(later_entry), entry_year)
                    )
                ):
                    comparison_id = later_entry["id"]
                    break

        if comparison_id:
            entry["comparisonElectionId"] = comparison_id


def _entry_year(entry: dict[str, Any]) -> int | None:
    """Return the year leading an entry's id (``"2024-us-house"`` → 2024), or ``None``."""
    match = re.match(r"(\d{4})-", str(entry.get("id") or ""))
    return int(match.group(1)) if match else None


def _is_earlier(candidate_year: int | None, year: int | None) -> bool:
    """True when both years are known and ``candidate_year`` comes before ``year``."""
    return candidate_year is not None and year is not None and candidate_year < year


def remove_comparison_for_supplemental_entries(manifest_entries: list[dict[str, Any]]) -> None:
    """Strip ``comparisonElectionId`` from supplemental entries flagged ``noComparison``.

    After ``assign_comparison_elections`` has run, this pass removes the
    comparison field from supplemental legacy elections that have
    ``"noComparison": True`` in ``SUPPLEMENTAL_LEGACY_ELECTIONS``.

    Args:
        manifest_entries: Manifest election list to modify in-place.
    """
    ids_without_comparison = {
        supplemental["id"]
        for supplemental in SUPPLEMENTAL_LEGACY_ELECTIONS
        if supplemental.get("noComparison")
    }
    if not ids_without_comparison:
        return

    for entry in manifest_entries:
        if entry.get("id") in ids_without_comparison:
            entry.pop("comparisonElectionId", None)
