"""Unit tests for the shared Wikipedia fetch helper.

``urlopen`` is replaced with an in-memory fake — no network access required.
"""

from __future__ import annotations

from http.client import IncompleteRead
from types import TracebackType
from urllib.request import Request

import pytest

from polls.importers import wikipedia_common
from polls.importers.wikipedia_common import (
    MAX_PAGE_BYTES,
    PageTooLargeError,
    fetch_html,
)

URL = "https://en.wikipedia.org/wiki/Example"


class _FakeResponse:
    """A ``urlopen`` stand-in serving ``size`` bytes; never touches the network.

    ``promised`` is the ``Content-Length`` the server announced (``None`` for a
    chunked response). ``length`` tracks what is still owed after each read,
    as ``http.client.HTTPResponse`` does.
    """

    def __init__(self, size: int, promised: int | None = None) -> None:
        self._size = size
        self.length = promised

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        """Return up to ``amount`` bytes (the whole body when negative)."""
        count = self._size if amount < 0 else min(amount, self._size)
        if self.length is not None:
            self.length = max(0, self.length - count)
        return b"a" * count


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    promised: int | None = None,
) -> None:
    """Point ``wikipedia_common.urlopen`` at a fake serving ``size`` bytes."""

    def fake_urlopen(req: Request, timeout: int) -> _FakeResponse:
        return _FakeResponse(size, promised)

    monkeypatch.setattr(wikipedia_common, "urlopen", fake_urlopen)


class TestFetchHtml:
    def test_over_default_limit_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, MAX_PAGE_BYTES + 1)
        with pytest.raises(PageTooLargeError) as err:
            fetch_html(URL)
        assert URL in str(err.value)
        assert "8 MiB" in str(err.value)

    def test_exactly_at_default_limit_is_returned(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, MAX_PAGE_BYTES, promised=MAX_PAGE_BYTES)
        assert len(fetch_html(URL)) == MAX_PAGE_BYTES

    def test_custom_max_bytes_is_honoured(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 2 MiB passes the default cap but not a 1.5 MiB override.
        _serve(monkeypatch, 2 * 1024 * 1024)
        assert len(fetch_html(URL)) == 2 * 1024 * 1024
        with pytest.raises(PageTooLargeError) as err:
            fetch_html(URL, max_bytes=3 * 512 * 1024)
        assert "1.5 MiB" in str(err.value)

    def test_body_cut_short_of_content_length_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The connection drops 100 bytes into a 1,000-byte page.
        _serve(monkeypatch, 100, promised=1000)
        with pytest.raises(IncompleteRead):
            fetch_html(URL)

    def test_chunked_body_without_length_is_returned(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _serve(monkeypatch, 100, promised=None)
        assert fetch_html(URL) == "a" * 100
