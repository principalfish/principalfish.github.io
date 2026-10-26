"""Inspect pinned House national anchor candidates; none authorizes projection."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

CATALOGUE_PATH = (
    Path(__file__).resolve().parents[1] / "models/us/house_anchors/candidates-v1.json"
)
_CATALOGUE_SHA256 = "e20765425615bdc43a2e063f0ab260ca6f00e2ebd27bff6e1be9101dabe50757"
_SCHEMA = "pf-us-house-anchor-candidates-v1"
_VIEWS = {"published-general", "final-rcv"}


class HouseAnchorError(ValueError):
    """The offline candidate evidence is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class AnchorCounts:
    democratic: int
    republican: int
    other: int
    denominator: int


@dataclass(frozen=True, slots=True)
class AnchorView:
    identity: str
    status: str
    stage: str
    counts: AnchorCounts | None
    qualification: str


@dataclass(frozen=True, slots=True)
class AnchorYear:
    year: int
    source_url: str
    source_sha256: str
    source_publisher: str
    measured_units: int
    unmeasured_units: tuple[str, ...]
    uncertified_units: tuple[str, ...]
    population: str
    denominator_policy: str
    extraction_json: str
    corrections_json: str
    views: tuple[AnchorView, ...]

    def view(self, identity: str) -> AnchorView:
        """Return an audit candidate, never an approved runtime reference."""
        for view in self.views:
            if view.identity == identity:
                return view
        raise HouseAnchorError(f"Unknown {self.year} House anchor view: {identity}")


@dataclass(frozen=True, slots=True)
class HouseAnchorCatalogue:
    identity: str
    sha256: str
    years: tuple[AnchorYear, ...]

    def for_baselines(self, baseline_ids: Iterable[str]) -> tuple[AnchorYear, ...]:
        """Inspect only years named by actual Senate race baseline identities."""
        years: set[int] = set()
        for baseline_id in baseline_ids:
            match = re.fullmatch(r"([0-9]{4})-us-senate", baseline_id)
            if match is None:
                raise HouseAnchorError(f"Invalid Senate baseline: {baseline_id}")
            years.add(int(match[1]))
        if not years:
            raise HouseAnchorError("At least one Senate race baseline is required")
        candidates = {item.year: item for item in self.years}
        if missing := years - candidates.keys():
            raise HouseAnchorError(f"Missing House anchor years: {sorted(missing)}")
        return tuple(candidates[year] for year in sorted(years))


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise HouseAnchorError("Expected an object with string keys")
    return cast(dict[str, object], value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise HouseAnchorError("Expected an array")
    return cast(list[object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HouseAnchorError("Expected nonempty text")
    return value


def _integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise HouseAnchorError("Expected a nonnegative integer")
    return value


def _sha256(value: object) -> str:
    result = _text(value)
    if re.fullmatch(r"[a-f0-9]{64}", result) is None:
        raise HouseAnchorError("Expected a SHA256 digest")
    return result


def _url(value: object) -> str:
    result = _text(value)
    parsed = urlparse(result)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username:
        raise HouseAnchorError("Expected a public HTTPS source URL")
    return result


def _units(value: object) -> tuple[str, ...]:
    result = tuple(_text(item) for item in _array(value))
    if len(set(result)) != len(result) or any(
        re.fullmatch(r"[A-Z]{2}-[0-9]{2}", unit) is None for unit in result
    ):
        raise HouseAnchorError("Invalid or duplicate House units")
    return result


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _view(value: object) -> AnchorView:
    item = _object(value)
    identity = _text(item.get("id"))
    status = _text(item.get("status"))
    if identity not in _VIEWS or status not in {"pending", "blocked", "unavailable"}:
        raise HouseAnchorError("Unknown anchor view or candidate status")
    counts = None
    if item.get("counts") is not None:
        raw = _object(item["counts"])
        if set(raw) != {"D", "R", "other", "denominator"}:
            raise HouseAnchorError("Anchor counts require D/R/Other and denominator")
        counts = AnchorCounts(*(_integer(raw[key]) for key in (
            "D", "R", "other", "denominator",
        )))
        if (min(counts.democratic, counts.republican, counts.denominator) <= 0
                or counts.democratic + counts.republican + counts.other
                != counts.denominator):
            raise HouseAnchorError("Anchor valid-vote count sum differs")
    elif status != "unavailable":
        raise HouseAnchorError("A candidate count view needs integer counts")
    return AnchorView(
        identity, status, _text(item.get("stage")), counts,
        _text(item.get("qualification")),
    )


def _year(value: object) -> AnchorYear:
    item = _object(value)
    year = _integer(item.get("year"))
    if year not in {2016, 2018, 2020, 2022, 2024}:
        raise HouseAnchorError("Unexpected House anchor election year")
    source = _object(item.get("source"))
    extraction = _object(item.get("extraction"))
    for key in (
        "id", "sheet", "selection", "party_grouping", "stage_columns", "reference_unit",
    ):
        _text(extraction.get(key))
    _sha256(extraction.get("selected_records_sha256"))
    rows = tuple(_integer(row) for row in _array(extraction.get("record_numbers")))
    if not rows or rows != tuple(sorted(set(rows))) or rows[0] <= 0:
        raise HouseAnchorError("Source row numbers must be positive and unique")
    unmeasured = _units(item.get("unmeasured_units"))
    uncertified = _units(item.get("uncertified_units"))
    measured = _integer(item.get("measured_units"))
    if _integer(item.get("regular_units")) != 435 or measured + len(unmeasured) != 435:
        raise HouseAnchorError("Measured and unmeasured House units must total 435")
    if set(unmeasured) & set(uncertified):
        raise HouseAnchorError("An uncertified measured unit cannot be unmeasured")
    views = tuple(_view(view) for view in _array(item.get("views")))
    if len(views) != 2 or {view.identity for view in views} != _VIEWS:
        raise HouseAnchorError("Exactly two distinct named count views are required")
    if year in {2016, 2018} and any(view.status != "blocked" for view in views):
        raise HouseAnchorError("Historical party/certification policies remain blocked")
    if year == 2024 and any(view.status != "unavailable" for view in views):
        raise HouseAnchorError("2024 evidence cannot nominate an available anchor")
    corrections = _array(item.get("corrections"))
    correction_ids: set[str] = set()
    for raw in corrections:
        correction = _object(raw)
        identity = _text(correction.get("id"))
        if identity in correction_ids:
            raise HouseAnchorError("Duplicate anchor correction")
        correction_ids.add(identity)
        _url(correction.get("source_url"))
        _text(correction.get("source_identity"))
        _text(correction.get("rule"))
        source_hash = correction.get("source_sha256")
        if source_hash is not None:
            _sha256(source_hash)
        else:
            _text(correction.get("verification_limit"))
    return AnchorYear(
        year, _url(source.get("url")), _sha256(source.get("sha256")),
        _text(source.get("publisher")), measured, unmeasured, uncertified,
        _text(item.get("population")), _text(item.get("denominator_policy")),
        _canonical(extraction), _canonical(corrections), views,
    )


def inspect_house_anchor_catalogue(path: Path = CATALOGUE_PATH) -> HouseAnchorCatalogue:
    """Read integrity-pinned candidates without DB, network or runtime policy."""
    try:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != _CATALOGUE_SHA256:
            raise HouseAnchorError("House anchor catalogue bytes differ from pinned v1")
        document = _object(json.loads(raw))
        if (document.get("schema") != _SCHEMA
                or document.get("accepted") is not False
                or document.get("activation_policy") is not None):
            raise HouseAnchorError("House anchor candidate identity/approval differs")
        payload = _object(document.get("payload"))
        if hashlib.sha256(_canonical(payload).encode()).hexdigest() != _sha256(
            document.get("payload_sha256"),
        ):
            raise HouseAnchorError("House anchor payload hash differs")
        years = tuple(_year(item) for item in _array(payload.get("years")))
        if tuple(item.year for item in years) != (2016, 2018, 2020, 2022, 2024):
            raise HouseAnchorError("House anchor years must be unique and complete")
        return HouseAnchorCatalogue(_text(document.get("id")), digest, years)
    except (OSError, json.JSONDecodeError) as exc:
        raise HouseAnchorError(f"Cannot read House anchor candidates: {exc}") from exc
