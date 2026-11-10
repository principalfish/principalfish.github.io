# Resume point — read this first

State checks: piece 8 committed `73df3a2c` and pushed. Builder session `local_25f43011-ef61-46dc-9023-7e1b5918b096` is idle, waiting for the go-ahead for piece 9. Draft PR #85 is open. State is `building`.

## The pending decision (exact)
Whether to start piece 9 (run log), with these recommendations put to the user: (1) records in `console-runs/` beside the database file (`/home/philiph/dbs/`), outside git and not in the B2 backup; (2) keep the newest 200 (`CONSOLE_RUN_LOG_KEEP`), no size cap; (3) `record.json` plus `stdout.txt` and `stderr.txt`, with a status field piece 10 can extend to running and interrupted; (4) top-bar "Runs", `/runs?section=…`, `/runs/<id>`, a "Recent runs" link per section, read-only; (5) logging inside `run_command` so no call site is missed; (6) a run refused for a busy slot is not logged, and partial output of failed or timed-out runs is kept; (7) add a short manual review: one dry-run model run, then the run in `/runs`, still there after a console restart.
