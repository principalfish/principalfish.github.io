// Guess recommender — port of referdle/suggest.py. Scores guesses by EXPECTED
// REMAINING CANDIDATES (Σ bucket²/n over feedback buckets); lower is better.

import { compareCode } from "./compare.js";
import { pmCode } from "./data.js";
import { STRATEGY } from "./strategy.js";
import { solve } from "./solver.js";

const LOOKAHEAD_MAX = 14;

function hasRepeat(word) {
  return new Set(word).size < 5;
}

// Python's round(x, 2): round-half-to-even (banker's rounding), so the UI's
// displayed expRemaining/score match the Python reference exactly.
function round2(x) {
  const scaled = x * 100;
  const floor = Math.floor(scaled);
  const diff = scaled - floor;
  let r;
  if (diff > 0.5) r = floor + 1;
  else if (diff < 0.5) r = floor;
  else r = floor % 2 === 0 ? floor : floor + 1; // half -> even
  return r / 100;
}

function freePositions(words) {
  if (!words.length) return 0;
  let count = 0;
  for (let p = 0; p < 5; p++) {
    const ch = words[0][p];
    if (!words.every((w) => w[p] === ch)) count++;
  }
  return count;
}

// --- expected-remaining scoring over a hypothesis set (uses PM for pool words) -

function answerIndices(answers, poolIndex) {
  // index into PM for each answer (all answers are pool words here)
  const idx = new Int32Array(answers.length);
  for (let i = 0; i < answers.length; i++) idx[i] = poolIndex.get(answers[i]);
  return idx;
}

// code(answers[g] vs answers[s]) using PM.
function setCode(idx, PM, N, g, s) {
  return pmCode(PM, N, idx[g], idx[s]);
}

export function expectedRemainingAll(answers, PM, N, poolIndex) {
  const n = answers.length;
  const exp = new Float64Array(n);
  if (n === 0) return exp;
  const idx = answerIndices(answers, poolIndex);
  const counts = new Int32Array(243);
  const seen = [];
  for (let i = 0; i < n; i++) {
    seen.length = 0;
    for (let j = 0; j < n; j++) {
      const c = pmCode(PM, N, idx[i], idx[j]);
      if (counts[c] === 0) seen.push(c);
      counts[c]++;
    }
    let total = 0;
    for (const c of seen) { total += counts[c] * counts[c]; counts[c] = 0; }
    exp[i] = total / n;
  }
  return exp;
}

export function expectedAndMaxAll(answers, PM, N, poolIndex) {
  const n = answers.length;
  const exp = new Float64Array(n);
  const max = new Float64Array(n);
  if (n === 0) return { exp, max };
  const idx = answerIndices(answers, poolIndex);
  const counts = new Int32Array(243);
  const seen = [];
  for (let i = 0; i < n; i++) {
    seen.length = 0;
    for (let j = 0; j < n; j++) {
      const c = pmCode(PM, N, idx[i], idx[j]);
      if (counts[c] === 0) seen.push(c);
      counts[c]++;
    }
    let total = 0, mx = 0;
    for (const c of seen) {
      total += counts[c] * counts[c];
      if (counts[c] > mx) mx = counts[c];
      counts[c] = 0;
    }
    exp[i] = total / n;
    max[i] = mx;
  }
  return { exp, max };
}

// --- probe search --------------------------------------------------------------

// Best NON-candidate probe word from ALL_GUESSES. Returns {expRemaining, word} or null.
export function bestProbeWords(ALL_GUESSES, answers, PM, N, poolIndex, avoidDoubles, tailLambda) {
  const na = answers.length;
  if (na === 0 || !ALL_GUESSES.length) return null;
  const aIdx = answerIndices(answers, poolIndex);
  const answerSet = new Set(answers);
  const counts = new Int32Array(243);
  const seen = [];
  let best = null; // [score, word, exp]
  for (let gi = 0; gi < ALL_GUESSES.length; gi++) {
    const g = ALL_GUESSES[gi];
    if (answerSet.has(g)) continue; // pure probes only
    if (avoidDoubles && hasRepeat(g)) continue;
    const gPoolIdx = poolIndex.get(g);
    seen.length = 0;
    for (let s = 0; s < na; s++) {
      const code = gPoolIdx !== undefined
        ? pmCode(PM, N, gPoolIdx, aIdx[s])
        : compareCode(g, answers[s]);
      if (counts[code] === 0) seen.push(code);
      counts[code]++;
    }
    let total = 0, mx = 0;
    for (const c of seen) {
      total += counts[c] * counts[c];
      if (tailLambda > 0 && counts[c] > mx) mx = counts[c];
      counts[c] = 0;
    }
    const exp = total / na;
    const score = tailLambda > 0 ? (1 - tailLambda) * exp + tailLambda * mx : exp;
    if (best === null || score < best[0] || (score === best[0] && g < best[1])) {
      best = [score, g, exp];
    }
  }
  return best ? { expRemaining: best[2], word: best[1] } : null;
}

function findProbe(ALL_GUESSES, answers, PM, N, poolIndex, avoidDoubles) {
  // probe_source == "expanded" (default): use ALL_GUESSES.
  return bestProbeWords(ALL_GUESSES, answers, PM, N, poolIndex, avoidDoubles, STRATEGY.probe_tail_lambda);
}

// --- lookahead -----------------------------------------------------------------

function lookaheadChoice(answers, PM, N, poolIndex, ALL_GUESSES, avoidDoubles) {
  const n = answers.length;
  const idx = answerIndices(answers, poolIndex);
  // P[g][s] = code(answers[g] vs answers[s])
  const P = new Array(n);
  for (let g = 0; g < n; g++) {
    const row = new Int32Array(n);
    for (let s = 0; s < n; s++) row[s] = pmCode(PM, N, idx[g], idx[s]);
    P[g] = row;
  }
  const memo = new Map();

  function cost(idxTuple) {
    // idxTuple: sorted array of local indices; key by join.
    if (idxTuple.length === 1) return 1.0;
    const key = idxTuple.join(",");
    const cached = memo.get(key);
    if (cached !== undefined) return cached;
    const m = idxTuple.length;
    let best = Infinity;
    for (const g of idxTuple) {
      const buckets = new Map();
      for (const s of idxTuple) {
        const pat = P[g][s];
        let arr = buckets.get(pat);
        if (!arr) { arr = []; buckets.set(pat, arr); }
        arr.push(s);
      }
      let c = 1.0;
      for (const [, T] of buckets) {
        if (T.length === 1 && T[0] === g) continue;
        c += (T.length / m) * cost(T);
      }
      if (c < best) best = c;
      if (best === 1.0) break;
    }
    memo.set(key, best);
    return best;
  }

  let cand = [];
  for (let i = 0; i < n; i++) cand.push(i);
  if (avoidDoubles) {
    const nd = cand.filter((i) => !hasRepeat(answers[i]));
    if (nd.length) cand = nd;
  }
  const full = [];
  for (let i = 0; i < n; i++) full.push(i);

  let bestG = null, bestC = Infinity;
  for (const g of cand) {
    const buckets = new Map();
    for (const s of full) {
      const pat = P[g][s];
      let arr = buckets.get(pat);
      if (!arr) { arr = []; buckets.set(pat, arr); }
      arr.push(s);
    }
    let c = 1.0;
    for (const [, T] of buckets) {
      if (T.length === 1 && T[0] === g) continue;
      c += (T.length / n) * cost(T);
    }
    if (c < bestC || (c === bestC && answers[g] < answers[bestG])) {
      bestC = c;
      bestG = g;
    }
  }

  const exp = expectedRemainingAll(answers, PM, N, poolIndex);
  let choice = {
    word: answers[bestG],
    expRemaining: exp[bestG],
    reduction: n - exp[bestG],
    scored: true,
    probe: false,
  };

  // best probe by expected guesses-to-solve (hard mode: single best probe)
  const probe = findProbe(ALL_GUESSES, answers, PM, N, poolIndex, avoidDoubles);
  if (probe) {
    const pIdx = poolIndex.get(probe.word);
    const buckets = new Map();
    for (let s = 0; s < n; s++) {
      const code = pIdx !== undefined ? pmCode(PM, N, pIdx, idx[s]) : compareCode(probe.word, answers[s]);
      let arr = buckets.get(code);
      if (!arr) { arr = []; buckets.set(code, arr); }
      arr.push(s);
    }
    let cp = 1.0;
    for (const [, T] of buckets) cp += (T.length / n) * cost(T);
    if (cp < bestC) {
      choice = {
        word: probe.word,
        expRemaining: probe.expRemaining,
        reduction: n - probe.expRemaining,
        scored: true,
        probe: true,
      };
    }
  }
  return choice;
}

// --- best guess for one set ----------------------------------------------------

export function bestGuessForSet(answers, PM, N, poolIndex, ALL_GUESSES, PLURALS, opts) {
  opts = opts || {};
  const avoidDoubles = !!opts.avoidDoubles;
  const board = opts.board == null ? null : opts.board;

  if (!answers.length) return null;
  if (answers.length === 1) {
    return { word: answers[0], expRemaining: 1, reduction: 0, scored: true };
  }

  const nLa = answers.length;
  const dangerous =
    STRATEGY.danger_lookahead &&
    nLa >= STRATEGY.danger_min_n &&
    freePositions(answers) <= STRATEGY.danger_free_max;

  if (2 <= nLa && nLa <= STRATEGY.lookahead_max &&
      (STRATEGY.danger_lookahead && dangerous)) {
    return lookaheadChoice(answers, PM, N, poolIndex, ALL_GUESSES, avoidDoubles);
  }

  // Exclude double-letter words from SELECTION for W1-3 (but score over all).
  const nd = avoidDoubles ? answers.filter((w) => !hasRepeat(w)) : [];

  let exp, score;
  if (STRATEGY.tail_lambda > 0) {
    const r = expectedAndMaxAll(answers, PM, N, poolIndex);
    exp = r.exp;
    score = new Float64Array(answers.length);
    for (let i = 0; i < answers.length; i++) {
      score[i] = (1 - STRATEGY.tail_lambda) * r.exp[i] + STRATEGY.tail_lambda * r.max[i];
    }
  } else {
    exp = expectedRemainingAll(answers, PM, N, poolIndex);
    score = exp;
  }

  // Selectable indices: W1-3 exclude doubles from the final pick.
  const sel = (avoidDoubles && nd.length)
    ? answers.map((_, i) => i).filter((i) => !hasRepeat(answers[i]))
    : answers.map((_, i) => i);

  let bestI = sel[0];
  for (let k = 1; k < sel.length; k++) {
    const i = sel[k];
    if (score[i] < score[bestI] || (score[i] === score[bestI] && answers[i] < answers[bestI])) {
      bestI = i;
    }
  }

  // Plural endgame tie-break — W5 only. W5 is a plural in 0/1000 current-era games;
  // swapping a plural best-pick to a near-tied non-plural is pure upside.
  if (STRATEGY.plural_endgame_avoid && board === 4 && answers.length <= STRATEGY.plural_endgame_max_n) {
    if (PLURALS.has(answers[bestI])) {
      const thr = score[bestI] + STRATEGY.plural_endgame_eps;
      let alt = null;
      for (const i of sel) {
        if (PLURALS.has(answers[i]) || score[i] > thr) continue;
        if (alt === null || score[i] < score[alt] ||
            (score[i] === score[alt] && answers[i] < answers[alt])) {
          alt = i;
        }
      }
      if (alt !== null) bestI = alt;
    }
  }

  const candExp = exp[bestI];
  const bestWord = answers[bestI];
  const n = answers.length;
  const trigger = STRATEGY.probe_trigger;

  if (STRATEGY.probe_floor <= n && n <= STRATEGY.probe_cap && candExp > trigger) {
    const probe = findProbe(ALL_GUESSES, answers, PM, N, poolIndex, avoidDoubles);
    if (probe && probe.expRemaining < candExp - 1.0 / n) {
      return {
        word: probe.word,
        expRemaining: probe.expRemaining,
        reduction: n - probe.expRemaining,
        scored: true,
        probe: true,
      };
    }
  }

  return {
    word: bestWord,
    expRemaining: candExp,
    reduction: n - candExp,
    scored: true,
    probe: false,
  };
}

// --- top candidates ------------------------------------------------------------

export function topCandidates(answers, PM, N, poolIndex, n = 5, avoidDoubles = false) {
  if (!answers.length) return [];
  if (answers.length === 1) return [{ word: answers[0], expRemaining: 1.0, score: 1.0 }];
  const tl = STRATEGY.tail_lambda;
  let exp, score;
  if (tl > 0) {
    const r = expectedAndMaxAll(answers, PM, N, poolIndex);
    exp = r.exp;
    score = new Float64Array(answers.length);
    for (let i = 0; i < answers.length; i++) score[i] = (1 - tl) * r.exp[i] + tl * r.max[i];
  } else {
    exp = expectedRemainingAll(answers, PM, N, poolIndex);
    score = exp;
  }
  let idxs = [];
  for (let i = 0; i < answers.length; i++) idxs.push(i);
  if (avoidDoubles) {
    const nd = idxs.filter((i) => !hasRepeat(answers[i]));
    if (nd.length) idxs = nd;
  }
  idxs.sort((a, b) => (score[a] - score[b]) || (answers[a] < answers[b] ? -1 : answers[a] > answers[b] ? 1 : 0));
  return idxs.slice(0, n).map((i) => ({
    word: answers[i],
    expRemaining: round2(exp[i]),
    score: round2(score[i]),
    probe: false,
  }));
}

// Top-n guesses for one board, INCLUDING the best probe when a probe is the
// solver's actual pick (topCandidates alone only ranks in-set answers, so a
// winning probe would otherwise be invisible in the per-word view).
export function topGuessesForBoard(answers, PM, N, poolIndex, ALL_GUESSES, PLURALS,
                                  n = 5, avoidDoubles = false, board = null) {
  const top = topCandidates(answers, PM, N, poolIndex, n, avoidDoubles);
  const best = bestGuessForSet(answers, PM, N, poolIndex, ALL_GUESSES, PLURALS, { avoidDoubles, board });
  if (best && best.probe && !top.some((t) => t.word === best.word)) {
    top.unshift({
      word: best.word,
      expRemaining: round2(best.expRemaining),
      score: round2(best.expRemaining),
      probe: true,
    });
    if (top.length > n) top.length = n;
  }
  return top;
}

// --- best guess across boards --------------------------------------------------

// `ctx` (optional) = { slots, clueGrid, pool } — the live game state. When given
// (and STRATEGY.resolve_tiebreak is on), the endgame tie-break refines the chosen
// board's word; without it, behaviour is unchanged.
export function bestGuessAcrossBoards(res, PM, N, poolIndex, ALL_GUESSES, PLURALS, ctx) {
  if (!res || !res.solvable) return [];

  const out = [];
  for (let board = 0; board < res.perSlotFeasible.length; board++) {
    const answers = res.perSlotFeasible[board];
    if (answers.length <= 1) continue;
    const avoid = board < 3 && STRATEGY.avoid_doubles_w13;
    const best = bestGuessForSet(answers, PM, N, poolIndex, ALL_GUESSES, PLURALS, {
      avoidDoubles: avoid,
      board,
    });
    if (best) {
      out.push({ board, setSize: answers.length, ...best });
    }
  }

  // Board ordering: frac objective = expRemaining/setSize, ascending.
  // Prefer W5 (board 4) when tied — pinning it collapses the clue-grid coupling.
  function metric(e) {
    if (e.expRemaining == null) return -e.reduction;
    return e.expRemaining / e.setSize;
  }

  out.sort((a, b) => {
    const ma = metric(a), mb = metric(b);
    if (ma !== mb) return ma - mb;
    if (a.board !== b.board) return b.board - a.board;
    return a.word < b.word ? -1 : a.word > b.word ? 1 : 0;
  });

  resolveTiebreak(out, res, PM, N, poolIndex, PLURALS, ctx);
  return out;
}

// [resolve tie-break] Endgame refinement of ONLY the chosen board's word: among
// its candidate answers tied on own-board frac, prefer the one that — if it is
// the answer — collapses the most OTHER boards via the clue coupling. The board
// being attacked is unchanged (perturbation-free); this replaces the lexical
// tiebreak with a meaningful one. Mutates out[0].word in place.
function resolveTiebreak(out, res, PM, N, poolIndex, PLURALS, ctx) {
  if (!STRATEGY.resolve_tiebreak || !ctx || !out.length || out[0].probe) return;

  const totalRemaining = res.perSlotFeasible.reduce((sum, s) => sum + s.length, 0);
  if (totalRemaining > STRATEGY.resolve_tiebreak_cap) return;

  const top = out[0];
  const b = top.board;
  const answers = res.perSlotFeasible[b];
  const nb = answers.length;
  if (nb < 2) return;

  const ex = expectedRemainingAll(answers, PM, N, poolIndex);
  let minEx = Infinity;
  for (let i = 0; i < nb; i++) if (ex[i] < minEx) minEx = ex[i];
  const thr = minEx / nb + STRATEGY.resolve_tiebreak_eps / nb;
  const tied = [];
  for (let i = 0; i < nb; i++) if (ex[i] / nb <= thr) tied.push(answers[i]);
  if (tied.length < 2) return;

  const { slots, clueGrid, pool } = ctx;
  // Other boards pinned to a single answer if `w` is correct (all-green on b).
  const otherPins = (w) => {
    const sim = slots.map((s) => ({ guesses: (s.guesses || []).slice() }));
    sim[b].guesses.push({ word: w, colors: "22222" });
    const r = solve(sim, clueGrid, pool, PM, N, poolIndex, PLURALS);
    if (!r.solvable) return 0;
    let c = 0;
    for (let j = 0; j < r.perSlotFeasible.length; j++) {
      if (j !== b && r.perSlotFeasible[j].length === 1) c++;
    }
    return c;
  };

  // `tied` is sorted ascending, so a strict ">" keeps the lexically-smallest of
  // the words that pin the most other boards (matches the Python first-char key).
  let pick = tied[0];
  let pickPins = otherPins(tied[0]);
  for (let k = 1; k < tied.length; k++) {
    const p = otherPins(tied[k]);
    if (p > pickPins) { pick = tied[k]; pickPins = p; }
  }
  if (pick !== top.word) top.word = pick;
}
