// Self-playing auto-solver — port of referdle/autoplay.py + Flask's
// _autosolve_steps. Given the five TRUE words, simulate a full game.

import { getComparison } from "./compare.js";
import { solve, solveRelaxed, localCandidates } from "./solver.js";
import { bestGuessAcrossBoards, topGuessesForBoard } from "./suggest.js";
import { STRATEGY, MAX_STEPS } from "./strategy.js";

const FINAL = 4;

export function clueGridFromWords(words) {
  const final = words[FINAL];
  return [
    getComparison(words[0], final),
    getComparison(words[1], final),
    getComparison(words[2], final),
    getComparison(words[3], final),
    "22222",
  ];
}

function parseClue(clueGrid) {
  const clue = [null, null, null, null, null];
  if (clueGrid) {
    for (let i = 0; i < 4; i++) {
      const c = i < clueGrid.length ? clueGrid[i] : null;
      if (typeof c === "string" && c.length === 5 && [...c].every((ch) => "012".includes(ch))) {
        clue[i] = c;
      }
    }
  }
  return clue;
}

function allPinned(res) {
  return res.solvable && res.perSlotFeasible.every((s) => s.length === 1);
}

// auto_solve. `st` = {PM, N, POOL, poolIndex, ALL_GUESSES, PLURALS}.
export function autoSolve(trueWords, st, startSlots) {
  const { PM, N, POOL, poolIndex, ALL_GUESSES, PLURALS } = st;
  const words = trueWords.map((w) => w.toUpperCase());
  const clueGrid = clueGridFromWords(words);
  const poolSet = poolIndex;

  let slots;
  if (startSlots) {
    slots = startSlots.map((s) => ({ guesses: (s.guesses || []).map((g) => ({ ...g })) }));
  } else {
    slots = [0, 1, 2, 3, 4].map(() => ({ guesses: [] }));
  }
  const sequence = [];

  let pruneW13 = STRATEGY.prune_w13_doubles;
  let pruneW5 = STRATEGY.prune_w5_plurals;
  let pruneCombos = STRATEGY.prune_w13_combos;
  let relaxedAt = null;

  const doSolve = (p13, p5, pc) =>
    solve(slots, clueGrid, POOL, PM, N, poolIndex, PLURALS, p13, p5, pc, null);

  const rank = (r) => bestGuessAcrossBoards(r, PM, N, poolIndex, ALL_GUESSES, PLURALS);

  let res = doSolve(pruneW13, pruneW5, pruneCombos);
  let step = 0;
  while (step < MAX_STEPS && res.solvable) {
    if (allPinned(res)) break;
    let ranked = rank(res);

    if ((!ranked || !ranked.length) && pruneCombos) {
      pruneCombos = false;
      relaxedAt = relaxedAt || step + 1;
      res = doSolve(pruneW13, pruneW5, pruneCombos);
      ranked = res.solvable ? rank(res) : null;
    }
    if ((!ranked || !ranked.length) && (pruneW13 || pruneW5)) {
      pruneW13 = pruneW5 = false;
      relaxedAt = relaxedAt || step + 1;
      res = doSolve(false, false, false);
      ranked = res.solvable ? rank(res) : null;
    }
    if (!ranked || !ranked.length) break;

    let top = ranked[0];
    // forced_opener is "" by default — skipped.
    const board = top.board;
    const word = top.word;
    const colors = getComparison(word, words[board]);
    slots[board].guesses.push({ word, colors });
    sequence.push({
      step: step + 1,
      board,
      word,
      colors,
      setSize: top.setSize,
      expRemaining: top.expRemaining,
      probe: top.probe || false,
      expanded: !!top.probe && !poolSet.has(word),
    });

    res = doSolve(pruneW13, pruneW5, pruneCombos);
    if (!res.solvable && pruneCombos) {
      pruneCombos = false;
      relaxedAt = relaxedAt || step + 1;
      res = doSolve(pruneW13, pruneW5, pruneCombos);
    }
    if (!res.solvable && (pruneW13 || pruneW5)) {
      pruneW13 = pruneW5 = false;
      relaxedAt = relaxedAt || step + 1;
      res = doSolve(false, false, false);
    }
    step++;
  }

  const lastOn = {};
  for (const s of sequence) lastOn[s.board] = s.word;
  let closing = 0;
  for (let b = 0; b < 5; b++) if (lastOn[b] !== words[b]) closing++;

  return {
    sequence,
    guessCount: sequence.length,
    completedCount: sequence.length + closing,
    solved: allPinned(res),
    clueGrid,
    words,
    pruneRelaxed: relaxedAt,
  };
}

// Build the full move list + cached per-move view (solve after + suggest before).
// Mirrors Flask's _autosolve_steps. `st` as in autoSolve.
export function buildSteps(autoResult, startSlots, st) {
  const { PM, N, POOL, poolIndex, ALL_GUESSES, PLURALS } = st;
  const clueGrid = autoResult.clueGrid;

  const moves = autoResult.sequence.map((s) => ({
    board: s.board,
    word: s.word,
    colors: s.colors,
    probe: s.probe || false,
    expanded: s.expanded || false,
    setSize: s.setSize,
    expRemaining: s.expRemaining,
    isClosing: false,
  }));

  if (autoResult.solved) {
    const last = {};
    for (let b = 0; b < startSlots.length; b++) {
      const gg = startSlots[b].guesses || [];
      if (gg.length) last[b] = gg[gg.length - 1].word;
    }
    for (const s of autoResult.sequence) last[s.board] = s.word;
    for (let b = 0; b < autoResult.words.length; b++) {
      if (last[b] !== autoResult.words[b]) {
        moves.push({
          board: b,
          word: autoResult.words[b],
          colors: "22222",
          probe: false,
          expanded: false,
          setSize: null,
          expRemaining: null,
          isClosing: true,
        });
      }
    }
  }

  const steps = [];
  const state = startSlots.map((s) => ({ guesses: (s.guesses || []).map((g) => ({ ...g })) }));
  for (const m of moves) {
    const resB = solveRelaxed(state, clueGrid, POOL, PM, N, poolIndex, PLURALS, null);
    let suggest;
    if (resB.solvable) {
      const ranked = bestGuessAcrossBoards(resB, PM, N, poolIndex, ALL_GUESSES, PLURALS);
      const perBoard = [];
      for (let b = 0; b < resB.perSlotFeasible.length; b++) {
        const ans = resB.perSlotFeasible[b];
        if (ans.length > 1) {
          const avoid = b < 3 && STRATEGY.avoid_doubles_w13;
          perBoard.push({
            board: b,
            top: topGuessesForBoard(ans, PM, N, poolIndex, ALL_GUESSES, PLURALS, 5, avoid, b),
          });
        }
      }
      suggest = { solvable: true, ranked, perBoard };
    } else {
      suggest = { solvable: false };
    }
    state[m.board].guesses.push({ word: m.word, colors: m.colors });
    const resA = solveRelaxed(state, clueGrid, POOL, PM, N, poolIndex, PLURALS, null);
    steps.push({ after: resA, suggest });
  }
  return { moves, steps };
}

// One turn's ranking from the current state.
export function advanceOneTurn(slots, clueGrid, st) {
  const { PM, N, POOL, poolIndex, ALL_GUESSES, PLURALS } = st;
  const res = solveRelaxed(slots, clueGrid, POOL, PM, N, poolIndex, PLURALS, null);
  if (!res.solvable) return { ranked: [], res };
  const ranked = bestGuessAcrossBoards(res, PM, N, poolIndex, ALL_GUESSES, PLURALS);
  return { ranked, res };
}
