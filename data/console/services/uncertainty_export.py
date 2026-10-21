"""What the election export would publish for the current model run.

Read-only: it builds the export's own payloads (``build_result_payload``,
``model_run_uncertainty``, ``trend_uncertainty``) in memory and measures their
serialised text (``compact_json``, the text ``write_json`` writes). Nothing is
written.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from db import Database
from model_support.persistence import (
    StoredUncertainty,
    load_uncertainty,
    uncertainty_status,
    westminster_uns_scope,
)
from model_support.public_uncertainty import (
    PUBLIC_NATIONAL_FIELDS,
    PUBLIC_NATIONAL_PARTY_FIELDS,
    PUBLIC_REGION_FIELDS,
    PUBLIC_REGION_PARTY_FIELDS,
    PUBLIC_SEAT_PARTY_FIELDS,
    basis_label,
    run_size,
    trend_uncertainty,
)
from models import Region, Seat, Vote
from scripts.export.payload import SeatRow, build_result_payload
from scripts.export.serialize import compact_json
from scripts.export.uncertainty import (
    UNCERTAINTY_FILENAME,
    latest_model_run,
    link_uncertainty_payload,
    model_run_uncertainty,
)

@dataclass(frozen=True, slots=True)
class PublishedFile:
    """One export output, as the export would write it now.

    Attributes:
        name: Path under the page's data directory.
        size: Bytes it would have, or None when it is not written.
        details: One line each on what it holds.
    """

    name: str
    size: int | None
    details: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExportPreview:
    """The export's view of the current model run.

    Attributes:
        election_name: The run the export ships as the current prediction.
        basis: The reading guide published with the figures, or None.
        files: What each affected output would contain.
        withheld: Stored fields that are never published, by block.
    """

    election_name: str
    basis: str | None
    files: tuple[PublishedFile, ...]
    withheld: tuple[str, ...]


def _keys(blocks: object) -> set[str]:
    """Every key of the mapping values of ``blocks`` (a mapping of blocks)."""
    keys: set[str] = set()
    for block in blocks.values() if isinstance(blocks, Mapping) else ():
        if isinstance(block, Mapping):
            keys |= set(block)
    return keys


def _withheld(stored: StoredUncertainty) -> tuple[str, ...]:
    """Stored keys outside the public field sets, by block."""

    def private(keys: set[str], public: tuple[str, ...]) -> str:
        return ", ".join(sorted(keys - set(public))) or "none"

    national = stored.national
    lines = [
        f"national: {private(set(national), PUBLIC_NATIONAL_FIELDS)}",
        "national parties: "
        f"{private(_keys(national.get('parties')), PUBLIC_NATIONAL_PARTY_FIELDS)}",
    ]
    if stored.regions is not None:
        region_parties: set[str] = set()
        for region in stored.regions.values():
            if isinstance(region, Mapping):
                region_parties |= _keys(region.get("parties"))
        lines += [
            f"regions: {private(_keys(stored.regions), PUBLIC_REGION_FIELDS)}",
            "region parties: "
            f"{private(region_parties, PUBLIC_REGION_PARTY_FIELDS)}",
        ]
    if stored.seats is not None:
        seat_parties: set[str] = set()
        for seat in stored.seats.values():
            seat_parties |= _keys(seat)
        lines.append(
            f"seat parties: {private(seat_parties, PUBLIC_SEAT_PARTY_FIELDS)}"
        )
    return tuple(lines)


def export_preview(db: Database) -> ExportPreview | None:
    """Describe what the export would publish for its current model run.

    Args:
        db: The console's database.

    Returns:
        The preview, or None when there is no ``model_uns`` run.

    Raises:
        UncertaintyStoreError: The run's stored row is unreadable.
        PublicUncertaintyError: A published field is missing or malformed.
    """
    with db.session() as session:
        election = latest_model_run(session)
        if election is None:
            return None
        rows = session.execute(
            select(Seat.id, Seat.seat_name, Region.id, Region.name)
            .outerjoin(Region, Region.id == Seat.region_id)
            .where(Seat.map_id == election.map_id)
            .order_by(Seat.seat_name)
        ).all()
        # The payload does not use the electorate, so it is not loaded here.
        seats = [SeatRow(row[0], row[1], row[2], row[3], None) for row in rows]
        votes = list(
            session.execute(
                select(Vote)
                .where(Vote.election_id == election.id)
                .options(joinedload(Vote.party))
            ).scalars()
        )
        stored = load_uncertainty(Path(db.config.database_path), election.id)
        published = model_run_uncertainty(election, stored)
        plain = build_result_payload(seats, votes, election_year=election.year)
        with_u = build_result_payload(
            seats,
            votes,
            election_year=election.year,
            seat_uncertainty=published.seat_bands,
        )
        document = link_uncertainty_payload(with_u, published.document)
        election_name = election.name
        map_id = election.map_id

    seat_entries = with_u["seats"]
    with_rows = [seat for seat in seat_entries if "u" in seat]
    rows_total = sum(len(seat["u"]) for seat in with_rows)
    missing = sum(len(seat["p"]) - len(seat["u"]) for seat in with_rows)
    plain_size = len(compact_json(plain).encode("utf-8"))
    simulation_size = len(compact_json(with_u).encode("utf-8"))
    simulation_details = (
        (
            f"{len(with_rows)} of {len(seat_entries)} seats gain u: {rows_total} "
            f"party rows (+{simulation_size - plain_size:,} bytes)",
            f"{missing} p parties have no row (below the stored 0.5-point floor)",
        )
        if with_rows
        else ("No u rows: the run has no stored seat detail.",)
    )
    files = [
        PublishedFile(
            "results/prediction-simulation.json", simulation_size, simulation_details
        )
    ]

    if document is None:
        files.append(
            PublishedFile(
                f"results/{UNCERTAINTY_FILENAME}",
                None,
                (
                    "Not written: the run has no stored regional detail "
                    "(an existing file would be removed).",
                ),
            )
        )
    else:
        national = document["national"]
        regions = document["regions"]
        national_parties = national["parties"] if isinstance(national, dict) else {}
        region_count = len(regions) if isinstance(regions, dict) else 0
        files.append(
            PublishedFile(
                f"results/{UNCERTAINTY_FILENAME}",
                len(compact_json(document).encode("utf-8")),
                (
                    f"schema {document['schema']}, as of {document['as_of_date']}",
                    f"national: {len(national_parties)} parties with v, s, pm, pl "
                    "and seat histogram; hung",
                    f"{region_count} regions with v and s (pm, pl, hung where "
                    "stored)",
                ),
            )
        )

    basis: str | None = None
    if stored is None:
        files.append(
            PublishedFile(
                "results/model_output_trends.json",
                None,
                ("This run's entry gets no uncertainty keys (no stored summary).",),
            )
        )
        withheld: tuple[str, ...] = ()
    else:
        simulations, interval = run_size(stored.national)
        basis = basis_label(interval, simulations)
        trend = trend_uncertainty(stored.national)
        # Each party's keys join its existing object: a comma plus the inner
        # text of {"vl":...}; then the entry's ,"hung":... .
        added = sum(
            len(compact_json(dict(keys))) - 1 for keys in trend.parties.values()
        )
        added += len(f',"hung":{json.dumps(trend.hung)}')
        with_pm = sum("pm" in keys for keys in trend.parties.values())
        with_pl = sum("pl" in keys for keys in trend.parties.values())
        files.append(
            PublishedFile(
                "results/model_output_trends.json",
                None,
                (
                    f"This run's entry: vl/vh for {len(trend.parties)} parties, pm "
                    f"for {with_pm}, pl for {with_pl} (zeros left out), hung "
                    f"{trend.hung}: about +{added:,} bytes",
                    f"{_runs_phrase(_stored_runs(db, map_id))} a stored national "
                    "block, so their entries carry these keys (rebuilt when the "
                    "model runs)",
                ),
            )
        )
        withheld = _withheld(stored)
    return ExportPreview(election_name, basis, tuple(files), withheld)


def _runs_phrase(count: int) -> str:
    return "1 run on this map has" if count == 1 else f"{count} runs on this map have"


def _stored_runs(db: Database, map_id: int) -> int:
    status = uncertainty_status(
        Path(db.config.database_path), westminster_uns_scope(map_id)
    )
    return 0 if status is None else status.stored_runs
