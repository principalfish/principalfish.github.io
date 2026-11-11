# Resume point — read this first

State checks: builder session `local_25f43011-ef61-46dc-9023-7e1b5918b096`, branch `claude/redesign-data-console-into-five-sections-e76f9c`, HEAD `0c45b65b` (piece 9/12 committed and pushed). Piece 10 is built and staged but uncommitted (45 files, +5157/-525). Draft PR #85 is open. State is `building`.

## The pending decisions (exact)
1. Manual review and approval of piece 10's diff: a Holyrood dry-run model run (watch progress, leave the page and come back, check the result block), and a second dry run refused while the first runs. Then the builder commits and pushes; tick piece 10 and give the piece 11 briefing.
2. A question for the user: add a run-slot guard to Restore (`/db/restore`), so it is refused while a job runs? Recommendation: yes, a small guard with the same refusal text; the Restore confirm text stays word for word.
