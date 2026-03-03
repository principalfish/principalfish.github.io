# 005 Apply Concept 1 editorial design to live pages

## Status
- [x] Review current live pages and concept reference
- [x] Apply Concept 1 styling system in shared site CSS
- [x] Update root homepage markup to Concept 1 layout
- [x] Restyle `bio/` and `election-maps/` pages to match
- [x] Validate links/layout and append learnings

## Scope
1. Make Concept 1 the primary design for the live homepage experience.
2. Keep existing route structure (`/`, `/bio/`, `/election-maps/`, `/guesstheyear/`).
3. Preserve static hosting compatibility.

## Decisions
- Keep Tailwind loaded, but use shared custom CSS classes as the main style system for this concept.
- Add Google Fonts for editorial typography (`Merriweather`, `Source Sans 3`, `DM Sans`).
- Keep `designs/` preview pages intact for future reference.

## Validation plan
- Verify the three primary cards still point to `bio/`, `election-maps/`, and `guesstheyear/`.
- Verify `bio/` and `election-maps/` use the same editorial visual language.

## Validation notes
- Confirmed homepage links:
  - `bio/`
  - `election-maps/`
  - `guesstheyear/`
  - `designs/`
- Confirmed shared editorial styles are active on root, bio, and election maps pages (`site-kicker`, `editorial-card`, serif/sans font pair).
