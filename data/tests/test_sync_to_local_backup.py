"""Tests for scripts/sync_to_local_backup.py — column introspection and no-op detection."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from models import Poll, PollRow, Pollster
from sync_to_local_backup import _col_names, main


# ── _col_names ────────────────────────────────────────────────────────────────


class TestColNames:
    def test_pollster_cols(self) -> None:
        """All expected Pollster columns are present."""
        cols = _col_names(Pollster)
        assert set(cols) >= {"id", "name", "identifier", "weight", "regions_mapping"}

    def test_poll_cols(self) -> None:
        """All expected Poll columns are present."""
        cols = _col_names(Poll)
        assert set(cols) >= {"id", "pollster_id", "map_id", "fieldwork_start", "fieldwork_end", "source_url"}

    def test_poll_row_cols(self) -> None:
        """All expected PollRow columns are present."""
        cols = _col_names(PollRow)
        assert set(cols) >= {"id", "poll_id", "party_id", "region_id", "percentage"}

    def test_no_duplicates(self) -> None:
        """Column names are unique (no aliasing creates duplicates)."""
        for model in (Pollster, Poll, PollRow):
            cols = _col_names(model)
            assert len(cols) == len(set(cols)), f"Duplicate cols in {model.__name__}: {cols}"

    def test_id_is_first(self) -> None:
        """Primary key 'id' is the first column (merge depends on PK being present)."""
        for model in (Pollster, Poll, PollRow):
            cols = _col_names(model)
            assert cols[0] == "id", f"Expected 'id' first for {model.__name__}, got {cols[0]}"


# ── main() no-op when Supabase is not configured ─────────────────────────────


class TestMainNoOp:
    def test_returns_zero_without_supabase(self, capsys: object) -> None:
        """main() exits 0 with a no-op message when Supabase vars are absent."""
        mock_config = MagicMock()
        mock_config.supabase_region = None
        mock_config.supabase_db_username = None
        mock_config.supabase_db_password = None

        with (
            patch("sync_to_local_backup.DatabaseConfig") as mock_cfg_cls,
            patch("sys.argv", ["sync_to_local_backup.py"]),
        ):
            mock_cfg_cls.from_env.return_value = mock_config
            result = main()

        assert result == 0

    def test_returns_zero_with_partial_supabase_config(self) -> None:
        """main() exits 0 (no-op) when only some SUPABASE_* vars are set."""
        mock_config = MagicMock()
        mock_config.supabase_region = "aws-1-eu-west-1"
        mock_config.supabase_db_username = "postgres.abc"
        mock_config.supabase_db_password = None  # password missing

        with (
            patch("sync_to_local_backup.DatabaseConfig") as mock_cfg_cls,
            patch("sys.argv", ["sync_to_local_backup.py"]),
        ):
            mock_cfg_cls.from_env.return_value = mock_config
            result = main()

        assert result == 0
