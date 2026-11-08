# Referdle solver

Static frontend (`js/`, `css/`, `data/`).

## Referdle solver correctness and timing benchmark

Requires Node.js 22 or later and the existing npm development dependencies.
Fast harness tests run with `npm test`. The separate benchmark uses 100 frozen
games spread across days #1000–1414, with pool probes first and expanded probes
second (200 cases), through the application's actual daily controller and the
production Worker client/handler using native Node `worker_threads`. The default
backend is `worker`; a single Worker owns and decodes the matrix once, then
reuses it across the sequential cases. Logical hashes and frozen-answer checks
are computed inside that Worker; the host does not load another decoded matrix.

The full reference archive is local and ignored by Git. On a fresh checkout,
generate it from the current solver before running full verification:

```bash
npm run benchmark:referdle -- --record
```

This records all 100 games in both modes (200 traces) and their current timings.
It takes roughly ten minutes on the development machine; other machines vary.
Recording refuses to replace an existing archive. Keep a baseline unchanged
while checking later solver changes; regenerating it records the current
version, not the original unoptimized timing baseline.

From the repository root, verify against the local reference:

```bash
npm run benchmark:referdle
npm run benchmark:referdle -- --days 1000,1414 --report /tmp/referdle-report.json
npm run benchmark:referdle -- --backend direct --days 1000,1414 --report /tmp/referdle-direct.json
```

The explicit `direct` backend is for focused compatibility checks; it does not
validate native Worker transport and has no message roundtrip measurements.
Worker benchmarks fail rather than silently substituting a direct backend.
Both backends close their resources on success or failure.

Verification never creates or replaces a reference. Missing/corrupt references,
changed logical inputs/strategy, incomplete games and any exact trace mismatch
exit nonzero. Traces compare each move, board, feedback, candidate array/order,
ranking and score, plus the actual final board and outcome. Scores retain their
full precision; missing fields, undefined, null and negative zero remain distinct.

Initial recording and deliberate replacement are explicit actions:

```bash
npm run benchmark:referdle -- --record
npm run benchmark:referdle -- --record --overwrite
```

The complete local baseline lives in `referdle-solver/benchmarks/references/`, with one
compressed trace per case under `pool/` and `expanded/`, plus
`reference-manifest.json` and `timing-baseline.json`. Recording stages all cases
before publishing the directory; a failed run preserves an existing baseline.
The initial archive has 200 compressed traces and two metadata files, totaling
36,606,099 bytes (36.61 MB). Initial recording took 35m49.4s; an independent
36m16.5s verification reproduced all 200 traces exactly. The existing local
archive is preserved; these files are no longer tracked or shipped with the
site. The production matrix under `referdle-solver/data/` remains tracked.
Three fast-suite integration checks use the local archive when present and are
explicitly skipped without it. The remaining unit and Worker transport tests
run in fresh checkouts and CI without generating an archive.
Replacement refuses directories containing unrelated files. A writer lock
prevents simultaneous recording; after an interrupted process, confirm it has
stopped before manually removing the sibling `.references.lock` (or the matching
lock for a custom baseline directory).

For an isolated two-case smoke recording and verification:

```bash
npm run benchmark:referdle -- --record --days 1000 --baseline-dir /tmp/referdle-smoke
npm run benchmark:referdle -- --days 1000 --baseline-dir /tmp/referdle-smoke
```

Turn a saved JSON report into a standalone HTML report without rerunning any games:

```bash
npm run report:referdle -- /tmp/referdle-report.json --output /tmp/referdle-report.html
```

The offline report plots per-game computation seconds with shared duration bands
and count scales for pool/expanded modes and baseline/current series, alongside
duration means. Worker reports additionally plot advance-job roundtrip seconds
and show roundtrip minus compute for the cases with both measurements. This
overhead includes benchmark evidence cloning/transfer and is not pure messaging
latency. Older/direct reports leave unavailable roundtrip values explicit; no
historical roundtrip baseline is inferred. It also plots total played moves, including closing moves, and
their means; baseline move counts are unavailable in timing rows. Comparison
means/deltas use the same cases with both finite timings and show the paired
count. Missing measurements are excluded, and partial/failed runs are labeled
prominently. Record reports have no baseline series. With the initial maximum
34.4s duration, the report uses nine 4-second bands covering 0–36s. The HTML
includes its SVG charts and styles, uses no network or scripts, and must be
written outside the repository/baseline, separately from the input JSON.

Partial recording requires an external baseline directory. Selected days must
belong to the frozen sample, and both probe modes always run. Optional report
files must be outside the repository and baseline; their parent directory must
already exist. Incomplete games retain compressed trace evidence in a temporary
directory, whose path appears in the report.

Progress goes to stderr and the JSON report to stdout (npm also prints its command
banner; invoke `node referdle-solver/benchmarks/runner.mjs` directly for pure JSON).
Reports include per-case times/deltas, per-mode and overall computation totals,
medians, slowest cases, separate Worker roundtrip totals/means/medians and paired
transport overhead, setup/elapsed wall time and total reference bytes. Runtime
footprint records unminified component HTML/CSS/JavaScript and shipped data file
bytes separately from decoded matrix bytes. It excludes references and shared
site assets; these file counts are not an initial network-transfer measurement. Source
and raw asset hashes are provenance; logical matrix hashes accept a different
storage type or compression when every pattern code remains identical. Changes
to Node/platform/CPU produce a timing comparability note instead of failing
correctness.

Compute time sums synchronous computation-service intervals for daily advances
inside the Worker (or the explicit direct service). It excludes Promise waiting,
asset loading/decoding, bundling, controller publication/rendering, animation
waits, trace normalization/encoding, transfer, compression and comparison.
Roundtrip time sums each advance job from host send through accepted reply;
initialization and day loading remain outside those totals in setup/wall time.
Render helpers are capture/no-op shims; these timings do not measure browser
rendering. The original baseline included synchronous `nextTurn` controller and
publication work, so CPU percentage comparisons across that boundary are
informative rather than identical-boundary measurements. Original timings are
never erased or rebased. Each run is one sequential pass without warmup; early
cases include JIT startup, and host load can change timings. Performance deltas
have no pass/fail speed threshold; failed correctness runs are invalid for
performance acceptance.

The original 200-case compute total was 35m41.3s. Recorded optimization passes
were 12m59.9s after pattern-block reuse, 12m06.0s after lossless uint8 storage,
10m34.1s after daily-result reuse and 9m22s after shared suggestion analysis.
The engine extraction pass was 9m25s. These are individual passes, not repeated
statistical estimates; each reproduced all 200 complete traces exactly.

## Referdle Worker behavior and browser checks

The browser uses one dedicated module Worker, shared by daily and manual mode.
The Worker owns the decoded matrix; the page receives lightweight words,
results and accepted continuation state. Day/mode/probe/reset and manual-input
controls remain usable while calculations run. Changing inputs invalidates
obsolete replies and physically terminates active obsolete work. An idle Worker
is reused; after cancellation it restarts lazily from accepted continuation or
manual input without replaying all prior guesses. Manual typing does not trigger
unsolicited analysis; Suggest and selected replay steps request computation.

If a Worker cannot start, the page visibly reports compatibility mode and uses
the direct service after disposing the failed Worker. That mode retains solver
functionality but performs calculations on the page. Missing/corrupt assets stay
explicit errors. Runtime/message errors preserve accepted state and permit an
explicit retry; failed day loading has a Reload day control.

Serve with `./server.sh` and open `/referdle-solver/`. Browser acceptance includes
both probe modes, a slow daily solve with responsive controls, interruption by
mode/day/probe/reset changes, manual Suggest, clue-only input, lazy scrubbing,
and failure/retry. Native Worker tests verify exact computation/transport and
host-event-loop progress, but cannot establish browser module URLs, rendering
or frame/input responsiveness. Expanded words are currently loaded at startup.

An isolated headless Edge check on 9 October 2026 passed both daily probe modes,
slow-solve frame/control responsiveness, cancellation on probe/day/mode changes,
reset, manual clue-only Suggest, lazy replay and rapid scrubbing, on-demand
manual edits, and retry after a simulated Worker error event. The page produced
no unhandled JavaScript errors during these checks; the error event was injected
to exercise recovery rather than an actual engine crash.

The full native Worker verification on 9 October 2026 matched all 200 frozen
traces. Synchronous service computation totaled 556.55 seconds (9m17s), and
advance-job roundtrips totaled 559.52 seconds (9m20s): 2.97 seconds of paired
transport/job overhead, averaging 14.85 ms per game. Mean computation was 2.604
seconds with pool probes and 2.962 seconds with expanded probes. The run took
567.01 seconds overall, including setup and trace comparisons. These are one
sequential pass's measurements; the original baseline also included controller
publication in its compute interval, so comparisons across that boundary remain
informative rather than an exact CPU speedup claim.

The current matrix is 7,491,562 gzip bytes and 16,378,209 decoded bytes, compared
with 8,857,127 gzip bytes and 32,756,418 decoded bytes before uint8 conversion.
At that measurement, unminified component HTML/CSS, JavaScript and shipped data totaled
7,804,246 file bytes, excluding the benchmark archive, stats page and shared
site assets. This file inventory is separate from browser startup transfer or
peak process memory.

## Referdle matrix storage

The deployed solver has one matrix, `referdle-solver/data/pool_matrix.uint8.gz`.
`data/manifest.json` declares its component-local filename, `uint8` storage,
dimensions, compressed/decoded lengths and SHA256 hashes. The browser loads the
declared gzip with `DecompressionStream`, checks dimensions, decoded length and
pattern codes (0–242), and keeps a `Uint8Array`. Word order and logical pattern
values are unchanged.

The 4,047 × 4,047 matrix now occupies 16,378,209 decoded bytes, half the former
32,756,418 bytes. Its gzip is 7,491,562 bytes, down from 8,857,127 bytes (15.42%).
The logical SHA256 remains
`27ac836c6842a1076564d7197b8af46840d42ca24d4768016b090127dbc3547d`.
The legacy archive is retained in Git history rather than deployed alongside
the byte matrix. Reference traces are development evidence, not page downloads.

Reproduce the conversion in an isolated directory using the last revision with
the legacy asset, from the repository root:

```bash
REFERDLE_REPRO_DIR=$(mktemp -d /tmp/referdle-matrix.XXXXXX)
git show b0670d30aa7ff18b523860822eb5489a75cf8c38:referdle-solver/data/pool_matrix.int16.gz > "$REFERDLE_REPRO_DIR/legacy.int16.gz"
node referdle-solver/benchmarks/convert-matrix.mjs \
  --input "$REFERDLE_REPRO_DIR/legacy.int16.gz" \
  --output "$REFERDLE_REPRO_DIR/pool_matrix.uint8.gz" \
  --dimension 4047 --manifest "$REFERDLE_REPRO_DIR/manifest.json"
```

The converter reads signed little-endian int16 with `DataView`, validates every
value before narrowing, and verifies every converted value, the complete
logical hash and the gzip round trip before publication. It uses gzip level 9
with zero mtime and no filename; identical inputs produce identical gzip under
the same Node/zlib version. Output overwrite and input/output aliases are
refused. Replacing an existing manifest requires `--replace-manifest` and
matching dimensions. Output and manifest must share an existing directory;
failed publication removes the new output and preserves an existing manifest.

Focused loader tests exercise native Node `DecompressionStream` and streamed
responses. These verify loading/validation, not browser rendering or browser
module support; an actual browser smoke check remains a separate acceptance
step. Serve with `./server.sh` and open `/referdle-solver/` to check asset loading
and both probe modes in a browser. Full trace verification uses the benchmark
command above and never replaces the original references.
