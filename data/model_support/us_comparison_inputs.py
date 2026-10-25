"""Validated offline evidence for unaccepted US comparison reports."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from polls.importers.us.us_geography import STATE_POSTAL

JsonObject = dict[str, object]
DIRECTORY = Path(__file__).resolve().parents[1] / "models/us/calibration"
PARTY_KEYS = (
    "democrat", "republican", "independent", "libertarian", "usgreen", "others",
)
PARTY_NAMES = (
    "Democratic", "Republican", "Independent", "Libertarian", "US Green", "Others",
)


class ComparisonError(ValueError):
    """Offline evidence or a comparison request is inconsistent."""


def obj(value: object) -> JsonObject:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ComparisonError("expected an object with string keys")
    return cast(JsonObject, value)


def seq(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ComparisonError("expected an array")
    return cast(list[object], value)


def number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ComparisonError("expected a finite number")
    if not math.isfinite(value):
        raise ComparisonError("expected a finite number")
    return float(value)


def count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ComparisonError("expected a nonnegative integer count")
    return value


def text_value(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ComparisonError("expected nonempty text")
    return value


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()).hexdigest()


def source_hash(value: object) -> str:
    result = text_value(value)
    if len(result) != 64 or any(c not in "0123456789abcdef" for c in result):
        raise ComparisonError("invalid source SHA256")
    return result


def margin(d: float, r: float, total: float, *, decided: bool) -> float:
    denominator = d + r if decided else total
    if (not all(math.isfinite(value) for value in (d, r, total))
            or min(d, r) < 0 or denominator <= 0 or total < d + r):
        raise ComparisonError("invalid contrast denominator")
    return 100 * (d - r) / denominator


@dataclass(frozen=True, slots=True)
class NationalFact:
    cycle: int
    poll: tuple[float, float]
    result: tuple[int, int, int]
    included: bool
    provenance: JsonObject

    def error(self, *, decided: bool = False) -> float:
        return margin(*self.poll, 100, decided=decided) - margin(
            *self.result, decided=decided,
        )


@dataclass(frozen=True, slots=True)
class UnitFact:
    key: str
    state: str
    baseline: tuple[int, ...]
    observed: tuple[int, ...]
    reported_total: int
    issues: tuple[str, ...]
    provenance: JsonObject
    # President targets resolve D/R only; the remainder is not an engine party.
    complete_target_parties: bool = True


@dataclass(frozen=True, slots=True)
class CycleFact:
    cycle: int
    units: tuple[UnitFact, ...]
    national: NationalFact
    denominator_policy: str


@dataclass(frozen=True, slots=True)
class ComparisonData:
    model: str
    input_sha256: str
    payload_sha256: str
    payload: JsonObject
    national: tuple[NationalFact, ...]
    cycles: tuple[CycleFact, ...]
    proxies: tuple[JsonObject, ...]
    ledger: tuple[JsonObject, ...]


def load_payload(model: str, path: Path | None = None) -> tuple[JsonObject, str]:
    if model not in {"president", "senate"}:
        raise ComparisonError("comparison model must be president or senate")
    raw = (path or DIRECTORY / f"{model}-comparison-inputs.json").read_bytes()
    document = obj(json.loads(raw))
    if (document.get("schema") != "pf-us-comparison-inputs-v1"
            or document.get("model") != model or document.get("accepted") is not False):
        raise ComparisonError("invalid comparison input identity/acceptance")
    payload = obj(document["payload"])
    if fingerprint(payload) != document.get("payload_sha256"):
        raise ComparisonError("comparison input payload fingerprint differs")
    source_hash(payload.get("source_inventory_sha256"))
    return payload, hashlib.sha256(raw).hexdigest()


def major_result(record: JsonObject) -> tuple[int, int, int]:
    return (count(record["d_votes"]), count(record["r_votes"]),
            count(record["total_votes"]))


def stats(values: Sequence[float]) -> JsonObject:
    if not values:
        return {"n": 0, "mean": None, "rms": None, "mean_square": None}
    mean = sum(values) / len(values)
    square = sum(x * x for x in values) / len(values)
    return {"n": len(values), "mean": mean, "rms": math.sqrt(square),
            "mean_square": square,
            "centered_mean_square": sum((x - mean) ** 2 for x in values) / len(values)}


def national_record(record: JsonObject) -> NationalFact:
    raw = obj(record["raw_shares"])
    official = obj(record["official_result"])
    source_hash(official["sha256"])
    fact = NationalFact(
        count(record["year"]),
        (number(obj(raw["D"])["percentage"]),
         number(obj(raw["R"])["percentage"])),
        major_result(official), True, record,
    )
    fact.error()
    return fact


def proxy_diagnostics(
    record: JsonObject, poll: tuple[float, float], votes: tuple[int, int],
    total: int,
    *, contrast_axis: str = "D-R", contrast_group: str = "D-R",
) -> JsonObject:
    return {
        "cycle": count(record["year"]), "source_record": record,
        "raw_poll_levels": list(poll), "selected_candidate_votes": list(votes),
        "reported_total": total,
        "contrast_axis": contrast_axis, "contrast_group": contrast_group,
        "raw_error": margin(*poll, 100, decided=False)
        - margin(*votes, total, decided=False),
        "decided_error": margin(*poll, 100, decided=True)
        - margin(*votes, total, decided=True),
        "scope": "Total average error; not an admitted individual-poll input",
        "accepted": False,
    }


def canonical_state_names() -> dict[str, str]:
    return {code: name for name, code in STATE_POSTAL.items()}
