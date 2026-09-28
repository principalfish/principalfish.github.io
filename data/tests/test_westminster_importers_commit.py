"""Shared commit tests for the eleven Westminster pollster importers.

Every module in ``polls/importers/westminster/*_import.py`` carries its own copy
of ``commit_import_plan`` and ``_find_existing_poll``. The copies of
``_find_existing_poll`` are identical; ``commit_import_plan`` comes in two
variants:

- **Variant A** (``VARIANT_A``) reuses an existing pollster as it is.
- **Variant B** (``VARIANT_B``) also rewrites the existing pollster's
  ``regions_mapping`` when the plan carries a different one.

Each test is parametrised over the modules by name, so a refactor that merges
the copies has to keep (or deliberately drop) the split these tests pin.

The plans are built directly from each module's own ``ImportPlan`` /
``ParsedPoll`` / ``PlannedPollRow`` classes, whose field names differ slightly
per module; nothing here parses a source document or touches the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from types import MappingProxyType, ModuleType

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from db import Database
from models import Poll, Pollster
from polls.importers.types import PollImportResult
from polls.importers.westminster import (
    bmg_research_import,
    deltapoll_import,
    find_out_now_import,
    focaldata_import,
    ipsos_import,
    lord_ashcroft_import,
    more_in_common_import,
    opinium_import,
    survation_import,
    techne_import,
    yougov_import,
)
from tests.uk_fixtures import (
    WestminsterWorld,
    add_poll_with_rows,
    seed_westminster_world,
)

# ── The importers and their commit variants ───────────────────────────────────

ALL_IMPORTERS: tuple[ModuleType, ...] = (
    bmg_research_import,
    deltapoll_import,
    find_out_now_import,
    focaldata_import,
    ipsos_import,
    lord_ashcroft_import,
    more_in_common_import,
    opinium_import,
    survation_import,
    techne_import,
    yougov_import,
)

# Reuse an existing pollster as it is.
VARIANT_A: tuple[ModuleType, ...] = (
    bmg_research_import,
    deltapoll_import,
    focaldata_import,
    ipsos_import,
    lord_ashcroft_import,
    survation_import,
    techne_import,
)

# Also rewrite an existing pollster's regions_mapping when the plan's differs.
VARIANT_B: tuple[ModuleType, ...] = (
    find_out_now_import,
    more_in_common_import,
    opinium_import,
    yougov_import,
)


def _short_name(importer: ModuleType) -> str:
    """Return ``"opinium"`` for ``polls.importers.westminster.opinium_import``."""
    return importer.__name__.rsplit(".", 1)[-1].removesuffix("_import")


def _over(importers: Sequence[ModuleType]) -> pytest.MarkDecorator:
    """Parametrise ``importer`` over ``importers``, ids by short module name."""
    return pytest.mark.parametrize("importer", importers, ids=_short_name)


# ── Per-module model kwargs ───────────────────────────────────────────────────

# The ParsedPoll fields every module shares.
_PARSED_COMMON_FIELDS = frozenset({"sample_size", "fieldwork_start", "fieldwork_end"})

# Each module's remaining ParsedPoll fields. ``commit_import_plan`` never reads
# them, so a single Labour figure is enough to satisfy the model.
_REGIONAL = {"__national__": {"Labour": 31.5}}
_MACRO = {"GB": {"Labour": 31.5}}
_NATIONAL = {"Labour": 31.5}
_PARSED_PERCENTAGES: Mapping[str, Mapping[str, object]] = MappingProxyType(
    {
        "bmg_research": {"party_region_percentages": _REGIONAL},
        "deltapoll": {"party_region_percentages": _REGIONAL},
        "find_out_now": {"party_region_percentages": _REGIONAL},
        "focaldata": {"party_macro_percentages": _MACRO},
        "ipsos": {
            "party_percentages": _NATIONAL,
            "party_region_percentages": _REGIONAL,
        },
        "lord_ashcroft": {
            "party_percentages": _NATIONAL,
            "party_region_percentages": _REGIONAL,
        },
        "more_in_common": {"party_region_percentages": _REGIONAL},
        "opinium": {"party_macro_percentages": _MACRO},
        "survation": {"party_region_percentages": _REGIONAL},
        "techne": {"party_percentages": _NATIONAL},
        "yougov": {"party_macro_percentages": _MACRO},
    }
)

# PlannedPollRow fields. YouGov's rows also carry the source macro region (and
# a non-optional region_id), so every planned row below is regional.
_ROW_FIELDS = frozenset(
    {"party_id", "party_name", "region_id", "region_name", "percentage"}
)
_ROW_EXTRA_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {"yougov": frozenset({"macro_region"})}
)

# ── Plan defaults ─────────────────────────────────────────────────────────────

_IDENTIFIER = "commit_test_pollster"
_PLAN_POLLSTER_NAME = "Commit Test Pollster"
_SEEDED_POLLSTER_NAME = "Seeded Pollster Name"
_SEEDED_WEIGHT = 0.5
_OLD_MAPPING = '{"North": ["North East England"]}'
_NEW_MAPPING = '{"North": ["North East England", "North West England"]}'
_SOURCE_URL = "https://example.test/new-tables.xlsx"
_OLD_SOURCE_URL = "https://example.test/old-tables.xlsx"
_START = date(2026, 3, 1)
_END = date(2026, 3, 3)
_SAMPLE_SIZE = 1500

# (party, region, percentage) for each planned row.
_PLAN_ROWS: tuple[tuple[str, str, float], ...] = (
    ("Labour", "London", 31.5),
    ("Conservative", "Scotland", 18.0),
    ("Reform UK", "Wales", 27.25),
)

_StoredRow = tuple[int, int | None, float]


def _parsed(importer: ModuleType) -> BaseModel:
    """Build ``importer``'s own ``ParsedPoll`` for ``_START``–``_END``."""
    parsed: BaseModel = importer.ParsedPoll.model_validate(
        {
            "sample_size": _SAMPLE_SIZE,
            "fieldwork_start": _START,
            "fieldwork_end": _END,
            **_PARSED_PERCENTAGES[_short_name(importer)],
        }
    )
    return parsed


def _plan(
    importer: ModuleType,
    world: WestminsterWorld,
    *,
    pollster_exists: bool,
    pollster_id: int | None = None,
    poll_id: int | None = None,
) -> BaseModel:
    """Build ``importer``'s own ``ImportPlan`` for one poll on the seeded map.

    The plan carries ``_NEW_MAPPING``, ``_SOURCE_URL``, :func:`_parsed` and one
    row per ``_PLAN_ROWS`` entry. Every model is built with ``model_validate``,
    so a field the module renames fails here loudly.
    """
    rows: list[BaseModel] = []
    for party_name, region_name, percentage in _PLAN_ROWS:
        row_kwargs: dict[str, object] = {
            "party_id": world.party_ids[party_name],
            "party_name": party_name,
            "region_id": world.region_ids[region_name],
            "region_name": region_name,
            "percentage": percentage,
        }
        if importer is yougov_import:
            row_kwargs["macro_region"] = region_name
        rows.append(importer.PlannedPollRow.model_validate(row_kwargs))
    plan: BaseModel = importer.ImportPlan.model_validate(
        {
            "pollster_identifier": _IDENTIFIER,
            "pollster_name": _PLAN_POLLSTER_NAME,
            "pollster_id": pollster_id,
            "pollster_exists": pollster_exists,
            "regions_mapping": _NEW_MAPPING,
            "map_id": world.map_id,
            "map_name": world.map_name,
            "source_url": _SOURCE_URL,
            "parsed": _parsed(importer),
            "poll_id": poll_id,
            "poll_exists": poll_id is not None,
            "rows": rows,
        }
    )
    return plan


def _commit(
    importer: ModuleType, db: Database, plan: BaseModel, **kwargs: bool
) -> PollImportResult:
    """Run ``importer.commit_import_plan`` with a typed result."""
    result: PollImportResult = importer.commit_import_plan(db, plan, **kwargs)
    return result


# ── Seeding and reading back ──────────────────────────────────────────────────


def _seed_pollster(
    db: Database, *, regions_mapping: str | None = _NEW_MAPPING
) -> Pollster:
    """Seed the plan's pollster, named and weighted unlike a newly created one."""
    return db.add_pollster(
        _SEEDED_POLLSTER_NAME,
        _IDENTIFIER,
        weight=_SEEDED_WEIGHT,
        regions_mapping=regions_mapping,
    )


def _seed_poll(
    db: Database,
    world: WestminsterWorld,
    *,
    national: Mapping[int, float] | None = None,
    pollster_identifier: str = _IDENTIFIER,
    map_id: int | None = None,
    fieldwork_start: date = _START,
    fieldwork_end: date = _END,
    sample_size: int = _SAMPLE_SIZE,
    source_url: str = _OLD_SOURCE_URL,
) -> Poll:
    """Seed a poll matching the plan's five keys unless one is overridden."""
    return add_poll_with_rows(
        db,
        map_id=world.map_id if map_id is None else map_id,
        pollster_identifier=pollster_identifier,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        national=national or {},
        sample_size=sample_size,
        source_url=source_url,
    )


def _sorted_rows(rows: list[_StoredRow]) -> list[_StoredRow]:
    """Sort ``(party_id, region_id, percentage)`` tuples, national rows first."""
    return sorted(rows, key=lambda r: (r[0], -1 if r[1] is None else r[1], r[2]))


def _stored_rows(db: Database, poll_id: int) -> list[_StoredRow]:
    """Return a poll's rows as sorted ``(party_id, region_id, percentage)``."""
    return _sorted_rows(
        [
            (row.party_id, row.region_id, row.percentage)
            for row in db.get_rows_for_poll(poll_id)
        ]
    )


def _expected_plan_rows(world: WestminsterWorld) -> list[_StoredRow]:
    """Return ``_PLAN_ROWS`` resolved to the seeded ids, sorted like the DB rows."""
    assert len(_PLAN_ROWS) == 3
    return _sorted_rows(
        [
            (world.party_ids[party], world.region_ids[region], percentage)
            for party, region, percentage in _PLAN_ROWS
        ]
    )


def _count(db: Database, model: type[Poll] | type[Pollster]) -> int:
    """Return the number of rows in ``model``'s table."""
    with db.session() as session:
        count: int = session.execute(
            select(func.count()).select_from(model)
        ).scalar_one()
    return count


def _poll(db: Database, poll_id: int) -> Poll:
    """Return the stored poll, failing the test if it is missing."""
    poll = db.get_poll(poll_id)
    assert poll is not None
    return poll


def _pollster(db: Database) -> Pollster:
    """Return the plan's pollster, failing the test if it is missing."""
    pollster = db.get_pollster_by_identifier(_IDENTIFIER)
    assert pollster is not None
    return pollster


# ── The variant split and the per-module models ───────────────────────────────


class TestImporterLists:
    """Pins the importer lists and each module's model fields."""

    def test_variants_partition_all_importers_by_name(self) -> None:
        all_names = [_short_name(importer) for importer in ALL_IMPORTERS]
        assert len(all_names) == 11
        assert len(set(all_names)) == 11
        assert [_short_name(importer) for importer in VARIANT_A] == [
            "bmg_research",
            "deltapoll",
            "focaldata",
            "ipsos",
            "lord_ashcroft",
            "survation",
            "techne",
        ]
        assert [_short_name(importer) for importer in VARIANT_B] == [
            "find_out_now",
            "more_in_common",
            "opinium",
            "yougov",
        ]
        assert set(VARIANT_A).isdisjoint(VARIANT_B)
        assert set(VARIANT_A) | set(VARIANT_B) == set(ALL_IMPORTERS)

    @_over(ALL_IMPORTERS)
    def test_model_fields_match_the_per_module_kwargs(
        self, importer: ModuleType
    ) -> None:
        """The kwargs tables above cover each module's models exactly."""
        name = _short_name(importer)
        parsed_fields = set(importer.ParsedPoll.model_fields)
        row_fields = set(importer.PlannedPollRow.model_fields)

        assert parsed_fields == _PARSED_COMMON_FIELDS | set(_PARSED_PERCENTAGES[name])
        assert row_fields == _ROW_FIELDS | _ROW_EXTRA_FIELDS.get(name, frozenset())


# ── Pollster ──────────────────────────────────────────────────────────────────


class TestCommitPollster:
    """``commit_import_plan`` creates the plan's pollster or reuses it."""

    @_over(ALL_IMPORTERS)
    def test_absent_pollster_is_created_with_unit_weight_and_mapping(
        self, importer: ModuleType, db: Database
    ) -> None:
        world = seed_westminster_world(db)
        plan = _plan(importer, world, pollster_exists=False)

        result = _commit(importer, db, plan)

        assert result.created_pollster is True
        assert _count(db, Pollster) == 1
        pollster = _pollster(db)
        assert pollster.name == _PLAN_POLLSTER_NAME
        assert pollster.weight == 1.0
        assert pollster.regions_mapping == _NEW_MAPPING
        assert _poll(db, result.poll_id).pollster_id == pollster.id

    @_over(ALL_IMPORTERS)
    def test_existing_pollster_is_reused_without_renaming(
        self, importer: ModuleType, db: Database
    ) -> None:
        world = seed_westminster_world(db)
        seeded = _seed_pollster(db)
        plan = _plan(importer, world, pollster_exists=True, pollster_id=seeded.id)

        result = _commit(importer, db, plan)

        assert result.created_pollster is False
        assert _count(db, Pollster) == 1
        pollster = _pollster(db)
        assert pollster.id == seeded.id
        assert pollster.name == _SEEDED_POLLSTER_NAME
        assert pollster.weight == _SEEDED_WEIGHT
        assert _poll(db, result.poll_id).pollster_id == seeded.id

    @_over(ALL_IMPORTERS)
    def test_pollster_missing_at_commit_raises_and_writes_nothing(
        self, importer: ModuleType, db: Database
    ) -> None:
        """The plan says the pollster exists, but the DB no longer has it."""
        world = seed_westminster_world(db)
        plan = _plan(importer, world, pollster_exists=True, pollster_id=99)

        with pytest.raises(ValueError, match="Pollster lookup failed during commit"):
            _commit(importer, db, plan)

        assert _count(db, Pollster) == 0
        assert _count(db, Poll) == 0


class TestCommitRegionsMapping:
    """Only Variant B rewrites an existing pollster's ``regions_mapping``."""

    @_over(VARIANT_B)
    @pytest.mark.parametrize(
        "stored_mapping", [_OLD_MAPPING, None], ids=["different", "unset"]
    )
    def test_variant_b_updates_a_changed_mapping(
        self, importer: ModuleType, db: Database, stored_mapping: str | None
    ) -> None:
        world = seed_westminster_world(db)
        seeded = _seed_pollster(db, regions_mapping=stored_mapping)
        plan = _plan(importer, world, pollster_exists=True, pollster_id=seeded.id)

        result = _commit(importer, db, plan)

        assert result.created_pollster is False
        pollster = _pollster(db)
        assert pollster.regions_mapping == _NEW_MAPPING
        assert pollster.name == _SEEDED_POLLSTER_NAME
        assert pollster.weight == _SEEDED_WEIGHT

    @_over(VARIANT_B)
    def test_variant_b_leaves_an_equal_mapping_alone(
        self, importer: ModuleType, db: Database
    ) -> None:
        world = seed_westminster_world(db)
        seeded = _seed_pollster(db, regions_mapping=_NEW_MAPPING)
        plan = _plan(importer, world, pollster_exists=True, pollster_id=seeded.id)

        result = _commit(importer, db, plan)

        assert result.created_pollster is False
        pollster = _pollster(db)
        assert pollster.regions_mapping == _NEW_MAPPING
        assert pollster.name == _SEEDED_POLLSTER_NAME
        assert pollster.weight == _SEEDED_WEIGHT

    @_over(VARIANT_A)
    @pytest.mark.parametrize(
        "stored_mapping", [_OLD_MAPPING, None], ids=["different", "unset"]
    )
    def test_variant_a_leaves_the_mapping_untouched(
        self, importer: ModuleType, db: Database, stored_mapping: str | None
    ) -> None:
        world = seed_westminster_world(db)
        seeded = _seed_pollster(db, regions_mapping=stored_mapping)
        plan = _plan(importer, world, pollster_exists=True, pollster_id=seeded.id)

        result = _commit(importer, db, plan)

        assert result.created_pollster is False
        assert _pollster(db).regions_mapping == stored_mapping


# ── Poll ──────────────────────────────────────────────────────────────────────


class TestCommitPoll:
    """``commit_import_plan`` creates the poll or reuses the one matching it."""

    @_over(ALL_IMPORTERS)
    def test_absent_poll_is_created_with_plan_metadata_and_rows(
        self, importer: ModuleType, db: Database
    ) -> None:
        world = seed_westminster_world(db)
        plan = _plan(importer, world, pollster_exists=False)

        result = _commit(importer, db, plan)

        assert result.created_poll is True
        assert result.inserted_rows == 3
        assert result.replaced_rows == 0
        assert result.skipped_existing_rows is False
        assert _count(db, Poll) == 1
        poll = _poll(db, result.poll_id)
        assert poll.map_id == world.map_id
        assert poll.fieldwork_start == _START
        assert poll.fieldwork_end == _END
        assert poll.sample_size == _SAMPLE_SIZE
        assert poll.source_url == _SOURCE_URL
        assert _stored_rows(db, result.poll_id) == _expected_plan_rows(world)

    @_over(ALL_IMPORTERS)
    @pytest.mark.parametrize(
        "stored_url", [_OLD_SOURCE_URL, _SOURCE_URL], ids=["changed", "unchanged"]
    )
    def test_matching_poll_is_reused_with_the_plan_source_url(
        self, importer: ModuleType, db: Database, stored_url: str
    ) -> None:
        """A rowless matching poll gets the plan's rows and source URL."""
        world = seed_westminster_world(db)
        seeded_pollster = _seed_pollster(db)
        seeded_poll = _seed_poll(db, world, source_url=stored_url)
        plan = _plan(
            importer,
            world,
            pollster_exists=True,
            pollster_id=seeded_pollster.id,
            poll_id=seeded_poll.id,
        )

        result = _commit(importer, db, plan)

        assert result.created_poll is False
        assert result.poll_id == seeded_poll.id
        assert result.inserted_rows == 3
        assert result.skipped_existing_rows is False
        assert _count(db, Poll) == 1
        assert _poll(db, seeded_poll.id).source_url == _SOURCE_URL
        assert _stored_rows(db, seeded_poll.id) == _expected_plan_rows(world)

    @_over(ALL_IMPORTERS)
    def test_differing_sample_size_commits_a_new_poll(
        self, importer: ModuleType, db: Database
    ) -> None:
        world = seed_westminster_world(db)
        seeded_pollster = _seed_pollster(db)
        labour = world.party_ids["Labour"]
        near_miss = _seed_poll(
            db, world, national={labour: 40.0}, sample_size=_SAMPLE_SIZE + 1
        )
        rows_before = _stored_rows(db, near_miss.id)
        plan = _plan(
            importer, world, pollster_exists=True, pollster_id=seeded_pollster.id
        )

        result = _commit(importer, db, plan)

        assert result.created_poll is True
        assert result.poll_id != near_miss.id
        assert _count(db, Poll) == 2
        assert _poll(db, result.poll_id).sample_size == _SAMPLE_SIZE
        assert _stored_rows(db, result.poll_id) == _expected_plan_rows(world)
        untouched = _poll(db, near_miss.id)
        assert untouched.sample_size == _SAMPLE_SIZE + 1
        assert untouched.source_url == _OLD_SOURCE_URL
        assert _stored_rows(db, near_miss.id) == rows_before


class TestCommitStalePlan:
    """The commit re-finds the pollster and poll instead of trusting the plan's ids."""

    @_over(ALL_IMPORTERS)
    def test_poll_and_pollster_ids_missing_from_the_plan_are_found(
        self, importer: ModuleType, db: Database
    ) -> None:
        """A plan built before the poll existed still lands on the stored poll."""
        world = seed_westminster_world(db)
        seeded_pollster = _seed_pollster(db)
        seeded_poll = _seed_poll(db, world)
        plan = _plan(importer, world, pollster_exists=True)

        result = _commit(importer, db, plan)

        assert result.created_pollster is False
        assert result.created_poll is False
        assert result.poll_id == seeded_poll.id
        assert _count(db, Pollster) == 1
        assert _count(db, Poll) == 1
        assert _poll(db, seeded_poll.id).pollster_id == seeded_pollster.id
        assert _stored_rows(db, seeded_poll.id) == _expected_plan_rows(world)

    @_over(ALL_IMPORTERS)
    def test_stale_absent_pollster_raises_integrity_error_pins_current_behaviour(
        self, importer: ModuleType, db: Database
    ) -> None:
        """A plan saying the pollster is new, committed after it was created, fails.

        ``pollster_exists=False`` goes straight to ``add_pollster``, which hits
        the unique identifier. A preview confirmed after another import of the
        same new pollster would fail this way rather than reuse it.
        """
        world = seed_westminster_world(db)
        _seed_pollster(db)
        plan = _plan(importer, world, pollster_exists=False)

        with pytest.raises(IntegrityError):
            _commit(importer, db, plan)

        assert _count(db, Pollster) == 1
        assert _pollster(db).name == _SEEDED_POLLSTER_NAME
        assert _count(db, Poll) == 0


# ── Rows──────────────────────────────────────────────────────────────────────


class TestCommitExistingRows:
    """A reused poll's rows are kept by default and swapped with ``replace_rows``."""

    @_over(ALL_IMPORTERS)
    def test_existing_rows_are_kept_without_replace_rows(
        self, importer: ModuleType, db: Database
    ) -> None:
        """The default keeps the rows but still refreshes the source URL."""
        world = seed_westminster_world(db)
        seeded_pollster = _seed_pollster(db)
        national = {world.party_ids["Labour"]: 40.0, world.party_ids["Green"]: 9.0}
        seeded_poll = _seed_poll(db, world, national=national)
        rows_before = _stored_rows(db, seeded_poll.id)
        plan = _plan(
            importer,
            world,
            pollster_exists=True,
            pollster_id=seeded_pollster.id,
            poll_id=seeded_poll.id,
        )

        result = _commit(importer, db, plan)

        assert result == PollImportResult(
            created_pollster=False,
            created_poll=False,
            poll_id=seeded_poll.id,
            inserted_rows=0,
            replaced_rows=0,
            skipped_existing_rows=True,
        )
        assert _stored_rows(db, seeded_poll.id) == rows_before
        assert _poll(db, seeded_poll.id).source_url == _SOURCE_URL

    @_over(ALL_IMPORTERS)
    def test_replace_rows_swaps_only_this_polls_rows(
        self, importer: ModuleType, db: Database
    ) -> None:
        world = seed_westminster_world(db)
        seeded_pollster = _seed_pollster(db)
        labour = world.party_ids["Labour"]
        green = world.party_ids["Green"]
        seeded_poll = _seed_poll(db, world, national={labour: 40.0, green: 9.0})
        sibling = _seed_poll(
            db, world, national={labour: 35.0}, fieldwork_end=date(2026, 3, 4)
        )
        sibling_rows_before = _stored_rows(db, sibling.id)
        plan = _plan(
            importer,
            world,
            pollster_exists=True,
            pollster_id=seeded_pollster.id,
            poll_id=seeded_poll.id,
        )

        result = _commit(importer, db, plan, replace_rows=True)

        assert result == PollImportResult(
            created_pollster=False,
            created_poll=False,
            poll_id=seeded_poll.id,
            inserted_rows=3,
            replaced_rows=2,
            skipped_existing_rows=False,
        )
        assert _stored_rows(db, seeded_poll.id) == _expected_plan_rows(world)
        assert _stored_rows(db, sibling.id) == sibling_rows_before


# ── _find_existing_poll ───────────────────────────────────────────────────────


class TestFindExistingPoll:
    """``_find_existing_poll`` matches pollster, map, both dates and sample size."""

    @_over(ALL_IMPORTERS)
    def test_poll_matching_all_five_keys_is_found(
        self, importer: ModuleType, db: Database
    ) -> None:
        world = seed_westminster_world(db)
        pollster = _seed_pollster(db)
        seeded_poll = _seed_poll(db, world)

        found = importer._find_existing_poll(
            db, pollster.id, world.map_id, _parsed(importer)
        )

        assert found is not None
        assert found.id == seeded_poll.id

    @_over(ALL_IMPORTERS)
    @pytest.mark.parametrize(
        "differing_key",
        ["pollster", "map", "fieldwork_start", "fieldwork_end", "sample_size"],
    )
    def test_poll_differing_in_one_key_is_not_found(
        self, importer: ModuleType, db: Database, differing_key: str
    ) -> None:
        """A poll matching on every key but ``differing_key`` is not a match."""
        world = seed_westminster_world(db)
        pollster = _seed_pollster(db)
        db.add_pollster("Rival Pollster", "rival_pollster")
        other_map_id = db.add_map("Other Map", parliament="westminster").id
        _seed_poll(
            db,
            world,
            pollster_identifier=(
                "rival_pollster" if differing_key == "pollster" else _IDENTIFIER
            ),
            map_id=other_map_id if differing_key == "map" else world.map_id,
            fieldwork_start=(
                date(2026, 2, 28) if differing_key == "fieldwork_start" else _START
            ),
            fieldwork_end=(
                date(2026, 3, 4) if differing_key == "fieldwork_end" else _END
            ),
            sample_size=(
                _SAMPLE_SIZE - 1 if differing_key == "sample_size" else _SAMPLE_SIZE
            ),
        )
        assert _count(db, Poll) == 1

        found = importer._find_existing_poll(
            db, pollster.id, world.map_id, _parsed(importer)
        )

        assert found is None
