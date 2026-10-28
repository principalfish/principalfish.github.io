"""Pure, explicit official-result contracts for the 2026 validation exercise."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

RESULT_SCHEMA = "pf-us-validation-results-v1"
SNAPSHOT_SCHEMA = "pf-us-validation-snapshot-v1"
POLLING_DAY = date(2026, 11, 3)


class ValidationError(ValueError):
    """An input is incomplete, incompatible, or fails its pinned identity."""


def mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValidationError(f"{label} must be an object with string keys")
    return cast(dict[str, object], value)


def sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be an array")
    return cast(list[object], value)


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be nonempty text")
    return value


def integer(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValidationError(f"{label} must be an integer >= {minimum}")
    return value


def number(value: object, label: str, low: float, high: float) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValidationError(f"{label} must be finite in [{low}, {high}]")
    return float(value)


def digest(value: object) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Input must contain finite JSON values") from exc
    return hashlib.sha256(raw.encode()).hexdigest()


def sha256(value: object, label: str) -> str:
    result = text(value, label)
    if len(result) != 64 or any(c not in "0123456789abcdef" for c in result):
        raise ValidationError(f"{label} must be a lowercase SHA256")
    return result


def model_name(value: object) -> str:
    if value not in ("house", "senate"):
        raise ValidationError("Validation supports only House/Senate 2026")
    return str(value)


def source_refs(value: object, sources: set[str], label: str) -> None:
    refs = sequence(value, label)
    if not refs or any(text(ref, label) not in sources for ref in refs):
        raise ValidationError(f"{label} must reference pinned sources")


def stage(value: object, sources: set[str], label: str) -> dict[str, object]:
    result = mapping(value, label)
    for key in ("identity", "revision", "denominator_policy", "exclusions_policy", "authority"):
        text(result.get(key), f"{label}.{key}")
    try:
        completed = date.fromisoformat(text(result.get("date"), f"{label}.date"))
    except ValueError as exc:
        raise ValidationError(f"{label} has an invalid date") from exc
    if completed < POLLING_DAY:
        raise ValidationError(f"{label} cannot precede polling day")
    if result.get("certification_status") != "certified":
        raise ValidationError(f"{label} requires explicit certified results")
    text(result.get("vote_basis"), f"{label}.vote_basis")
    source_refs(result.get("source_ids"), sources, f"{label}.source_ids")
    return result


@dataclass(frozen=True, slots=True)
class ObservedRace:
    seat_id: int
    identity: dict[str, object]
    votes: dict[int, int]
    denominator: int
    winner_party: int


@dataclass(frozen=True, slots=True)
class OfficialResults:
    model: str
    field: dict[str, object]
    races: tuple[ObservedRace, ...]
    document: dict[str, object]


def evaluation_policy(value: object, model: str) -> dict[str, object]:
    policy = mapping(value, "evaluation policy")
    if (policy.get("schema") != "pf-us-validation-policy-v1"
            or policy.get("model") != model or policy.get("cycle") != 2026):
        raise ValidationError("Evaluation policy must identify its 2026 model")
    for key in ("approval", "provenance", "candidate_aggregation_policy"):
        text(policy.get(key), f"evaluation policy {key}")
    if policy.get("winner_domain") != "raw_model_party":
        raise ValidationError("Evaluation policy requires explicit raw model party winners")
    for name in ("share_stage", "winner_stage"):
        definition = mapping(policy.get(name), f"evaluation policy {name}")
        for key in ("vote_basis", "denominator_policy", "exclusions_policy"):
            text(definition.get(key), f"evaluation policy {name}.{key}")
    return policy


def house_field(value: object) -> dict[str, object]:
    """Require a separately approved exact boundary and district identity."""
    field = mapping(value, "House field")
    sha256(field.get("model_field_id"), "model_field_id")
    sha256(field.get("boundary_sha256"), "boundary_sha256")
    text(field.get("boundary_approval"), "boundary_approval")
    districts = mapping(field.get("districts"), "districts")
    district_ids = [text(value, "district ID") for value in districts.values()]
    if len(districts) != 435 or len(set(district_ids)) != 435:
        raise ValidationError("House field requires 435 distinct exact district IDs")
    for seat, district in districts.items():
        if str(integer(int(seat), "seat ID", 1)) != seat:
            raise ValidationError("House seat IDs must be canonical integers")
        text(district, "district ID")
    return field


def parse_results(value: object) -> OfficialResults:
    """Parse explicit facts; never derive winners, parties, stages or geography."""
    document = mapping(value, "official results")
    if document.get("schema") != RESULT_SCHEMA:
        raise ValidationError("Unsupported official result schema")
    model = model_name(document.get("model"))
    if document.get("cycle") != 2026 or document.get("polling_day") != str(POLLING_DAY):
        raise ValidationError("Official results must identify the 2026 polling day")
    text(document.get("revision"), "revision")
    sources: set[str] = set()
    for raw in sequence(document.get("sources"), "sources"):
        source = mapping(raw, "source")
        source_id = text(source.get("id"), "source ID")
        if source_id in sources:
            raise ValidationError("Duplicate source ID")
        sources.add(source_id)
        for key in ("url", "artifact", "extraction_provenance", "authority"):
            text(source.get(key), f"source.{key}")
        try:
            retrieved = datetime.fromisoformat(text(source.get("retrieved_at"), "retrieved_at"))
            date.fromisoformat(text(source.get("publication_date"), "publication_date"))
        except ValueError as exc:
            raise ValidationError("Source retrieval/publication dates are required") from exc
        if retrieved.tzinfo is None:
            raise ValidationError("Source retrieval time requires an explicit UTC offset")
        sha256(source.get("sha256"), "source.sha256")
    share_stage = stage(document.get("share_stage"), sources, "share_stage")
    winner_stage = stage(document.get("winner_stage"), sources, "winner_stage")
    text(document.get("candidate_aggregation_policy"), "candidate_aggregation_policy")
    if document.get("winner_domain") != "raw_model_party":
        raise ValidationError("Official winner domain must be explicit raw model parties")
    field = (house_field(document.get("field")) if model == "house"
             else mapping(document.get("field"), "Senate field"))
    sha256(field.get("model_field_id"), "model_field_id")
    source_refs(field.get("source_ids"), sources, "field.source_ids")
    races: list[ObservedRace] = []
    slots: set[tuple[str, int]] = set()
    seen: set[int] = set()
    for raw in sequence(document.get("races"), "races"):
        race = mapping(raw, "race")
        seat = integer(race.get("seat_id"), "seat_id", 1)
        if seat in seen:
            raise ValidationError("Duplicate result race")
        seen.add(seat)
        if (race.get("share_stage_id") != share_stage["identity"]
                or race.get("winner_stage_id") != winner_stage["identity"]):
            raise ValidationError("Race must reference the explicit share/winner stages")
        identity = mapping(race.get("identity"), "race identity")
        if model == "house":
            districts = mapping(field["districts"], "districts")
            if (identity.get("district_id") != districts.get(str(seat))
                    or identity.get("boundary_sha256") != field["boundary_sha256"]):
                raise ValidationError("House result district/boundary identity mismatch")
        else:
            state = text(identity.get("state"), "state")
            seat_class = integer(identity.get("seat_class"), "seat_class", 1)
            if seat_class > 3 or type(identity.get("special")) is not bool:
                raise ValidationError("Senate identity needs class and special status")
            if (state, seat_class) in slots:
                raise ValidationError("Duplicate Senate state/class slot")
            slots.add((state, seat_class))
        denominator = integer(race.get("denominator"), "denominator", 1)
        votes: dict[int, int] = {}
        candidates: dict[str, tuple[str, str, int]] = {}
        for raw_candidate in sequence(race.get("candidates"), "candidates"):
            candidate = mapping(raw_candidate, "candidate")
            cid = text(candidate.get("id"), "candidate ID")
            if cid in candidates:
                raise ValidationError("Candidate totals must consolidate fusion lines")
            name = text(candidate.get("name"), "candidate name")
            raw_party = text(candidate.get("raw_party"), "raw party")
            party = integer(candidate.get("model_party_id"), "model_party_id", 1)
            candidates[cid] = (name, raw_party, party)
            votes[party] = votes.get(party, 0) + integer(candidate.get("votes"), "votes")
        if not candidates or sum(votes.values()) != denominator:
            raise ValidationError("Candidate integer totals must equal the denominator")
        for excluded in sequence(race.get("excluded_counts"), "excluded_counts"):
            exclusion = mapping(excluded, "excluded count")
            text(exclusion.get("reason"), "exclusion reason")
            integer(exclusion.get("count"), "excluded count")
        winner = mapping(race.get("winner"), "authoritative winner")
        if winner.get("method") not in ("plurality", "runoff", "rcv_final", "unopposed", "official_tiebreak"):
            raise ValidationError("Authoritative winner requires an explicit selection method")
        cid = text(winner.get("candidate_id"), "winner candidate ID")
        winner_identity = (
            text(winner.get("name"), "winner name"),
            text(winner.get("raw_party"), "winner raw party"),
            integer(winner.get("model_party_id"), "winner model_party_id", 1),
        )
        if cid in candidates and candidates[cid] != winner_identity:
            raise ValidationError("Candidate mapping differs between result stages")
        races.append(ObservedRace(seat, identity, votes, denominator, winner_identity[2]))
    if not races or (model == "house" and len(races) != 435):
        raise ValidationError("A complete comparable official field is required")
    return OfficialResults(model, field, tuple(races), document)
