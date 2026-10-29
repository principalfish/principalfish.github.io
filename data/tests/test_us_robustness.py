"""Archive failures and discrete interval ranks at floating point boundaries."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from db import Database
from model_support import us_replay
from model_support.us_comparison_inputs import obj
from model_support.us_run import house_context, house_payload
from model_support.us_simulation import UsErrorStreams, run_us_simulations
from tests.test_us_house_persistence import _raw, _saved, _world
from tests.test_us_simulation import _inputs
from _common import database_file, run_simulation


def test_truncated_us_archive_is_a_read_error(
    db: Database, tmp_path: Path,
) -> None:
    cfg = _world(db, tmp_path)
    run_simulation(db, cfg)
    stored = _saved(database_file(db), cfg)
    with patch("model_support.us_replay.np.load", side_effect=EOFError("truncated NPZ")):
        with pytest.raises(ValueError, match="cannot read US simulation archive") as error:
            us_replay.load_us_draws(_raw(), stored)
    assert isinstance(error.value.__cause__, EOFError)


def test_us_national_and_regional_intervals_round_half_rank_boundary() -> None:
    inputs = _inputs(baselines={i: {20: 5000.0, 21: 5000.0} for i in range(435)})
    inputs = replace(inputs, seats=tuple(replace(seat, region_id=1) for seat in inputs.seats))
    errors = np.zeros((101, 435, 2))
    errors[0, 0] = (-1, 1)
    with patch.object(UsErrorStreams, "draw", return_value=errors):
        run = run_us_simulations(inputs, simulations=101, chunk_size=101, interval=0.99)
    # The lower rank is exactly 0.5, so nearest chooses rank zero. Float dust
    # in (1 - .99) / 2 otherwise moves it to rank one, dropping the one loss.
    assert run.regions[0].seat_ranges[:, 0].tolist() == [434, 435, 435]
    context = house_context(
        inputs, map_id=1, target_cycle=2026, options={}, comparability_id="test",
    )
    payload = house_payload(run, context, options={}, provenance={}, interval=0.99)
    metrics = obj(obj(payload.national["summary"])["metrics"])
    assert obj(obj(metrics["house_seats"])["ranges"])["20"] == [434, 435, 435]
