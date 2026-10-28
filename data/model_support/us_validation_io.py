"""Bounded local file IO and read-only SQL capture for US validation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import cast

from model_support.us_uncertainty import validate_document
from model_support.us_validation import validate_snapshot
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
    parse_results,
    sequence,
    text,
)

MAX_JSON_BYTES = 128 * 1024 * 1024
MAX_RUNS = 256
PREREGISTRATION = Path(__file__).resolve().parents[1] / "models/us/validation-2026.md"


def read_json(path: Path) -> object:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValidationError(f"JSON exceeds {MAX_JSON_BYTES} bytes: {path}")
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValidationError("JSON exceeds the local resource limit")
    return cast(object, json.loads(raw))


def write_json(path: Path, value: object) -> None:
    """An explicit new destination avoids clobbering an earlier preregistration."""
    raw = json.dumps(value, indent=2, allow_nan=False, ensure_ascii=False) + "\n"
    if len(raw.encode()) > MAX_JSON_BYTES:
        raise ValidationError("Output exceeds the local resource limit")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(raw)


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_results(path: Path) -> OfficialResults:
    result = parse_results(read_json(path))
    for raw in sequence(result.document["sources"], "sources"):
        source = mapping(raw, "source")
        artifact = path.parent / text(source["artifact"], "artifact")
        if _file_sha256(artifact) != source["sha256"]:
            raise ValidationError(f"Official source artifact checksum mismatch: {artifact}")
    return result


def _central_rows(connection: sqlite3.Connection, election_id: int) -> list[list[object]]:
    rows: list[list[object]] = []
    for seat, party, votes, elected in connection.execute(
        "SELECT seat_id, party_id, vote_total, elected FROM votes "
        "WHERE election_id=? ORDER BY seat_id, party_id LIMIT 10001", (election_id,),
    ):
        if (party is None or not isinstance(votes, (int, float))
                or not float(votes).is_integer() or votes < 0 or elected not in (0, 1)):
            raise ValidationError("Saved central rows require integer counts and explicit parties")
        rows.append([int(seat), int(party), int(votes), bool(elected)])
    if len(rows) > 10000:
        raise ValidationError("Saved central rows exceed the resource limit")
    return rows


def _freeze_run(
    connection: sqlite3.Connection, election_id: int, as_of: str, model: str,
    map_id: int, has_uncertainty: bool,
) -> dict[str, object]:
    document: dict[str, object] | None = None
    detail: object = None
    if has_uncertainty:
        row = connection.execute(
            "SELECT national_json,seats_json FROM model_uncertainty WHERE election_id=?",
            (election_id,),
        ).fetchone()
        if row is not None:
            if sum(len(part or "") for part in row) > MAX_JSON_BYTES:
                raise ValidationError("Saved summary exceeds the resource limit")
            document = mapping(json.loads(row[0]), "document")
            context = validate_document(document, public=False)
            if (context.model != model or context.map_id != map_id
                    or context.as_of_date != as_of or context.target_cycle != 2026):
                raise ValidationError("Saved run is outside the requested 2026 scope")
            detail = json.loads(row[1]) if row[1] is not None else None
    rows = _central_rows(connection, election_id)
    hashes = {"document": digest(document), "race_detail": digest(detail),
              "central_rows": digest(rows)}
    if document is not None:
        private = mapping(document["private"], "private")
        inputs = mapping(private["inputs"], "inputs")
        hashes["config"] = digest(inputs["config"])
        hashes["release"] = digest(mapping(private.get("provenance"), "provenance").get("release"))
    run: dict[str, object] = {
        "election_id": election_id, "as_of_date": as_of, "document": document,
        "race_detail": detail, "central_rows": rows, "hashes": hashes,
    }
    run["run_sha256"] = digest(run)
    return run


def capture_snapshot(
    database: Path, *, model: str, map_id: int, name_prefix: str,
    include_history: bool = False, frozen_house_field: object = None,
    reviewed_evaluation_policy: object = None,
    captured_at: datetime | None = None,
) -> dict[str, object]:
    """Freeze saved payloads in one read-only transaction, without any replay.

    ``captured_at`` is injectable only for deterministic tests. The CLI always
    records the current clock and never accepts a replacement timestamp.
    """
    model = model_name(model)
    integer(map_id, "map_id", 1)
    text(name_prefix, "name_prefix")
    runs: list[dict[str, object]] = []
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        candidates = connection.execute(
            "SELECT id,name FROM elections WHERE type=? AND map_id=? LIMIT 10001",
            (f"us_{model}_model", map_id),
        ).fetchall()
        if len(candidates) > 10000:
            raise ValidationError("Election scope exceeds the resource limit")
        eligible: list[tuple[int, date]] = []
        for rid, name in candidates:
            # Match persistence's canonical date and optional run suffix without
            # importing its write-capable database/configuration dependencies.
            match = re.fullmatch(rf"{re.escape(name_prefix)} (\d{{4}}-\d{{2}}-\d{{2}})(?: .+)?", name)
            if match is None:
                continue
            try:
                as_of = date.fromisoformat(match.group(1))
            except ValueError:
                continue
            if as_of < POLLING_DAY:
                eligible.append((int(rid), as_of))
        eligible.sort(key=lambda item: (item[1], item[0]))
        selected = eligible if include_history else eligible[-1:]
        if len(selected) > MAX_RUNS:
            raise ValidationError(f"History exceeds {MAX_RUNS} runs; capture a smaller scope")
        has_uncertainty = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_uncertainty'"
        ).fetchone() is not None
        size = 0
        for election_id, as_of in selected:
            run = _freeze_run(connection, election_id, str(as_of), model, map_id, has_uncertainty)
            size += len(json.dumps(run).encode())
            if size > MAX_JSON_BYTES:
                raise ValidationError("Frozen history exceeds the resource limit")
            runs.append(run)
    now = captured_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValidationError("Capture timestamp must have a UTC offset")
    kernel_root = Path(__file__).resolve().parent
    kernel_files = ("us_simulation.py", "us_run.py", "us_senate.py", "monte_carlo.py",
                    "senate_cycles.py", "us_validation.py", "us_validation_contracts.py",
                    "us_validation_io.py", "us_uncertainty.py", "us_outcomes.py",
                    "us_poll_evidence.py")
    code_hashes = {f"model_support/{name}": _file_sha256(kernel_root / name) for name in kernel_files}
    for name in ("models/us/_common.py", f"models/us/run_us_{model}_model.py",
                 "scripts/validate_us_uncertainty.py"):
        code_hashes[name] = _file_sha256(kernel_root.parent / name)
    snapshot: dict[str, object] = {
        "schema": SNAPSHOT_SCHEMA, "model": model, "cycle": 2026,
        "polling_day": str(POLLING_DAY), "map_id": map_id, "name_prefix": name_prefix,
        "captured_at": now.astimezone(timezone.utc).isoformat(),
        "capture_kind": ("pre_result_capture_attestation" if now.astimezone(timezone.utc).date() < POLLING_DAY else "retrospective"),
        "capture_code_sha256": code_hashes,
        "kernel_provenance": "Saved kernel version and exact payloads; source hashes identify code at capture, not independently verified historical production bytes",
        "preregistration_sha256": _file_sha256(PREREGISTRATION),
        "house_field": (house_field(frozen_house_field) if frozen_house_field is not None else None),
        "evaluation_policy": (evaluation_policy(reviewed_evaluation_policy, model)
                              if reviewed_evaluation_policy is not None else None),
        "includes_history": include_history, "runs": runs,
    }
    snapshot["snapshot_sha256"] = digest(snapshot)
    return validate_snapshot(snapshot)


def readiness(model: str) -> dict[str, object]:
    model_name(model)
    return {
        "status": "pending_official_results", "cycle": 2026,
        "polling_day": str(POLLING_DAY), "score": None,
        "definitions": [
            "Latest saved run dated strictly before polling day; ties use election ID",
            "Primary share coverage: two largest central parties, ties use frozen party order",
            "Secondary coverage: all parties; inclusive bounds and mean interval width",
            "Full-vector party winner Brier and favourite confidence in ten fixed 10% bins",
            "House raw party seats; Senate contested/full raw seats from saved holdovers",
        ],
        "requirements": [
            "Self-contained offline snapshot and independently retained pre-result hash evidence",
            "Pinned official source artifacts, revisions, extraction and explicit candidate-party mapping",
            "Separate share/winner stages, integer valid-vote denominator and exclusions",
            "Reviewed evaluation policy frozen explicitly; actual stage/denominator policy remains pending",
            ("Approved exact House boundary/district identity; 435 matching labels are insufficient"
             if model == "house" else "Exact saved Senate state/class/special field and holdovers"),
        ],
        "limitations": [
            "Actual certified 2026 result validation remains pending; no score is inferred from today's database",
            "No historical replay, current roster lookup, calibration change or simulation on this view",
        ],
    }
