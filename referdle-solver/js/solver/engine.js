// DOM-free daily computation. The cache belongs only to the current position.
import { dailyClueGrid } from "./data.js";
import { solve } from "./solver.js";
import { buildSuggestions } from "./suggest.js";
import { getComparison } from "./compare.js";
import { STRATEGY } from "./strategy.js";

export const freshSlots = () => [0, 1, 2, 3, 4].map(() => ({ guesses: [] }));

export function reconstructSlots(startSlots, moves, upto) {
  const slots = startSlots.map((s) => ({ guesses: (s.guesses || []).map((g) => ({ ...g })) }));
  for (let i = 0; i < upto; i++) {
    const m = moves[i];
    slots[m.board].guesses.push({ word: m.word, colors: m.colors });
  }
  return slots;
}

export function createDailyEngine(st) {
  let words = null;
  let turn = null;
  let lastResult = null;
  let useExpanded = true;
  const guessSet = () => useExpanded ? st.ALL_GUESSES : st.POOL;

  function load(answers) {
    words = answers.slice();
    turn = {
      slots: freshSlots(), grid: dailyClueGrid(words),
      pruneW13: STRATEGY.prune_w13_doubles, pruneW5: STRATEGY.prune_w5_plurals,
      pruneCombos: STRATEGY.prune_w13_combos, solveCache: new Map(),
      lastPlayedWords: Array(5).fill(null), closingQueue: null, done: false,
    };
    lastResult = null;
    return structuredClone({ words, grid: turn.grid });
  }

  // A continuation contains every committed input needed to resume without replay:
  // slots, clue, sticky prunes, closing queue, completion and last played words.
  function continuation() {
    const { solveCache, ...position } = turn;
    return { words, ...position };
  }

  function restore(position) {
    const copied = structuredClone(position);
    words = copied.words;
    delete copied.words;
    turn = { ...copied, solveCache: new Map() };
    lastResult = null;
    return structuredClone(continuation());
  }

  function publish(fields = {}) {
    return structuredClone({ ...fields, result: fields.result ?? lastResult, continuation: continuation() });
  }

  function allPinned(res) {
    return res.solvable && res.perSlotFeasible.every((s) => s.length === 1);
  }

  function turnSolve(p13, p5, pc) {
    // Entries belong to the current slots/grid; every guess append clears them.
    const key = `${p13 ? 1 : 0}${p5 ? 1 : 0}${pc ? 1 : 0}`;
    let result = turn.solveCache.get(key);
    if (!turn.solveCache.has(key)) {
      result = solve(turn.slots, turn.grid, st.POOL, st.PM, st.N, st.poolIndex, st.PLURALS, p13, p5, pc, null);
      turn.solveCache.set(key, result);
    }
    lastResult = result;
    return result;
  }

  // Per-board top-5 (probes included when one wins) for the state-before-move panel.
  function buildSuggest(res) {
    const { ranked, perBoard } = buildSuggestions(res, st.PM, st.N, st.poolIndex, guessSet(), st.PLURALS,
      { slots: turn.slots, clueGrid: turn.grid, pool: st.POOL });
    return { solvable: true, ranked, perBoard };
  }

  // After-move solve (relaxed) for the overlay + word-list panels.
  function afterSolve() {
    // Display solves start with global defaults; move selection may already have
    // disabled sticky prunes. Reuse results only for the same pruning options.
    const p13 = STRATEGY.prune_w13_doubles;
    const p5 = STRATEGY.prune_w5_plurals;
    const pc = STRATEGY.prune_w13_combos;
    let result = turnSolve(p13, p5, pc);
    if (!result.solvable && (p13 || p5 || pc)) result = turnSolve(false, false, false);
    return result;
  }

  function startClosing() {
    const lastOn = {};
    turn.lastPlayedWords.forEach((word, board) => { lastOn[board] = word; });
    turn.closingQueue = [];
    for (let b = 0; b < 5; b++) if (lastOn[b] !== words[b]) turn.closingQueue.push(b);
    if (!turn.closingQueue.length) turn.done = true;
  }

  function playClosing() {
    if (!turn.closingQueue) startClosing();
    if (!turn.closingQueue.length) { turn.done = true; return publish(); }
    const b = turn.closingQueue.shift();
    const before = afterSolve();
    const suggest = before.solvable ? buildSuggest(before) : { solvable: false };
    turn.slots[b].guesses.push({ word: words[b], colors: "22222" });
    turn.solveCache.clear();
    const move = {
      board: b, word: words[b], colors: "22222", probe: false, expanded: false,
      setSize: null, expRemaining: null, isClosing: true,
    };
    turn.lastPlayedWords[move.board] = move.word;
    const after = afterSolve();
    if (!turn.closingQueue.length) turn.done = true;
    return publish({ before, suggest, after, move });
  }

  function advance(expanded = true) {
    useExpanded = expanded;
    if (!turn) throw new Error("No daily position loaded");
    if (turn.done) return publish();
    if (turn.closingQueue) { return playClosing(); }

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
    if (!res.solvable) { turn.done = true; return publish({ before: res, result: res }); }
    if (allPinned(res)) { return playClosing(); }

    let suggest = buildSuggest(res);
    let ranked = suggest.ranked;
    if (!ranked.length && turn.pruneCombos) {
      turn.pruneCombos = false;
      res = turnSolve(turn.pruneW13, turn.pruneW5, false);
      suggest = res.solvable ? buildSuggest(res) : { solvable: false };
      ranked = suggest.ranked || [];
    }
    if (!ranked.length && (turn.pruneW13 || turn.pruneW5)) {
      turn.pruneW13 = turn.pruneW5 = false;
      res = turnSolve(false, false, false);
      suggest = res.solvable ? buildSuggest(res) : { solvable: false };
      ranked = suggest.ranked || [];
    }
    if (!ranked.length) { return playClosing(); }

    const top = ranked[0];
    const colors = getComparison(top.word, words[top.board]);
    turn.slots[top.board].guesses.push({ word: top.word, colors });
    turn.solveCache.clear();
    const move = {
      board: top.board, word: top.word, colors,
      probe: top.probe || false,
      expanded: !!top.probe && !st.poolIndex.has(top.word),
      setSize: top.setSize, expRemaining: top.expRemaining, isClosing: false,
    };
    turn.lastPlayedWords[move.board] = move.word;
    const after = afterSolve();
    return publish({ before: res, suggest, after, move });
  }

  return { load, advance, restore };
}
