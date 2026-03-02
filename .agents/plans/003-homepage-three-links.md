# 003 Homepage three-link navigation (Bio, Election Maps, Guess The Year)

## Status
- [x] Review current homepage and available assets
- [x] Update root homepage cards and links
- [x] Add `bio/` page with text and photos
- [x] Add `election-maps/` interactive-hosting landing page
- [x] Validate links and append learnings

## Scope
1. Replace homepage project cards with three requested entries.
2. Ensure each entry opens an appropriate page.
3. Keep implementation static-site friendly for GitHub Pages.

## Decisions
- Keep Guess The Year linked to existing `guesstheyear/` path.
- Create lightweight static pages at `bio/` and `election-maps/`.
- Use local SVG placeholders as photos until real images are provided.

## Validation plan
- Verify `index.html` has exactly three destination cards.
- Verify target paths exist: `bio/`, `election-maps/`, `guesstheyear/`.

## Validation notes
- Confirmed homepage includes cards and links for:
  - `bio/`
  - `election-maps/`
  - `guesstheyear/`
- Confirmed destination files exist:
  - `bio/index.html`
  - `election-maps/index.html`
