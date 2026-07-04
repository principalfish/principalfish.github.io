"""Rebuild the SQLite database from committed source files + live upstreams.

Runs every base-data importer in its **ID-preserving** ``--refresh`` mode (see
each ``import_*.py``) in dependency order, then ``export_elections.py`` to
regenerate the static site data. Because ``--refresh`` never deletes a
map/region/seat/party row and never deletes a historical-election row (it only
clears+reinserts that election's votes), every primary key is preserved — so
polls and model runs, which foreign-key into those tables, survive the rebuild.

Model-run and poll data is **not** touched here: there is no ``run_*.py`` step
and no poll import, and nothing deletes polls, pollsters, poll_rows, or model
elections/votes.

One failed step does **not** abort the run: every step is attempted and a
per-step summary is printed at the end. The process exits non-zero if any step
failed.

Usage:
    python scripts/rebuild_database.py            # run the full rebuild
    python scripts/rebuild_database.py --dry-run  # list the steps, run nothing
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1]
OLD_SCRIPTS = DATA_DIR / "old_data" / "scripts"
USA_FILES = DATA_DIR / "old_data" / "files" / "usa"
WESTMINSTER_FILES = DATA_DIR / "old_data" / "files" / "westminster"
BY_ELECTIONS_FILE = WESTMINSTER_FILES / "by_elections.txt"
REGION_POPULATIONS_CSV = WESTMINSTER_FILES / "region_populations.csv"
REGION_POPULATIONS_MAP = "UK Constituencies post 2022"
EXPORT_SCRIPT = DATA_DIR / "scripts" / "export_elections.py"
BY_ELECTION_IMPORT = DATA_DIR / "scripts" / "by_election_import.py"

# Boundary downloads + a full reseed are slow; give each step plenty of room.
DEFAULT_STEP_TIMEOUT = 1800

# US data-file prefix -> (importer script name, election-name chamber label).
US_CHAMBERS: dict[str, tuple[str, str]] = {
    "house": ("import_house_elections.py", "House"),
    "senate": ("import_senate_elections.py", "Senate"),
    "presidential": ("import_presidential_elections.py", "Presidential"),
}


@dataclass(frozen=True)
class Step:
    """One importer/export invocation in the rebuild pipeline."""

    label: str
    script: Path
    args: tuple[str, ...]


@dataclass(frozen=True)
class ByElectionEntry:
    """A parsed line from ``by_elections.txt``."""

    url: str
    parent_election: str | None = None
    map_name: str | None = None


def parse_by_elections_file(path: Path) -> list[ByElectionEntry]:
    """Parse ``by_elections.txt`` into entries.

    Each non-blank, non-comment line is a Wikipedia URL, optionally followed by
    pipe-separated parent-election and map overrides::

        <url> | <parent election name> | <map name>

    Blank fields fall back to the importer defaults. Blank lines and lines
    beginning with ``#`` are ignored.

    Args:
        path: Path to the ``by_elections.txt`` source list.

    Returns:
        One :class:`ByElectionEntry` per data line, in file order.
    """
    if not path.exists():
        return []

    entries: list[ByElectionEntry] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split("|")]
        url = fields[0]
        if not url:
            continue
        parent = fields[1] if len(fields) > 1 and fields[1] else None
        map_name = fields[2] if len(fields) > 2 and fields[2] else None
        entries.append(ByElectionEntry(url=url, parent_election=parent, map_name=map_name))
    return entries


def us_election_steps(usa_files_dir: Path) -> list[Step]:
    """Build the per-file US import steps from ``old_data/files/usa/*.json``.

    A file named ``<chamber>-<year>.json`` maps to that chamber's importer with
    ``--file/--year/--name/--refresh``; the ``--name`` matches the stored DB
    election name (``"<year> US <Chamber> Election"``) so the refresh reuses the
    existing row.

    Args:
        usa_files_dir: Directory holding the committed US election JSON files.

    Returns:
        One :class:`Step` per recognised JSON file, sorted by filename.
    """
    steps: list[Step] = []
    for json_file in sorted(usa_files_dir.glob("*.json")):
        prefix, _, year_text = json_file.stem.partition("-")
        chamber = US_CHAMBERS.get(prefix)
        if chamber is None or not year_text.isdigit():
            continue
        script_name, label = chamber
        year = int(year_text)
        name = f"{year} US {label} Election"
        steps.append(
            Step(
                label=f"Import {name}",
                script=OLD_SCRIPTS / "usa" / script_name,
                args=(
                    "--file", str(json_file),
                    "--year", str(year),
                    "--name", name,
                    "--refresh",
                ),
            )
        )
    return steps


def by_election_steps(entries: list[ByElectionEntry]) -> list[Step]:
    """Build the per-URL by-election import steps.

    Args:
        entries: Parsed :class:`ByElectionEntry` list from
            :func:`parse_by_elections_file`.

    Returns:
        One :class:`Step` per by-election, each in ``--refresh`` mode.
    """
    steps: list[Step] = []
    for entry in entries:
        args = ["--url", entry.url]
        if entry.parent_election:
            args += ["--parent-election", entry.parent_election]
        if entry.map_name:
            args += ["--map-name", entry.map_name]
        args.append("--refresh")
        slug = entry.url.rstrip("/").rsplit("/", 1)[-1]
        steps.append(
            Step(
                label=f"Import by-election: {slug}",
                script=BY_ELECTION_IMPORT,
                args=tuple(args),
            )
        )
    return steps


def build_steps() -> list[Step]:
    """Assemble the full rebuild pipeline in dependency order.

    Order: parties -> Westminster geometry -> region populations -> Westminster
    GEs -> Holyrood elections -> US elections -> by-elections -> export. Each
    base importer runs ID-preserving (``--refresh`` where the importer supports
    it); the export regenerates static data last.

    Returns:
        The ordered list of :class:`Step` to execute.
    """
    steps: list[Step] = [
        Step("Import parties", OLD_SCRIPTS / "import_parties.py", ()),
        Step(
            "Import Westminster geometry",
            OLD_SCRIPTS / "westminster" / "import_topojson.py",
            ("--refresh",),
        ),
        Step(
            "Import region populations",
            OLD_SCRIPTS / "import_region_populations.py",
            ("--map-name", REGION_POPULATIONS_MAP, "--input", str(REGION_POPULATIONS_CSV)),
        ),
        Step(
            "Import Westminster general elections",
            OLD_SCRIPTS / "westminster" / "import_general_elections.py",
            ("--refresh",),
        ),
        Step(
            "Import Holyrood elections",
            OLD_SCRIPTS / "holyrood" / "import_holyrood_elections.py",
            ("--refresh",),
        ),
    ]
    steps += us_election_steps(USA_FILES)
    steps += by_election_steps(parse_by_elections_file(BY_ELECTIONS_FILE))
    steps.append(Step("Export elections to static data", EXPORT_SCRIPT, ()))
    return steps


def run_step(step: Step, *, timeout: int) -> tuple[bool, str]:
    """Run one step as a subprocess, capturing its combined output.

    Args:
        step: The step to execute.
        timeout: Hard per-step timeout in seconds.

    Returns:
        ``(ok, output)`` where ``ok`` is True on a zero exit code and ``output``
        is the captured stdout+stderr (or a timeout/error message).
    """
    command = [sys.executable, str(step.script), *step.args]
    if not step.script.exists():
        return False, f"script not found: {step.script}"
    try:
        result = subprocess.run(
            command,
            cwd=str(DATA_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except OSError as exc:
        # A spawn-time failure (missing interpreter, permissions, resource
        # limits) must not abort the whole run — record it and carry on.
        return False, f"failed to launch: {exc}"
    output = result.stdout
    if result.stderr:
        output = f"{output}\n[stderr]\n{result.stderr}"
    return result.returncode == 0, output


def main() -> int:
    """Run (or list) the rebuild pipeline and report per-step outcomes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the steps and their commands without running anything",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_STEP_TIMEOUT,
        help=f"Per-step timeout in seconds (default {DEFAULT_STEP_TIMEOUT})",
    )
    args = parser.parse_args()

    steps = build_steps()

    if args.dry_run:
        print(f"Rebuild pipeline: {len(steps)} steps (dry run — nothing executed)\n")
        for index, step in enumerate(steps, start=1):
            command = shlex.join([step.script.name, *step.args])
            print(f"  {index:2d}. {step.label}\n      {command}")
        return 0

    print(f"Rebuild pipeline: {len(steps)} steps\n")
    results: list[tuple[str, bool]] = []
    for index, step in enumerate(steps, start=1):
        print(f"=== [{index}/{len(steps)}] {step.label} ===")
        ok, output = run_step(step, timeout=args.timeout)
        print(output.strip())
        print(f"--- {'OK' if ok else 'FAILED'}: {step.label} ---\n")
        results.append((step.label, ok))

    failed = [label for label, ok in results if not ok]
    print("=== Rebuild summary ===")
    for label, ok in results:
        print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")
    print(f"\n{len(results) - len(failed)}/{len(results)} steps succeeded.")
    if failed:
        print(f"Failed: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
