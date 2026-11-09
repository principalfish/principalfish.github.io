# Resume point — read this first

State checks: 0005 is merged (`gh pr view 84 --json state` gives MERGED; `git merge-base --is-ancestor c34e8c76 master`). No builder exists yet (`list_sessions`, title `builder: redesign-data-console-into-five-sections`).

## The pending decision (exact)
Whether to start piece 1, and these four choices, recommendation first:
1. Section slugs: `westminster`, `holyrood`, `house`, `senate`, `president`. Piece 4 builds the URLs from them.
2. Move each route file whole in piece 1, or split `westminster.py` and `holyrood.py` into model and outputs now. Recommended: whole, because piece 3 rewrites the outputs routes anyway.
3. `home.py` and `site.py` aren't in the proposed tree. Recommended: `console/home.py` and `console/admin/site.py`.
4. `mypy.ini` has `[mypy-console.blueprints.*]` and `[mypy-console.blueprints.us]`, pinned by `tests/test_project_layout.py:20`. Recommended: one `type-var` section per new package holding routes, keep the `arg-type` exception for the US routes, update the pin.

## What the next session does
On the go-ahead: record the choices in `decisions.md`, add the wider file list to piece 1 in `plan.md` (about 10 non-console test files, `mypy.ini`, `tests/test_project_layout.py`, docstrings in `importers/polls/types.py` and `importers/polls/westminster/wikipedia_index.py`), save, then follow the planner skill's "Starting the builder".
