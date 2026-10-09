// Manual computation is on demand. Broad replay prefixes stay lazy until visited.
import { solveRelaxed } from "./solver.js";
import { buildSuggestions } from "./suggest.js";
import { freshSlots, reconstructSlots } from "./engine.js";

// An all-gray clue carries no signal; null takes the fast no-clue solver path.
export const clueIsSet = (grid) =>
  grid && grid.slice(0, 4).some((c) => typeof c === "string" && (c.includes("1") || c.includes("2")));
export const manualClue = (grid) => clueIsSet(grid) ? grid : null;

export function createManualAnalysis(st) {
  let replayCache = null;
  const reSolve = (slots, clueGrid) =>
    solveRelaxed(slots, clueGrid, st.POOL, st.PM, st.N, st.poolIndex, st.PLURALS, null);
  const buildSuggest = (res, slots, clueGrid, expanded) =>
    buildSuggestions(res, st.PM, st.N, st.poolIndex, expanded ? st.ALL_GUESSES : st.POOL,
      st.PLURALS, { slots, clueGrid, pool: st.POOL });

  function prepare(inputSlots, rawGrid) {
    const slots = structuredClone(inputSlots);
    const clueGrid = manualClue(rawGrid);
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
    const steps = [];
    for (let i = 0; i < reuse; i++) {                 // unchanged prefix — reuse cached solve (lazy)
      moves[i].setSize = replayCache.moves[i].setSize;
      moves[i].expRemaining = replayCache.moves[i].expRemaining;
      steps.push(replayCache.steps[i]);
    }
    for (let i = reuse; i < moves.length; i++) {      // diverging tail — solve LAZILY (on scrub)
      // Broad early prefixes are expensive. Solve their results and cut counts
      // only when the user visits them, just like their suggestions.
      steps.push({ after: undefined, suggest: undefined });
      moves[i].setSize = undefined;
      moves[i].expRemaining = undefined;
    }
    replayCache = { clueKey, moves, steps, startSlots };
    return { slots, clueGrid, startSlots, moves, steps, reuse };
  }

  function analyse(inputSlots, rawGrid, expanded, compact = false) {
    const { slots, clueGrid, startSlots, moves, steps, reuse } = prepare(inputSlots, rawGrid);

    // Solve ONLY the current/final state — that's all the displayed suggestion needs, and a
    // settled end-state is narrow (fast). The per-move replay is filled in lazily on scrub.
    const res = reSolve(slots, clueGrid);
    if (moves.length) steps[steps.length - 1].after = res;  // last step == final state; reuse it
    return structuredClone({ result: res, clueGrid, startSlots, moves,
      ...(compact ? { reuse, finalStep: steps.at(-1) } : { steps }),
      suggest: !moves.length && res.solvable ? buildSuggest(res, slots, clueGrid, expanded) : undefined });
  }

  function ensureStepSolved(K, clueGrid) {
    const { startSlots, moves, steps } = replayCache;
    const step = steps[K];
    if (step.after === undefined) {
      step.after = reSolve(reconstructSlots(startSlots, moves, K + 1), clueGrid);
    }
    if (moves[K].setSize === undefined) {
      const before = reSolve(reconstructSlots(startSlots, moves, K), clueGrid);
      moves[K].setSize = before.solvable ? before.perSlotFeasible[moves[K].board].length : null;
      moves[K].expRemaining = step.after.solvable
        ? step.after.perSlotFeasible[moves[K].board].length : null;
    }
  }

  function replay(K, rawGrid, expanded) {
    if (!replayCache || !Number.isInteger(K) || K < 0 || K >= replayCache.moves.length) {
      throw new Error("No manual replay move at this index");
    }
    const clueGrid = manualClue(rawGrid);
    const { startSlots, moves, steps } = replayCache;
    const slotsAfter = reconstructSlots(startSlots, moves, K + 1);
    ensureStepSolved(K, clueGrid);
    const step = steps[K];
    if (step.suggest === undefined) {
      step.suggest = step.after.solvable
        ? buildSuggest(step.after, slotsAfter, clueGrid, expanded)
        : { solvable: false };
    }
    return structuredClone({ slots: slotsAfter, clueGrid, move: moves[K], step });
  }

  function reset() { replayCache = null; }
  return { analyse, replay, reset, restore: prepare };
}
