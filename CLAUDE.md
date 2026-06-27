# principalfish.github.io

This project is managed by the global Claude framework at `~/.claude`.

- Global instructions: `~/.claude/claude.md`
- Project config (auto-loaded each session by the SessionStart hook):
  `~/.claude/projects-data/principalfish.github.io/claude.md`

Skills, guides, and workflows live under `~/.claude/`. The old in-repo `.claude/`
framework has been removed.

## Testing

The Python data pipeline and console are tested under `data/`. The test commands
use the `data/election_data` virtualenv (falling back to `data/.venv`).

Run the full check suite — strict `mypy` first (it gates the run), then `pytest`:

    cd data && ./run_tests.sh

`run_tests.sh` forwards extra args to pytest, so you can narrow a run:

    cd data && ./run_tests.sh tests/test_export_payload.py    # one file
    cd data && ./run_tests.sh -k model_outputs -q             # by keyword

Run a single step with the venv interpreter directly:

    cd data && ./election_data/bin/python -m mypy             # type-check only (data/mypy.ini, strict)
    cd data && ./election_data/bin/python -m pytest tests/ -q # tests only

Tests live in `data/tests/`; shared DB fixtures are in `data/tests/conftest.py`
(each test gets a fresh temporary SQLite database, so real data is never touched).

Quick smoke checks (no test runner):

    cd data && ./election_data/bin/python -c "from console import create_app; create_app()"   # console app builds
    cd data && ./election_data/bin/python scripts/export_elections.py --dry-run                # export plan, writes nothing
