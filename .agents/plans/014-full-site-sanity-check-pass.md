# 014 Full site sanity check pass

## Status
- [x] Establish baseline diagnostics for static site/runtime files.
- [x] Check for broken local asset references and stale route references.
- [x] Apply minimal fixes for any concrete issues found.
- [x] Re-run validations and document outcomes.

## Scope
- Root static pages (`index.html`, `bio/index.html`, `404.html`, `electionmaps/index.html`, `guesstheyear/index.html`).
- Shared site assets (`site/*.js`, `site/*.css`, `site/vendor/*`).
- Election maps runtime entry (`electionmaps/electionmaps.js`) and startup assets.

## Validation plan
- `get_errors` over key page/script/style files.
- Workspace scan for broken local references and stale legacy route references.
- Focused checks after any edits and concise learnings update.

## Validation notes
- `get_errors` returned no issues for core static entry files and shared site assets (`index`, `bio`, `404`, `electionmaps`, `guesstheyear`, `site/*`, `electionmaps/electionmaps.js`).
- Local-reference audit of public pages (`/`, `/bio`, `/404`, `/electionmaps`, `/guesstheyear`) reported `0` missing local refs, including JS module imports and CSS `url(...)` assets.
- Legacy `election-maps` string matches are now confined to historical `.agents` documentation; no active runtime references remain.
- One user-visible draft artifact was found and fixed: replaced literal `TODO` text in `bio/index.html` with production-safe placeholder copy.
