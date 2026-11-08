"""The New York Times results JSON files the US results import reads.

Each source is keyed by ``(year, chamber, kind)``: the cycle year, ``"house"``
or ``"senate"``, and ``"general"`` or ``"runoff"``. A runoff is filed under the
cycle it settles, so the January 2021 Georgia Senate runoffs are
``(2020, "senate", "runoff")``.

NYT changed its results format between cycles; ``schema`` names the layout a
file uses so a parser can be picked for it:

- ``"A"``: 2024 onwards. Top-level ``races`` plus ``resultsMeta`` (whose
  ``resultsUpdatedAt`` is the feed's own updated-at time).
- ``"A2"``: 2022, an older variant of A with the same ``resultsMeta``.
- ``"B"``: 2020 and its December 2020 / January 2021 runoffs. Top-level
  ``data`` and ``meta`` (``meta.timestamp`` is the updated-at time).
- ``"C"``: 2018 and its November 2018 runoff. The general file holds every
  office, so House and Senate share a URL; the top-level ``generated`` is the
  updated-at time.

Every runoff whose result decides a seat is listed, since the decisive round
is the one stored. Each key holds a single file.

A ``pending`` source is a contest that has not been held yet, so it has no URL
(and, until it is scheduled, no date).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal

Chamber = Literal["house", "senate"]
Kind = Literal["general", "runoff"]
SchemaId = Literal["A", "A2", "B", "C"]
SourceKey = tuple[int, Chamber, Kind]


class SourceStatus(StrEnum):
    """Whether a source's file can be fetched yet."""

    AVAILABLE = "available"
    PENDING = "pending"


class UnknownSourceError(LookupError):
    """Raised when no source is listed for a ``(year, chamber, kind)`` key."""


@dataclass(frozen=True, slots=True)
class Source:
    """One NYT results JSON file and how to read it.

    Attributes:
        year: Election cycle year the results belong to.
        chamber: ``"house"`` or ``"senate"``.
        kind: ``"general"`` or ``"runoff"``.
        schema: Layout of the file (see the module docstring).
        status: ``AVAILABLE`` when ``url`` can be fetched, ``PENDING`` when the
            contest has not been held.
        url: The file's URL; ``None`` only while pending.
        election_date: Polling day; ``None`` only while pending and unscheduled.
        note: Free-text detail about the source, such as which races it covers.
    """

    year: int
    chamber: Chamber
    kind: Kind
    schema: SchemaId
    status: SourceStatus
    url: str | None
    election_date: date | None
    note: str = ""

    def __post_init__(self) -> None:
        """Check that an available source has a URL and a date."""
        if self.status is SourceStatus.AVAILABLE and (
            self.url is None or self.election_date is None
        ):
            raise ValueError(f"available source {self.key} needs a url and a date")

    @property
    def key(self) -> SourceKey:
        """The ``(year, chamber, kind)`` key this source is listed under."""
        return (self.year, self.chamber, self.kind)


_STATIC = "https://static01.nyt.com/elections-assets"
_A26 = f"{_STATIC}/pages/data/2026-11-03"
_A24 = f"{_STATIC}/pages/data/2024-11-05"
_A2_22 = f"{_STATIC}/2022/data/2022-11-08"
_A2_22_RUNOFF = f"{_STATIC}/2022/data/2022-12-06"
_B20 = f"{_STATIC}/2020/data/api/2020-11-03/national-map-page/national"
_B20_RUNOFF = f"{_STATIC}/2020/data/api/2020-12-05/state-page"
_B21_RUNOFF = f"{_STATIC}/2020/data/api/2021-01-05/state-page"
_C18_BASE = "https://int.nyt.com/applications/elections/2018/api/1/races"
_C18 = f"{_C18_BASE}/2018-11-06.json"


def _available(
    key: SourceKey,
    schema: SchemaId,
    url: str,
    election_date: date,
    note: str = "",
) -> Source:
    year, chamber, kind = key
    return Source(
        year=year,
        chamber=chamber,
        kind=kind,
        schema=schema,
        status=SourceStatus.AVAILABLE,
        url=url,
        election_date=election_date,
        note=note,
    )


_SOURCE_LIST: tuple[Source, ...] = (
    _available(
        (2026, "house", "general"),
        "A",
        f"{_A26}/results-house.json",
        date(2026, 11, 3),
    ),
    _available(
        (2026, "senate", "general"),
        "A",
        f"{_A26}/results-senate.json",
        date(2026, 11, 3),
    ),
    Source(
        year=2026,
        chamber="house",
        kind="runoff",
        schema="A",
        status=SourceStatus.PENDING,
        url=None,
        election_date=None,
        note=(
            "Louisiana district 5 December runoff; not yet held, so no URL or "
            "date. Its schema is assumed to match the 2026 general files."
        ),
    ),
    _available(
        (2024, "house", "general"),
        "A",
        f"{_A24}/results-house.json",
        date(2024, 11, 5),
    ),
    _available(
        (2024, "senate", "general"),
        "A",
        f"{_A24}/results-senate.json",
        date(2024, 11, 5),
    ),
    _available(
        (2022, "house", "general"),
        "A2",
        f"{_A2_22}/results-house.json",
        date(2022, 11, 8),
    ),
    _available(
        (2022, "senate", "general"),
        "A2",
        f"{_A2_22}/results-senate.json",
        date(2022, 11, 8),
        note="Also carries the Georgia runoff race GA-G-S-2022-12-06.",
    ),
    _available(
        (2022, "senate", "runoff"),
        "A2",
        f"{_A2_22_RUNOFF}/results-georgia-us-senate-runoff.json",
        date(2022, 12, 6),
        note="Georgia Senate runoff (race GA-G-S-2022-12-06) only.",
    ),
    _available(
        (2020, "house", "general"),
        "B",
        f"{_B20}/house.json",
        date(2020, 11, 3),
    ),
    _available(
        (2020, "senate", "general"),
        "B",
        f"{_B20}/senate.json",
        date(2020, 11, 3),
    ),
    _available(
        (2020, "senate", "runoff"),
        "B",
        f"{_B21_RUNOFF}/georgia.json",
        date(2021, 1, 5),
        note=(
            "Georgia state page: both Senate runoffs (GA-G-S-2021-01-05 and "
            "the special GA-S-S-2021-01-05) plus a Public Service Commission "
            "runoff, which the importer should skip."
        ),
    ),
    _available(
        (2020, "house", "runoff"),
        "B",
        f"{_B20_RUNOFF}/louisiana.json",
        date(2020, 12, 5),
        note=(
            "Louisiana state page: the district 5 House runoff "
            "(LA-G-H-5-2020-12-05) plus a ballot measure and a Public Service "
            "Commission runoff, which the importer should skip."
        ),
    ),
    _available(
        (2018, "senate", "runoff"),
        "C",
        f"{_C18_BASE}/2018-11-27.json",
        date(2018, 11, 27),
        note="Mississippi special Senate runoff (ms-27192-2018-11-27) only.",
    ),
    _available(
        (2018, "house", "general"),
        "C",
        _C18,
        date(2018, 11, 6),
        note="Shared with the Senate source: one file holds every office.",
    ),
    _available(
        (2018, "senate", "general"),
        "C",
        _C18,
        date(2018, 11, 6),
        note="Shared with the House source: one file holds every office.",
    ),
)

SOURCES: dict[SourceKey, Source] = {source.key: source for source in _SOURCE_LIST}


def source_for(year: int, chamber: Chamber, kind: Kind = "general") -> Source:
    """Return the source listed for ``(year, chamber, kind)``.

    Args:
        year: Election cycle year.
        chamber: ``"house"`` or ``"senate"``.
        kind: ``"general"`` (default) or ``"runoff"``.

    Returns:
        The matching source, which may be pending.

    Raises:
        UnknownSourceError: If no source is listed for the key.
    """
    try:
        return SOURCES[(year, chamber, kind)]
    except KeyError as err:
        listed = ", ".join(f"{y}/{c}/{k}" for y, c, k in sorted(SOURCES))
        raise UnknownSourceError(
            f"no NYT source for {year}/{chamber}/{kind}; listed: {listed}",
        ) from err


def supported_years() -> tuple[int, ...]:
    """Return the cycle years that have at least one source, oldest first."""
    return tuple(sorted({year for year, _, _ in SOURCES}))
