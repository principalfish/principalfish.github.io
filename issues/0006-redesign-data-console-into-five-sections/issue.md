# Redesign the data console into five uniform sections

The data console has grown page by page. It has no navigation, so the home page is the only way around. "Run model", "outputs" and "import" each have three different URL shapes, Westminster, Holyrood and US do the same jobs in different ways, and long runs block the page and lose their output once you leave.

This redesigns it around five uniform sections: Westminster, Holyrood, House, Senate and President. Each offers the same actions: automatic poll import, view polls, run models with parameters, view model runs, and delete. Single-poll import stays for Westminster, and section-specific tools such as by-election import stay where they belong. A global export page lets you tick sections and what to write, and rebuild, backup and restore move to an admin page. Templates are consolidated, URLs made consistent, and destructive actions get confirm steps.

Model runs move to the background with live progress, and every run's output is kept in a log. Each run form gets an "export when done" tick box, on by default.

Every existing diagnostic number stays visible, along with the safeguards around imports and destructive actions. Full stage-by-stage diagnostics for Holyrood and US are a separate follow-up issue. This builds on the folder restructure in #82.

## Plan

_2026-11-11 · 12 of 12 pieces done · now: review fixes (round 1) in progress_

- [x] **1/12 Section registry and internal layout** · `0b98a7b1`
  Define the five sections in one place and reorganise the console's code, with no visible change.
- [x] **2/12 Navigation and section pages** · `2178b777`
  Add navigation everywhere and a landing page per section; home becomes links only.
- [x] **3/12 Template and route consolidation** · `4a7a8b9e`
  Merge near-duplicate pages and routes.
- [x] **4/12 One URL shape** · `572cee15`
  Give every section the same URLs.
- [x] **5/12 Section parity** · `c237fea0`
  Filtered polls, per-chamber US runs and imports, and export-when-done on each run form.
- [x] **6/12 Global export page** · `3809a18c`
  One page to tick sections and what to write, with dry run on by default.
- [x] **7/12 Admin page, confirms and small fixes** · `40e84df0`
  Gather rebuild, backup and restore, and add confirm steps to destructive actions.
- [x] **8/12 Looks and readability** · `73df3a2c`
  Consistent styling, tables and spacing, in light and dark.
- [x] **9/12 Run log** · `0c45b65b`
  Keep every run's output, viewable afterwards.
- [x] **10/12 Background runs with live progress** · `c0700844`
  Runs stop blocking the page and show progress as they go.
- [x] **11/12 Docs** · `7afd0b84`
  Document the new layout, export page and run log.
- [x] **12/12 Favicon** · `58ce3da6`, added 2026-11-09: the user asked for it as a final step
  Give the console its own icon in the browser tab.
- [ ] **Review fixes (round 1)** · in progress: 13 findings from the self review
