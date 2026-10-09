// Daily mode controller — load a bundled daily, then drive the solver two ways:
//   • Solve     — auto-play the whole game, then scrub the move sequence.
//   • Next turn — advance exactly ONE solver move per press (one solve, not the
//                 whole game), revealing the game incrementally.

import { createComputeService } from "./compute-service.js";
import { freshSlots, reconstructSlots } from "./engine.js";
import { dailyClueGrid } from "./data.js";
import { isCancelled } from "./worker-client.js";
import {
  buildOverlay, renderWordLists, renderStepSuggest, renderAutoSolveTable,
} from "./render.js";

export function initDailyMode(state, manual, clueUI, uiEls, compute = createComputeService(state)) {
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
  let revision = 0;
  let pendingTurn = null;
  const cancelAnim = () => {
    animToken++;
    revision++;
    solving = false;
    pendingTurn = null;
    compute.cancel?.();
    if (uiEls.nextTurnBtn) uiEls.nextTurnBtn.disabled = !words;
  };

  const expanded = () => !uiEls.expandedToggle || uiEls.expandedToggle.checked;

  function status(msg) {
    if (uiEls.statusEl) uiEls.statusEl.textContent = msg;
  }

  async function loadDay(d) {
    cancelAnim();
    const token = revision;
    day = d;
    words = null;
    enableControls(false);
    status(`Loading daily #${d}…`);
    let loaded;
    try {
      loaded = await compute.request({ type: "load-day", day: d });
      if (token !== revision) return false;
    } catch (error) {
      if (token === revision && !isCancelled(error)) status(`Failed to load daily: ${error.message} Retry by selecting the day again.`);
      return false;
    }
    words = loaded.words || null;
    if (!words) { status(`No bundled puzzle for day ${d}.`); return false; }
    compute.acceptDaily?.({ day, continuation: loaded.continuation });
    const grid = loaded.grid;
    clueUI.setClueGrid(grid);
    manual.reset();
    lastAutoSolve = null;
    turn = null;
    activeIdx = -1;
    clearPanels();
    setupScrub(0); // reset the progress bar / scrub from any previous game
    status(`Loaded daily #${d}. Click "Solve" to auto-play, or "Next turn" to step.`);
    enableControls(true);
    return true;
  }

  function clearPanels() {
    uiEls.slotsEl.innerHTML = "";
    uiEls.suggestEl.innerHTML = "";
    uiEls.moveTableEl.innerHTML = "";
  }

  // Discard the current solve (keeping the loaded daily) so the next Solve
  // recomputes — used when the probe word-set toggle changes.
  async function resetSolve() {
    cancelAnim();
    if (!words) return day == null ? undefined : loadDay(day);
    const token = revision;
    compute.resetDaily?.(day);
    const loaded = compute.resetDaily ? { grid: dailyClueGrid(words) }
      : await compute.request({ type: "reset-daily" });
    if (token !== revision) return;
    clueUI.setClueGrid(loaded.grid);
    manual.reset();
    lastAutoSolve = null;
    turn = null;
    activeIdx = -1;
    clearPanels();
    setupScrub(0);
    status(`Probe set changed — press "Solve" to re-run daily #${day}.`);
  }

  function enableControls(on) {
    if (uiEls.solveToEndBtn) uiEls.solveToEndBtn.disabled = !on;
    if (uiEls.nextTurnBtn) uiEls.nextTurnBtn.disabled = !on;
  }

  // --- Solve (whole game, then scrub) -------------------------------------------

  // Play the whole game by driving the real per-move engine (nextTurn) one move
  // at a time, yielding between moves so each genuinely-computed move paints.
  // The asynchronous backend calculates each move while the page stays usable.
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
        await nextTurn(true);
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
    } catch (error) {
      if (!aborted() && !isCancelled(error)) status(`Calculation failed: ${error.message} Press "Solve" or "Next turn" to retry.`);
    } finally {
      if (myToken === animToken) {
        solving = false;
        if (uiEls.nextTurnBtn) uiEls.nextTurnBtn.disabled = !!pendingTurn || !words;
      }
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
    if (turn?.unsolvable) {
      status(`Daily #${day} is unsolvable. ${turn.reason || ""}`);
      return;
    }
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

  async function nextTurn(fromSolve = false) {
    if (!words || solving && !fromSolve) return false;
    if (pendingTurn) {
      if (!fromSolve) return false;
      const existing = pendingTurn;
      await existing.completion;
      if (existing.error) throw existing.error;
      return existing.accepted || false;
    }
    if (!turn) {
      turn = { moves: [], steps: [], done: false };
      lastAutoSolve = null;
      activeIdx = -1;
      manual.reset();
    }
    if (turn.done) return;
    const owner = { revision };
    owner.completion = new Promise((resolve) => { owner.complete = resolve; });
    pendingTurn = owner;
    if (uiEls.nextTurnBtn) uiEls.nextTurnBtn.disabled = true;
    status("Calculating next move…");
    try {
      const reply = await compute.request({ type: "advance-daily", expanded: expanded() });
      if (owner.revision !== revision || pendingTurn !== owner) return false;
      compute.acceptDaily?.({ day, continuation: reply.continuation });
      turn.done = reply.continuation.done;
      turn.unsolvable = !reply.result.solvable;
      turn.reason = reply.result.reason;
      if (reply.move) {
        turn.moves.push(reply.move);
        turn.steps.push({ after: reply.after, suggest: reply.suggest });
      }
      finishStep();
      owner.accepted = true;
      return true;
    } catch (error) {
      if (owner.revision === revision && !isCancelled(error)) {
        owner.error = error;
        status(`Calculation failed: ${error.message} Press "Next turn" to retry.`);
        throw error;
      }
      return false;
    } finally {
      owner.complete();
      if (pendingTurn === owner) {
        pendingTurn = null;
        if (uiEls.nextTurnBtn) uiEls.nextTurnBtn.disabled = solving || !words;
      }
    }
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
    renderAutoSolveTable(uiEls.moveTableEl, lastAutoSolve, activeIdx, scrubTo);
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

  function notifyReady() {
    enableControls(!!words);
    if (!words) { status('Select a daily, or "Reload day" to retry loading.'); return; }
    clueUI.setClueGrid(dailyClueGrid(words));
    if (lastAutoSolve?.moves.length) {
      jumpToMove(activeIdx >= 0 ? activeIdx : lastAutoSolve.moves.length - 1);
      posStatus();
    } else {
      manual.reset();
      clearPanels();
      setupScrub(0);
      status(`Loaded daily #${day}. Click "Solve" to auto-play, or "Next turn" to step.`);
    }
  }

  return { loadDay, solveToEnd, nextTurn, resetSolve, jumpToMove, prev, next, scrubTo, notifyReady, stop: cancelAnim,
           getDay: () => day, getWords: () => words, getTiming: () => compute.getTiming?.() };
}
