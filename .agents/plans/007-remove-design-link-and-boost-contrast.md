# 007 Remove designs link and boost editorial contrast

## Status
- [x] Confirm requested UI changes
- [x] Remove designs link from root homepage
- [x] Increase text and card-edge contrast in shared CSS
- [x] Validate and append learnings

## Scope
1. Keep homepage focused on primary destinations only.
2. Improve readability and card definition within the current editorial style.
3. Add lightweight shared-site navigation affordance to Guess The Year.
4. Replace placeholder bio copy with provided personal text and keep photo placeholders.
5. Temporarily switch bio content to long lorem ipsum and place images inline in the text flow.
6. Refine bio into a two-column layout with text left and pictures right at one-third width.

## Decisions
- Remove only the `designs/` link from root footer.
- Apply contrast changes in `site/styles.css` so root/bio/election-maps update together.
- Add a minimal top bar + back link in `guesstheyear/` without changing gameplay structure or logic.

## Validation plan
- Verify no `designs/` link remains in root `index.html`.
- Verify darker text tones and stronger card border values in shared CSS.

## Validation notes
- `index.html` no longer contains a `designs/` link.
- Contrast token updates applied in `site/styles.css`:
  - `--ink: #0f172a`
  - `--muted: #334155`
  - `--line: rgba(15, 23, 42, 0.26)`
- Card hover shadow strengthened for clearer card separation.
- Added `guesstheyear` top bar and back navigation:
  - `guesstheyear/index.html` includes `site-topbar` with link to `../`
  - `guesstheyear/styles.css` includes isolated `site-topbar*` styles
- Updated `bio/index.html` with provided personal bio paragraphs and `/electionmaps/` link.
- Bio page keeps two image placeholders (`profile-1.svg`, `profile-2.svg`).
- Bio content now uses extended lorem ipsum placeholder copy.
- Bio is now a split layout:
  - left text column: `2fr`
  - right image rail: `1fr` (one-third width target)
- Right rail contains both placeholders (`profile-1.svg`, `profile-2.svg`) stacked vertically.

## Issue closure
- Closed when user explicitly declared a new issue.
- Election Maps redesign/scaffold tracking moved to issue `008`.
