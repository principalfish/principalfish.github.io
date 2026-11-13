"""Tests for the export's ``--sections`` and ``--content`` filters.

A filtered export narrows the full export to some sections and content kinds
while leaving the rest of each shared page (``electionmaps`` is Westminster and
Holyrood, ``uselectionmaps`` is House, Senate and President) exactly as it was.
The tests seed all five sections into a temporary database, export everything
once under ``--output-base``, change the database, and then check that a
filtered rerun changes only what the filter selects.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from console.forms import EXPORT_CONTENT_KINDS
from console.sections import SECTIONS
from core.db import Database
from core.schema import Election, ElectionType, Map, Party, Region, Vote
from export import export_elections
from export.export_elections import (
    CONTENT_KINDS,
    SECTION_PARLIAMENTS,
    build_export_scope,
    exportable_forecast_parliaments,
    scope_map_modes,
)
from export.payload import set_others_party_id
from tests.export_golden import LEGACY_2019, read_files, seed_model_runs
from tests.uk_fixtures import WestminsterWorld
from tests.us_ev_fixtures import seed_canonical_allocations, set_model_target

PAGES = ("electionmaps", "uselectionmaps")
UK_SHELL: dict[str, Any] = {
    "defaultElection": "current-prediction",
    "misc": {"title": "UK"},
    "parliamentFeatures": {},
    "mapModes": {
        "1": {"projection": "mercator", "tag": "westminster"},
        "2": {"projection": "mercator", "tag": "westminster-2019"},
        "3": {"projection": "mercator", "tag": "holyrood"},
    },
    "partyKeyAliases": {},
}
US_SHELL: dict[str, Any] = {
    "defaultElection": "2024-us-president",
    "misc": {"title": "US"},
    "parliamentFeatures": {},
    "mapModes": {
        "4": {"projection": "albers", "tag": "house"},
        "5": {"projection": "albers", "tag": "senate"},
        "6": {"projection": "albers", "tag": "president"},
    },
    "partyKeyAliases": {},
}
HOLYROOD_FORECAST_ENTRY: dict[str, Any] = {
    "id": "current-holyrood-prediction",
    "name": "Current prediction",
    "type": "holyrood_uns",
    "mapId": 3,
    "parliament": "holyrood",
    "model": True,
}


@dataclass(frozen=True)
class World:
    """A seeded database with all five sections, plus where its exports go."""

    db: Database
    base: Path
    legacy: Path
    maps: dict[str, int]
    monkeypatch: pytest.MonkeyPatch

    def run(self, *argv: str, base: Path | None = None) -> str:
        """Run ``main`` under ``--output-base`` and return what it printed."""
        target = self.base if base is None else base
        self.monkeypatch.setattr(
            sys,
            "argv",
            [
                "export_elections.py",
                "--legacy-files-dir",
                str(self.legacy),
                "--output-base",
                str(target),
                *argv,
            ],
        )
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            export_elections.main()
        return printed.getvalue()

    def tree(self, base: Path | None = None) -> dict[str, str]:
        """Every file under the export base, by path relative to it."""
        return read_files(self.base if base is None else base)

    def manifest(self, page: str, base: Path | None = None) -> dict[str, Any]:
        """The parsed ``map-modes.json`` of ``page`` under the export base."""
        path = (self.base if base is None else base) / page / "data" / "map-modes.json"
        parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return parsed


def _add_seat_election(
    db: Database,
    map_id: int,
    seat_name: str,
    election_type: ElectionType,
    year: int,
    name: str,
    party_id: int,
    *,
    electoral_votes: int | None = None,
    seat_id: int | None = None,
) -> int:
    if seat_id is None:
        seat_id = db.add_seat(map_id, seat_name, electoral_votes=electoral_votes).id
    election = db.add_election(map_id, year, name, election_type)
    db.add_vote(election.id, seat_id, party_id=party_id, vote_total=60, elected=True)
    return seat_id


@pytest.fixture()
def world(
    db: Database,
    westminster_world: WestminsterWorld,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    """Westminster, Holyrood, House, Senate and President, ready to export."""
    seed_model_runs(db, westminster_world)
    seed_canonical_allocations(db)
    labour = westminster_world.party_ids["Labour"]
    maps = {"westminster": westminster_world.map_id}
    db.add_map("UK 2019 boundaries", parliament="westminster")
    # A by-election makes the full export build the current-parliament composite.
    by_election = db.add_election(
        maps["westminster"],
        2025,
        "Hexham by-election",
        ElectionType.by_election,
        parent_election_id=westminster_world.baseline_election_id,
    )
    db.add_vote(
        by_election.id,
        next(iter(westminster_world.seat_ids.values())),
        party_id=labour,
        vote_total=70,
        elected=True,
    )
    for parliament in ("holyrood", "us_house", "us_senate", "us_presidential"):
        maps[parliament] = db.add_map(f"{parliament} map", parliament=parliament).id
        db.add_region(maps[parliament], f"{parliament} region")
    assert list(maps.values()) == [1, 3, 4, 5, 6]

    _add_seat_election(
        db,
        maps["holyrood"],
        "Glasgow",
        ElectionType.holyrood_general,
        2021,
        "2021 Scottish Parliament Election",
        labour,
    )
    _add_seat_election(
        db,
        maps["us_house"],
        "AL-01",
        ElectionType.us_house,
        2024,
        "2024 US House",
        labour,
    )
    _add_seat_election(
        db,
        maps["us_senate"],
        "Ohio",
        ElectionType.us_senate,
        2024,
        "2024 US Senate",
        labour,
    )
    seat_id = _add_seat_election(
        db,
        maps["us_presidential"],
        "California",
        ElectionType.us_presidential,
        2024,
        "2024 US Presidential Election",
        labour,
        electoral_votes=54,
    )
    _add_seat_election(
        db,
        maps["us_presidential"],
        "California",
        ElectionType.us_presidential_model,
        2028,
        "2028 US President run",
        labour,
        seat_id=seat_id,
    )
    with db.session() as session:
        model = session.execute(
            select(Election).where(Election.type == ElectionType.us_presidential_model)
        ).scalar_one()
        model_id = model.id
    set_model_target(db, model_id)

    repo = tmp_path / "repo"
    for page, shell in zip(PAGES, (UK_SHELL, US_SHELL)):
        root = repo / page / "data"
        (root / "maps").mkdir(parents=True)
        (root / "results").mkdir()
        (root / "map-modes-shell.json").write_text(json.dumps(shell), encoding="utf-8")
    uk_maps = repo / "electionmaps" / "data" / "maps"
    (uk_maps / "map-3.topo.json").write_text("{}", encoding="utf-8")
    us_root = repo / "uselectionmaps" / "data"
    for map_id in (4, 5, 6):
        (us_root / "maps" / f"map-{map_id}.topo.json").write_text(
            "{}", encoding="utf-8"
        )
    (us_root / "results" / "senate-current.json").write_text(
        '{"schema":"pf-results-v4","seats":[]}', encoding="utf-8"
    )

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "650map_new.json").write_text('{"type":"Topology"}', encoding="utf-8")
    (legacy / "2019election_new.json").write_text(
        json.dumps(LEGACY_2019), encoding="utf-8"
    )

    monkeypatch.setattr(export_elections, "Database", lambda: db)
    monkeypatch.setattr(
        export_elections,
        "PAGE_OUTPUT_ROOTS",
        {page: repo / page / "data" for page in PAGES},
    )
    return World(db, tmp_path / "base", legacy, maps, monkeypatch)


def _change_database(db: Database) -> None:
    """Change every export input: votes, notes, party colours and region names."""
    with db.session() as session:
        for vote in session.execute(select(Vote)).scalars():
            vote.vote_total = (vote.vote_total or 0) + 7
        for election in session.execute(select(Election)).scalars():
            election.source_note = "changed"
        for party in session.execute(select(Party)).scalars():
            party.colour = "#123456"
        for region in session.execute(select(Region)).scalars():
            region.name = f"{region.name} (renamed)"


def _owners(world: World) -> dict[str, tuple[str, str]]:
    """Map each exported file (relative to the base) to its (parliament, kind)."""
    parliament_of_map = {str(map_id): name for name, map_id in world.maps.items()}
    owners: dict[str, tuple[str, str]] = {}
    for page in PAGES:
        manifest = world.manifest(page)
        by_id = manifest["files"]["elections"]["electionsById"]
        for entry in manifest["elections"]:
            kind = "forecast" if entry.get("model") else "results"
            owners[f"{page}/data/{by_id[entry['id']]}"] = (entry["parliament"], kind)
        for map_id, relpath in manifest["files"]["elections"]["mapsById"].items():
            if map_id in parliament_of_map:
                owner = (parliament_of_map[map_id], "results")
                owners[f"{page}/data/{relpath}"] = owner
    owners["uselectionmaps/data/results/us-president-trends-by-state.json"] = (
        "us_presidential",
        "results",
    )
    return owners


def _changed(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    }


def _data_changes(before: dict[str, str], after: dict[str, str]) -> set[str]:
    """The changed files other than the manifests."""
    return {
        path for path in _changed(before, after) if not path.endswith("map-modes.json")
    }


def _entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in manifest["elections"]}


class TestEverythingEqualsNoFlags:
    """Selecting every section and every kind writes exactly the unfiltered export."""

    def test_full_scope_matches_no_flags_byte_for_byte(self, world: World) -> None:
        plain = world.base.parent / "plain"
        everything = world.base.parent / "everything"
        world.run(base=plain)
        out = world.run(
            "--sections",
            "westminster,holyrood,house,senate,president",
            "--content",
            "results,forecast,metadata",
            base=everything,
        )
        assert out.splitlines()[0] == (
            "Export scope: sections=westminster,holyrood,house,senate,president "
            "content=results,forecast,metadata"
        )
        assert world.tree(plain) == world.tree(everything)
        assert len(world.tree(plain)) > 10

    def test_either_flag_alone_with_everything_matches_too(self, world: World) -> None:
        plain = world.base.parent / "plain"
        world.run(base=plain)
        for flag, value in (
            ("--sections", "house,senate,president,westminster,holyrood"),
            ("--content", "metadata,forecast,results"),
        ):
            other = world.base.parent / flag.strip("-")
            world.run(flag, value, base=other)
            assert world.tree(plain) == world.tree(other)


class TestOneSectionLeavesTheRestAlone:
    """A section export rewrites that section's files and nothing else."""

    @pytest.mark.parametrize(
        "slug", ["westminster", "holyrood", "house", "senate", "president"]
    )
    def test_only_the_selected_sections_files_change(
        self, world: World, slug: str
    ) -> None:
        world.run()
        owners = _owners(world)
        before = world.tree()
        before_manifests = {page: world.manifest(page) for page in PAGES}
        _change_database(world.db)

        world.run("--sections", slug)

        parliament = SECTION_PARLIAMENTS[slug]
        after = world.tree()
        changed = _changed(before, after)
        data_changes = _data_changes(before, after)
        assert data_changes, "the section's own files were not rewritten"
        for path in data_changes:
            assert owners[path][0] == parliament, f"{path} belongs to {owners[path]}"

        for page in PAGES:
            old, new = before_manifests[page], world.manifest(page)
            if parliament not in export_elections.PAGE_PARLIAMENTS[page]:
                assert f"{page}/data/map-modes.json" not in changed
                continue
            old_entries, new_entries = _entries(old), _entries(new)
            assert list(old_entries) == list(new_entries)
            for entry_id, entry in old_entries.items():
                if entry["parliament"] == parliament:
                    continue
                assert new_entries[entry_id] == entry
                assert (
                    new["files"]["elections"]["electionsById"][entry_id]
                    == old["files"]["elections"]["electionsById"][entry_id]
                )
            assert any(
                new_entries[i].get("sourceNote") == "changed"
                for i, e in old_entries.items()
                if e["parliament"] == parliament
            )
            for map_id, mode in old["mapModes"].items():
                if map_id != str(world.maps[parliament]):
                    assert new["mapModes"][map_id] == mode
            assert (
                new["files"]["elections"]["mapsById"]
                == (old["files"]["elections"]["mapsById"])
            )
            # A section alone means all its content, so metadata is refreshed: the
            # page's parties, and the regions of the section's own map.
            assert {p["colour"] for p in new["parties"]} == {"#123456"}
            own = str(world.maps[parliament])
            assert new["mapModes"][own]["regions"] != old["mapModes"][own]["regions"]

    def test_unchanged_database_leaves_the_manifest_bytes_alone(
        self, world: World
    ) -> None:
        world.run()
        before = world.tree()
        world.run("--sections", "house,senate")
        assert world.tree() == before

    def test_page_without_selected_sections_is_not_touched_or_seeded(
        self, world: World
    ) -> None:
        out = world.run("--sections", "president")
        assert not (world.base / "electionmaps").exists()
        assert "/electionmaps/" not in out
        manifest = world.manifest("uselectionmaps")
        assert [e["parliament"] for e in manifest["elections"]] == [
            "us_presidential",
            "us_presidential",
        ]

    def test_unselected_page_manifest_is_not_rewritten(self, world: World) -> None:
        world.run()
        manifest_path = world.base / "electionmaps" / "data" / "map-modes.json"
        stamp = manifest_path.stat().st_mtime_ns
        _change_database(world.db)
        world.run("--sections", "house")
        assert manifest_path.stat().st_mtime_ns == stamp

    def test_export_pages_skips_a_page_the_scope_has_nothing_on(
        self, world: World
    ) -> None:
        # ``main`` filters before calling ``export_pages``, so the page guard inside
        # it is reached only by calling it directly with every page root.
        world.run()
        roots = {page: world.base / page / "data" for page in PAGES}
        before = world.tree()
        _change_database(world.db)
        scope = build_export_scope(["president"], None)
        self._export_pages(world, roots, scope)
        changed = _changed(before, world.tree())
        assert changed
        assert all(path.startswith("uselectionmaps/") for path in changed)

        fresh = {page: world.base.parent / "fresh" / page / "data" for page in PAGES}
        (fresh["uselectionmaps"] / "maps").mkdir(parents=True)
        (fresh["uselectionmaps"] / "maps" / "map-6.topo.json").write_text(
            "{}", encoding="utf-8"
        )
        self._export_pages(world, fresh, scope)
        assert not fresh["electionmaps"].parent.exists()
        assert fresh["uselectionmaps"].exists()

    def test_refresh_manifest_metadata_skips_a_page_the_scope_has_nothing_on(
        self, world: World
    ) -> None:
        # Likewise reached only by a direct call: ``main`` passes just the pages
        # the scope touches.
        world.run()
        roots = {page: world.base / page / "data" for page in PAGES}
        before = world.tree()
        _change_database(world.db)
        export_elections.refresh_manifest_metadata(
            roots, dry_run=False, scope=build_export_scope(["president"], ["metadata"])
        )
        changed = _changed(before, world.tree())
        assert changed == {"uselectionmaps/data/map-modes.json"}

    @staticmethod
    def _export_pages(
        world: World, page_roots: dict[str, Path], scope: Any
    ) -> None:
        """Call ``export_pages`` directly, as ``main`` does, for ``scope``."""
        others = world.db.get_party_by_name("Others")
        assert others is not None
        set_others_party_id(others.id)
        monkeypatch = world.monkeypatch
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "export_elections.py",
                "--legacy-files-dir",
                str(world.legacy),
            ],
        )
        args = export_elections.parse_args()
        with world.db.session() as session:
            elections = [
                election
                for election in session.execute(
                    select(Election).options(joinedload(Election.map))
                ).scalars()
                if scope.covers_election(election)
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                export_elections.export_pages(
                    session=session,
                    elections=elections,
                    page_roots=page_roots,
                    args=args,
                    manifest_parties=[],
                    manifest_regions_by_map_id={},
                    has_electorate=True,
                    single_election_mode=False,
                    scope=scope,
                )

    def test_page_without_a_manifest_is_written_with_just_the_selection(
        self, world: World
    ) -> None:
        world.run("--sections", "senate")
        manifest = world.manifest("uselectionmaps")
        assert list(_entries(manifest)) == ["2024-us-senate", "current-senate"]
        assert sorted(manifest["mapModes"]) == ["5"]
        assert manifest["parties"]
        results = world.base / "uselectionmaps" / "data" / "results"
        assert not (results / "us-house-2024.json").exists()

    def test_selected_sections_stale_map_is_removed_others_are_kept(
        self, world: World
    ) -> None:
        world.run()
        maps = world.base / "uselectionmaps" / "data" / "maps"
        (maps / "map-4.topo.json").unlink()
        stale = maps / "map-99.topo.json"
        stale.write_text("{}", encoding="utf-8")
        orphan_house = maps / "map-40.topo.json"
        orphan_house.write_text("{}", encoding="utf-8")
        (maps / "map-4.topo.json").write_text("{}", encoding="utf-8")
        with world.db.session() as session:
            session.add(Map(id=40, name="old house map", parliament="us_house"))
        out = world.run("--sections", "president")
        # Neither file belongs to the president, so both stay.
        assert stale.exists() and orphan_house.exists()
        assert "stale map" not in out
        out = world.run("--sections", "house")
        assert not orphan_house.exists()
        assert stale.exists()
        assert "Removed stale map" in out


class TestContentKinds:
    """Each content kind rewrites its own files and manifest parts only."""

    @staticmethod
    def _run_kinds(
        world: World, kinds: str
    ) -> tuple[dict[str, str], dict[str, str], dict[str, tuple[str, str]]]:
        world.run()
        before = world.tree()
        owners = _owners(world)
        _change_database(world.db)
        world.run("--content", kinds)
        return before, world.tree(), owners

    def test_results_only(self, world: World) -> None:
        before, after, owners = self._run_kinds(world, "results")
        changed = _data_changes(before, after)
        assert changed
        assert {owners[p][1] for p in changed} == {"results"}
        new = world.manifest("electionmaps")
        # The forecast entries and files are as they were, and so are the regions;
        # the page-wide parties are refreshed with any manifest write.
        old_manifest = json.loads(before["electionmaps/data/map-modes.json"])
        assert {p["colour"] for p in new["parties"]} == {"#123456"}
        assert new["mapModes"] == old_manifest["mapModes"]
        forecast = _entries(new)["current-prediction"]
        assert (
            forecast
            == _entries(json.loads(before["electionmaps/data/map-modes.json"]))[
                "current-prediction"
            ]
        )
        results = _entries(new)["2024-general"]
        assert results["sourceNote"] == "changed"
        us = world.manifest("uselectionmaps")
        assert "sourceNote" not in _entries(us)["current-us-president"]
        assert _entries(us)["2024-us-president"]["sourceNote"] == "changed"
        assert not any(
            "(renamed)" in region["name"]
            for mode in us["mapModes"].values()
            for region in mode["regions"]
        )

    def test_forecast_only(self, world: World) -> None:
        before, after, owners = self._run_kinds(world, "forecast")
        changed = _data_changes(before, after)
        assert changed == {
            "electionmaps/data/results/prediction-simulation.json",
            "uselectionmaps/data/results/us-president-forecast.json",
        }
        uk = world.manifest("electionmaps")
        old_uk = json.loads(before["electionmaps/data/map-modes.json"])
        assert _entries(uk)["current-prediction"]["sourceNote"] == "changed"
        assert "sourceNote" not in _entries(uk)["2024-general"]
        assert list(_entries(uk)) == list(_entries(old_uk))
        assert {p["colour"] for p in uk["parties"]} == {"#123456"}
        assert uk["mapModes"] == old_uk["mapModes"]
        assert not any(owners[p][1] == "results" for p in changed)

    def test_metadata_only_rewrites_parties_and_regions(self, world: World) -> None:
        before, after, _ = self._run_kinds(world, "metadata")
        assert set(_changed(before, after)) == {
            f"{page}/data/map-modes.json" for page in PAGES
        }
        for page in PAGES:
            old = json.loads(before[f"{page}/data/map-modes.json"])
            new = world.manifest(page)
            assert new["elections"] == old["elections"]
            assert new["files"] == old["files"]
            assert {p["colour"] for p in new["parties"]} == {"#123456"}
            assert any(
                "(renamed)" in region["name"]
                for mode in new["mapModes"].values()
                for region in mode["regions"]
            )

    @pytest.mark.parametrize(
        ("kinds", "results", "forecast", "metadata"),
        [
            ("results,forecast", True, True, False),
            ("results,metadata", True, False, True),
            ("forecast,metadata", False, True, True),
        ],
    )
    def test_combinations(
        self, world: World, kinds: str, results: bool, forecast: bool, metadata: bool
    ) -> None:
        before, after, owners = self._run_kinds(world, kinds)
        changed = _data_changes(before, after)
        wanted = {
            kind
            for kind, selected in (("results", results), ("forecast", forecast))
            if selected
        }
        assert {owners[p][1] for p in changed} == wanted
        uk = world.manifest("electionmaps")
        old_uk = json.loads(before["electionmaps/data/map-modes.json"])
        assert {p["colour"] for p in uk["parties"]} == {"#123456"}
        assert (_entries(uk)["2024-general"].get("sourceNote") == "changed") is results
        assert (
            _entries(uk)["current-prediction"].get("sourceNote") == "changed"
        ) is forecast
        regions = uk["mapModes"]["1"]["regions"]
        assert (regions != old_uk["mapModes"]["1"]["regions"]) is metadata


class TestPartiesFollowEveryWrite:
    """A new party reaches the manifest even from a results-only run."""

    @pytest.mark.parametrize("kinds", ["results", "forecast"])
    def test_new_party_is_listed_without_touching_the_rest(
        self, world: World, kinds: str
    ) -> None:
        world.run()
        before = {page: world.manifest(page) for page in PAGES}
        world.db.add_party("Brand New Party")

        world.run("--content", kinds, "--sections", "president,westminster")

        for page in PAGES:
            after = world.manifest(page)
            assert "Brand New Party" in {p["name"] for p in after["parties"]}
            assert after["elections"] == before[page]["elections"]
            assert after["mapModes"] == before[page]["mapModes"]
            assert after["files"] == before[page]["files"]


class TestMetadataForOneSection:
    """Metadata for one section refreshes its page's parties, only its own regions."""

    def test_regions_change_only_for_the_selected_sections_maps(
        self, world: World
    ) -> None:
        world.run()
        before = {page: world.manifest(page) for page in PAGES}
        us_bytes = world.tree()["uselectionmaps/data/map-modes.json"]
        _change_database(world.db)

        out = world.run("--content", "metadata", "--sections", "holyrood")

        assert out.splitlines() == [
            "Export scope: sections=holyrood content=metadata",
            f"Wrote manifest metadata: {world.base}/electionmaps/data/map-modes.json",
        ]
        uk = world.manifest("electionmaps")
        old_modes = before["electionmaps"]["mapModes"]
        assert uk["mapModes"]["3"]["regions"] != old_modes["3"]["regions"]
        assert uk["mapModes"]["1"] == old_modes["1"]
        assert {p["colour"] for p in uk["parties"]} == {"#123456"}
        assert uk["elections"] == before["electionmaps"]["elections"]
        assert world.tree()["uselectionmaps/data/map-modes.json"] == us_bytes

    def test_missing_manifest_is_an_error(self, world: World) -> None:
        with pytest.raises(FileNotFoundError):
            world.run("--content", "metadata", "--sections", "house")

    def test_metadata_with_results_for_one_section(self, world: World) -> None:
        world.run()
        before = world.manifest("uselectionmaps")
        _change_database(world.db)
        world.run("--content", "results,metadata", "--sections", "house")
        after = world.manifest("uselectionmaps")
        assert after["mapModes"]["4"]["regions"] != before["mapModes"]["4"]["regions"]
        assert after["mapModes"]["5"] == before["mapModes"]["5"]
        assert after["mapModes"]["6"] == before["mapModes"]["6"]
        assert {p["colour"] for p in after["parties"]} == {"#123456"}


class TestHolyroodForecast:
    """The export keeps Holyrood's forecast entry but never writes its files."""

    @staticmethod
    def _install_holyrood_forecast(world: World) -> None:
        """What the Holyrood runner and a one-off manifest edit leave on disk."""
        root = world.base / "electionmaps" / "data"
        (root / "results" / "holyrood-prediction.json").write_text(
            '{"schema":"pf-results-v4","seats":[]}', encoding="utf-8"
        )
        manifest = world.manifest("electionmaps")
        manifest["elections"].insert(1, dict(HOLYROOD_FORECAST_ENTRY))
        by_id = manifest["files"]["elections"]["electionsById"]
        by_id[HOLYROOD_FORECAST_ENTRY["id"]] = "results/holyrood-prediction.json"
        (root / "map-modes.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_full_export_preserves_the_entry(self, world: World) -> None:
        world.run()
        self._install_holyrood_forecast(world)
        world.run()
        manifest = world.manifest("electionmaps")
        entry = _entries(manifest)["current-holyrood-prediction"]
        assert entry["type"] == "holyrood_uns"
        assert manifest["files"]["elections"]["electionsById"][entry["id"]] == (
            "results/holyrood-prediction.json"
        )

    def test_forecast_for_holyrood_alone_says_so_and_writes_nothing(
        self, world: World
    ) -> None:
        world.run()
        self._install_holyrood_forecast(world)
        before = world.tree()
        out = world.run("--content", "forecast", "--sections", "holyrood")
        assert out.splitlines()[0] == "Export scope: sections=holyrood content=forecast"
        assert "Holyrood forecast is written by the Holyrood model runner" in out
        assert "Nothing to export for this selection." in out
        assert world.tree() == before

    def test_holyrood_results_keep_the_forecast_entry_verbatim(
        self, world: World
    ) -> None:
        world.run()
        self._install_holyrood_forecast(world)
        before = world.manifest("electionmaps")
        _change_database(world.db)
        out = world.run("--sections", "holyrood")
        assert "Holyrood forecast is written by" in out
        after = world.manifest("electionmaps")
        assert _entries(after)["current-holyrood-prediction"] == HOLYROOD_FORECAST_ENTRY
        assert list(_entries(after)) == list(_entries(before))

    def test_other_sections_keep_the_forecast_entry_verbatim(
        self, world: World
    ) -> None:
        world.run()
        self._install_holyrood_forecast(world)
        before = world.manifest("electionmaps")
        _change_database(world.db)
        world.run("--sections", "westminster")
        after = world.manifest("electionmaps")
        assert _entries(after)["current-holyrood-prediction"] == HOLYROOD_FORECAST_ENTRY
        assert list(_entries(after)) == list(_entries(before))
        assert (
            after["files"]["elections"]["electionsById"]["current-holyrood-prediction"]
            == "results/holyrood-prediction.json"
        )

    def test_forecast_for_all_sections_exports_the_exportable_ones(
        self, world: World
    ) -> None:
        out = world.run("--content", "forecast")
        assert "Holyrood forecast" in out
        assert "house forecast export is switched off" in out
        assert "senate forecast export is switched off" in out
        manifest = world.manifest("electionmaps")
        assert list(_entries(manifest)) == ["current-prediction"]


class TestFilteredMatchesFull:
    """A filtered export builds what a full export does for the same inputs."""

    def test_forecast_compares_with_the_general_election_not_the_composite(
        self, world: World
    ) -> None:
        world.run()
        entries = _entries(world.manifest("electionmaps"))
        # The composite sorts ahead of the general election it is built from.
        order = list(entries)
        assert order.index("current-parliament") < order.index("2024-general")
        reference = world.base.parent / "reference"
        shutil.copytree(world.base, reference)

        world.run("--sections", "westminster", "--content", "forecast")
        world.run(base=reference)

        filtered = world.manifest("electionmaps")
        full = world.manifest("electionmaps", reference)
        assert _entries(filtered)["current-prediction"]["comparisonElectionId"] == (
            "2024-general"
        )
        assert filtered["elections"] == full["elections"]
        assert filtered == full

    def test_a_selected_sections_vanished_map_goes_as_in_a_full_export(
        self, world: World
    ) -> None:
        world.run()
        with world.db.session() as session:
            house = session.execute(
                select(Election).where(Election.type == ElectionType.us_house)
            ).scalar_one()
            for vote in session.execute(
                select(Vote).where(Vote.election_id == house.id)
            ).scalars():
                session.delete(vote)
            session.delete(house)
        maps = world.base / "uselectionmaps" / "data" / "maps"
        assert (maps / "map-4.topo.json").exists()

        out = world.run("--sections", "house")

        by_map = world.manifest("uselectionmaps")["files"]["elections"]["mapsById"]
        assert "4" not in by_map
        assert not (maps / "map-4.topo.json").exists()
        assert "Removed stale map" in out
        assert {"5", "6"} <= set(by_map)
        assert (maps / "map-5.topo.json").exists()
        assert (maps / "map-6.topo.json").exists()


class TestDryRun:
    """A filtered dry run writes nothing and lists only what it would write."""

    def test_writes_nothing_and_lists_in_scope_files(self, world: World) -> None:
        world.run()
        _change_database(world.db)
        before = world.tree()
        out = world.run(
            "--sections", "house,senate", "--content", "results", "--dry-run"
        )
        assert world.tree() == before
        lines = out.splitlines()
        assert lines[0] == "Export scope: sections=house,senate content=results"
        would = [line for line in lines if line.startswith("Would write")]
        assert all("/uselectionmaps/" in line for line in would)
        assert not any("us-president" in line for line in would)
        assert any("us-house-2024.json" in line for line in would)
        assert any("us-senate-2024.json" in line for line in would)
        assert any(line.startswith("Would write manifest:") for line in would)

    def test_metadata_dry_run_leaves_the_tree_and_reports_the_scope_maps(
        self, world: World
    ) -> None:
        world.run()
        _change_database(world.db)
        before = world.tree()
        out = world.run("--content", "metadata", "--sections", "house", "--dry-run")
        assert world.tree() == before
        assert out.splitlines() == [
            "Export scope: sections=house content=metadata",
            "Would write manifest metadata: "
            f"{world.base}/uselectionmaps/data/map-modes.json",
            f"parties={len(world.manifest('uselectionmaps')['parties'])} maps=1",
        ]

    def test_dry_run_does_not_seed_unselected_pages(self, world: World) -> None:
        world.run("--sections", "westminster", "--dry-run")
        assert not (world.base / "uselectionmaps").exists()


class TestParseErrors:
    """Bad values and bad combinations are refused before anything runs."""

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (["--sections", "mars"], "--sections: unknown mars"),
            (["--content", "results,vibes"], "--content: unknown vibes"),
            (["--sections", ""], "--sections selected nothing"),
            (["--content", " , "], "--content selected nothing"),
            (["--sections", "house", "--election-name", "x"], "cannot be combined"),
            (["--content", "results", "--current-simulation"], "cannot be combined"),
            (["--sections", "house", "--metadata-only"], "cannot be combined"),
            (
                [
                    "--content",
                    "results",
                    "--election-name",
                    "x",
                    "--output-file",
                    "o.json",
                ],
                "cannot be combined",
            ),
            (["--sections", "house", "--output-file", "o.json"], "cannot be combined"),
        ],
    )
    def test_refused(
        self,
        world: World,
        capsys: pytest.CaptureFixture[str],
        argv: list[str],
        message: str,
    ) -> None:
        with pytest.raises(SystemExit):
            world.run(*argv)
        assert message in capsys.readouterr().err
        assert not world.base.exists()

    def test_repeated_and_unordered_values_are_accepted(self, world: World) -> None:
        out = world.run(
            "--sections", "senate,house,house", "--content", "results", "--dry-run"
        )
        assert out.splitlines()[0] == (
            "Export scope: sections=house,senate content=results"
        )


class TestScopeUnits:
    """The scope object and the map-mode merge, without a database."""

    def test_section_slugs_match_the_console_registry(self) -> None:
        assert SECTION_PARLIAMENTS == {s.slug: s.parliament for s in SECTIONS}

    def test_content_kinds_match_the_console_form(self) -> None:
        assert EXPORT_CONTENT_KINDS == CONTENT_KINDS

    @pytest.mark.parametrize(
        ("sections", "content", "message"),
        [
            (["mars"], None, "unknown section"),
            (None, ["vibes"], "unknown content"),
            ([], None, "selection is empty"),
            (None, [], "selection is empty"),
        ],
    )
    def test_bad_selections_are_refused(
        self,
        sections: list[str] | None,
        content: list[str] | None,
        message: str,
    ) -> None:
        with pytest.raises(ValueError, match=message):
            build_export_scope(sections, content)

    def test_forecast_parliaments_follow_the_chamber_switch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert exportable_forecast_parliaments() == {"westminster", "us_presidential"}
        monkeypatch.setattr(export_elections, "EXPORT_US_CHAMBER_FORECASTS", True)
        assert exportable_forecast_parliaments() == {
            "westminster",
            "us_presidential",
            "us_house",
            "us_senate",
        }

    def test_everything_is_recognised(self) -> None:
        assert build_export_scope(None, None).is_everything
        assert not build_export_scope(["house"], None).is_everything
        assert not build_export_scope(None, ["results"]).is_everything

    def test_scope_map_modes_keeps_out_of_scope_modes_and_metadata(self) -> None:
        rebuilt = {
            "1": {
                "tag": "new",
                "regions": [{"id": 1, "name": "fresh"}],
                "senateClassNextElection": {},
            },
            "2": {"tag": "new2", "regions": [{"id": 2, "name": "fresh2"}]},
            "3": {"tag": "added", "regions": []},
        }
        existing = {
            "1": {"tag": "old", "regions": [{"id": 1, "name": "stale"}]},
            "2": {"tag": "old2", "regions": [{"id": 2, "name": "stale2"}]},
            "9": {"tag": "unlisted"},
        }
        kept = scope_map_modes(rebuilt, existing, {"1", "3"}, refresh_metadata=False)
        assert kept == {
            "1": {"tag": "new", "regions": [{"id": 1, "name": "stale"}]},
            "2": existing["2"],
            "3": rebuilt["3"],
            "9": existing["9"],
        }
        refreshed = scope_map_modes(
            rebuilt, existing, {"1", "3"}, refresh_metadata=True
        )
        assert refreshed["1"] == rebuilt["1"]
        assert refreshed["2"] == existing["2"]
