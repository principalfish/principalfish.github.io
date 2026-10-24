"""Chronological model trends reconstructed from authoritative recorded votes.

Each entry is one run's national seats ``s`` and vote share ``v`` per party.
A run with a stored Monte Carlo summary also carries, per party, its vote band
``vl``/``vh`` (the summary's vote interval, 90%; 1 dp), ``sl``/``sh``
(integer seat bounds), and P(majority) ``pm`` /
P(largest) ``pl`` (fractions, 4 dp; left out when zero), plus an entry-level
``hung``. A run without one gets none of these keys, so its entry is exactly
as before. In an entry with ``hung``, a party lacking ``pm``/``pl`` has
probability 0, and a party lacking ``vl``/``vh`` was not in the summary.

Consecutive runs with the same seats and shares collapse to the first, as
before; uncertainty does not keep a duplicate (the simulation seed changes
daily, so its odds always differ slightly). The one exception: a run with a
summary is kept after an equal run without one, so the odds appear. The
reverse is not kept: an equal run without a summary after one with a summary
adds nothing, and the earlier entry's odds carry forward. With no summaries
anywhere the rule is exactly the old one.

A run whose stored error budget shows no national polls for any party is
left out: with nothing polled it projects the baseline with no error, which
would plot as a spike. The previous entry carries across the gap.
"""

from __future__ import annotations

import re
import json
import shlex
import sqlite3
from collections.abc import Collection, Iterator, Mapping
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from electoral_votes import (
    ElectoralVoteError,
    allocation_year,
    get_electoral_votes_sqlite,
)
from model_support.io import (
    OutputPublicationError,
    publish_json,
    validate_output_target,
)
from model_support.persistence import (
    OutputScope,
    UncertaintyStoreError,
    committed_dates,
    iter_national_blocks,
)
from model_support.public_uncertainty import (
    PublicUncertaintyError,
    TrendUncertainty,
    trend_uncertainty,
    public_us_document,
)
from model_support.us_release import house_publication_allowed
from model_support.us_run import fingerprint, restore_run_inputs
from model_support.us_uncertainty import US_TREND_SCHEMA, keep_trend_point
from polls.importers.us.us_geography import parent_seat_name


class TrendEntry(TypedDict):
    election_id: int
    election_name: str
    as_of_date: str
    parties: dict[str, dict[str, int | float]]
    hung: NotRequired[float]
    context: NotRequired[dict[str, Any] | None]
    segment_id: NotRequired[str | None]
    metrics: NotRequired[dict[str, Any]]
    summary: NotRequired[dict[str, Any] | None]


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
        "us_presidential",
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
        is_presidential = scope.election_type == "us_presidential_model"
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
        ev_by_election: dict[int, dict[str, int]] = {}
        if is_presidential:
            election_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(elections)")
            }
            if "target_election_year" not in election_columns:
                raise ElectoralVoteError(
                    "Presidential target metadata is missing; run "
                    "scripts/migrate_us_electoral_votes.py "
                    "with an explicit legacy target."
                )
            year_by_election = {
                int(eid): allocation_year(scope.election_type, year, target)
                for eid, year, target in conn.execute(
                    "SELECT id,year,target_election_year FROM elections "
                    "WHERE type=? AND map_id=?",
                    (scope.election_type, scope.map_id),
                )
                if eid in elections
            }
            units_by_year: dict[int, set[str]] = {}
            for eid, unit_name in conn.execute(
                "SELECT DISTINCT e.id,s.seat_name FROM elections e "
                "JOIN votes v ON v.election_id=e.id JOIN seats s ON s.id=v.seat_id "
                "WHERE e.type=? AND e.map_id=?",
                (scope.election_type, scope.map_id),
            ):
                if eid in year_by_election:
                    units_by_year.setdefault(year_by_election[eid], set()).add(
                        unit_name,
                    )
            weights_by_year = {
                year: get_electoral_votes_sqlite(
                    conn, year, units_by_year.get(year, set()),
                )
                for year in set(year_by_election.values())
            }
            ev_by_election = {
                eid: weights_by_year[year] for eid, year in year_by_election.items()
            }
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
        for eid, pid, votes, elected, has_popular in conn.execute(
            "SELECT e.id,v.party_id,SUM(CASE WHEN t.popular=1 "
            "AND (t.parent_id IS NULL OR NOT EXISTS "
            "(SELECT 1 FROM votes p WHERE p.election_id=e.id "
            "AND p.seat_id=t.parent_id)) THEN v.vote_total ELSE 0 END),"
            "SUM(CASE WHEN v.elected THEN 1 ELSE 0 END),"
            "MAX(t.popular) "
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
                    metrics[eid][str(pid)]["e"] = 0
        if is_presidential:
            for eid, pid, unit_name in conn.execute(
                "SELECT e.id,v.party_id,s.seat_name FROM elections e "
                "JOIN votes v ON v.election_id=e.id JOIN seats s ON s.id=v.seat_id "
                "WHERE e.type=? AND e.map_id=? AND v.elected",
                (scope.election_type, scope.map_id),
            ):
                if eid in metrics:
                    metrics[eid][str(pid)]["e"] += ev_by_election[eid][unit_name]
        uncertainty, unpolled = _trend_uncertainty(conn, scope, elections)
        house = (
            _house_metadata(conn, scope) if scope.election_type == "us_house_model"
            else None
        )
        dates: set[str] = set()
        previous: tuple[dict[str, dict[str, int | float]], bool] | None = None
        previous_segment: str | None = None
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
            if eid in unpolled:
                continue
            summary = uncertainty.get(eid)
            metadata = house.get(eid, {}) if house is not None else None
            segment = metadata.get("segment_id") if metadata else None
            has_summary = bool(metadata.get("summary")) if metadata else summary is not None
            if house is not None and previous is not None and not keep_trend_point(
                previous_context=previous_segment, context=segment,
                central_equal=previous[0] == parties,
                previous_has_summary=previous[1], has_summary=has_summary,
            ):
                continue
            if (
                house is None
                and
                previous is not None
                and previous[0] == parties
                and (summary is None or previous[1])
            ):
                continue
            previous = (parties, has_summary)
            previous_segment = segment
            entry: TrendEntry = {
                "election_id": eid,
                "election_name": name,
                "as_of_date": as_of.isoformat(),
                "parties": {
                    party_id: (
                        {**fields, **summary.parties.get(party_id, {})}
                        if summary is not None
                        else fields
                    )
                    for party_id, fields in sorted(
                        parties.items(), key=lambda item: int(item[0])
                    )
                },
            }
            if summary is not None:
                entry["hung"] = summary.hung
            if house is not None:
                entry.update({
                    "context": metadata.get("context") if metadata else None,
                    "segment_id": segment,
                    "metrics": {
                        "house_seats": {"unit": "seats", "domain": "party",
                                        "total": 435,
                                        "central": {p: row["s"]
                                                    for p, row in parties.items()}},
                        "national_vote_share": {
                            "unit": "percent", "domain": "party", "total": 100,
                            "central": {p: row["v"] for p, row in parties.items()},
                        },
                    },
                    "summary": metadata.get("summary") if metadata else None,
                })
            entries.append(entry)
    return entries


def _house_metadata(
    conn: sqlite3.Connection, scope: OutputScope,
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_uncertainty'",
    ).fetchone():
        return result
    for election_id, name, raw in conn.execute(
        "SELECT e.id,e.name,u.national_json FROM elections e "
        "JOIN model_uncertainty u ON e.id=u.election_id "
        "WHERE e.type=? AND e.map_id=?", (scope.election_type, scope.map_id),
    ):
        try:
            as_of = scope.date_from_name(name)
            if as_of is None:
                continue
            document: dict[str, Any] = json.loads(raw)
            inputs = restore_run_inputs(document)
            if (document["context"]["map_id"] != scope.map_id
                    or inputs.as_of_date != as_of):
                continue
            private = document["private"]
            if private["artifact_id"] != fingerprint([
                document["result_identity"], document["summary"],
                {key: v for key, v in private.items() if key != "artifact_id"},
            ]):
                continue
            rows = conn.execute(
                "SELECT seat_id,party_id,vote_total,elected FROM votes "
                "WHERE election_id=?", (election_id,),
            )
            actual = []
            for seat, party, votes, winner in rows:
                if votes is None or votes != int(votes):
                    raise ValueError("nonintegral recorded House vote")
                actual.append((seat, party, int(votes), bool(winner)))
            actual.sort()
            if actual != sorted(inputs.central_rows):
                continue
            config = {key: v for key, v in private["inputs"]["config"].items()
                      if key != "national_scale"}
            result[election_id] = {
                "context": document["context"],
                "segment_id": fingerprint([
                    {key: v for key, v in document["context"].items()
                     if key != "as_of_date"},
                    inputs.version, config,
                    private["provenance"].get("calibration"),
                ]),
                "summary": (
                    public_us_document(document)["summary"]
                    if house_publication_allowed(document) else None
                ),
            }
        except (ValueError, KeyError, TypeError, AttributeError):
            # Unsupported optional metadata cannot supply public uncertainty.
            continue
    return result


def trend_document(scope: OutputScope, entries: list[TrendEntry]) -> object:
    """Activate the explicit US envelope for House; other models retain arrays."""
    if scope.election_type == "us_house_model":
        return {"schema": US_TREND_SCHEMA, "model": "house", "points": entries}
    return entries


def _trend_uncertainty(
    conn: sqlite3.Connection, scope: OutputScope, election_ids: Collection[int]
) -> tuple[dict[int, TrendUncertainty], set[int]]:
    """Read each scoped run's trend additions from its stored national block.

    Args:
        conn: The reconstruction's connection, inside its transaction.
        scope: The model scope.
        election_ids: The scope's dated runs.

    Returns:
        Election id -> its additions, for the runs with a summary; and the
        runs whose budget shows no national polls (:func:`_unpolled`).

    Raises:
        ValueError: A stored block is unreadable or lacks a published field
            (callers report trend failures as ``ValueError``).
    """
    result: dict[int, TrendUncertainty] = {}
    unpolled: set[int] = set()
    if scope.election_type == "us_house_model":
        return result, unpolled
    try:
        for election_id, national in iter_national_blocks(conn, scope):
            if election_id not in election_ids:
                continue
            if national.get("schema") == "pf-us-model-uncertainty-v1":
                # US trends retain their legacy central contract until activated.
                continue
            if _unpolled(national):
                unpolled.add(election_id)
            try:
                result[election_id] = trend_uncertainty(national)
            except PublicUncertaintyError as err:
                raise ValueError(
                    f"Stored uncertainty for election {election_id}: {err}"
                ) from err
    except UncertaintyStoreError as err:
        raise ValueError(f"Stored uncertainty is unreadable: {err}") from err
    return result, unpolled


def _unpolled(national: Mapping[str, object]) -> bool:
    """Whether a stored block's error budget shows no national polls at all.

    Only the Westminster budget records ``poll_count`` per party. Any other or
    malformed budget is private and unvalidated, so it counts as polled and
    its run is kept.
    """
    budget = national.get("budget")
    parties = budget.get("parties") if isinstance(budget, Mapping) else None
    if not isinstance(parties, Mapping) or not parties:
        return False
    counts = [
        party.get("poll_count") if isinstance(party, Mapping) else None
        for party in parties.values()
    ]
    return all(type(count) is int and count == 0 for count in counts)


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
    publish_json(
        trend_document(scope, reconstruct_trends(sqlite_path, scope)),
        destination, repair=repair,
    )


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
