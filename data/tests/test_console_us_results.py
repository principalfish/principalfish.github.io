"""Exercise the US results console routes with the real pipeline and a temp DB.

Only NYT downloads and subprocesses are replaced: downloads serve the
hand-made fixtures under ``tests/fixtures/nyt`` (which a test may edit first),
and the ``export_runner`` records every script run, which none of these routes
should make. The out-dir and the NYT cache are temporary folders set through
the app config, and tripwires fail any test that would reach the network or run
a script without opting in.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient
from sqlalchemy.exc import OperationalError

from console.shared import runner as script_runner
from console.shared.preview import PREVIEW_CACHE, store_preview
from console.us import results as us_results
from console.us.models import MODEL_RUN_LOCK, model_run_slot
from core.db import Database
from core.schema import Election, ElectionType, Party
from importers.results.us.import_senate_elections import import_senate
from importers.results.us.nyt import cli
from importers.results.us.nyt import fetch as fetch_module
from importers.results.us.nyt.convert import ConversionResult
from importers.results.us.nyt.current_senate import DEFAULT_SNAPSHOT, render
from importers.results.us.nyt.sources import source_for, supported_years
from importers.results.us.us_import import PARTY_KEY_TO_NAME
from tests.console_fixtures import (
    RecordingRunner,
    app,
    command_line,
    flashes,
    flashes_on_page,
    select_block,
)
from tests.senate_current_fixtures import senate_election

FIXTURES = Path(__file__).parent / "fixtures" / "nyt"
_EXPIRED = "Preview expired. Please preview again."
_CROSS_SITE = {"Sec-Fetch-Site": "cross-site"}
_HOUSE = "2026 US House Election"
_RESTORED = "The out-dir files were put back as they were."


@dataclasses.dataclass
class FakeNyt:
    """Fixture bodies served in place of NYT, and the URLs requested."""

    bodies: dict[str, bytes]
    requested: list[str] = dataclasses.field(default_factory=list)

    def edit_2026_house(self, edit: Any) -> None:
        """Serve the 2026 House file after ``edit`` changes its parsed JSON."""
        url = str(source_for(2026, "house").url)
        data = json.loads(self.bodies[url])
        edit(data)
        self.bodies[url] = json.dumps(data).encode()


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    """The (out-dir, cache dir) the app is configured with."""
    return tmp_path / "out", tmp_path / "cache"


@pytest.fixture(autouse=True)
def _isolate(
    app: Flask,
    db: Database,
    dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    def unexpected_run(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("Running a script requires the export_runner fixture")

    def unexpected_download(url: str) -> NoReturn:
        raise AssertionError(f"Downloading {url} requires the nyt fixture")

    out_dir, cache_dir = dirs
    app.config[us_results.OUT_DIR_CONFIG] = out_dir
    app.config[us_results.CACHE_DIR_CONFIG] = cache_dir
    # Never the committed Current Senate file: a test that rebuilds it opts in.
    app.config[us_results.SNAPSHOT_CONFIG] = out_dir.parent / "senate-current.json"
    monkeypatch.setattr(us_results, "get_db", lambda: db)
    monkeypatch.setattr(script_runner, "run_command", unexpected_run)
    monkeypatch.setattr(fetch_module, "_download", unexpected_download)
    for name in PARTY_KEY_TO_NAME.values():
        db.add_party(name)
    PREVIEW_CACHE.clear()
    yield
    PREVIEW_CACHE.clear()


@pytest.fixture()
def nyt(monkeypatch: pytest.MonkeyPatch) -> FakeNyt:
    """Serve the fixture files in place of NYT."""
    fake = FakeNyt(
        bodies={
            str(source.url): (FIXTURES / fixture).read_bytes()
            for source, fixture in (
                (source_for(2026, "house"), "schema-a.json"),
                (source_for(2026, "senate"), "schema-a.json"),
            )
        },
    )

    def fake_download(url: str) -> bytes:
        fake.requested.append(url)
        return fake.bodies[url]

    monkeypatch.setattr(fetch_module, "_download", fake_download)
    return fake


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[datetime]:
    """Make each fetch's retrieval time one minute after the last."""
    start = datetime(2026, 11, 7, 12, tzinfo=UTC)
    times = (start + timedelta(minutes=n) for n in range(1000))
    monkeypatch.setattr(fetch_module, "_utcnow", lambda: next(times))
    return times


@pytest.fixture()
def export_runner(monkeypatch: pytest.MonkeyPatch) -> RecordingRunner:
    """Record every subprocess the console would start instead of starting it."""
    runner = RecordingRunner()
    monkeypatch.setattr(script_runner, "run_command", runner)
    return runner


def _preview(
    client: FlaskClient[Response],
    year: int = 2026,
    chamber: str = "house",
    source: str = "fetch",
) -> Response:
    return client.post(
        f"/{chamber}/results/preview",
        data={"year": str(year), "source": source},
    )


def _token(response: Response) -> str:
    match = re.search(
        r'action="/(?:house|senate)/results/confirm/([0-9a-f]{32})"',
        response.get_data(as_text=True),
    )
    assert match is not None
    return match.group(1)


def _import(client: FlaskClient[Response], chamber: str = "house") -> None:
    token = _token(_preview(client, chamber=chamber))
    assert client.post(f"/{chamber}/results/confirm/{token}").status_code == 200


def _files(folder: Path) -> dict[Path, bytes]:
    if not folder.exists():
        return {}
    return {
        path: path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()
    }


def _us_elections(db: Database) -> list[str]:
    with db.session() as session:
        return sorted(
            election.name
            for election in session.query(Election).all()
            if election.type in (ElectionType.us_house, ElectionType.us_senate)
        )


def _row(body: str, seat: str, *, special: bool = False) -> str:
    """Return a race row's cells after the seat, whitespace collapsed."""
    label = re.escape(f"{seat} (special)" if special else seat)
    match = re.search(rf"<tr>\s*<td>{label}</td>(.*?)</tr>", body, re.DOTALL)
    assert match is not None, f"no race row for {seat}"
    return re.sub(r"\s+", " ", match.group(1))


def _change_counts(body: str) -> dict[str, int]:
    """Parse the preview's "Against the database" counts."""
    match = re.search(r"Against the database:</strong>(.*?)</p>", body, re.DOTALL)
    assert match is not None
    counts: dict[str, int] = {}
    for item in " ".join(match.group(1).split()).split(", "):
        label, _, count = item.rpartition(" ")
        counts[label] = int(count)
    return counts


def _race(data: dict[str, Any], nyt_id: str) -> dict[str, Any]:
    [race] = [race for race in data["races"] if race["nyt_id"] == nyt_id]
    assert isinstance(race, dict)
    return race


def _state_votes(race: dict[str, Any], candidate: str, votes: int) -> None:
    [unit] = [unit for unit in race["reporting_units"] if unit["level"] == "state"]
    [line] = [line for line in unit["candidates"] if line["nyt_id"] == candidate]
    line["votes"]["total"] = votes


def _without_specials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the clashing special from every conversion (all fixtures have one)."""
    real_convert = cli.convert_cycle

    def convert(*args: Any, **kwargs: Any) -> ConversionResult:
        return dataclasses.replace(real_convert(*args, **kwargs), special=None)

    monkeypatch.setattr(us_results, "convert_cycle", convert)


class TestForm:
    @pytest.mark.parametrize("chamber", ["house", "senate"])
    def test_lists_years_and_sources(self, app: Flask, chamber: str) -> None:
        response = app.test_client().get(f"/{chamber}/results")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        years = re.findall(r'<option value="(\d+)"', select_block(body, "year"))
        assert years == [str(year) for year in sorted(supported_years(), reverse=True)]
        assert 'value="2026" selected' in select_block(body, "year")
        # The office is the URL's: there is no chamber choice to make.
        assert 'name="chamber"' not in body
        assert 'name="source" value="fetch" checked' in body
        assert 'name="source" value="cache"' in body
        assert f'action="/{chamber}/results/preview"' in body
        assert "not cached" in body
        # Only the House has a source that is not yet held (a December runoff).
        assert ("pending (not yet held)" in body) is (chamber == "house")

    def test_shows_cached_retrieval_times(
        self, app: Flask, nyt: FakeNyt, dirs: tuple[Path, Path]
    ) -> None:
        _, cache_dir = dirs
        fetched = fetch_module.fetch(source_for(2026, "house"), cache_dir)

        client = app.test_client()
        body = client.get("/house/results").get_data(as_text=True)

        assert fetched.retrieved_at.isoformat() in body
        assert str(fetched.source_updated_at) in body
        # Only the page's own office lists its sources.
        assert str(fetched.source_updated_at) not in client.get(
            "/senate/results"
        ).get_data(as_text=True)

    def test_shows_why_a_pointer_is_unusable(
        self, app: Flask, nyt: FakeNyt, dirs: tuple[Path, Path]
    ) -> None:
        _, cache_dir = dirs
        house = fetch_module.fetch(source_for(2026, "house"), cache_dir)
        house.cache_path.unlink()
        fetch_module.fetch(source_for(2026, "senate"), cache_dir)
        pointer = cache_dir / "2026" / "senate-general-latest.json"
        data = json.loads(pointer.read_text(encoding="utf-8"))
        data["url"] = "https://static01.nyt.com/elections-assets/old.json"
        pointer.write_text(json.dumps(data), encoding="utf-8")

        client = app.test_client()
        house_page = client.get("/house/results").get_data(as_text=True)
        senate_page = client.get("/senate/results").get_data(as_text=True)

        missing = f"unusable: {cache_dir / '2026' / 'house-general-latest.json'}"
        assert missing in house_page
        assert "which is missing" in house_page
        assert "not the current" not in house_page
        assert "not the current" in senate_page
        assert missing not in senate_page

    @pytest.mark.parametrize("path", ["/house", "/senate"])
    def test_landing_pages_link_to_their_own_tool(
        self, app: Flask, path: str
    ) -> None:
        body = app.test_client().get(path).get_data(as_text=True)

        assert f'href="{path}/results"' in body

    def test_the_president_has_no_results_page(self, app: Flask) -> None:
        client = app.test_client()

        assert client.get("/president/results").status_code == 404
        assert client.post("/president/results/preview").status_code == 404
        assert 'href="/president/results"' not in client.get("/president").get_data(
            as_text=True
        )


class TestPreview:
    def test_shows_races_mapping_diff_and_create(
        self,
        app: Flask,
        db: Database,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
    ) -> None:
        out_dir, cache_dir = dirs

        response = _preview(app.test_client())

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert nyt.requested == [source_for(2026, "house").url]
        # Fetch metadata and the pending runoff that was skipped.
        assert "NYT (fetched now)" in body
        assert str(source_for(2026, "house").url) in body
        assert "skipping pending runoff 2026/house" in body
        # Create, not refresh.
        assert f"house-2026.json</strong> → {_HOUSE}" in body
        assert "creates a new election" in body
        assert "House: no specials file" in body
        # Per-race rows.
        tx = _row(body, "TX-28")
        assert "Ana Garcia (DEM → democrat)" in tx
        assert "Ben Lopez (GOP)" in tx
        assert "9.07%" in tx  # (120000 - 100000) / 220500
        assert "99.5%" in tx
        assert "yes (democrat)" in tx
        assert "<td>new</td>" in tx
        az = _row(body, "AZ-06")
        assert "UNCALLED" in az and "80.5%" in az
        assert "rcv-final" in _row(body, "AK-01")
        assert "<td>yes</td>" in _row(body, "CA-12")  # same party
        assert "nominal" in _row(body, "FL-10")
        assert "<strong>Uncalled (2):</strong>" in body
        assert "Not imported" not in body
        # Party mapping.
        assert re.search(
            r"<td>DEM</td>\s*<td>democrat</td>\s*<td>Democratic</td>", body,
        )
        assert re.search(
            r"<td>OTH</td>\s*<td>others</td>\s*<td>Others</td>\s*"
            r'<td class="num">1</td>', body,
        )
        # Pipeline counts, source note, no diff yet.
        assert re.search(r"races parsed \(runoffs applied\)</th><td>6</td>", body)
        assert re.search(r"passed the file and import checks</th><td>1</td>", body)
        assert "Provisional until certified." in body
        assert "No committed file or database election to compare with." in body
        # Nothing written but the cache snapshot.
        assert _us_elections(db) == []
        assert not out_dir.exists()
        assert "house-general-latest.json" in {path.name for path in _files(cache_dir)}
        cached = PREVIEW_CACHE[_token(response)]
        assert cached["type"] == us_results.PREVIEW_TYPE
        assert (cached["year"], cached["chamber"]) == (2026, "house")

    def test_shows_each_change_against_the_database(
        self, app: Flask, nyt: FakeNyt
    ) -> None:
        client = app.test_client()
        _import(client)

        def edit(data: dict[str, Any]) -> None:
            tx = _race(data, "TX-G-H-28-2026-11-03")
            _state_votes(tx, "lopez-b", 130000)
            tx["outcome"]["won"] = ["lopez-b"]
            az = _race(data, "AZ-G-H-6-2026-11-03")
            az["race_call_status"] = "called"
            az["outcome"]["won"] = [next(iter(az["candidate_metadata"]))]
            ca = _race(data, "CA-G-H-12-2026-11-03")
            ca["race_call_status"] = "too_early"
            ca["outcome"]["won"] = []
            la = _race(data, "LA-L-H-5-2026-11-03")
            la["reporting_units"][0]["eevp"] = 99.5

        nyt.edit_2026_house(edit)

        body = _preview(client).get_data(as_text=True)

        changes = {
            seat: _row(body, seat).rsplit("<td>", 1)[1].removesuffix("</td> ")
            for seat in ("TX-28", "AZ-06", "CA-12", "LA-05", "AK-01", "FL-10")
        }
        assert changes == {
            "TX-28": "winner changed",
            "AZ-06": "newly called",
            "CA-12": "newly uncalled",
            "LA-05": "count updated",
            "AK-01": "unchanged",
            "FL-10": "unchanged",
        }
        assert _change_counts(body) == {
            "winner changed": 1,
            "newly called": 1,
            "newly uncalled": 1,
            "count updated": 1,
            "unchanged": 2,
        }

    def test_house_special_is_listed_as_not_imported(
        self, app: Flask, nyt: FakeNyt
    ) -> None:
        def edit(data: dict[str, Any]) -> None:
            special = json.loads(json.dumps(_race(data, "TX-G-H-28-2026-11-03")))
            special["nyt_id"] = "TX-S-H-28-2026-11-03"
            special["type"] = "Special General"
            data["races"].append(special)

        nyt.edit_2026_house(edit)

        body = _preview(app.test_client()).get_data(as_text=True)

        row = _row(body, "TX-28", special=True)
        assert "<td>not imported (House special)</td>" in row
        assert "<strong>Not imported (1):</strong> TX-28:" in " ".join(body.split())
        assert re.search(r"not imported \(House specials\)</th><td>1</td>", body)
        assert _change_counts(body) == {"new": 6}
        assert "TX-28: House special TX-S-H-28-2026-11-03 dropped" in body

    def test_cache_mode_writes_nothing(
        self,
        app: Flask,
        db: Database,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
    ) -> None:
        out_dir, cache_dir = dirs
        fetch_module.fetch(source_for(2026, "house"), cache_dir)
        nyt.requested.clear()
        before = _files(cache_dir)

        response = _preview(app.test_client(), source="cache")

        assert response.status_code == 200
        assert nyt.requested == []
        assert "<td>cache</td>" in response.get_data(as_text=True)
        assert _files(cache_dir) == before
        assert not out_dir.exists()
        assert _us_elections(db) == []

    def test_senate_preview_shows_specials(
        self, app: Flask, db: Database, nyt: FakeNyt
    ) -> None:
        body = _preview(app.test_client(), chamber="senate").get_data(as_text=True)

        special = "2026 US Senate Special Election"
        assert f"senate-2026-special.json</strong> → {special}" in body
        assert "a child of &#39;2026 US Senate Election&#39;" in body
        assert "<td>main</td>" in _row(body, "Minnesota")
        assert "<td>special</td>" in _row(body, "Minnesota", special=True)
        assert "<td>main</td>" in _row(body, "Ohio", special=True)
        assert re.search(r"<td>DFL</td>\s*<td>democrat</td>", body)
        assert re.search(r"in the specials file</th><td>1</td>", body)
        assert re.search(r"passed the file and import checks</th><td>2</td>", body)
        assert _us_elections(db) == []

    def test_second_senate_preview_with_specials_file_on_disk(
        self, app: Flask, db: Database, nyt: FakeNyt, dirs: tuple[Path, Path]
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        _import(client, "senate")
        assert (out_dir / "senate-2026-special.json").exists()
        special = db.get_election_by_name("2026 US Senate Special Election")
        assert special is not None

        response = _preview(client, chamber="senate")

        body = response.get_data(as_text=True)
        assert flashes_on_page(body) == []
        assert f"in place (id {special.id})" in body
        assert "<td>unchanged</td>" in _row(body, "Minnesota", special=True)

    def test_leftover_db_special_is_noted(
        self,
        app: Flask,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        _import(client, "senate")
        (out_dir / "senate-2026-special.json").unlink()
        _without_specials(monkeypatch)

        body = _preview(client, chamber="senate").get_data(as_text=True)

        assert flashes_on_page(body) == []
        assert "No specials file: every special this cycle is standalone" in body
        assert (
            "The database still holds &#39;2026 US Senate Special Election&#39;, "
            "which no file names; it will not be refreshed."
        ) in body

    def test_stale_specials_file_is_refused(
        self,
        app: Flask,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        _import(client, "senate")
        special_file = out_dir / "senate-2026-special.json"
        before = _files(out_dir)
        _without_specials(monkeypatch)

        response = _preview(client, chamber="senate")

        [message] = flashes_on_page(response.get_data(as_text=True))
        assert message.startswith(f"Preview failed: {special_file} is left over")
        assert "remove it, then re-run" in message
        assert _files(out_dir) == before
        assert PREVIEW_CACHE == {}

    def test_no_cache_shows_error_on_form(
        self, app: Flask, dirs: tuple[Path, Path]
    ) -> None:
        out_dir, cache_dir = dirs

        response = _preview(app.test_client(), source="cache")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        [message] = flashes_on_page(body)
        assert message.startswith(
            "Preview failed: nothing cached for 2026/house/general",
        )
        assert 'action="/house/results/preview"' in body
        assert PREVIEW_CACHE == {}
        assert not out_dir.exists() and not cache_dir.exists()

    def test_adapter_error_shows_on_form(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(fetch_module, "_download", lambda url: b'{"races": "nope"}')

        response = _preview(app.test_client(), chamber="senate")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert flashes_on_page(body) == [
            "Preview failed: file: 'races' should be a list",
        ]
        assert 'action="/senate/results/preview"' in body
        assert PREVIEW_CACHE == {}

    @pytest.mark.parametrize(
        "error", [KeyError("seatInfo"), TypeError("bad"), ValueError("v")],
    )
    def test_conversion_crash_shows_on_form(
        self,
        app: Flask,
        nyt: FakeNyt,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
    ) -> None:
        def crash(*_args: object, **_kwargs: object) -> NoReturn:
            raise error

        monkeypatch.setattr(us_results, "convert_cycle", crash)

        response = _preview(app.test_client())

        assert response.status_code == 200
        assert flashes_on_page(response.get_data(as_text=True)) == [
            f"Preview failed: could not convert 2026 house: {error!r}",
        ]
        assert PREVIEW_CACHE == {}

    def test_bad_committed_file_shows_on_form(
        self, app: Flask, nyt: FakeNyt, dirs: tuple[Path, Path]
    ) -> None:
        out_dir, _ = dirs
        out_dir.mkdir()
        (out_dir / "house-2026.json").write_text("not json", encoding="utf-8")

        response = _preview(app.test_client())

        [message] = flashes_on_page(response.get_data(as_text=True))
        assert message.startswith("Preview failed: ")
        assert "house-2026.json is not valid JSON" in message
        assert PREVIEW_CACHE == {}

    def test_missing_db_parties_show_on_form(
        self, app: Flask, db: Database, nyt: FakeNyt
    ) -> None:
        party = db.get_party_by_name("Others")
        assert party is not None
        with db.session() as session:
            session.delete(session.get(Party, party.id))
            session.commit()

        response = _preview(app.test_client())

        [message] = flashes_on_page(response.get_data(as_text=True))
        assert message == (
            "Preview failed: house-2026.json: Party 'Others' not found — "
            "run import_parties.py first."
        )

    @pytest.mark.parametrize(
        "data",
        [
            {"year": "1999", "source": "fetch"},
            {"year": "2026", "source": "web"},
            {"year": "x", "source": "fetch"},
        ],
    )
    def test_invalid_choice_is_refused(self, app: Flask, data: dict[str, str]) -> None:
        response = app.test_client().post("/house/results/preview", data=data)

        assert response.status_code == 400
        assert flashes_on_page(response.get_data(as_text=True)) == [
            "Choose a year, and whether to fetch or use the cache."
        ]
        assert PREVIEW_CACHE == {}

    @pytest.mark.parametrize(
        ("path_chamber", "posted"),
        [("house", "senate"), ("senate", "house"), ("house", "governor")],
    )
    def test_another_office_in_the_form_is_refused(
        self, app: Flask, nyt: FakeNyt, path_chamber: str, posted: str
    ) -> None:
        response = app.test_client().post(
            f"/{path_chamber}/results/preview",
            data={"year": "2026", "chamber": posted, "source": "fetch"},
        )

        assert response.status_code == 400
        label = path_chamber.capitalize()
        assert flashes_on_page(response.get_data(as_text=True)) == [
            f"This page imports US {label} results only."
        ]
        assert nyt.requested == []
        assert PREVIEW_CACHE == {}

    def test_the_posted_office_may_repeat_the_urls(
        self, app: Flask, nyt: FakeNyt
    ) -> None:
        response = app.test_client().post(
            "/senate/results/preview",
            data={"year": "2026", "chamber": "senate", "source": "fetch"},
        )

        assert response.status_code == 200
        assert "Results Preview" in response.get_data(as_text=True)

    def test_the_preview_is_for_the_urls_office(
        self, app: Flask, nyt: FakeNyt
    ) -> None:
        token = _token(_preview(app.test_client(), chamber="senate"))

        assert PREVIEW_CACHE[token]["chamber"] == "senate"


class TestConfirm:
    def test_imports_writes_files_and_refreshes_in_place(
        self,
        app: Flask,
        db: Database,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        export_runner: RecordingRunner,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        token = _token(_preview(client))

        response = client.post(f"/house/results/confirm/{token}")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        election = db.get_election_by_name(_HOUSE)
        assert election is not None
        assert election.map_id == 24
        assert len(db.get_seat_statuses(election.id)) == 6
        assert flashes_on_page(body) == [
            f"Imported 2026 US House results: {_HOUSE} (id {election.id})"
        ]
        assert sorted(path.name for path in out_dir.iterdir()) == ["house-2026.json"]
        written = json.loads((out_dir / "house-2026.json").read_text(encoding="utf-8"))
        assert written["TX-28"]["seatInfo"]["current"] == "democrat"
        cells = re.sub(r"\s+", " ", body)
        assert (
            f'<td>{_HOUSE}</td> <td class="num">{election.id}</td> '
            '<td class="num">24</td>'
        ) in cells
        assert (
            '<td class="num">6</td> <td class="num">4</td> '
            '<td>democrat 3, republican 1</td> <td class="num">6</td>'
        ) in cells
        assert f"imported {_HOUSE}: 11 votes, election id {election.id}" in body
        assert 'href="/export?sections=house&amp;content=results"' in body
        assert "/results/export" not in body
        assert token not in PREVIEW_CACHE
        assert export_runner.calls == []

        # The same token cannot be confirmed twice.
        repeated = client.post(f"/house/results/confirm/{token}")
        assert flashes(client, repeated) == [_EXPIRED]

        # A second round refreshes the same election and finds nothing changed.
        again = _preview(client)
        again_body = again.get_data(as_text=True)
        refresh = f"refreshes the existing election in place (id {election.id})"
        assert refresh in again_body
        assert "<td>unchanged</td>" in _row(again_body, "TX-28")
        assert client.post(f"/house/results/confirm/{_token(again)}").status_code == 200
        refreshed = db.get_election_by_name(_HOUSE)
        assert refreshed is not None and refreshed.id == election.id
        assert export_runner.calls == []

    def test_senate_imports_the_special_as_a_child(
        self, app: Flask, db: Database, nyt: FakeNyt, dirs: tuple[Path, Path]
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        token = _token(_preview(client, chamber="senate"))

        response = client.post(f"/senate/results/confirm/{token}")

        assert response.status_code == 200
        regular = db.get_election_by_name("2026 US Senate Election")
        special = db.get_election_by_name("2026 US Senate Special Election")
        assert regular is not None and special is not None
        assert special.parent_election_id == regular.id
        assert special.type == ElectionType.us_senate
        assert special.map_id == regular.map_id == 23
        assert len(db.get_seat_statuses(regular.id)) == 2
        special_statuses = list(db.get_seat_statuses(special.id).values())
        assert len(special_statuses) == 1
        assert special_statuses[0].called is True
        assert sorted(path.name for path in out_dir.iterdir()) == [
            "senate-2026-special.json",
            "senate-2026.json",
        ]
        cells = re.sub(r"\s+", " ", response.get_data(as_text=True))
        assert (
            f'<td>{special.name}</td> <td class="num">{special.id}</td> '
            '<td class="num">23</td>'
        ) in cells

    def test_refused_import_writes_nothing_and_keeps_token(
        self, app: Flask, db: Database, nyt: FakeNyt, dirs: tuple[Path, Path]
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        token = _token(_preview(client))
        other_map = db.add_map("Elsewhere", parliament="us_house")
        db.add_election(other_map.id, 2026, _HOUSE, ElectionType.us_house)

        response = client.post(f"/house/results/confirm/{token}")

        assert response.status_code == 302
        assert response.headers["Location"] == "/house/results"
        [message] = flashes(client, response)
        assert message.startswith(
            f"Import failed: house-2026.json: Election '{_HOUSE}'",
        )
        assert f"is on map {other_map.id}, not 24" in message
        assert not out_dir.exists()
        assert token in PREVIEW_CACHE

    def test_database_error_puts_the_files_back(
        self,
        app: Flask,
        db: Database,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        _import(client)
        before = _files(out_dir)
        nyt.edit_2026_house(
            lambda data: _state_votes(
                _race(data, "TX-G-H-28-2026-11-03"), "lopez-b", 110000,
            ),
        )
        token = _token(_preview(client))

        def locked(*_args: object, **_kwargs: object) -> NoReturn:
            raise OperationalError("INSERT", {}, Exception("database is locked"))

        monkeypatch.setattr(us_results, "import_files", locked)

        response = client.post(f"/house/results/confirm/{token}")

        assert response.status_code == 302
        [message] = flashes(client, response)
        assert message.startswith("Import failed: OperationalError: ")
        assert "database is locked" in message
        assert message.endswith(_RESTORED)
        assert _files(out_dir) == before
        assert token in PREVIEW_CACHE

    def test_failed_special_keeps_the_imported_main_file(
        self,
        app: Flask,
        db: Database,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        _import(client, "senate")
        special_before = (out_dir / "senate-2026-special.json").read_bytes()
        main_before = (out_dir / "senate-2026.json").read_bytes()
        url = str(source_for(2026, "senate").url)
        data = json.loads(nyt.bodies[url])
        _state_votes(_race(data, "MN-G-S-2026-11-03"), "lund-q", 1600000)
        _state_votes(_race(data, "MN-S_W-S-2026-11-03"), "voss-t", 1500000)
        nyt.bodies[url] = json.dumps(data).encode()
        token = _token(_preview(client, chamber="senate"))
        real_import = import_senate

        def special_fails(*args: Any, **kwargs: Any) -> int:
            if kwargs.get("parent_name") is not None:
                raise OperationalError("INSERT", {}, Exception("database is locked"))
            return real_import(*args, **kwargs)

        monkeypatch.setattr(cli, "import_senate", special_fails)

        response = client.post(f"/senate/results/confirm/{token}")

        [message] = flashes(client, response)
        assert message.startswith(
            "Import failed: senate-2026.json imported; senate-2026-special.json "
            "import failed: OperationalError: ",
        )
        assert message.endswith(
            "; senate-2026-special.json restored; re-run to retry",
        )
        assert (out_dir / "senate-2026-special.json").read_bytes() == special_before
        main_file = out_dir / "senate-2026.json"
        main_now = json.loads(main_file.read_text(encoding="utf-8"))
        assert (out_dir / "senate-2026.json").read_bytes() != main_before
        assert main_now["Minnesota"]["partyInfo"]["democrat"]["total"] == 1600000
        db_main = cli.snapshot_election(db, "2026 US Senate Election")
        assert db_main is not None
        assert db_main["Minnesota"].totals["democrat"] == 1600000
        db_special = cli.snapshot_election(db, "2026 US Senate Special Election")
        assert db_special is not None
        assert db_special["Minnesota"].totals["republican"] == 1450000
        assert token in PREVIEW_CACHE

    def test_failed_first_import_leaves_no_out_dir(
        self,
        app: Flask,
        db: Database,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        token = _token(_preview(client))

        def refuse(*_args: object, **_kwargs: object) -> NoReturn:
            raise SystemExit("Parent election not found")

        monkeypatch.setattr(us_results, "import_files", refuse)

        response = client.post(f"/house/results/confirm/{token}")

        assert flashes(client, response) == [
            f"Import failed: Parent election not found. {_RESTORED}",
        ]
        assert not out_dir.exists()
        assert token in PREVIEW_CACHE
        assert db.get_election_by_name(_HOUSE) is None

    def test_failed_first_import_removes_every_folder_it_created(
        self,
        app: Flask,
        nyt: FakeNyt,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        nested = tmp_path / "a" / "b" / "out"
        app.config[us_results.OUT_DIR_CONFIG] = nested
        client = app.test_client()
        token = _token(_preview(client))

        def refuse(*_args: object, **_kwargs: object) -> NoReturn:
            raise SystemExit("refused")

        monkeypatch.setattr(us_results, "import_files", refuse)

        response = client.post(f"/house/results/confirm/{token}")

        assert flashes(client, response) == [f"Import failed: refused. {_RESTORED}"]
        assert not (tmp_path / "a").exists()

    @pytest.mark.parametrize("first_import", [True, False])
    def test_restore_failure_names_the_file_and_keeps_it(
        self,
        app: Flask,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
        first_import: bool,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        if not first_import:
            _import(client)
            nyt.edit_2026_house(
                lambda data: _state_votes(
                    _race(data, "TX-G-H-28-2026-11-03"), "lopez-b", 110000,
                ),
            )
        token = _token(_preview(client))

        method = "unlink" if first_import else "write_bytes"
        real_method = getattr(Path, method)
        failing: list[str] = []

        def broken(self: Path, *args: Any, **kwargs: Any) -> Any:
            if self.name in failing:
                raise OSError("disk gone")
            return real_method(self, *args, **kwargs)

        def refuse(*_args: object, **_kwargs: object) -> NoReturn:
            # The write has happened; from here on, putting the file back fails.
            failing.append("house-2026.json")
            raise SystemExit("refused")

        monkeypatch.setattr(Path, method, broken)
        monkeypatch.setattr(us_results, "import_files", refuse)

        response = client.post(f"/house/results/confirm/{token}")

        [message] = flashes(client, response)
        assert message == (
            "Import failed: refused. Putting house-2026.json back failed: "
            "house-2026.json: disk gone"
        )
        assert (out_dir / "house-2026.json").exists()
        assert token in PREVIEW_CACHE

    @pytest.mark.parametrize(
        "retrieved_at",
        ["<missing>", 17, "yesterday", "2099-01-01T00:00:00"],
    )
    def test_unusable_disk_retrieval_time_skips_the_stale_check(
        self,
        app: Flask,
        db: Database,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        clock: Iterator[datetime],
        retrieved_at: object,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        older = _token(_preview(client))
        older_payload = PREVIEW_CACHE[older]
        _import(client)
        PREVIEW_CACHE[older] = older_payload
        path = out_dir / "house-2026.json"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        if retrieved_at == "<missing>":
            del on_disk["meta"]["retrieved_at"]
        else:
            on_disk["meta"]["retrieved_at"] = retrieved_at
        path.write_text(json.dumps(on_disk), encoding="utf-8")

        response = client.post(f"/house/results/confirm/{older}")

        assert response.status_code == 200
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["meta"]["retrieved_at"] == "2026-11-07T12:00:00+00:00"
        assert older not in PREVIEW_CACHE

    def test_double_submit_waiting_on_the_slot_is_refused(
        self,
        app: Flask,
        db: Database,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        token = _token(_preview(client))
        real_slot = model_run_slot

        @contextmanager
        def slot_after_first_submit() -> Iterator[bool]:
            # The first submit imports and pops the token while this one has
            # passed the token check and is waiting for the slot.
            with real_slot() as free:
                PREVIEW_CACHE.pop(token)
                yield free

        monkeypatch.setattr(us_results, "model_run_slot", slot_after_first_submit)

        response = client.post(f"/house/results/confirm/{token}")

        assert flashes(client, response) == [_EXPIRED]
        assert not out_dir.exists()
        assert db.get_election_by_name(_HOUSE) is None

    def test_older_preview_cannot_overwrite_newer_results(
        self,
        app: Flask,
        db: Database,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        clock: Iterator[datetime],
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        older = _token(_preview(client))
        older_payload = PREVIEW_CACHE[older]
        _import(client)
        newer_file = _files(out_dir)
        # Importing the newer preview dropped the older one; bring it back as a
        # preview kept from before would be.
        assert older not in PREVIEW_CACHE
        PREVIEW_CACHE[older] = older_payload

        response = client.post(f"/house/results/confirm/{older}")

        [message] = flashes(client, response)
        assert message == (
            "Import failed: house-2026.json already holds results retrieved "
            "2026-11-07T12:01:00+00:00, newer than this preview's "
            "2026-11-07T12:00:00+00:00; preview again"
        )
        assert _files(out_dir) == newer_file
        assert older in PREVIEW_CACHE

    def test_import_drops_other_previews_of_the_same_cycle(
        self, app: Flask, nyt: FakeNyt
    ) -> None:
        client = app.test_client()
        first = _token(_preview(client))
        senate = _token(_preview(client, chamber="senate"))
        second = _token(_preview(client))

        assert client.post(f"/house/results/confirm/{second}").status_code == 200

        assert first not in PREVIEW_CACHE and second not in PREVIEW_CACHE
        assert senate in PREVIEW_CACHE
        assert flashes(client, client.post(f"/house/results/confirm/{first}")) == [
            _EXPIRED,
        ]

    def test_unknown_token_is_expired(self, app: Flask) -> None:
        client = app.test_client()

        response = client.post("/house/results/confirm/missing")

        assert response.status_code == 302
        assert response.headers["Location"] == "/house/results"
        assert flashes(client, response) == [_EXPIRED]

    @pytest.mark.parametrize(("made", "used"), [("house", "senate"), ("senate", "house")])
    def test_a_token_made_under_the_other_chamber_is_expired(
        self, app: Flask, db: Database, nyt: FakeNyt, dirs: tuple[Path, Path],
        made: str, used: str,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        token = _token(_preview(client, chamber=made))

        response = client.post(f"/{used}/results/confirm/{token}")

        assert response.status_code == 302
        assert response.headers["Location"] == f"/{used}/results"
        assert flashes(client, response) == [_EXPIRED]
        # Untouched: the preview is still good under its own chamber's URL.
        assert token in PREVIEW_CACHE
        assert not out_dir.exists()
        assert _us_elections(db) == []
        assert client.post(f"/{made}/results/confirm/{token}").status_code == 200

    @pytest.mark.parametrize("preview_type", ["by_election", "wikipedia_queue"])
    def test_other_flow_token_is_rejected(self, app: Flask, preview_type: str) -> None:
        token = store_preview({"type": preview_type})
        client = app.test_client()

        response = client.post(f"/house/results/confirm/{token}")

        assert flashes(client, response) == [_EXPIRED]
        assert token in PREVIEW_CACHE

    def test_busy_slot_refuses_import(
        self, app: Flask, db: Database, nyt: FakeNyt
    ) -> None:
        client = app.test_client()
        token = _token(_preview(client))

        with MODEL_RUN_LOCK:
            response = client.post(f"/house/results/confirm/{token}")

        [message] = flashes(client, response)
        assert message.startswith("Results not imported: another US model run")
        assert token in PREVIEW_CACHE
        assert db.get_election_by_name(_HOUSE) is None


class TestCurrentSenate:
    @pytest.fixture()
    def files(self, app: Flask, dirs: tuple[Path, Path]) -> tuple[Path, Path, dict[str, Any]]:
        """The configured snapshot (pre-2026) and the imported 2026 Senate file."""
        out_dir, _ = dirs
        snapshot_path = Path(app.config[us_results.SNAPSHOT_CONFIG])
        snapshot = json.loads(DEFAULT_SNAPSHOT.read_text(encoding="utf-8"))
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(render(snapshot), encoding="utf-8")
        out_dir.mkdir(parents=True, exist_ok=True)
        election_path = out_dir / "senate-2026.json"
        election_path.write_text(json.dumps(senate_election(snapshot)), encoding="utf-8")
        return snapshot_path, election_path, snapshot

    def test_rebuilds_the_snapshot_without_touching_the_db_or_export(
        self,
        app: Flask,
        db: Database,
        files: tuple[Path, Path, dict[str, Any]],
        export_runner: RecordingRunner,
    ) -> None:
        snapshot_path, _, snapshot = files
        before = _us_elections(db)

        response = app.test_client().post("/senate/results/current-senate")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert command_line(body) == "cli.py current-senate (rebuild senate-current.json)"
        assert "<strong>Exit code:</strong> 0" in body
        assert "65 holdovers, 35 winners, 0 uncalled (100 members)" in body
        derived = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert derived != snapshot
        assert sum(len(seat["members"]) for seat in derived["seats"]) == 100
        assert _us_elections(db) == before
        assert export_runner.calls == []

    def test_it_lives_under_the_senate_only(self, app: Flask) -> None:
        client = app.test_client()

        assert client.post("/senate/results/current-senate").status_code != 404
        for path in ("/house/results/current-senate", "/president/results/current-senate"):
            assert client.post(path).status_code == 404
        # A POST-only route: the page that offers it is the Senate results page.
        assert client.get("/senate/results/current-senate").status_code == 405

    def test_only_the_senate_page_offers_it(self, app: Flask) -> None:
        client = app.test_client()

        assert "Rebuild Current Senate" in client.get("/senate/results").get_data(
            as_text=True
        )
        assert "current-senate" not in client.get("/house/results").get_data(
            as_text=True
        )

    def test_running_it_again_changes_nothing(
        self, app: Flask, files: tuple[Path, Path, dict[str, Any]]
    ) -> None:
        snapshot_path, _, _ = files
        client = app.test_client()
        client.post("/senate/results/current-senate")
        first = snapshot_path.read_bytes()

        body = client.post("/senate/results/current-senate").get_data(as_text=True)

        assert snapshot_path.read_bytes() == first
        assert f"unchanged {snapshot_path}" in body

    def test_missing_election_file_is_reported_and_writes_nothing(
        self, app: Flask, files: tuple[Path, Path, dict[str, Any]]
    ) -> None:
        snapshot_path, election_path, snapshot = files
        election_path.unlink()

        body = app.test_client().post("/senate/results/current-senate").get_data(as_text=True)

        assert "<strong>Exit code:</strong> 1" in body
        assert "election file not found" in body
        assert snapshot_path.read_text(encoding="utf-8") == render(snapshot)

    def test_a_refused_build_is_reported_and_writes_nothing(
        self, app: Flask, files: tuple[Path, Path, dict[str, Any]]
    ) -> None:
        snapshot_path, election_path, snapshot = files
        election = json.loads(election_path.read_text(encoding="utf-8"))
        del election["Georgia"]
        election_path.write_text(json.dumps(election), encoding="utf-8")

        body = app.test_client().post("/senate/results/current-senate").get_data(as_text=True)

        assert "<strong>Exit code:</strong> 1" in body
        assert "no class 2 race for: Georgia" in body
        assert snapshot_path.read_text(encoding="utf-8") == render(snapshot)

    def test_busy_slot_refuses_the_rebuild(
        self, app: Flask, files: tuple[Path, Path, dict[str, Any]]
    ) -> None:
        snapshot_path, _, snapshot = files
        client = app.test_client()

        with MODEL_RUN_LOCK:
            response = client.post("/senate/results/current-senate")

        [message] = flashes(client, response)
        assert message.startswith("Current Senate not rebuilt: another US model run")
        assert snapshot_path.read_text(encoding="utf-8") == render(snapshot)

    def test_the_form_offers_it_and_importing_never_runs_it(
        self,
        app: Flask,
        nyt: FakeNyt,
        files: tuple[Path, Path, dict[str, Any]],
    ) -> None:
        snapshot_path, _, snapshot = files
        client = app.test_client()

        form = client.get("/senate/results").get_data(as_text=True)
        _import(client, "senate")

        assert 'action="/senate/results/current-senate"' in form
        assert "Rebuild Current Senate" in form
        assert snapshot_path.read_text(encoding="utf-8") == render(snapshot)


class TestCrossSite:
    def test_posts_from_another_site_are_refused(
        self,
        app: Flask,
        db: Database,
        nyt: FakeNyt,
        dirs: tuple[Path, Path],
        export_runner: RecordingRunner,
    ) -> None:
        out_dir, _ = dirs
        client = app.test_client()
        token = _token(_preview(client))
        nyt.requested.clear()

        preview = client.post(
            "/house/results/preview",
            data={"year": "2026", "source": "fetch"},
            headers=_CROSS_SITE,
        )
        confirm = client.post(f"/house/results/confirm/{token}", headers=_CROSS_SITE)
        senate = client.post("/senate/results/current-senate", headers=_CROSS_SITE)

        statuses = [preview.status_code, confirm.status_code, senate.status_code]
        assert statuses == [403, 403, 403]
        assert nyt.requested == []
        assert token in PREVIEW_CACHE
        assert not out_dir.exists()
        assert db.get_election_by_name(_HOUSE) is None
        assert export_runner.calls == []
