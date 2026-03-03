# 004 Three style concept pages for homepage direction

## Status
- [x] Confirm root-site context and existing plans
- [x] Create 3 distinct static concept pages
- [x] Add a concept switch/index page and link from root homepage
- [x] Validate files/links and append learnings

## Scope
1. Implement three separate visual directions from prior suggestions.
2. Keep pages static and GitHub Pages compatible.
3. Provide a fast comparison flow for choosing a direction.

## Decisions
- Create `designs/` with one page per concept.
- Add shared concept styling in `site/design-showcase.css`.
- Keep existing production homepage content intact except adding a link to concepts.

## Validation plan
- Verify all design pages exist and are linked from `designs/index.html`.
- Verify root homepage includes link to `designs/`.

## Validation notes
- Created concept previews:
  - `designs/editorial.html`
  - `designs/atlas.html`
  - `designs/studio.html`
  - `designs/index.html` selector page
- Added homepage link to concept selector: `index.html` -> `designs/`.
- Verified links to core destinations (`bio/`, `election-maps/`, `guesstheyear/`) from each concept page.
