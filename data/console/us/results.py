"""US House and Senate results from NYT: form -> preview -> confirm, then export.

The console face of ``importers/results/us/nyt/cli.py``, reusing its steps rather than
repeating them:

- **Form** (GET ``/us/results``): pick a cycle year and chamber, and whether to
  fetch the latest NYT files or read the newest cached snapshot (offline). The
  page lists what the cache holds for every source.
- **Preview** (POST ``/us/results/preview``): fetch (or read the cache), parse,
  apply runoffs, convert, and run every check the import makes, writing nothing
  to the database or the out-dir (a fresh fetch does add a cache snapshot).
  Every stage's numbers are shown: the files read and their times, a row per
  race, the party mapping, warnings, the diff against the committed file and the
  database, and whether the import creates or refreshes each election. The
  conversion is held under a one-time token.
- **Confirm** (POST ``/us/results/confirm/<token>``): write the converted
  file(s) to the out-dir and import them, refreshing existing elections in
  place. Every file is checked before anything is written, and the CLI's
  import plans every file before importing any.
- **Export** (POST ``/us/results/export``): a separate button that runs the
  site export. Importing never exports.
- **Rebuild Current Senate** (POST ``/us/results/current-senate``): a separate
  button that re-derives ``senate-current.json`` from the imported 2026 Senate
  file (``importers/results/us/nyt/current_senate.py``). It writes one file, never
  touches the database, and does not run during an import.

The out-dir and the NYT cache come from the app config keys
:data:`OUT_DIR_CONFIG` and :data:`CACHE_DIR_CONFIG`, defaulting to the CLI's
``inputs/us`` and ``core.config.NYT_CACHE_DIR``; the Current Senate file
from :data:`SNAPSHOT_CONFIG`, defaulting to the committed snapshot. Tests point
all three at a temporary folder.

Import, export and the Current Senate rebuild all hold the console's one US model-run slot
(:func:`~console.us.models.model_run_slot`): the export publishes
every US chamber's trend history, which a running history rebuild may have
half-cleared, and a double-submitted confirm must not import twice at once.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue

from console.db import get_db
from console.paths import EXPORT_ELECTION_SCRIPT
from console.shared.preview import (
    PREVIEW_CACHE,
    get_preview,
    pop_preview,
    store_preview,
)
from console.shared.runner import render_command_result, run_python_script
from console.us.models import busy_message, model_run_slot
from core import config
from core.db import Database
from importers.results.us.nyt.adapters import AdapterError
from importers.results.us.nyt.cli import (
    DEFAULT_OUT_DIR,
    CliError,
    DiffReport,
    SeatResult,
    check_import,
    convert_cycle,
    diff_against_existing,
    election_name,
    fetch_cycle,
    import_files,
    parse_cycle,
    read_election_file,
    refuse_stale_special,
    seat_results_from_file,
    snapshot_election,
    validate_election_file,
    write_conversion,
)
from importers.results.us.nyt.convert import (
    META_KEY,
    ConversionResult,
    ConvertError,
    output_name,
)
from importers.results.us.nyt.current_senate import (
    DEFAULT_SNAPSHOT,
    CurrentSenateError,
    rebuild_current_senate,
)
from importers.results.us.nyt.fetch import FetchError, cached_snapshot_info
from importers.results.us.nyt.races import Race
from importers.results.us.nyt.sources import (
    SOURCES,
    Chamber,
    SourceStatus,
    UnknownSourceError,
    supported_years,
)
from importers.results.us.us_import import PARTY_KEY_TO_NAME

bp = Blueprint("us_results", __name__)

OUT_DIR_CONFIG = "US_RESULTS_OUT_DIR"
CACHE_DIR_CONFIG = "US_RESULTS_CACHE_DIR"
SNAPSHOT_CONFIG = "US_RESULTS_CURRENT_SENATE"
# The converted 2026 Senate election, in the out-dir.
CURRENT_SENATE_ELECTION_FILE = output_name(2026, "senate")

# The same full export as the Admin page's "Rebuild Site Data" and its timeout.
EXPORT_TIMEOUT_SECONDS = 900
EXPORT_COMMAND_LABEL = "export_elections.py (rebuild site data)"
CURRENT_SENATE_COMMAND_LABEL = "cli.py current-senate (rebuild senate-current.json)"

PREVIEW_TYPE = "us_results"
_EXPIRED_MESSAGE = "Preview expired. Please preview again."
FETCH_LATEST = "fetch"
USE_CACHE = "cache"
_SOURCE_MODES = (FETCH_LATEST, USE_CACHE)
_CHAMBERS: dict[str, Chamber] = {"house": "house", "senate": "senate"}
_CHAMBER_LABELS: dict[Chamber, str] = {"house": "House", "senate": "Senate"}

# Change labels for a seat against the database election it would refresh.
CHANGE_NEW = "new"
CHANGE_WINNER = "winner changed"
CHANGE_CALLED = "newly called"
CHANGE_UNCALLED = "newly uncalled"
CHANGE_COUNT = "count updated"
CHANGE_NONE = "unchanged"
NOT_IMPORTED = "not imported (House special)"


# --- view models -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """What the NYT cache holds for one source.

    Attributes:
        year: Cycle year.
        chamber: ``"house"`` or ``"senate"``.
        kind: ``"general"`` or ``"runoff"``.
        pending: The contest has not been held, so there is nothing to fetch.
        url: The source's URL, when it has one.
        retrieved_at: When the newest snapshot was downloaded, if cached.
        source_updated_at: The feed's own updated-at value for that snapshot.
        problem: Why an offline read would refuse the cached snapshot, when a
            pointer exists but is unusable.
    """

    year: int
    chamber: Chamber
    kind: str
    pending: bool
    url: str | None
    retrieved_at: str | None
    source_updated_at: str | None
    problem: str | None = None


@dataclass(frozen=True, slots=True)
class FileTarget:
    """One converted file and the election it imports into.

    Attributes:
        file_name: The file written into the out-dir.
        election_name: The database election it imports into.
        existing_id: That election's id when it exists (the import refreshes
            it in place), ``None`` when the import creates it.
        seats: Seats in the file.
        called: Seats with a called winner.
        tally: Called seats per party key, most first.
        source_note: The note stored with the election and shown under the map.
        diff: The file against the committed file and the database election.
    """

    file_name: str
    election_name: str
    existing_id: int | None
    seats: int
    called: int
    tally: tuple[tuple[str, int], ...]
    source_note: str
    diff: DiffReport


@dataclass(frozen=True, slots=True)
class RaceRow:
    """One race as the preview shows it.

    Attributes:
        seat: Seat key (``ST-NN`` or the state's name).
        file: ``"main"``, ``"special"`` (the clashing-specials file) or
            ``NOT_IMPORTED`` (a House special, which the converter leaves out).
        special: The race is a special election.
        leader: Top candidate's name.
        leader_party: Top candidate's NYT party code.
        leader_key: The project party key that code maps to.
        runner_up: Second candidate's name, if any.
        runner_up_party: Second candidate's NYT party code.
        margin_pct: Leader's lead over the runner-up as a share of all votes
            counted, or ``None`` before any are counted.
        pct_counted: NYT's share of the expected vote counted.
        called: A winner is called.
        winner: The called winner's party key.
        decisive_round: ``None``, ``"rcv-final"`` or ``"runoff"``.
        votes_source: Where the stored votes come from (``nyt``, ``538``,
            ``nominal``).
        same_party: The top two share a party key.
        change: Against the database election (see the ``CHANGE_*`` labels),
            or ``NOT_IMPORTED``.
    """

    seat: str
    file: str
    special: bool
    leader: str
    leader_party: str
    leader_key: str
    runner_up: str | None
    runner_up_party: str | None
    margin_pct: float | None
    pct_counted: float | None
    called: bool
    winner: str | None
    decisive_round: str | None
    votes_source: str | None
    same_party: bool
    change: str


@dataclass(frozen=True, slots=True)
class PartyRow:
    """One NYT party code and where its candidates' votes go.

    Attributes:
        nyt_party: The code as NYT gives it.
        party_key: The project party key it maps to.
        db_party: The database party that key loads into.
        candidates: Candidates running under the code.
        votes: Their votes.
    """

    nyt_party: str
    party_key: str
    db_party: str
    candidates: int
    votes: int


@dataclass(frozen=True, slots=True)
class ImportedElection:
    """One election as it stands after the import.

    Attributes:
        name: Election name.
        election_id: Its id.
        map_id: Its map.
        file: The file it was imported from.
        seats: Seats with votes.
        called: Seats with a called winner.
        elected: Called seats per party key, most first.
        status_rows: Per-seat status rows stored.
        source_note: The stored source note.
    """

    name: str
    election_id: int
    map_id: int
    file: Path
    seats: int
    called: int
    elected: tuple[tuple[str, int], ...]
    status_rows: int
    source_note: str | None


# --- routes ----------------------------------------------------------------------


@bp.route("/us/results", methods=["GET"])
def us_results_form() -> str:
    """GET /us/results — Choose a cycle and chamber to import NYT results for."""
    return _render_form()


@bp.route("/us/results/preview", methods=["POST"])
def us_results_preview() -> ResponseReturnValue:
    """POST /us/results/preview — Fetch, convert and check results, writing nothing.

    Form parameters:
        year (int): A supported cycle year.
        chamber (str): ``house`` or ``senate``.
        source (str): ``fetch`` (download the latest NYT files into the cache)
            or ``cache`` (read the newest cached snapshot).

    Returns:
        The preview page with a confirm form, or the form again with the error
        (a 400 for an invalid choice).
    """
    year_text = request.form.get("year", "")
    chamber_text = request.form.get("chamber", "")
    mode = request.form.get("source", "")
    chamber = _CHAMBERS.get(chamber_text)
    year = int(year_text) if year_text.isdecimal() else None
    if (
        chamber is None
        or year is None
        or year not in supported_years()
        or mode not in _SOURCE_MODES
    ):
        flash("Choose a year, a chamber, and whether to fetch or use the cache.")
        return _render_form(year=year, chamber=chamber_text, mode=mode), 400

    db = get_db()
    out_dir = _out_dir()
    fetch_log: list[str] = []
    try:
        general, runoffs = fetch_cycle(
            year,
            chamber,
            _cache_dir(),
            offline=mode == USE_CACHE,
            echo=fetch_log.append,
        )
        races = parse_cycle(general, runoffs)
        result = convert_cycle(general, runoffs, out_dir=out_dir, races=races)
        targets = _check_and_describe(
            db, result, year=year, chamber=chamber, out_dir=out_dir,
        )
    except (
        FetchError,
        AdapterError,
        ConvertError,
        UnknownSourceError,
        CliError,
        OSError,
    ) as err:
        flash(f"Preview failed: {err}")
        return _render_form(year=year, chamber=chamber, mode=mode)
    except (KeyError, TypeError, ValueError) as err:
        # The CLI reports the same: a file the adapters accepted but the
        # converter could not shape.
        flash(f"Preview failed: could not convert {year} {chamber}: {err!r}")
        return _render_form(year=year, chamber=chamber, mode=mode)

    rows = _race_rows(db, races, result, year=year, chamber=chamber)
    token = store_preview(
        {"type": PREVIEW_TYPE, "year": year, "chamber": chamber, "result": result},
    )
    return render_template(
        "us_results_preview.html",
        title=f"Preview {year} US {_CHAMBER_LABELS[chamber]} results",
        token=token,
        year=year,
        chamber=chamber,
        chamber_label=_CHAMBER_LABELS[chamber],
        mode=mode,
        out_dir=out_dir,
        fetches=[general, *runoffs],
        fetch_log=fetch_log,
        pipeline=_pipeline_counts(races, rows, files_checked=len(targets)),
        targets=targets,
        specials_note=_specials_note(db, result, year=year, chamber=chamber),
        rows=rows,
        uncalled=[
            row.seat for row in rows if row.file != NOT_IMPORTED and not row.called
        ],
        change_counts=Counter(
            row.change for row in rows if row.file != NOT_IMPORTED
        ).most_common(),
        not_imported=[row.seat for row in rows if row.file == NOT_IMPORTED],
        parties=_party_rows(races),
        warnings=list(result.warnings),
    )


@bp.route("/us/results/confirm/<token>", methods=["POST"])
def us_results_confirm(token: str) -> ResponseReturnValue:
    """POST /us/results/confirm/<token> — Write the previewed files and import them.

    Existing elections are refreshed in place (their ids kept). A failure
    keeps the token, so the same preview can be confirmed again; the files
    written for it are put back as they were. A preview older than the
    results already in the out-dir is refused, so it cannot overwrite them.
    Once one preview of a year and chamber is imported, every other preview
    of it is dropped.

    Args:
        token: One-time token naming the cached conversion.

    Returns:
        The result page (with the separate export button), or a redirect to
        the form with a flash on an unknown token or a failed import.
    """
    if _cached_import(token) is None:
        flash(_EXPIRED_MESSAGE)
        return redirect(url_for("us_results.us_results_form"))

    out_dir = _out_dir()
    db = get_db()
    with model_run_slot() as free:
        if not free:
            flash(busy_message("Results not imported"))
            return redirect(url_for("us_results.us_results_form"))
        # Read again now the slot is held: a double submit that got past the
        # check above while the first request was importing finds it gone.
        cached = _cached_import(token)
        if cached is None:
            flash(_EXPIRED_MESSAGE)
            return redirect(url_for("us_results.us_results_form"))
        year: int = cached["year"]
        chamber: Chamber = cached["chamber"]
        result: ConversionResult = cached["result"]
        try:
            _refuse_older_preview(result, year=year, chamber=chamber, out_dir=out_dir)
            written, report = _write_and_import(
                db, result, year=year, chamber=chamber, out_dir=out_dir,
            )
        except (CliError, OSError) as err:
            flash(f"Import failed: {err}")
            return redirect(url_for("us_results.us_results_form"))
        # Still inside the slot, so a second submit of this token finds it gone.
        pop_preview(token)
        _drop_previews_for(year, chamber)

    elections = _imported_elections(db, written, year=year, chamber=chamber)
    flash(
        f"Imported {year} US {_CHAMBER_LABELS[chamber]} results: "
        + "; ".join(f"{e.name} (id {e.election_id})" for e in elections),
    )
    return render_template(
        "us_results_result.html",
        title=f"Imported {year} US {_CHAMBER_LABELS[chamber]} results",
        year=year,
        chamber_label=_CHAMBER_LABELS[chamber],
        written=written,
        elections=elections,
        report=report,
    )


@bp.route("/us/results/export", methods=["POST"])
def us_results_export() -> ResponseReturnValue:
    """POST /us/results/export — Run the site export (``export_elections.py``).

    The same full export as the Admin page's Rebuild Site Data, run separately from the
    import so a provisional refresh is only published on purpose.

    Returns:
        The command result, or a redirect to the form when the script is
        missing or another US run holds the slot.
    """
    if not EXPORT_ELECTION_SCRIPT.exists():
        flash(f"Export script not found: {EXPORT_ELECTION_SCRIPT}")
        return redirect(url_for("us_results.us_results_form"))
    with model_run_slot() as free:
        if not free:
            flash(busy_message("Not exported"))
            return redirect(url_for("us_results.us_results_form"))
        try:
            export = run_python_script(
                EXPORT_ELECTION_SCRIPT, timeout=EXPORT_TIMEOUT_SECONDS,
            )
        except (subprocess.SubprocessError, OSError) as err:
            stdout, stderr, code = "", f"{type(err).__name__}: {err}", 1
        else:
            stdout, stderr, code = export.stdout, export.stderr, export.returncode
    return render_command_result(
        title="Export US Results to Site",
        command=EXPORT_COMMAND_LABEL,
        stdout=stdout,
        stderr=stderr,
        return_code=code,
        back_endpoint="us_results.us_results_form",
        back_label="Back to US results",
    )


@bp.route("/us/results/current-senate", methods=["POST"])
def us_results_current_senate() -> ResponseReturnValue:
    """POST /us/results/current-senate — Re-derive the Current Senate snapshot.

    Reads the committed Current Senate file and the imported 2026 Senate
    election file in the out-dir and rewrites the Current Senate file from
    them (holdovers plus the 2026 winners, uncalled seats left unknown). The
    result is the same however often it is run. Nothing is exported; run the
    export afterwards to publish it.

    Returns:
        The command result with the build report, or a redirect to the form
        when another US run holds the slot.
    """
    snapshot = Path(current_app.config.get(SNAPSHOT_CONFIG, DEFAULT_SNAPSHOT))
    election = _out_dir() / CURRENT_SENATE_ELECTION_FILE
    with model_run_slot() as free:
        if not free:
            flash(busy_message("Current Senate not rebuilt"))
            return redirect(url_for("us_results.us_results_form"))
        try:
            report = rebuild_current_senate(snapshot_path=snapshot, election_path=election)
        except (CurrentSenateError, OSError) as err:
            stdout, stderr, code = "", f"error: {err}", 1
        else:
            stdout, stderr, code = report, "", 0
    return render_command_result(
        title="Rebuild Current Senate",
        command=CURRENT_SENATE_COMMAND_LABEL,
        stdout=stdout,
        stderr=stderr,
        return_code=code,
        back_endpoint="us_results.us_results_form",
        back_label="Back to US results",
    )


# --- steps -----------------------------------------------------------------------


def _cached_import(token: str) -> dict[str, Any] | None:
    """The cached preview for ``token``, if it is one of this flow's."""
    cached = get_preview(token)
    if cached is None or cached.get("type") != PREVIEW_TYPE:
        return None
    return cached


def _out_dir() -> Path:
    return Path(current_app.config.get(OUT_DIR_CONFIG, DEFAULT_OUT_DIR))


def _cache_dir() -> Path:
    return Path(current_app.config.get(CACHE_DIR_CONFIG, config.NYT_CACHE_DIR))


def _files(result: ConversionResult) -> list[tuple[dict[str, Any], bool]]:
    """The conversion's files as ``(content, is_specials_file)``, main first."""
    files: list[tuple[dict[str, Any], bool]] = [(result.main, False)]
    if result.special is not None:
        files.append((result.special, True))
    return files


def _check(
    db: Database,
    result: ConversionResult,
    *,
    year: int,
    chamber: Chamber,
    out_dir: Path,
) -> None:
    """Run every check the write and import make, writing nothing.

    Raises:
        CliError: A leftover specials file, or a file the importer would refuse.
    """
    refuse_stale_special(result, year=year, chamber=chamber, out_dir=out_dir, db=db)
    for data, special in _files(result):
        name = output_name(year, chamber, special=special)
        validate_election_file(data, chamber, what=name)
        check_import(
            db,
            data,
            name=election_name(year, chamber, special=special),
            year=year,
            chamber=chamber,
            what=name,
        )


def _check_and_describe(
    db: Database,
    result: ConversionResult,
    *,
    year: int,
    chamber: Chamber,
    out_dir: Path,
) -> list[FileTarget]:
    """Check a conversion and describe each file against what it replaces."""
    _check(db, result, year=year, chamber=chamber, out_dir=out_dir)
    targets: list[FileTarget] = []
    for data, special in _files(result):
        file_name = output_name(year, chamber, special=special)
        name = election_name(year, chamber, special=special)
        existing = db.get_election_by_name(name)
        results = seat_results_from_file(data)
        targets.append(
            FileTarget(
                file_name=file_name,
                election_name=name,
                existing_id=None if existing is None else existing.id,
                seats=len(results),
                called=sum(seat.called for seat in results.values()),
                tally=_tally(results.values()),
                source_note=str(data[META_KEY]["source_note"]),
                diff=diff_against_existing(
                    data,
                    read_election_file(out_dir / file_name, chamber),
                    snapshot_election(db, name),
                    file_label=f"committed {file_name}",
                ),
            ),
        )
    return targets


def _write_and_import(
    db: Database,
    result: ConversionResult,
    *,
    year: int,
    chamber: Chamber,
    out_dir: Path,
) -> tuple[list[Path], str]:
    """Check, write and import a conversion, refreshing existing elections.

    Returns:
        The files written and the import's report.

    Raises:
        CliError: A check or the import refused the files.
    """
    # Checked again: the database or the out-dir may have moved on since the
    # preview, and a refused file must not be written.
    _check(db, result, year=year, chamber=chamber, out_dir=out_dir)
    paths = [
        out_dir / output_name(year, chamber, special=special)
        for _, special in _files(result)
    ]
    previous = {path: path.read_bytes() if path.exists() else None for path in paths}
    created_dirs = _missing_dirs(out_dir)
    imported: list[Path] = []
    try:
        written = write_conversion(result, year=year, chamber=chamber, out_dir=out_dir)
        report = import_files(
            db,
            year=year,
            chamber=chamber,
            out_dir=out_dir,
            refresh=True,
            dry_run=False,
            report=False,
            on_imported=imported.append,
        )
    # Any failure, a database error included: a file must not stay ahead of its
    # election, so each file whose import did not land is put back before the
    # error is reported. A file whose election did import stays, matching it.
    except (Exception, SystemExit) as err:
        # The seed importers report bad input as SystemExit with a message.
        message = str(err.code) if isinstance(err, SystemExit) else str(err)
        if not isinstance(err, (CliError, SystemExit)):
            message = f"{type(err).__name__}: {message}"
        to_restore = {
            path: old for path, old in previous.items() if path not in imported
        }
        problems = _restore_files(
            to_restore, () if imported else created_dirs,
        )
        raise CliError(
            _failure_message(message, imported, to_restore, problems),
        ) from err
    return written, report


def _failure_message(
    message: str,
    imported: Sequence[Path],
    restored: Iterable[Path],
    problems: Sequence[str],
) -> str:
    """Say what a failed import left: which files imported, which were put back."""
    names = ", ".join(path.name for path in restored)
    if problems:
        undone = f"putting {names} back failed: " + "; ".join(problems)
        if not imported:
            undone = undone[0].upper() + undone[1:]
    elif imported:
        undone = f"{names} restored"
    else:
        undone = "The out-dir files were put back as they were."
    if not imported:
        return f"{message}. {undone}"
    landed = ", ".join(path.name for path in imported)
    return (
        f"{landed} imported; {names} import failed: {message}; {undone}; "
        "re-run to retry"
    )


def _missing_dirs(folder: Path) -> list[Path]:
    """``folder`` and every missing parent a write would create, deepest first."""
    missing: list[Path] = []
    while not folder.exists() and folder != folder.parent:
        missing.append(folder)
        folder = folder.parent
    return missing


def _restore_files(
    previous: dict[Path, bytes | None],
    created_dirs: Sequence[Path],
) -> list[str]:
    """Put out-dir files back as they were before a failed import.

    Args:
        previous: Each file's bytes before the write, ``None`` if it was absent.
        created_dirs: Folders the write created, deepest first, to remove
            again; empty when a file in them is kept.

    Returns:
        A line per file that could not be put back; empty when all were.
    """
    problems: list[str] = []
    for path, old in previous.items():
        try:
            if old is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(old)
        except OSError as err:
            problems.append(f"{path.name}: {err}")
    if problems:
        # A file that could not be put back stays, and so do its folders.
        return problems
    for folder in created_dirs:
        try:
            folder.rmdir()
        except OSError as err:
            problems.append(f"{folder}: {err}")
            break
    return problems


def _refuse_older_preview(
    result: ConversionResult,
    *,
    year: int,
    chamber: Chamber,
    out_dir: Path,
) -> None:
    """Refuse a preview whose files are older than those already in the out-dir.

    A preview confirmed after a newer one of the same year and chamber would
    otherwise overwrite the newer results.

    Raises:
        CliError: If a file on disk was retrieved after the previewed one.
    """
    for data, special in _files(result):
        name = output_name(year, chamber, special=special)
        on_disk = read_election_file(out_dir / name, chamber)
        if on_disk is None:
            continue
        disk_time, preview_time = _retrieved_at(on_disk), _retrieved_at(data)
        if disk_time is None or preview_time is None:
            continue
        if disk_time > preview_time:
            raise CliError(
                f"{name} already holds results retrieved {disk_time.isoformat()}, "
                f"newer than this preview's {preview_time.isoformat()}; "
                "preview again",
            )


def _retrieved_at(data: dict[str, Any]) -> datetime | None:
    """A converted file's ``meta.retrieved_at``, when it has a usable one."""
    meta = data.get(META_KEY)
    value = meta.get("retrieved_at") if isinstance(meta, dict) else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _drop_previews_for(year: int, chamber: Chamber) -> None:
    """Drop every other cached preview of ``year``/``chamber`` once one is imported."""
    for token, payload in list(PREVIEW_CACHE.items()):
        if (
            payload.get("type") == PREVIEW_TYPE
            and payload.get("year") == year
            and payload.get("chamber") == chamber
        ):
            pop_preview(token)


def _imported_elections(
    db: Database,
    written: Sequence[Path],
    *,
    year: int,
    chamber: Chamber,
) -> list[ImportedElection]:
    elections: list[ImportedElection] = []
    for path, special in zip(written, (False, True), strict=False):
        name = election_name(year, chamber, special=special)
        election = db.get_election_by_name(name)
        if election is None:
            raise CliError(f"{name!r} is missing after its import")
        results = snapshot_election(db, name) or {}
        elections.append(
            ImportedElection(
                name=name,
                election_id=election.id,
                map_id=election.map_id,
                file=path,
                seats=len(results),
                called=sum(seat.called for seat in results.values()),
                elected=_tally(results.values()),
                status_rows=len(db.get_seat_statuses(election.id)),
                source_note=election.source_note,
            ),
        )
    return elections


# --- preview details -------------------------------------------------------------


def _tally(results: Iterable[SeatResult]) -> tuple[tuple[str, int], ...]:
    """Called seats per party key, most first."""
    counts = Counter(
        seat.winner for seat in results if seat.called and seat.winner is not None
    )
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _race_rows(
    db: Database,
    races: Sequence[Race],
    result: ConversionResult,
    *,
    year: int,
    chamber: Chamber,
) -> list[RaceRow]:
    main_seats = seat_results_from_file(result.main)
    special_seats: dict[str, SeatResult] = {}
    if result.special is not None:
        special_seats = seat_results_from_file(result.special)
    main_db = snapshot_election(db, election_name(year, chamber))
    special_db = snapshot_election(db, election_name(year, chamber, special=True))

    rows: list[RaceRow] = []
    for race in sorted(races, key=lambda r: (r.seat_key, r.is_special)):
        in_special = race.is_special and race.seat_key in special_seats
        if in_special:
            file, data, after, before = (
                "special",
                result.special,
                special_seats[race.seat_key],
                (special_db or {}).get(race.seat_key),
            )
        elif race.seat_key in main_seats and not (
            chamber == "house" and race.is_special
        ):
            file, data, after, before = (
                "main",
                result.main,
                main_seats[race.seat_key],
                (main_db or {}).get(race.seat_key),
            )
        else:
            file, data, after, before = (NOT_IMPORTED, None, None, None)
        rows.append(_race_row(race, file, data, before, after))
    return rows


def _race_row(
    race: Race,
    file: str,
    data: dict[str, Any] | None,
    before: SeatResult | None,
    after: SeatResult | None,
) -> RaceRow:
    leader = race.candidates[0]
    runner_up = race.candidates[1] if len(race.candidates) > 1 else None
    total = sum(candidate.votes for candidate in race.candidates)
    margin = None
    if total > 0:
        second = runner_up.votes if runner_up is not None else 0
        margin = (leader.votes - second) / total * 100
    seat_info: dict[str, Any] = {}
    if data is not None:
        seat_info = data[race.seat_key]["seatInfo"]
    return RaceRow(
        seat=race.seat_key,
        file=file,
        special=race.is_special,
        leader=leader.name,
        leader_party=leader.nyt_party,
        leader_key=leader.party_key,
        runner_up=None if runner_up is None else runner_up.name,
        runner_up_party=None if runner_up is None else runner_up.nyt_party,
        margin_pct=margin,
        pct_counted=race.pct_counted,
        called=race.called,
        winner=None if after is None else after.winner,
        decisive_round=race.decisive_round,
        votes_source=seat_info.get("votesSource"),
        same_party=race.same_party,
        change=NOT_IMPORTED if after is None else _change(before, after),
    )


def _change(before: SeatResult | None, after: SeatResult) -> str:
    """How a seat moves against the database election it would refresh."""
    if before is None:
        return CHANGE_NEW
    if before.called and after.called and before.winner != after.winner:
        return CHANGE_WINNER
    if not before.called and after.called:
        return CHANGE_CALLED
    if before.called and not after.called:
        return CHANGE_UNCALLED
    if before.totals != after.totals or before.pct != after.pct:
        return CHANGE_COUNT
    return CHANGE_NONE


def _party_rows(races: Sequence[Race]) -> list[PartyRow]:
    candidates: Counter[tuple[str, str]] = Counter()
    votes: Counter[tuple[str, str]] = Counter()
    for race in races:
        for candidate in race.candidates:
            key = (candidate.nyt_party, candidate.party_key)
            candidates[key] += 1
            votes[key] += candidate.votes
    return [
        PartyRow(
            nyt_party=nyt_party,
            party_key=party_key,
            db_party=PARTY_KEY_TO_NAME.get(party_key, "?"),
            candidates=count,
            votes=votes[(nyt_party, party_key)],
        )
        for (nyt_party, party_key), count in sorted(
            candidates.items(), key=lambda item: (item[0][1], -item[1], item[0][0]),
        )
    ]


def _pipeline_counts(
    races: Sequence[Race],
    rows: Sequence[RaceRow],
    *,
    files_checked: int,
) -> list[tuple[str, int]]:
    """The stage-by-stage counts shown at the top of the preview."""
    return [
        ("races parsed (runoffs applied)", len(races)),
        ("decided by a runoff", sum(r.decisive_round == "runoff" for r in races)),
        (
            "final ranked-choice round",
            sum(r.decisive_round == "rcv-final" for r in races),
        ),
        ("special elections", sum(race.is_special for race in races)),
        ("uncontested", sum(race.uncontested for race in races)),
        ("called", sum(race.called for race in races)),
        ("uncalled", sum(not race.called for race in races)),
        ("in the main file", sum(row.file == "main" for row in rows)),
        ("in the specials file", sum(row.file == "special" for row in rows)),
        (
            "not imported (House specials)",
            sum(row.file == NOT_IMPORTED for row in rows),
        ),
        ("files that passed the file and import checks", files_checked),
    ]


def _specials_note(
    db: Database,
    result: ConversionResult,
    *,
    year: int,
    chamber: Chamber,
) -> str:
    if chamber == "house":
        return (
            "House: no specials file. A same-day House special would contest a "
            "seat twice, so the converter drops it (listed under warnings)."
        )
    regular = election_name(year, chamber)
    special = election_name(year, chamber, special=True)
    if result.special is not None:
        return (
            f"{output_name(year, chamber, special=True)} is written beside the main "
            f"file and imported as {special!r}, a child of {regular!r}: specials "
            "held in a state that also holds its regular race. Standalone specials "
            "fill their state's seat in the main file."
        )
    note = (
        "No specials file: every special this cycle is standalone and fills its "
        "state's seat in the main file."
    )
    if db.get_election_by_name(special) is not None:
        note += (
            f" The database still holds {special!r}, which no file names; it will "
            "not be refreshed."
        )
    return note


# --- form ------------------------------------------------------------------------


def _render_form(
    *,
    year: int | None = None,
    chamber: str = "house",
    mode: str = FETCH_LATEST,
) -> str:
    years = sorted(supported_years(), reverse=True)
    return render_template(
        "us_results_form.html",
        title="Import US Results",
        years=years,
        chambers=list(_CHAMBER_LABELS.items()),
        selected_year=year if year in years else years[0],
        selected_chamber=chamber if chamber in _CHAMBERS else "house",
        selected_mode=mode if mode in _SOURCE_MODES else FETCH_LATEST,
        cache_dir=_cache_dir(),
        out_dir=_out_dir(),
        senate_file=_out_dir() / CURRENT_SENATE_ELECTION_FILE,
        cache=cache_entries(_cache_dir()),
    )


def cache_entries(cache_dir: Path) -> list[CacheEntry]:
    """Read what the NYT cache holds for every listed source, newest cycle first.

    Each source's pointer is checked as an offline read would check it, but the
    snapshot itself is not read. A pointer an offline read would refuse (cached
    from an old URL, naming a missing snapshot, corrupt) is listed with why.

    Args:
        cache_dir: Root of the cache.

    Returns:
        One entry per source.
    """
    entries: list[CacheEntry] = []
    for (year, chamber, kind), source in sorted(
        SOURCES.items(), key=lambda item: (-item[0][0], item[0][1], item[0][2]),
    ):
        pending = source.status is SourceStatus.PENDING
        info = problem = None
        if not pending:
            try:
                info = cached_snapshot_info(source, cache_dir)
            except (FetchError, OSError) as err:
                problem = f"unusable: {err}"
        entries.append(
            CacheEntry(
                year=year,
                chamber=chamber,
                kind=kind,
                pending=pending,
                url=source.url,
                retrieved_at=None if info is None else info.retrieved_at.isoformat(),
                source_updated_at=None if info is None else info.source_updated_at,
                problem=problem,
            ),
        )
    return entries
