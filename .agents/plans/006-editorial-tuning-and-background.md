# 006 Tune editorial styling and change background color

## Status
- [x] Confirm requested visual adjustments
- [x] Tune spacing/headline scale/border softness in shared CSS
- [x] Apply a new background color palette
- [x] Validate styles and append learnings

## Scope
1. Refine the live Concept 1 design details.
2. Keep page structure/routes unchanged.

## Decisions
- Perform tuning in `site/styles.css` only to keep behavior centralized.
- Keep editorial style while changing the overall background color family.

## Validation plan
- Verify updated values for spacing, title size, border opacity, and background colors in shared CSS.

## Validation notes
- Updated shared CSS values in `site/styles.css`:
  - background palette (`--paper`, `--paper-soft`)
  - tighter spacing (`site-wrap`, `top-rule`, `card-grid`, `subpage-panel`, `footer-note`)
  - larger headline scale (`site-title`)
  - softer card treatment (`--line`, `editorial-card` hover shadow)
