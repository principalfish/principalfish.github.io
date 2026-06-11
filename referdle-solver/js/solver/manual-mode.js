// Manual mode controller — the user inputs guesses + observed colours (and the
// clue grid) themselves; nothing is auto-coloured and no answers are known.
// "Suggest next guess" solves the current state and shows: the possible words
// per board, the ranked next guess (+ top-5 per word), and a move list of the
// entered guesses you can scrub through. No whole-game auto-solve (manual mode
// doesn't know the answers, so it can't honestly play a game out).

import { solveRelaxed } from "./solver.js";
import { bestGuessAcrossBoards, topGuessesForBoard } from "./suggest.js";
import { STRATEGY } from "./strategy.js";
import {
  buildOverlay, renderWordLists, renderStepSuggest, renderAutoSolveTable, perWordTopHTML,
} from "./render.js";

export function initManualMode(state, manual, clueUI, uiEls) {
  let lastAutoSolve = null;
  let activeIdx = -1;
  // Cache of the last replay ({ clueKey, moves, steps }) so a re-press reuses the
  // unchanged prefix instead of re-solving every move from scratch.
  let replayCache = null;

  const st = {
    PM: state.PM, N: state.N, POOL: state.POOL, poolIndex: state.poolIndex,
    ALL_GUESSES: state.ALL_GUESSES, PLURALS: state.PLURALS,
  };

  const status = (m) => { if (uiEls.statusEl) uiEls.statusEl.textContent = m; };
  // "Set" only when a tile is actually coloured (yellow/green). A default
  // all-gray grid carries no signal — and solving it from empty boards would
  // branch over the whole pool of finals (a freeze), so treat it as unset.
  const clueIsSet = (grid) =>
    grid && grid.slice(0, 4).some((c) => typeof c === "string" && (c.includes("1") || c.includes("2")));
  const freshSlots = () => [0, 1, 2, 3, 4].map(() => ({ guesses: [] }));
  const reSolve = (slots, clueGrid) =>
    solveRelaxed(slots, clueGrid, st.POOL, st.PM, st.N, st.poolIndex, st.PLURALS, null);

  function buildSuggest(res, ctx) {
    const ranked = bestGuessAcrossBoards(res, st.PM, st.N, st.poolIndex, st.ALL_GUESSES, st.PLURALS, ctx);
    const perBoard = [];
    for (let b = 0; b < res.perSlotFeasible.length; b++) {
      const ans = res.perSlotFeasible[b];
      if (ans.length > 1) {
        const avoid = b < 3 && STRATEGY.avoid_doubles_w13;
        perBoard.push({
          board: b,
          top: topGuessesForBoard(ans, st.PM, st.N, st.poolIndex, st.ALL_GUESSES, st.PLURALS, 5, avoid, b),
        });
      }
    }
    return { solvable: true, ranked, perBoard };
  }

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

    // An all-gray clue grid is "unset" — pass null so the solver uses the fast
    // no-clue path. (A literal all-gray clue would otherwise branch the clue
    // solver over the whole final-word pool — a freeze.)
    const clueGrid = clueIsSet(rawGrid) ? rawGrid : null;

    // Build a move list from the entered guesses (board, then row order) and
    // replay it from empty, caching each prefix's solve + the next-guess
    // suggestion at that point.
    const moves = [];
    for (let b = 0; b < slots.length; b++) {
      for (const g of slots[b].guesses) {
        moves.push({ board: b, word: g.word, colors: g.colors, probe: false, expanded: false, isClosing: false });
      }
    }

    // Reuse the longest unchanged prefix from the previous solve: same clue grid
    // and identical earlier moves. Only the diverging tail is re-solved — so a
    // continued game reuses everything but the new move, while an earlier edit or
    // reset shortens the reusable prefix (down to zero → solve from the start).
    const clueKey = clueGrid ? clueGrid.join("|") : "";
    let reuse = 0;
    if (replayCache && replayCache.clueKey === clueKey) {
      const cm = replayCache.moves;
      while (reuse < cm.length && reuse < moves.length &&
             cm[reuse].board === moves[reuse].board &&
             cm[reuse].word === moves[reuse].word &&
             cm[reuse].colors === moves[reuse].colors) {
        reuse++;
      }
    }

    const startSlots = freshSlots();
    const stateSlots = freshSlots();
    const steps = [];
    for (let i = 0; i < reuse; i++) {                 // unchanged prefix — no re-solve
      moves[i].setSize = replayCache.moves[i].setSize;
      moves[i].expRemaining = replayCache.moves[i].expRemaining;
      steps.push(replayCache.steps[i]);
      stateSlots[moves[i].board].guesses.push({ word: moves[i].word, colors: moves[i].colors });
    }
    for (let i = reuse; i < moves.length; i++) {      // diverging tail — solve from here
      const m = moves[i];
      const before = reSolve(stateSlots, clueGrid);
      m.setSize = before.solvable ? before.perSlotFeasible[m.board].length : null;
      stateSlots[m.board].guesses.push({ word: m.word, colors: m.colors });
      const after = reSolve(stateSlots, clueGrid);
      m.expRemaining = after.solvable ? after.perSlotFeasible[m.board].length : null;
      // Suggestion is computed lazily (in jumpToMove) — building it for every
      // prefix up front is O(n²)+lookahead per step, which freezes on a long /
      // completed game where intermediate boards still hold huge candidate sets.
      steps.push({ after, suggest: undefined });
    }
    replayCache = { clueKey, moves, steps };

    // The full final state equals the last replayed prefix, so reuse its solve
    // rather than re-solving the whole board again. (No moves → solve clue only.)
    const res = moves.length ? steps[steps.length - 1].after : reSolve(slots, clueGrid);
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
      renderSuggestStandalone(buildSuggest(res, { slots, clueGrid, pool: st.POOL }));
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

  function reconstructSlots(startSlots, moves, upto) {
    const slots = startSlots.map((s) => ({ guesses: (s.guesses || []).map((g) => ({ ...g })) }));
    for (let i = 0; i < upto; i++) {
      const m = moves[i];
      slots[m.board].guesses.push({ word: m.word, colors: m.colors });
    }
    return slots;
  }

  function jumpToMove(K) {
    if (!lastAutoSolve) return;
    const { startSlots, moves, steps } = lastAutoSolve;
    activeIdx = K;
    const slotsAfter = reconstructSlots(startSlots, moves, K + 1);
    const rawGrid = clueUI.getClueGrid();
    const clueGrid = clueIsSet(rawGrid) ? rawGrid : null;
    const step = steps[K];
    if (step.suggest === undefined) {
      step.suggest = step.after.solvable
        ? buildSuggest(step.after, { slots: slotsAfter, clueGrid, pool: st.POOL })
        : { solvable: false };
    }

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
    replayCache = null;
  }

  // Full reset of the manual state — clears the boards, the clue grid, the
  // results and the solve cache, but STAYS in manual mode (no page reload).
  function reset() {
    manual.reset();
    clueUI.setClueGrid(null);
    clearAll();
    status('Enter a game state, then "Suggest next guess".');
  }

  return { onEdit, notifyReady, reset, prev, next, scrubTo };
}
