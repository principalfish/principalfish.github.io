"""Staged per-model archives and exact replay, independent of live inputs."""

from __future__ import annotations

import json
import hashlib
import os
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any

import numpy as np

from model_support.io import OutputPublicationError, validate_output_target
from model_support.persistence import StoredUncertainty, UncertaintyPayload
from model_support.us_run import fingerprint, restore_run_inputs
from model_support.us_simulation import (
    FloatArray, IntArray, UsErrorStreams, UsProjection, UsSimulationInputs,
    UsSimulationRun, project_us_seats,
)
from model_support.us_uncertainty import US_STORED_SCHEMA, validate_document

US_RAW_SCHEMA = "pf-us-source-draws-v1"
US_RAW_BYTE_LIMIT = 1_000_000_000


def us_raw_path(simulations_path: Path, model: str) -> Path:
    if model not in {"house", "senate", "president"}:
        raise ValueError("unsupported US raw model")
    return simulations_path.parent / f"us-{model}-latest.npz"


@dataclass(frozen=True, slots=True)
class StagedUsDraws:
    temporary: Path
    destination: Path

    def publish(self, election_id: int) -> None:
        try:
            if type(election_id) is not int or election_id <= 0:
                raise ValueError("invalid raw election binding")
            # The database ID exists only after its transaction commits. The
            # expensive archive is already complete; this small final member
            # binds it before the single atomic move to the public raw path.
            binding = BytesIO()
            np.save(binding, np.array(election_id, dtype=np.int64), allow_pickle=False)
            with zipfile.ZipFile(self.temporary, "a") as archive:
                archive.writestr("election_id.npy", binding.getvalue())
            with self.temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(self.temporary, self.destination)
        except OSError as exc:
            raise OutputPublicationError(
                "House results committed, but raw publication failed. "
                "Previous draws cannot match this run's saved artifact identity. "
                "Repair the raw destination and rerun the date.",
            ) from exc

    def discard(self) -> None:
        self.temporary.unlink(missing_ok=True)


def stage_us_draws(
    destination: Path, run: UsSimulationRun, payload: UncertaintyPayload,
) -> StagedUsDraws:
    """Finish all expensive file writes before the SQLite replacement can begin."""
    cells = len(run.winners) * len(run.inputs.seats) * len(run.inputs.party_ids)
    raw_bytes = cells * 16 + sum(
        array.nbytes for array in (run.winners, run.seat_totals, run.vote_totals)
    )
    if raw_bytes > US_RAW_BYTE_LIMIT:
        raise ValueError(
            "US raw arrays exceed the 1 GB resource limit; lower --simulations "
            "or use --no-raw-sims to retain compact summaries only",
        )
    validate_output_target(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with TemporaryDirectory(prefix=".us-draws-", dir=destination.parent) as scratch:
            shape = (len(run.winners), len(run.inputs.seats), len(run.inputs.party_ids))
            # Memory maps keep full race counts/errors on disk rather than
            # multiplying the runner's in-memory requirements by every party.
            errors = np.lib.format.open_memmap(
                Path(scratch) / "errors.npy", mode="w+", dtype=np.float64, shape=shape,
            )
            votes = np.lib.format.open_memmap(
                Path(scratch) / "votes.npy", mode="w+", dtype=np.int64, shape=shape,
            )
            streams = UsErrorStreams(run.inputs, run.seed)
            for start in range(0, shape[0], 256):
                stop = min(start + 256, shape[0])
                draws = streams.draw(run.inputs, stop - start)
                projection = project_us_seats(run.inputs, draws)
                if (
                    not np.array_equal(projection.winners, run.winners[start:stop])
                    or not np.array_equal(
                        projection.votes.sum(axis=1), run.vote_totals[start:stop],
                    )
                ):
                    raise ValueError("raw staging differs from the House simulation")
                errors[start:stop] = draws
                votes[start:stop] = projection.votes
            errors.flush()
            votes.flush()
            header = json.dumps(
                {"schema": US_RAW_SCHEMA, "document": dict(payload.national)},
                sort_keys=True, separators=(",", ":"), allow_nan=False,
            )
            with NamedTemporaryFile(
                dir=destination.parent, prefix=f".{destination.name}.",
                suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                np.savez_compressed(
                    handle, header=np.array(header), errors=errors, votes=votes,
                    winners=run.winners, seat_totals=run.seat_totals,
                    vote_totals=run.vote_totals,
                )
                handle.flush()
                os.fsync(handle.fileno())
            del errors, votes
        return StagedUsDraws(temporary, destination)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


@dataclass(frozen=True, slots=True)
class UsDraws:
    inputs: UsSimulationInputs
    document: Mapping[str, Any]
    errors: FloatArray
    votes: IntArray
    winners: IntArray
    seat_totals: IntArray
    vote_totals: IntArray
    seed: int


def raw_detail_available(path: Path, stored: StoredUncertainty) -> bool:
    """Check identity using only small metadata members, without loading draws."""
    try:
        read_us_draw_rows(path, stored)
        return True
    except ValueError:
        return False


def load_us_draws(path: Path, stored: StoredUncertainty) -> UsDraws:
    """Load only the archive belonging to this saved, explicitly scoped summary."""
    if stored.schema != US_STORED_SCHEMA or stored.seats is None:
        raise ValueError("US detailed draws are unavailable or have been pruned")
    context = validate_document(stored.national, public=False)
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 3_000_000_000:
                raise ValueError("US raw archive exceeds supported size")
        with np.load(path, allow_pickle=False) as archive_arrays:
            expected = {"header", "errors", "votes", "winners", "seat_totals",
                        "vote_totals", "election_id"}
            if set(archive_arrays.files) != expected or len(archive_arrays.files) != 7:
                raise ValueError("unsupported US raw archive members")
            arrays = {name: archive_arrays[name] for name in expected}
        raw_header = arrays.pop("header")
        election_id = arrays.pop("election_id")
        if (election_id.shape != () or election_id.dtype.kind != "i"
                or int(election_id.item()) != stored.election_id):
            raise ValueError("US raw archive election ID mismatch")
        if raw_header.shape != () or raw_header.dtype.kind != "U":
            raise ValueError("US raw header is not a scalar JSON string")
        header = json.loads(str(raw_header.item()))
        if header.get("schema") != US_RAW_SCHEMA:
            raise ValueError("unsupported US raw archive schema")
        document = header["document"]
        if validate_document(document, public=False) != context:
            raise ValueError("US raw model or counting context mismatch")
        private = document["private"]
        expected_private = stored.national["private"]
        if not isinstance(expected_private, Mapping):
            raise ValueError("invalid stored US replay metadata")
        identity = private.get("artifact_id")
        if identity != expected_private.get("artifact_id") or identity != fingerprint([
            document["result_identity"], document["summary"],
            {key: value for key, value in private.items() if key != "artifact_id"},
        ]):
            raise ValueError("US raw archive does not match the saved run")
        inputs = restore_run_inputs(document)
        if inputs.model != f"us-{context.model}":
            raise ValueError("US replay input model mismatch")
        n = document["summary"]["simulations"]
        s, k = len(inputs.seats), len(inputs.party_ids)
        shapes = {"errors": (n, s, k), "votes": (n, s, k), "winners": (n, s),
                  "seat_totals": (n, k), "vote_totals": (n, k)}
        for name, array in arrays.items():
            kind = "f" if name == "errors" else "i"
            if array.shape != shapes[name] or array.dtype.kind != kind:
                raise ValueError(f"invalid US raw {name} shape or dtype")
            if not np.isfinite(array).all():
                raise ValueError(f"nonfinite US raw {name}")
        if (arrays["winners"] < 0).any() or (arrays["winners"] >= k).any():
            raise ValueError("invalid saved US winner index")
        if type(private["seed"]) is not int or private["seed"] < 0:
            raise ValueError("invalid saved US seed")
        return UsDraws(
            inputs, document, arrays["errors"], arrays["votes"], arrays["winners"],
            arrays["seat_totals"], arrays["vote_totals"], private["seed"],
        )
    except (OSError, KeyError, TypeError, zipfile.BadZipFile, zlib.error) as exc:
        raise ValueError(f"cannot read US simulation archive: {exc}") from exc


@dataclass(frozen=True, slots=True)
class UsReplayedSimulation:
    projection: UsProjection
    errors: FloatArray
    central: UsProjection


@dataclass(frozen=True, slots=True)
class UsDrawMetadata:
    """Validated identity and dimensions, without allocating any draw cubes."""

    inputs: UsSimulationInputs
    document: Mapping[str, Any]
    simulations: int
    seed: int


def _array_header(
    archive: zipfile.ZipFile, name: str,
) -> tuple[tuple[int, ...], np.dtype[Any], int]:
    with archive.open(f"{name}.npy") as handle:
        if np.lib.format.read_magic(handle) != (1, 0):
            raise ValueError("unsupported US raw array format")
        shape, fortran, dtype = np.lib.format.read_array_header_1_0(handle)
        offset = handle.tell()
    if fortran or dtype.hasobject:
        raise ValueError("unsupported US raw array layout")
    if archive.getinfo(f"{name}.npy").file_size != (
        offset + int(np.prod(shape, dtype=object)) * dtype.itemsize
    ):
        raise ValueError(f"invalid US raw {name} size")
    return shape, dtype, offset


def _small_array(archive: zipfile.ZipFile, name: str) -> Any:
    if archive.getinfo(f"{name}.npy").file_size > 20_000_000:
        raise ValueError("US raw metadata exceeds supported size")
    shape, dtype, _ = _array_header(archive, name)
    if shape != () or (
        dtype.kind != "U" if name == "header" else dtype != np.dtype("int64")
    ):
        raise ValueError(f"invalid US raw {name} metadata shape or dtype")
    with archive.open(f"{name}.npy") as handle:
        return np.lib.format.read_array(handle, allow_pickle=False)


def _draw_metadata(
    archive: zipfile.ZipFile, stored: StoredUncertainty,
) -> UsDrawMetadata:
    names = {f"{name}.npy" for name in (
        "header", "election_id", "errors", "votes", "winners", "seat_totals",
        "vote_totals",
    )}
    members = archive.infolist()
    if len(members) != len(names) or {m.filename for m in members} != names:
        raise ValueError("unsupported US raw archive members")
    if sum(item.file_size for item in members) > 3_000_000_000:
        raise ValueError("US raw archive exceeds supported size")
    if stored.schema != US_STORED_SCHEMA or stored.seats is None:
        raise ValueError("US detailed draws are unavailable or have been pruned")
    election_id = _small_array(archive, "election_id")
    header_array = _small_array(archive, "header")
    if (election_id.shape != () or election_id.dtype != np.dtype("int64")
            or int(election_id.item()) != stored.election_id):
        raise ValueError("US raw archive election ID mismatch")
    if header_array.shape != () or header_array.dtype.kind != "U":
        raise ValueError("invalid US raw header")
    header = json.loads(str(header_array.item()))
    if header["schema"] != US_RAW_SCHEMA:
        raise ValueError("unsupported US raw archive schema")
    document = header["document"]
    context = validate_document(document, public=False)
    if context != validate_document(stored.national, public=False):
        raise ValueError("US raw counting context mismatch")
    private = document["private"]
    expected = stored.national["private"]
    if not isinstance(expected, Mapping) or (
        private["artifact_id"] != expected["artifact_id"]
        or private["artifact_id"] != fingerprint([
            document["result_identity"], document["summary"],
            {key: value for key, value in private.items() if key != "artifact_id"},
        ])
    ):
        raise ValueError("US raw archive does not match the saved run")
    inputs = restore_run_inputs(document)
    n = document["summary"]["simulations"]
    s, k = len(inputs.seats), len(inputs.party_ids)
    shapes = {"errors": (n, s, k), "votes": (n, s, k), "winners": (n, s),
              "seat_totals": (n, k), "vote_totals": (n, k)}
    for name, expected_shape in shapes.items():
        shape, dtype, _ = _array_header(archive, name)
        expected_dtype = np.dtype("float64" if name == "errors" else "int64")
        if shape != expected_shape or dtype != expected_dtype:
            raise ValueError(f"invalid US raw {name} shape or dtype")
    if type(private["seed"]) is not int or private["seed"] < 0:
        raise ValueError("invalid saved US seed")
    return UsDrawMetadata(inputs, document, n, private["seed"])


def read_us_draw_rows(
    path: Path, stored: StoredUncertainty, *, start: int = 0, count: int = 0,
    detail: bool = False,
) -> tuple[UsDrawMetadata, dict[str, Any]]:
    """Read bounded contiguous rows; ZIP seeking never allocates skipped rows."""
    try:
        with zipfile.ZipFile(path) as archive:
            metadata = _draw_metadata(archive, stored)
            if (type(start) is not int or type(count) is not int
                    or start < 0 or count < 0 or count > 50
                    or start + count > metadata.simulations):
                raise ValueError("simulation page is outside the saved run")
            arrays: dict[str, Any] = {}
            names: tuple[str, ...] = ("seat_totals", "vote_totals")
            if detail:
                names += ("errors", "votes", "winners")
            for name in names:
                shape, dtype, offset = _array_header(archive, name)
                row_size = int(np.prod(shape[1:])) * dtype.itemsize
                with archive.open(f"{name}.npy") as handle:
                    handle.seek(offset + start * row_size)
                    data = handle.read(count * row_size)
                array = np.frombuffer(data, dtype=dtype).reshape((count, *shape[1:]))
                if not np.isfinite(array).all():
                    raise ValueError(f"nonfinite US raw {name}")
                arrays[name] = array
            return metadata, arrays
    except (OSError, KeyError, TypeError, AttributeError, IndexError, EOFError,
            zipfile.BadZipFile, zlib.error) as exc:
        raise ValueError(f"cannot read US simulation archive: {exc}") from exc


def replay_us_selected(
    path: Path, stored: StoredUncertainty, index: int,
) -> tuple[UsDrawMetadata, UsReplayedSimulation]:
    metadata, arrays = read_us_draw_rows(
        path, stored, start=index, count=1, detail=True,
    )
    streams = UsErrorStreams(metadata.inputs, metadata.seed)
    for start in range(0, index, 256):
        streams.draw(metadata.inputs, min(256, index - start))
    errors = streams.draw(metadata.inputs, 1)
    projection = project_us_seats(metadata.inputs, errors)
    totals = np.bincount(
        projection.winners[0], minlength=len(metadata.inputs.party_ids),
    )
    if not all((
        np.array_equal(errors, arrays["errors"]),
        np.array_equal(projection.votes, arrays["votes"]),
        np.array_equal(projection.winners, arrays["winners"]),
        np.array_equal(totals, arrays["seat_totals"][0]),
        np.array_equal(projection.votes.sum(axis=1), arrays["vote_totals"]),
    )):
        raise ValueError("US simulation replay differs from saved counts or errors")
    central = project_us_seats(metadata.inputs, np.zeros_like(errors))
    return metadata, UsReplayedSimulation(projection, errors, central)


def us_source_contributions(
    metadata: UsDrawMetadata, index: int, seat_index: int,
) -> list[tuple[str, str, FloatArray]]:
    """Regenerate each shared source's actual loaded contribution to one race."""
    if not 0 <= index < metadata.simulations:
        raise ValueError("simulation index is outside the saved run")
    result: list[tuple[str, str, FloatArray]] = []
    k = len(metadata.inputs.party_ids)
    for source in metadata.inputs.sources:
        loadings = [item for item in source.loadings if item.seat_index == seat_index]
        if not loadings:
            continue
        contribution = np.zeros(k)
        if source.scale:
            generator = np.random.default_rng(np.random.SeedSequence([
                metadata.seed,
                int.from_bytes(hashlib.sha256(source.source_id.encode()).digest()[:8],
                               "big"),
            ]))
            for start in range(0, index, 256):
                generator.standard_normal((min(256, index - start), k))
            shape = np.array(source.proportions)
            weighted = np.sqrt(shape) * generator.standard_normal((1, k))
            error = (weighted - shape * weighted.sum(axis=-1, keepdims=True))
            error *= source.scale
            for loading in loadings:
                contribution += (error * loading.coefficients)[0]
        result.append((source.source_id, source.kind, contribution))
    return result


def replay_us_simulation(draws: UsDraws, index: int) -> UsReplayedSimulation:
    if type(index) is not int or not 0 <= index < len(draws.winners):
        raise ValueError("simulation index is outside the saved run")
    streams = UsErrorStreams(draws.inputs, draws.seed)
    for start in range(0, index, 256):
        streams.draw(draws.inputs, min(256, index - start))
    errors = streams.draw(draws.inputs, 1)
    projection = project_us_seats(draws.inputs, errors)
    totals = np.bincount(projection.winners[0], minlength=len(draws.inputs.party_ids))
    if not all((
        np.array_equal(errors[0], draws.errors[index]),
        np.array_equal(projection.votes[0], draws.votes[index]),
        np.array_equal(projection.winners[0], draws.winners[index]),
        np.array_equal(totals, draws.seat_totals[index]),
        np.array_equal(projection.votes[0].sum(axis=0), draws.vote_totals[index]),
    )):
        raise ValueError("US simulation replay differs from saved counts or errors")
    central = project_us_seats(draws.inputs, np.zeros_like(errors))
    return UsReplayedSimulation(projection, errors, central)
