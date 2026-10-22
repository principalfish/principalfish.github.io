"""Resolve US polling provenance without inventing respondent counts."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol


class EvidenceReading(Protocol):
    """Evidence attached to an actual weighted poll contributor."""

    @property
    def poll_id(self) -> int: ...
    @property
    def evidence_kind(self) -> str | None: ...
    @property
    def sample_size(self) -> int | None: ...
    @property
    def population_type(self) -> str | None: ...
    @property
    def source_url(self) -> str | None: ...
    @property
    def evidence_source(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class SamplingEvidence:
    """Reported evidence; aggregate estimates deliberately have no sample N."""

    poll_id: int
    kind: str
    population: str | None
    n: int | None
    source: str


def validate_contributor(reading: EvidenceReading) -> SamplingEvidence:
    """Validate only selected contributors when uncertainty is requested.

    Deterministic estimators remain able to consume legacy unknown records.
    Aggregate error is calibrated as an estimator, without synthetic N.
    """
    kind = reading.evidence_kind or "unknown"
    repair = (
        f"US poll {reading.poll_id}: repair provenance/sample size with "
        "scripts/backfill_us_poll_evidence.py before running uncertainty; "
        "--no-uncertainty remains available"
    )
    if kind not in {"individual_poll", "aggregate_estimate"}:
        raise ValueError(f"{repair} (unknown evidence kind)")
    source = reading.evidence_source or reading.source_url
    if not source:
        raise ValueError(f"{repair} (missing source provenance)")
    n = reading.sample_size if kind == "individual_poll" else None
    if reading.population_type not in {None, "LV", "RV", "A"}:
        raise ValueError(f"{repair} (unsupported population)")
    if kind == "individual_poll" and (type(n) is not int or n <= 0):
        raise ValueError(f"{repair} (individual poll has no usable N)")
    return SamplingEvidence(reading.poll_id, kind, reading.population_type, n, source)


def simple_random_sample_moe(n: int | None) -> float | None:
    """90% MoE at 50%, in points, assuming simple random sampling only."""
    return 1.6448536269514722 * 50 / math.sqrt(n) if n is not None and n > 0 else None
