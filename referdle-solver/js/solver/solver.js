// Referdle solver core — port of referdle/solver.py.
//
// A game state = 5 word boards + the master clue grid. Board i has 0..N guess
// rows; a pool word is a LOCAL candidate of board i iff consistent with every
// row. The clue grid couples every word to the unknown final word W5, so the
// clue solver branches on the final word.

import { compareCode, cluePatternCode } from "./compare.js";
import { pmCode } from "./data.js";
import { STRATEGY } from "./strategy.js";

const NUM_SLOTS = 5;
const FINAL = 4;

// --- pattern access ------------------------------------------------------------

// Pattern code of get_comparison(guess, cand): PM lookup when both are pool
// words, else computed on the fly. (PM[i*N+j] = code(pool[i] vs pool[j]).)
function codeFor(guess, cand, PM, N, poolIndex) {
  const gi = poolIndex.get(guess);
  if (gi !== undefined) {
    const ci = poolIndex.get(cand);
    if (ci !== undefined) return pmCode(PM, N, gi, ci);
  }
  return compareCode(guess, cand);
}

// 2D pattern-code block M[a][b] = code(aWords[a] vs bWords[b]).
function poolBlock(aWords, bWords, PM, N, poolIndex) {
  const na = aWords.length, nb = bWords.length;
  const M = new Array(na);
  for (let ai = 0; ai < na; ai++) {
    const row = new Array(nb);
    const aIdx = poolIndex.get(aWords[ai]);
    for (let bi = 0; bi < nb; bi++) {
      if (aIdx !== undefined) {
        const bIdx = poolIndex.get(bWords[bi]);
        row[bi] = bIdx !== undefined ? pmCode(PM, N, aIdx, bIdx) : compareCode(aWords[ai], bWords[bi]);
      } else {
        row[bi] = compareCode(aWords[ai], bWords[bi]);
      }
    }
    M[ai] = row;
  }
  return M;
}

// --- local candidate filtering -------------------------------------------------

export function localCandidates(slot, pool, PM, N, poolIndex) {
  const guesses = slot.guesses || [];
  if (!guesses.length) return pool.slice();
  const mask = new Uint8Array(pool.length).fill(1);
  for (const g of guesses) {
    const target = cluePatternCode(g.colors);
    const gi = poolIndex.get(g.word);
    for (let i = 0; i < pool.length; i++) {
      if (!mask[i]) continue;
      const code = gi !== undefined ? pmCode(PM, N, gi, i) : compareCode(g.word, pool[i]);
      if (code !== target) mask[i] = 0;
    }
  }
  const out = [];
  for (let i = 0; i < pool.length; i++) if (mask[i]) out.push(pool[i]);
  return out;
}

// --- bipartite matching (Kuhn) -------------------------------------------------

function kuhn(slotCands, forbid) {
  const matchedBy = new Map(); // word -> slot index
  const matchSlot = new Array(slotCands.length).fill(null);

  function augment(s, visited) {
    for (const w of slotCands[s]) {
      if (w === forbid || visited.has(w)) continue;
      visited.add(w);
      const owner = matchedBy.has(w) ? matchedBy.get(w) : null;
      if (owner === null || augment(owner, visited)) {
        matchedBy.set(w, s);
        matchSlot[s] = w;
        return true;
      }
    }
    return false;
  }

  let size = 0;
  for (let s = 0; s < slotCands.length; s++) {
    if (augment(s, new Set())) size++;
  }
  return { size, matchSlot };
}

function hasPerfectMatching(slotCands, forbid) {
  return kuhn(slotCands, forbid).size === slotCands.length;
}

function feasibleForSlot(s, cands, found) {
  const others = [];
  for (let i = 0; i < cands.length; i++) if (i !== s) others.push(cands[i]);

  const base = kuhn(others, null);
  if (base.size !== others.length) return [];
  const baseUsed = new Set(base.matchSlot);

  const feasible = [];
  const forbidMemo = new Map();
  for (const w of cands[s]) {
    if (found && found.has(w)) continue;
    if (!baseUsed.has(w)) {
      feasible.push(w);
    } else {
      let ok = forbidMemo.get(w);
      if (ok === undefined) {
        ok = hasPerfectMatching(others, w);
        forbidMemo.set(w, ok);
      }
      if (ok) feasible.push(w);
    }
  }
  return feasible;
}

// --- clue grid normalisation ---------------------------------------------------

function normalizeClue(clueGrid) {
  const clue = [null, null, null, null, null];
  if (!clueGrid) return clue;
  for (let i = 0; i < 4; i++) {
    const c = i < clueGrid.length ? clueGrid[i] : null;
    if (typeof c === "string" && c.length === 5 && [...c].every((ch) => "012".includes(ch))) {
      clue[i] = c;
    }
  }
  return clue;
}

// --- helpers -------------------------------------------------------------------

function knownPresentLetters(slot) {
  const s = new Set();
  for (const g of slot.guesses || []) {
    for (let p = 0; p < 5; p++) {
      if (g.colors[p] !== "0") s.add(g.word[p]);
    }
  }
  return s;
}

function forbiddenLetters(claimed, slot) {
  const s = new Set();
  for (let k = 0; k < claimed.length; k++) {
    if (k !== slot) for (const c of claimed[k]) s.add(c);
  }
  return s;
}

function sharesLetter(word, letterSet) {
  for (let p = 0; p < 5; p++) if (letterSet.has(word[p])) return true;
  return false;
}

function dedupSorted(words) {
  return [...new Set(words)].sort();
}

function explainConflict(cands) {
  const empty = [];
  for (let i = 0; i < cands.length; i++) if (cands[i].length === 0) empty.push(i + 1);
  if (empty.length) return `Word(s) ${empty.join(", ")} have no candidate words at all.`;
  return "Some group of words shares too few candidate words (Hall's condition).";
}

function unsolvableResult(cands, reason) {
  return {
    solvable: false,
    reason,
    note: null,
    clueUsed: false,
    cands,
    viableFinals: [],
    unionFeasible: [],
    perSlotFeasible: cands.map(() => []),
    candCounts: cands.map((c) => c.length),
  };
}

function allPossible(pool) {
  const all = pool.slice().sort();
  return {
    solvable: true,
    reason: null,
    note: "No guesses yet — showing all words. Enter a guess to narrow.",
    clueUsed: false,
    cands: [pool, pool, pool, pool, pool],
    viableFinals: all,
    unionFeasible: all,
    perSlotFeasible: [all, all, all, all, all],
    candCounts: [pool.length, pool.length, pool.length, pool.length, pool.length],
  };
}

// --- chain propagation ---------------------------------------------------------

// keep B columns that have a compatible A row, and A rows that have a compatible B.
function chainFeasible(C, clue, PM, N, poolIndex) {
  const F = new Array(5).fill(null);
  F[0] = C[0];
  for (let k = 1; k < 5; k++) {
    const pat = clue[k - 1];
    const prev = F[k - 1];
    if (pat) {
      const code = cluePatternCode(pat);
      const M = poolBlock(prev, C[k], PM, N, poolIndex);
      const keep = [];
      for (let j = 0; j < C[k].length; j++) {
        let any = false;
        for (let i = 0; i < prev.length; i++) {
          if (M[i][j] === code) { any = true; break; }
        }
        if (any) keep.push(C[k][j]);
      }
      F[k] = keep;
    } else {
      F[k] = C[k].slice();
    }
  }
  const B = new Array(5).fill(null);
  B[4] = F[4];
  for (let k = 3; k >= 0; k--) {
    const pat = clue[k];
    const nxt = B[k + 1];
    if (pat) {
      const code = cluePatternCode(pat);
      const M = poolBlock(F[k], nxt, PM, N, poolIndex);
      const keep = [];
      for (let i = 0; i < F[k].length; i++) {
        let any = false;
        for (let j = 0; j < nxt.length; j++) {
          if (M[i][j] === code) { any = true; break; }
        }
        if (any) keep.push(F[k][i]);
      }
      B[k] = keep;
    } else {
      B[k] = F[k].slice();
    }
  }
  return B;
}

// Arc-consistency on word1—word2—word3—word4, mutating `cands` in place.
function chainArcConsistency(cands, clue, PM, N, poolIndex) {
  let changed = true;
  let guard = 0;
  while (changed && guard < 8) {
    guard++;
    changed = false;
    for (let i = 0; i < 3; i++) {
      const pat = clue[i];
      if (!pat) continue;
      const code = cluePatternCode(pat);
      let A = cands[i];
      let B = cands[i + 1];
      let M = poolBlock(A, B, PM, N, poolIndex);
      // keep B[j] iff some A[a] matches
      const keepBidx = [];
      for (let j = 0; j < B.length; j++) {
        let any = false;
        for (let a = 0; a < A.length; a++) {
          if (M[a][j] === code) { any = true; break; }
        }
        if (any) keepBidx.push(j);
      }
      if (keepBidx.length !== B.length) {
        const newB = keepBidx.map((j) => B[j]);
        cands[i + 1] = newB;
        B = newB;
        // restrict M columns for the A pass
        M = M.map((row) => keepBidx.map((j) => row[j]));
        changed = true;
      }
      // keep A[a] iff some B[j] matches
      const keepA = [];
      for (let a = 0; a < A.length; a++) {
        let any = false;
        for (let j = 0; j < B.length; j++) {
          if (M[a][j] === code) { any = true; break; }
        }
        if (any) keepA.push(A[a]);
      }
      if (keepA.length !== A.length) {
        cands[i] = keepA;
        changed = true;
      }
    }
  }
}

// --- simple solver (no clue) ---------------------------------------------------

function solveSimple(cands) {
  const full = kuhn(cands, null);
  if (full.size !== NUM_SLOTS) {
    return unsolvableResult(cands, explainConflict(cands));
  }
  const unionFeasible = dedupSorted(cands.flat());
  const perSlotFeasible = [];
  for (let s = 0; s < cands.length; s++) {
    perSlotFeasible.push(feasibleForSlot(s, cands).sort());
  }
  return {
    solvable: true,
    reason: null,
    note: null,
    clueUsed: false,
    cands,
    viableFinals: cands[FINAL].slice().sort(),
    unionFeasible,
    perSlotFeasible,
    candCounts: cands.map((c) => c.length),
  };
}

// --- clue solver ---------------------------------------------------------------

function solveWithClue(cands, clue, knownPresent, PM, N, poolIndex) {
  // Global elimination: an all-grey clue row means each known letter of that
  // word is absent from the final, so it lives in no other word.
  const globalOwner = new Map();
  for (let i = 0; i < 5; i++) {
    if (clue[i] === "00000") {
      for (const c of knownPresent[i]) globalOwner.set(c, i);
    }
  }
  if (globalOwner.size) {
    const keep = (w, i) => {
      for (let p = 0; p < 5; p++) {
        const owner = globalOwner.get(w[p]);
        if (owner !== undefined && owner !== i) return false;
      }
      return true;
    };
    cands = cands.map((lst, i) => lst.filter((w) => keep(w, i)));
  }

  chainArcConsistency(cands, clue, PM, N, poolIndex);

  const finals = cands[FINAL];
  const unionSet = new Set();
  const perSlot = [new Set(), new Set(), new Set(), new Set(), new Set()];
  const viableFinals = [];

  // Precompute, per clued board i<4, code(cands[i][k] vs finals[fj]).
  const finalCodes = {}; // i -> 2D array
  const clueCodes = {};
  for (let i = 0; i < FINAL; i++) {
    if (clue[i]) {
      finalCodes[i] = poolBlock(cands[i], finals, PM, N, poolIndex);
      clueCodes[i] = cluePatternCode(clue[i]);
    }
  }

  for (let fj = 0; fj < finals.length; fj++) {
    const f = finals[fj];
    const fset = new Set(f);
    const claimed = knownPresent.map((kp) => [...kp].filter((c) => !fset.has(c)));

    const conditioned = new Array(5).fill(null);
    let ok = true;
    for (let i = 0; i < FINAL; i++) {
      let lst;
      if (clue[i]) {
        const block = finalCodes[i];
        const code = clueCodes[i];
        lst = [];
        for (let k = 0; k < cands[i].length; k++) {
          if (block[k][fj] === code) lst.push(cands[i][k]);
        }
      } else {
        lst = cands[i];
      }
      const forbidden = forbiddenLetters(claimed, i);
      if (forbidden.size) lst = lst.filter((w) => !sharesLetter(w, forbidden));
      conditioned[i] = lst;
      if (lst.length === 0) { ok = false; break; }
    }
    if (!ok) continue;
    conditioned[FINAL] = [f];

    const feas = chainFeasible(conditioned, clue, PM, N, poolIndex);
    if (feas[FINAL].length === 0) continue;

    viableFinals.push(f);
    for (let i = 0; i < 5; i++) {
      for (const w of feas[i]) {
        perSlot[i].add(w);
        unionSet.add(w);
      }
    }
  }

  if (viableFinals.length === 0) {
    return unsolvableResult(
      cands,
      "No final word (Word 5) is consistent with both the clue grid and the " +
        "board guesses. Check the clue-grid colours and Word 5's guesses."
    );
  }

  return {
    solvable: true,
    reason: null,
    note: null,
    clueUsed: true,
    cands,
    viableFinals: viableFinals.slice().sort(),
    unionFeasible: [...unionSet].sort(),
    perSlotFeasible: perSlot.map((s) => [...s].sort()),
    candCounts: cands.map((c) => c.length),
  };
}

// --- public solve --------------------------------------------------------------

// solve(slots, clueGrid, pool, PM, N, poolIndex, PLURALS, pruneW13?, pruneW5?, pruneCombos?)
export function solve(slots, clueGrid, pool, PM, N, poolIndex, PLURALS,
                      pruneW13, pruneW5, pruneCombos, combos) {
  const clue = normalizeClue(clueGrid);
  const hasClue = clue.some((c) => c !== null);
  const hasGuess = slots.some((s) => (s.guesses || []).length > 0);
  if (!hasGuess && !hasClue) return allPossible(pool);

  let cands = slots.map((slot) => localCandidates(slot, pool, PM, N, poolIndex));
  const knownPresent = slots.map((slot) => knownPresentLetters(slot));

  if (pruneW13 === undefined) pruneW13 = STRATEGY.prune_w13_doubles;
  if (pruneW5 === undefined) pruneW5 = STRATEGY.prune_w5_plurals;
  if (pruneCombos === undefined) pruneCombos = STRATEGY.prune_w13_combos;

  if (pruneW13) {
    cands = cands.map((c, i) =>
      i < 3 ? c.filter((w) => new Set(w).size === 5) : c
    );
  }
  if (pruneCombos && combos) {
    cands = cands.map((c, i) => (i >= 3 ? c : c.filter((w) => !combos.has(w))));
  }
  if (pruneW5 && PLURALS) {
    cands = cands.map((c, i) => (i !== 4 ? c : c.filter((w) => !PLURALS.has(w))));
  }

  return hasClue
    ? solveWithClue(cands, clue, knownPresent, PM, N, poolIndex)
    : solveSimple(cands);
}

// solveRelaxed: solve with the strategy's prunes, retry once with all prunes off
// if that yields unsolvable while a prune is on.
export function solveRelaxed(slots, clueGrid, pool, PM, N, poolIndex, PLURALS, combos) {
  let res = solve(slots, clueGrid, pool, PM, N, poolIndex, PLURALS, undefined, undefined, undefined, combos);
  if (!res.solvable &&
      (STRATEGY.prune_w13_doubles || STRATEGY.prune_w5_plurals || STRATEGY.prune_w13_combos)) {
    res = solve(slots, clueGrid, pool, PM, N, poolIndex, PLURALS, false, false, false, combos);
  }
  return res;
}

// --- pick one concrete game (manual mode) --------------------------------------

function matchDistinct(cond, clue, final) {
  const fset = new Set(final);
  const used = new Set([final]);
  const owner = new Map(); // letter absent from final -> slot allowed to hold it
  const result = [null, null, null, null, final];

  function claim(w, s) {
    const taken = [];
    for (let p = 0; p < 5; p++) {
      const c = w[p];
      if (fset.has(c)) continue;
      const o = owner.has(c) ? owner.get(c) : null;
      if (o !== null && o !== s) {
        for (const t of taken) owner.delete(t);
        return null;
      }
      if (o === null) {
        owner.set(c, s);
        taken.push(c);
      }
    }
    return taken;
  }

  function dfs(s) {
    if (s < 0) return true;
    const pat = clue[s];
    for (const w of cond[s]) {
      if (used.has(w)) continue;
      if (pat && getCmp(w, result[s + 1]) !== pat) continue;
      const taken = claim(w, s);
      if (taken === null) continue;
      used.add(w);
      result[s] = w;
      if (dfs(s - 1)) return true;
      used.delete(w);
      for (const c of taken) owner.delete(c);
    }
    return false;
  }

  return dfs(FINAL - 1) ? result : null;
}

// local getComparison wrapper to avoid an import cycle name clash
import { getComparison as getCmp } from "./compare.js";

export function pickSolution(slots, clueGrid, pool, PM, N, poolIndex) {
  const clue = normalizeClue(clueGrid);
  const cands = slots.map((s) => localCandidates(s, pool, PM, N, poolIndex));
  for (const f of cands[FINAL]) {
    const cond = new Array(5).fill(null);
    cond[FINAL] = [f];
    let ok = true;
    for (let i = 0; i < FINAL; i++) {
      if (clue[i]) {
        cond[i] = cands[i].filter((w) => getCmp(w, f) === clue[i]);
      } else {
        cond[i] = cands[i];
      }
      if (cond[i].length === 0) { ok = false; break; }
    }
    if (!ok) continue;
    const assign = matchDistinct(cond, clue, f);
    if (assign) return assign;
  }
  return null;
}
