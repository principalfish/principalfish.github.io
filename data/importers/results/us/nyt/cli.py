"""Fetch, convert and import NYT US House and Senate results.

Subcommands, each for one ``--year`` and ``--chamber``:

- ``fetch``: download the general results file and any runoff file for the
  cycle into the cache (pending runoffs are skipped with a message).
- ``convert``: parse the cached files, apply runoffs, and write
  ``{chamber}-{year}.json`` (plus ``senate-{year}-special.json`` when a special
  shares a state with a regular race) into ``--out-dir``.
- ``import``: load those files into the database as ``"{year} US House
  Election"`` / ``"{year} US Senate Election"`` (and ``"{year} US Senate
  Special Election"``, a child of the regular one).
- ``all``: fetch, convert and import in one go.

A fifth subcommand, ``current-senate``, takes no ``--year`` or ``--chamber``: it
rebuilds the Current Senate snapshot (``senate-current.json``) from the
committed 2026 Senate election file and never touches the database (see
``importers/results/us/nyt/current_senate.py``). Options: ``--snapshot`` (input),
``--election-file``, ``--out`` (both default to the committed paths) and
``--dry-run``, which writes nothing and prints the holdover, winner and
uncalled counts.

Every run prints a diff of the new results against the committed file and the
database's current rows: seats added or removed, winner changes, newly called
or uncalled seats, % counted changes and the largest vote changes. Against the
FiveThirtyEight files for 2020-2024 this doubles as a check of the NYT data.

``--dry-run`` writes nothing at all: no cache files (``all`` downloads into a
throwaway folder), no election files, no database rows. It prints what would
change.

Usage (from ``data/``)::

    python importers/results/us/nyt/cli.py fetch --year 2026 --chamber house
    python importers/results/us/nyt/cli.py convert --year 2026 --chamber house --dry-run
    python importers/results/us/nyt/cli.py import --year 2026 --chamber house --refresh
    python importers/results/us/nyt/cli.py all --year 2026 --chamber senate --refresh
    python importers/results/us/nyt/cli.py current-senate --dry-run

``python -m importers.results.us.nyt.cli ...`` works too.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import config
from core.db import Database
from importers.results.us.import_house_elections import district_state, import_house
from importers.results.us.import_senate_elections import import_senate
from importers.results.us.nyt.adapters import AdapterError, apply_runoffs, parse
from importers.results.us.nyt.convert import (
    META_KEY,
    VOTES_538,
    VOTES_NOMINAL,
    map_for,
    ConversionResult,
    ConvertError,
    SourceMeta,
    convert_races,
    output_name,
)
from importers.results.us.nyt.current_senate import (
    DEFAULT_ELECTION_FILE,
    DEFAULT_SNAPSHOT,
    CurrentSenateError,
    rebuild_current_senate,
)
from importers.results.us.nyt.fetch import FetchError, FetchResult, fetch
from importers.results.us.nyt.races import Race
from importers.results.us.nyt.sources import (
    SOURCES,
    Chamber,
    Source,
    SourceStatus,
    UnknownSourceError,
    source_for,
    supported_years,
)
from importers.results.us.us_import import (
    PARTY_KEY_TO_NAME,
    check_election_map,
    check_us_election_file,
    map_from_meta,
    plan_us_election,
)

DATA_DIR = Path(__file__).resolve().parents[4]
DEFAULT_OUT_DIR = DATA_DIR / "inputs" / "us"
DEFAULT_TOP_N = 10
# The subcommand with its own options (no year or chamber); see _run_current_senate.
CURRENT_SENATE_COMMAND = "current-senate"

_CHAMBER_LABELS: dict[Chamber, str] = {"house": "House", "senate": "Senate"}
_PARTY_KEY_BY_NAME = {name: key for key, name in PARTY_KEY_TO_NAME.items()}


_EXISTS_MESSAGE = "{name!r} already exists; pass --refresh to update it in place"


class CliError(Exception):
    """Raised for a problem the CLI reports as a one-line error."""


def election_name(year: int, chamber: Chamber, *, special: bool = False) -> str:
    """Return the DB election name for a cycle's results.

    Args:
        year: Election cycle year.
        chamber: ``"house"`` or ``"senate"``.
        special: The same-day Senate specials election rather than the regular one.

    Returns:
        E.g. ``"2026 US House Election"`` or ``"2020 US Senate Special Election"``.
    """
    if special:
        return f"{year} US Senate Special Election"
    return f"{year} US {_CHAMBER_LABELS[chamber]} Election"


# --- diff ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeatResult:
    """One seat's result, as compared between versions.

    Attributes:
        winner: The elected party key, or ``None`` while uncalled.
        called: Whether a winner is called.
        pct: Share of the vote counted, when known.
        totals: Votes per party key.
    """

    winner: str | None
    called: bool
    pct: float | None
    totals: dict[str, float]


@dataclass(frozen=True, slots=True)
class WinnerChange:
    """A seat called for a different party than before."""

    seat: str
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class PctChange:
    """A seat whose share of the vote counted moved."""

    seat: str
    old: float | None
    new: float | None


@dataclass(frozen=True, slots=True)
class VoteDelta:
    """How much a seat's votes moved.

    Attributes:
        seat: Seat key.
        old_total: All parties' votes before.
        new_total: All parties' votes now.
        party_deltas: Per-party change (new minus old), largest move first.
    """

    seat: str
    old_total: float
    new_total: float
    party_deltas: tuple[tuple[str, float], ...]

    @property
    def magnitude(self) -> float:
        """Sum of the per-party moves, which ranks seats by how much changed."""
        return sum(abs(delta) for _, delta in self.party_deltas)


@dataclass(frozen=True, slots=True)
class Comparison:
    """New results against one earlier version (the committed file or the DB).

    Attributes:
        label: What the new results were compared with.
        old_seats: Seat count before.
        new_seats: Seat count now.
        added: Seats only in the new results.
        removed: Seats only in the old ones.
        winner_changes: Seats called both times, for different parties.
        newly_called: Seats uncalled before and called now.
        newly_uncalled: Seats called before and uncalled now.
        pct_changes: Seats whose % counted moved; only seats that had a % before.
        vote_deltas: Every seat whose votes moved, largest move first.
    """

    label: str
    old_seats: int
    new_seats: int
    added: tuple[str, ...]
    removed: tuple[str, ...]
    winner_changes: tuple[WinnerChange, ...]
    newly_called: tuple[str, ...]
    newly_uncalled: tuple[str, ...]
    pct_changes: tuple[PctChange, ...]
    vote_deltas: tuple[VoteDelta, ...]

    def summary(self, top_n: int = DEFAULT_TOP_N) -> list[str]:
        """Return printable lines for this comparison."""
        lines = [
            f"vs {self.label}: {self.old_seats} -> {self.new_seats} seats "
            f"(+{len(self.added)} added, -{len(self.removed)} removed)",
        ]
        if self.added:
            lines.append(f"  added: {', '.join(self.added)}")
        if self.removed:
            lines.append(f"  removed: {', '.join(self.removed)}")
        lines.append(f"  winner changes: {len(self.winner_changes)}")
        lines.extend(f"    {c.seat}: {c.old} -> {c.new}" for c in self.winner_changes)
        lines.append(f"  newly called: {len(self.newly_called)}")
        if self.newly_called:
            lines.append(f"    {', '.join(self.newly_called)}")
        lines.append(f"  newly uncalled: {len(self.newly_uncalled)}")
        if self.newly_uncalled:
            lines.append(f"    {', '.join(self.newly_uncalled)}")
        lines.append(f"  % counted changes: {len(self.pct_changes)}")
        ranked_pct = sorted(self.pct_changes, key=_pct_move, reverse=True)
        lines.extend(
            f"    {c.seat}: {_pct_text(c.old)} -> {_pct_text(c.new)}"
            for c in ranked_pct[:top_n]
        )
        lines.append(f"  seats with vote changes: {len(self.vote_deltas)}")
        for delta in self.vote_deltas[:top_n]:
            party, change = delta.party_deltas[0]
            lines.append(
                f"    {delta.seat}: {delta.old_total:,.0f} -> {delta.new_total:,.0f} "
                f"(largest: {party} {change:+,.0f})",
            )
        return lines


@dataclass(frozen=True, slots=True)
class DiffReport:
    """New results against every earlier version found.

    Attributes:
        comparisons: One per earlier version (committed file, database).
        backfilled: Unopposed seats in the new results whose votes are the
            FiveThirtyEight totals carried over, since NYT reports none.
        nominal: Unopposed seats given the nominal placeholder total instead.
    """

    comparisons: tuple[Comparison, ...] = field(default_factory=tuple)
    backfilled: tuple[str, ...] = ()
    nominal: tuple[str, ...] = ()

    def summary(self, top_n: int = DEFAULT_TOP_N) -> str:
        """Return the printable report."""
        lines: list[str] = []
        if self.backfilled:
            lines.append(
                f"unopposed seats with 538 totals: {len(self.backfilled)} "
                f"({', '.join(self.backfilled)})",
            )
        if self.nominal:
            lines.append(
                f"unopposed seats with nominal totals: {len(self.nominal)} "
                f"({', '.join(self.nominal)})",
            )
        if not self.comparisons:
            lines.append("no earlier file or database election to compare with")
        for comparison in self.comparisons:
            lines.extend(comparison.summary(top_n))
        return "\n".join(lines)


def seat_results_from_file(data: dict[str, Any]) -> dict[str, SeatResult]:
    """Read each seat's result from an election file (any converter's).

    Files without status fields (FiveThirtyEight) count every seat as called
    for its ``current`` party.
    """
    results: dict[str, SeatResult] = {}
    for key, seat in data.items():
        if key == META_KEY:
            continue
        seat_info = seat["seatInfo"]
        called = bool(seat_info.get("called", True))
        winner = seat_info.get("current") if called else None
        pct = seat_info.get("pct")
        results[key] = SeatResult(
            winner=winner,
            called=called and winner is not None,
            pct=None if pct is None else float(pct),
            totals={
                party: float(info["total"]) for party, info in seat["partyInfo"].items()
            },
        )
    return results


def snapshot_election(db: Database, name: str) -> dict[str, SeatResult] | None:
    """Read an election's current per-seat results from the database.

    Args:
        db: The database.
        name: Election name.

    Returns:
        Results keyed by seat name, or ``None`` when no such election exists.
    """
    election = db.get_election_by_name(name)
    if election is None:
        return None
    seat_names = {
        seat.id: seat.seat_name for seat in db.get_seats_for_map(election.map_id)
    }
    party_keys = {
        party.id: _PARTY_KEY_BY_NAME.get(party.name, "others")
        for party in db.get_all_parties()
    }
    statuses = db.get_seat_statuses(election.id)
    totals: dict[int, dict[str, float]] = {}
    winners: dict[int, str] = {}
    for vote in db.get_votes_for_election(election.id):
        key = "others"
        if vote.party_id is not None:
            key = party_keys.get(vote.party_id, "others")
        seat_totals = totals.setdefault(vote.seat_id, {})
        seat_totals[key] = seat_totals.get(key, 0.0) + (vote.vote_total or 0.0)
        if vote.elected:
            winners[vote.seat_id] = key
    results: dict[str, SeatResult] = {}
    for seat_id, seat_totals in totals.items():
        status = statuses.get(seat_id)
        winner = winners.get(seat_id)
        results[seat_names[seat_id]] = SeatResult(
            winner=winner,
            called=winner is not None and (status is None or status.called),
            pct=None if status is None else status.pct_counted,
            totals=seat_totals,
        )
    return results


def diff_against_existing(
    new: dict[str, Any],
    old_file_dict: dict[str, Any] | None,
    db_snapshot: dict[str, SeatResult] | None,
    *,
    file_label: str = "committed file",
) -> DiffReport:
    """Compare new results with the committed file and the database.

    Args:
        new: The newly converted election file.
        old_file_dict: The file it would replace, or ``None`` when there is none.
        db_snapshot: The database election's results (:func:`snapshot_election`),
            or ``None`` when it is not imported.
        file_label: How to name the file in the report.

    Returns:
        One comparison per earlier version given.
    """
    new_results = seat_results_from_file(new)
    comparisons: list[Comparison] = []
    if old_file_dict is not None:
        comparisons.append(
            _compare(file_label, seat_results_from_file(old_file_dict), new_results),
        )
    if db_snapshot is not None:
        comparisons.append(_compare("database", db_snapshot, new_results))
    return DiffReport(
        comparisons=tuple(comparisons),
        backfilled=_seats_with_votes_source(new, VOTES_538),
        nominal=_seats_with_votes_source(new, VOTES_NOMINAL),
    )


def _seats_with_votes_source(
    data: dict[str, Any],
    votes_source: str,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            key
            for key, seat in data.items()
            if key != META_KEY and seat["seatInfo"].get("votesSource") == votes_source
        ),
    )


def _compare(
    label: str,
    old: dict[str, SeatResult],
    new: dict[str, SeatResult],
) -> Comparison:
    common = sorted(old.keys() & new.keys())
    winner_changes: list[WinnerChange] = []
    newly_called: list[str] = []
    newly_uncalled: list[str] = []
    pct_changes: list[PctChange] = []
    vote_deltas: list[VoteDelta] = []
    for seat in common:
        before, after = old[seat], new[seat]
        if (
            before.called
            and after.called
            and before.winner is not None
            and after.winner is not None
            and before.winner != after.winner
        ):
            winner_changes.append(WinnerChange(seat, before.winner, after.winner))
        if not before.called and after.called:
            newly_called.append(seat)
        if before.called and not after.called:
            newly_uncalled.append(seat)
        if before.pct is not None and before.pct != after.pct:
            pct_changes.append(PctChange(seat, before.pct, after.pct))
        deltas = [
            (party, after.totals.get(party, 0.0) - before.totals.get(party, 0.0))
            for party in sorted(before.totals.keys() | after.totals.keys())
        ]
        moved = sorted(
            ((party, delta) for party, delta in deltas if delta),
            key=lambda item: abs(item[1]),
            reverse=True,
        )
        if moved:
            vote_deltas.append(
                VoteDelta(
                    seat=seat,
                    old_total=sum(before.totals.values()),
                    new_total=sum(after.totals.values()),
                    party_deltas=tuple(moved),
                ),
            )
    vote_deltas.sort(key=lambda delta: delta.magnitude, reverse=True)
    return Comparison(
        label=label,
        old_seats=len(old),
        new_seats=len(new),
        added=tuple(sorted(new.keys() - old.keys())),
        removed=tuple(sorted(old.keys() - new.keys())),
        winner_changes=tuple(winner_changes),
        newly_called=tuple(newly_called),
        newly_uncalled=tuple(newly_uncalled),
        pct_changes=tuple(pct_changes),
        vote_deltas=tuple(vote_deltas),
    )


def _pct_move(change: PctChange) -> float:
    return abs((change.new or 0.0) - (change.old or 0.0))


def _pct_text(value: float | None) -> str:
    return "n/a" if value is None else f"{value:g}%"


# --- steps ---------------------------------------------------------------------


def cycle_sources(year: int, chamber: Chamber) -> tuple[Source, list[Source]]:
    """Return the cycle's general source and its runoff sources, pending included."""
    general = source_for(year, chamber, "general")
    runoffs = [
        source
        for (source_year, source_chamber, kind), source in SOURCES.items()
        if (source_year, source_chamber, kind) == (year, chamber, "runoff")
    ]
    return general, runoffs


def fetch_cycle(
    year: int,
    chamber: Chamber,
    cache_dir: Path,
    *,
    offline: bool,
    echo: Callable[[str], None] = print,
) -> tuple[FetchResult, list[FetchResult]]:
    """Fetch (or read from the cache) a cycle's general file and available runoffs.

    Pending runoffs are skipped with a message.

    Args:
        year: Election cycle year.
        chamber: ``"house"`` or ``"senate"``.
        cache_dir: Root of the NYT cache.
        offline: Read the latest cached files instead of downloading.
        echo: Receives one line per file read and per pending runoff skipped;
            the CLI prints them, the console shows them on its preview.

    Returns:
        The general file and the runoff files read.
    """
    general_source, runoff_sources = cycle_sources(year, chamber)
    general = fetch(general_source, cache_dir, offline=offline)
    echo(_fetch_line(general))
    runoffs: list[FetchResult] = []
    for source in runoff_sources:
        if source.status is SourceStatus.PENDING:
            echo(f"skipping pending runoff {year}/{chamber}: {source.note}")
            continue
        result = fetch(source, cache_dir, offline=offline)
        echo(_fetch_line(result))
        runoffs.append(result)
    return general, runoffs


def parse_cycle(general: FetchResult, runoffs: Sequence[FetchResult]) -> list[Race]:
    """Parse a cycle's fetched files and apply its runoffs.

    Args:
        general: The general election file.
        runoffs: Runoff files whose races replace the general file's.

    Returns:
        The cycle's races, each holding its decisive round.
    """
    races = parse(general.source, general.data)
    runoff_races = [
        race for result in runoffs for race in parse(result.source, result.data)
    ]
    return apply_runoffs(races, runoff_races)


def convert_cycle(
    general: FetchResult,
    runoffs: Sequence[FetchResult],
    *,
    out_dir: Path | None = None,
    races: Sequence[Race] | None = None,
) -> ConversionResult:
    """Parse a cycle's fetched files, apply its runoffs and convert the races.

    Args:
        general: The general election file.
        runoffs: Runoff files to apply.
        out_dir: Folder holding the files this conversion replaces; unopposed
            seats take their real totals from them where they can.
        races: The races :func:`parse_cycle` already returned for these files,
            so a caller that shows them need not parse twice; parsed here when
            omitted.

    Returns:
        The conversion.
    """
    source = general.source
    if races is None:
        races = parse_cycle(general, runoffs)
    previous = previous_special = None
    if out_dir is not None:
        previous = read_election_file(
            out_dir / output_name(source.year, source.chamber),
            source.chamber,
        )
        if source.chamber == "senate":
            previous_special = read_election_file(
                out_dir / output_name(source.year, source.chamber, special=True),
                source.chamber,
            )
    return convert_races(
        races,
        year=source.year,
        chamber=source.chamber,
        source_meta=SourceMeta.from_fetches(general, runoffs),
        previous=previous,
        previous_special=previous_special,
    )


def report_conversion(
    result: ConversionResult,
    *,
    year: int,
    chamber: Chamber,
    out_dir: Path,
    db: Database | None,
) -> str:
    """Return the printable summary and diff for a conversion."""
    lines: list[str] = []
    files: list[tuple[dict[str, Any], bool]] = [(result.main, False)]
    if result.special is not None:
        files.append((result.special, True))
    for data, special in files:
        name = output_name(year, chamber, special=special)
        lines.append(f"{name}: {_status_line(data)}")
        lines.append(f"  source note: {data[META_KEY]['source_note']}")
        old_file = read_election_file(out_dir / name, chamber)
        snapshot = None
        if db is not None:
            snapshot = snapshot_election(
                db,
                election_name(year, chamber, special=special),
            )
        report = diff_against_existing(
            data,
            old_file,
            snapshot,
            file_label=f"committed {name}",
        )
        lines.extend(f"  {line}" for line in report.summary().splitlines())
    stale_special = out_dir / output_name(year, chamber, special=True)
    if chamber == "senate" and result.special is None and stale_special.exists():
        lines.append(
            f"warning: {stale_special.name} exists but this conversion has no specials",
        )
    lines.extend(f"warning: {warning}" for warning in result.warnings)
    return "\n".join(lines)


def refuse_stale_special(
    result: ConversionResult,
    *,
    year: int,
    chamber: Chamber,
    out_dir: Path,
    db: Database,
) -> None:
    """Refuse a Senate conversion with no specials over a leftover specials file.

    Writing the new main file would leave ``senate-<year>-special.json`` beside
    a main file that does not name it, which the import then refuses; checking
    first writes nothing.

    Raises:
        CliError: If the leftover file exists.
    """
    stale_special = out_dir / output_name(year, chamber, special=True)
    if chamber == "senate" and result.special is None and stale_special.exists():
        raise CliError(_stale_special_message(stale_special, db, year))


def check_import(
    db: Database,
    data: dict[str, Any],
    *,
    name: str,
    year: int,
    chamber: Chamber,
    what: str,
) -> None:
    """Run the importer's own checks for one election file, writing nothing.

    The map in ``meta`` must be the election's existing map (when it exists),
    the US parties must be seeded and every seat must be well formed.

    Args:
        db: Target database.
        data: The election file's content.
        name: The election it imports into.
        year: Election cycle year.
        chamber: ``"house"`` or ``"senate"``.
        what: How to name the file in an error.

    Raises:
        CliError: Naming ``what`` and the first problem found.
    """
    default_map = map_for(year, chamber)
    try:
        map_id, _ = map_from_meta(data, default_map.id, default_map.name)
        check_election_map(db, name, map_id)
        plan_us_election(db, data, state_for_key=_state_for_key(chamber))
    except SystemExit as err:
        raise CliError(f"{what}: {err.code}") from err


def write_conversion(
    result: ConversionResult,
    *,
    year: int,
    chamber: Chamber,
    out_dir: Path,
) -> list[Path]:
    """Write a conversion's files into ``out_dir`` and return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    files: list[tuple[dict[str, Any], bool]] = [(result.main, False)]
    if result.special is not None:
        files.append((result.special, True))
    for data, special in files:
        path = out_dir / output_name(year, chamber, special=special)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def import_files(
    db: Database,
    *,
    year: int,
    chamber: Chamber,
    out_dir: Path,
    refresh: bool,
    dry_run: bool,
    report: bool = True,
    on_imported: Callable[[Path], None] | None = None,
) -> str:
    """Import a cycle's converted files from ``out_dir`` (or report what would change).

    Args:
        db: Target database.
        year: Election cycle year.
        chamber: ``"house"`` or ``"senate"``.
        out_dir: Folder holding the converted files.
        refresh: Update existing elections in place.
        dry_run: Report only; import nothing.
        report: Include the diff against the database.
        on_imported: Called with each file's path once its election is
            imported, so a caller whose later file fails knows which landed.

    Returns:
        The printable report.

    Raises:
        CliError: If the main file is missing, a specials file is left over
            from an earlier conversion (or one the main file names is missing),
            or (unless a dry run) an election exists without ``refresh``.
    """
    main_path = out_dir / output_name(year, chamber)
    main_data = read_election_file(main_path, chamber)
    if main_data is None:
        raise CliError(f"{main_path} not found; run convert first")
    plan: list[tuple[Path, dict[str, Any], str, bool]] = [
        (main_path, main_data, election_name(year, chamber), False),
    ]
    special_path = _special_to_import(
        db,
        main_data,
        out_dir,
        year=year,
        chamber=chamber,
    )
    if special_path is not None:
        special_data = read_election_file(special_path, chamber)
        if special_data is None:
            raise CliError(
                f"{main_path.name} names {special_path.name}, which is missing",
            )
        special_name = election_name(year, chamber, special=True)
        plan.append((special_path, special_data, special_name, True))

    lines: list[str] = []
    if chamber == "senate" and special_path is None:
        orphan = _orphan_special_warning(db, year)
        if orphan is not None:
            lines.append(f"warning: {orphan}")
    for path, data, name, _ in plan:
        exists = db.get_election_by_name(name) is not None
        if exists and not refresh and not dry_run:
            raise CliError(_EXISTS_MESSAGE.format(name=name))
        lines.append(f"{name} from {path.name}: {_status_line(data)}")
        if not exists:
            lines.append("  new election")
        elif not refresh:
            lines.append("  already exists: a real run needs --refresh")
        if report:
            diff = diff_against_existing(data, None, snapshot_election(db, name))
            lines.extend(f"  {line}" for line in diff.summary().splitlines())
    if dry_run:
        lines.append("dry run: nothing imported")
        return "\n".join(lines)

    db.create_tables()
    # Plan every file before importing any, so a bad specials file cannot leave
    # the regular election refreshed and the special stale.
    for path, data, name, _ in plan:
        check_import(db, data, name=name, year=year, chamber=chamber, what=path.name)
    for path, _, name, special in plan:
        try:
            if chamber == "house":
                inserted = import_house(db, path, year, name, refresh=refresh)
            else:
                parent = election_name(year, chamber) if special else None
                inserted = import_senate(
                    db,
                    path,
                    year,
                    name,
                    refresh=refresh,
                    parent_name=parent,
                )
        except (KeyError, TypeError, ValueError) as err:
            raise CliError(f"{path.name} is malformed: {err!r}") from err
        election = db.get_election_by_name(name)
        if election is None:
            raise CliError(f"{name!r} is missing after its import")
        if on_imported is not None:
            on_imported(path)
        lines.append(
            f"imported {name}: {inserted} votes, election id {election.id}, "
            f"map id {election.map_id}",
        )
    return "\n".join(lines)


def _special_to_import(
    db: Database,
    main_data: dict[str, Any],
    out_dir: Path,
    *,
    year: int,
    chamber: Chamber,
) -> Path | None:
    """Return the specials file that belongs with ``main_data``, if any.

    The main file's ``meta.special_file`` names it. A specials file on disk that
    the main file does not name is left over from an earlier conversion; it is
    refused, not deleted, so the operator decides what to do with it.
    """
    meta = main_data.get(META_KEY)
    named = meta.get("special_file") if isinstance(meta, dict) else None
    on_disk = out_dir / output_name(year, chamber, special=True)
    if named is None:
        if chamber == "senate" and on_disk.exists():
            raise CliError(_stale_special_message(on_disk, db, year))
        return None
    if named != on_disk.name:
        raise CliError(f"meta special_file {named!r} is not {on_disk.name!r}")
    return on_disk


def _stale_special_message(path: Path, db: Database, year: int) -> str:
    message = (
        f"{path} is left over from an earlier conversion (this one has no "
        "same-day specials); remove it, then re-run"
    )
    orphan = _orphan_special_warning(db, year)
    return message if orphan is None else f"{message}. Also: {orphan}"


def _orphan_special_warning(db: Database, year: int) -> str | None:
    """Warn about a special election in the DB that no current file names."""
    name = election_name(year, "senate", special=True)
    if db.get_election_by_name(name) is None:
        return None
    return (
        f"the database still holds {name!r}, which no current file names; "
        "it will not be refreshed"
    )


def _status_line(data: dict[str, Any]) -> str:
    results = seat_results_from_file(data)
    called = sum(result.called for result in results.values())
    parties: dict[str, int] = {}
    for result in results.values():
        if result.winner is not None:
            parties[result.winner] = parties.get(result.winner, 0) + 1
    tally = ", ".join(f"{party} {count}" for party, count in sorted(parties.items()))
    return f"{len(results)} seats, {called} called ({tally})"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise CliError(f"{path} is not valid JSON: {err}") from err
    if not isinstance(data, dict):
        raise CliError(f"{path} does not hold a JSON object")
    return data


def read_election_file(path: Path, chamber: Chamber) -> dict[str, Any] | None:
    """Read and check a converted election file; ``None`` when it does not exist.

    Raises:
        CliError: If the file is not valid JSON or not shaped as an election file.
    """
    data = _read_json(path)
    if data is not None:
        validate_election_file(data, chamber, what=path.name)
    return data


def validate_election_file(
    data: dict[str, Any],
    chamber: Chamber,
    *,
    what: str,
) -> None:
    """Check an election file with the importer's own checks, writing nothing.

    Covers ``meta`` (date, note, map), the seat keys (``ST-NN`` districts, or
    state names for the Senate), the status fields and every party line's key,
    name and total, plus ``meta.special_file``.

    Raises:
        CliError: Naming ``what`` and the first problem found.
    """
    try:
        check_us_election_file(data, state_for_key=_state_for_key(chamber))
    except SystemExit as err:
        raise CliError(f"{what}: {err.code}") from err
    meta = data.get(META_KEY)
    special_file = meta.get("special_file") if isinstance(meta, dict) else None
    if special_file is not None and not isinstance(special_file, str):
        raise CliError(f"{what}: meta special_file must be a file name or null")


def _state_for_key(chamber: Chamber) -> Callable[[str], str]:
    if chamber == "house":
        return district_state
    return _senate_state


def _senate_state(key: str) -> str:
    # A Senate seat's key is already its state's name.
    return key


def _fetch_line(result: FetchResult) -> str:
    where = "cache" if result.from_cache else "NYT"
    return (
        f"{result.source.kind} {result.source.year}/{result.source.chamber} "
        f"from {where}: "
        f"retrieved {result.retrieved_at.isoformat()}, "
        f"updated {result.source_updated_at or 'n/a'}"
    )


# --- CLI -----------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch, convert and import NYT US House/Senate results.",
        epilog=(
            f"Also: '{CURRENT_SENATE_COMMAND}' rebuilds the Current Senate "
            f"snapshot; run it with --help for its options."
        ),
    )
    parser.add_argument("command", choices=("fetch", "convert", "import", "all"))
    parser.add_argument("--year", type=int, required=True, choices=supported_years())
    parser.add_argument("--chamber", required=True, choices=("house", "senate"))
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Update existing elections in place (keeps their ids)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write nothing (no cache, files or DB rows); print what would change",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Read the latest cached files instead of downloading (fetch, all)",
    )
    parser.add_argument("--cache-dir", type=Path, default=config.NYT_CACHE_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser


def _current_senate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"cli.py {CURRENT_SENATE_COMMAND}",
        description=(
            "Rebuild the Current Senate snapshot from the 2026 Senate election "
            "file. Never touches the database."
        ),
    )
    parser.add_argument(
        "--snapshot", type=Path, default=DEFAULT_SNAPSHOT, help="Input snapshot"
    )
    parser.add_argument(
        "--election-file",
        type=Path,
        default=DEFAULT_ELECTION_FILE,
        help="Converted 2026 Senate file",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="Output (default: the snapshot)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Write nothing; print the counts"
    )
    return parser


def _run_current_senate(argv: Sequence[str]) -> int:
    args = _current_senate_parser().parse_args(argv)
    try:
        report = rebuild_current_senate(
            snapshot_path=args.snapshot,
            election_path=args.election_file,
            out_path=args.out,
            dry_run=args.dry_run,
        )
    except (CurrentSenateError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(report)
    return 0


def main(argv: Sequence[str] | None = None, *, db: Database | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).
        db: Database to use; defaults to the configured one.

    Returns:
        The process exit code: 0 on success, 1 on a fetch, parse, convert or
        import error, or (``current-senate``) a Current Senate build or file
        error.
    """
    tokens = list(sys.argv[1:] if argv is None else argv)
    if tokens and tokens[0] == CURRENT_SENATE_COMMAND:
        # Its options differ from the per-cycle commands' (no year or chamber).
        return _run_current_senate(tokens[1:])
    args = _parser().parse_args(tokens)
    chamber: Chamber = args.chamber
    year: int = args.year
    try:
        return _run(args, year=year, chamber=chamber, db=db)
    except (
        FetchError,
        AdapterError,
        ConvertError,
        UnknownSourceError,
        CliError,
    ) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except SystemExit as err:
        # The seed importers report bad input as SystemExit with a message.
        if err.code is None or err.code == 0:
            raise
        print(f"error: {err.code}", file=sys.stderr)
        return 1


def _run(
    args: argparse.Namespace,
    *,
    year: int,
    chamber: Chamber,
    db: Database | None,
) -> int:
    command: str = args.command
    if command == "fetch":
        if args.dry_run:
            general_source, runoff_sources = cycle_sources(year, chamber)
            for source in (general_source, *runoff_sources):
                available = source.status is SourceStatus.AVAILABLE
                state = source.url if available else "pending"
                print(f"would fetch {source.kind} {year}/{chamber}: {state}")
            return 0
        fetch_cycle(year, chamber, args.cache_dir, offline=args.offline)
        return 0

    database = db if db is not None else Database()
    if command == "import":
        print(
            import_files(
                database,
                year=year,
                chamber=chamber,
                out_dir=args.out_dir,
                refresh=args.refresh,
                dry_run=args.dry_run,
            ),
        )
        return 0

    # convert reads the cache; all downloads first unless --offline. A dry run
    # downloads into a throwaway folder so the real cache is left alone.
    offline = command == "convert" or args.offline
    if command == "all" and not args.refresh and not args.dry_run:
        # Refuse before writing any file, rather than leave files the import rejects.
        for special in (False, True) if chamber == "senate" else (False,):
            name = election_name(year, chamber, special=special)
            if database.get_election_by_name(name) is not None:
                raise CliError(_EXISTS_MESSAGE.format(name=name))
    with tempfile.TemporaryDirectory(prefix="nyt-dry-run-") as scratch:
        cache_dir = Path(scratch) if args.dry_run and not offline else args.cache_dir
        general, runoffs = fetch_cycle(year, chamber, cache_dir, offline=offline)
        try:
            result = convert_cycle(general, runoffs, out_dir=args.out_dir)
        except (KeyError, TypeError, ValueError) as err:
            raise CliError(f"could not convert {year} {chamber}: {err!r}") from err
    print(
        report_conversion(
            result,
            year=year,
            chamber=chamber,
            out_dir=args.out_dir,
            db=database,
        ),
    )
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    # Refuse rather than leave a specials file the new main file does not name.
    refuse_stale_special(
        result,
        year=year,
        chamber=chamber,
        out_dir=args.out_dir,
        db=database,
    )
    written = write_conversion(result, year=year, chamber=chamber, out_dir=args.out_dir)
    for path in written:
        print(f"wrote {path}")
    if command == "all":
        print(
            import_files(
                database,
                year=year,
                chamber=chamber,
                out_dir=args.out_dir,
                refresh=args.refresh,
                dry_run=False,
                report=False,
            ),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
