# Review: uncommitted Senate House-anchor correction

Date: 26 October 2026.
Mode: findings only; no source edits, fixes, commits or remote posting.
Confirmed base: HEAD `76be5ea8` on `poll-errors-simulation`.
Scope: all 16 uncommitted tracked and untracked files, including candidate audit,
accepted source policy, loaders, arithmetic, compatibility, diagnostics and tests.
Acceptance reference: issue plan
`issues/anchor-us-senate-swings-to-matching-house-national-baselines/plan.md`.

## Verdict

**Follow-up status (26 October 2026): all three selected findings are addressed and verified. Changes remain uncommitted at the user’s requested checkpoint.**

Original findings:

No functional or security defects found. One test-coverage gap should be closed
before committing, plus two minor style issues. The runtime policy guards are
present and correct today; the coverage finding concerns their regression protection.

## Findings

### [P2] Test anchor-policy incompatibility independently of estimator identity

Location: [test_us_senate_anchors.py:355](/home/philiph/workspace/principalfish.github.io/data/tests/test_us_senate_anchors.py:355).
Related publication assertions:
[test_us_senate_anchors.py:321](/home/philiph/workspace/principalfish.github.io/data/tests/test_us_senate_anchors.py:321).
Runtime guards:
[us_release.py:123](/home/philiph/workspace/principalfish.github.io/data/model_support/us_release.py:123) and
[us_release.py:226](/home/philiph/workspace/principalfish.github.io/data/model_support/us_release.py:226).

The rejection tests remove `central_estimator` or the entire anchor options block,
but do not independently change or remove the policy digest while leaving the
estimator correct. Removing all three policy-digest comparisons in memory still
passed all 46 tests across `test_us_senate_anchors.py` and
`test_us_senate_uncertainty.py` (6.95 seconds). A regression could therefore permit
calibration or saved odds from a different anchor policy under the same estimator
name without these tests detecting it.

Add independent missing/wrong-digest cases for current release, calibration,
saved release/calibration provenance, and frozen anchor options. Keep the
estimator and remaining acceptance fields valid; assert that current uncertainty
or publication remains blocked, with a valid control case. This is a test-only
recommendation; the current production comparisons do reject mismatches.

### [P3] Sort the new first-party imports

Locations: [us_release.py:14](/home/philiph/workspace/principalfish.github.io/data/model_support/us_release.py:14)
and [_common.py:70](/home/philiph/workspace/principalfish.github.io/data/models/us/_common.py:70).

The added imports are not alphabetically ordered within their first-party group,
contrary to the Python guide. Sort the affected import groups. This does not
change runtime behavior.

### [P3] Add the multiline tuple's trailing comma

Location: [senate_cycles.py:390](/home/philiph/workspace/principalfish.github.io/data/model_support/senate_cycles.py:390).

The new multiline tuple of party/count pairs omits the trailing comma required
by the Python guide. Expand and format the tuple consistently. This is formatting
only.

## Review coverage and evidence

Independent passes covered correctness/reuse/simplification, coverage/common
mistakes, and security/style. The per-race matching-year fallback reaches local
blending, unpolled projection, frozen inputs and diagnostics. Tests meaningfully
exercise the 70/30 recipe, same-region mixed baselines, cohort/turnout independence,
local alpha endpoints/intermediate blending, structural support/new entrants,
Other handling, missing national evidence, 2018 unavailability, uncertainty-on/off
central parity, legacy snapshot dispatch and context identity separation.

Runtime source/catalogue raw-byte hashes are independently pinned. The offline
audit CLI verifies supplied workbook/correction bytes before parsing and has no
network or database operation. New Jinja output remains autoescaped. No secrets
or additional recurring mistake requiring a guide update were found.

A suspected destructive batch-validation failure was checked and rejected:
a scratch database rebuild with unsupported national Independent evidence raised
`HouseAnchorError` while preserving all four existing saved vote rows exactly.
The retrospective reset flag retains previous results until replacements succeed.

The prior implementation verification covered 6,016 cases across a full run
(6,015 passed) and successful rerun of the sole outdated fixture after correction;
strict mypy passed 286 source files. This review did not rerun the full suite.
`git diff --check` passed. Ruff is unavailable, so no formatter verification was
claimed. At review completion all 16 scoped files still match the verified
implementation copy. Only this report was written.

## Deliberately deferred scope

Full console presentation of each race's baseline year, anchor and frozen current
generic levels, and regeneration of historical comparison/calibration reports
remain later plan pieces. They are not defects in this completed implementation
section. The approved 2018 historical anchor remains unavailable. Source-policy
acceptance does not enable production calibration, live reruns, backfills or
publication.


## Selected fixes verified — 26 October 2026

The user explicitly selected all three findings. Added ten independent policy-only
negative cases, retaining valid estimator identities and positive acceptance
controls: missing/wrong digests for current release, calibration, saved release
provenance, saved calibration provenance, and frozen options. Runtime digest
comparisons were not changed. The new anchor imports were tidied without broadly
reformatting unrelated legacy imports; the multiline tuple now has trailing commas.

Strict mypy passed all 286 source files. All 84 focused tests passed in 21.85s.
In-memory removal of the current-settings digest comparison caused the four
corresponding new cases to fail; removal of the saved-provenance comparison caused
four failures; removal of the frozen-options comparison caused two failures.
These are expected mutation-test failures proving each guard is independently
protected. No source files were mutated for those probes.

The complete isolated Python suite passed **6,026 tests in 439.15s** after strict
mypy. The copy excluded .env, and all 16 changed files match the passing copy.
`git diff --check` passed. Only four files changed relative to the reviewed
implementation: test_us_senate_anchors.py, us_release.py, _common.py, and
senate_cycles.py. Production syntax trees are identical except for import order;
the tuple edit is formatting only. Candidate and accepted-policy hashes remain
unchanged. No automatic review workflow, commit, live operation, export,
comparison regeneration or remote write occurred. Paused before commit as requested.

Verification logs under data/.pytest_cache/:

- senate-review-mypy.log
- senate-review-focused.log
- senate-review-current-mutation.log
- senate-review-saved-mutation.log
- senate-review-frozen-mutation.log
- senate-anchor-review-fixes-full.log
