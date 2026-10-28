"""US central parity, shared-source covariance and genuine sampling variance."""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from pathlib import Path
import sys

import numpy as np
import pytest
from flask import Flask

from db import Database
from model_support.us_simulation import (
    UsErrorConfig,
    UsErrorStreams,
    UsLocalAverage,
    UsPollSnapshot,
    UsSeatInput,
    UsSimulationInputs,
    assert_zero_error_parity,
    build_us_simulation_inputs,
    project_us_seats,
    run_us_simulations,
    us_error_budget,
)
from models import ElectionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "us"))

from _common import (
    SeatPollAverage,
    UsModelSpec,
    UsSimulationConfig,
    baseline_shares_for_seat,
    blend_seat_swings,
    build_house_simulation_inputs,
    freeze_house_central_inputs,
    prepare_us_projection,
    project_seat_votes,
    run_simulation,
)
from tests.console_fixtures import app

TODAY = date(2026, 10, 19)
NAMES = {20: "Democratic", 21: "Republican", 22: "Independent", 23: "Others"}


def _poll(
    poll_id: int = 1,
    *,
    seat_id: int | None = 1,
    weight: float = 1.0,
    shares: tuple[tuple[int, float], ...] = ((20, 50.0), (21, 50.0)),
    n: int | None = 1000,
    kind: str | None = "individual_poll",
) -> UsPollSnapshot:
    return UsPollSnapshot(
        poll_id, seat_id, weight, shares, kind, "LV", n,
        "https://example.org/methodology", None,
    )


def _inputs(
    *,
    baselines: dict[int, dict[int, float]] | None = None,
    averages: dict[int, SeatPollAverage] | None = None,
    parents: dict[int, int] | None = None,
    states: dict[int, str] | None = None,
    polls: tuple[UsPollSnapshot, ...] = (),
    prior: float = 1.0,
    config: UsErrorConfig | None = None,
    region_swing: dict[int, float] | None = None,
) -> UsSimulationInputs:
    votes = baselines or {1: {20: 5000.0, 21: 5000.0}}
    local = averages or {}
    graph = parents or {}
    parties = set().union(*(set(value) for value in votes.values()))
    parties.update(party for avg in local.values() for party in avg.shares)
    parties.update((region_swing or {}).keys())
    order = tuple(parties)
    regions: dict[int, int | None] = {seat: 1 for seat in votes}
    swings = {1: region_swing or {}}
    blended = blend_seat_swings(
        seat_averages=local, seat_party_vote_totals=votes,
        region_by_seat_id=regions, region_swings=swings,
        party_universe=parties, party_name_by_id=NAMES,
        parent_seat_by_id=graph, prior_weight=prior,
    )
    reference, _ = project_seat_votes(
        votes, regions, parties, swings, NAMES, blended,
        seat_poll_averages=local,
    )
    seats: list[UsSeatInput] = []
    local_inputs: list[UsLocalAverage] = []
    for seat_id, baseline_votes in votes.items():
        baseline = baseline_shares_for_seat(baseline_votes)
        avg = local.get(seat_id)
        active = avg is not None and avg.n_polls > 0 and avg.total_weight > 0
        alpha = (
            avg.total_weight / (avg.total_weight + prior)
            if active and avg is not None else 0.0
        )
        broad = tuple(
            baseline_votes.get(p, 0) > 0 or NAMES[p] == "Others" for p in order
        )
        eligible = tuple(
            broad[j] or bool(active and avg is not None and p in avg.shares)
            for j, p in enumerate(order)
        )
        swing = blended.get(seat_id, swings[1])
        seats.append(UsSeatInput(
            seat_id, f"district-{seat_id}", (states or {}).get(seat_id, "Texas"),
            sum(baseline_votes.values()),
            tuple(baseline.get(p, 0.0) for p in order),
            tuple(
                baseline.get(p, 0.0) + (swing.get(p, 0.0) if eligible[j] else 0.0)
                for j, p in enumerate(order)
            ),
            broad, eligible, graph.get(seat_id), alpha,
            sum(x > 0 for x in baseline_votes.values()) == 1,
        ))
        if active and avg is not None:
            local_inputs.append(UsLocalAverage(
                seat_id, alpha, tuple(avg.shares.items()),
            ))
    return build_us_simulation_inputs(
        model="us-house", as_of_date=TODAY, party_ids=order,
        party_names=tuple(NAMES[p] for p in order), seats=tuple(seats),
        averages=tuple(local_inputs), polls=polls,
        config=config or UsErrorConfig("synthetic test", sampling=False),
        central_rows=reference,
    )


@pytest.mark.parametrize("prior", [0.0, 1.0, 3.0])
def test_zero_error_matches_independent_known_projection(prior: float) -> None:
    inputs = _inputs(
        averages={1: SeatPollAverage(1, {20: 60.0, 21: 40.0}, 1, "D vs R")},
        polls=(_poll(shares=((20, 60.0), (21, 40.0))),), prior=prior,
        region_swing={20: 2.0, 21: -2.0},
    )
    run = run_us_simulations(inputs, simulations=3)
    alpha = 1 / (1 + prior)
    expected_d = 5000 + round(100 * (alpha * 10 + (1 - alpha) * 2))
    j = inputs.party_ids.index(20)
    assert run.central.votes[0, 0, j] == expected_d
    assert (run.vote_totals[:, j] == expected_d).all()
    assert run.central.recorded_rows(inputs) == [
        {
            "seat_id": 1, "party_id": party,
            "vote_total": expected_d if party == 20 else 10000 - expected_d,
            "elected": party == 20,
        }
        for party in inputs.party_ids
    ]


def test_zero_error_clamp_fallback_rounding_and_first_max_ties() -> None:
    inputs = _inputs(
        baselines={1: {20: 1.0, 21: 1.0}, 2: {20: 2.0, 21: 6.0}},
        region_swing={20: -100.0, 21: -100.0},
    )
    central = run_us_simulations(inputs, simulations=1).central
    # Both raw shares clamp to zero, so the unchanged baseline is restored.
    assert central.votes.tolist() == [[[1, 1], [2, 6]]]
    assert central.winners.tolist() == [[0, 1]]
    assert inputs.party_ids[0] == 20
    assert_zero_error_parity(inputs)
    # Python banker's rounding is applied to counts after winner selection.
    rounding = _inputs(
        baselines={1: {20: 1.0, 21: 2.0}},
        region_swing={20: 50 / 3, 21: -50 / 3},
    )
    assert (
        run_us_simulations(rounding, simulations=1).central.votes.tolist()
        == [[[2, 1]]]
    )


def test_absent_parties_others_and_local_independent_entrant() -> None:
    inputs = _inputs(
        baselines={1: {20: 5000.0, 21: 5000.0}, 2: {21: 10000.0}},
        averages={2: SeatPollAverage(1.0, {21: 45.0, 22: 55.0}, 1, "R vs I")},
        polls=(_poll(seat_id=2, shares=((21, 45.0), (22, 55.0))),),
        prior=0, region_swing={20: 9.0, 21: -9.0, 23: 2.0},
    )
    projection = run_us_simulations(inputs, simulations=1).central
    independent = inputs.party_ids.index(22)
    dem = inputs.party_ids.index(20)
    others = inputs.party_ids.index(23)
    assert projection.shares[0, 0, independent] == 0
    assert projection.shares[0, 1, independent] > 50
    assert projection.shares[0, 1, dem] == 0
    assert projection.shares[0, 1, others] > 0
    assert inputs.seats[1].single_party_baseline
    source = next(source for source in inputs.sources if source.source_id == "poll:1")
    assert source.loadings[0].coefficients[independent] == 1


@pytest.mark.parametrize("shares", [
    ((20, 50.0), (21, 50.0)),
    ((20, 80.0), (21, 20.0)),
    ((20, 60.0), (21, 30.0), (22, 10.0)),
])
def test_sampling_only_post_projection_variance(
    shares: tuple[tuple[int, float], ...],
) -> None:
    baseline = {party: share * 1000 for party, share in shares}
    inputs = _inputs(
        baselines={1: baseline},
        averages={1: SeatPollAverage(1.0, dict(shares), 1, "synthetic")},
        polls=(_poll(shares=shares),), prior=0,
        config=UsErrorConfig("SRS synthetic, all respondents decided", sampling=True),
    )
    errors = UsErrorStreams(inputs).draw(inputs, 120_000)
    projection = project_us_seats(inputs, errors)
    j = inputs.party_ids.index(20)
    p = dict(shares)[20] / 100
    analytic = 10000 * p * (1 - p) / 1000
    assert np.var(projection.shares[:, 0, j]) == pytest.approx(analytic, rel=0.02)
    assert np.abs(errors.sum(axis=2)).max() < 1e-12


def test_sampling_weights_decided_denominator_design_effect_and_squared_alpha() -> None:
    inputs = _inputs(
        averages={1: SeatPollAverage(3.0, {20: 50, 21: 50}, 2, "D vs R")},
        polls=(
            _poll(weight=1, shares=((20, 45), (21, 45))),
            _poll(2, weight=2, shares=((20, 40), (21, 40)), n=500),
        ),
        prior=3,
        config=UsErrorConfig("synthetic design effect 2", design_effect=2),
    )
    sources = {source.source_id: source for source in inputs.sources}
    assert sources["poll:1"].n_eff == 450
    assert sources["poll:2"].n_eff == 200
    j = inputs.party_ids.index(20)
    assert sources["poll:1"].loadings[0].coefficients[j] == pytest.approx(0.5 / 3)
    assert sources["poll:2"].loadings[0].coefficients[j] == pytest.approx(1 / 3)
    expected = (0.5 / 3)**2 * 2500 / 450 + (1 / 3)**2 * 2500 / 200
    errors = UsErrorStreams(inputs).draw(inputs, 100_000)
    assert np.var(errors[:, 0, j]) == pytest.approx(expected, rel=0.02)


def test_party_specific_average_denominators_are_retained() -> None:
    inputs = _inputs(
        baselines={1: {20: 5000, 21: 4000, 22: 1000}},
        averages={
            1: SeatPollAverage(
                3, {20: 160 / 3, 21: 130 / 3, 22: 10}, 2, "synthetic",
            ),
        },
        polls=(
            _poll(shares=((20, 50), (21, 40), (22, 10))),
            _poll(2, weight=2, shares=((20, 55), (21, 45))),
        ), prior=0,
    )
    source = next(source for source in inputs.sources if source.source_id == "poll:1")
    loadings = source.loadings[0].coefficients
    assert loadings[inputs.party_ids.index(22)] == 1
    assert loadings[inputs.party_ids.index(20)] == pytest.approx(1 / 3)


def test_parent_local_sampling_is_shared_with_child_products() -> None:
    inputs = _inputs(
        baselines={i: {20: 5000, 21: 5000} for i in (1, 2, 3)},
        averages={
            1: SeatPollAverage(1, {20: 50, 21: 50}, 1, "D vs R"),
            2: SeatPollAverage(1, {20: 50, 21: 50}, 1, "D vs R"),
        },
        parents={2: 1, 3: 1}, polls=(_poll(), _poll(2, seat_id=2)),
        config=UsErrorConfig("synthetic parent graph"),
    )
    sources = {source.source_id: source for source in inputs.sources}
    parent_loadings = {
        loading.seat_index: loading.coefficients[0]
        for loading in sources["poll:1"].loadings
    }
    assert parent_loadings == {0: 0.5, 1: 0.25, 2: 0.5}
    errors = UsErrorStreams(inputs).draw(inputs, 100_000)
    assert np.array_equal(errors[:, 0, :], errors[:, 2, :])
    assert np.cov(errors[:, 0, 0], errors[:, 1, 0])[0, 1] == pytest.approx(
        0.5 * 0.25 * 2.5, rel=0.025,
    )


def test_common_national_once_and_state_sharing_not_division_sharing() -> None:
    baselines = {i: {20: 5000.0, 21: 5000.0} for i in (1, 2, 3)}
    states = {1: "Texas", 2: "Texas", 3: "Arkansas"}
    national = _inputs(
        baselines=baselines, states=states,
        config=UsErrorConfig("national only", national_scale=4, sampling=False),
    )
    errors = UsErrorStreams(national).draw(national, 10_000)
    assert np.array_equal(errors[:, 0, :], errors[:, 1, :])
    assert np.array_equal(errors[:, 0, :], errors[:, 2, :])
    state = _inputs(
        baselines=baselines, states=states,
        config=UsErrorConfig("state only", state_scale=4, sampling=False),
    )
    errors = UsErrorStreams(state).draw(state, 20_000)
    assert np.array_equal(errors[:, 0, :], errors[:, 1, :])
    assert abs(np.corrcoef(errors[:, 0, 0], errors[:, 2, 0])[0, 1]) < 0.03
    national_row = next(
        row for row in us_error_budget(national)
        if row["seat_id"] is None and row["party"] == "Democratic"
    )
    assert national_row["combined_sd"] == 2


def test_aggregate_count_does_not_create_sampling_or_shrink_error() -> None:
    config = UsErrorConfig("aggregate total error", national_scale=4)
    one = _inputs(
        polls=(_poll(seat_id=None, kind="aggregate_estimate", n=None),),
        config=config,
    )
    many = _inputs(
        polls=tuple(
            _poll(i, seat_id=None, kind="aggregate_estimate", n=None)
            for i in range(1, 20)
        ), config=config,
    )
    assert all(source.kind != "sampling" for source in many.sources)
    assert np.array_equal(
        UsErrorStreams(one).draw(one, 2000), UsErrorStreams(many).draw(many, 2000),
    )


@pytest.mark.parametrize("poll", [_poll(kind=None), _poll(n=None)])
def test_unknown_or_missing_individual_evidence_stops(poll: UsPollSnapshot) -> None:
    with pytest.raises(ValueError, match="repair provenance/sample size"):
        _inputs(
            averages={1: SeatPollAverage(1, {20: 50, 21: 50}, 1, "synthetic")},
            polls=(poll,),
        )


def test_model_date_seed_and_chunk_invariance_central_is_not_mean() -> None:
    inputs = _inputs(
        config=UsErrorConfig(
            "synthetic all components", national_scale=4, state_scale=2, race_scale=3,
        ),
    )
    one = run_us_simulations(inputs, simulations=103, chunk_size=103)
    chunks = run_us_simulations(inputs, simulations=103, chunk_size=7)
    assert np.array_equal(one.winners, chunks.winners)
    assert np.array_equal(one.vote_totals, chunks.vote_totals)
    assert one.central.votes.tolist() == [[[5000, 5000]]]
    assert not np.array_equal(one.vote_totals.mean(axis=0), one.central.votes[0, 0])
    assert inputs.seed != dataclasses.replace(inputs, model="us-senate").seed
    assert inputs.seed != dataclasses.replace(
        inputs, as_of_date=TODAY + timedelta(days=1),
    ).seed
    with pytest.raises(dataclasses.FrozenInstanceError):
        inputs.model = "us-senate"  # type: ignore[misc]


def test_parity_rejects_changed_recorded_rows_and_bad_shapes() -> None:
    inputs = _inputs()
    altered = dataclasses.replace(
        inputs, central_rows=((1, 20, 4999, True), (1, 21, 5001, False)),
    )
    with pytest.raises(ValueError, match="does not match"):
        assert_zero_error_parity(altered)
    with pytest.raises(ValueError, match="shape mismatch"):
        project_us_seats(inputs, np.zeros((1, 2, 3)))


def _seed_house(
    db: Database, tmp_path: Path, *, unknown: bool = False,
) -> UsSimulationConfig:
    election_map = db.add_map("US House Districts 2024", parliament="us_house")
    region = db.add_region(election_map.id, "West South Central")
    baseline = db.add_election(
        election_map.id, 2024, "2024 US House Election", ElectionType.us_house,
    )
    dem = db.add_party("Democratic")
    rep = db.add_party("Republican")
    for name in ("TX-01", "TX-02", "AR-01"):
        seat = db.add_seat(election_map.id, name, region_id=region.id)
        db.add_vote(baseline.id, seat.id, party_id=dem.id, vote_total=4500)
        db.add_vote(baseline.id, seat.id, party_id=rep.id, vote_total=5500)
    pollster = db.add_pollster("Synthetic aggregate", "synthetic-aggregate")
    poll = db.add_poll(
        pollster.id, election_map.id, TODAY, TODAY,
        evidence_kind=None if unknown else "aggregate_estimate",
        evidence_source="https://example.org/aggregate",
    )
    db.add_poll_row(poll.id, dem.id, 50)
    db.add_poll_row(poll.id, rep.id, 50)
    spec = UsModelSpec(
        map_name=election_map.name, baseline_election_name=baseline.name,
        election_type="us_house_model", election_name_prefix="US House UNS",
        trend_cache_json=tmp_path / "trend.json",
        trend_cache_meta_json=tmp_path / "meta.json",
    )
    return UsSimulationConfig(spec, TODAY, TODAY - timedelta(days=60), 30, True)


def test_adapter_equals_actual_deterministic_run_without_writes(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _seed_house(db, tmp_path)
    prepared = prepare_us_projection(db, cfg)
    inputs = build_house_simulation_inputs(prepared, cfg, UsErrorConfig("scratch"))
    independent = run_simulation(db, cfg)[1]
    assert (
        run_us_simulations(inputs, simulations=10).central.recorded_rows(inputs)
        == independent
    )
    assert [seat.state for seat in inputs.seats] == ["Texas", "Texas", "Arkansas"]
    assert not cfg.spec.trend_cache_json.exists()
    assert len(db.get_elections_for_map(1)) == 1


def test_central_only_parity_remains_available_when_sampling_is_blocked(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _seed_house(db, tmp_path, unknown=True)
    prepared = prepare_us_projection(db, cfg)
    frozen = freeze_house_central_inputs(prepared, cfg)
    assert_zero_error_parity(frozen)
    with pytest.raises(ValueError, match="unknown evidence kind"):
        build_house_simulation_inputs(prepared, cfg, UsErrorConfig("scratch"))


@pytest.mark.parametrize("unknown", [False, True])
def test_read_only_console_budget_reports_parity_and_evidence(
    db: Database, tmp_path: Path, app: Flask,
    monkeypatch: pytest.MonkeyPatch, unknown: bool,
) -> None:
    cfg = _seed_house(db, tmp_path, unknown=unknown)
    monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
    monkeypatch.setattr("console.blueprints.us.HOUSE_SPEC", cfg.spec)
    response = app.test_client().get(f"/us/house/uncertainty/budget?as_of={TODAY}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Exact zero-error parity verified" in body
    assert "Provisional assumptions" in body
    if unknown:
        assert "unknown evidence kind" in body
    else:
        assert "National turnout-weighted reference" in body
        assert "N/A (aggregate estimator)" in body
    assert len(db.get_elections_for_map(1)) == 1
    assert (
        app.test_client().get("/us/house/uncertainty/budget?as_of=bad").status_code
        == 400
    )


def test_parent_does_not_duplicate_common_factors() -> None:
    inputs = _inputs(
        baselines={1: {20: 5000, 21: 5000}, 2: {20: 5000, 21: 5000}},
        averages={1: SeatPollAverage(1, {20: 50, 21: 50}, 1, "D vs R")},
        parents={2: 1}, polls=(_poll(),),
        config=UsErrorConfig(
            "shared common factors, no local error", national_scale=4,
            state_scale=2, sampling=False,
        ),
    )
    errors = UsErrorStreams(inputs).draw(inputs, 50_000)
    assert np.array_equal(errors[:, 0, :], errors[:, 1, :])
    assert np.var(errors[:, 0, 0]) == pytest.approx(4 + 1, rel=0.025)


def test_unpolled_fallback_has_race_error_and_absent_party_stays_absent() -> None:
    inputs = _inputs(
        baselines={1: {20: 5000, 21: 5000}, 2: {21: 10000}},
        config=UsErrorConfig(
            "unpolled fallback", race_scale=4, national_scale=2, sampling=False,
        ),
    )
    errors = UsErrorStreams(inputs).draw(inputs, 10_000)
    assert np.var(errors[:, 0, 0]) > 4
    assert (errors[:, 1, inputs.party_ids.index(20)] == 0).all()
    projection = project_us_seats(inputs, errors)
    assert (projection.shares[:, 1, inputs.party_ids.index(20)] == 0).all()


def test_stale_poll_evidence_does_not_describe_a_different_local_average() -> None:
    with pytest.raises(ValueError, match="differs from local estimator"):
        _inputs(
            averages={1: SeatPollAverage(1, {20: 60, 21: 40}, 1, "D vs R")},
            polls=(_poll(),),
        )


@pytest.mark.parametrize("chunk_size,simulations", [(0, 10), (10001, 10), (2, 0)])
def test_simulation_resource_bounds(chunk_size: int, simulations: int) -> None:
    with pytest.raises(ValueError):
        run_us_simulations(_inputs(), chunk_size=chunk_size, simulations=simulations)
