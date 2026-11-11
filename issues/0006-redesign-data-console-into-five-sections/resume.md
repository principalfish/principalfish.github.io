# Resume point — read this first

State checks: piece 11 (docs) staged and uncommitted in the builder worktree (12 files, +762/−59) on top of `c0700844`, nothing unstaged. The builder (`local_25f43011-ef61-46dc-9023-7e1b5918b096`, Sonnet) hit its 5-hour limit (resets 13:20 Europe/London, 2026-11-11) with only the correctness check outstanding in round 1; security clean; common-mistakes, coverage and style found holes in the docs scan test (`/polls/coverage`, `/by-elections`, typo'd and placeholder-led paths slip through, no non-empty assertion) and minor doc issues (a hard-coded home path, a repeated Limits section, "always did", long lines). Draft PR #85 open. State `building`.

## The pending decision (exact)
Resumed on Sonnet at the user's "Resume now" (2026-11-11; the 5-hour window had reset, no separate Sonnet limit). On resume: correctness re-run, fresh verifier over all five checks, fixes (docs scan holes, home path), checks, review request.
