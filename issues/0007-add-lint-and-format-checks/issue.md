# Add lint and format checks for Python, JavaScript and shell

Formatting, naming and coding idioms are checked by review today, and most review noise comes from them. This hands that job to tools: ruff for Python, Biome for JavaScript and shellcheck for shell scripts. They run from one `./lint.sh` and as a CI job, so every change is held to the same rules without anyone having to read for them.

Python under `data/` (and the three `guesstheyear` scripts) is checked against ruff's default rules plus naming, pathlib and comprehension rules, and formatted with `ruff format`. All JavaScript source files are linted with Biome's recommended rules and formatted to a 2-space, double-quote style. The two live shell scripts already pass shellcheck. Every existing finding is fixed here, so the checks gate from the day they land. Strict mypy keeps gating the Python tests as it does now.

The one-off reformat lands as one formatting-only commit per language. Both commits are listed in `.git-blame-ignore-revs` so `git blame` skips them. The tools are pinned so every machine and CI see the same results.

Rules that no linter can check, such as how booleans are named, guard clauses or comments that explain why, stay with review.

## Plan

_2026-11-14 · 1 of 7 pieces done · now: piece 2 not started_

- [x] **1/7 Pin the tools and add their settings** · `68720459`
  Pin ruff, shellcheck and Biome and add their settings.
- [ ] **2/7 Reformat the Python once** · not started
  One formatting-only commit with ruff format.
- [ ] **3/7 Reformat the JavaScript once** · not started
  One formatting-only commit with Biome, bundles rebuilt.
- [ ] **4/7 Fix the ruff findings in the tests** · not started
  Clean up the tests, including how shared fixtures are registered.
- [ ] **5/7 Fix the ruff findings in the pipeline code** · not started
  Clean up the pipeline code without changing behaviour.
- [ ] **6/7 Fix the Biome findings** · not started
  Clean up the JavaScript across every page, checked by hand in the browser.
- [ ] **7/7 One lint script, CI and docs** · not started
  `./lint.sh`, a CI lint job, and the docs.
