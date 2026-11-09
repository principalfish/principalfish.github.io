# Redesign the data console into five uniform sections

The data console has grown page by page. It has no navigation, so the home page is the only way around. "Run model", "outputs" and "import" each have three different URL shapes, Westminster, Holyrood and US do the same jobs in different ways, and long runs block the page and lose their output once you leave.

This redesigns it around five uniform sections: Westminster, Holyrood, House, Senate and President. Each offers the same actions: automatic poll import, view polls, run models with parameters, view model runs, and delete. Single-poll import stays for Westminster, and section-specific tools such as by-election import stay where they belong. A global export page lets you tick sections and what to write, and rebuild, backup and restore move to an admin page. Templates are consolidated, URLs made consistent, and destructive actions get confirm steps.

Model runs move to the background with live progress, and every run's output is kept in a log. Each run form gets an "export when done" tick box, on by default.

Every existing diagnostic number stays visible, along with the safeguards around imports and destructive actions. Full stage-by-stage diagnostics for Holyrood and US are a separate follow-up issue. This builds on the folder restructure in #82.

## Plan

_2026-11-09 · 2 of 11 pieces done · now: waiting on a decision_

- [x] **1/11 Section registry and internal layout** · `0b98a7b1`
  Define the five sections in one place and reorganise the console's code, with no visible change.
- [x] **2/11 Navigation and section pages** · `2178b777`
  Add navigation everywhere and a landing page per section; home becomes links only.
- [ ] **3/11 Template and route consolidation** · not started
  Merge near-duplicate pages and routes.
- [ ] **4/11 One URL shape** · not started
  Give every section the same URLs, with redirects for old pages.
- [ ] **5/11 Section parity** · not started
  Filtered polls, per-chamber US runs and imports, and export-when-done on each run form.
- [ ] **6/11 Global export page** · not started
  One page to tick sections and what to write, with dry run on by default.
- [ ] **7/11 Admin page, confirms and small fixes** · not started
  Gather rebuild, backup and restore, and add confirm steps to destructive actions.
- [ ] **8/11 Looks and readability** · not started
  Consistent styling, tables and spacing, in light and dark.
- [ ] **9/11 Run log** · not started
  Keep every run's output, viewable afterwards.
- [ ] **10/11 Background runs with live progress** · not started
  Runs stop blocking the page and show progress as they go.
- [ ] **11/11 Docs** · not started
  Document the new layout, export page and run log.
