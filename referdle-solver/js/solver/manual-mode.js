// Manual mode controller — the user inputs guesses + observed colours (and the
// clue grid) themselves; nothing is auto-coloured and no answers are known.
// "Suggest next guess" solves the current state and shows: the possible words
// per board, the ranked next guess (+ top-5 per word), and a move list of the
// entered guesses you can scrub through. No whole-game auto-solve (manual mode
// doesn't know the answers, so it can't honestly play a game out).

import { createComputeService } from "./compute-service.js";
import { clueIsSet } from "./analysis.js";
import { isCancelled } from "./worker-client.js";
import {
  buildOverlay, renderWordLists, renderStepSuggest, renderAutoSolveTable, perWordTopHTML,
} from "./render.js";

export function initManualMode(state, manual, clueUI, uiEls, compute = createComputeService(state)) {
  let lastAutoSolve = null;
  let activeIdx = -1;
  let revision = 0;
  let viewToken = 0;
  let busy = null;
  let acceptedInput = null;
  const invalidate = () => {
    revision++;
    viewToken++;
    busy = null;
    compute.cancel?.();
  };
  const status = (m) => { if (uiEls.statusEl) uiEls.statusEl.textContent = m; };
  const expanded = () => !uiEls.expandedToggle || uiEls.expandedToggle.checked;

  // Entering manual mode: keep the carried-over board, but clear any leftover
  // daily results and wait for the user to ask. No solving until "Suggest".
  function notifyReady() {
    invalidate();
    clearAll();
    const slots = manual.getSlots();
    const hasAny = slots.some((s) => s.guesses.length > 0) || clueIsSet(clueUI.getClueGrid());
    status(hasAny
      ? 'Manual mode — press "Suggest next guess" to analyse, or edit the board.'
      : 'Enter a game state, then "Suggest next guess".');
  }

  async function onEdit() {
    invalidate();
    const token = revision;
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

    status("Solving…");
    const owner = { token };
    busy = owner;
    try {
      const reply = await compute.request({ type: "analyse-manual", slots, clueGrid: rawGrid,
        expanded: expanded(), compact: true });
      if (token !== revision || busy !== owner) return false;
      acceptedInput = { slots: structuredClone(slots), clueGrid: structuredClone(rawGrid), expanded: expanded() };
      compute.acceptManual?.(acceptedInput);
      const { result: res, clueGrid, startSlots, moves } = reply;
      const steps = reply.steps || moves.map((m, i) => i < reply.reuse && lastAutoSolve
        ? lastAutoSolve.steps[i] : { after: undefined, suggest: undefined });
      if (moves.length && reply.finalStep) steps[steps.length - 1] = reply.finalStep;
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
        busy = null;
        await jumpToMove(moves.length - 1);
      } else {
        // Clue grid only — no move list; show the suggestion panel directly.
        lastAutoSolve = null;
        disableScrub();
        uiEls.moveTableEl.innerHTML = "";
        renderSuggestStandalone(reply.suggest);
      }
      return true;
    } catch (error) {
      if (token === revision && !isCancelled(error)) status(`Calculation failed: ${error.message} Press "Suggest next guess" to retry.`);
      return false;
    } finally {
      if (busy === owner) busy = null;
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

  async function jumpToMove(K) {
    if (!lastAutoSolve || !Number.isInteger(K) || K < 0 || K >= lastAutoSolve.moves.length) return;
    compute.cancel?.();
    const token = ++viewToken;
    const inputRevision = revision;
    const owner = { token };
    busy = owner;
    const { moves, steps } = lastAutoSolve;
    activeIdx = K;
    status(`Calculating move ${K + 1}…`);
    try {
      const reply = await compute.request({ type: "replay-manual", index: K,
        clueGrid: acceptedInput.clueGrid, expanded: acceptedInput.expanded });
      if (token !== viewToken || inputRevision !== revision || busy !== owner) return false;
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
      status(`Move ${K + 1} / ${moves.length}`);
      return true;
    } catch (error) {
      if (token === viewToken && inputRevision === revision && !isCancelled(error)) status(`Replay failed: ${error.message} Select the move again to retry.`);
      return false;
    } finally {
      if (busy === owner) busy = null;
    }
  }

  function renderTable() {
    renderAutoSolveTable(uiEls.moveTableEl, lastAutoSolve, activeIdx, jumpToMove);
  }

  function prev() { if (lastAutoSolve && activeIdx > 0) return jumpToMove(activeIdx - 1); }
  function next() {
    if (lastAutoSolve && activeIdx < lastAutoSolve.moves.length - 1) return jumpToMove(activeIdx + 1);
  }
  function scrubTo(v) { if (lastAutoSolve) return jumpToMove(+v); }

  function clearAll() {
    if (clueUI) clueUI.setResults(null);
    uiEls.slotsEl.innerHTML = "";
    uiEls.suggestEl.innerHTML = "";
    uiEls.moveTableEl.innerHTML = "";
    disableScrub();
    lastAutoSolve = null;
    activeIdx = -1;
    acceptedInput = null;
    if (compute.resetManual) compute.resetManual();
    else compute.request({ type: "reset-manual" });
  }

  // The probe word-set toggle changed — cached suggestions are stale (they rank
  // probes from the old set), so drop the cache and re-suggest with the new set.
  function refreshSuggest() {
    invalidate();
    clearAll();
    return onEdit();
  }

  // Full reset of the manual state — clears the boards, the clue grid, the
  // results and the solve cache, but STAYS in manual mode (no page reload).
  function reset() {
    invalidate();
    manual.reset();
    clueUI.setClueGrid(null);
    clearAll();
    status('Enter a game state, then "Suggest next guess".');
  }

  function inputsChanged() {
    invalidate();
    clearAll();
    status("Inputs changed — press \"Suggest next guess\" to analyse.");
  }

  return { onEdit, notifyReady, reset, refreshSuggest, prev, next, scrubTo, inputsChanged, stop: invalidate };
}
