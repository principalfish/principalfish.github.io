// Manual mode controller — the user inputs guesses + observed colours (and the
// clue grid) themselves; nothing is auto-coloured and no answers are known.
// "Suggest next guess" solves the current state and shows: the possible words
// per board, the ranked next guess (+ top-5 per word), and a move list of the
// entered guesses you can scrub through. No whole-game auto-solve (manual mode
// doesn't know the answers, so it can't honestly play a game out).

import { createComputeService } from "./compute-service.js";
import { clueIsSet } from "./analysis.js";
import {
  buildOverlay, renderWordLists, renderStepSuggest, renderAutoSolveTable, perWordTopHTML,
} from "./render.js";

export function initManualMode(state, manual, clueUI, uiEls, compute = createComputeService(state)) {
  let lastAutoSolve = null;
  let activeIdx = -1;
  const status = (m) => { if (uiEls.statusEl) uiEls.statusEl.textContent = m; };
  const expanded = () => !uiEls.expandedToggle || uiEls.expandedToggle.checked;

  // Entering manual mode: keep the carried-over board, but clear any leftover
  // daily results and wait for the user to ask. No solving until "Suggest".
  function notifyReady() {
    clearAll();
    const slots = manual.getSlots();
    const hasAny = slots.some((s) => s.guesses.length > 0) || clueIsSet(clueUI.getClueGrid());
    status(hasAny
      ? 'Manual mode — press "Suggest next guess" to analyse, or edit the board.'
      : 'Enter a game state, then "Suggest next guess".');
  }

  async function onEdit() {
    // Refuse on a half-typed word — a partial row carries no valid guess and
    // would otherwise be silently dropped.
    const incomplete = manual.incompleteBoards ? manual.incompleteBoards() : [];
    if (incomplete.length) {
      const list = incomplete.map((b) => `board ${b}`).join(", ");
      const plural = incomplete.length > 1 ? "words" : "word";
      status(`Incomplete ${plural} on ${list} — each guess needs all 5 letters. Finish or clear it, then try again.`);
      return;
    }
    const slots = manual.getSlots();
    const rawGrid = clueUI.getClueGrid();
    const hasAny = slots.some((s) => s.guesses.length > 0) || clueIsSet(rawGrid);
    if (!hasAny) {
      status('Enter a game state, then "Suggest next guess".');
      clearAll();
      return;
    }

    // The solve is synchronous and can be heavy — flag it and let the status
    // paint before the work blocks the thread.
    status("Solving…");
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

    const reply = compute.request({ type: "analyse-manual", slots, clueGrid: rawGrid, expanded: expanded() });
    const { result: res, clueGrid, startSlots, moves, steps } = reply;
    clueUI.setResults(buildOverlay(slots, res));
    if (!res.solvable) {
      status(`Unsolvable. ${res.reason || ""}`);
      uiEls.slotsEl.innerHTML = "";
      uiEls.suggestEl.innerHTML = "";
      uiEls.moveTableEl.innerHTML = "";
      disableScrub();
      lastAutoSolve = null;
      return;
    }
    const clueNote = res.clueUsed ? ` · ${res.viableFinals.length} possible final word(s)` : "";
    status(`Solvable · ${res.unionFeasible.length} word(s) can appear${clueNote}.`);
    renderWordLists(uiEls.slotsEl, slots, clueGrid, res);

    if (moves.length) {
      lastAutoSolve = {
        game: { manual: true, solved: false, completedCount: moves.length, guessCount: moves.length, words: null },
        startSlots, moves, steps,
        source: "Replaying your entered guesses.", ms: null,
      };
      setupScrub(moves.length);
      jumpToMove(moves.length - 1);
    } else {
      // Clue grid only — no move list; show the suggestion panel directly.
      lastAutoSolve = null;
      disableScrub();
      uiEls.moveTableEl.innerHTML = "";
      renderSuggestStandalone(reply.suggest);
    }
  }

  function renderSuggestStandalone(sug) {
    const ranked = sug.ranked || [];
    if (!ranked.length) {
      uiEls.suggestEl.innerHTML =
        `<div class="panel"><span class="good">All words pinned — nothing left to guess.</span></div>`;
      return;
    }
    const fmtExp = (x) => (x == null ? "opener" : `~${x.toFixed(1)}`);
    const top = ranked[0];
    const rows = ranked
      .map((r) =>
        `<tr><td><b>${r.word}</b></td><td>Word ${r.board + 1}</td>` +
        `<td>${r.setSize}</td><td>${fmtExp(r.expRemaining)}</td></tr>`)
      .join("");
    const perWord = perWordTopHTML(sug.perBoard, (b, w) => b === top.board && w === top.word);
    uiEls.suggestEl.innerHTML =
      `<div class="panel"><h3>Suggested next guess</h3>` +
      `<p>Guess <b>${top.word}</b> on <b>Word ${top.board + 1}</b> — leaves ` +
      `${fmtExp(top.expRemaining)} of ${top.setSize}.</p>` +
      `<table class="suggest-table"><thead><tr><th>word</th><th>board</th>` +
      `<th>now</th><th>leaves</th></tr></thead><tbody>${rows}</tbody></table>${perWord}</div>`;
  }

  // --- scrub + render -----------------------------------------------------------

  function setupScrub(n) {
    const s = uiEls.sliderEl;
    if (s) { s.min = 0; s.max = Math.max(0, n - 1); s.value = 0; s.disabled = n === 0; }
    if (uiEls.prevBtn) uiEls.prevBtn.disabled = n === 0;
    if (uiEls.nextBtn) uiEls.nextBtn.disabled = n === 0;
  }
  function disableScrub() { setupScrub(0); }

  function jumpToMove(K) {
    if (!lastAutoSolve) return;
    const { moves, steps } = lastAutoSolve;
    activeIdx = K;
    const reply = compute.request({ type: "replay-manual", index: K, clueGrid: clueUI.getClueGrid(), expanded: expanded() });
    const { slots: slotsAfter, clueGrid, step } = reply;
    moves[K] = reply.move;
    steps[K] = step;

    manual.reset();
    slotsAfter.forEach((s, b) => s.guesses.forEach((g) => manual.addGuess(b, g.word, g.colors)));
    clueUI.setResults(buildOverlay(slotsAfter, step.after));
    if (step.after.solvable) {
      renderWordLists(uiEls.slotsEl, slotsAfter, clueGrid, step.after);
    }
    renderStepSuggest(uiEls.suggestEl, K, moves[K], step.suggest, moves.length, "Played", K === moves.length - 1);
    renderTable();
    if (uiEls.sliderEl) uiEls.sliderEl.value = K;
  }

  function renderTable() {
    renderAutoSolveTable(uiEls.moveTableEl, lastAutoSolve, activeIdx, jumpToMove);
  }

  function prev() { if (lastAutoSolve && activeIdx > 0) jumpToMove(activeIdx - 1); }
  function next() {
    if (lastAutoSolve && activeIdx < lastAutoSolve.moves.length - 1) jumpToMove(activeIdx + 1);
  }
  function scrubTo(v) { if (lastAutoSolve) jumpToMove(+v); }

  function clearAll() {
    if (clueUI) clueUI.setResults(null);
    uiEls.slotsEl.innerHTML = "";
    uiEls.suggestEl.innerHTML = "";
    uiEls.moveTableEl.innerHTML = "";
    disableScrub();
    lastAutoSolve = null;
    activeIdx = -1;
    compute.request({ type: "reset-manual" });
  }

  // The probe word-set toggle changed — cached suggestions are stale (they rank
  // probes from the old set), so drop the cache and re-suggest with the new set.
  function refreshSuggest() {
    compute.request({ type: "reset-manual" });
    return onEdit();
  }

  // Full reset of the manual state — clears the boards, the clue grid, the
  // results and the solve cache, but STAYS in manual mode (no page reload).
  function reset() {
    manual.reset();
    clueUI.setClueGrid(null);
    clearAll();
    status('Enter a game state, then "Suggest next guess".');
  }

  return { onEdit, notifyReady, reset, refreshSuggest, prev, next, scrubTo };
}
