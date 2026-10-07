"""Chronological model trends reconstructed from authoritative recorded votes."""

from __future__ import annotations

import re
import shlex
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from model_support.io import (
    OutputPublicationError,
    publish_json,
    validate_output_target,
)
from model_support.persistence import OutputScope, committed_dates
from polls.importers.us.us_geography import parent_seat_name


class TrendEntry(TypedDict):
    election_id: int
    election_name: str
    as_of_date: str
    parties: dict[str, dict[str, int | float]]


@dataclass(frozen=True, slots=True)
class TrendModel:
    election_type: str
    name_prefix: str
    parliament: str
    relative_path: str


TREND_MODELS = {
    "westminster": TrendModel(
        "model_uns",
        "UNS",
        "westminster",
        "electionmaps/data/results/model_output_trends.json",
    ),
    "holyrood": TrendModel(
        "holyrood_uns",
        "Holyrood UNS",
        "holyrood",
        "electionmaps/data/results/holyrood-trends.json",
    ),
    "us-house": TrendModel(
        "us_house_model",
        "US House UNS",
        "us_house",
        "uselectionmaps/data/results/us-house-trends.json",
    ),
    "us-senate": TrendModel(
        "us_senate_model",
        "US Senate UNS",
        "us_senate",
        "uselectionmaps/data/results/us-senate-trends.json",
    ),
    "us-president": TrendModel(
        "us_presidential_model",
        "US President UNS",
        "us_president",
        "uselectionmaps/data/results/us-president-trends.json",
    ),
}


def default_trend_path(model: str) -> Path:
    return Path(__file__).resolve().parents[2] / TREND_MODELS[model].relative_path


def _validate_scope(conn: sqlite3.Connection, scope: OutputScope) -> None:
    expected = next(
        (
            model.parliament
            for model in TREND_MODELS.values()
            if model.election_type == scope.election_type
        ),
        None,
    )
    row = conn.execute(
        "SELECT parliament FROM maps WHERE id = ?", (scope.map_id,)
    ).fetchone()
    if row is None or (expected is not None and row[0] != expected):
        raise ValueError(f"Map {scope.map_id} does not belong to {scope.election_type}")
    for name, seat_map_id in conn.execute(
        "SELECT DISTINCT e.name, s.map_id FROM elections e "
        "JOIN votes v ON v.election_id=e.id "
        "LEFT JOIN seats s ON s.id=v.seat_id WHERE e.type=? AND e.map_id=?",
        (scope.election_type, scope.map_id),
    ):
        if scope.date_from_name(name) is not None and seat_map_id != scope.map_id:
            raise ValueError("Model output contains votes outside its map scope")


def validate_trend_scope(sqlite_path: Path, scope: OutputScope) -> None:
    if any(
        source == sqlite_path.resolve() and requested == scope
        for source, requested, _ in _DEFERRED.get()
    ):
        return
    if not sqlite_path.is_file():
        raise ValueError(f"Database does not exist: {sqlite_path}")
    with closing(sqlite3.connect(sqlite_path)) as conn:
        _validate_scope(conn, scope)


def reconstruct_trends(sqlite_path: Path, scope: OutputScope) -> list[TrendEntry]:
    if not sqlite_path.is_file():
        raise ValueError(f"Database does not exist: {sqlite_path}")
    entries: list[TrendEntry] = []
    with closing(sqlite3.connect(sqlite_path)) as conn:
        conn.execute("BEGIN")
        _validate_scope(conn, scope)
        elections = {
            int(election_id): (name, as_of)
            for election_id, name in conn.execute(
                "SELECT id,name FROM elections WHERE type=? AND map_id=?",
                (scope.election_type, scope.map_id),
            )
            if (as_of := scope.date_from_name(name)) is not None
        }
        # Group popular votes by election/party in SQLite. A child is excluded only
        # when that same election contains its parent, so orphan districts count.
        seats = list(
            conn.execute(
                "SELECT id,seat_name FROM seats WHERE map_id=?", (scope.map_id,)
            )
        )
        id_by_name = {name: seat_id for seat_id, name in seats}
        conn.execute(
            "CREATE TEMP TABLE trend_seats "
            "(id INTEGER PRIMARY KEY, popular INTEGER, parent_id INTEGER)"
        )
        conn.executemany(
            "INSERT INTO trend_seats VALUES (?,?,?)",
            (
                (
                    seat_id,
                    int(
                        not (
                            scope.election_type == "holyrood_uns"
                            and re.search(r"\bList\s+\d+$", name, re.IGNORECASE)
                        )
                    ),
                    id_by_name.get(parent_seat_name(name))
                    if scope.election_type == "us_presidential_model"
                    else None,
                )
                for seat_id, name in seats
            ),
        )
        metrics: dict[int, dict[str, dict[str, int | float]]] = {
            eid: {} for eid in elections
        }
        for eid, pid, votes, elected, ev, has_popular in conn.execute(
            "SELECT e.id,v.party_id,SUM(CASE WHEN t.popular=1 "
            "AND (t.parent_id IS NULL OR NOT EXISTS "
            "(SELECT 1 FROM votes p WHERE p.election_id=e.id "
            "AND p.seat_id=t.parent_id)) THEN v.vote_total ELSE 0 END),"
            "SUM(CASE WHEN v.elected THEN 1 ELSE 0 END),"
            "SUM(CASE WHEN v.elected THEN COALESCE(s.electoral_votes,0) "
            "ELSE 0 END),MAX(t.popular) "
            "FROM elections e JOIN votes v ON v.election_id=e.id "
            "JOIN seats s ON s.id=v.seat_id JOIN trend_seats t ON t.id=s.id "
            "WHERE e.type=? AND e.map_id=? GROUP BY e.id,v.party_id",
            (scope.election_type, scope.map_id),
        ):
            if eid in metrics and (
                scope.election_type != "holyrood_uns" or has_popular or elected
            ):
                metrics[eid][str(pid)] = {"s": int(elected), "v": float(votes)}
                if scope.election_type == "us_presidential_model":
                    metrics[eid][str(pid)]["e"] = int(ev)
        dates: set[str] = set()
        for eid, (name, as_of) in sorted(
            elections.items(), key=lambda item: (item[1][1], item[0])
        ):
            if as_of.isoformat() in dates:
                raise ValueError(
                    f"Multiple scoped model results for {as_of}; rerun this model date"
                )
            dates.add(as_of.isoformat())
            parties = metrics[eid]
            total = sum(party["v"] for party in parties.values())
            for party in parties.values():
                party["v"] = round(party["v"] / total * 100.0, 1) if total > 0 else 0.0
            entry: TrendEntry = {
                "election_id": eid,
                "election_name": name,
                "as_of_date": as_of.isoformat(),
                "parties": dict(sorted(parties.items(), key=lambda item: int(item[0]))),
            }
            if not entries or entries[-1]["parties"] != parties:
                entries.append(entry)
    return entries


_DEFERRED: ContextVar[frozenset[tuple[Path, OutputScope, Path]]] = ContextVar(
    "deferred_model_trends", default=frozenset()
)


def publish_trends(sqlite_path: Path, scope: OutputScope, destination: Path) -> None:
    key = (sqlite_path.resolve(), scope, destination.resolve())
    if key in _DEFERRED.get():
        return
    model = next(
        (
            slug
            for slug, definition in TREND_MODELS.items()
            if definition.election_type == scope.election_type
        ),
        None,
    )
    repair = (
        f"Repair trends with rebuild_model_trends.py --model {model} "
        f"--map-id {scope.map_id} "
        f"--database {shlex.quote(str(sqlite_path))} "
        f"--output {shlex.quote(str(destination))}."
        if model
        else (
            "Regenerate this custom model trend cache from its scoped database outputs."
        )
    )
    publish_json(reconstruct_trends(sqlite_path, scope), destination, repair=repair)


@contextmanager
def trend_batch(
    sqlite_path: Path, scope: OutputScope, destination: Path, *, enabled: bool = True
) -> Iterator[None]:
    if not enabled:
        yield
        return
    validate_trend_scope(sqlite_path, scope)
    validate_output_target(destination, database=sqlite_path)
    key = (sqlite_path.resolve(), scope, destination.resolve())
    if key in _DEFERRED.get():
        yield
        return
    token = _DEFERRED.set(_DEFERRED.get() | {key})
    failure: BaseException | None = None
    with committed_dates(sqlite_path, scope) as saved_dates:
        try:
            yield
        except BaseException as exc:
            failure = exc
            raise
        finally:
            _DEFERRED.reset(token)
            try:
                publish_trends(sqlite_path, scope, destination)
            except (OutputPublicationError, OSError, sqlite3.Error, ValueError) as exc:
                dates = ", ".join(day.isoformat() for day in sorted(set(saved_dates)))
                saved = (
                    f"Database dates committed in this batch: {dates}."
                    if dates
                    else "No database dates committed in this batch."
                )
                model = next(
                    (
                        slug
                        for slug, definition in TREND_MODELS.items()
                        if definition.election_type == scope.election_type
                    ),
                    None,
                )
                repair = (
                    f"Regenerate trends with rebuild_model_trends.py --model {model} "
                    f"--map-id {scope.map_id} "
                    f"--database {shlex.quote(str(sqlite_path))} "
                    f"--output {shlex.quote(str(destination))}."
                    if model
                    else (
                        "Regenerate this custom model trend cache "
                        "from its scoped database outputs."
                    )
                )
                original = f" Original model failure: {failure}" if failure else ""
                raise OutputPublicationError(
                    f"Could not finalize trends: {exc}. {saved} {repair}{original}"
                ) from (failure if failure is not None else exc)
