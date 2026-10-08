"""Election manifest positioning rules, independent of payload and file handling."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any


def reorder_manifest_entries(
    entries: list[dict[str, Any]],
    existing_order: Sequence[str],
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
    """Move truthy-model entries before their parliament's first occurrence.

    The election selector lists a parliament's entries in manifest order, and the
    UI convention (established by Westminster's ``current-prediction`` and
    Holyrood's prediction entry) is that the live forecast leads, with the
    Predict / Poll-tracker links anchored right after it. ``reorder_manifest_entries``
    keeps whatever position an entry held in the previous manifest, so a forecast
    that first shipped at the bottom of its block would stay there forever — this
    pass pins forecasts to the front of their parliament's block regardless.

    Preserves the relative order of the forecasts themselves and all other
    entries without regrouping interleaved parliaments. In-place; a no-op
    when forecasts already lead (UK pages).

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


def reposition_supplemental_entries(
    manifest_entries: list[dict[str, Any]],
    supplemental_entries: Sequence[dict[str, Any]],
    *,
    parliaments: set[str] | None = None,
) -> None:
    """Re-apply each supplemental's ``insertBeforeId`` / ``insertAfterId`` position in-place.

    ``reorder_manifest_entries`` sorts entries by the previous manifest's order, which can
    override the position ``apply_supplemental_legacy_elections`` gave a supplemental (e.g.
    a newly-promoted "Current Senate" that should lead its parliament). Running this after
    the reorder restores the configured position. Idempotent.

    Args:
        manifest_entries: Manifest election list to reorder in-place.
        supplemental_entries: Supplemental descriptors containing placement anchors.
        parliaments: Restrict placement to these parliaments, or include all.
    """
    by_id = {entry.get("id"): entry for entry in manifest_entries}
    for supplemental in supplemental_entries:
        if (
            parliaments is not None
            and supplemental.get("parliament", "westminster") not in parliaments
        ):
            continue
        before_id = supplemental.get("insertBeforeId")
        after_id = supplemental.get("insertAfterId")
        entry = by_id.get(supplemental["id"])
        if entry is None or (before_id is None and after_id is None):
            continue
        manifest_entries.remove(entry)
        if before_id is not None:
            index = next(
                (
                    i
                    for i, e in enumerate(manifest_entries)
                    if e.get("id") == before_id
                ),
                len(manifest_entries),
            )
        else:
            index = next(
                (
                    i + 1
                    for i, e in enumerate(manifest_entries)
                    if e.get("id") == after_id
                ),
                len(manifest_entries),
            )
        manifest_entries.insert(index, entry)


def finalize_manifest_order(
    entries: list[dict[str, Any]],
    existing_order: Sequence[str],
    supplemental_entries: Sequence[dict[str, Any]],
    *,
    parliaments: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return final order: remembered positions, supplemental anchors, then forecasts.

    The supplied list is unchanged; the returned list contains the same entry
    dictionaries. Comparison assignment and default selection remain independent
    export stages.
    """
    ordered_entries = reorder_manifest_entries(entries, existing_order)
    reposition_supplemental_entries(
        ordered_entries,
        supplemental_entries,
        parliaments=parliaments,
    )
    float_model_entries_first(ordered_entries)
    return ordered_entries
