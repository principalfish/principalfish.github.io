// ─── US election maps — page entry ──────────────────────────────────────────
//
// Thin shell for the US page (House, Senate, President). The engine lives in
// /electionmapslogic; this page enables no extra features today, so it imports only the
// shared bootstrap and ships none of the predict / poll-tracker code. window.MAPS_PAGE is set
// inline in index.html before this module loads and is read via `page` in state.js.
//
// To add a US forecast or poll tracker later, import the relevant feature module(s) here and
// pass their hooks to startApp (mirroring electionmaps/electionmaps.js) — no engine change.

import { startApp } from '../electionmapslogic/app.js';

startApp();
