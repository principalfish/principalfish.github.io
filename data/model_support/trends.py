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
from model_support.senate_cycles import (
    senate_cycle_from_snapshot, senate_central_anchor_from_snapshot,
)
from model_support.us_senate import senate_party
from model_support.us_release import (
    house_publication_allowed, senate_publication_allowed,
    president_publication_allowed,
)
from model_support.us_run import fingerprint, restore_run_inputs
from model_support.us_uncertainty import US_TREND_SCHEMA, keep_trend_point
from polls.importers.us.us_geography import parent_seat_name


_US_MODEL_TYPES = {
    "us_house_model": "house",
    "us_senate_model": "senate",
    "us_presidential_model": "president",
}


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
    central_basis: NotRequired[dict[str, Any]]
    display: NotRequired[dict[str, object]]


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
        us_metadata = (
            _us_metadata(conn, scope)
            if scope.election_type in _US_MODEL_TYPES
            else None
        )
        if scope.election_type == "us_senate_model":
            partial = _senate_roster_metadata(conn, scope)
            partial.update(us_metadata or {})
            us_metadata = partial
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
            metadata = us_metadata.get(eid, {}) if us_metadata is not None else None
            segment = metadata.get("segment_id") if metadata else None
            has_summary = bool(metadata.get("summary")) if metadata else summary is not None
            if us_metadata is not None and previous is not None and not keep_trend_point(
                previous_context=previous_segment, context=segment,
                central_equal=previous[0] == parties,
                previous_has_summary=previous[1], has_summary=has_summary,
            ):
                continue
            if (
                us_metadata is None
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
            if us_metadata is not None:
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
                if scope.election_type == "us_senate_model":
                    # Contested votes cannot supply full-chamber seat counts or votes.
                    # Only a validated frozen run may provide the 100-seat metric.
                    entry["metrics"] = metadata.get("metrics", {}) if metadata else {}
                    if metadata and "central_basis" in metadata:
                        entry["central_basis"] = metadata["central_basis"]
                if is_presidential:
                    # Legacy s counts map units; only e counts the electoral college.
                    entry["metrics"]["electoral_votes"] = {
                        "unit": "electoral_votes", "domain": "party", "total": 538,
                        "central": {p: row["e"] for p, row in parties.items()},
                    }
                    del entry["metrics"]["house_seats"]
                    if metadata and "display" in metadata:
                        entry["display"] = metadata["display"]
            entries.append(entry)
    return entries


def _senate_roster_metadata(
    conn: sqlite3.Connection, scope: OutputScope,
) -> dict[int, dict[str, Any]]:
    """Recover isolated central chamber counts from saved deterministic rosters.

    The roster and exact field can be checked without inventing unavailable model
    options or calibration context. Such a point cannot supply odds or continuity.
    """
    result: dict[int, dict[str, Any]] = {}
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_context'",
    ).fetchone():
        return result
    names = dict(conn.execute("SELECT id,name FROM parties"))
    by_party: dict[str, list[int]] = {}
    for party_id, name in names.items():
        by_party.setdefault(senate_party(name), []).append(party_id)
    has_uncertainty = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_uncertainty'",
    ).fetchone() is not None
    without_snapshot = (
        "AND NOT EXISTS (SELECT 1 FROM model_uncertainty u "
        "WHERE u.election_id=e.id)" if has_uncertainty else ""
    )
    for election_id, name, raw in conn.execute(
        "SELECT e.id,e.name,c.payload_json FROM elections e "
        "JOIN model_context c ON e.id=c.election_id "
        f"WHERE e.type=? AND e.map_id=? {without_snapshot}",
        (scope.election_type, scope.map_id),
    ):
        try:
            as_of = scope.date_from_name(name)
            if as_of is None:
                continue
            snapshot = json.loads(raw)
            cycle = senate_cycle_from_snapshot(snapshot, as_of)
            roster = cycle.require_chamber()
            slots = {(race.state, race.seat_class) for race in cycle.races}
            if len(cycle.states) != len(cycle.races):
                continue
            holdovers = [member for member in roster
                         if (member.state, member.seat_class) not in slots]
            if len(holdovers) + len(cycle.races) != 100:
                continue
            rows = list(conn.execute(
                "SELECT s.seat_name,s.map_id,v.party_id,v.elected FROM votes v "
                "JOIN seats s ON s.id=v.seat_id WHERE v.election_id=?",
                (election_id,),
            ))
            winners = [(state, party) for state, _, party, elected in rows if elected]
            if (any(map_id != scope.map_id or state not in cycle.states
                    or party not in names for state, map_id, party, _ in rows)
                    or len(winners) != len(cycle.races)
                    or {state for state, _ in winners} != cycle.states):
                continue
            counts = {str(party): 0 for _, _, party, _ in rows}
            for _, party in winners:
                counts[str(party)] = counts.get(str(party), 0) + 1
            for member in holdovers:
                matches = by_party.get(member.party, [])
                if len(matches) != 1:
                    raise ValueError("Missing or ambiguous saved Senate party ID")
                party = str(matches[0])
                counts[party] = counts.get(party, 0) + 1
            if sum(counts.values()) != 100:
                continue
            result[election_id] = {
                "context": None, "segment_id": None, "summary": None,
                "central_basis": {
                    "schema": "pf-us-senate-central-roster-v1",
                    "map_id": scope.map_id, "as_of_date": as_of.isoformat(),
                    "target_cycle": cycle.target_year,
                    "roster_id": fingerprint(snapshot),
                    "field_id": fingerprint(sorted(slots)),
                    **({
                        "central_estimator": anchor["estimator"],
                        "house_anchor_policy_sha256": anchor["policy_sha256"],
                        "house_anchor_id": fingerprint(anchor),
                    } if (anchor := senate_central_anchor_from_snapshot(snapshot))
                       is not None else {}),
                },
                "metrics": {"senate_full_seats": {
                    "unit": "seats", "domain": "party", "total": 100,
                    "central": counts,
                }},
            }
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return result


def _us_metadata(
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
            model = _US_MODEL_TYPES[scope.election_type]
            if document["model"] != model:
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
                    raise ValueError("nonintegral recorded US vote")
                actual.append((seat, party, int(votes), bool(winner)))
            actual.sort()
            if actual != sorted(inputs.central_rows):
                continue
            display: dict[str, object] | None = None
            if model == "president":
                context = document["context"]
                target = conn.execute(
                    "SELECT target_election_year FROM elections WHERE id=?",
                    (election_id,),
                ).fetchone()
                matchup = private["options"]["tracked_matchup"]
                if (target is None or target[0] != context["target_cycle"]
                        or not isinstance(matchup, str) or not matchup.strip()):
                    continue
                allocation = get_electoral_votes_sqlite(
                    conn, target[0], (seat.name for seat in inputs.seats),
                )
                saved = private["president_counting"]["allocation"]
                if any(allocation[seat.name] != saved[str(seat.seat_id)]
                       for seat in inputs.seats):
                    continue
                display = {"target_cycle": target[0], "matchup": matchup}
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
                "metrics": {
                    key: {field: value for field, value in metric.items()
                          if field != "ranges"}
                    for key, metric in document["summary"]["metrics"].items()
                },
                "summary": (
                    public_us_document(document)["summary"]
                    if {"house": house_publication_allowed,
                        "senate": senate_publication_allowed,
                        "president": president_publication_allowed}[model](document)
                    else None
                ),
            }
            if display is not None:
                result[election_id]["display"] = display
        except (ValueError, KeyError, TypeError, AttributeError):
            # Unsupported optional metadata cannot supply public uncertainty.
            continue
    return result


def trend_document(scope: OutputScope, entries: list[TrendEntry]) -> object:
    """Publish activated US models with explicit metric and context domains."""
    if scope.election_type in _US_MODEL_TYPES:
        model = _US_MODEL_TYPES[scope.election_type]
        return {"schema": US_TREND_SCHEMA, "model": model, "points": entries}
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
    if scope.election_type in _US_MODEL_TYPES:
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
