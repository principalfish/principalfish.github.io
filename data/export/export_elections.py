#!/usr/bin/env python3
"""Export elections into the per-page static data directories.

The full export partitions elections by parliament and writes each front-end page's data
under its own root (see ``PAGE_OUTPUT_ROOTS`` / ``PAGE_PARLIAMENTS``): UK → ``electionmaps/data``,
US → ``uselectionmaps/data``.

Outputs (per page root):
    - <root>/results/*.json (pf-results-v4 payloads)
        - <root>/maps/*.topo.json (TopoJSON map per map_id)
        - <root>/map-modes.json (manifest, built from that page's map-modes-shell.json)
        - <root>/results/prediction-uncertainty.json (latest forecast's uncertainty)

Page roots are also read: each page's map-modes-shell.json, existing map-modes.json,
maps/ and parent results. ``--output-base DIR`` moves every page root to
``DIR/<page>/data`` (seeded once by copying the repo's page data directory), for a
scratch export that leaves the repository untouched; it works with every mode.
``--output-root DIR`` applies only to ``--current-simulation``.

Usage:
    python data/export/export_elections.py
    python data/export/export_elections.py --dry-run
    python data/export/export_elections.py --output-base /tmp/export-check
    python data/export/export_elections.py --election-name "2019 General Election"
    python data/export/export_elections.py --current-simulation --output-file /tmp/current-simulation.json
    python data/export/export_elections.py --current-simulation --output-root /tmp/uk-data
    python data/export/export_elections.py --metadata-only
    python data/export/export_elections.py --sections house,senate --content results
    python data/export/export_elections.py --content forecast --dry-run

``--sections`` (westminster, holyrood, house, senate, president) and ``--content``
(results, forecast, metadata) narrow a full export; see :class:`ExportScope`.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Collection, Mapping, Sequence

from sqlalchemy import inspect, select
from sqlalchemy.orm import joinedload, Session

from core.db import Database, SeatStatus
from core.electoral_votes import allocation_year, get_electoral_votes
from core.schema import (
    Election,
    ElectionSeatStatus,
    ElectionType,
    Map,
    Party,
    Region,
    Seat,
    Vote,
)
from export.legacy import (
    SUPPLEMENTAL_LEGACY_ELECTIONS,
    SUPPLEMENTAL_LEGACY_ELECTION_NAMES,
    apply_supplemental_legacy_elections,
)
from export.manifest import (
    build_manifest_party_settings,
    build_manifest_regions_by_map_id,
    build_map_modes_with_regions,
    assign_comparison_elections,
    remove_comparison_for_supplemental_entries,
)
from export.naming import (
    choose_map_template_filename,
    map_filename_for_map_id,
    file_stem_for_election,
    is_folded_senate_special,
    manifest_id_for_election,
    manifest_name_for_election,
)
from export.ordering import (
    finalize_manifest_order,
    insert_current_parliament_entry,
    insert_preserved_entry,
)
from export.payload import (
    SenateResults,
    SeatRow,
    add_previous_senate_results,
    build_result_payload,
    build_special_entries,
    compact_votes_to_dict,
    senate_class_by_name,
    senate_results_by_class,
    set_others_party_id,
)
from export.seat_overrides import apply_seat_overrides, load_seat_overrides
from export.serialize import (
    write_json,
    write_manifest,
)
from export.uncertainty import (
    ModelUncertaintyExport,
    latest_model_run,
    link_uncertainty_payload,
    model_run_uncertainty,
    party_names,
    sync_uncertainty_file,
)
from models.support.persistence import load_uncertainty

DEM_PARTY_ID = 20   # Democratic — see uselectionmaps/data/map-modes.json parties[]
REP_PARTY_ID = 21   # Republican

DATA_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DATA_DIR.parent
LEGACY_FILES_DIR_DEFAULT = DATA_DIR / "inputs" / "westminster"

# Each front-end page owns its own data directory + manifest. The full export partitions
# elections by parliament and writes each page's maps/, results/, and map-modes.json under its
# own root (reading that page's hand-authored map-modes-shell.json). Adding a jurisdiction is a
# matter of adding a page here plus its shell.
PAGE_OUTPUT_ROOTS = {
    "electionmaps": REPO_ROOT / "electionmaps" / "data",
    "uselectionmaps": REPO_ROOT / "uselectionmaps" / "data",
}
PAGE_PARLIAMENTS = {
    "electionmaps": {"westminster", "holyrood"},
    "uselectionmaps": {"us_house", "us_senate", "us_presidential"},
}


# Whether the US House and Senate forecast ("2026 Prediction") elections, types
# ``us_house_model`` / ``us_senate_model``, are exported. OFF: they are dropped before any
# page is exported, so no ``current-us-house`` / ``current-us-senate`` manifest entry, no
# ``us-house-forecast.json`` / ``us-senate-forecast.json`` result file is written, and an
# old manifest entry cannot return (the preservation pass only restores UK/Holyrood types).
# The forecast code (naming, comparison ids, the front-end forecast view) is kept dormant;
# set this to True to publish chamber forecasts again, e.g. for the 2028 cycle. The
# presidential forecast (``us_presidential_model``) is independent of this flag.
EXPORT_US_CHAMBER_FORECASTS = False
US_CHAMBER_FORECAST_TYPES = (ElectionType.us_house_model, ElectionType.us_senate_model)


def drop_disabled_forecasts(elections: Sequence[Election]) -> list[Election]:
    """Return ``elections`` without the US House/Senate forecasts unless they are enabled.

    Governed by :data:`EXPORT_US_CHAMBER_FORECASTS`, read at call time. Every other
    election, including the presidential forecast, is kept in its original order.
    """
    if EXPORT_US_CHAMBER_FORECASTS:
        return list(elections)
    return [e for e in elections if e.type not in US_CHAMBER_FORECAST_TYPES]


# The console's section slugs and the ``Map.parliament`` value each one exports. Kept
# here (the console registry is ``console.sections.SECTIONS``) so the export does not
# import the web app; a test pins the two together.
SECTION_PARLIAMENTS: dict[str, str] = {
    "westminster": "westminster",
    "holyrood": "holyrood",
    "house": "us_house",
    "senate": "us_senate",
    "president": "us_presidential",
}
CONTENT_KINDS: tuple[str, ...] = ("results", "forecast", "metadata")

# Election types whose files and manifest entries are the forecast content; every
# other exported election is a past result.
FORECAST_ELECTION_TYPES: frozenset[str] = frozenset(
    election_type.value
    for election_type in (
        ElectionType.model_uns,
        ElectionType.holyrood_uns,
        ElectionType.us_house_model,
        ElectionType.us_presidential_model,
        ElectionType.us_senate_model,
    )
)

_MAP_FILE_NAME = re.compile(r"map-(\d+)\.topo\.json")

# Manifest entry types the preservation pass re-inserts from the existing manifest,
# and the id of the by-election composite. A full export may add these late, after
# its first comparison pass, so a filtered export holds them back (see _added_late).
PRESERVE_ENTRY_TYPES = (
    "holyrood_uns",
    "model_uns",
    "eu_referendum",
    "holyrood_general",
)
COMPOSITE_ENTRY_ID = "current-parliament"


def exportable_forecast_parliaments() -> frozenset[str]:
    """Return the parliaments whose forecast this script writes.

    Westminster's ``model_uns`` run and the presidential forecast are exported;
    the House and Senate forecasts only while :data:`EXPORT_US_CHAMBER_FORECASTS`
    is on (read at call time). Holyrood's forecast is never exported: the Holyrood
    model runner writes ``results/holyrood-prediction.json`` itself and the export
    only keeps its manifest entry from one run to the next.
    """
    parliaments = {"westminster", "us_presidential"}
    if EXPORT_US_CHAMBER_FORECASTS:
        parliaments |= {"us_house", "us_senate"}
    return frozenset(parliaments)


@dataclass(frozen=True)
class ExportScope:
    """Which sections and which content kinds a filtered export covers.

    Content kinds partition what the full export writes. ``results`` is every
    past-election result file, map and manifest entry; ``forecast`` is the model
    elections' files and entries; ``metadata`` is the ``regions`` (and derived
    keys) of the selected sections' map modes, plus ``parties``. The page's
    ``parties`` list is page-wide and is refreshed whenever the manifest is
    written, whichever kinds are selected.
    Forecast content only counts for the parliaments that
    :func:`exportable_forecast_parliaments` lists: the others are reported by
    :meth:`notes` and left alone.

    Attributes:
        sections: Selected section slugs in registry order.
        parliaments: The ``Map.parliament`` values of ``sections``.
        content: Selected content kinds.
        forecast_parliaments: Selected parliaments whose forecast is exportable.
    """

    sections: tuple[str, ...]
    parliaments: frozenset[str]
    content: frozenset[str]
    forecast_parliaments: frozenset[str]

    @property
    def results(self) -> bool:
        """Whether past-election results are selected."""
        return "results" in self.content

    @property
    def metadata(self) -> bool:
        """Whether parties and regions are selected."""
        return "metadata" in self.content

    @property
    def results_parliaments(self) -> frozenset[str]:
        """Parliaments whose past-election results this scope exports."""
        return self.parliaments if self.results else frozenset()

    @property
    def effective_parliaments(self) -> frozenset[str]:
        """Parliaments where the scope does anything (results, metadata or forecast)."""
        if self.results or self.metadata:
            return self.parliaments | self.forecast_parliaments
        return self.forecast_parliaments

    @property
    def exports_elections(self) -> bool:
        """Whether any election file or manifest entry is written, not just metadata."""
        return bool(self.results_parliaments or self.forecast_parliaments)

    @property
    def is_everything(self) -> bool:
        """Whether this scope is the full export (every section, every content kind)."""
        return (
            self.parliaments == frozenset(SECTION_PARLIAMENTS.values())
            and self.content == frozenset(CONTENT_KINDS)
        )

    def covers(self, parliament: str, *, forecast: bool) -> bool:
        """Whether election content of this kind and parliament is in scope."""
        if forecast:
            return parliament in self.forecast_parliaments
        return parliament in self.results_parliaments

    def covers_election(self, election: Election) -> bool:
        """Whether ``election`` is exported under this scope."""
        parliament = getattr(election.map, "parliament", "westminster")
        return self.covers(
            parliament, forecast=election.type.value in FORECAST_ELECTION_TYPES
        )

    def covers_entry(self, entry: Mapping[str, Any]) -> bool:
        """Whether a manifest election entry is rebuilt (not held back) here."""
        is_forecast = entry.get("type") in FORECAST_ELECTION_TYPES or bool(
            entry.get("model")
        )
        return self.covers(
            str(entry.get("parliament", "westminster")), forecast=is_forecast
        )

    def touches_page(self, page_parliaments: Collection[str]) -> bool:
        """Whether the scope does anything on a page with these parliaments."""
        return any(
            parliament in page_parliaments
            for parliament in self.effective_parliaments
        )

    def plan_line(self) -> str:
        """Return the one-line description printed at the start of a filtered run."""
        kinds = [kind for kind in CONTENT_KINDS if kind in self.content]
        return (
            f"Export scope: sections={','.join(self.sections)} "
            f"content={','.join(kinds)}"
        )

    def notes(self) -> list[str]:
        """Return a line per selected forecast the export cannot write."""
        if "forecast" not in self.content:
            return []
        lines: list[str] = []
        for slug in self.sections:
            parliament = SECTION_PARLIAMENTS[slug]
            if parliament in self.forecast_parliaments:
                continue
            if parliament == "holyrood":
                lines.append(
                    "Note: the Holyrood forecast is written by the Holyrood model "
                    "runner (results/holyrood-prediction.json); the export only keeps "
                    "its manifest entry, so there is nothing to export for it."
                )
            else:
                lines.append(
                    f"Note: the {slug} forecast export is switched off "
                    "(EXPORT_US_CHAMBER_FORECASTS); there is nothing to export for it."
                )
        return lines


def build_export_scope(
    sections: Sequence[str] | None, content: Sequence[str] | None
) -> ExportScope:
    """Build the scope for ``--sections`` / ``--content`` values.

    Args:
        sections: Section slugs, or None for every section.
        content: Content kinds, or None for all of them.

    Returns:
        The scope, with slugs in registry order and kinds in
        :data:`CONTENT_KINDS` order.

    Raises:
        ValueError: If a slug or kind is unknown, or a selection is empty.
    """
    chosen_sections = (
        tuple(SECTION_PARLIAMENTS) if sections is None else tuple(sections)
    )
    chosen_content = frozenset(CONTENT_KINDS if content is None else content)
    unknown = [s for s in chosen_sections if s not in SECTION_PARLIAMENTS]
    if unknown:
        raise ValueError(
            f"unknown section {', '.join(unknown)} "
            f"(choose from {', '.join(SECTION_PARLIAMENTS)})"
        )
    unknown = sorted(chosen_content - set(CONTENT_KINDS))
    if unknown:
        raise ValueError(
            f"unknown content {', '.join(unknown)} "
            f"(choose from {', '.join(CONTENT_KINDS)})"
        )
    if not chosen_sections or not chosen_content:
        raise ValueError("the selection is empty")
    ordered = tuple(slug for slug in SECTION_PARLIAMENTS if slug in chosen_sections)
    parliaments = frozenset(SECTION_PARLIAMENTS[slug] for slug in ordered)
    forecast = (
        parliaments & exportable_forecast_parliaments()
        if "forecast" in chosen_content
        else frozenset()
    )
    return ExportScope(ordered, parliaments, chosen_content, forecast)


def _added_late(entry: Mapping[str, Any]) -> bool:
    """Whether a full export may add this manifest entry after its first comparison.

    True for the by-election composite and the preserved types. Holding them back
    from the first pass does no harm: a held-back entry is restored verbatim.
    """
    return entry.get("id") == COMPOSITE_ENTRY_ID or (
        entry.get("type") in PRESERVE_ENTRY_TYPES
    )


def restore_held_back_entries(
    entries: list[dict[str, Any]], held_back: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Put the existing manifest's out-of-scope entries back exactly as they were.

    A filtered export assembles the page with copies of the entries it does not
    rebuild, so ordering and comparison logic sees the whole page. Those copies may
    have been touched on the way; this swaps the originals back in and re-lays them
    in their existing relative order in the slots they occupy.

    Args:
        entries: The assembled page entries.
        held_back: The existing manifest's out-of-scope entries, in existing order.

    Returns:
        A new list with the originals in place of their copies.
    """
    originals = {entry["id"]: entry for entry in held_back}
    restored = [originals.get(entry.get("id"), entry) for entry in entries]
    slots = [i for i, entry in enumerate(restored) if entry.get("id") in originals]
    present = {restored[i]["id"] for i in slots}
    for slot, original in zip(slots, [e for e in held_back if e["id"] in present]):
        restored[slot] = original
    return restored


def scope_map_modes(
    rebuilt: dict[str, Any],
    existing: Mapping[str, Any],
    in_scope_map_ids: Collection[str],
    *,
    refresh_metadata: bool,
) -> dict[str, Any]:
    """Merge rebuilt map modes with an existing manifest's for a filtered export.

    A map outside the scope keeps its existing mode verbatim (and is left out when the
    existing manifest has none). A map in scope takes the rebuilt mode, except that
    without ``refresh_metadata`` its ``regions`` and :data:`DERIVED_MODE_KEYS` stay as
    the existing mode has them. Existing modes the shell no longer lists survive only
    outside the scope, as a full export would drop the others.

    Args:
        rebuilt: Modes from :func:`export.manifest.build_map_modes_with_regions`.
        existing: The existing manifest's ``mapModes``.
        in_scope_map_ids: String map ids of the selected parliaments' maps.
        refresh_metadata: Whether regions and derived keys are being refreshed.

    Returns:
        The merged modes, in the rebuilt order followed by any leftover existing ones.
    """
    merged: dict[str, Any] = {}
    for map_id, mode in rebuilt.items():
        old = existing.get(map_id)
        if map_id not in in_scope_map_ids:
            if old is not None:
                merged[map_id] = old
            continue
        if old is not None and not refresh_metadata:
            mode = dict(mode)
            for key in ("regions", *DERIVED_MODE_KEYS):
                if key in old:
                    mode[key] = old[key]
                else:
                    mode.pop(key, None)
        merged[map_id] = mode
    for map_id, old in existing.items():
        if map_id not in rebuilt and map_id not in in_scope_map_ids:
            merged[map_id] = old
    return merged


# mapMode keys that the shell doesn't hold verbatim: ``build_map_modes_with_regions`` derives
# each one at export time (the Senate class cycle resolved to concrete years; the special
# elections filtered to the ones the next Senate election holds; the per-seat baseline
# overrides cleaned of malformed records). A ``--metadata-only`` refresh therefore has to
# re-copy them from the rebuilt mode — and drop the key when the rebuild no longer produces it,
# so a removed shell entry (or a special whose cycle has passed) disappears from the manifest.
DERIVED_MODE_KEYS: tuple[str, ...] = (
    "senateClassNextElection",
    "senateSpecialElections",
    "senateBaselineOverrides",
)


def refresh_manifest_modes(
    manifest: dict[str, Any],
    rebuilt_modes: dict[str, Any],
    regions_by_map_id: dict[str, list[dict[str, Any]]],
    only_map_ids: Collection[str] | None = None,
) -> None:
    """Refresh an existing manifest's mapModes in place from freshly rebuilt ones.

    This is the ``--metadata-only`` counterpart to a full export: it leaves each mapMode's
    hand-authored config alone and only replaces what the export derives — the DB regions and
    every key in :data:`DERIVED_MODE_KEYS`. A mapMode with no rebuilt counterpart (no shell, or
    the shell no longer lists that map) still gets its raw DB regions when they exist.

    Args:
        manifest: Parsed ``map-modes.json`` for one page. Its ``mapModes`` values are mutated.
        rebuilt_modes: Modes as built by
            :func:`export.manifest.build_map_modes_with_regions` from the page shell,
            keyed by string map id.
        regions_by_map_id: Raw DB region lists keyed by string map id, used as the fallback
            when a mapMode has no rebuilt counterpart.
        only_map_ids: When given, only the mapModes with these string map ids are
            refreshed; the others are left untouched.
    """
    for map_id_str, mode in manifest.get("mapModes", {}).items():
        if only_map_ids is not None and map_id_str not in only_map_ids:
            continue
        rebuilt = rebuilt_modes.get(map_id_str)
        if rebuilt is None:
            if map_id_str in regions_by_map_id:
                mode["regions"] = regions_by_map_id[map_id_str]
            continue
        mode["regions"] = rebuilt["regions"]
        for key in DERIVED_MODE_KEYS:
            if key in rebuilt:
                mode[key] = rebuilt[key]
            else:
                mode.pop(key, None)


def resolve_page_roots(
    output_base: Path | None, pages: Collection[str] | None = None
) -> dict[str, Path]:
    """Return each page's data directory for this export.

    Without ``output_base`` these are :data:`PAGE_OUTPUT_ROOTS` (the repository). With
    it, each page's root is ``output_base/<page>/data``; a root that does not exist yet
    is seeded by copying the page's repository data directory, because the export reads
    the shell, the existing manifest, maps and parent results from the page root. An
    existing scratch root is reused as it is. Seeding happens under ``--dry-run`` too:
    it writes only inside ``output_base``, so the plan reads the same inputs.

    Args:
        output_base: The scratch base directory, or None for the repository.
        pages: The pages to resolve (and seed), or None for every page.

    Returns:
        Page name -> its data directory.
    """
    if output_base is None:
        return {
            page: root
            for page, root in PAGE_OUTPUT_ROOTS.items()
            if pages is None or page in pages
        }
    roots: dict[str, Path] = {}
    for page, source in PAGE_OUTPUT_ROOTS.items():
        if pages is not None and page not in pages:
            continue
        destination = output_base.resolve() / page / "data"
        if not destination.exists():
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                destination.mkdir(parents=True)
            print(f"Seeded scratch page: {source} -> {destination}")
        roots[page] = destination
    return roots


def partition_elections_by_page(elections: Sequence[Election]) -> dict[str, list[Election]]:
    """Group ``elections`` by the front-end page that owns their parliament.

    Routing is driven by :data:`PAGE_PARLIAMENTS`: an election lands in the page whose parliament
    set contains ``election.map.parliament`` (defaulting to ``"westminster"`` for legacy maps with
    no ``parliament`` attribute). Elections whose parliament matches no page are dropped — they
    belong to no exported page. Every :data:`PAGE_PARLIAMENTS` key is present in the result, with
    an empty list when nothing routes to it. The parliament sets are disjoint, so first match wins.
    """
    by_page: dict[str, list[Election]] = {page: [] for page in PAGE_PARLIAMENTS}
    for election in elections:
        parliament = getattr(election.map, "parliament", "westminster")
        for page_name, parliaments in PAGE_PARLIAMENTS.items():
            if parliament in parliaments:
                by_page[page_name].append(election)
                break
    return by_page


def seat_statuses_for_election(session: Session, election_id: int) -> dict[int, SeatStatus]:
    """Return an election's seat statuses keyed by seat id (seats without a row are absent).

    The in-session counterpart of :meth:`core.db.Database.get_seat_statuses`, so the
    export reads statuses in the same session as the votes.
    """
    rows = session.execute(
        select(ElectionSeatStatus).where(ElectionSeatStatus.election_id == election_id)
    ).scalars()
    return {
        row.seat_id: SeatStatus(
            seat_id=row.seat_id,
            called=row.called,
            pct_counted=row.pct_counted,
            same_party=row.same_party,
            decisive_round=row.decisive_round,
            senate_class=row.senate_class,
        )
        for row in rows
    }


def senate_special_entries(
    session: Session, election: Election, seats: list[SeatRow]
) -> list[dict[str, Any]]:
    """Build the ``specials`` list for a regular Senate election from its child specials.

    Children are the ``us_senate`` elections whose ``parent_election_id`` is
    ``election`` (see :func:`export.naming.is_folded_senate_special`). They
    share the parent's map, so ``seats`` (the parent's) serve both.

    Raises:
        RuntimeError: If a child special is on a different map from its parent; its
            seat names would not match the parent's states.
    """
    children = session.execute(
        select(Election)
        .where(
            Election.parent_election_id == election.id,
            Election.type == ElectionType.us_senate,
        )
        .order_by(Election.id)
    ).scalars().all()
    entries: list[dict[str, Any]] = []
    for child in children:
        if child.map_id != election.map_id:
            raise RuntimeError(
                f"Senate special {child.name!r} is on map {child.map_id}, "
                f"but its parent {election.name!r} is on map {election.map_id}"
            )
        child_votes = list(session.execute(
            select(Vote)
            .where(Vote.election_id == child.id)
            .options(joinedload(Vote.party))
        ).scalars().all())
        entries.extend(build_special_entries(
            seats,
            child_votes,
            seat_statuses_for_election(session, child.id),
            election_year=child.year,
        ))
    entries.sort(key=lambda entry: str(entry["n"]))
    return entries


def earlier_senate_results(
    session: Session, election: Election, cache: dict[int, SenateResults]
) -> list[SenateResults]:
    """Return the results of every ``us_senate`` election before ``election``.

    The list is ordered oldest first, so the caller takes the latest match by scanning
    it backwards. It includes folded same-day specials, which are elections of their
    own, but never ``election`` itself or its own child specials. "Before" is strictly
    earlier by election date (the year's 1 January when undated); a child special is
    dated with its parent, so a parent without a date and a child with one cannot
    order each other. Among elections on the same date the regular one sorts last
    (so it is preferred), then the lower id. Each election's results are keyed by
    ``(state, senate_class)`` (see :func:`export.payload.senate_results_by_class`);
    ``cache`` memoises them by election id across calls.
    """
    all_rows = {
        row.id: row
        for row in session.execute(
            select(Election).where(Election.type == ElectionType.us_senate)
        ).scalars()
    }

    def when(row: Election) -> date:
        parent = all_rows.get(row.parent_election_id) if row.parent_election_id is not None else None
        dated = parent if parent is not None else row
        return dated.election_date or date(dated.year, 1, 1)

    cutoff = when(election)
    rows = [
        row
        for row in all_rows.values()
        if row.id != election.id and row.parent_election_id != election.id and when(row) < cutoff
    ]
    rows.sort(key=lambda row: (when(row), row.parent_election_id is None, -row.id))
    results: list[SenateResults] = []
    for row in rows:
        if row.id not in cache:
            seats = [
                SeatRow(seat_id=seat.id, seat_name=seat.seat_name, region_id=seat.region_id,
                        region_name=None, electorate=None)
                for seat in session.execute(select(Seat).where(Seat.map_id == row.map_id)).scalars()
            ]
            votes = list(session.execute(
                select(Vote).where(Vote.election_id == row.id).options(joinedload(Vote.party))
            ).scalars().all())
            statuses = seat_statuses_for_election(session, row.id)
            payload = build_result_payload(seats, votes, election_year=row.year, statuses=statuses)
            cache[row.id] = senate_results_by_class(payload["seats"], senate_class_by_name(seats, statuses))
        results.append(cache[row.id])
    return results


def refresh_manifest_metadata(
    page_roots: Mapping[str, Path],
    *,
    dry_run: bool,
    scope: ExportScope | None = None,
) -> None:
    """Refresh ``parties`` and per-map ``regions`` in every existing page manifest.

    Each page's manifest carries the full party lookup (results may reference any
    party id) and only the mapModes for that page, so the regions refresh limits
    itself to the page's maps. With ``scope``, ``parties`` is still refreshed whole
    (it is page-wide), but regions and derived keys change only for the maps of the
    scope's parliaments, and pages with none of them are skipped.

    Args:
        page_roots: Page name -> data directory.
        dry_run: Print the plan instead of writing.
        scope: The sections to refresh, or None for every map on every page.

    Raises:
        FileNotFoundError: If no page has a ``map-modes.json``.
    """
    db = Database()
    with db.session() as session:
        parties = session.execute(select(Party)).scalars().all()
        regions = session.execute(select(Region)).scalars().all()
        map_parliaments = {
            str(map_id): parliament
            for map_id, parliament in session.execute(select(Map.id, Map.parliament))
        }
    manifest_parties = build_manifest_party_settings(parties)
    manifest_regions_by_map_id = build_manifest_regions_by_map_id(regions)
    wrote_any = False
    for page_name, page_root in page_roots.items():
        only_map_ids: set[str] | None = None
        if scope is not None:
            if not scope.touches_page(PAGE_PARLIAMENTS[page_name]):
                continue
            only_map_ids = {
                map_id
                for map_id, parliament in map_parliaments.items()
                if parliament in scope.parliaments
            }
        manifest_path = page_root / "map-modes.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["parties"] = manifest_parties
        # Rebuild regions the way a full export does so the two paths stay consistent:
        # apply each map's shell ``regionNameOverride``, re-resolve the durable
        # ``senateClassCycle`` and re-filter ``senateSpecialElections``. Without this, a
        # metadata-only refresh silently regressed curated labels (e.g. the shortened
        # Holyrood-2026 names) to raw DB names and left the Senate's derived keys stale.
        # Falls back to raw regions with no shell.
        shell_path = page_root / "map-modes-shell.json"
        shell = (
            json.loads(shell_path.read_text(encoding="utf-8"))
            if shell_path.exists()
            else {}
        )
        rebuilt_modes = build_map_modes_with_regions(
            shell.get("mapModes", {}),
            manifest_regions_by_map_id,
            parliament_features=shell.get("parliamentFeatures", {}),
        )
        refresh_manifest_modes(
            manifest, rebuilt_modes, manifest_regions_by_map_id, only_map_ids
        )
        if dry_run:
            print(f"Would write manifest metadata: {manifest_path}")
            refreshed_maps = (
                len(manifest_regions_by_map_id)
                if only_map_ids is None
                else len(only_map_ids & manifest_regions_by_map_id.keys())
            )
            print(f"parties={len(manifest_parties)} maps={refreshed_maps}")
        else:
            write_manifest(manifest_path, manifest)
            print(f"Wrote manifest metadata: {manifest_path}")
        wrote_any = True
    if not wrote_any:
        roots = [str(root) for root in page_roots.values()]
        raise FileNotFoundError(f"No map-modes.json found under any page root: {roots}")


def parse_args() -> argparse.Namespace:
    """Parse and validate command-line arguments.

    Mutually exclusive target flags:
    - ``--election-name NAME``: export only the named election.
    - ``--current-simulation``: export only the latest ``model_uns`` election.
    - ``--metadata-only``: refresh only ``parties`` and per-map ``regions``
      in the existing ``map-modes.json``.

    Other flags:
    - ``--output-base DIR``: write every page under ``DIR/<page>/data`` instead of
      the repository (see :func:`resolve_page_roots`). Works with every mode.
    - ``--output-root DIR``: ``--current-simulation`` only: the UK page data
      directory to write ``results/prediction-simulation.json`` (and the uncertainty
      file) under; defaults to ``electionmaps/data``. Mutually exclusive with
      ``--output-base``.
    - ``--legacy-files-dir PATH``: override the directory containing legacy
      map template files.
    - ``--dry-run``: print planned actions without writing any files.
    - ``--output-file PATH``: write a single election result payload to this
      path; requires ``--election-name`` or ``--current-simulation``.
    - ``--sections A,B`` / ``--content X,Y``: narrow the full export to some
      sections and/or content kinds (see :class:`ExportScope`); stored on the
      namespace as lists, or None when not given. Not combinable with the
      single-target flags above or ``--output-file``.

    Returns:
        Parsed ``argparse.Namespace`` object.

    Raises:
        SystemExit: If ``--output-file`` is given without ``--election-name``
            or ``--current-simulation``, or ``--output-root`` without
            ``--current-simulation``, or both ``--output-root`` and
            ``--output-base``, or ``--sections`` / ``--content`` with a
            single-target flag or ``--output-file``, an unknown name or an
            empty selection.
    """
    parser = argparse.ArgumentParser(description="Export non-simulation elections to electionmaps/data")
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--election-name",
        type=str,
        default=None,
        help="Export only the election with this exact name",
    )
    target_group.add_argument(
        "--current-simulation",
        action="store_true",
        help="Export only the latest model_uns election",
    )
    target_group.add_argument(
        "--metadata-only",
        action="store_true",
        help="Update only parties and per-map regions in map-modes.json",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help=(
            "With --current-simulation only: the UK page data directory to write "
            "results/prediction-simulation.json under (default electionmaps/data). "
            "For a scratch export of every page, use --output-base."
        ),
    )
    output_group.add_argument(
        "--output-base",
        type=Path,
        default=None,
        help=(
            "Write every page under DIR/<page>/data (electionmaps, uselectionmaps) "
            "instead of the repository, seeding each from the repository's page data "
            "directory if it does not exist yet. Works with every mode."
        ),
    )
    parser.add_argument(
        "--legacy-files-dir",
        type=Path,
        default=LEGACY_FILES_DIR_DEFAULT,
        help="Directory containing legacy map templates (650map.json and 650map_new.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing files",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Write a single exported election payload to this JSON file",
    )
    parser.add_argument(
        "--sections",
        default=None,
        metavar="A,B",
        help=(
            "Comma-separated sections to export (" + ", ".join(SECTION_PARLIAMENTS)
            + "); the others' files and manifest entries are left as they are"
        ),
    )
    parser.add_argument(
        "--content",
        default=None,
        metavar="X,Y",
        help=(
            "Comma-separated content to export (" + ", ".join(CONTENT_KINDS)
            + "); the other kinds' files and manifest entries are left as they are"
        ),
    )

    args = parser.parse_args()

    for flag, attribute, choices in (
        ("--sections", "sections", tuple(SECTION_PARLIAMENTS)),
        ("--content", "content", CONTENT_KINDS),
    ):
        raw = getattr(args, attribute)
        if raw is None:
            continue
        if (
            args.election_name
            or args.current_simulation
            or args.metadata_only
            or args.output_file
        ):
            parser.error(
                f"{flag} cannot be combined with --election-name, "
                "--current-simulation, --metadata-only or --output-file"
            )
        names = [name.strip() for name in raw.split(",") if name.strip()]
        if not names:
            parser.error(
                f"{flag} selected nothing (choose from {', '.join(choices)})"
            )
        unknown = [name for name in names if name not in choices]
        if unknown:
            parser.error(
                f"{flag}: unknown {', '.join(unknown)} "
                f"(choose from {', '.join(choices)})"
            )
        setattr(args, attribute, list(dict.fromkeys(names)))

    if args.output_file and not (args.election_name or args.current_simulation):
        parser.error("--output-file requires --election-name or --current-simulation")
    if args.output_root is not None and not args.current_simulation:
        parser.error(
            "--output-root applies only to --current-simulation; use --output-base DIR "
            "to write a full export (every page) under DIR instead of the repository"
        )

    return args


def main() -> None:
    """Entry point: export elections from the DB to electionmaps static data files.

    Reads all relevant elections (or a subset selected by CLI flags) from the
    database, writes per-election ``pf-results-v4`` JSON files under
    ``electionmaps/data/results/``, copies map TopoJSON templates to
    ``electionmaps/data/maps/``, builds the composite ``current-parliament``
    result by folding in by-election changes, and writes the top-level
    ``electionmaps/data/map-modes.json`` manifest.

    Behaviour is controlled by parsed CLI arguments (see ``parse_args``).
    Exits without writing any files when ``--dry-run`` is set.

    Raises:
        FileNotFoundError: If a required pre-built map, map template or legacy
            source file is missing (checked before anything is written).
        RuntimeError: If the ``"Others"`` party is absent from the DB, a
            required election is not found, ``--output-file`` is used
            with more than one matching election, or ``--election-name``
            names a folded Senate special (export its parent instead).
    """
    args = parse_args()
    single_election_mode = bool(args.election_name or args.current_simulation)
    # A filtered run narrows the full export. Selecting everything is the full export,
    # so that case takes the unfiltered path (scope None) and writes the same files.
    # (getattr: callers that build the namespace by hand predate the two flags.)
    sections = getattr(args, "sections", None)
    content = getattr(args, "content", None)
    scope: ExportScope | None = None
    if sections is not None or content is not None:
        requested = build_export_scope(sections, content)
        print(requested.plan_line())
        if not requested.is_everything:
            scope = requested
            for note in scope.notes():
                print(note)
            if not scope.effective_parliaments:
                print("Nothing to export for this selection.")
                return
    page_roots = resolve_page_roots(
        args.output_base,
        None
        if scope is None
        else [
            page
            for page, page_parliaments in PAGE_PARLIAMENTS.items()
            if scope.touches_page(page_parliaments)
        ],
    )

    if args.metadata_only:
        refresh_manifest_metadata(page_roots, dry_run=args.dry_run)
        return
    if scope is not None and not scope.exports_elections:
        refresh_manifest_metadata(page_roots, dry_run=args.dry_run, scope=scope)
        return

    db = Database()
    database_path = Path(db.config.database_path)
    others_party = db.get_party_by_name("Others")
    if others_party is None:
        raise RuntimeError("'Others' party not found in DB — run import_parties.py first")
    set_others_party_id(others_party.id)
    seat_columns = {column["name"] for column in inspect(db.engine).get_columns("seats")}
    has_electorate = "electorate" in seat_columns

    with db.session() as session:
        parties = session.execute(select(Party)).scalars().all()
        regions = session.execute(select(Region)).scalars().all()
        # The whole seats' last tie-break, from the same source as the console preview.
        tie_break_names = party_names(session)
        manifest_parties = build_manifest_party_settings(parties)
        manifest_regions_by_map_id = build_manifest_regions_by_map_id(regions)

        if args.election_name:
            elections = session.execute(
                select(Election)
                .where(Election.name == args.election_name)
                .options(joinedload(Election.map))
            ).scalars().all()
            if not elections:
                raise RuntimeError(f"Election not found: {args.election_name}")
        elif args.current_simulation:
            # Export the latest model_uns election (the current prediction).
            sim_election = latest_model_run(session)
            if sim_election is None:
                raise RuntimeError("No model_uns elections found for --current-simulation")

            map_id = sim_election.map_id

            if has_electorate:
                seat_rows = session.execute(
                    select(Seat.id, Seat.seat_name, Region.id, Region.name, Seat.electorate)
                    .outerjoin(Region, Region.id == Seat.region_id)
                    .where(Seat.map_id == map_id)
                    .order_by(Seat.seat_name)
                ).all()
            else:
                seat_rows = session.execute(  # type: ignore[assignment]
                    select(Seat.id, Seat.seat_name, Region.id, Region.name)
                    .outerjoin(Region, Region.id == Seat.region_id)
                    .where(Seat.map_id == map_id)
                    .order_by(Seat.seat_name)
                ).all()

            seats = [
                SeatRow(
                    seat_id=row[0],
                    seat_name=row[1],
                    region_id=row[2],
                    region_name=row[3],
                    electorate=(row[4] if has_electorate else None),
                )
                for row in seat_rows
            ]

            votes = list(session.execute(
                select(Vote)
                .where(Vote.election_id == sim_election.id)
                .options(joinedload(Vote.party))
            ).scalars().all())
            published = model_run_uncertainty(
                sim_election,
                load_uncertainty(database_path, sim_election.id),
                tie_break_names,
            )
            result_payload = build_result_payload(
                seats,
                votes,
                election_year=sim_election.year,
                seat_uncertainty=published.seat_bands,
            )
            uncertainty_document = link_uncertainty_payload(
                result_payload, published.document
            )

            if args.output_file:
                output_file = args.output_file.resolve()
                seat_count = len(result_payload.get("seats", []))
                if args.dry_run:
                    print(f"Would write simulation payload: {output_file} ({seat_count} seats)")
                else:
                    write_json(output_file, result_payload)
                    print(f"Wrote simulation payload: {output_file} ({seat_count} seats from election '{sim_election.name}')")
            else:
                # Write to default results dir
                # --output-root and --output-base are exclusive; without either this
                # is the repository's UK page, electionmaps/data.
                uk_root = args.output_root or page_roots["electionmaps"]
                result_path = uk_root.resolve() / "results" / "prediction-simulation.json"
                seat_count = len(result_payload.get("seats", []))
                if args.dry_run:
                    print(f"Would write simulation: {result_path} ({seat_count} seats)")
                else:
                    write_json(result_path, result_payload)
                    print(f"Wrote simulation: {result_path} ({seat_count} seats from election '{sim_election.name}')")
                sync_uncertainty_file(
                    result_path.parent, uncertainty_document, dry_run=args.dry_run
                )
            return
        else:
            elections = session.execute(
                select(Election)
                .where(Election.type.in_([
                    ElectionType.uk_general,
                    ElectionType.by_election,
                    ElectionType.holyrood_general,
                    ElectionType.us_house,
                    ElectionType.us_presidential,
                    ElectionType.us_senate,
                ]))
                .options(joinedload(Election.map))
                .order_by(Election.year.desc(), Election.name.asc())
            ).scalars().all()

            elections = [
                election
                for election in elections
                if str(election.name or "").strip().lower() not in SUPPLEMENTAL_LEGACY_ELECTION_NAMES
            ]

            if not elections:
                raise RuntimeError("No non-simulation elections found (uk_general/by_election/holyrood_general)")

            latest_simulation = latest_model_run(session)
            if latest_simulation is not None:
                elections = [latest_simulation, *elections]

            # Prepend the latest forecast run for each US election type. Like the
            # UK model_uns run, only the most recent run per type ships as the
            # "current forecast" anchor; earlier runs stay in the DB for the
            # poll-tracker trend history only.
            # The House and Senate forecasts are skipped here while
            # EXPORT_US_CHAMBER_FORECASTS is off, to save their queries; export_pages
            # drops them again, which covers the other ways elections are selected.
            latest_us_models: list[Election] = []
            for _us_model_type in (
                ElectionType.us_house_model,
                ElectionType.us_presidential_model,
                ElectionType.us_senate_model,
            ):
                if (
                    _us_model_type in US_CHAMBER_FORECAST_TYPES
                    and not EXPORT_US_CHAMBER_FORECASTS
                ):
                    continue
                latest_us_model = session.execute(
                    select(Election)
                    .where(Election.type == _us_model_type)
                    .options(joinedload(Election.map))
                    .order_by(Election.id.desc())
                    .limit(1)
                ).scalars().first()
                if latest_us_model is not None:
                    latest_us_models.append(latest_us_model)
            if latest_us_models:
                elections = [*latest_us_models, *elections]

        if scope is not None:
            elections = [
                election for election in elections if scope.covers_election(election)
            ]

        if args.output_file and len(elections) != 1:
            raise RuntimeError("--output-file supports exactly one target election")

        # The exported model_uns run (at most one: the latest, or a named run)
        # publishes its stored uncertainty alongside its result file. Built once,
        # before any file is written, so a malformed summary writes nothing.
        model_run = next(
            (election for election in elections if election.type == ElectionType.model_uns),
            None,
        )
        model_uncertainty = (
            model_run_uncertainty(
                model_run,
                load_uncertainty(database_path, model_run.id),
                tie_break_names,
            )
            if model_run is not None
            else None
        )

        export_pages(
            session=session,
            elections=elections,
            page_roots=page_roots,
            args=args,
            manifest_parties=manifest_parties,
            manifest_regions_by_map_id=manifest_regions_by_map_id,
            has_electorate=has_electorate,
            single_election_mode=single_election_mode,
            model_uncertainty=model_uncertainty,
            scope=scope,
        )


def missing_export_inputs(
    elections_by_page: Mapping[str, Sequence[Election]],
    page_roots: Mapping[str, Path],
    legacy_files_dir: Path,
    *,
    single_election_mode: bool,
    scope: ExportScope | None = None,
) -> list[str]:
    """List every input the page exports need on disk but cannot find.

    Mirrors the checks :func:`_export_page` and
    :func:`export.legacy.apply_supplemental_legacy_elections` make as they go,
    so a missing input is reported before anything is written rather than after one
    page and part of another are already out:

    - a non-Westminster map's pre-built ``maps/map-{id}.topo.json`` under its page root;
    - a Westminster map's legacy template under ``legacy_files_dir``;
    - in a full export, each supplemental entry's prebuilt result file or legacy
      source file.

    Folded Senate specials and by-elections write no file of their own, so they are
    skipped. Elections with no map are left to the export's own error.

    Args:
        elections_by_page: Elections per page, as :func:`partition_elections_by_page` returns.
        page_roots: Output root per page to check; pages not listed are skipped.
        legacy_files_dir: Directory holding the Westminster templates and legacy sources.
        single_election_mode: True for ``--election-name``, which writes no supplementals.
        scope: The filtered export's scope; supplementals are checked only for the
            parliaments whose results it exports. None checks them all.

    Returns:
        One description per missing file, empty when nothing is missing: map files
        first (``"pre-built map <path> (needed by 'A', 'B')"`` or ``"map template
        <path> (needed by …)"``), then ``"supplemental '<id>' file <path>"``.
    """
    # Each missing map file is reported once, naming every election that needs it.
    needed_by: dict[str, list[str]] = {}
    supplementals: list[str] = []
    for page_name, page_root in page_roots.items():
        for election in elections_by_page.get(page_name, []):
            map_row = election.map
            if (
                map_row is None
                or is_folded_senate_special(election)
                or election.type == ElectionType.by_election
            ):
                continue
            if getattr(map_row, "parliament", "westminster") != "westminster":
                path = page_root / "maps" / map_filename_for_map_id(election.map_id)
                kind = "pre-built map"
            else:
                path = legacy_files_dir / choose_map_template_filename(map_row)
                kind = "map template"
            if not path.exists():
                needed_by.setdefault(f"{kind} {path}", []).append(repr(election.name))
        if single_election_mode:
            continue
        for supplemental in SUPPLEMENTAL_LEGACY_ELECTIONS:
            if supplemental.get("parliament", "westminster") not in PAGE_PARLIAMENTS[page_name]:
                continue
            if scope is not None and not scope.covers(
                supplemental.get("parliament", "westminster"), forecast=False
            ):
                continue
            if supplemental.get("prebuilt"):
                source_path = page_root / "results" / str(supplemental["resultFile"])
            elif supplemental.get("sourceFile"):
                source_path = legacy_files_dir / str(supplemental["sourceFile"])
            else:
                continue  # A config slip the supplemental pass reports itself.
            if not source_path.exists():
                supplementals.append(f"supplemental {supplemental['id']!r} file {source_path}")
    maps = [f"{item} (needed by {', '.join(names)})" for item, names in needed_by.items()]
    return maps + supplementals


def export_pages(
    session: Session,
    elections: Sequence[Election],
    page_roots: Mapping[str, Path],
    args: argparse.Namespace,
    manifest_parties: list[dict[str, Any]],
    manifest_regions_by_map_id: dict[str, list[dict[str, Any]]],
    has_electorate: bool,
    single_election_mode: bool,
    model_uncertainty: ModelUncertaintyExport | None = None,
    scope: ExportScope | None = None,
) -> None:
    """Check every page's inputs up front, then export each page in ``page_roots``.

    ``main`` passes :func:`resolve_page_roots`'s result, so every page is exported; tests
    pass temp roots. ``model_uncertainty`` goes to every page: see :func:`_export_page`.
    ``scope`` makes the export a filtered one: ``elections`` must already be the
    in-scope ones, and pages the scope does nothing on are not touched (see
    :class:`ExportScope`).

    Raises:
        RuntimeError: If a single-election export names a folded Senate special,
            which has no result file of its own.
        FileNotFoundError: On a real (non-dry-run) export, when
            :func:`missing_export_inputs` finds anything missing; nothing has been
            written. A dry run prints the missing files instead and carries on.
    """
    # Switched-off chamber forecasts are dropped here, so every path that selects elections
    # (full export, --election-name, tests) skips them alike.
    kept = drop_disabled_forecasts(elections)
    for dropped in (e for e in elections if e not in kept):
        print(f"Skipping {dropped.name!r}: US chamber forecast export is switched off")
    elections = kept

    if single_election_mode:
        folded = [election.name for election in elections if is_folded_senate_special(election)]
        if folded:
            raise RuntimeError(
                f"{', '.join(repr(name) for name in folded)} is a folded Senate special, "
                "exported inside its parent's results; export its parent election instead"
            )

    elections_by_page = partition_elections_by_page(elections)
    if not args.output_file:
        missing = missing_export_inputs(
            elections_by_page,
            page_roots,
            args.legacy_files_dir,
            single_election_mode=single_election_mode,
            scope=scope,
        )
        if missing and not args.dry_run:
            raise FileNotFoundError(
                "Export inputs missing; nothing was written:\n  " + "\n  ".join(missing)
            )
        for item in missing:
            print(f"Missing (a real export would fail): {item}")

    for page_name, page_root in page_roots.items():
        if scope is not None and not scope.touches_page(PAGE_PARLIAMENTS[page_name]):
            continue
        _export_page(
            session=session,
            elections=elections_by_page[page_name],
            output_root=page_root,
            parliaments=PAGE_PARLIAMENTS[page_name],
            args=args,
            manifest_parties=manifest_parties,
            manifest_regions_by_map_id=manifest_regions_by_map_id,
            has_electorate=has_electorate,
            single_election_mode=single_election_mode,
            model_uncertainty=model_uncertainty,
            scope=scope,
        )


def _export_page(
    session: Session,
    elections: Sequence[Election],
    output_root: Path,
    parliaments: set[str],
    args: argparse.Namespace,
    manifest_parties: list[dict[str, Any]],
    manifest_regions_by_map_id: dict[str, list[dict[str, Any]]],
    has_electorate: bool,
    single_election_mode: bool,
    model_uncertainty: ModelUncertaintyExport | None = None,
    scope: ExportScope | None = None,
) -> None:
    """Build and write one page's results, maps, and map-modes.json manifest.

    Runs the per-election export + manifest assembly for the subset of ``elections`` whose
    parliament belongs to this page, writing under ``output_root`` and reading that root's
    ``map-modes-shell.json``. Called once per page by :func:`main`.

    ``model_uncertainty`` is what the exported ``model_uns`` run publishes (``main``
    exports at most one): its seat bands add ``u`` rows to that run's result file, and
    its document is written to ``results/prediction-uncertainty.json`` beside it. When
    it has no document, a stale uncertainty file is removed instead (see
    :func:`export.uncertainty.sync_uncertainty_file`). None leaves the model
    run's file without ``u`` and removes a stale uncertainty file.

    ``scope`` (None for the full export) makes this a filtered export of a page that
    may be shared with sections outside it. ``elections`` are then only the in-scope
    ones. The page is assembled as a full export would be, with copies of the
    existing manifest's out-of-scope entries (and their file references) standing in
    for the elections that were not exported, so comparison and ordering rules see
    the whole page; those entries are then restored exactly as the existing manifest
    has them, as are the map modes of maps outside the scope. Result and map files of
    out-of-scope sections are never written or removed. ``files.elections.mapsById``
    is pruned only of stale maps of the selected sections (maps their results own
    that were not rebuilt and that no held-back entry uses). The in-scope maps'
    ``regions`` (with
    :data:`DERIVED_MODE_KEYS`) change only when the scope selects metadata. The
    page-wide ``parties`` list is refreshed on every manifest write, because a result
    file may reference a party the old list lacks. A page without a manifest yet is
    written with just what the scope exports.
    """
    maps_dir = output_root / "maps"
    results_dir = output_root / "results"

    manifest_entries: list[dict[str, Any]] = []
    state_trends: dict[str, list[dict[str, int | float]]] = {}
    default_election_id: str | None = None
    map_files_by_id: dict[str, str] = {}
    data_files_by_election_id: dict[str, str] = {}
    written_map_ids: set[int] = set()
    manifest_id_by_db_id: dict[int, str] = {}
    pending_by_election_rows: list[dict[str, Any]] = []
    senate_results_cache: dict[int, SenateResults] = {}
    seat_overrides = load_seat_overrides()

    for election in elections:
        if is_folded_senate_special(election):
            # Exported inside its parent's payload (``specials``) when the parent is;
            # export_pages refuses it as a single-election target.
            print(f"Folding {election.name!r} into its parent election's results")
            continue

        map_row = election.map
        if map_row is None:
            raise RuntimeError(f"Election {election.id} has no map")

        # Electorate is optional in older seat schemas.
        columns: list[Any] = [Seat.id, Seat.seat_name, Region.id, Region.name]
        electorate_idx = None
        if has_electorate:
            electorate_idx = len(columns)
            columns.append(Seat.electorate)

        seat_rows = session.execute(
            select(*columns)
            .outerjoin(Region, Region.id == Seat.region_id)
            .where(Seat.map_id == election.map_id)
            .order_by(Seat.seat_name)
        ).all()

        seats = [
            SeatRow(
                seat_id=row[0],
                seat_name=row[1],
                region_id=row[2],
                region_name=row[3],
                electorate=(row[electorate_idx] if electorate_idx is not None else None),
            )
            for row in seat_rows
        ]

        votes = list(session.execute(
            select(Vote)
            .where(Vote.election_id == election.id)
            .options(joinedload(Vote.party))
        ).scalars().all())

        # For Holyrood general elections, also fetch votes from the linked list election
        # so that all 129 seats (73 constituency + 56 list) appear in one results file.
        if election.type == ElectionType.holyrood_general:
            list_election = session.execute(
                select(Election)
                .where(
                    Election.parent_election_id == election.id,
                    Election.type == ElectionType.holyrood_list,
                )
                .options(joinedload(Election.map))
            ).scalars().first()
            if list_election is not None:
                list_votes = session.execute(
                    select(Vote)
                    .where(Vote.election_id == list_election.id)
                    .options(joinedload(Vote.party))
                ).scalars().all()
                votes = votes + list(list_votes)

        stem = file_stem_for_election(election)
        result_filename = f"{stem}.json"
        map_filename = map_filename_for_map_id(election.map_id)
        election_manifest_id = manifest_id_for_election(election)

        ev_by_unit: dict[str, int] | None = None
        if election.type in {
            ElectionType.us_presidential,
            ElectionType.us_presidential_model,
        }:
            ev_by_unit = get_electoral_votes(
                session,
                allocation_year(
                    election.type.value, election.year, election.target_election_year
                ),
                (seat.seat_name for seat in seats),
            )
        published: ModelUncertaintyExport | None = None
        if election.type == ElectionType.model_uns:
            published = model_uncertainty or ModelUncertaintyExport(None, None)
        election_statuses = seat_statuses_for_election(session, election.id)
        result_payload = build_result_payload(
            seats,
            votes,
            election_year=election.year,
            ev_by_unit=ev_by_unit,
            seat_uncertainty=published.seat_bands if published is not None else None,
            statuses=election_statuses,
        )
        apply_seat_overrides(
            result_payload,
            election.name,
            {party["key"]: party["id"] for party in manifest_parties},
            seat_overrides,
        )
        if election.type == ElectionType.us_senate:
            specials = senate_special_entries(session, election, seats)
            add_previous_senate_results(
                result_payload["seats"],
                specials,
                senate_class_by_name(seats, election_statuses),
                earlier_senate_results(session, election, senate_results_cache),
            )
            if specials:
                result_payload["specials"] = specials
        # Last, because its fingerprint covers the finished public results.
        uncertainty_document = link_uncertainty_payload(
            result_payload, published.document if published is not None else None
        )

        if election.type == ElectionType.us_presidential:
            for seat_row in result_payload["seats"]:
                v20 = 0.0
                v21 = 0.0
                for prow in seat_row["p"]:
                    if prow[0] == DEM_PARTY_ID:
                        v20 = float(prow[1])
                    elif prow[0] == REP_PARTY_ID:
                        v21 = float(prow[1])
                two_party = v20 + v21
                if two_party <= 0:
                    continue
                state_trends.setdefault(seat_row["n"], []).append(
                    {
                        "year": int(election.year),
                        "dem": round(v20 / two_party * 100.0, 2),
                        "rep": round(v21 / two_party * 100.0, 2),
                    }
                )

        if args.output_file:
            output_file = args.output_file.resolve()
            seat_count = len(result_payload.get("seats", []))
            if args.dry_run:
                print(f"Would write single result payload: {output_file} ({seat_count} seats)")
            else:
                write_json(output_file, result_payload)
                print(f"Wrote single result payload: {output_file} ({seat_count} seats)")
            continue

        # By-elections: collect seat changes only; they are folded into current-parliament.json
        if election.type == ElectionType.by_election:
            manifest_id_by_db_id[election.id] = election_manifest_id
            changes = [
                {
                    "seat": seat_row.get("n"),
                    "winner": seat_row.get("w"),
                    "votes": compact_votes_to_dict(seat_row.get("p", [])),
                }
                for seat_row in result_payload.get("seats", [])
                if seat_row.get("n") and seat_row.get("p")  # only seats with actual votes
            ]
            pending_by_election_rows.append(
                {
                    "id": election_manifest_id,
                    "dbId": election.id,
                    "parentDbId": election.parent_election_id,
                    "name": election.name,
                    "date": str(election.election_date) if election.election_date else None,
                    "changes": changes,
                }
            )
            continue

        # Only Westminster maps are bootstrapped from a legacy template; every other
        # parliament (Holyrood, US) ships a pre-built TopoJSON in maps/ that we verify.
        uses_prebuilt_map = getattr(map_row, "parliament", "westminster") != "westminster"
        map_relpath = f"maps/{map_filename}"
        result_path = results_dir / result_filename
        map_path = maps_dir / map_filename

        if uses_prebuilt_map:
            # The TopoJSON for these maps is produced by a dedicated build step
            # (e.g. build_house_topojson.py) and lives directly in maps/. We only
            # need to verify it exists.
            if args.dry_run:
                print(f"Would write results: {result_path} ({len(result_payload.get('seats', []))} seats)")
                # A missing map was already reported by export_pages' preflight.
                if election.map_id not in written_map_ids and map_path.exists():
                    print(f"Pre-built map already in place: {map_path}")
            else:
                if election.map_id not in written_map_ids and not map_path.exists():
                    # Fail rather than emit a manifest pointing at a 404 map (matches the
                    # missing-template error on the Westminster path below).
                    raise FileNotFoundError(
                        f"Pre-built map not found at {map_path} — build it first (e.g. build_house_topojson.py)"
                    )
                write_json(result_path, result_payload)
        else:
            map_template_filename = choose_map_template_filename(map_row)
            map_template_path = args.legacy_files_dir / map_template_filename

            if not map_template_path.exists():
                raise FileNotFoundError(
                    f"Map template not found for map '{map_row.name}': {map_template_path}"
                )

            if args.dry_run:
                print(f"Would write results: {result_path} ({len(result_payload.get('seats', []))} seats)")
                if election.map_id not in written_map_ids:
                    if map_path.exists():
                        print(f"Map already in place: {map_path}")
                    else:
                        print(f"Would write map: {map_path} (template {map_template_filename})")
            else:
                write_json(result_path, result_payload)
                if election.map_id not in written_map_ids and not map_path.exists():
                    # Bootstrap a missing map from its legacy template only.  Never overwrite
                    # an existing map: the committed TopoJSON is hand-curated (e.g. manual
                    # boundary fixes) and is not reflected in the legacy templates.
                    map_payload = json.loads(map_template_path.read_text(encoding="utf-8"))
                    write_json(map_path, map_payload)

        if published is not None:
            sync_uncertainty_file(results_dir, uncertainty_document, dry_run=args.dry_run)

        map_files_by_id[str(election.map_id)] = map_relpath
        written_map_ids.add(election.map_id)
        manifest_id_by_db_id[election.id] = election_manifest_id

        # Holyrood elections with non-standard names (e.g. remapped boundary elections)
        # are exported to disk for model use but excluded from the UI manifest.  Their
        # data-file ref is registered below by manifest_id, so skip them here to avoid
        # polluting electionsById with a malformed slug (they are re-registered under
        # their curated manifest id by the preservation pass).
        is_standard_holyrood = election.type != ElectionType.holyrood_general or bool(
            re.fullmatch(r"\d{4}\s+Scottish Parliament Election", election.name)
        )
        if not is_standard_holyrood:
            continue

        data_files_by_election_id[election_manifest_id] = f"results/{result_filename}"

        parliament = getattr(map_row, "parliament", "westminster")
        manifest_entry = {
            "id": election_manifest_id,
            "name": manifest_name_for_election(election),
            "type": election.type.value,
            "mapId": election.map_id,
            "parliament": parliament,
        }
        # The results' source credit, shown under the map. Recomputed from the DB on every
        # export (not in PRESERVED_ENTRY_FIELDS), so clearing the note drops the field.
        if election.source_note:
            manifest_entry["sourceNote"] = election.source_note
        # Prediction elections carry ``model: true`` so the front-end shows the
        # predict UI / hides raw vote counts for them.
        if election.type in (
            ElectionType.model_uns,
            ElectionType.holyrood_uns,
            ElectionType.us_house_model,
            ElectionType.us_presidential_model,
            ElectionType.us_senate_model,
        ):
            manifest_entry["model"] = True
        manifest_entries.append(manifest_entry)

        if default_election_id is None and election.type == ElectionType.uk_general:
            default_election_id = election_manifest_id

    if args.output_file:
        return

    if single_election_mode:
        print("Skipping manifest write for single-election export mode")
        return

    manifest_path = output_root / "map-modes.json"
    existing = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )

    held_back: list[dict[str, Any]] = []
    map_parliaments: dict[str, str] = {}
    if scope is not None:
        map_parliaments = {
            str(map_id): parliament
            for map_id, parliament in session.execute(select(Map.id, Map.parliament))
        }
        held_back = [
            entry
            for entry in existing.get("elections", [])
            if "id" in entry and not scope.covers_entry(entry)
        ]
        # The entries a full export may add late (the composite and the preserved
        # types) are held back here and added below; this does no harm because
        # held-back entries are restored verbatim.
        manifest_entries.extend(
            copy.deepcopy(entry) for entry in held_back if not _added_late(entry)
        )
        existing_files = existing.get("files", {}).get("elections", {})
        # Maps the scope's results own are rebuilt from the elections exported now, so
        # one whose elections are gone drops out as it would in a full export; the
        # others, and any map a held-back entry still shows, are kept.
        held_map_ids = {
            str(entry["mapId"]) for entry in held_back if entry.get("mapId") is not None
        }
        map_files_by_id = {
            **{
                map_id: relpath
                for map_id, relpath in existing_files.get("mapsById", {}).items()
                if map_parliaments.get(map_id) not in scope.results_parliaments
                or map_id in held_map_ids
            },
            **map_files_by_id,
        }
        held_ids = {entry["id"] for entry in held_back}
        for entry_id, data_file in existing_files.get("electionsById", {}).items():
            if entry_id in held_ids:
                data_files_by_election_id.setdefault(entry_id, data_file)

    if "us_presidential" in parliaments and (
        scope is None or scope.covers("us_presidential", forecast=False)
    ) and not args.dry_run:
        for series in state_trends.values():
            series.sort(key=lambda entry: entry["year"])
        write_json(
            results_dir / "us-president-trends-by-state.json",
            {"schema": "pf-state-trends-v1", "units": state_trends},
        )

    apply_supplemental_legacy_elections(
        manifest_entries=manifest_entries,
        map_files_by_id=map_files_by_id,
        data_files_by_election_id=data_files_by_election_id,
        results_dir=results_dir,
        legacy_files_dir=args.legacy_files_dir,
        dry_run=args.dry_run,
        manifest_parties=manifest_parties,
        manifest_regions_by_map_id=manifest_regions_by_map_id,
        parliaments=(
            parliaments
            if scope is None
            else set(scope.results_parliaments & parliaments)
        ),
    )

    if default_election_id is None and (scope is None or manifest_entries):
        default_election_id = manifest_entries[0]["id"]

    assign_comparison_elections(manifest_entries)
    remove_comparison_for_supplemental_entries(manifest_entries)

    if pending_by_election_rows:
        by_elections_by_parent_manifest_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for by_election in pending_by_election_rows:
            parent_db_id = by_election.get("parentDbId")
            parent_manifest_id = manifest_id_by_db_id.get(parent_db_id) if parent_db_id is not None else None
            if not parent_manifest_id:
                continue
            by_elections_by_parent_manifest_id[parent_manifest_id].append(by_election)

        for parent_manifest_id, by_rows in sorted(by_elections_by_parent_manifest_id.items()):
            all_changes: list[dict[str, Any]] = []
            for row in sorted(by_rows, key=lambda row: (row.get("date") or "", str(row.get("name") or ""))):
                for change in row.get("changes", []):
                    if change.get("seat"):
                        all_changes.append(change)

            parent_data_relpath = data_files_by_election_id.get(parent_manifest_id)
            if not parent_data_relpath:
                continue
            parent_result_path = output_root / parent_data_relpath
            if not parent_result_path.exists():
                continue

            parent_payload = json.loads(parent_result_path.read_text(encoding="utf-8"))
            composite_seats = {
                seat_row["n"]: dict(seat_row)
                for seat_row in parent_payload.get("seats", [])
            }

            for change in all_changes:
                seat_name = change.get("seat")
                if seat_name and seat_name in composite_seats:
                    seat_entry = composite_seats[seat_name]
                    if change.get("winner") is not None:
                        seat_entry["w"] = change["winner"]
                    change_votes = change.get("votes", {})
                    if change_votes:
                        seat_entry["p"] = [
                            [int(pid), v]
                            for pid, v in sorted(
                                change_votes.items(),
                                key=lambda item: (-float(item[1]), int(item[0])),
                            )
                            if float(v) > 0
                        ]

            composite_payload = {
                "schema": "pf-results-v4",
                "seats": sorted(composite_seats.values(), key=lambda s: s["n"]),
            }

            composite_filename = "current-parliament.json"
            composite_path = results_dir / composite_filename
            composite_manifest_id = COMPOSITE_ENTRY_ID

            if args.dry_run:
                print(f"Would write composite: {composite_path} ({len(composite_seats)} seats, {len(all_changes)} overrides)")
            else:
                write_json(composite_path, composite_payload)
                print(f"Wrote composite: {composite_path} ({len(composite_seats)} seats, {len(all_changes)} overrides)")

            data_files_by_election_id[composite_manifest_id] = f"results/{composite_filename}"

            parent_entry = next(
                (e for e in manifest_entries if e.get("id") == parent_manifest_id),
                None,
            )
            parent_map_id = parent_entry["mapId"] if parent_entry else 2

            composite_entry = {
                "id": composite_manifest_id,
                "name": "Current Parliament",
                "type": ElectionType.uk_general.value,
                "mapId": parent_map_id,
                "parliament": "westminster",
                "comparisonElectionId": parent_manifest_id,
                "byElectionSeats": [change["seat"] for change in all_changes],
            }

            insert_current_parliament_entry(manifest_entries, composite_entry)
            default_election_id = composite_manifest_id

    if scope is not None:
        # The entries a full export may add late: the composite where this run did
        # not build it, then the preserved ones.
        present = {entry.get("id") for entry in manifest_entries}
        for entry in held_back:
            if not _added_late(entry) or entry["id"] in present:
                continue
            if entry["id"] == COMPOSITE_ENTRY_ID:
                insert_current_parliament_entry(manifest_entries, copy.deepcopy(entry))
            else:
                insert_preserved_entry(manifest_entries, copy.deepcopy(entry))

    expected_map_filenames = {Path(path).name for path in map_files_by_id.values()}
    if maps_dir.exists():
        for existing_map in maps_dir.glob("*.topo.json"):
            if existing_map.name not in expected_map_filenames:
                if scope is not None:
                    # Only maps the scope's results own can be stale; any other file
                    # (or one that does not name a known map) stays.
                    named = _MAP_FILE_NAME.fullmatch(existing_map.name)
                    owner = map_parliaments.get(named.group(1)) if named else None
                    if owner is None or owner not in scope.results_parliaments:
                        continue
                if args.dry_run:
                    print(f"Would remove stale map: {existing_map}")
                else:
                    existing_map.unlink()
                    print(f"Removed stale map: {existing_map}")

    # map-modes-shell.json is the hand-authored source of truth for the carried-over,
    # non-generated config (misc, parliamentFeatures, mapModes structure, defaultElection).
    # When absent we fall back to the previously-written map-modes.json so older
    # checkouts and tests without a shell keep working unchanged.
    shell_path = output_root / "map-modes-shell.json"
    shell = json.loads(shell_path.read_text(encoding="utf-8")) if shell_path.exists() else {}
    config = shell if shell else existing

    # model/poll-tracker meta files are keyed by parliament; keep only those belonging to
    # this page. Defaults cover every parliament with a forecast model (the front end
    # fetches ``data/<files.meta[parliament]>`` for the "Latest poll used" snippet on
    # model elections and the poll tracker); values from the existing manifest win so
    # hand-tuned paths survive regeneration.
    default_meta = {
        "westminster": "results/model_output_trends_meta.json",
        "holyrood": "results/holyrood-prediction-meta.json",
        "us_house": "results/us-house-trends_meta.json",
        "us_presidential": "results/us-president-trends_meta.json",
        "us_senate": "results/us-senate-trends_meta.json",
    }
    meta_all = {**default_meta, **existing.get("files", {}).get("meta", {})}
    meta = {k: v for k, v in meta_all.items() if k in parliaments}

    files = {
        "elections": {
            "mapsById": map_files_by_id,
            "electionsById": data_files_by_election_id,
        },
        "meta": meta,
    }

    # Re-insert any holyrood_uns or model_uns prediction entry from the existing
    # manifest, provided its data file still exists on disk.  This lets the model write
    # the entry once and have it survive subsequent full export runs without needing a
    # DB record (the westminster model writes to SQLite only; Supabase never receives
    # simulation data).  Non-standard holyrood_general elections (e.g. remapped-boundary
    # elections like 2021-holyrood-2026) are also preserved here — they are exported to
    # disk by the main loop but excluded from manifest_entries by the standard-name filter.
    existing_by_id = {e.get("id"): e for e in existing.get("elections", [])}
    existing_data_files = existing.get("files", {}).get("elections", {}).get("electionsById", {})
    for entry_id, entry in existing_by_id.items():
        entry_type = entry.get("type")
        if entry_type not in PRESERVE_ENTRY_TYPES:
            continue
        if entry_id in {e["id"] for e in manifest_entries}:
            continue  # already present
        data_file = existing_data_files.get(entry_id)
        if data_file and (output_root / data_file).exists():
            insert_preserved_entry(manifest_entries, entry)
            data_files_by_election_id[entry_id] = data_file
            files["elections"]["electionsById"] = data_files_by_election_id

    # Preserve the curated defaultElection across exports when it still points at a
    # real election (so a hand-set default like "current-prediction" survives regen);
    # otherwise fall back to current-holyrood-prediction when present.
    manifest_ids = {e.get("id") for e in manifest_entries}
    existing_default = config.get("defaultElection", existing.get("defaultElection"))
    if existing_default in manifest_ids:
        default_election_id = existing_default
    elif "current-holyrood-prediction" in manifest_ids:
        default_election_id = "current-holyrood-prediction"

    # Preserve manually-curated election fields that the export pipeline does not compute.
    # The main loop builds entries from DB rows with only the standard fields (id, name,
    # type, mapId, parliament), so without this merge, manual additions (e.g. the
    # referendum flag) would be wiped on every export.
    PRESERVED_ENTRY_FIELDS = ("referendum",)
    for entry in manifest_entries:
        existing_entry = existing_by_id.get(entry.get("id"))
        if not existing_entry:
            continue
        for field in PRESERVED_ENTRY_FIELDS:
            if field in existing_entry and field not in entry:
                entry[field] = existing_entry[field]

    existing_order = [e["id"] for e in existing.get("elections", []) if "id" in e]
    manifest_entries = finalize_manifest_order(
        manifest_entries,
        existing_order,
        SUPPLEMENTAL_LEGACY_ELECTIONS,
        parliaments=parliaments,
    )

    # Second comparison pass, now that preserved boundary-changed baselines (e.g.
    # 2021-holyrood-2026) are present and the list is in curated newest-first order.
    # This resolves any comparison left unset by the first pass because its
    # same-boundary baseline had not yet been re-inserted.  Idempotent: entries that
    # already have a comparison are skipped.
    assign_comparison_elections(manifest_entries)
    remove_comparison_for_supplemental_entries(manifest_entries)

    map_modes = build_map_modes_with_regions(
        config.get("mapModes", {}),
        manifest_regions_by_map_id,
        parliament_features=config.get("parliamentFeatures", {}),
    )
    if scope is not None:
        manifest_entries = restore_held_back_entries(manifest_entries, held_back)
        final_ids = {entry.get("id") for entry in manifest_entries}
        file_order = {entry_id: i for i, entry_id in enumerate(existing_data_files)}
        files["elections"]["electionsById"] = {
            entry_id: data_file
            for entry_id, data_file in sorted(
                data_files_by_election_id.items(),
                key=lambda item: file_order.get(item[0], len(file_order)),
            )
            if entry_id in final_ids
        }
        map_order = {
            map_id: i
            for i, map_id in enumerate(
                existing.get("files", {}).get("elections", {}).get("mapsById", {})
            )
        }
        files["elections"]["mapsById"] = dict(
            sorted(
                map_files_by_id.items(),
                key=lambda item: map_order.get(item[0], len(map_order)),
            )
        )
        map_modes = scope_map_modes(
            map_modes,
            existing.get("mapModes", {}),
            {
                map_id
                for map_id, parliament in map_parliaments.items()
                if parliament in scope.effective_parliaments
            },
            refresh_metadata=scope.metadata,
        )
    unmoded_map_ids = sorted(
        {
            int(entry["mapId"])
            for entry in manifest_entries
            if entry.get("mapId") is not None and str(entry["mapId"]) not in map_modes
        }
    )
    if unmoded_map_ids:
        # The front end needs a mapMode (projection, regions) to draw a map; add it to
        # the page's map-modes-shell.json.
        print(f"Warning: no mapModes entry for map id(s) {unmoded_map_ids} in {shell_path}")

    manifest_payload = {
        "defaultElection": default_election_id,
        "elections": manifest_entries,
        "misc": config.get("misc", {}),
        "parliamentFeatures": config.get("parliamentFeatures", {}),
        "mapModes": map_modes,
        # Hand-authored, jurisdiction-specific party-key aliases (carried through from the
        # page's shell); the front-end's resolvePartyRef reads them so no aliases live in JS.
        "partyKeyAliases": config.get("partyKeyAliases", {}),
        "files": files,
        "parties": manifest_parties,
    }

    if args.dry_run:
        print(f"Would write manifest: {manifest_path} ({len(manifest_entries)} elections)")
    else:
        write_manifest(manifest_path, manifest_payload)
        print(f"Wrote manifest: {manifest_path} ({len(manifest_entries)} elections)")




if __name__ == "__main__":
    main()
