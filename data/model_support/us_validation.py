"""Pure scoring of self-contained saved runs against explicit official facts.

No database, model execution, mutable roster or current calibration is consulted.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from model_support.us_validation_contracts import (
    POLLING_DAY,
    SNAPSHOT_SCHEMA,
    OfficialResults,
    ValidationError,
    digest,
    evaluation_policy,
    house_field,
    integer,
    mapping,
    model_name,
    number,
    sequence,
    sha256,
    text,
)


def validate_snapshot(value: object) -> dict[str, object]:
    snapshot = mapping(value, "snapshot")
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise ValidationError("Unsupported snapshot schema")
    model_name(snapshot.get("model"))
    map_id = integer(snapshot.get("map_id"), "snapshot map_id", 1)
    text(snapshot.get("name_prefix"), "snapshot name_prefix")
    sha256(snapshot.get("preregistration_sha256"), "preregistration SHA256")
    code_hashes = mapping(snapshot.get("capture_code_sha256"), "capture code hashes")
    if not code_hashes:
        raise ValidationError("Capture code hashes are required")
    for value_to_check in code_hashes.values():
        sha256(value_to_check, "capture code SHA256")
    if snapshot.get("cycle") != 2026 or snapshot.get("polling_day") != str(POLLING_DAY):
        raise ValidationError("Snapshot must identify the 2026 polling day")
    if snapshot.get("snapshot_sha256") != digest({
        k: v for k, v in snapshot.items() if k != "snapshot_sha256"
    }):
        raise ValidationError("Snapshot checksum mismatch")
    try:
        captured = datetime.fromisoformat(text(snapshot.get("captured_at"), "captured_at"))
    except ValueError as exc:
        raise ValidationError("Invalid snapshot capture time") from exc
    if captured.tzinfo is None:
        raise ValidationError("Capture time needs an explicit UTC offset")
    capture_kind = (
        "pre_result_capture_attestation"
        if captured.astimezone(timezone.utc).date() < POLLING_DAY else "retrospective"
    )
    if snapshot.get("capture_kind") != capture_kind:
        raise ValidationError("Capture label differs from its recorded time")
    if snapshot.get("evaluation_policy") is not None:
        evaluation_policy(snapshot["evaluation_policy"], str(snapshot["model"]))
    seen: set[int] = set()
    for raw in sequence(snapshot.get("runs"), "snapshot runs"):
        run = mapping(raw, "run")
        rid = integer(run.get("election_id"), "election_id", 1)
        if rid in seen:
            raise ValidationError("Duplicate frozen election ID")
        seen.add(rid)
        as_of = text(run.get("as_of_date"), "as_of_date")
        if date.fromisoformat(as_of).isoformat() != as_of:
            raise ValidationError("Run date must be canonical YYYY-MM-DD")
        if run.get("run_sha256") != digest({
            k: v for k, v in run.items() if k != "run_sha256"
        }):
            raise ValidationError("Frozen run checksum mismatch")
        hashes = mapping(run.get("hashes"), "run hashes")
        for key in ("document", "race_detail", "central_rows"):
            if hashes.get(key) != digest(run.get(key)):
                raise ValidationError(f"Frozen {key} checksum mismatch")
        if run.get("document") is None:
            continue
        document = mapping(run["document"], "document")
        if document.get("schema") != "pf-us-model-uncertainty-v1":
            raise ValidationError("Unsupported frozen uncertainty schema")
        context = mapping(document.get("context"), "context")
        if (document.get("model") != snapshot["model"]
                or context.get("model") != snapshot["model"]
                or context.get("map_id") != map_id
                or context.get("target_cycle") != 2026
                or context.get("as_of_date") != run["as_of_date"]):
            raise ValidationError("Frozen run scope/context mismatch")
        private = mapping(document.get("private"), "private")
        if private.get("schema") != "pf-us-source-replay-v1":
            raise ValidationError("Unsupported frozen private input schema")
        inputs = mapping(private.get("inputs"), "inputs")
        if inputs.get("as_of_date") != run["as_of_date"] or inputs.get("model") != f"us-{snapshot['model']}":
            raise ValidationError("Frozen inputs differ from the run model/date")
        if private.get("artifact_id") != digest([
            document.get("result_identity"), document.get("summary"),
            {k: v for k, v in private.items() if k != "artifact_id"},
        ]):
            raise ValidationError("Saved artifact checksum mismatch")
        if document.get("result_identity") != digest([
            context, inputs.get("central_rows"),
        ]):
            raise ValidationError("Saved central result identity mismatch")
        frozen_rows = sequence(inputs.get("central_rows"), "input central rows")
        actual_rows = sequence(run.get("central_rows"), "stored central rows")
        if sorted(digest(row) for row in frozen_rows) != sorted(digest(row) for row in actual_rows):
            raise ValidationError("Stored central rows differ from frozen inputs")
        for key, value_to_hash in (
            ("config", inputs.get("config")),
            ("release", mapping(private.get("provenance"), "provenance").get("release")),
        ):
            if hashes.get(key) != digest(value_to_hash):
                raise ValidationError(f"Saved {key} checksum mismatch")
        text(inputs.get("version"), "kernel version")
    return snapshot


def interval_score(low: float, high: float, observed: float) -> dict[str, object]:
    """Inclusive coverage and unmodified interval width in the recorded unit."""
    if not all(math.isfinite(v) for v in (low, high, observed)) or low > high:
        raise ValidationError("Invalid scoring interval")
    return {"covered": low <= observed <= high, "width": high - low}


def winner_brier(probabilities: list[float], winner: int) -> float:
    """Multiclass party score: sum (p - one_hot(winner))², range [0, 2]."""
    if (not probabilities or not 0 <= winner < len(probabilities)
            or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities)
            or not math.isclose(sum(probabilities), 1.0, abs_tol=1e-8)):
        raise ValidationError("Winner probabilities must be a complete party simplex")
    return sum((p - int(i == winner)) ** 2 for i, p in enumerate(probabilities))


def reliability(observations: list[tuple[float, bool]]) -> list[dict[str, object]]:
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(10)]
    for confidence, correct in observations:
        number(confidence, "confidence", 0, 1)
        bins[min(9, math.floor(confidence * 10))].append((confidence, correct))
    return [
        {
            "low": i / 10, "high": (i + 1) / 10, "upper_inclusive": i == 9,
            "count": len(rows),
            "mean_confidence": sum(p for p, _ in rows) / len(rows) if rows else None,
            "accuracy": sum(correct for _, correct in rows) / len(rows) if rows else None,
        }
        for i, rows in enumerate(bins)
    ]


def _coverage(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "count": len(rows),
        "coverage": sum(row["covered"] is True for row in rows) / len(rows) if rows else None,
        "mean_width": sum(number(row["width"], "width", 0, math.inf) for row in rows) / len(rows) if rows else None,
    }


def _range(value: object, total: int) -> tuple[float, float]:
    bounds = sequence(value, "range")
    if len(bounds) != 3:
        raise ValidationError("Recorded ranges must contain low/median/high")
    low, median, high = (number(v, "bound", 0, total) for v in bounds)
    if not low <= median <= high:
        raise ValidationError("Recorded range is not ordered")
    return low, high


def _compatible(
    run: dict[str, object], snapshot: dict[str, object], results: OfficialResults,
) -> tuple[dict[str, object], dict[str, object], list[int], dict[int, dict[str, object]]]:
    document = mapping(run["document"], "document")
    context = mapping(document["context"], "context")
    if context["field_id"] != results.field["model_field_id"]:
        raise ValidationError("Official field differs from the exact frozen field")
    private = mapping(document["private"], "private")
    inputs = mapping(private["inputs"], "inputs")
    parties = [integer(p, "party ID", 1) for p in sequence(inputs["party_ids"], "party_ids")]
    if len(parties) < 2 or len(set(parties)) != len(parties):
        raise ValidationError("Frozen party order must contain distinct party IDs")
    frozen_seats = sequence(inputs["seats"], "seats")
    seats = {
        integer(seat.get("seat_id"), "seat_id", 1): seat
        for raw in frozen_seats
        for seat in [mapping(raw, "seat")]
    }
    if len(seats) != len(frozen_seats):
        raise ValidationError("Duplicate frozen seat ID")
    if set(seats) != {race.seat_id for race in results.races}:
        raise ValidationError("Official results do not match the complete frozen field")
    for race in results.races:
        if not set(race.votes) <= set(parties) or race.winner_party not in parties:
            raise ValidationError("Official party mapping is outside the frozen party vector")
    if results.model == "house":
        frozen_field = house_field(snapshot.get("house_field"))
        for key in ("model_field_id", "boundary_sha256", "districts"):
            if frozen_field[key] != results.field[key]:
                raise ValidationError("Approved frozen House geography differs from results")
    else:
        cycle = mapping(mapping(private.get("senate_cycle"), "senate_cycle").get("cycle"), "cycle")
        expected: dict[str, tuple[int, bool]] = {
            text(state, "regular state"): (integer(cycle.get("regular_class"), "regular class", 1), False)
            for state in sequence(cycle.get("regular_states"), "regular states")
        }
        for raw in sequence(cycle.get("specials"), "Senate specials"):
            slot = mapping(raw, "Senate special")
            state = text(slot.get("state"), "special state")
            if state in expected:
                raise ValidationError("Saved state-to-race mapping is ambiguous")
            expected[state] = (integer(slot.get("seat_class"), "seat_class", 1), True)
        if len(expected) != len(seats):
            raise ValidationError("Saved Senate field size differs from the cycle")
        for race in results.races:
            state = text(seats[race.seat_id].get("state"), "frozen state")
            if (race.identity.get("state") != state or expected.get(state) != (
                race.identity.get("seat_class"), race.identity.get("special"),
            )):
                raise ValidationError("Official Senate state/class/special identity differs")
    return document, inputs, parties, seats


def _race_scores(
    run: dict[str, object], document: dict[str, object], parties: list[int],
    results: OfficialResults,
) -> dict[str, object]:
    if run["race_detail"] is None:
        return {"status": "unavailable_detail", "primary": None, "all_party": None,
                "brier": None, "reliability": None, "races": None}
    detail = mapping(run["race_detail"], "race detail")
    for key in ("model", "context", "result_identity"):
        if detail.get(key) != document[key]:
            raise ValidationError("Retained race detail identity mismatch")
    summary = mapping(document["summary"], "summary")
    if (detail.get("schema") != "pf-us-race-summary-v1"
            or any(detail.get(k) != summary[k] for k in ("simulations", "interval"))):
        raise ValidationError("Retained race detail summary mismatch")
    seats = mapping(detail.get("seats"), "race detail seats")
    if set(seats) != {str(r.seat_id) for r in results.races}:
        raise ValidationError("Retained detail does not cover the complete field")
    rows: list[dict[str, object]] = []
    all_rows: list[dict[str, object]] = []
    primary: list[dict[str, object]] = []
    briers: list[float] = []
    confidence_rows: list[tuple[float, bool]] = []
    for observed in results.races:
        party_rows = mapping(mapping(seats[str(observed.seat_id)], "race").get("parties"), "parties")
        if set(party_rows) != {str(p) for p in parties}:
            raise ValidationError("Race probability vector differs from frozen party order")
        parsed = [mapping(party_rows[str(p)], "party") for p in parties]
        center = [number(row.get("central_share"), "central share", 0, 100) for row in parsed]
        probabilities = [number(row.get("win_probability"), "probability", 0, 1) for row in parsed]
        top_two = set(sorted(range(len(parties)), key=lambda j: (-center[j], j))[:2])
        favourite = max(range(len(parties)), key=lambda j: probabilities[j])
        score = winner_brier(probabilities, parties.index(observed.winner_party))
        briers.append(score)
        confidence_rows.append((probabilities[favourite], parties[favourite] == observed.winner_party))
        shares: list[dict[str, object]] = []
        for j, party in enumerate(parties):
            low, high = _range(parsed[j].get("share_range"), 100)
            actual = observed.votes.get(party, 0) / observed.denominator * 100
            item = {"party_id": party, "observed": actual, "low": low, "high": high,
                    "primary": j in top_two, **interval_score(low, high, actual)}
            shares.append(item)
            all_rows.append(item)
            if j in top_two:
                primary.append(item)
        rows.append({"seat_id": observed.seat_id, "shares": shares, "brier": score,
                     "favourite_party": parties[favourite], "confidence": probabilities[favourite],
                     "correct": parties[favourite] == observed.winner_party})
    return {"status": "available", "primary": _coverage(primary), "all_party": _coverage(all_rows),
            "brier": sum(briers) / len(briers), "reliability": reliability(confidence_rows), "races": rows}


def _chamber_scores(document: dict[str, object], results: OfficialResults) -> dict[str, object]:
    summary = mapping(document["summary"], "summary")
    metrics = mapping(summary["metrics"], "metrics")
    names = (("house_seats",) if results.model == "house"
             else ("senate_contested_seats", "senate_full_seats"))
    actual: dict[str, int] = {}
    for race in results.races:
        party = str(race.winner_party)
        actual[party] = actual.get(party, 0) + 1
    holdovers: dict[str, int] = {}
    if results.model == "senate":
        contested = mapping(metrics.get("senate_contested_seats"), "contested metric")
        full = mapping(metrics.get("senate_full_seats"), "full metric")
        up = mapping(contested.get("central"), "contested central")
        all_seats = mapping(full.get("central"), "full central")
        if set(up) != set(all_seats):
            raise ValidationError("Saved Senate raw party metrics disagree")
        for party in all_seats:
            holdovers[party] = integer(all_seats[party], "full central") - integer(up[party], "contested central")
        if any(n < 0 for n in holdovers.values()) or sum(holdovers.values()) != 100 - len(results.races):
            raise ValidationError("Saved Senate holdover arithmetic is inconsistent")
        private = mapping(document["private"], "private")
        saved = mapping(private.get("senate_counting"), "Senate counting")
        if len(sequence(saved.get("holdovers"), "saved holdovers")) != sum(holdovers.values()):
            raise ValidationError("Saved Senate holdover roster size disagrees")
    output: dict[str, object] = {}
    for name in names:
        metric = mapping(metrics.get(name), name)
        total = 435 if name == "house_seats" else 100 if name == "senate_full_seats" else len(results.races)
        if metric.get("total") != total:
            raise ValidationError("Saved chamber metric has the wrong field size")
        ranges = mapping(metric.get("ranges"), "chamber ranges")
        if not set(actual) <= set(ranges):
            raise ValidationError("Official winners fall outside the chamber party vector")
        rows: list[dict[str, object]] = []
        for party, bounds in ranges.items():
            observed = actual.get(party, 0) + (holdovers.get(party, 0) if name == "senate_full_seats" else 0)
            low, high = _range(bounds, total)
            rows.append({"party_id": int(party), "observed": observed, "low": low, "high": high,
                         **interval_score(low, high, observed)})
        output[name] = {"parties": rows, **_coverage(rows)}
    return output


def generic_diagnostic(document: dict[str, object], results: OfficialResults) -> dict[str, object]:
    """House valid-vote D−R contrast; this is not an observed latent G draw."""
    if results.model != "house":
        return {"status": "unavailable_house_national_truth",
                "reason": "Senate contested votes are not generic-ballot national truth"}
    # IDs are explicit result facts; party labels never determine the contrast.
    contrast = mapping(results.document.get("generic_contrast"), "generic_contrast")
    dem = integer(contrast.get("democratic_party_id"), "Democratic party ID", 1)
    rep = integer(contrast.get("republican_party_id"), "Republican party ID", 1)
    if dem == rep:
        raise ValidationError("Generic contrast requires two distinct explicit parties")
    private = mapping(document["private"], "private")
    inputs = mapping(private["inputs"], "inputs")
    parties = sequence(inputs["party_ids"], "parties")
    if dem not in parties or rep not in parties:
        raise ValidationError("Generic contrast parties are outside the saved vector")
    provenance = mapping(private.get("provenance"), "provenance")
    if provenance.get("selected_polls") is None:
        return {"status": "unavailable_frozen_estimator"}
    sums = {dem: 0.0, rep: 0.0}
    weights = {dem: 0.0, rep: 0.0}
    contributors: list[int] = []
    for raw in sequence(provenance["selected_polls"], "selected_polls"):
        poll = mapping(raw, "selected poll")
        if poll.get("seat_id") is not None or poll.get("matchup") is not None:
            continue
        weight = number(poll.get("weight"), "poll weight", 0, math.inf)
        if weight <= 0:
            continue
        shares = mapping(poll.get("shares"), "poll shares")
        if not shares:
            continue
        contributors.append(integer(poll.get("poll_id"), "poll ID", 1))
        for party in (dem, rep):
            if str(party) in shares:
                sums[party] += number(shares[str(party)], "poll share", 0, 100) * weight
                weights[party] += weight
    if any(weight <= 0 for weight in weights.values()):
        return {"status": "unavailable_frozen_estimator"}
    national = [mapping(raw, "source") for raw in sequence(inputs["sources"], "sources")
                if mapping(raw, "source").get("kind") == "national"]
    if len(national) != 1:
        raise ValidationError("Generic diagnostic requires one frozen national source")
    source = national[0]
    proportions = sequence(source.get("proportions"), "source proportions")
    if len(proportions) != len(parties):
        raise ValidationError("National source proportions differ from party order")
    if not math.isclose(sum(number(t, "source proportion", 0, 1) for t in proportions), 1, abs_tol=1e-8):
        raise ValidationError("National source proportions must sum to one")
    t_dem = number(proportions[parties.index(dem)], "D proportion", 0, 1)
    t_rep = number(proportions[parties.index(rep)], "R proportion", 0, 1)
    scale = number(source.get("scale"), "national scale", 0, math.inf)
    sigma = scale * math.sqrt(max(0, t_dem + t_rep - (t_dem - t_rep) ** 2))
    denominator = sum(r.denominator for r in results.races)
    outcome = sum(r.votes.get(dem, 0) - r.votes.get(rep, 0) for r in results.races) / denominator * 100
    estimator = sums[dem] / weights[dem] - sums[rep] / weights[rep]
    return {
        "status": "available" if sigma > 0 else "zero_sigma",
        "estimator_margin": estimator, "official_house_margin": outcome,
        "estimator_minus_outcome": estimator - outcome, "kernel_contrast_sd": sigma,
        "standardized_residual": (estimator - outcome) / sigma if sigma > 0 else None,
        "selected_poll_ids": contributors, "party_total_weights": {str(p): w for p, w in weights.items()},
        "interpretation": "Raw frozen generic estimator versus eventual House valid votes; includes opinion drift; not observed latent G",
    }


def score_snapshot(value: object, results: OfficialResults) -> dict[str, object]:
    snapshot = validate_snapshot(value)
    if snapshot["model"] != results.model:
        raise ValidationError("Snapshot and official results belong to different models")
    policy = (evaluation_policy(snapshot["evaluation_policy"], results.model)
              if snapshot.get("evaluation_policy") is not None else None)
    if policy is not None:
        for key in ("candidate_aggregation_policy", "winner_domain"):
            if policy[key] != results.document[key]:
                raise ValidationError(f"Official {key} differs from the frozen evaluation policy")
        for name in ("share_stage", "winner_stage"):
            selected = mapping(policy[name], "policy stage")
            official = mapping(results.document[name], "official stage")
            for key in ("vote_basis", "denominator_policy", "exclusions_policy"):
                if selected[key] != official[key]:
                    raise ValidationError(f"Official {name}.{key} differs from the frozen evaluation policy")
    runs = [mapping(raw, "run") for raw in sequence(snapshot["runs"], "runs")]
    eligible = [run for run in runs if text(run["as_of_date"], "as_of_date") < str(POLLING_DAY)]
    final = max(eligible, key=lambda run: (str(run["as_of_date"]), integer(run["election_id"], "ID")), default=None)
    output: list[dict[str, object]] = []
    for run in runs:
        row: dict[str, object] = {
            "election_id": run["election_id"], "as_of_date": run["as_of_date"],
            "run_sha256": run["run_sha256"], "is_final": run is final,
            "capture_kind": snapshot["capture_kind"],
        }
        if run not in eligible:
            row["status"] = "ineligible_date"
        elif run["document"] is None:
            row["status"] = "unavailable_uncertainty"
        elif policy is None:
            row["status"] = "unavailable_evaluation_basis"
        else:
            document, _, parties, _ = _compatible(run, snapshot, results)
            row.update({
                "status": "scored", "context": document["context"],
                "interval": mapping(document["summary"], "summary")["interval"],
                "race_scores": _race_scores(run, document, parties, results),
                "chamber_scores": _chamber_scores(document, results),
                "generic_diagnostic": generic_diagnostic(document, results),
            })
        output.append(row)
    return {
        "schema": "pf-us-validation-report-v1", "model": results.model, "cycle": 2026,
        "snapshot_sha256": snapshot["snapshot_sha256"], "official_results_sha256": digest(results.document),
        "final_election_id": final["election_id"] if final else None,
        "capture_kind": snapshot["capture_kind"], "runs": output,
        "evaluation_policy": policy,
        "limitations": ["One correlated election; earlier nowcasts include opinion drift",
                        "Local capture timestamps are operator attestations, not independent pre-result publication proof",
                        "Missing historical race detail is unavailable; no replay or carry across contexts"],
    }
