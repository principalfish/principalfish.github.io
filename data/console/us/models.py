"""Chamber wiring for the US console, and the model-run sequence.

:data:`US_CHAMBERS` is the single description of the three US election types —
their model runners, forecast/baseline election types, trend caches and the
poll-queue contests each one imports. The US blueprint re-exports it for the
per-chamber output routes it registers.

:func:`run_us_chamber_and_export` runs one chamber's runner and then, optionally,
the static export. It is Flask-free so it can be called from the end of the
poll-review queue, and it takes its subprocess runner as an argument so tests
never shell out. It never takes the run slot itself: it runs inside a background
job, or under the caller's own hold of :func:`model_run_slot`. It serves each
chamber's model page, the matchup pages' "rebuild history" option and the poll
queue's "run model at end" option.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from console.paths import (
    EXPORT_ELECTION_SCRIPT,
    US_HOUSE_MODEL_SCRIPT,
    US_HOUSE_TREND_CACHE_JSON,
    US_PRESIDENT_MODEL_SCRIPT,
    US_PRESIDENT_TREND_CACHE_JSON,
    US_SENATE_MODEL_SCRIPT,
    US_SENATE_TREND_CACHE_JSON,
)
from console.shared.run_log import section_context
from console.shared.runner import run_python_script
from core.db import Database
from core.schema import ElectionType

STEP_TIMEOUT_SECONDS = 300

# A rebuild recomputes one projection per day of trend history, and the runner
# deletes the old points before it starts, so a timeout there leaves history
# half-written rather than merely stale. The shipped President series already
# spans ~21 months (December 2024 onwards) and only grows; at an estimated few
# hundred milliseconds to a couple of seconds per day (each day re-reads the
# map's polls and writes a full projection), that is several minutes today and
# past 300 s within the cycle. An hour covers a two-year daily series at nearly
# five seconds a day, and still bounds a genuinely hung runner.
REBUILD_TIMEOUT_SECONDS = 3600

REBUILD_HISTORY_FLAG = "--rebuild-history"
SINCE_DAYS_BACK_FLAG = "--since-days-back"

EXPORT_STEP_LABEL = "Export elections to static data files"

# Wording matters: the queue summary and the command-result page are the only
# places the user learns why a chamber produced no output.
NO_MATCHUP_NOTE = (
    "SKIPPED: no tracked presidential matchup set. Choose a matchup in the "
    "console, then run the models again."
)


class ScriptRunner(Protocol):
    """Callable that runs one Python script and captures its output.

    Matches :func:`console.shared.runner.run_python_script`; tests pass a
    recorder instead.
    """

    def __call__(
        self, script: Path, *args: str, timeout: int
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True)
class UsChamber:
    """Console wiring for one US election type.

    Attributes:
        slug: URL segment and endpoint-name stem (``"house"``); also the
            section its run-log records belong to.
        label: Display name used in headings (``"US House"``).
        model_type: The forecast-output election type this chamber lists.
        baseline_type: Real-election type eligible as the seat-level baseline
            on the output detail page.
        model_script: The chamber's forecast runner under ``models/us/``.
        model_args: Extra CLI args for the runner. The seat-level polls the
            models blend in are sparse, so every chamber widens the default
            30-day window; the President's matchup polls are sparser still.
        trend_cache_path: The chamber's shipped poll-tracker trend JSON.
        contest_slugs: The poll-queue contests (``UsContest.slug``) that import
            this chamber's polls; the chamber's import page offers only these.
        tracked_matchup_map_name: Map whose *national* tracked matchup this
            chamber's model needs, or None when it needs none. Set only for the
            President, whose national series is a head-to-head between two named
            candidates — with no matchup chosen the runner exits 2, so the
            console skips the step instead of failing the whole run.
        rebuild_flag: Runner flag that recomputes the whole trend history,
            appended when this chamber is named in ``rebuild``.
    """

    slug: str
    label: str
    model_type: ElectionType
    baseline_type: ElectionType
    model_script: Path
    model_args: tuple[str, ...]
    trend_cache_path: Path
    contest_slugs: tuple[str, ...]
    tracked_matchup_map_name: str | None = None
    rebuild_flag: str = REBUILD_HISTORY_FLAG

    @property
    def default_since_days_back(self) -> int:
        """The poll window, in days, that :attr:`model_args` passes the runner."""
        index = self.model_args.index(SINCE_DAYS_BACK_FLAG)
        return int(self.model_args[index + 1])

    @property
    def tracked_matchup_required(self) -> bool:
        """Whether this chamber's model refuses to run without a tracked matchup."""
        return self.tracked_matchup_map_name is not None

    @property
    def model_step_label(self) -> str:
        """Heading used for this chamber's block of model output."""
        return f"Run {self.label} model"


@dataclass(frozen=True)
class UsModelOptions:
    """Per-run overrides of a chamber's runner arguments.

    A field left at its default adds no argument, so the runner's own default
    (or the chamber's :attr:`UsChamber.model_args`) applies.

    Attributes:
        since_days_back: Poll window in days; replaces the chamber's own.
        half_life_days: Poll decay half-life in days.
        seat_prior_weight: Weight of the uniform-swing prior against a seat's
            own polls.
        ignore_seat_polls: Project the pure uniform national swing.
        dry_run: Compute without writing to the database or to disk.
    """

    since_days_back: int | None = None
    half_life_days: float | None = None
    seat_prior_weight: float | None = None
    ignore_seat_polls: bool = False
    dry_run: bool = False

    def args_for(
        self, chamber: UsChamber, *, rebuild_history: bool = False
    ) -> tuple[str, ...]:
        """The runner arguments for ``chamber`` under these options.

        Args:
            chamber: The chamber whose runner is invoked.
            rebuild_history: Whether to append the chamber's
                :attr:`UsChamber.rebuild_flag`.

        Returns:
            The arguments, in the order the runner is called with. With the
            default options and no rebuild this is exactly
            :attr:`UsChamber.model_args`.

        Raises:
            ValueError: A dry run was asked to rebuild history. The runner
                skips the rebuild in dry-run mode, so the request would do
                nothing.
        """
        if rebuild_history and self.dry_run:
            raise ValueError("A dry run cannot rebuild history: it writes nothing.")
        args = list(chamber.model_args)
        if self.since_days_back is not None:
            args[args.index(SINCE_DAYS_BACK_FLAG) + 1] = str(self.since_days_back)
        if self.half_life_days is not None:
            args += ["--half-life-days", str(self.half_life_days)]
        if self.seat_prior_weight is not None:
            args += ["--seat-prior-weight", str(self.seat_prior_weight)]
        if self.ignore_seat_polls:
            args.append("--ignore-seat-polls")
        if self.dry_run:
            args.append("--dry-run")
        if rebuild_history:
            args.append(chamber.rebuild_flag)
        return tuple(args)


US_CHAMBERS: tuple[UsChamber, ...] = (
    UsChamber(
        slug="house",
        label="US House",
        model_type=ElectionType.us_house_model,
        baseline_type=ElectionType.us_house,
        model_script=US_HOUSE_MODEL_SCRIPT,
        model_args=("--since-days-back", "60"),
        trend_cache_path=US_HOUSE_TREND_CACHE_JSON,
        contest_slugs=("house_national", "house_districts"),
    ),
    UsChamber(
        slug="president",
        label="US President",
        model_type=ElectionType.us_presidential_model,
        baseline_type=ElectionType.us_presidential,
        model_script=US_PRESIDENT_MODEL_SCRIPT,
        model_args=("--since-days-back", "120"),
        trend_cache_path=US_PRESIDENT_TREND_CACHE_JSON,
        contest_slugs=("president",),
        tracked_matchup_map_name="US Presidential 2024",
    ),
    UsChamber(
        slug="senate",
        label="US Senate",
        model_type=ElectionType.us_senate_model,
        baseline_type=ElectionType.us_senate,
        model_script=US_SENATE_MODEL_SCRIPT,
        model_args=("--since-days-back", "60"),
        trend_cache_path=US_SENATE_TREND_CACHE_JSON,
        contest_slugs=("senate_races",),
    ),
)


US_CHAMBERS_BY_SLUG: Mapping[str, UsChamber] = {
    chamber.slug: chamber for chamber in US_CHAMBERS
}

US_CHAMBER_SLUGS: tuple[str, ...] = tuple(chamber.slug for chamber in US_CHAMBERS)

# The chambers decided race by race (House districts, Senate races): they have
# a matchup per race, unlike the President's one national matchup. Those are the
# chambers that need no tracked national matchup.
US_RACE_CHAMBER_SLUGS: tuple[str, ...] = tuple(
    chamber.slug for chamber in US_CHAMBERS if not chamber.tracked_matchup_required
)

# Flask URL converters restricting ``<chamber>`` to those slugs, so a route
# never has to repeat the list.
US_CHAMBER_CONVERTER = f"<any({','.join(US_CHAMBER_SLUGS)}):chamber>"
US_RACE_CHAMBER_CONVERTER = f"<any({','.join(US_RACE_CHAMBER_SLUGS)}):chamber>"


@dataclass(frozen=True)
class UsModelRun:
    """Combined outcome of one "run a US model and export" sequence.

    Attributes:
        stdout: Every step's stdout, each under an ``=== <label> ===`` heading.
            A skipped chamber contributes its heading and :data:`NO_MATCHUP_NOTE`.
        stderr: The same for stderr, omitting steps that wrote none.
        return_code: The first non-zero return code, or 0 when every step that
            ran succeeded. A skipped chamber is not a failure.
        skipped: The chamber's slug if it was skipped, else empty.
    """

    stdout: str
    stderr: str
    return_code: int
    skipped: frozenset[str]


class UsModelRunInterrupted(subprocess.SubprocessError):
    """A step timed out or could not be started, part-way through a run.

    A :class:`subprocess.SubprocessError`, so callers that already catch that
    (and ``OSError``) still do. The original error is its ``__cause__``.

    Attributes:
        step: Label of the step that died.
        partial: The run up to that point: every finished step's output, then
            whatever the dying step printed, under its own heading. Its
            ``return_code`` is 1. It shows whether the model step finished, and
            so saved an output the export never picked up.
    """

    def __init__(self, step: str, partial: UsModelRun, cause: BaseException) -> None:
        # Every constructor argument goes to ``args``, so copy and pickle, which
        # rebuild an exception from its args, still work.
        super().__init__(step, partial, cause)
        self.step = step
        self.partial = partial
        self.cause = cause

    def __str__(self) -> str:
        return f"{self.step} did not finish: {type(self.cause).__name__}: {self.cause}"


def _output_text(output: str | bytes | None) -> str:
    """A subprocess error's captured output as text.

    ``TimeoutExpired`` carries whatever the step printed before it was killed —
    as bytes on POSIX even when the run asked for text.
    """
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


# The console's one run slot. Model runs, exports and database rebuilds, the
# Holyrood import, a US results import and Rebuild Current Senate hold it for
# their whole run; a poll queue finish, a single-poll import and a by-election
# hold it around their model or export step only (their polls and tracking are
# committed before). A background job (:func:`console.shared.jobs.start_job`)
# takes it for the job's whole life, so the work inside a job never takes it
# again, and :func:`run_us_chamber_and_export` never takes it itself: its callers
# (the job, or the queue finish) already hold it.
# One lock, not one per chamber or section, because every run ends with an export
# that publishes *every* chamber's trend history: a run that exported while
# another chamber's rebuild had cleared its points would publish that half-cleared
# history, and two exports would rewrite the same files at once. The threaded dev
# server makes a double submit reach a run twice.
# Process-local: it does not guard against a second server process or a
# concurrent CLI run.
MODEL_RUN_LOCK = threading.Lock()


@contextmanager
def model_run_slot() -> Iterator[bool]:
    """Hold the console's one run slot, if it is free.

    Model runs, exports and database rebuilds, the Holyrood import, a US results
    import and Rebuild Current Senate hold this slot for their whole run; a poll
    queue finish, a single-poll import and a by-election hold it around their
    model or export step only (their polls and tracking are committed before).
    The lock is tried without blocking, so a second submit is told a run is in
    progress instead of queueing silently behind an hour-long rebuild.

    Yields:
        True while the slot is held (it is released on exit), or False when
        another request or job holds it, in which case nothing may run.
    """
    if not MODEL_RUN_LOCK.acquire(blocking=False):
        yield False
        return
    try:
        yield True
    finally:
        MODEL_RUN_LOCK.release()


def busy_message(refused: str) -> str:
    """The message for a run refused because another one holds the slot.

    Args:
        refused: What did not happen, e.g. ``"History not rebuilt"``.
    """
    return (
        f"{refused}: another run is in progress (a model run, export or database "
        "rebuild); wait for it to finish, then try again."
    )


def tracked_matchup_in_force(db: Database, chamber: UsChamber) -> bool:
    """Whether ``chamber``'s required national tracked matchup is set.

    True for a chamber that requires none. A missing ``tracked_matchups`` row
    and a row whose ``matchup`` is NULL (the deliberate *ignore this race*
    marker) both count as unset, as does a missing map — in every one of those
    cases the runner would refuse to produce a forecast.

    Args:
        db: Active Database instance.
        chamber: The chamber to check.

    Returns:
        True when the chamber's model can run.
    """
    map_name = chamber.tracked_matchup_map_name
    if map_name is None:
        return True

    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        return False

    tracked = db.get_tracked_matchup(poll_map.id, None)
    return tracked is not None and tracked.matchup is not None


def run_us_chamber_and_export(
    db: Database,
    chamber: UsChamber,
    *,
    runner: ScriptRunner = run_python_script,
    rebuild_history: bool,
    since_days_back: int | None = None,
    half_life_days: float | None = None,
    seat_prior_weight: float | None = None,
    ignore_seat_polls: bool = False,
    dry_run: bool = False,
    export: bool = True,
) -> UsModelRun:
    """Run one US chamber's forecast model, then (optionally) the static export.

    A chamber whose required tracked matchup is unset is skipped, and the
    export still runs. A step that fails stops the sequence, so a broken model
    never gets exported over a good one. Called with only ``rebuild_history`` it
    runs the chamber's own :attr:`UsChamber.model_args`, then the export.

    Args:
        db: Active Database instance, used only to read tracked matchups.
        chamber: The chamber whose model to run.
        runner: Subprocess runner; injected so tests record calls instead of
            shelling out.
        rebuild_history: Whether to append the chamber's
            :attr:`UsChamber.rebuild_flag`, recomputing its whole trend
            history rather than only today's point.
        since_days_back: Poll window in days, replacing the chamber's own.
        half_life_days: Poll decay half-life in days.
        seat_prior_weight: Weight of the uniform-swing prior against a seat's
            own polls.
        ignore_seat_polls: Project the pure uniform national swing.
        dry_run: Compute without writing anything. A dry run never exports,
            whatever ``export`` says.
        export: Whether to run the static export after the model.

    Returns:
        The combined :class:`UsModelRun`.

    Raises:
        ValueError: ``dry_run`` and ``rebuild_history`` were both set.
        UsModelRunInterrupted: A step timed out or could not be started.
    """
    options = UsModelOptions(
        since_days_back=since_days_back,
        half_life_days=half_life_days,
        seat_prior_weight=seat_prior_weight,
        ignore_seat_polls=ignore_seat_polls,
        dry_run=dry_run,
    )
    # Building the arguments also rejects a dry run combined with a rebuild.
    model_args = options.args_for(chamber, rebuild_history=rebuild_history)
    return _run_chamber_and_export(
        db,
        chamber,
        runner=runner,
        model_args=model_args,
        rebuild_history=rebuild_history,
        export=export and not dry_run,
    )


def _run_chamber_and_export(
    db: Database,
    chamber: UsChamber,
    *,
    runner: ScriptRunner,
    model_args: tuple[str, ...],
    rebuild_history: bool,
    export: bool,
) -> UsModelRun:
    """Run one chamber's model, then the export.

    Args:
        db: Active Database instance, used only to read tracked matchups.
        chamber: The chamber whose model to run.
        runner: Subprocess runner.
        model_args: The model runner's arguments.
        rebuild_history: Whether ``model_args`` rebuild the trend history, which
            runs under the longer rebuild timeout.
        export: Whether to run the export once the model has succeeded or been
            skipped.

    Returns:
        The combined :class:`UsModelRun`; see :func:`run_us_chamber_and_export`
        for the skip and failure rules.

    Raises:
        UsModelRunInterrupted: A step timed out (``TimeoutExpired``), otherwise
            failed to run, or could not be started (``OSError``). It carries
            the output of every step before it and is chained to the original.
    """
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    skipped: list[str] = []

    def run_step(
        label: str,
        script: Path,
        args: tuple[str, ...],
        *,
        timeout: int = STEP_TIMEOUT_SECONDS,
        kind: str,
    ) -> int:
        try:
            with section_context(chamber.slug, kind):
                result = runner(script, *args, timeout=timeout)
        except (subprocess.SubprocessError, OSError) as err:
            stdout_parts.append(
                f"=== {label} ===\n{_output_text(getattr(err, 'stdout', None))}"
            )
            partial_stderr = _output_text(getattr(err, "stderr", None))
            if partial_stderr:
                stderr_parts.append(f"=== {label} ===\n{partial_stderr}")
            partial = UsModelRun(
                stdout="\n".join(stdout_parts),
                stderr="\n".join(stderr_parts),
                return_code=1,
                skipped=frozenset(skipped),
            )
            raise UsModelRunInterrupted(label, partial, err) from err
        stdout_parts.append(f"=== {label} ===\n{result.stdout}")
        if result.stderr:
            stderr_parts.append(f"=== {label} ===\n{result.stderr}")
        return result.returncode

    return_code = 0
    if not tracked_matchup_in_force(db, chamber):
        skipped.append(chamber.slug)
        stdout_parts.append(f"=== {chamber.model_step_label} ===\n{NO_MATCHUP_NOTE}")
    else:
        return_code = run_step(
            chamber.model_step_label,
            chamber.model_script,
            model_args,
            timeout=REBUILD_TIMEOUT_SECONDS
            if rebuild_history
            else STEP_TIMEOUT_SECONDS,
            kind="model",
        )
    if return_code == 0 and export:
        return_code = run_step(
            EXPORT_STEP_LABEL, EXPORT_ELECTION_SCRIPT, (), kind="export"
        )

    return UsModelRun(
        stdout="\n".join(stdout_parts),
        stderr="\n".join(stderr_parts),
        return_code=return_code,
        skipped=frozenset(skipped),
    )
