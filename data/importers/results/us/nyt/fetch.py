"""Download NYT results JSON into a local cache, or read it back offline.

Cache layout, under ``cache_dir`` (``config.NYT_CACHE_DIR`` by default)::

    <year>/<chamber>-<kind>-<UTC timestamp>.json   one file per download
    <year>/<chamber>-<kind>-latest.json            pointer to the newest one

Snapshots are the response bytes exactly as served. The pointer is a small
JSON object naming the newest snapshot's file, its URL and schema, when it was
retrieved and the feed's own updated-at value, so offline reads need no
symlinks and work on any filesystem. Both are written to a temporary file in
the same folder and renamed into place, so a crash never leaves a half-written
file under a real name.

2018's one file holds House and Senate alike. It is still cached once per
chamber, each with its own pointer, so every ``(year, chamber, kind)`` has the
same layout and refreshing one chamber never moves the other's pointer; the
cost is a second ~3 MB copy.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException, HTTPMessage
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Protocol, TypeGuard
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from importers.results.us.nyt.sources import SchemaId, Source, SourceStatus

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset({"static01.nyt.com", "int.nyt.com"})

# Generic client string: identifies the tool, carries no personal details.
USER_AGENT = "Mozilla/5.0 (compatible; election-results-fetcher/1.0)"

TIMEOUT_SECONDS = 60

# The largest feed (2020 House) is ~9 MB, so 64 MiB is ample headroom while
# still bounding how much one response can buffer.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024

# Basic ISO 8601 with microseconds: no colons (portable file names), sorts in
# time order, and two fetches in the same second do not collide.
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S.%fZ"


class FetchError(Exception):
    """Base class for errors fetching or reading cached NYT results."""


class DisallowedHostError(FetchError):
    """Raised when a source URL is not on an allowed NYT static host."""


class SourcePendingError(FetchError):
    """Raised when asked to fetch a source whose contest has not happened."""


class DownloadError(FetchError):
    """Raised when the download fails or is too large."""


class InvalidJsonError(FetchError):
    """Raised when a download or cached snapshot is not a JSON object."""


class NoCachedSnapshotError(FetchError):
    """Raised offline when no usable snapshot is cached for a source.

    That covers nothing cached yet, a snapshot file that is missing or cannot
    be read, and a cache written for a different URL or schema than the
    source now lists.
    """


class _Response(Protocol):
    """The parts of an HTTP response that ``_download`` uses."""

    length: int | None

    def read(self, amount: int, /) -> bytes: ...

    def geturl(self) -> str: ...

    def __enter__(self) -> _Response: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class _AllowlistRedirectHandler(HTTPRedirectHandler):
    """Follows a redirect only when its target passes the host allowlist."""

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        """Refuse redirects off the allowed hosts; otherwise follow as usual.

        Raises:
            DisallowedHostError: If ``newurl`` is not https on an allowed host.
        """
        try:
            _check_url(newurl)
        except DisallowedHostError:
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Replaces urllib's default redirect handler with the allowlisting one.
_OPENER = build_opener(_AllowlistRedirectHandler)


@dataclass(frozen=True, slots=True)
class FetchResult:
    """A results file, fresh from NYT or read back from the cache.

    Attributes:
        source: The source that was fetched.
        data: The parsed top-level JSON object.
        retrieved_at: When the snapshot was downloaded (UTC).
        source_updated_at: The feed's own updated-at value, verbatim, or
            ``None`` when the file does not carry one.
        cache_path: The snapshot file on disk.
        from_cache: ``True`` when read offline rather than downloaded now.
    """

    source: Source
    data: dict[str, Any]
    retrieved_at: datetime
    source_updated_at: str | None
    cache_path: Path
    from_cache: bool


def fetch(source: Source, cache_dir: Path, *, offline: bool = False) -> FetchResult:
    """Fetch ``source``, caching it, or read its latest cached snapshot.

    Args:
        source: The NYT file to fetch.
        cache_dir: Root of the cache (see the module docstring for layout).
        offline: Read the newest cached snapshot instead of downloading.

    Returns:
        The parsed file with its retrieval details.

    Raises:
        SourcePendingError: If the source's contest has not been held.
        DisallowedHostError: If the URL, or a redirect it leads to, is not
            https on an allowed host (the URL is checked before any request).
        DownloadError: If the request fails, the body is cut short, or the
            body is too large.
        InvalidJsonError: If the body is not a JSON object (nothing is
            cached), or offline, if the cached pointer or snapshot is corrupt.
        NoCachedSnapshotError: Offline, if no usable snapshot is cached.
        OSError: If the cache folder cannot be written; the previous pointer
            is left in place.
    """
    url = _checked_url(source)
    if offline:
        return _read_latest(source, cache_dir)

    raw = _download(url)
    data = _parse(raw, what=url)
    retrieved_at = _utcnow()
    updated_at = source_updated_at(source.schema, data)

    folder = cache_dir / str(source.year)
    folder.mkdir(parents=True, exist_ok=True)
    stem = _stem(source)
    snapshot = folder / f"{stem}-{retrieved_at.strftime(_TIMESTAMP_FORMAT)}.json"
    _write_atomic(snapshot, raw)
    pointer = {
        "snapshot": snapshot.name,
        "url": url,
        "schema": source.schema,
        "retrieved_at": retrieved_at.isoformat(),
        "source_updated_at": updated_at,
    }
    _write_atomic(
        _pointer_path(source, cache_dir),
        json.dumps(pointer, indent=2).encode("utf-8"),
    )
    logger.info("cached %s as %s", url, snapshot)
    return FetchResult(
        source=source,
        data=data,
        retrieved_at=retrieved_at,
        source_updated_at=updated_at,
        cache_path=snapshot,
        from_cache=False,
    )


def source_updated_at(schema: SchemaId, data: dict[str, Any]) -> str | None:
    """Return the feed's own updated-at value for a file of ``schema``.

    Args:
        schema: The file's layout id.
        data: The parsed top-level JSON object.

    Returns:
        The value as a string, or ``None`` when the file lacks it.
    """
    value: object
    if schema in ("A", "A2"):
        meta = data.get("resultsMeta")
        value = meta.get("resultsUpdatedAt") if isinstance(meta, dict) else None
    elif schema == "B":
        meta = data.get("meta")
        value = meta.get("timestamp") if isinstance(meta, dict) else None
    else:
        value = data.get("generated")
    return value if isinstance(value, str) else None


def _checked_url(source: Source) -> str:
    if source.status is SourceStatus.PENDING or source.url is None:
        raise SourcePendingError(
            f"{_label(source)} is pending (contest not held yet); nothing to fetch",
        )
    _check_url(source.url)
    return source.url


def _check_url(url: str) -> None:
    """Raise unless ``url`` is plain https on an allowed host.

    Userinfo and explicit ports are refused too, so look-alikes such as
    ``https://static01.nyt.com@evil.example/`` cannot slip through.

    Raises:
        DisallowedHostError: If the URL fails the check.
    """
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_HOSTS
        or parts.username is not None
        or port is not None
    ):
        allowed = ", ".join(sorted(ALLOWED_HOSTS))
        raise DisallowedHostError(f"refusing {url}: only https on {allowed} is fetched")


def _open(request: Request, timeout: float) -> _Response:
    response: _Response = _OPENER.open(request, timeout=timeout)
    return response


def _download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _open(request, TIMEOUT_SECONDS) as response:
            # Redirects are vetted by the handler; this re-checks where the
            # response actually came from.
            _check_url(response.geturl())
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                limit_mib = MAX_RESPONSE_BYTES / (1024 * 1024)
                raise DownloadError(
                    f"{url} returned more than {limit_mib:g} MiB; not read",
                )
            # A bounded read(n) returns a cut-off body without complaint, so
            # check the server is not still owed bytes.
            if response.length:
                raise DownloadError(
                    f"{url} was cut short: {response.length} bytes still owed",
                )
    except (OSError, HTTPException) as err:
        # OSError covers URLError, HTTPError, timeouts and connection resets;
        # HTTPException covers IncompleteRead, RemoteDisconnected, BadStatusLine.
        raise DownloadError(f"could not download {url}: {err}") from err
    return raw


def _parse(raw: bytes, *, what: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as err:
        # The C JSON decoder raises RecursionError on very deep nesting.
        raise InvalidJsonError(f"{what} is not valid JSON: {err}") from err
    if not isinstance(data, dict):
        raise InvalidJsonError(f"{what} is JSON but not an object")
    return data


@dataclass(frozen=True, slots=True)
class CachedSnapshot:
    """What the cache's pointer says about a source's newest snapshot.

    Attributes:
        snapshot_path: The snapshot file the pointer names (it exists).
        retrieved_at: When it was downloaded (UTC).
        source_updated_at: The feed's own updated-at value, verbatim, or
            ``None`` when the file does not carry one.
    """

    snapshot_path: Path
    retrieved_at: datetime
    source_updated_at: str | None


def cached_snapshot_info(source: Source, cache_dir: Path) -> CachedSnapshot | None:
    """Describe ``source``'s newest cached snapshot without reading the snapshot.

    Makes the same checks an offline :func:`fetch` makes before it reads the
    snapshot: the pointer is valid, was cached from the source's current URL and
    schema, and names a snapshot file that is there.

    Args:
        source: The NYT file.
        cache_dir: Root of the cache.

    Returns:
        The snapshot's details, or ``None`` when nothing is cached for it.

    Raises:
        SourcePendingError: If the source's contest has not been held.
        DisallowedHostError: If the source's URL is not an allowed one.
        NoCachedSnapshotError: If the pointer is for another URL or schema, or
            its snapshot is missing or not a file, so an offline read would fail.
        InvalidJsonError: If the pointer is corrupt.
    """
    _checked_url(source)
    if not _pointer_path(source, cache_dir).exists():
        return None
    info = _latest_pointer(source, cache_dir)
    if not info.snapshot_path.is_file():
        raise NoCachedSnapshotError(f"cannot read {info.snapshot_path}: not a file")
    return info


def _latest_pointer(source: Source, cache_dir: Path) -> CachedSnapshot:
    """Read and check ``source``'s pointer; see :func:`cached_snapshot_info`."""
    pointer_path = _pointer_path(source, cache_dir)
    try:
        pointer_raw = pointer_path.read_bytes()
    except FileNotFoundError as err:
        raise NoCachedSnapshotError(
            f"nothing cached for {_label(source)} under {cache_dir}; "
            "fetch it online first",
        ) from err
    except OSError as err:
        raise NoCachedSnapshotError(f"cannot read {pointer_path}: {err}") from err
    pointer = _parse(pointer_raw, what=str(pointer_path))

    if pointer.get("url") != source.url or pointer.get("schema") != source.schema:
        raise NoCachedSnapshotError(
            f"{pointer_path} was cached from {pointer.get('url')} "
            f"(schema {pointer.get('schema')}), not the current {source.url} "
            f"(schema {source.schema}); fetch it online again",
        )

    name = pointer.get("snapshot")
    if not _is_snapshot_name(name, source):
        raise InvalidJsonError(f"{pointer_path} names no valid snapshot: {name!r}")
    retrieved_at = _parse_retrieved_at(pointer.get("retrieved_at"), pointer_path)

    snapshot = pointer_path.parent / name
    if not snapshot.exists():
        raise NoCachedSnapshotError(
            f"{pointer_path} names {snapshot.name}, which is missing",
        )
    updated_at = pointer.get("source_updated_at")
    return CachedSnapshot(
        snapshot_path=snapshot,
        retrieved_at=retrieved_at,
        source_updated_at=updated_at if isinstance(updated_at, str) else None,
    )


def _read_latest(source: Source, cache_dir: Path) -> FetchResult:
    info = _latest_pointer(source, cache_dir)
    snapshot = info.snapshot_path
    try:
        raw = snapshot.read_bytes()
    except FileNotFoundError as err:
        pointer_path = _pointer_path(source, cache_dir)
        raise NoCachedSnapshotError(
            f"{pointer_path} names {snapshot.name}, which is missing",
        ) from err
    except OSError as err:
        raise NoCachedSnapshotError(f"cannot read {snapshot}: {err}") from err
    return FetchResult(
        source=source,
        data=_parse(raw, what=str(snapshot)),
        retrieved_at=info.retrieved_at,
        source_updated_at=info.source_updated_at,
        cache_path=snapshot,
        from_cache=True,
    )


def _is_snapshot_name(name: object, source: Source) -> TypeGuard[str]:
    """Whether ``name`` is a bare snapshot file name for ``source``.

    It must sit beside the pointer (no path parts), start with the source's
    ``<chamber>-<kind>-`` stem, end in ``.json`` and not be the pointer itself.
    A NUL byte is refused too: the filesystem calls raise ``ValueError`` on it.
    """
    if not isinstance(name, str) or "\x00" in name or Path(name).name != name:
        return False
    prefix = f"{_stem(source)}-"
    return (
        name.startswith(prefix)
        and name.endswith(".json")
        and len(name) > len(prefix) + len(".json")
        and name != f"{prefix}latest.json"
    )


def _parse_retrieved_at(value: object, pointer_path: Path) -> datetime:
    """Parse the pointer's ``retrieved_at``, which must be timezone-aware."""
    if not isinstance(value, str):
        raise InvalidJsonError(f"{pointer_path} has no retrieval time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise InvalidJsonError(
            f"{pointer_path} has a bad retrieval time {value!r}",
        ) from err
    if parsed.tzinfo is None:
        raise InvalidJsonError(
            f"{pointer_path} has a retrieval time without a timezone: {value!r}",
        )
    return parsed


def _write_atomic(path: Path, payload: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        # A no-op once the rename has happened; removes the leftover otherwise.
        tmp.unlink(missing_ok=True)


def _pointer_path(source: Source, cache_dir: Path) -> Path:
    return cache_dir / str(source.year) / f"{_stem(source)}-latest.json"


def _stem(source: Source) -> str:
    return f"{source.chamber}-{source.kind}"


def _label(source: Source) -> str:
    return f"{source.year}/{source.chamber}/{source.kind}"


def _utcnow() -> datetime:
    return datetime.now(UTC)
