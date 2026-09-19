"""map-modes.json manifest construction for the election export."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
import math
from typing import Any, Sequence

from models import Election, ElectionType, Party, Region
from scripts.export.legacy import SUPPLEMENTAL_LEGACY_ELECTIONS
from scripts.export.naming import party_key_for_party

# Election types that must never auto-chain to a comparison election. The US Senate's
# staggered classes contest a different ~third of the states each cycle, so a delta against
# the preceding cycle would compare unrelated seats. See ``assign_comparison_elections``.
NO_AUTO_COMPARISON_TYPES: frozenset[str] = frozenset({ElectionType.us_senate.value})

# Each US forecast entry compares against the same baseline its model swings from
# (``models/us/run_us_*_model.py``), so gains / comparison columns read against the
# result the projection actually moved. The Senate forecast projects the 2026
# Class-2 field, last contested in 2020 — hence 2020, not the latest Senate cycle.
# Bump alongside the model baselines when the cycles roll.
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
    """Build the ``settings.parties`` list for the elections manifest.

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
    """Build the ``settings.regionsByMapId`` dict for the elections manifest.

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
        current_year: Year to resolve Senate ``senateClassCycle`` "next up" years against, and
            to expire past ``senateSpecialElections`` entries at; defaults to the current
            calendar year. Injectable so exports (and tests) can pin a year rather than
            depend on the wall clock.
        parliament_features: The page's ``parliamentFeatures`` block. Its
            ``us_senate.nextElectionYear`` picks the ``senateSpecialElections`` entries
            that are live (see :func:`_live_senate_specials`).

    Returns:
        A new dict keyed by string map id, each value being the mapMode config with a
        ``regions`` list (DB-derived, display names overridden where configured).
    """
    if current_year is None:
        current_year = date.today().year
    special_year = senate_next_election_year(parliament_features)
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
            entry["senateClassNextElection"] = _senate_class_next_election(cycle, current_year)
        # Senate special elections (an off-cycle vacancy contested alongside the regular class,
        # e.g. Ohio and Florida's Class-3 seats in 2026) are filtered here, once, to the ones the
        # next Senate election holds; the front end then uses the list as-is.
        if "senateSpecialElections" in entry:
            entry["senateSpecialElections"] = _live_senate_specials(
                entry["senateSpecialElections"], special_year, current_year
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
    - The last entry of each (parliament, mapId, type) receives no comparison.
    - Types in ``NO_AUTO_COMPARISON_TYPES`` (US Senate) never auto-chain: their
      staggered classes contest different states each cycle, so a delta against
      the preceding cycle would compare unrelated seats.

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

        # Staggered-class chambers (US Senate) contest different states each cycle, so
        # auto-chaining against the previous cycle would compare unrelated seats — skip them.
        if entry.get("type") in NO_AUTO_COMPARISON_TYPES:
            continue

        comparison_id: str | None = None

        if entry.get("type") == ElectionType.model_uns.value:
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
            for later_entry in manifest_entries[index + 1:]:
                if (
                    later_entry.get("parliament") == parliament
                    and later_entry.get("mapId") == map_id
                    and later_entry.get("type") == entry_type
                ):
                    comparison_id = later_entry["id"]
                    break

        if comparison_id:
            entry["comparisonElectionId"] = comparison_id


def reorder_manifest_entries(
    entries: list[dict[str, Any]], existing_order: Sequence[str]
) -> list[dict[str, Any]]:
    """Reorder freshly-built manifest entries to match the curated manifest order.

    The export builds entries in DB/insertion order, which differs from the
    hand-curated order of the existing ``map-modes.json``.  To keep regen diffs
    minimal and the UI election selector stable, entries already present in
    ``existing_order`` keep that order.  A new entry (not in the existing
    manifest, e.g. a freshly-added election) is slotted immediately after the
    existing entry that names it as its ``comparisonElectionId`` (its "newer"
    neighbour); failing that, immediately before the existing entry it itself
    compares against; otherwise appended at the end.

    When ``existing_order`` is empty (no prior manifest), the built order is
    returned unchanged.

    Args:
        entries: Manifest entry dicts in their freshly-built order.
        existing_order: Election ids in the order of the existing manifest.

    Returns:
        A new list of the same entries reordered to follow ``existing_order``.
    """
    if not existing_order:
        return list(entries)

    existing_index = {eid: i for i, eid in enumerate(existing_order)}
    end = len(existing_order)

    # id -> id of the first entry that compares against it (its newer neighbour)
    compared_by: dict[str, str] = {}
    for entry in entries:
        comp = entry.get("comparisonElectionId")
        if comp:
            compared_by.setdefault(comp, entry["id"])

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[float, int, int]:
        built_pos, entry = item
        eid = entry["id"]
        if eid in existing_index:
            return (existing_index[eid], 0, built_pos)
        anchor = compared_by.get(eid)
        if anchor in existing_index:
            # right after the existing entry that compares against this one
            return (existing_index[anchor], 1, built_pos)
        comp = entry.get("comparisonElectionId")
        if comp in existing_index:
            # right before the existing entry this one compares against
            return (existing_index[comp] - 1, 2, built_pos)
        return (end, 0, built_pos)

    return [entry for _, entry in sorted(enumerate(entries), key=sort_key)]


def float_model_entries_first(manifest_entries: list[dict[str, Any]]) -> None:
    """Move each parliament's forecast (``model: true``) entries to the front of its block.

    The election selector lists a parliament's entries in manifest order, and the
    UI convention (established by Westminster's ``current-prediction`` and
    Holyrood's prediction entry) is that the live forecast leads, with the
    Predict / Poll-tracker links anchored right after it. ``reorder_manifest_entries``
    keeps whatever position an entry held in the previous manifest, so a forecast
    that first shipped at the bottom of its block would stay there forever — this
    pass pins forecasts to the front of their parliament's block regardless.

    Preserves the relative order of the forecasts themselves, the order of all
    other entries, and the block layout (entries never cross into another
    parliament's span). In-place; a no-op when forecasts already lead (UK pages).

    Args:
        manifest_entries: Manifest election list to modify in-place.
    """
    models_by_parliament: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in manifest_entries:
        if entry.get("model"):
            models_by_parliament[str(entry.get("parliament"))].append(entry)
    if not models_by_parliament:
        return

    first_index_by_parliament: dict[str, int] = {}
    for index, entry in enumerate(manifest_entries):
        first_index_by_parliament.setdefault(str(entry.get("parliament")), index)

    result: list[dict[str, Any]] = []
    emitted: set[int] = set()
    for index, entry in enumerate(manifest_entries):
        parliament = str(entry.get("parliament"))
        if first_index_by_parliament.get(parliament) == index:
            for model_entry in models_by_parliament.get(parliament, []):
                result.append(model_entry)
                emitted.add(id(model_entry))
        if id(entry) not in emitted:
            result.append(entry)
    manifest_entries[:] = result


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
