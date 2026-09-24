"""Chamber wiring for the US console, and the "run all models" sequence.

:data:`US_CHAMBERS` is the single description of the three US election types —
their model runners, forecast/baseline election types and trend caches. The US
blueprint re-exports it for the per-chamber output routes it registers.

:func:`run_us_models_and_export` runs those runners and then the static export.
It is Flask-free so it can be called both from ``POST /us/run-models`` and from
the end of the poll-review queue, and it takes its subprocess runner as an
argument so tests never shell out. :func:`run_us_chamber_and_export` is the
same sequence narrowed to one chamber, for the matchup pages' "rebuild
history" option.
"""

from __future__ import annotations

import subprocess
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from db import Database
from models import ElectionType

from console.paths import (
    EXPORT_ELECTION_SCRIPT,
    US_HOUSE_MODEL_SCRIPT,
    US_HOUSE_TREND_CACHE_JSON,
    US_PRESIDENT_MODEL_SCRIPT,
    US_PRESIDENT_TREND_CACHE_JSON,
    US_SENATE_MODEL_SCRIPT,
    US_SENATE_TREND_CACHE_JSON,
)
from console.services.runner import run_python_script

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

EXPORT_STEP_LABEL = "Export elections to static data files"

# Wording matters: the queue summary and the command-result page are the only
# places the user learns why a chamber produced no output.
NO_MATCHUP_NOTE = (
    "SKIPPED: no tracked presidential matchup set. Choose a matchup in the "
    "console, then run the models again."
)


class ScriptRunner(Protocol):
    """Callable that runs one Python script and captures its output.

    Matches :func:`console.services.runner.run_python_script`; tests pass a
    recorder instead.
    """

    def __call__(
        self, script: Path, *args: str, timeout: int
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True)
class UsChamber:
    """Console wiring for one US election type.

    Attributes:
        slug: URL segment and endpoint-name stem (``"house"``). Also the name
            used in the ``rebuild`` set of :func:`run_us_models_and_export`.
        label: Display name used in headings (``"US House"``).
        model_type: The forecast-output election type this chamber lists.
        baseline_type: Real-election type eligible as the seat-level baseline
            on the output detail page.
        model_script: The chamber's forecast runner under ``models/us/``.
        model_args: Extra CLI args for the runner. The seat-level polls the
            models blend in are sparse, so every chamber widens the default
            30-day window; the President's matchup polls are sparser still.
        trend_cache_path: The chamber's shipped poll-tracker trend JSON.
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
    tracked_matchup_map_name: str | None = None
    rebuild_flag: str = REBUILD_HISTORY_FLAG

    @property
    def tracked_matchup_required(self) -> bool:
        """Whether this chamber's model refuses to run without a tracked matchup."""
        return self.tracked_matchup_map_name is not None

    @property
    def model_step_label(self) -> str:
        """Heading used for this chamber's block of model output."""
        return f"Run {self.label} model"


US_CHAMBERS: tuple[UsChamber, ...] = (
    UsChamber(
        slug="house",
        label="US House",
        model_type=ElectionType.us_house_model,
        baseline_type=ElectionType.us_house,
        model_script=US_HOUSE_MODEL_SCRIPT,
        model_args=("--since-days-back", "60"),
        trend_cache_path=US_HOUSE_TREND_CACHE_JSON,
    ),
    UsChamber(
        slug="president",
        label="US President",
        model_type=ElectionType.us_presidential_model,
        baseline_type=ElectionType.us_presidential,
        model_script=US_PRESIDENT_MODEL_SCRIPT,
        model_args=("--since-days-back", "120"),
        trend_cache_path=US_PRESIDENT_TREND_CACHE_JSON,
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
    ),
)


US_CHAMBERS_BY_SLUG: Mapping[str, UsChamber] = {
    chamber.slug: chamber for chamber in US_CHAMBERS
}


@dataclass(frozen=True)
class UsModelRun:
    """Combined outcome of one "run the US models and export" sequence.

    Attributes:
        stdout: Every step's stdout, each under an ``=== <label> ===`` heading.
            A skipped chamber contributes its heading and :data:`NO_MATCHUP_NOTE`.
        stderr: The same for stderr, omitting steps that wrote none.
        return_code: The first non-zero return code, or 0 when every step that
            ran succeeded. A skipped chamber is not a failure.
        skipped: Slugs of the chambers that were skipped.
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
            ``return_code`` is 1. It shows which chambers finished, and so
            saved outputs the export never picked up.
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


def run_us_models_and_export(
    db: Database,
    *,
    runner: ScriptRunner = run_python_script,
    rebuild: Collection[str] = frozenset(),
) -> UsModelRun:
    """Run each US chamber's forecast model, then the static export.

    A chamber whose required tracked matchup is unset is skipped on its own —
    the other chambers and the export still run, because their forecasts do not
    depend on it. Any step that fails stops the sequence, so a broken model
    never gets exported over a good one.

    Args:
        db: Active Database instance, used only to read tracked matchups.
        runner: Subprocess runner; injected so tests record calls instead of
            shelling out.
        rebuild: Chamber slugs whose whole trend history should be recomputed.
            Unknown slugs are ignored. Each named chamber's runner gets its
            :attr:`UsChamber.rebuild_flag` and :data:`REBUILD_TIMEOUT_SECONDS`;
            chambers outside the set are run exactly as before, so an
            unsupported flag can only affect a run the caller explicitly asked
            to rebuild.

    Returns:
        The combined :class:`UsModelRun`.

    Raises:
        UsModelRunInterrupted: A step timed out or could not be started. It
            carries the output of every step before it.
    """
    return _run_chambers_and_export(db, US_CHAMBERS, runner=runner, rebuild=rebuild)


def run_us_chamber_and_export(
    db: Database,
    chamber: UsChamber,
    *,
    runner: ScriptRunner = run_python_script,
    rebuild_history: bool,
) -> UsModelRun:
    """Run one US chamber's forecast model, then the static export.

    The single-chamber form of :func:`run_us_models_and_export`, with the same
    matchup gating and stop-on-failure rules: the export only runs once the
    model has succeeded (or been skipped for want of a matchup).

    Args:
        db: Active Database instance, used only to read tracked matchups.
        chamber: The chamber whose model to run.
        runner: Subprocess runner; injected so tests record calls instead of
            shelling out.
        rebuild_history: Whether to append the chamber's
            :attr:`UsChamber.rebuild_flag`, recomputing its whole trend
            history rather than only today's point.

    Returns:
        The combined :class:`UsModelRun`.

    Raises:
        UsModelRunInterrupted: A step timed out or could not be started.
    """
    rebuild = frozenset({chamber.slug}) if rebuild_history else frozenset()
    return _run_chambers_and_export(db, (chamber,), runner=runner, rebuild=rebuild)


def _run_chambers_and_export(
    db: Database,
    chambers: Collection[UsChamber],
    *,
    runner: ScriptRunner,
    rebuild: Collection[str],
) -> UsModelRun:
    """Run the given chambers' models in order, then the export.

    Args:
        db: Active Database instance, used only to read tracked matchups.
        chambers: The chambers to run, in order.
        runner: Subprocess runner.
        rebuild: Chamber slugs to run with their rebuild flag.

    Returns:
        The combined :class:`UsModelRun`; see :func:`run_us_models_and_export`
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
        label: str, script: Path, args: tuple[str, ...], *, timeout: int = STEP_TIMEOUT_SECONDS
    ) -> int:
        try:
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
    for chamber in chambers:
        if not tracked_matchup_in_force(db, chamber):
            skipped.append(chamber.slug)
            stdout_parts.append(f"=== {chamber.model_step_label} ===\n{NO_MATCHUP_NOTE}")
            continue

        args = chamber.model_args
        timeout = STEP_TIMEOUT_SECONDS
        if chamber.slug in rebuild:
            args = (*args, chamber.rebuild_flag)
            timeout = REBUILD_TIMEOUT_SECONDS

        return_code = run_step(
            chamber.model_step_label, chamber.model_script, args, timeout=timeout
        )
        if return_code != 0:
            break
    else:
        return_code = run_step(EXPORT_STEP_LABEL, EXPORT_ELECTION_SCRIPT, ())

    return UsModelRun(
        stdout="\n".join(stdout_parts),
        stderr="\n".join(stderr_parts),
        return_code=return_code,
        skipped=frozenset(skipped),
    )
