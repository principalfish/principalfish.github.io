"""The Westminster model run's published uncertainty, for the election export.

A model run's stored summary (``persistence.load_uncertainty``) feeds two
outputs, both through the :mod:`model_support.public_uncertainty` whitelist:

* the per-seat ``u`` rows of ``results/prediction-simulation.json`` (from the
  seat block), and
* ``results/prediction-uncertainty.json`` (``pf-uncertainty-v1``, from the
  national and region blocks).

Both need the run's full detail, which only the latest-dated run keeps. A run
without it publishes neither, and an uncertainty file left from an earlier
run is removed so it cannot sit beside a newer projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from model_support.persistence import (
    StoredUncertainty,
    latest_run,
    westminster_uns_scope,
)
from model_support.public_uncertainty import SeatBand, public_document, seat_bands
from models import Election, ElectionType
from scripts.export.serialize import write_json

UNCERTAINTY_FILENAME = "prediction-uncertainty.json"


@dataclass(frozen=True, slots=True)
class ModelUncertaintyExport:
    """What one model run publishes.

    Attributes:
        seat_bands: Seat id -> party id -> band for the result payload's
            ``u`` rows, or None without a seat block.
        document: The ``pf-uncertainty-v1`` document, or None without a
            region block.
    """

    seat_bands: dict[int, dict[int, SeatBand]] | None
    document: dict[str, object] | None


def model_uncertainty_export(
    stored: StoredUncertainty | None, *, election_name: str, as_of_date: str | None
) -> ModelUncertaintyExport:
    """Build a model run's published uncertainty from its stored summary.

    Args:
        stored: The run's stored summary, or None when it has none.
        election_name: The run's election name.
        as_of_date: Its as-of date, ISO format, or None.

    Returns:
        The seat bands and document; each None when its block is absent
        (never stored, or pruned because a later-dated run exists).

    Raises:
        PublicUncertaintyError: A published field is missing or malformed.
    """
    if stored is None:
        return ModelUncertaintyExport(None, None)
    bands = None if stored.seats is None else seat_bands(stored.seats)
    document = (
        None
        if stored.regions is None
        else public_document(
            stored.national,
            stored.regions,
            election_name=election_name,
            as_of_date=as_of_date,
        )
    )
    return ModelUncertaintyExport(bands, document)


def latest_model_run(session: Session) -> Election | None:
    """Return the ``model_uns`` run the export ships as the current prediction.

    The latest dated run (:func:`model_support.persistence.latest_run`: as-of
    date parsed from the name, then id), the same run that keeps the detailed
    summary. Runs whose name carries no date are never chosen. Re-running an
    older date therefore does not replace the current prediction.
    """
    runs = [
        (election_id, as_of)
        for election_id, name, map_id in session.execute(
            select(Election.id, Election.name, Election.map_id).where(
                Election.type == ElectionType.model_uns
            )
        )
        if (as_of := westminster_uns_scope(map_id).date_from_name(name)) is not None
    ]
    latest = latest_run(runs)
    if latest is None:
        return None
    return session.execute(
        select(Election)
        .where(Election.id == latest[0])
        .options(joinedload(Election.map))
    ).scalar_one()


def model_run_uncertainty(
    election: Election, stored: StoredUncertainty | None
) -> ModelUncertaintyExport:
    """Return what a ``model_uns`` run publishes from its stored summary.

    Args:
        election: The model run being exported.
        stored: Its stored summary (``load_uncertainty``), or None.

    Returns:
        Its seat bands and ``pf-uncertainty-v1`` document, each None when the
        run lacks that block.

    Raises:
        PublicUncertaintyError: A published field is missing or malformed.
        ValueError: ``stored`` belongs to another election.
    """
    if stored is not None and stored.election_id != election.id:
        raise ValueError(
            f"Summary of election {stored.election_id} given for {election.id}"
        )
    as_of = westminster_uns_scope(election.map_id).date_from_name(election.name)
    return model_uncertainty_export(
        stored,
        election_name=election.name,
        as_of_date=as_of.isoformat() if as_of is not None else None,
    )


def sync_uncertainty_file(
    results_dir: Path, document: dict[str, object] | None, *, dry_run: bool
) -> None:
    """Write the uncertainty file, or remove a stale one when there is none.

    With no document and no existing file this does nothing and prints
    nothing, so an export without uncertainty is unchanged.

    Args:
        results_dir: The page's ``results`` directory.
        document: The document to publish, or None.
        dry_run: Print the action instead of taking it.
    """
    path = results_dir / UNCERTAINTY_FILENAME
    if document is not None:
        regions = document["regions"]
        count = len(regions) if isinstance(regions, dict) else 0
        if dry_run:
            print(f"Would write uncertainty: {path} ({count} regions)")
        else:
            write_json(path, document)
            print(f"Wrote uncertainty: {path} ({count} regions)")
        return
    if not path.exists():
        return
    if dry_run:
        print(f"Would remove stale uncertainty file: {path}")
    else:
        path.unlink()
        print(f"Removed stale uncertainty file: {path}")
