"""Export Senate forecast metadata from its saved field and chamber snapshot."""

from __future__ import annotations

import copy
import json
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from model_support.persistence import OutputScope, latest_run
from model_support.senate_cycles import (
    SenateCycle,
    resolve_senate_cycle,
    senate_cycle_from_snapshot,
)
from models import Election, ElectionType, Map, Seat, Vote


def senate_run_date(election: Election) -> date | None:
    scope = OutputScope("us_senate_model", election.map_id, "US Senate UNS")
    return scope.date_from_name(election.name)


def latest_senate_run(session: Session) -> Election | None:
    """Select the latest dated canonical Senate model/map/prefix scope."""
    runs = list(session.scalars(select(Election).join(Election.map).where(
        Election.type == ElectionType.us_senate_model,
        Map.name == "US Senate 2024",
    ).options(joinedload(Election.map))))
    latest = latest_run(
        (run.id, as_of) for run in runs
        if (as_of := senate_run_date(run)) is not None
    )
    return next((run for run in runs if latest and run.id == latest[0]), None)


def senate_cycle_for_election(
    session: Session, election: Election
) -> SenateCycle | None:
    """Legacy partial fields stay central-only; saved inputs bypass live files."""
    as_of = senate_run_date(election)
    if as_of is None:
        return None
    if election.election_date is not None and election.election_date != as_of:
        raise ValueError("Senate election date disagrees with its run name")
    stored: str | None = None
    if session.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_context'"
    )).first():
        stored = session.execute(text(
            "SELECT payload_json FROM model_context WHERE election_id=:id"
        ), {"id": election.id}).scalar_one_or_none()
    if stored is not None:
        cycle = senate_cycle_from_snapshot(json.loads(stored), as_of)
    else:
        # Only the verified historical 2026 field has a legacy interpretation.
        if as_of.year != 2026:
            return None
        cycle = resolve_senate_cycle(as_of)
    states = set(session.scalars(select(Seat.seat_name).join(
        Vote, Vote.seat_id == Seat.id
    ).where(Vote.election_id == election.id).distinct()))
    if states != cycle.states:
        if stored is not None:
            raise ValueError("Saved Senate field disagrees with its recorded vote rows")
        return None
    return cycle


def public_senate_forecast(cycle: SenateCycle, map_id: int) -> dict[str, Any]:
    """Only the display/counting identity needed by the frontend is public."""
    roster_id = (
        f"senate-forecast-roster-{cycle.roster_sha256}"
        if cycle.roster is not None else None
    )
    return {
        "configId": cycle.identity,
        "targetYear": cycle.target_year,
        "regularClass": cycle.regular_class,
        "baselineElectionId": cycle.baseline_id,
        "nextElectionDate": cycle.election_date,
        "mapId": map_id,
        "specials": [
            {"seat": race.state, "class": race.seat_class, "year": cycle.target_year,
             "baselineElectionId": race.baseline_id}
            for race in cycle.races if race.special
        ],
        "rosterElectionId": roster_id,
        "chamberUnavailable": None if roster_id else (
            "A verified post-election roster and control policy "
            "are not yet configured."
        ),
    }


def pinned_roster_payload(cycle: SenateCycle) -> dict[str, Any]:
    cycle.require_chamber()
    if cycle.roster_json is None:
        raise ValueError("Pinned Senate roster is unavailable")
    payload: dict[str, Any] = json.loads(cycle.roster_json)
    return payload


def configure_senate_forecast(
    config: dict[str, Any], forecast: dict[str, Any] | None
) -> dict[str, Any]:
    """Keep actual/current Senate data separate from the frozen forecast baseline."""
    result = copy.deepcopy(config)
    feature = result.get("parliamentFeatures", {}).get("us_senate")
    if not isinstance(feature, dict):
        return result
    predict = feature.get("predict")
    if not isinstance(predict, dict):
        return result
    # No known counting context means no derived full-chamber view.
    predict.pop("chamberElectionId", None)
    predict.pop("regularClass", None)
    predict["tabs"] = [{"key": "seatsup", "label": "Seats up"}]
    if forecast is None:
        return result
    year = forecast["targetYear"]
    predict["regularClass"] = forecast["regularClass"]
    feature["nextElectionYear"] = year
    feature["nextElectionDate"] = forecast["nextElectionDate"]
    feature["predictBaselineElectionId"] = forecast["baselineElectionId"]
    predict["tabs"] = [{"key": "seatsup", "label": f"Seats up ({year})"}]
    roster_id = forecast["rosterElectionId"]
    if roster_id:
        predict["chamberElectionId"] = roster_id
        predict["tabs"].append({"key": "chamber", "label": "Full Senate"})
        feature["hiddenElectionIds"] = list(dict.fromkeys([
            *feature.get("hiddenElectionIds", []), roster_id,
        ]))
    mode = result.get("mapModes", {}).get(str(forecast["mapId"]))
    if isinstance(mode, dict):
        mode["senateSpecialElections"] = forecast["specials"]
        mode["senateForecastYear"] = year
    return result
