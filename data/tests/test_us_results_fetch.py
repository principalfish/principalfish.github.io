"""Tests for the NYT results source table and fetch/cache layer.

The HTTP layer (``urlopen`` in ``importers.results.us.nyt.fetch``) is stubbed, so no
test touches the network; every cache lives under ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
from datetime import UTC, date, datetime
from http.client import BadStatusLine, HTTPMessage, RemoteDisconnected
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request

import pytest

import config
from importers.results.us.nyt import fetch as fetch_module
from importers.results.us.nyt.fetch import (
    CachedSnapshot,
    DisallowedHostError,
    InvalidJsonError,
    NoCachedSnapshotError,
    SourcePendingError,
    cached_snapshot_info,
    fetch,
    source_updated_at,
)
from importers.results.us.nyt.sources import (
    SOURCES,
    Chamber,
    SchemaId,
    Source,
    SourceStatus,
    UnknownSourceError,
    source_for,
    supported_years,
)


class _FakeResponse:
    """Stands in for the response the fetch module's opener returns."""

    def __init__(
        self,
        body: bytes,
        *,
        url: str = "https://static01.nyt.com/stub.json",
        length: int | None = None,
    ) -> None:
        self._body = body
        self._url = url
        self.length = length

    def read(self, amount: int = -1) -> bytes:
        return self._body if amount < 0 else self._body[:amount]

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class _FakeHttp:
    """Serves queued bodies in order and records each request."""

    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> _FakeResponse:
        self.requests.append(request)
        return _FakeResponse(self.bodies.pop(0), url=request.full_url)


@pytest.fixture()
def http(monkeypatch: pytest.MonkeyPatch) -> _FakeHttp:
    """Replace the fetch module's ``_open`` with a recording stub."""
    fake = _FakeHttp()
    monkeypatch.setattr(fetch_module, "_open", fake)
    return fake


def _stub_open(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeResponse | BaseException,
) -> None:
    """Make every request return ``response``, or raise it."""

    def fake_open(request: Request, timeout: float) -> _FakeResponse:
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(fetch_module, "_open", fake_open)


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Make the fetch module's clock return queued times in order."""
    times: list[datetime] = []
    monkeypatch.setattr(fetch_module, "_utcnow", lambda: times.pop(0))
    return times


def _schema_a(updated: str) -> bytes:
    return json.dumps(
        {"races": [], "resultsMeta": {"resultsUpdatedAt": updated}},
    ).encode()


def _pointer(cache: Path, year: int, stem: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (cache / str(year) / f"{stem}-latest.json").read_text(),
    )
    return loaded


# --- sources -----------------------------------------------------------------


def test_source_for_known_key() -> None:
    source = source_for(2024, "house")
    assert source.kind == "general"
    assert source.schema == "A"
    assert source.status is SourceStatus.AVAILABLE
    assert source.url is not None and source.url.endswith("results-house.json")
    assert source.election_date == date(2024, 11, 5)


def test_source_for_unknown_key_names_it() -> None:
    with pytest.raises(UnknownSourceError, match="2016/house/general"):
        source_for(2016, "house")
    with pytest.raises(UnknownSourceError, match="2024/senate/runoff"):
        source_for(2024, "senate", "runoff")


def test_supported_years() -> None:
    assert supported_years() == (2018, 2020, 2022, 2024, 2026)


def test_table_keys_match_sources() -> None:
    for key, source in SOURCES.items():
        assert source.key == key


def test_runoff_sources() -> None:
    runoffs = {key for key in SOURCES if key[2] == "runoff"}
    assert runoffs == {
        (2018, "senate", "runoff"),
        (2020, "house", "runoff"),
        (2020, "senate", "runoff"),
        (2022, "senate", "runoff"),
        (2026, "house", "runoff"),
    }
    expected_dates: dict[tuple[int, Chamber], tuple[date, SchemaId, str]] = {
        (2018, "senate"): (date(2018, 11, 27), "C", "int.nyt.com"),
        (2020, "house"): (date(2020, 12, 5), "B", "static01.nyt.com"),
        (2020, "senate"): (date(2021, 1, 5), "B", "static01.nyt.com"),
        (2022, "senate"): (date(2022, 12, 6), "A2", "static01.nyt.com"),
    }
    for (year, chamber), (when, schema, host) in expected_dates.items():
        source = source_for(year, chamber, "runoff")
        assert source.status is SourceStatus.AVAILABLE
        assert source.election_date == when
        assert source.schema == schema
        assert source.url is not None and f"//{host}/" in source.url
    pending = source_for(2026, "house", "runoff")
    assert pending.status is SourceStatus.PENDING
    assert pending.url is None


def test_2018_chambers_share_one_url() -> None:
    assert source_for(2018, "house").url == source_for(2018, "senate").url


def test_available_source_requires_url() -> None:
    with pytest.raises(ValueError, match="needs a url"):
        Source(
            year=2030,
            chamber="house",
            kind="general",
            schema="A",
            status=SourceStatus.AVAILABLE,
            url=None,
            election_date=date(2030, 11, 5),
        )


def test_cache_dir_default_is_outside_repo() -> None:
    repo = Path(config.__file__).resolve().parent.parent
    assert repo not in config.NYT_CACHE_DIR.resolve().parents


# --- fetch online ------------------------------------------------------------


def test_fetch_writes_snapshot_and_pointer(
    tmp_path: Path,
    http: _FakeHttp,
    clock: list[datetime],
) -> None:
    source = source_for(2026, "house")
    http.bodies.append(_schema_a("2026-11-07T11:02:45.497Z"))
    clock.append(datetime(2026, 11, 7, 12, 0, 0, 123456, tzinfo=UTC))

    result = fetch(source, tmp_path)

    expected = tmp_path / "2026" / "house-general-20261107T120000.123456Z.json"
    assert result.cache_path == expected
    assert expected.read_bytes() == _schema_a("2026-11-07T11:02:45.497Z")
    assert result.data["resultsMeta"]["resultsUpdatedAt"] == "2026-11-07T11:02:45.497Z"
    assert result.source_updated_at == "2026-11-07T11:02:45.497Z"
    assert result.retrieved_at == datetime(2026, 11, 7, 12, 0, 0, 123456, tzinfo=UTC)
    assert result.from_cache is False
    assert _pointer(tmp_path, 2026, "house-general") == {
        "snapshot": expected.name,
        "url": source.url,
        "schema": "A",
        "retrieved_at": "2026-11-07T12:00:00.123456+00:00",
        "source_updated_at": "2026-11-07T11:02:45.497Z",
    }
    # No temporary files are left behind.
    assert sorted(p.name for p in (tmp_path / "2026").iterdir()) == [
        "house-general-20261107T120000.123456Z.json",
        "house-general-latest.json",
    ]


def test_fetch_sends_generic_user_agent_and_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_open(request: Request, timeout: float) -> _FakeResponse:
        seen["agent"] = request.get_header("User-agent")
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return _FakeResponse(_schema_a("x"), url=request.full_url)

    monkeypatch.setattr(fetch_module, "_open", fake_open)
    source = source_for(2024, "senate")
    fetch(source, tmp_path)

    assert seen["url"] == source.url
    assert seen["agent"] == fetch_module.USER_AGENT
    assert "@" not in seen["agent"]
    assert seen["timeout"] == fetch_module.TIMEOUT_SECONDS


def test_second_fetch_moves_latest_pointer(
    tmp_path: Path,
    http: _FakeHttp,
    clock: list[datetime],
) -> None:
    source = source_for(2026, "senate")
    http.bodies += [_schema_a("first"), _schema_a("second")]
    clock += [
        datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
        datetime(2026, 11, 7, 13, 0, tzinfo=UTC),
    ]

    first = fetch(source, tmp_path)
    second = fetch(source, tmp_path)

    assert first.cache_path != second.cache_path
    assert first.cache_path.exists() and second.cache_path.exists()
    pointer = _pointer(tmp_path, 2026, "senate-general")
    assert pointer["snapshot"] == second.cache_path.name
    assert pointer["source_updated_at"] == "second"

    offline = fetch(source, tmp_path, offline=True)
    assert offline.source_updated_at == "second"
    assert offline.cache_path == second.cache_path


def test_2018_caches_each_chamber_separately(
    tmp_path: Path,
    http: _FakeHttp,
) -> None:
    body = json.dumps({"generated": "2019-05-15T19:08:52.339Z", "races": []})
    http.bodies += [body.encode(), body.encode()]

    house = fetch(source_for(2018, "house"), tmp_path)
    senate = fetch(source_for(2018, "senate"), tmp_path)

    assert house.cache_path.name.startswith("house-general-")
    assert senate.cache_path.name.startswith("senate-general-")
    assert house.source_updated_at == "2019-05-15T19:08:52.339Z"
    assert _pointer(tmp_path, 2018, "house-general")["snapshot"] == (
        house.cache_path.name
    )
    assert _pointer(tmp_path, 2018, "senate-general")["snapshot"] == (
        senate.cache_path.name
    )


@pytest.mark.parametrize(
    ("schema", "data", "expected"),
    [
        ("A", {"resultsMeta": {"resultsUpdatedAt": "t"}}, "t"),
        ("A2", {"resultsMeta": {"resultsUpdatedAt": "t"}}, "t"),
        ("B", {"data": {}, "meta": {"timestamp": "t"}}, "t"),
        ("C", {"generated": "t"}, "t"),
        ("A", {"races": []}, None),
        ("B", {"meta": "unexpected"}, None),
        ("C", {"generated": 5}, None),
    ],
)
def test_source_updated_at(
    schema: Any,
    data: dict[str, Any],
    expected: str | None,
) -> None:
    assert source_updated_at(schema, data) == expected


# --- refusals ----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.nytimes.com/interactive/2024/11/05/us/elections/results.json",
        "https://example.com/results-house.json",
        "http://static01.nyt.com/elections-assets/results-house.json",
        "https://static01.nyt.com.evil.com/results-house.json",
        "https://static01.nyt.com@evil.com/results-house.json",
        "https://static01.nyt.com:abc/results-house.json",
        "https://user@static01.nyt.com/results-house.json",
        "https://static01.nyt.com:8443/results-house.json",
        "http://int.nyt.com/applications/elections/2018/api/1/races/x.json",
        "ftp://static01.nyt.com/results-house.json",
    ],
)
def test_disallowed_host_refused_before_request(
    tmp_path: Path,
    http: _FakeHttp,
    url: str,
) -> None:
    source = dataclasses.replace(source_for(2024, "house"), url=url)

    with pytest.raises(DisallowedHostError):
        fetch(source, tmp_path)
    with pytest.raises(DisallowedHostError):
        fetch(source, tmp_path, offline=True)

    assert http.requests == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "body",
    [b"<html>captcha</html>", b"[1, 2, 3]", b"\xff\xfe not utf-8", b""],
)
def test_invalid_json_not_cached(
    tmp_path: Path,
    http: _FakeHttp,
    body: bytes,
) -> None:
    http.bodies.append(body)

    with pytest.raises(InvalidJsonError):
        fetch(source_for(2022, "house"), tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_pending_source_refused(tmp_path: Path, http: _FakeHttp) -> None:
    source = source_for(2026, "house", "runoff")

    with pytest.raises(SourcePendingError, match="pending"):
        fetch(source, tmp_path)
    with pytest.raises(SourcePendingError):
        fetch(source, tmp_path, offline=True)

    assert http.requests == []


def test_oversized_response_refused(
    tmp_path: Path,
    http: _FakeHttp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_module, "MAX_RESPONSE_BYTES", 10)
    http.bodies.append(_schema_a("x"))

    with pytest.raises(fetch_module.DownloadError, match="more than"):
        fetch(source_for(2024, "house"), tmp_path)

    assert list(tmp_path.iterdir()) == []


# --- offline -----------------------------------------------------------------


def test_offline_hit_reads_latest_without_network(
    tmp_path: Path,
    http: _FakeHttp,
    clock: list[datetime],
) -> None:
    source = source_for(2020, "house")
    body = json.dumps({"data": {"races": []}, "meta": {"timestamp": "ts"}})
    http.bodies.append(body.encode())
    clock.append(datetime(2026, 11, 7, 9, 30, tzinfo=UTC))
    online = fetch(source, tmp_path)

    result = fetch(source, tmp_path, offline=True)

    assert len(http.requests) == 1
    assert result.from_cache is True
    assert result.cache_path == online.cache_path
    assert result.data == {"data": {"races": []}, "meta": {"timestamp": "ts"}}
    assert result.retrieved_at == datetime(2026, 11, 7, 9, 30, tzinfo=UTC)
    assert result.source_updated_at == "ts"


def test_offline_miss_is_clear(tmp_path: Path, http: _FakeHttp) -> None:
    with pytest.raises(NoCachedSnapshotError, match="2020/senate/general"):
        fetch(source_for(2020, "senate"), tmp_path, offline=True)
    assert http.requests == []


def test_offline_missing_snapshot_is_clear(
    tmp_path: Path,
    http: _FakeHttp,
) -> None:
    http.bodies.append(_schema_a("x"))
    online = fetch(source_for(2024, "house"), tmp_path)
    online.cache_path.unlink()

    with pytest.raises(NoCachedSnapshotError, match="missing"):
        fetch(source_for(2024, "house"), tmp_path, offline=True)


_HOUSE_24 = source_for(2024, "house")
_GOOD_SNAPSHOT = "house-general-20261107T120000.000000Z.json"


def _write_cache(
    cache: Path,
    *,
    snapshot_body: bytes | None = b'{"races": []}',
    pointer_body: bytes | None = None,
    **overrides: Any,
) -> Path:
    """Hand-write a 2024 House cache entry; returns the pointer path."""
    folder = cache / "2024"
    folder.mkdir(parents=True, exist_ok=True)
    if snapshot_body is not None:
        (folder / _GOOD_SNAPSHOT).write_bytes(snapshot_body)
    pointer = {
        "snapshot": _GOOD_SNAPSHOT,
        "url": _HOUSE_24.url,
        "schema": _HOUSE_24.schema,
        "retrieved_at": "2026-11-07T12:00:00+00:00",
        "source_updated_at": None,
    }
    pointer.update(overrides)
    path = folder / "house-general-latest.json"
    path.write_bytes(pointer_body or json.dumps(pointer).encode())
    return path


def test_hand_written_cache_reads(tmp_path: Path) -> None:
    _write_cache(tmp_path)
    result = fetch(_HOUSE_24, tmp_path, offline=True)
    assert result.cache_path.name == _GOOD_SNAPSHOT
    assert result.retrieved_at == datetime(2026, 11, 7, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "..",
        "../house-general-x.json",
        "sub/house-general-x.json",
        "senate-general-20261107T120000.000000Z.json",
        "house-general-.json",
        "house-general-latest.json",
        "house-general-20261107T120000.000000Z.txt",
        "house-general-\x00.json",
        None,
        5,
    ],
)
def test_offline_bad_snapshot_name_refused(tmp_path: Path, name: object) -> None:
    _write_cache(tmp_path, snapshot=name)
    with pytest.raises(InvalidJsonError, match="no valid snapshot"):
        fetch(_HOUSE_24, tmp_path, offline=True)


@pytest.mark.parametrize(
    "retrieved_at",
    ["not a time", "2026-11-07T12:00:00", None, 17],
)
def test_offline_bad_retrieved_at_refused(
    tmp_path: Path,
    retrieved_at: object,
) -> None:
    _write_cache(tmp_path, retrieved_at=retrieved_at)
    with pytest.raises(InvalidJsonError, match="retrieval time"):
        fetch(_HOUSE_24, tmp_path, offline=True)


@pytest.mark.parametrize(
    "pointer_body",
    [b"{not json", b"[]", b"[" * 100_000 + b"]" * 100_000],
)
def test_offline_corrupt_pointer_refused(
    tmp_path: Path,
    pointer_body: bytes,
) -> None:
    _write_cache(tmp_path, pointer_body=pointer_body)
    with pytest.raises(InvalidJsonError):
        fetch(_HOUSE_24, tmp_path, offline=True)


@pytest.mark.parametrize("snapshot_body", [b"{truncated", b'"a string"'])
def test_offline_corrupt_snapshot_refused(
    tmp_path: Path,
    snapshot_body: bytes,
) -> None:
    _write_cache(tmp_path, snapshot_body=snapshot_body)
    with pytest.raises(InvalidJsonError):
        fetch(_HOUSE_24, tmp_path, offline=True)


def test_offline_unreadable_snapshot_is_no_cache(tmp_path: Path) -> None:
    pointer = _write_cache(tmp_path, snapshot_body=None)
    (pointer.parent / _GOOD_SNAPSHOT).mkdir()  # reading a directory fails
    with pytest.raises(NoCachedSnapshotError, match="cannot read"):
        fetch(_HOUSE_24, tmp_path, offline=True)


@pytest.mark.parametrize(
    "override",
    [
        {"url": "https://static01.nyt.com/elections-assets/old/results-house.json"},
        {"schema": "A2"},
        {"url": None},
    ],
)
def test_offline_stale_source_definition_is_no_cache(
    tmp_path: Path,
    override: dict[str, Any],
) -> None:
    _write_cache(tmp_path, **override)
    with pytest.raises(NoCachedSnapshotError, match="fetch it online again"):
        fetch(_HOUSE_24, tmp_path, offline=True)


# --- download failures and redirects ------------------------------------------


def test_truncated_body_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_open(
        monkeypatch,
        _FakeResponse(b'{"races": [', url=_HOUSE_24.url or "", length=500),
    )
    with pytest.raises(fetch_module.DownloadError, match="cut short"):
        fetch(_HOUSE_24, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_oversize_with_content_length_reports_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_module, "MAX_RESPONSE_BYTES", 10)
    body = _schema_a("x")
    _stub_open(
        monkeypatch,
        _FakeResponse(body, url=_HOUSE_24.url or "", length=len(body) - 11),
    )
    with pytest.raises(fetch_module.DownloadError, match="more than"):
        fetch(_HOUSE_24, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "error",
    [
        URLError("name resolution failed"),
        HTTPError("https://static01.nyt.com/x", 404, "Gone", HTTPMessage(), None),
        ConnectionResetError("reset by peer"),
        TimeoutError("timed out"),
        RemoteDisconnected("closed"),
        BadStatusLine("garbage"),
    ],
)
def test_transport_errors_become_download_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    _stub_open(monkeypatch, error)
    with pytest.raises(fetch_module.DownloadError, match="could not download"):
        fetch(_HOUSE_24, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_response_from_disallowed_host_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_open(
        monkeypatch,
        _FakeResponse(_schema_a("x"), url="https://evil.com/results-house.json"),
    )
    with pytest.raises(DisallowedHostError):
        fetch(_HOUSE_24, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.com/results-house.json",
        "http://static01.nyt.com/results-house.json",
        "https://static01.nyt.com@evil.com/results-house.json",
    ],
)
def test_redirect_off_allowlist_refused(target: str) -> None:
    handler = fetch_module._AllowlistRedirectHandler()
    stream = io.BytesIO(b"")
    request = Request(_HOUSE_24.url or "")

    with pytest.raises(DisallowedHostError):
        handler.redirect_request(
            request,
            stream,
            302,
            "Found",
            HTTPMessage(),
            target,
        )
    assert stream.closed


def test_redirect_within_allowlist_followed() -> None:
    handler = fetch_module._AllowlistRedirectHandler()
    target = "https://static01.nyt.com/elections-assets/moved/results-house.json"

    followed = handler.redirect_request(
        Request(_HOUSE_24.url or ""),
        io.BytesIO(b""),
        301,
        "Moved",
        HTTPMessage(),
        target,
    )

    assert followed is not None and followed.full_url == target


def test_opener_uses_allowlist_redirect_handler() -> None:
    # OpenerDirector.handlers exists at runtime but is missing from the stubs.
    handlers: list[object] = getattr(fetch_module._OPENER, "handlers")
    redirects = [h for h in handlers if isinstance(h, HTTPRedirectHandler)]
    assert len(redirects) == 1
    assert isinstance(redirects[0], fetch_module._AllowlistRedirectHandler)


def test_deeply_nested_json_refused(tmp_path: Path, http: _FakeHttp) -> None:
    http.bodies.append(b"[" * 100_000 + b"]" * 100_000)
    with pytest.raises(InvalidJsonError):
        fetch(_HOUSE_24, tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- atomic writes -----------------------------------------------------------


@pytest.mark.parametrize("failing", ["fsync", "replace"])
def test_failed_write_keeps_previous_pointer(
    tmp_path: Path,
    http: _FakeHttp,
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
) -> None:
    http.bodies += [_schema_a("first"), _schema_a("second")]
    first = fetch(_HOUSE_24, tmp_path)

    def boom(*args: object) -> None:
        raise OSError("disk full")

    if failing == "fsync":
        monkeypatch.setattr(os, "fsync", boom)
    else:
        monkeypatch.setattr(Path, "replace", boom)

    with pytest.raises(OSError, match="disk full"):
        fetch(_HOUSE_24, tmp_path)
    monkeypatch.undo()

    folder = tmp_path / "2024"
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [first.cache_path.name, "house-general-latest.json"],
    )
    assert _pointer(tmp_path, 2024, "house-general")["snapshot"] == (
        first.cache_path.name
    )


# --- config -------------------------------------------------------------------


def test_nyt_cache_dir_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NYT_CACHE_DIR", str(tmp_path / "cache"))
    assert config.NytCacheConfig().nyt_cache_dir == tmp_path / "cache"


def test_nyt_cache_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NYT_CACHE_DIR", raising=False)
    default = Path("/home/philiph/dbs/nyt-cache")
    assert config.NytCacheConfig().nyt_cache_dir == default


# --- cached_snapshot_info ----------------------------------------------------


def test_snapshot_info_describes_the_pointer_without_reading_the_snapshot(
    tmp_path: Path,
) -> None:
    # The snapshot is not valid JSON: the info must not parse it.
    _write_cache(tmp_path, snapshot_body=b"{truncated", source_updated_at="u1")

    info = cached_snapshot_info(_HOUSE_24, tmp_path)

    assert info == CachedSnapshot(
        snapshot_path=tmp_path / "2024" / _GOOD_SNAPSHOT,
        retrieved_at=datetime(2026, 11, 7, 12, tzinfo=UTC),
        source_updated_at="u1",
    )


def test_snapshot_info_is_none_when_nothing_cached(tmp_path: Path) -> None:
    assert cached_snapshot_info(_HOUSE_24, tmp_path) is None


def test_snapshot_info_refuses_pending_source(tmp_path: Path) -> None:
    with pytest.raises(SourcePendingError):
        cached_snapshot_info(source_for(2026, "house", "runoff"), tmp_path)


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        (
            {"url": "https://static01.nyt.com/elections-assets/old.json"},
            NoCachedSnapshotError,
            "not the current",
        ),
        ({"schema": "A2"}, NoCachedSnapshotError, "not the current"),
        ({"snapshot": "../x.json"}, InvalidJsonError, "no valid snapshot"),
        ({"retrieved_at": "yesterday"}, InvalidJsonError, "retrieval time"),
    ],
)
def test_snapshot_info_refuses_a_pointer_offline_reads_refuse(
    tmp_path: Path,
    overrides: dict[str, Any],
    error: type[Exception],
    match: str,
) -> None:
    _write_cache(tmp_path, **overrides)

    with pytest.raises(error, match=match):
        cached_snapshot_info(_HOUSE_24, tmp_path)


def test_snapshot_info_refuses_missing_snapshot(tmp_path: Path) -> None:
    _write_cache(tmp_path, snapshot_body=None)

    with pytest.raises(NoCachedSnapshotError, match="which is missing"):
        cached_snapshot_info(_HOUSE_24, tmp_path)


def test_snapshot_info_refuses_snapshot_that_is_not_a_file(tmp_path: Path) -> None:
    pointer = _write_cache(tmp_path, snapshot_body=None)
    (pointer.parent / _GOOD_SNAPSHOT).mkdir()

    with pytest.raises(NoCachedSnapshotError, match="not a file"):
        cached_snapshot_info(_HOUSE_24, tmp_path)


def test_snapshot_info_refuses_corrupt_pointer(tmp_path: Path) -> None:
    _write_cache(tmp_path, pointer_body=b"{not json")

    with pytest.raises(InvalidJsonError):
        cached_snapshot_info(_HOUSE_24, tmp_path)
