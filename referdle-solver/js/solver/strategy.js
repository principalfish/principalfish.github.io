// Frozen strategy constants — the active shipped defaults from referdle/strategy.py.
// Only the fields actually referenced in the JS port are kept; unported levers
// (cross_board, scoring_switch, lookahead_soft, hicombo, forced_opener, …) are
// omitted because their branches are not implemented here.

export const STRATEGY = Object.freeze({
  // ── Probe / sacrifice guesses ──────────────────────────────────────────────
  probe_trigger: 1.5,        // probe when best candidate leaves > this many expected remaining
  probe_cap: 220,            // only probe when candidate set is this size or smaller
                             // (raised from 140: pool-world optimum, ~-0.02 current-era)
  probe_floor: 4,            // minimum set size worth probing (n<4 a probe can't win)
  probe_tail_lambda: 0.0,    // 0 = pure-expected probe scoring (no worst-case blend)

  // ── Lookahead ──────────────────────────────────────────────────────────────
  danger_lookahead: true,    // exact expected-guesses-to-solve for dense stuck clusters
  danger_free_max: 2,        // "low-dimensional" = ≤ this many free positions
  danger_min_n: 4,           // minimum cluster size to treat as dangerous
  lookahead_max: 14,         // hard cap on cluster size for exact lookahead

  // ── Doubles avoidance ─────────────────────────────────────────────────────
  avoid_doubles_w13: true,   // W1-3: exclude repeat-letter guesses unless forced
  prune_w13_doubles: true,   // W1-3 candidate prune: ~0% doubles as answers
  prune_w5_plurals: false,   // W5 candidate prune: ~0% plurals as W5 answers (off)
  prune_w13_combos: false,   // W1-3 hicombo+midcombo prune (off)

  // ── Scoring ────────────────────────────────────────────────────────────────
  tail_lambda: 0.5,          // blend: 0=pure-expected, 1=minimax worst-case bucket

  // ── Plural endgame tie-break (W5 only) ────────────────────────────────────
  plural_endgame_avoid: true, // swap best W5 pick to a near-tied non-plural
  plural_endgame_max_n: 8,    // only when candidate set is this small
  plural_endgame_eps: 0.5,    // "near tie" tolerance on expected-remaining
  w4_double_tiebreak: true,   // W4 only: prefer a near-tied double-letter candidate (W4 ~60% doubles)
  w4_double_max_n: 8,         // only when the W4 candidate set is this small
  w4_double_eps: 0.5,         // "near tie" tolerance on expected-remaining

  // ── Resolve tie-break (endgame within-board word choice) ──────────────────
  // Among the chosen board's frac-tied candidates, prefer the one that — if it
  // is the answer — pins the most OTHER boards via the clue coupling. The board
  // that gets attacked is unchanged, so this never perturbs board selection.
  resolve_tiebreak: true,      // toggle
  resolve_tiebreak_cap: 10,    // only when total remaining across boards ≤ this (matches Python; sweep found 10>15>20>30)
  resolve_tiebreak_eps: 0.25,  // own-board frac tolerance for "tied"
});

export const MAX_STEPS = 30;
export const LOOKAHEAD_MAX = 14;
