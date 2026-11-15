# Add lint and format checks for Python, JavaScript and shell

Formatting, naming and coding idioms are checked by review today, and most review noise comes from them. This hands that job to tools: ruff for Python, Biome for JavaScript and shellcheck for shell scripts. They run from one `./check.sh`, which also runs the type checks and both test suites as the repo's other projects do, and as a CI job, so every change is held to the same rules without anyone having to read for them.

Python under `data/` (and the three `guesstheyear` scripts) is checked against ruff's default rules plus naming, pathlib and comprehension rules, and formatted with `ruff format`. All JavaScript source files are linted with Biome's recommended rules and formatted to a 2-space, double-quote style. The two live shell scripts already pass shellcheck. Every existing finding is fixed here, so the checks gate from the day they land. Strict mypy keeps gating the Python tests as it does now.

The one-off reformat lands as one formatting-only commit per language. Both commits are listed in `.git-blame-ignore-revs` so `git blame` skips them. The tools are pinned so every machine and CI see the same results.

The front-door docs are brought up to date at the same time. The README becomes a short guide for a person new to the repo: what it is, setup, development and checks, how the site ships, and where to read more. `AGENTS.md` keeps only what a coding agent would otherwise get wrong. `data/README.md` stays the pipeline's front door. Detail that doesn't belong in a front door moves to the topic docs that already exist, and nothing is lost.

Rules that no linter can check, such as how booleans are named, guard clauses or comments that explain why, stay with review.

## Plan

_2026-11-15 · 8 of 9 pieces done · now: piece 9 (draft PR #87)_

- [x] **1/9 Pin the tools and add their settings** · `68720459`
  Pin ruff, shellcheck and Biome and add their settings.
- [x] **2/9 Reformat the Python once** · `5fb62b38`
  One formatting-only commit with ruff format.
- [x] **3/9 Reformat the JavaScript once** · `36dfaf34`
  One formatting-only commit with Biome, bundles rebuilt.
- [x] **4/9 Fix the ruff findings in the tests** · `149e3967`
  Clean up the tests, including how shared fixtures are registered.
- [x] **5/9 Fix the ruff findings in the pipeline code** · `c4de3561`
  Clean up the pipeline code without changing behaviour.
- [x] **6/9 Fix the Biome findings** · `5dd207ae`
  Clean up the JavaScript across every page, checked by hand in the browser.
- [x] **7/9 One lint script and CI** · `fb509ea7`
  `./lint.sh` and a CI lint job.
- [x] **8/9 Front-door docs** · `3fabbbd7`, added 2026-11-14: the user asked for the README, AGENTS.md and data/README.md to follow the docs guide
  Rewrite the README, AGENTS.md and data/README.md as short front doors, carrying the lint commands.
- [ ] **9/9 One check script for everything** · added 2026-11-15: the user asked for a single `check.sh` like the other repos
  Rename `lint.sh` to `check.sh` and have it run mypy, pytest and Vitest after the linters.
- [x] **Review fixes (CI)** · `cfcfa05b`
  Ignore ruff's executable-bit rules, which only fire off WSL.
- [x] **Review fixes (round 1)** · `8c06ded5`, `3d1edb61`
  Fail the lint on Biome warnings; correct the archive row; cut CLAUDE.md to `@AGENTS.md`.
