"""House publication boundary: frozen identity, current approval, safe bands."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from model_support.persistence import OutputScope, StoredUncertainty, latest_run
from model_support.public_uncertainty import (
    basis_label, public_us_document, public_us_regional_bands, public_us_seat_bands,
)
from model_support.us_release import HOUSE_RELEASE, house_publication_allowed
from model_support.us_run import fingerprint, restore_run_inputs
from models import Election, ElectionType, Map, Vote
from scripts.export.payload import SeatRow
from scripts.export.uncertainty import ModelUncertaintyExport

HOUSE_MAP_NAME = "US House Districts 2024"
HOUSE_PREFIX = "US House UNS"


def latest_us_model_run(
    session: Session, election_type: ElectionType, map_name: str, prefix: str,
) -> Election | None:
    """Select canonical as-of date before ID; an older rerun stays historical."""
    runs = list(session.scalars(select(Election).join(Map).where(
        Election.type == election_type, Map.name == map_name,
    ).options(joinedload(Election.map))))
    candidates = [(run.id, as_of) for run in runs
                  if (as_of := OutputScope(election_type.value, run.map_id, prefix)
                      .date_from_name(run.name)) is not None]
    latest = latest_run(candidates)
    return next((run for run in runs if latest and run.id == latest[0]), None)


def house_uncertainty_export(
    election: Election, stored: StoredUncertainty | None,
    votes: Sequence[Vote], seats: Sequence[SeatRow],
    *, release_path: Path = HOUSE_RELEASE,
) -> ModelUncertaintyExport:
    """Fail closed for optional odds; central results remain exportable."""
    absent = ModelUncertaintyExport(None, None)
    if stored is None or stored.seats is None:
        return absent
    try:
        document: dict[str, Any] = stored.national
        if stored.election_id != election.id or not house_publication_allowed(
            document, release_path=release_path,
        ):
            return absent
        inputs = restore_run_inputs(document)
        context = document["context"]
        scope = OutputScope("us_house_model", election.map_id, HOUSE_PREFIX)
        as_of = scope.date_from_name(election.name)
        if (election.type != ElectionType.us_house_model or as_of is None
                or as_of.isoformat() != context["as_of_date"]
                or election.map_id != context["map_id"]):
            return absent
        actual = []
        for vote in votes:
            if vote.vote_total is None or vote.vote_total != int(vote.vote_total):
                return absent
            actual.append((vote.seat_id, vote.party_id, int(vote.vote_total),
                           bool(vote.elected)))
        actual.sort()
        if actual != sorted(inputs.central_rows):
            return absent
        if fingerprint([context, inputs.central_rows]) != document["result_identity"]:
            return absent
        private = document["private"]
        if private["artifact_id"] != fingerprint([
            document["result_identity"], document["summary"],
            {key: value for key, value in private.items() if key != "artifact_id"},
        ]):
            return absent
        by_id = {seat.seat_id: seat for seat in seats}
        if set(by_id) != {seat.seat_id for seat in inputs.seats}:
            return absent
        if any(by_id[seat.seat_id].seat_name != seat.name
               or by_id[seat.seat_id].region_id != seat.region_id
               for seat in inputs.seats):
            return absent
        core = public_us_document(document)
        bands = public_us_seat_bands(document, stored.seats)
        if set(bands) != set(by_id) or any(
            set(row) != set(inputs.party_ids) for row in bands.values()
        ):
            return absent
        if stored.regions is not None:
            frozen_members = {
                region: [seat.name for seat in inputs.seats if seat.region_id == region]
                for region in {seat.region_id for seat in inputs.seats
                               if seat.region_id is not None}
            }
            region_rows = stored.regions["regions"]
            if not isinstance(region_rows, dict) or any(
                sorted(region_rows[str(region)]["seat_ids"]) != sorted(
                    seat.seat_id for seat in inputs.seats if seat.region_id == region
                ) for region in frozen_members
            ):
                return absent
            core["regional_bands"] = public_us_regional_bands(
                document, stored.regions, frozen_members,
            )
        summary = document["summary"]
        core["basis"] = basis_label(summary["interval"], summary["simulations"])
        return ModelUncertaintyExport(bands, core, private["artifact_id"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return absent
