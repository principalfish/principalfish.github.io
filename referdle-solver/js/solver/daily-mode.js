// Daily mode controller — load a bundled daily, then drive the solver two ways:
//   • Solve     — auto-play the whole game, then scrub the move sequence.
//   • Next turn — advance exactly ONE solver move per press (one solve, not the
//                 whole game), revealing the game incrementally.

import { dailyGame, dailyClueGrid } from "./data.js";
import { solve, solveRelaxed } from "./solver.js";
import { bestGuessAcrossBoards, topGuessesForBoard } from "./suggest.js";
import { getComparison } from "./compare.js";
import { STRATEGY } from "./strategy.js";
import {
  buildOverlay, renderWordLists, renderStepSuggest, renderAutoSolveTable,
} from "./render.js";

export function initDailyMode(state, manual, clueUI, uiEls) {
  let day = null;
  let words = null;
  let lastAutoSolve = null;
  let activeIdx = -1;
  let turn = null; // incremental turn-by-turn game state (null until first Next turn)

  // Animated playback ("Solve"): a short "thinking" pre-roll of rotating status
  // messages, then the computed moves are revealed on the board one at a time.
  //   • animToken — bumping it HARD-aborts an in-flight run (scrub / mode switch
  //     / load another day): the run bails WITHOUT settling on a final state.
  //   • skipRequested — a re-click of Solve mid-run; stops the animation but
  //     finalises straight to the solved board.
  let animToken = 0;
  let solving = false;
  let skipRequested = false;
  const ANIM_MS = 420;   // pause between moves during playback
  const delay = (ms) => new Promise((r) => setTimeout(r, ms));
  const cancelAnim = () => { animToken++; };

  const st = {
    PM: state.PM, N: state.N, POOL: state.POOL, poolIndex: state.poolIndex,
    ALL_GUESSES: state.ALL_GUESSES, PLURALS: state.PLURALS,
  };

  function status(msg) {
    if (uiEls.statusEl) uiEls.statusEl.textContent = msg;
  }

  async function loadDay(d) {
    cancelAnim();
    day = d;
    words = await dailyGame(state, d);
    if (!words) { status(`No bundled puzzle for day ${d}.`); return; }
    const grid = dailyClueGrid(words);
    clueUI.setClueGrid(grid);
    manual.reset();
    lastAutoSolve = null;
    turn = null;
    activeIdx = -1;
    clearPanels();
    setupScrub(0); // reset the progress bar / scrub from any previous game
    status(`Loaded daily #${d}. Click "Solve" to auto-play, or "Next turn" to step.`);
    enableControls(true);
  }

  function clearPanels() {
    uiEls.slotsEl.innerHTML = "";
    uiEls.suggestEl.innerHTML = "";
    uiEls.moveTableEl.innerHTML = "";
  }

  function enableControls(on) {
    if (uiEls.solveToEndBtn) uiEls.solveToEndBtn.disabled = !on;
    if (uiEls.nextTurnBtn) uiEls.nextTurnBtn.disabled = !on;
  }

  function freshSlots() {
    return [0, 1, 2, 3, 4].map(() => ({ guesses: [] }));
  }

  // --- Solve (whole game, then scrub) -------------------------------------------

  // Play the whole game by driving the real per-move engine (nextTurn) one move
  // at a time, yielding between moves so each genuinely-computed move paints.
  // (A single synchronous autoSolve can't show progress — the browser can't
  // repaint until it returns. Chunking at one-move granularity is as live as a
  // single thread gets; we can't paint *within* one move's solve.)
  async function solveToEnd() {
    if (!words) return;
    // Already solved (whole-game or stepped to the end) — just re-show the final
    // state; don't recompute the game.
    if (lastAutoSolve && lastAutoSolve.game && lastAutoSolve.game.solved) {
      cancelAnim();
      jumpToMove(lastAutoSolve.moves.length - 1);
      summaryStatus();
      return;
    }
    // A re-click while a run is underway means "skip the pacing, jump to end".
    if (solving) { skipRequested = true; return; }

    solving = true;
    skipRequested = false;
    const myToken = ++animToken;
    const aborted = () => myToken !== animToken; // hard stop (scrub / mode switch / load)
    if (uiEls.nextTurnBtn) uiEls.nextTurnBtn.disabled = true;

    try {
      status("Solving… reading the clue grid");
      await delay(0); // paint the marker before the first move blocks the thread

      let safety = 0;
      while (safety++ < 80) {
        if (aborted()) return;
        if (turn && turn.done) break;

        // Compute exactly ONE move. nextTurn() renders it live via finishStep.
        nextTurn();
        if (aborted()) return;

        const n = turn ? turn.moves.length : 0;
        if (turn && turn.done) break;

        if (!skipRequested) {
          // Describe the real move just played, and flag what's next.
          status(`${describeMove(turn.moves[n - 1], n)} — computing next…`);
          await delay(ANIM_MS);
        }
      }
      if (aborted()) return;
      summaryStatus();
    } finally {
      solving = false;
      if (uiEls.nextTurnBtn) uiEls.nextTurnBtn.disabled = false;
    }
  }

  // One-line description of an actual played move, for the live status line.
  function describeMove(m, n) {
    if (!m) return `Working out move ${n}`;
    const what = m.isClosing ? "locking in" : (m.probe ? "probing" : "guessing");
    return `Move ${n}: ${what} ${m.word} on board ${m.board + 1}`;
  }

  // Final one-line summary after a whole-game solve / skip.
  function summaryStatus() {
    if (!lastAutoSolve) return;
    const total = lastAutoSolve.moves.length;
    const guesses = lastAutoSolve.game ? lastAutoSolve.game.guessCount : total;
    const closing = total - guesses;
    const solved = lastAutoSolve.game && lastAutoSolve.game.solved;
    const breakdown = closing > 0 ? ` (${guesses} guesses + ${closing} closing)` : "";
    status(solved
      ? `Daily #${day} solved in ${total} move${total === 1 ? "" : "s"}${breakdown}.`
      : `Daily #${day} — ${total} move${total === 1 ? "" : "s"} played (not fully solved).`);
  }

  // --- Next turn (one solver move per press) ------------------------------------

  function allPinned(res) {
    return res.solvable && res.perSlotFeasible.every((s) => s.length === 1);
  }

  function turnSolve(p13, p5, pc) {
    return solve(turn.slots, turn.grid, st.POOL, st.PM, st.N, st.poolIndex, st.PLURALS, p13, p5, pc, null);
  }

  // Per-board top-5 (probes included when one wins) for the state-before-move panel.
  function buildSuggest(res, ranked) {
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

  function rank(res) {
    return bestGuessAcrossBoards(res, st.PM, st.N, st.poolIndex, st.ALL_GUESSES, st.PLURALS);
  }

  // After-move solve (relaxed) for the overlay + word-list panels.
  function afterSolve() {
    return solveRelaxed(turn.slots, turn.grid, st.POOL, st.PM, st.N, st.poolIndex, st.PLURALS, null);
  }

  function startClosing() {
    const lastOn = {};
    for (const m of turn.moves) lastOn[m.board] = m.word;
    turn.closingQueue = [];
    for (let b = 0; b < 5; b++) if (lastOn[b] !== words[b]) turn.closingQueue.push(b);
    if (!turn.closingQueue.length) turn.done = true;
  }

  function playClosing() {
    if (!turn.closingQueue) startClosing();
    if (!turn.closingQueue.length) { turn.done = true; finishStep(); return; }
    const b = turn.closingQueue.shift();
    const before = afterSolve();
    const suggest = before.solvable ? buildSuggest(before, rank(before)) : { solvable: false };
    turn.slots[b].guesses.push({ word: words[b], colors: "22222" });
    turn.moves.push({
      board: b, word: words[b], colors: "22222", probe: false, expanded: false,
      setSize: null, expRemaining: null, isClosing: true,
    });
    turn.steps.push({ after: afterSolve(), suggest });
    if (!turn.closingQueue.length) turn.done = true;
    finishStep();
  }

  function nextTurn() {
    if (!words) return;
    if (!turn) {
      turn = {
        slots: freshSlots(),
        grid: dailyClueGrid(words),
        pruneW13: STRATEGY.prune_w13_doubles,
        pruneW5: STRATEGY.prune_w5_plurals,
        pruneCombos: STRATEGY.prune_w13_combos,
        moves: [], steps: [], closingQueue: null, done: false,
      };
      lastAutoSolve = null;
      activeIdx = -1;
      manual.reset();
    }
    if (turn.done) return;
    if (turn.closingQueue) { playClosing(); return; }

    // One solve of the current state, relaxing sticky prunes only if forced.
    let res = turnSolve(turn.pruneW13, turn.pruneW5, turn.pruneCombos);
    if (!res.solvable && turn.pruneCombos) {
      turn.pruneCombos = false;
      res = turnSolve(turn.pruneW13, turn.pruneW5, false);
    }
    if (!res.solvable && (turn.pruneW13 || turn.pruneW5)) {
      turn.pruneW13 = turn.pruneW5 = false;
      res = turnSolve(false, false, false);
    }
    if (!res.solvable) { turn.done = true; finishStep(); return; }
    if (allPinned(res)) { playClosing(); return; }

    let ranked = rank(res);
    if (!ranked.length && turn.pruneCombos) {
      turn.pruneCombos = false;
      res = turnSolve(turn.pruneW13, turn.pruneW5, false);
      ranked = res.solvable ? rank(res) : [];
    }
    if (!ranked.length && (turn.pruneW13 || turn.pruneW5)) {
      turn.pruneW13 = turn.pruneW5 = false;
      res = turnSolve(false, false, false);
      ranked = res.solvable ? rank(res) : [];
    }
    if (!ranked.length) { playClosing(); return; }

    const suggest = buildSuggest(res, ranked); // state-before-move
    const top = ranked[0];
    const colors = getComparison(top.word, words[top.board]);
    turn.slots[top.board].guesses.push({ word: top.word, colors });
    turn.moves.push({
      board: top.board, word: top.word, colors,
      probe: top.probe || false,
      expanded: !!top.probe && !st.poolIndex.has(top.word),
      setSize: top.setSize, expRemaining: top.expRemaining, isClosing: false,
    });
    turn.steps.push({ after: afterSolve(), suggest });
    finishStep();
  }

  // Publish the turn's played-so-far moves as a lastAutoSolve and show the latest.
  function finishStep() {
    const guessCount = turn.moves.filter((m) => !m.isClosing).length;
    lastAutoSolve = {
      game: {
        solved: turn.done,
        inProgress: !turn.done,
        completedCount: turn.moves.length,
        guessCount,
        words,
      },
      startSlots: freshSlots(),
      moves: turn.moves,
      steps: turn.steps,
      source: `<span class="good">actual daily answers</span>`,
      ms: null,
    };
    setupScrub(turn.moves.length);
    activeIdx = -1;
    if (turn.moves.length) jumpToMove(turn.moves.length - 1);
    const n = turn.moves.length;
    if (turn.done) {
      summaryStatus();
    } else {
      status(`Daily #${day} — ${n} move${n === 1 ? "" : "s"} played. Press "Next turn" to continue.`);
    }
  }

  // --- scrubbing + rendering ----------------------------------------------------

  function setupScrub(n) {
    const slider = uiEls.sliderEl;
    if (slider) {
      slider.min = 0;
      slider.max = Math.max(0, n - 1);
      slider.value = 0;
      slider.disabled = n === 0;
    }
    if (uiEls.prevBtn) uiEls.prevBtn.disabled = n === 0;
    if (uiEls.nextBtn) uiEls.nextBtn.disabled = n === 0;
  }

  function reconstructSlots(startSlots, moves, upto) {
    const slots = (startSlots.length ? startSlots : freshSlots())
      .map((s) => ({ guesses: (s.guesses || []).map((g) => ({ ...g })) }));
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
    const clueGrid = clueUI.getClueGrid();
    const step = steps[K];

    manual.reset();
    slotsAfter.forEach((s, b) => s.guesses.forEach((g) => manual.addGuess(b, g.word, g.colors)));

    clueUI.setResults(buildOverlay(slotsAfter, step.after));
    if (step.after.solvable) {
      renderWordLists(uiEls.slotsEl, slotsAfter, clueGrid, step.after);
    }
    renderStepSuggest(uiEls.suggestEl, K, moves[K], step.suggest, moves.length);
    renderTable();
    if (uiEls.sliderEl) uiEls.sliderEl.value = K;
  }

  function renderTable() {
    renderAutoSolveTable(uiEls.moveTableEl, lastAutoSolve, activeIdx, jumpToMove);
  }

  function posStatus() {
    if (lastAutoSolve) status(`Move ${activeIdx + 1} / ${lastAutoSolve.moves.length}`);
  }
  function prev() {
    if (lastAutoSolve && activeIdx > 0) { cancelAnim(); jumpToMove(activeIdx - 1); posStatus(); }
  }
  function next() {
    if (lastAutoSolve && activeIdx < lastAutoSolve.moves.length - 1) {
      cancelAnim(); jumpToMove(activeIdx + 1); posStatus();
    }
  }
  function scrubTo(v) { if (lastAutoSolve) { cancelAnim(); jumpToMove(+v); posStatus(); } }

  return { loadDay, solveToEnd, nextTurn, jumpToMove, prev, next, scrubTo, stop: cancelAnim,
           getDay: () => day, getWords: () => words };
}
