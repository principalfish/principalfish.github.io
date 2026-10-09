import { describe, expect, it } from "vitest";
import { createControllerBundle, createUIDoubles, driveController } from "../harness.mjs";
import { normalizeTrace } from "../trace.mjs";

const GAMES = {
  1000: ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEE"],
  1001: ["FFFFF", "GGGGG", "HHHHH", "IIIII", "JJJJJ"],
};

function replacements(flags, scenario) {
  return {
    "strategy.js": `export const STRATEGY = {
  prune_w13_doubles: ${flags[0]}, prune_w5_plurals: ${flags[1]}, prune_w13_combos: ${flags[2]}, avoid_doubles_w13: false,
};`,
    "data.js": `const games = ${JSON.stringify(GAMES)};
export let currentWords = games[1000];
export async function dailyGame(state, day) { currentWords = games[day]; return currentWords; }
export function dailyClueGrid() { return Array(5).fill("22222"); }
export async function loadAssets() { return {}; }`,
    "solver.js": `import { capture } from "benchmark:capture";
import { currentWords } from "./data.js";
const scenario = ${JSON.stringify(scenario)};
export function solve(slots, grid, pool, PM, N, poolIndex, plurals, p13, p5, pc) {
  const flags = [p13, p5, pc];
  const key = flags.map(Number).join("");
  const count = slots.reduce((sum, slot) => sum + slot.guesses.length, 0);
  (capture.calls ??= []).push({ count, flags, words: currentWords.slice(), slots: structuredClone(slots), grid: grid.slice() });
  const failed = scenario === "unsolvable" || (flags.some(Boolean) && (scenario === "relax" || (scenario === "sticky" && count === 0)));
  const limit = scenario === "ordinary" ? 5 : scenario === "sticky" && !flags.some(Boolean) ? 2 : 1;
  const candidates = currentWords.map((word, b) => count >= limit || slots[b].guesses.length ? [word] : [word, "ZZZZZ"]);
  return {
    solvable: !failed, reason: failed ? "injected conflict" : null, note: key + ":" + count,
    cands: candidates, perSlotFeasible: failed ? [[], [], [], [], []] : candidates,
    viableFinals: failed ? [] : candidates[4], unionFeasible: failed ? [] : currentWords.slice(),
    candCounts: candidates.map((words) => words.length), clueUsed: true,
  };
}
export function solveRelaxed() { throw new Error("Unexpected uncached solveRelaxed call"); }`,
    "suggest.js": `import { capture } from "benchmark:capture";
export function bestGuessAcrossBoards(res, PM, N, poolIndex, guesses) {
  (capture.probeSizes ??= []).push(guesses.length);
  const board = res.perSlotFeasible.findIndex((words) => words.length > 1);
  return board < 0 ? [] : [{ board, word: res.perSlotFeasible[board][0], setSize: 2, expRemaining: 1.0000000000000002, probe: false }];
}
export function topGuessesForBoard(answers) { return [{ word: answers[0], expRemaining: 1, score: 1, probe: false }]; }
export function buildSuggestions(res, PM, N, poolIndex, guesses) {
  const ranked = bestGuessAcrossBoards(res, PM, N, poolIndex, guesses);
  const perBoard = res.perSlotFeasible.flatMap((answers, board) => answers.length > 1
    ? [{ board, top: topGuessesForBoard(answers) }] : []);
  return { solvable: true, ranked, perBoard };
}`,
  };
}

async function setup({ flags = [true, false, false], scenario = "normal" } = {}) {
  const module = await createControllerBundle({ replacements: replacements(flags, scenario) });
  const ui = createUIDoubles(false);
  const pool = Object.values(GAMES).flat();
  const state = {
    POOL: pool, ALL_GUESSES: [...pool, "ZZZZZ"], PM: new Uint8Array(), N: pool.length,
    poolIndex: new Map(pool.map((word, i) => [word, i])), PLURALS: new Set(),
  };
  const controller = module.initDailyMode(state, ui.manual, ui.clueUI, ui.uiEls);
  await controller.loadDay(1000);
  return { module, controller, ...ui };
}

describe("daily solve reuse", () => {
  it("solves each position once while preserving ordinary/closing moves and old snapshots", async () => {
    const { module, controller, manual, clueUI } = await setup();
    controller.nextTurn();
    const firstAfter = module.capture.publication.steps[0].after;
    const saved = structuredClone(firstAfter);
    expect(module.capture.calls.map((call) => call.count)).toEqual([0, 1]);
    controller.nextTurn();
    expect(module.capture.calls.map((call) => call.count)).toEqual([0, 1, 2]);
    expect(module.capture.publication.moves.map((move) => move.isClosing)).toEqual([false, true]);
    expect(module.capture.publication.steps[0].after).toBe(firstAfter);
    expect(firstAfter).toEqual(saved);
    for (let i = 0; i < 3; i++) controller.nextTurn();
    const result = { publication: module.capture.publication, finalResult: module.capture.lastResult };
    expect(module.capture.calls.map((call) => call.count)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(result.publication.moves.map((move) => move.word)).toEqual(GAMES[1000]);
    expect(result.publication.moves.map((move) => move.isClosing)).toEqual([false, true, true, true, true]);
    expect(result.finalResult.note).toBe("100:5");
    expect(firstAfter).toEqual(saved);
    const trace = normalizeTrace({ day: 1000, answers: GAMES[1000], expanded: false, clueGrid: clueUI.getClueGrid(), ...result, finalSlots: manual.getSlots() });
    expect(trace.final.status).toBe("solved");
    expect(trace.moves.map((move) => move.before.note)).toEqual(["100:0", "100:1", "100:2", "100:3", "100:4"]);
  });

  it.each([
    { flags: [true, false, false] }, { flags: [false, true, false] },
    { flags: [false, false, true] }, { flags: [true, true, true] },
  ])("keys successful and failed solves by all three pruning booleans %j", async ({ flags }) => {
    const { module, controller } = await setup({ flags, scenario: "relax" });
    const result = driveController(controller, module.capture);
    const initial = module.capture.calls.filter((call) => call.count === 0).map((call) => call.flags);
    const expectedInitial = [flags];
    if (flags[2]) expectedInitial.push([flags[0], flags[1], false]);
    if (flags[0] || flags[1]) expectedInitial.push([false, false, false]);
    expect(initial).toEqual(expectedInitial);
    for (let count = 1; count <= 5; count++) {
      expect(module.capture.calls.filter((call) => call.count === count).map((call) => call.flags))
        .toEqual([flags, [false, false, false]]);
    }
    expect(result.publication.steps.map((step) => step.after.note)).toEqual(["000:1", "000:2", "000:3", "000:4", "000:5"]);
    expect(result.finalResult).toBe(result.publication.steps.at(-1).after);
    expect(result.finalResult.note).toBe("000:5");
    expect(result.finalResult.solvable).toBe(true);
  });

  it("keeps sticky move selection distinct from a solvable global-default display result", async () => {
    const { module, controller } = await setup({ scenario: "sticky" });
    controller.nextTurn();
    const firstAfter = module.capture.publication.steps[0].after;
    expect(firstAfter.note).toBe("100:1");
    expect(firstAfter.perSlotFeasible.every((words) => words.length === 1)).toBe(true);
    controller.nextTurn();
    expect(module.capture.publication.moves[1].isClosing).toBe(false);
    expect(module.capture.publication.steps[1].suggest.__benchmarkBefore.note).toBe("000:1");
    expect(module.capture.calls.filter((call) => call.count === 1).map((call) => call.flags))
      .toEqual([[true, false, false], [false, false, false]]);
    expect(firstAfter.note).toBe("100:1");
    expect(module.capture.publication.steps[1].after.note).toBe("100:2");
  });

  it("does not invalidate the live position when scrubbing display history", async () => {
    const { module, controller } = await setup();
    controller.nextTurn();
    controller.nextTurn();
    const count = module.capture.calls.length;
    controller.scrubTo(0);
    controller.next();
    controller.prev();
    expect(module.capture.calls).toHaveLength(count);
    controller.nextTurn();
    expect(module.capture.calls.map((call) => call.count)).toEqual([0, 1, 2, 3]);
    expect(module.capture.calls.at(-1).slots.map((slot) => slot.guesses.length)).toEqual([1, 1, 1, 0, 0]);
  });

  it("discards cached positions on reset/probe changes and loading another day", async () => {
    const { module, controller, uiEls } = await setup();
    controller.nextTurn();
    controller.resetSolve();
    uiEls.expandedToggle.checked = true;
    controller.nextTurn();
    expect(module.capture.calls.map((call) => call.count)).toEqual([0, 1, 0, 1]);
    expect(module.capture.probeSizes).toEqual([10, 11]);
    await controller.loadDay(1001);
    controller.nextTurn();
    expect(module.capture.calls.map((call) => call.count)).toEqual([0, 1, 0, 1, 0, 1]);
    expect(module.capture.calls.at(-1).words).toEqual(GAMES[1001]);
    expect(module.capture.publication.moves.map((move) => move.word)).toEqual([GAMES[1001][0]]);
  });

  it("observes the full unpruned unsolvable terminal without playing a move", async () => {
    const { module, controller } = await setup({ scenario: "unsolvable" });
    const result = driveController(controller, module.capture);
    expect(module.capture.calls.map((call) => call.flags)).toEqual([[true, false, false], [false, false, false]]);
    expect(result.publication.moves).toEqual([]);
    expect(result.finalResult.note).toBe("000:0");
    expect(result.finalResult.solvable).toBe(false);
    expect(result.finalResult.cands).toHaveLength(5);
  });

  it("observes a cached terminal result when all answers were already played", async () => {
    const { module, controller } = await setup({ scenario: "ordinary" });
    const result = driveController(controller, module.capture);
    expect(module.capture.calls.map((call) => call.count)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(result.publication.moves.map((move) => move.isClosing)).toEqual([false, false, false, false, false]);
    expect(result.turnMs).toHaveLength(6);
    expect(result.finalResult).toBe(result.publication.steps.at(-1).after);
    expect(result.finalResult.note).toBe("100:5");
    expect(result.finalResult.solvable).toBe(true);
  });
});
