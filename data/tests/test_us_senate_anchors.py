"""Matching-population Senate swings and immutable estimator boundaries."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models/us"))

from _common import (
    SeatPollAverage,
    UsModelSpec,
    UsSimulationConfig,
    blend_seat_swings,
    freeze_house_central_inputs,
    house_central_diagnostics,
    prepare_us_projection,
    run_simulation,
    senate_fallback_swings,
)
from db import Database
from model_support import us_senate_anchors
from model_support.senate_cycles import (
    resolve_senate_cycle,
    senate_central_anchor_from_snapshot,
    senate_central_snapshot,
    senate_cycle_from_snapshot,
    senate_cycle_snapshot,
)
from model_support.us_house_anchors import CATALOGUE_PATH, HouseAnchorError
from model_support.us_outcomes import UsRunContext
from model_support.us_release import (
    UsReleaseError,
    senate_publication_allowed,
    senate_release,
)
from model_support.us_run import fingerprint, restore_run_inputs, senate_context
from model_support.us_senate_anchors import (
    SENATE_ANCHOR_POLICY_PATH,
    SENATE_ANCHOR_POLICY_SHA256,
    SENATE_CENTRAL_ESTIMATOR,
    resolve_senate_house_anchors,
)
from model_support.us_simulation import assert_zero_error_parity
from models import ElectionType
from tests.test_us_senate_uncertainty import TODAY, _saved, _world


def _fallback(
    anchors: dict[int, dict[str, float]],
    national: dict[int, float],
) -> dict[int, dict[int, float]]:
    return cast(dict[int, dict[int, float]], senate_fallback_swings(
        anchor_shares_by_seat=anchors,
        region_by_seat_id={seat: 1 for seat in anchors},
        region_swings={1: {1: 999.0, 2: -999.0}},
        weighted_sums={(None, party): share for party, share in national.items()},
        total_weights={(None, party): 1.0 for party in national},
        party_name_by_id={1: "Democratic", 2: "Republican", 3: "Independent"},
        party_universe=(1, 2, 3),
    ))


def test_seventy_thirty_recipe_keeps_seat_when_house_reference_is_unchanged() -> None:
    swings = _fallback({1: {"Democratic": 50.0, "Republican": 50.0}}, {1: 50, 2: 50})
    assert swings == {1: {1: 0.0, 2: 0.0, 3: 0.0}}
    # The Senate's 70/30 field must never become the House poll subtractor.
    blended = blend_seat_swings(
        seat_averages={1: SeatPollAverage(1, {1: 70, 2: 30}, 1, None)},
        seat_party_vote_totals={1: {1: 700, 2: 300}},
        region_by_seat_id={1: 1}, region_swings={1: {1: -20, 2: 20}},
        party_universe=(1, 2),
        party_name_by_id={1: "Democratic", 2: "Republican"},
        parent_seat_by_id={}, prior_weight=1,
        seat_fallback_swings=swings,
    )
    assert blended == {1: {1: 0.0, 2: 0.0}}


@pytest.mark.parametrize("weight,prior,alpha", [(0, 1, 0), (1, 1, .5), (1, 0, 1)])
def test_blending_keeps_masks_new_entrants_and_other_semantics(
    weight: float, prior: float, alpha: float,
) -> None:
    result = blend_seat_swings(
        seat_averages={1: SeatPollAverage(weight, {2: 50, 3: 45}, 1, None)},
        seat_party_vote_totals={1: {1: 70, 2: 25, 4: 5}},
        region_by_seat_id={1: 1}, region_swings={1: {1: 99}},
        party_universe=(1, 2, 3, 4),
        party_name_by_id={1: "Democratic", 2: "Republican", 3: "Independent",
                          4: "Others"},
        parent_seat_by_id={}, prior_weight=prior,
        seat_fallback_swings={1: {1: -2, 2: 3, 3: 99, 4: 1}},
    )[1]
    assert result[1] == -70 * alpha - 2 * (1 - alpha)
    assert result[2] == 25 * alpha + 3 * (1 - alpha)
    assert result[3] == 45 * alpha
    assert result[4] == 1  # Others absent from local polls retains its fallback.


def test_same_region_distinct_years_and_field_turnout_independence() -> None:
    resolved = resolve_senate_house_anchors({1: "2020-us-senate", 2: "2022-us-senate"})
    shares = {seat: anchor.shares for seat, anchor in resolved.items()}
    both = _fallback(shares, {1: 48, 2: 47})
    assert both[1][1] == 48 - 77548029 / 152644063 * 100
    assert both[2][1] == 48 - 51530747 / 107715038 * 100
    assert both[1] != both[2]
    assert _fallback({1: shares[1]}, {1: 48, 2: 47})[1] == both[1]
    assert both[1][3] == 0  # No national Independent observation.
    assert all(
        value == 0 for row in _fallback(shares, {}).values() for value in row.values()
    )


def test_own_regional_poll_keeps_region_swing_per_party() -> None:
    result = senate_fallback_swings(
        anchor_shares_by_seat={1: {"Democratic": 50, "Republican": 48}},
        region_by_seat_id={1: 7}, region_swings={7: {1: 4, 2: 99}},
        weighted_sums={(None, 1): 48, (None, 2): 47, (7, 1): 60},
        total_weights={(None, 1): 1, (None, 2): 1, (7, 1): 1},
        party_name_by_id={1: "Democratic", 2: "Republican"}, party_universe=(1, 2),
    )
    assert result == {1: {1: 4, 2: -1}}


def test_named_minor_national_observation_is_not_aggregate_other() -> None:
    with pytest.raises(HouseAnchorError, match="reference component.*Independent"):
        _fallback({1: {"Democratic": 50, "Republican": 48, "Others": 2}}, {3: 2})


def test_accepted_policy_retains_raw_other_denominator_and_historical_gate() -> None:
    refs = resolve_senate_house_anchors({1: "2016-us-senate", 2: "2020-us-senate"})
    assert refs[1].shares["Democratic"] == 61821857 / 128729718 * 100
    assert refs[2].shares["Republican"] == 72835828 / 152644063 * 100
    assert sum(refs[2].shares.values()) == pytest.approx(100)
    assert refs[2].shares["Democratic"] + refs[2].shares["Republican"] < 100
    for baseline in ("2018-us-senate", "2024-us-senate"):
        with pytest.raises(HouseAnchorError, match="unavailable"):
            resolve_senate_house_anchors({1: baseline})


@pytest.mark.parametrize("source", ["catalogue", "policy"])
def test_changed_or_missing_anchor_bytes_fail_closed(
    tmp_path: Path, source: str,
) -> None:
    path = tmp_path / "changed.json"
    args = {f"{source}_path": path}
    for raw in (None, b"{}", (CATALOGUE_PATH if source == "catalogue" else
                              SENATE_ANCHOR_POLICY_PATH).read_bytes() + b" "):
        if raw is not None:
            path.write_bytes(raw)
        with pytest.raises(HouseAnchorError):
            resolve_senate_house_anchors({1: "2020-us-senate"}, **args)


def _small_world(db: Database, tmp_path: Path) -> UsSimulationConfig:
    election_map = db.add_map("Synthetic Senate", parliament="us_senate")
    region = db.add_region(election_map.id, "Shared region")
    dem, rep = db.add_party("Democratic"), db.add_party("Republican")
    elections = {year: db.add_election(
        election_map.id, year, f"{year} US Senate Election", ElectionType.us_senate,
    ) for year in (2020, 2022, 2018)}
    for state in ("Florida", "Ohio"):
        seat = db.add_seat(election_map.id, state, region_id=region.id)
        for election in elections.values():
            db.add_vote(election.id, seat.id, party_id=dem.id, vote_total=700)
            db.add_vote(election.id, seat.id, party_id=rep.id, vote_total=300)
    pollster = db.add_pollster("Synthetic generic", "synthetic-generic")
    poll = db.add_poll(pollster.id, election_map.id, TODAY, TODAY)
    anchor = resolve_senate_house_anchors({1: "2020-us-senate"})[1]
    db.add_poll_row(poll.id, dem.id, anchor.shares["Democratic"])
    db.add_poll_row(poll.id, rep.id, anchor.shares["Republican"])
    spec = UsModelSpec(
        map_name=election_map.name, baseline_election_name=elections[2020].name,
        election_type="us_senate_model", election_name_prefix="Synthetic Senate UNS",
        trend_cache_json=tmp_path / "trend.json",
        trend_cache_meta_json=tmp_path / "meta.json",
        seat_baseline_overrides={"Ohio": "2022-us-senate"},
    )
    return UsSimulationConfig(spec, TODAY, date(2026, 9, 1), 30, True)


def test_unpolled_adapter_freeze_and_diagnostics_share_each_race_fallback(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _small_world(db, tmp_path)
    prepared = prepare_us_projection(db, cfg)
    inputs = freeze_house_central_inputs(prepared, cfg)
    assert_zero_error_parity(inputs)
    florida, ohio = inputs.seats
    assert florida.central_raw == florida.baseline
    assert ohio.central_raw != ohio.baseline
    diagnostics = house_central_diagnostics(prepared, cfg)
    seats: Any = diagnostics["seats"]
    florida_parties = seats[str(florida.seat_id)]["parties"]
    assert florida_parties[str(inputs.party_ids[0])]["fallback"] == 0
    assert seats[str(ohio.seat_id)]["fallback_kind"] == SENATE_CENTRAL_ESTIMATOR
    ignored = prepare_us_projection(db, replace(cfg, ignore_seat_polls=True))
    assert ignored.projected_votes == prepared.projected_votes
    assert {row["scope"] for row in prepared.region_diff_rows} == {
        "senate-race-fallback",
    }
    assert {row["baseline_election_id"] for row in prepared.region_diff_rows} == {
        "2020-us-senate", "2022-us-senate",
    }


def test_other_race_votes_and_field_membership_cannot_move_remaining_race(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _small_world(db, tmp_path)
    before = prepare_us_projection(db, cfg)
    florida = next(seat for seat in before.seats if seat.seat_name == "Florida")
    expected = [row for row in before.projected_votes if row["seat_id"] == florida.id]
    # Change the other race's turnout and party balance by orders of magnitude.
    with db.session() as session:
        session.execute(text(
            "UPDATE votes SET vote_total=CASE WHEN party_id=1 "
            "THEN 10000000 ELSE 1 END WHERE seat_id<>:seat",
        ), {"seat": florida.id})
        session.commit()
    changed = prepare_us_projection(db, cfg)
    assert [
        row for row in changed.projected_votes if row["seat_id"] == florida.id
    ] == expected
    narrowed = prepare_us_projection(db, replace(cfg, spec=replace(
        cfg.spec, seat_name_allowlist=frozenset({"Florida"}),
    )))
    assert narrowed.projected_votes == expected
    assert (
        narrowed.seat_fallback_swings[florida.id]
        == before.seat_fallback_swings[florida.id]
    )


def test_unavailable_anchor_fails_before_any_output_persistence(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _small_world(db, tmp_path)
    cfg = replace(cfg, dry_run=False, spec=replace(
        cfg.spec, baseline_election_name="2018 US Senate Election",
    ))
    with pytest.raises(HouseAnchorError, match="unavailable.*2018"):
        run_simulation(db, cfg)
    with db.session() as session:
        assert session.execute(text(
            "SELECT COUNT(*) FROM elections WHERE type='us_senate_model'",
        )).scalar_one() == 0
        assert session.execute(text(
            "SELECT 1 FROM sqlite_master WHERE name='model_context'",
        )).first() is None


def test_new_central_wrapper_and_legacy_decode_never_resolve_current_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cycle = resolve_senate_cycle(TODAY)
    legacy = senate_cycle_snapshot(cycle)
    anchor = resolve_senate_house_anchors({1: "2020-us-senate"})[1]
    wrapper = senate_central_snapshot(cycle, {
        "estimator": SENATE_CENTRAL_ESTIMATOR,
        "policy_sha256": SENATE_ANCHOR_POLICY_SHA256,
        "races": {"1": anchor.snapshot()},
    })
    assert wrapper is not None
    monkeypatch.setattr(
        us_senate_anchors, "resolve_senate_house_anchors",
        lambda *args: pytest.fail("Current anchors consulted"),
    )
    assert senate_cycle_from_snapshot(wrapper, TODAY).races == cycle.races
    assert senate_cycle_from_snapshot(legacy, TODAY).races == cycle.races
    assert senate_central_anchor_from_snapshot(legacy) is None
    assert senate_central_snapshot(cycle, None) == legacy
    changed = copy.deepcopy(wrapper)
    changed["house_anchor"] = {}
    with pytest.raises(ValueError, match="checksum"):
        senate_cycle_from_snapshot(changed, TODAY)


@pytest.mark.parametrize("change", ["baseline-year", "denominator", "shares"])
def test_resealed_frozen_anchor_inconsistency_is_rejected(change: str) -> None:
    cycle = resolve_senate_cycle(TODAY)
    anchor = resolve_senate_house_anchors({1: "2020-us-senate"})[1]
    wrapper: Any = senate_central_snapshot(cycle, {
        "estimator": SENATE_CENTRAL_ESTIMATOR,
        "policy_sha256": SENATE_ANCHOR_POLICY_SHA256,
        "races": {"1": anchor.snapshot()},
    })
    race = wrapper["house_anchor"]["races"]["1"]
    if change == "baseline-year":
        race["baseline_id"] = "2022-us-senate"
    elif change == "denominator":
        race["evidence"]["views"][0]["counts"]["denominator"] += 1
    else:
        race["shares"]["Democratic"] = 50
    wrapper["sha256"] = fingerprint({
        key: value for key, value in wrapper.items() if key != "sha256"
    })
    with pytest.raises(ValueError, match="(year|denominator|shares)"):
        senate_cycle_from_snapshot(wrapper, TODAY)


def test_uncertainty_and_central_only_runs_have_identical_recorded_rows(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _world(db, tmp_path)
    uncertain = run_simulation(db, cfg)[1]
    saved = _saved(db, cfg)
    stored = copy.deepcopy(saved.national)
    assert senate_publication_allowed(
        stored, release_path=cfg.spec.uncertainty_release_config,
    )
    legacy = copy.deepcopy(stored)
    legacy["private"]["options"].pop("senate_house_anchor")
    assert not senate_publication_allowed(
        legacy, release_path=cfg.spec.uncertainty_release_config,
    )
    for source in ("release", "calibration"):
        legacy_provenance = copy.deepcopy(stored)
        legacy_provenance["private"]["provenance"][source].pop("central_estimator")
        assert not senate_publication_allowed(
            legacy_provenance, release_path=cfg.spec.uncertainty_release_config,
        )
    # Frozen central values can replay even if accepted policy bytes disappear.
    monkeypatch.setattr(us_senate_anchors, "SENATE_ANCHOR_POLICY_SHA256", "0" * 64)
    restored = restore_run_inputs(stored)
    assert_zero_error_parity(restored)
    legacy_options = dict(stored["private"]["options"])
    legacy_options.pop("senate_house_anchor")
    old_context = senate_context(
        restored, map_id=stored["context"]["map_id"],
        cycle=resolve_senate_cycle(TODAY), options=legacy_options,
        comparability_id=stored["context"]["comparability_id"],
    )
    assert old_context.segment_id != UsRunContext(**stored["context"]).segment_id
    monkeypatch.undo()
    central = run_simulation(db, replace(cfg, no_uncertainty=True, simulations=None))[1]
    assert central == uncertain


@pytest.mark.parametrize("which", ["release", "calibration"])
def test_old_calibration_cannot_enable_corrected_estimator(
    db: Database, tmp_path: Path, which: str,
) -> None:
    cfg = _world(db, tmp_path)
    path = (
        cfg.spec.uncertainty_release_config if which == "release"
        else tmp_path / "candidate.json"
    )
    assert path is not None
    payload = json.loads(path.read_text())
    payload.pop("central_estimator")
    path.write_text(json.dumps(payload))
    assert not senate_release(cfg.spec.uncertainty_release_config).enabled


@pytest.mark.parametrize("source", ["release", "calibration"])
@pytest.mark.parametrize("digest", [None, "0" * 64], ids=["missing", "wrong"])
def test_current_policy_digest_must_match_with_correct_estimator(
    db: Database,
    tmp_path: Path,
    source: str,
    digest: str | None,
) -> None:
    cfg = _world(db, tmp_path)
    release_path = cfg.spec.uncertainty_release_config
    assert release_path is not None
    valid = senate_release(release_path)
    assert valid.enabled
    valid.require_enabled()
    path = release_path if source == "release" else tmp_path / "candidate.json"
    payload = json.loads(path.read_text())
    assert payload["central_estimator"] == SENATE_CENTRAL_ESTIMATOR
    assert payload["house_anchor_policy_sha256"] == SENATE_ANCHOR_POLICY_SHA256
    if digest is None:
        payload.pop("house_anchor_policy_sha256")
    else:
        payload["house_anchor_policy_sha256"] = digest
    path.write_text(json.dumps(payload))

    release = senate_release(release_path)
    assert not release.enabled
    with pytest.raises(UsReleaseError, match="House anchor policy"):
        release.require_enabled()


@pytest.mark.parametrize("source", ["release", "calibration", "options"])
@pytest.mark.parametrize("digest", [None, "0" * 64], ids=["missing", "wrong"])
def test_saved_policy_digest_must_match_with_correct_estimator(
    db: Database,
    tmp_path: Path,
    source: str,
    digest: str | None,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    stored = copy.deepcopy(_saved(db, cfg).national)
    release_path = cfg.spec.uncertainty_release_config
    assert senate_publication_allowed(stored, release_path=release_path)
    if source == "options":
        payload = stored["private"]["options"]["senate_house_anchor"]
        estimator_key, digest_key = "estimator", "policy_sha256"
    else:
        payload = stored["private"]["provenance"][source]
        estimator_key = "central_estimator"
        digest_key = "house_anchor_policy_sha256"
    assert payload[estimator_key] == SENATE_CENTRAL_ESTIMATOR
    assert payload[digest_key] == SENATE_ANCHOR_POLICY_SHA256
    if digest is None:
        payload.pop(digest_key)
    else:
        payload[digest_key] = digest

    assert not senate_publication_allowed(stored, release_path=release_path)
