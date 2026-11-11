# Resume point — read this first

State checks: piece 10 committed `c0700844` and pushed. Builder session `local_25f43011-ef61-46dc-9023-7e1b5918b096` is idle, waiting for the go-ahead for piece 11. Draft PR #85 is open. State is `building`.

## The pending decision (exact)
Whether to start piece 11 (docs), with recommendations put to the user: (1) a new `data/docs/console.md` linked from the README; (2) CLI equivalents kept, only console wording and URLs changed; (3) pages named by URL in code font and by top-bar label, no screenshots; (4) a short "Limits" list in `console.md`; (5) one help line on the Holyrood run form saying the run writes `results/holyrood-prediction.json` itself, even with Export when done unticked (beyond docs-only; builder recommended docs only); (6) a pytest test that every console path named in the docs matches a route and every relative docs link resolves (plan said no tests).
