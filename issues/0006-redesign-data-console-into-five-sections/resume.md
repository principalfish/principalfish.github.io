# Resume point — read this first

State checks: piece 10 (background runs) is mid-build in the builder worktree on top of `0c45b65b`, all unstaged (34 modified, 6 new files). The builder hit its usage limit while subagent B1 (Westminster, Holyrood, Export, Rebuild) was running; on the user's "resume" (2026-11-11) it was told to audit B1's call sites for half-made edits, finish, then run checks and send the review request. Builder `local_25f43011-ef61-46dc-9023-7e1b5918b096`. Draft PR #85 open. State `building`.

## The pending decision (exact)
Two questions to the user, both held by the builder until answered: (a) reword the US one-chamber rebuild texts in `console/us/model_form.py` (confirm: "This page stays open and blocked until it finishes, which can take hours."; checkbox help: "This page stays blocked until it finishes…") to say the run continues in the background and progress shows on its run page; recommended yes, updating the pinned test. (b) the app-wide backup hook fires on the POST that starts a dry-run job; recommended leave it (the hook is a listed safeguard, it fires on every POST today, and an unchanged database makes no new archive).
