// Bootstrap: load assets, wire the mode toggle, daily dropdown, auto-solve and
// scrub controls. All solving happens client-side (no server).

import { loadAssets, testableDays } from "./data.js";
import { createManualUI, createClueGridUI } from "../manual.js";
import { initDailyMode } from "./daily-mode.js";
import { initManualMode } from "./manual-mode.js";

const $ = (id) => document.getElementById(id);

let state = null;
let manual = null;
let clueUI = null;
let daily = null;
let manualCtl = null;
let mode = "daily";
let dailyGrid = null; // the loaded daily's clue grid (to detect leaving daily mode)

boot();

async function boot() {
  try {
    state = await loadAssets((msg) => { $("loading").textContent = msg; });
  } catch (e) {
    $("loading").innerHTML = `<span class="bad">Failed to load assets: ${e.message}</span>`;
    return;
  }
  $("loading").style.display = "none";
  $("app").style.display = "";
  initUI();
}

function uiEls() {
  return {
    slotsEl: $("result-slots"),
    suggestEl: $("result-suggest"),
    moveTableEl: $("result-autosolve"),
    statusEl: $("result-status"),
    solveToEndBtn: $("solve-to-end"),
    nextTurnBtn: $("next-turn"),
    prevBtn: $("scrub-prev"),
    nextBtn: $("scrub-next"),
    sliderEl: $("scrub-slider"),
    expandedToggle: $("expanded-toggle"),
  };
}

function initUI() {
  // Editing the clue grid by hand exits daily mode.
  clueUI = createClueGridUI($("manual-cluegrid"), onManualEdit);
  manual = createManualUI($("manual-grid"), onManualEdit);

  daily = initDailyMode(state, manual, clueUI, uiEls());
  manualCtl = initManualMode(state, manual, clueUI, uiEls());

  // Mode toggle.
  document.querySelectorAll('input[name="mode"]').forEach((r) => {
    r.addEventListener("change", () => setMode(r.value));
  });

  // Daily controls.
  populateDailyDropdown();
  $("daily-select").addEventListener("change", (e) => {
    if (e.target.value !== "") loadDaily(+e.target.value);
  });
  $("solve-to-end").addEventListener("click", () => daily.solveToEnd());
  $("next-turn").addEventListener("click", () => daily.nextTurn());
  // Changing the probe word-set invalidates the current result. In daily mode,
  // reset so the next Solve re-runs; in manual mode, re-suggest immediately.
  $("expanded-toggle").addEventListener("change", () => {
    if (mode === "daily") daily.resetSolve();
    else manualCtl.refreshSuggest();
  });

  // Manual controls. Solving is on-demand (typing does NOT re-solve every key);
  // manual mode has no whole-game auto-solve (it doesn't know the real answers).
  $("manual-suggest").addEventListener("click", () => manualCtl.onEdit());
  // Reset clears the manual boards, clue grid and results but stays in manual mode.
  $("manual-reset").addEventListener("click", () => manualCtl.reset());

  // Scrub controls (daily's step-through; manual has none, hence optional calls).
  $("scrub-prev").addEventListener("click", () => activeCtl().prev?.());
  $("scrub-next").addEventListener("click", () => activeCtl().next?.());
  $("scrub-slider").addEventListener("input", (e) => activeCtl().scrubTo?.(e.target.value));

  setMode("daily");
}

function activeCtl() {
  return mode === "daily" ? daily : manualCtl;
}

function setMode(m) {
  mode = m;
  daily?.stop?.(); // halt any in-flight Solve playback before switching mode
  const isDaily = m === "daily";
  $("daily-section").style.display = isDaily ? "" : "none";
  $("manual-section").style.display = isDaily ? "none" : "";
  // Daily boards are solver-driven and display-only — hide the manual "+ guess"
  // and make the whole boards/clue-grid subtree read-only (inert: no clicks,
  // colour-cycling, or keyboard focus; the solver still updates it in code).
  $("app").classList.toggle("mode-daily", isDaily);
  const boards = document.querySelector(".boards");
  if (boards) boards.inert = isDaily;
  document.querySelector(`input[name="mode"][value="${m}"]`).checked = true;
  // Manual mode carries over the current boards/clue grid (so you can take over a
  // daily mid-game) but does NOT solve on entry — solving is on demand only, via
  // "Suggest next guess". (Auto-solving here would also freeze on a completed
  // daily, replaying every prefix's suggestion up front.)
  if (!isDaily) manualCtl.notifyReady();
}

function gridMatches(a, b) {
  if (!a || !b) return false;
  for (let i = 0; i < 4; i++) if (a[i] !== b[i]) return false;
  return true;
}

// A hand edit while in daily mode means we've left it — drop to manual. In
// manual mode, edits do NOT auto-solve (full-pool re-solve on every keystroke is
// slow) — the user clicks "Suggest next guess" to solve on demand.
function onManualEdit() {
  if (mode === "daily") {
    if (dailyGrid && gridMatches(clueUI.getClueGrid(), dailyGrid)) return; // still the daily
    setMode("manual");
  }
}

async function loadDaily(day) {
  await daily.loadDay(day);
  dailyGrid = clueUI.getClueGrid();
}

async function populateDailyDropdown() {
  const sel = $("daily-select");
  const days = await testableDays(state);
  const fmt = (iso) =>
    new Date(iso + "T00:00:00").toLocaleDateString("en-GB", {
      weekday: "short", day: "numeric", month: "short", year: "numeric",
    });
  const opts = ['<option value="">Load daily…</option>'];
  for (const d of days) {
    opts.push(`<option value="${d}">${fmt(isoForDay(d))} · #${d}</option>`);
  }
  sel.innerHTML = opts.join("");
}

// ISO date for a day (mirrors EPOCH + day, local).
function isoForDay(day) {
  const dt = new Date(new Date(2022, 10, 25).getTime() + day * 86400000);
  const mo = String(dt.getMonth() + 1).padStart(2, "0");
  const da = String(dt.getDate()).padStart(2, "0");
  return `${dt.getFullYear()}-${mo}-${da}`;
}
