import { beforeAll, describe, expect, it } from "vitest";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { build } from "esbuild";
import { COMPONENT_DIR } from "../harness.mjs";

const solverDir = path.join(COMPONENT_DIR, "js/solver");
const fixtureDir = new URL("./fixtures/", import.meta.url);
let current, previous;

async function load({ legacy = false, overrides = {}, refined = false } = {}) {
  const output = await build({
    stdin: {
      contents: `export * from "./suggest.js"; export { STRATEGY } from "./strategy.js";
export { compareCode } from "./compare.js"; export { autoSolve, buildSteps } from "./autoplay.js";`,
      resolveDir: solverDir,
    },
    bundle: true, write: false, format: "esm", platform: "node", logLevel: "silent",
    plugins: [{
      name: "suggestion-parity",
      setup(builder) {
        builder.onLoad({ filter: /[/\\](suggest|strategy|autoplay|data|solver)\.js$/ }, async ({ path: filename }) => {
          const name = path.basename(filename);
          if (name === "data.js") return { contents: "export function pmCode(PM, N, g, a) { return PM[g * N + a]; }" };
          if (name === "solver.js" && refined) return {
            contents: `export function solve(slots) {
const word = slots.flatMap(s => s.guesses).at(-1).word;
return { solvable: true, perSlotFeasible: word === "BRISK" ? [["A"], ["B"], ["C"], ["D"], ["E"]] : [["A", "B"], ["B", "C"], ["C", "D"], ["D"], ["E", "F"]] };
}
export const solveRelaxed = solve; export function localCandidates() { return []; }`,
          };
          let contents = legacy && ["suggest.js", "autoplay.js"].includes(name)
            ? await readFile(new URL(`legacy-${name}.txt`, fixtureDir), "utf8")
            : await readFile(filename, "utf8");
          // Overrides must follow the defaults; keep the shipped strategy source authoritative.
          if (name === "strategy.js") contents = contents.replace("\n});", `\n...${JSON.stringify(overrides)}\n});`);
          if (name === "suggest.js") {
            const names = ["expectedRemainingAll", "expectedAndMaxAll", "findProbe", "lookaheadChoice"];
            for (const fn of names) {
              const marker = new RegExp(`(function ${fn}\\([^)]*\\) \\{)`);
              if (!marker.test(contents)) throw new Error(`Missing counter marker: ${fn}`);
              contents = contents.replace(marker, `$1\nbenchmarkCounts.${fn}++;`);
            }
            contents += `\nconst benchmarkCounts = ${JSON.stringify(Object.fromEntries(names.map(n => [n, 0])))};\nexport { benchmarkCounts as counts };\n`;
          }
          return { contents, resolveDir: solverDir };
        });
      },
    }],
  });
  return import(`data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text).toString("base64")}#${crypto.randomUUID()}`);
}

function state(pool, guesses = pool, synthetic) {
  const poolIndex = new Map(pool.map((word, i) => [word, i]));
  const PM = synthetic || new Uint8Array(pool.flatMap(guess => pool.map(answer => current.compareCode(guess, answer))));
  return { PM, N: pool.length, poolIndex, POOL: pool, ALL_GUESSES: guesses, PLURALS: new Set() };
}
function oldCombined(mod, res, st, ctx) {
  if (!res || !res.solvable) return { solvable: false };
  const args = [st.PM, st.N, st.poolIndex, st.ALL_GUESSES, st.PLURALS];
  return {
    solvable: true,
    ranked: mod.bestGuessAcrossBoards(res, ...args, ctx),
    perBoard: res.perSlotFeasible.flatMap((answers, board) => answers.length > 1 ? [{
      board, top: mod.topGuessesForBoard(answers, ...args, 5, board < 3 && mod.STRATEGY.avoid_doubles_w13, board),
    }] : []),
  };
}
function combined(mod, res, st, ctx) {
  return mod.buildSuggestions(res, st.PM, st.N, st.poolIndex, st.ALL_GUESSES, st.PLURALS, ctx);
}
function parity(mod, old, res, st, ctx) {
  const snapshot = structuredClone(res);
  const actual = combined(mod, res, st, ctx);
  expect(actual).toEqual(oldCombined(old, res, st, ctx));
  expect(res).toEqual(snapshot);
  const args = [st.PM, st.N, st.poolIndex, st.ALL_GUESSES, st.PLURALS];
  expect(mod.bestGuessAcrossBoards(res, ...args, ctx)).toEqual(old.bestGuessAcrossBoards(res, ...args, ctx));
  for (let board = 0; board < (res?.perSlotFeasible?.length || 0); board++) {
    const answers = res.perSlotFeasible[board];
    const avoid = board < 3 && mod.STRATEGY.avoid_doubles_w13;
    expect(mod.bestGuessForSet(answers, ...args, { avoidDoubles: avoid, board }))
      .toEqual(old.bestGuessForSet(answers, ...args, { avoidDoubles: avoid, board }));
    expect(mod.topGuessesForBoard(answers, ...args, 5, avoid, board))
      .toEqual(old.topGuessesForBoard(answers, ...args, 5, avoid, board));
    expect(mod.topCandidates(answers, st.PM, st.N, st.poolIndex, 3, avoid))
      .toEqual(old.topCandidates(answers, st.PM, st.N, st.poolIndex, 3, avoid));
  }
  return actual;
}
beforeAll(async () => { [current, previous] = await Promise.all([load(), load({ legacy: true })]); });

describe("request-local suggestion analysis", () => {
  it("preserves empty, singleton and unsolvable requests", () => {
    const st = state(["ALERT"]);
    for (const res of [null, { solvable: false }, { solvable: true, perSlotFeasible: [[], ["ALERT"]] }]) {
      parity(current, previous, res, st);
    }
    expect(current.topGuessesForBoard(["ALERT"], st.PM, st.N, st.poolIndex, st.ALL_GUESSES, st.PLURALS))
      .toEqual([{ word: "ALERT", expRemaining: 1, score: 1 }]);
  });

  it("retains exact ties, board ordering and board-specific double/plural selection", () => {
    const st = state(["ALERT", "APPLE"]);
    st.PLURALS.add("ALERT");
    const actual = parity(current, previous, { solvable: true, perSlotFeasible: Array.from({ length: 5 }, () => [...st.POOL]) }, st);
    expect(actual.ranked[0].board).toBe(4);
    expect(actual.ranked.find(r => r.board === 0).word).toBe("ALERT");
    expect(actual.ranked.find(r => r.board === 3).word).toBe("APPLE");
    expect(actual.ranked.find(r => r.board === 4).word).toBe("APPLE");
  });

  it("keeps near-tie thresholds and raw precision before display rounding", () => {
    const pool = ["ALERT", "APPLE", "BRISK", "CHORD", "DRIVE"];
    const PM = new Uint8Array(25);
    for (let g = 0; g < 5; g++) for (let a = 0; a < 5; a++) PM[g * 5 + a] = g === a ? 242 : 0;
    [0, 0, 1, 2].forEach((c, i) => { PM[i + 1] = c; });
    [0, 0, 1, 1].forEach((c, i) => { PM[5 + [0, 2, 3, 4][i]] = c; });
    const st = state(pool, pool, PM);
    st.PLURALS.add("ALERT");
    const res = { solvable: true, perSlotFeasible: [[], [], [], pool, pool] };
    const actual = parity(current, previous, res, st);
    expect(actual.ranked.every(r => r.word === "APPLE")).toBe(true);
    expect(actual.ranked[0].expRemaining).toBe(1.8);
    expect(current.expectedAndMaxAll(pool, PM, 5, st.poolIndex).exp[0]).toBe(1.4);
    expect(actual.perBoard[0].top[0]).toEqual({ word: "ALERT", expRemaining: 1.4, score: 1.7, probe: false });
  });

  it.each([false, true])("preserves winning %s expanded probe and lookahead", expanded => {
    const answers = ["BATCH", "CATCH", "HATCH", "LATCH", "MATCH", "PATCH"];
    const st = state(expanded ? answers : [...answers, "CLAMP"], [...answers, "CLAMP"]);
    const res = { solvable: true, perSlotFeasible: [answers, [], [], [], []] };
    const actual = parity(current, previous, res, st);
    expect(actual.ranked[0]).toMatchObject({ word: "CLAMP", probe: true });
    expect(actual.perBoard[0].top[0]).toMatchObject({ word: "CLAMP", probe: true });
    expect(current.counts.lookaheadChoice).toBeGreaterThan(0);
  });

  it.each([false, true])("scores and searches a board once with lookahead=%s", async lookahead => {
    const overrides = { danger_lookahead: lookahead, resolve_tiebreak: false };
    const [mod, old] = await Promise.all([load({ overrides }), load({ legacy: true, overrides })]);
    const answers = ["BATCH", "CATCH", "HATCH", "LATCH", "MATCH", "PATCH"];
    const st = state([...answers, "CLAMP"]);
    const res = { solvable: true, perSlotFeasible: [answers] };
    expect(combined(mod, res, st)).toEqual(oldCombined(old, res, st));
    expect(mod.counts.expectedAndMaxAll).toBe(1);
    expect(old.counts.expectedAndMaxAll).toBe(lookahead ? 1 : 3);
    expect(mod.counts.findProbe).toBe(1);
    expect(old.counts.findProbe).toBe(2);
    expect(mod.counts.lookaheadChoice).toBe(lookahead ? 1 : 0);
    expect(old.counts.lookaheadChoice).toBe(lookahead ? 2 : 0);
  });

  it("preserves pure-expected scoring and forced repeat-letter eligibility", async () => {
    const overrides = { tail_lambda: 0, danger_lookahead: false };
    const [mod, old] = await Promise.all([load({ overrides }), load({ legacy: true, overrides })]);
    const answers = ["APPLE", "EERIE", "LEVEL", "MAMMA"];
    const st = state(answers, [...answers, "ALERT", "MUMMY"]);
    const actual = parity(mod, old, { solvable: true, perSlotFeasible: [answers, [], [], [], answers] }, st);
    expect(actual.ranked).toHaveLength(2);
    expect(actual.perBoard[0].top).toHaveLength(4);
    expect(actual.perBoard[0].top.every(t => st.POOL.includes(t.word))).toBe(true);
  });

  it("endgame refinement changes only ranked copies and reuses raw scores", async () => {
    const [mod, old] = await Promise.all([load({ refined: true }), load({ legacy: true, refined: true })]);
    const st = state(["ALERT", "BRISK"]);
    const res = { solvable: true, perSlotFeasible: [["ALERT", "BRISK"], ["ALERT"], ["ALERT"], ["ALERT"], ["ALERT"]] };
    const ctx = { slots: Array.from({ length: 5 }, () => ({ guesses: [] })), clueGrid: null, pool: st.POOL };
    const result = combined(mod, res, st, ctx);
    expect(result).toEqual(oldCombined(old, res, st, ctx));
    expect(result.ranked[0].word).toBe("BRISK");
    expect(result.perBoard[0].top[0].word).toBe("ALERT");
    expect(mod.counts.expectedRemainingAll).toBe(0);
    expect(old.counts.expectedRemainingAll).toBe(1);
    expect(ctx.slots.every(s => s.guesses.length === 0)).toBe(true);
  });

  it("retains autoplay moves and full per-step suggestions", () => {
    const pool = ["ABCDE", "FGHIJ", "KLMNO", "PQQRS", "TTUVW", "LEVEL", "ALERT", "BRISK"];
    const st = state(pool);
    const answers = pool.slice(0, 5);
    const result = current.autoSolve(answers, st);
    expect(result.sequence.length).toBeGreaterThan(0);
    expect(result).toEqual(previous.autoSolve(answers, st));
    const slots = Array.from({ length: 5 }, () => ({ guesses: [] }));
    const steps = current.buildSteps(result, slots, st);
    expect(steps.steps.some(step => step.suggest.perBoard?.length > 0)).toBe(true);
    expect(steps).toEqual(previous.buildSteps(result, slots, st));
  });
});
