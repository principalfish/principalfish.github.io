import { afterEach, describe, expect, it, vi } from "vitest";
import { createControllerBundle, createUIDoubles } from "../harness.mjs";

const WORDS = ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEE"];
const replacements = {
  "strategy.js": `export const STRATEGY = { prune_w13_doubles: true, prune_w5_plurals: true, prune_w13_combos: true };`,
  "data.js": `export async function dailyGame(state, day) { return day === 1000 ? ${JSON.stringify(WORDS)} : null; }
export function dailyClueGrid() { return Array(5).fill("22222"); }
export async function loadAssets() { return {}; }`,
  "solver.js": `import { capture } from "benchmark:capture";
const words = ${JSON.stringify(WORDS)};
function result(slots, flags, relaxed) {
  (capture.calls ??= []).push({ slots: structuredClone(slots), flags, relaxed });
  const cands = words.map((w, b) => slots[b].guesses.length ? [w] : [w, "ZZZZZ"]);
  const failed = !relaxed && flags.some(Boolean);
  return { solvable: !failed, reason: failed ? "pruned" : undefined, cands,
    perSlotFeasible: cands, viableFinals: cands[4], unionFeasible: words.slice(), candCounts: cands.map(a => a.length),
    clueUsed: true, precise: 1.0000000000000002, missing: undefined, negativeZero: -0, infinite: Infinity };
}
export function solve(slots, grid, pool, PM, N, index, plurals, ...flags) { return result(slots, flags.slice(0, 3), false); }
export function solveRelaxed(slots) { return result(slots, [], true); }`,
  "suggest.js": `import { capture } from "benchmark:capture";
export function buildSuggestions(res, PM, N, index, guesses) {
  (capture.probeSizes ??= []).push(guesses.length);
  const board = res.perSlotFeasible.findIndex(a => a.length > 1);
  return { ranked: board < 0 ? [] : [{ board, word: res.perSlotFeasible[board][0], probe: false, setSize: 2, expRemaining: 1.0000000000000002 }],
    perBoard: res.perSlotFeasible.flatMap((a, b) => a.length > 1 ? [{ board: b, top: [{ word: a[0], score: -0 }] }] : []) };
}`,
};
const fresh = () => WORDS.map(() => ({ guesses: [] }));
const state = () => ({ POOL: WORDS.slice(), ALL_GUESSES: [...WORDS, "ZZZZZ"],
  PM: new Uint8Array(), N: 5, poolIndex: new Map(WORDS.map((w, i) => [w, i])), PLURALS: new Set() });
async function setup() {
  const module = await createControllerBundle({ replacements });
  return { module, compute: module.createComputeService(state()) };
}

describe("DOM-free direct computation", () => {
  it("distinguishes unavailable days and invalid requests from game results", async () => {
    const { compute } = await setup();
    expect(await compute.request({ type: "load-day", day: 999 })).toEqual({ status: "unavailable-day", day: 999 });
    expect(() => compute.request({ type: "advance-daily" })).toThrow("No daily puzzle");
    expect(() => compute.request({ type: "unknown" })).toThrow("Unknown computation");
    expect(compute.request({ type: "initialize" })).toEqual(compute.request({ type: "catalogue" }));
    const catalogue = compute.request({ type: "catalogue" });
    catalogue.pool.pop();
    expect(compute.request({ type: "catalogue" }).pool).toEqual(WORDS);
  });

  it("restores sticky fallback state and continues identically without replaying history", async () => {
    const { module, compute } = await setup();
    await compute.request({ type: "load-day", day: 1000 });
    const first = compute.request({ type: "advance-daily", expanded: false });
    expect(first.continuation.pruneW13).toBe(false);
    expect(first.continuation.pruneW5).toBe(false);
    expect(first.continuation.pruneCombos).toBe(false);
    expect(first.continuation.lastPlayedWords).toEqual([WORDS[0], null, null, null, null]);
    expect(first.continuation).not.toHaveProperty("moves");
    expect(first.continuation).not.toHaveProperty("solveCache");
    const next = compute.request({ type: "advance-daily", expanded: false });
    const restored = module.createComputeService(state());
    const count = module.capture.calls.length;
    restored.request({ type: "restore-daily", day: 1000, continuation: first.continuation });
    expect(module.capture.calls).toHaveLength(count);
    expect(restored.request({ type: "advance-daily", expanded: false })).toEqual(next);
  });

  it("isolates replies and restore inputs while preserving exact numeric and missing values", async () => {
    const { module, compute } = await setup();
    await compute.request({ type: "load-day", day: 1000 });
    const first = compute.request({ type: "advance-daily", expanded: false });
    expect(first.after.precise).toBe(1.0000000000000002);
    expect(Object.hasOwn(first.after, "missing")).toBe(true);
    expect(first.after.missing).toBeUndefined();
    expect(Object.is(first.after.negativeZero, -0)).toBe(true);
    expect(first.after.infinite).toBe(Infinity);
    const snapshot = structuredClone(first);
    first.after.perSlotFeasible[1].pop();
    first.continuation.slots[0].guesses[0].word = "ZZZZZ";
    first.suggest.ranked[0].word = "ZZZZZ";
    const next = compute.request({ type: "advance-daily", expanded: false });
    expect(next.before.perSlotFeasible[1]).toEqual([WORDS[1], "ZZZZZ"]);
    expect(next.continuation.slots[0].guesses[0].word).toBe(WORDS[0]);
    const other = module.createComputeService(state());
    other.request({ type: "restore-daily", day: 1000, continuation: snapshot.continuation });
    snapshot.continuation.slots[0].guesses.length = 0;
    expect(other.request({ type: "advance-daily", expanded: false })).toEqual(next);
  });

  it("restores closing queues and done positions without guessing or replaying", async () => {
    const pinned = { ...replacements, "solver.js": `export function solve() { const cands = ${JSON.stringify(WORDS)}.map(w => [w]); return { solvable: true, cands, perSlotFeasible: cands }; } export const solveRelaxed = solve;` };
    const module = await createControllerBundle({ replacements: pinned });
    const compute = module.createComputeService(state());
    await compute.request({ type: "load-day", day: 1000 });
    const first = compute.request({ type: "advance-daily", expanded: true });
    expect(first.move.isClosing).toBe(true);
    expect(first.continuation.closingQueue).toEqual([1, 2, 3, 4]);
    const restored = module.createComputeService(state());
    restored.request({ type: "restore-daily", day: 1000, continuation: first.continuation });
    let last;
    for (let b = 1; b < 5; b++) {
      last = restored.request({ type: "advance-daily", expanded: true });
      expect(last.move).toMatchObject({ board: b, word: WORDS[b], isClosing: true });
    }
    expect(last.continuation.done).toBe(true);
    const done = module.createComputeService(state());
    done.request({ type: "restore-daily", day: 1000, continuation: last.continuation });
    expect(done.request({ type: "advance-daily" }).move).toBeUndefined();
  });

  it("resets private daily cache and position", async () => {
    const { module, compute } = await setup();
    await compute.request({ type: "load-day", day: 1000 });
    const first = compute.request({ type: "advance-daily", expanded: false });
    compute.request({ type: "reset-daily" });
    const count = module.capture.calls.length;
    expect(compute.request({ type: "advance-daily", expanded: false })).toEqual(first);
    expect(module.capture.calls.length).toBeGreaterThan(count);
  });
});

describe("manual computation and controller", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
  async function controllerSetup() {
    const { module, compute } = await setup();
    const ui = createUIDoubles(false);
    vi.stubGlobal("requestAnimationFrame", (f) => f());
    const controller = module.initManualMode(state(), ui.manual, ui.clueUI, ui.uiEls, compute);
    return { module, compute, controller, ...ui };
  }

  it("keeps carried boards and incomplete or blank input on demand", async () => {
    const { module, controller, manual, uiEls } = await controllerSetup();
    manual.addGuess(0, WORDS[0], "22222");
    controller.notifyReady();
    expect(manual.getSlots()[0].guesses).toHaveLength(1);
    expect(module.capture.calls).toBeUndefined();
    manual.incompleteBoards = () => [2];
    await controller.onEdit();
    expect(uiEls.statusEl.textContent).toContain("Incomplete word on board 2");
    expect(module.capture.calls).toBeUndefined();
    manual.incompleteBoards = () => [];
    controller.reset();
    await controller.onEdit();
    expect(module.capture.calls).toBeUndefined();
  });

  it("analyses clue-only input and invalidates probe choices on refresh", async () => {
    const { module, controller, clueUI, uiEls } = await controllerSetup();
    clueUI.setClueGrid(["10000", "00000", "00000", "00000", "00000"]);
    await controller.onEdit();
    expect(module.capture.calls).toHaveLength(1);
    expect(module.capture.probeSizes).toEqual([5]);
    expect(uiEls.suggestEl.innerHTML).toContain("Suggested next guess");
    expect(uiEls.sliderEl.disabled).toBe(true);
    uiEls.expandedToggle.checked = true;
    await controller.refreshSuggest();
    expect(module.capture.probeSizes).toEqual([5, 6]);
  });

  it("solves final state and visited prefixes only, reusing unchanged replay steps", async () => {
    const { module, controller, manual, clueUI } = await controllerSetup();
    clueUI.setClueGrid(["10000", "00000", "00000", "00000", "00000"]);
    manual.addGuess(0, WORDS[0], "22222");
    manual.addGuess(1, WORDS[1], "22222");
    await controller.onEdit();
    expect(module.capture.calls).toHaveLength(2);
    expect(module.capture.calls.map(c => c.slots.reduce((n, s) => n + s.guesses.length, 0))).toEqual([2, 1]);
    const beforeVisit = module.capture.calls.length;
    controller.scrubTo(0);
    expect(module.capture.calls.length).toBe(beforeVisit + 2);
    expect(module.capture.step.move.setSize).toBe(2);
    expect(module.capture.step.move.expRemaining).toBe(1);
    const visited = module.capture.calls.length;
    controller.next();
    controller.prev();
    expect(module.capture.calls).toHaveLength(visited);
    controller.next();
    await controller.onEdit();
    expect(module.capture.calls).toHaveLength(visited + 1);
    controller.scrubTo(0);
    expect(module.capture.calls).toHaveLength(visited + 1);
  });

  it("invalidates an earlier changed move and changed clue while retaining a shared prefix", async () => {
    const { module, compute } = await setup();
    const slots = fresh();
    slots[0].guesses.push({ word: WORDS[0], colors: "22222" });
    slots[1].guesses.push({ word: WORDS[1], colors: "22222" });
    const clueGrid = ["10000", "00000", "00000", "00000", "00000"];
    compute.request({ type: "analyse-manual", slots, clueGrid, expanded: false });
    compute.request({ type: "replay-manual", index: 0, clueGrid, expanded: false });
    compute.request({ type: "replay-manual", index: 1, clueGrid, expanded: false });
    slots[1].guesses[0].colors = "00000";
    const edited = compute.request({ type: "analyse-manual", slots, clueGrid, expanded: false });
    expect(edited.steps[0].suggest).toBeDefined();
    expect(edited.steps[1].suggest).toBeUndefined();
    expect(edited.moves[1].setSize).toBeUndefined();
    clueGrid[0] = "20000";
    const changed = compute.request({ type: "analyse-manual", slots, clueGrid, expanded: false });
    expect(changed.steps[0].after).toBeUndefined();
    expect(changed.steps[0].suggest).toBeUndefined();
    expect(module.capture.calls.every(c => c.relaxed)).toBe(true);
  });

  it("retains daily re-click-to-skip pacing and scrub cancellation", async () => {
    const { module, compute } = await setup();
    const ui = createUIDoubles(false);
    const daily = module.initDailyMode(state(), ui.manual, ui.clueUI, ui.uiEls, compute);
    await daily.loadDay(1000);
    vi.useFakeTimers();
    const run = daily.solveToEnd();
    await daily.solveToEnd(); // Re-click before first turn removes all move pacing.
    await vi.runAllTimersAsync();
    await run;
    expect(module.capture.publication.game.solved).toBe(true);
    expect(module.capture.publication.moves).toHaveLength(5);
    expect(ui.uiEls.nextTurnBtn.disabled).toBe(false);
    daily.resetSolve();
    const paced = daily.solveToEnd();
    await vi.advanceTimersByTimeAsync(0);
    expect(module.capture.publication.moves).toHaveLength(1);
    daily.scrubTo(0);
    await vi.runAllTimersAsync();
    await paced;
    expect(module.capture.publication.moves).toHaveLength(1);
    daily.nextTurn();
    expect(module.capture.publication.moves).toHaveLength(2);
  });

  it("isolates manual snapshots, retains lazy undefined fields and resets replay", async () => {
    const { compute } = await setup();
    const slots = fresh();
    slots[0].guesses.push({ word: WORDS[0], colors: "22222" });
    const analysed = compute.request({ type: "analyse-manual", slots, clueGrid: null, expanded: false });
    expect(Object.hasOwn(analysed.steps[0], "suggest")).toBe(true);
    expect(analysed.steps[0].suggest).toBeUndefined();
    expect(analysed.moves[0].setSize).toBeUndefined();
    analysed.steps[0].after.perSlotFeasible[0].pop();
    analysed.moves[0].word = "ZZZZZ";
    slots[0].guesses.length = 0;
    const replay = compute.request({ type: "replay-manual", index: 0, clueGrid: null, expanded: false });
    expect(replay.move.word).toBe(WORDS[0]);
    expect(replay.step.after.perSlotFeasible[0]).toEqual([WORDS[0]]);
    replay.step.suggest.ranked[0].word = "ZZZZZ";
    expect(compute.request({ type: "replay-manual", index: 0, clueGrid: null, expanded: false }).step.suggest.ranked[0].word).toBe(WORDS[1]);
    compute.request({ type: "reset-manual" });
    expect(() => compute.request({ type: "replay-manual", index: 0 })).toThrow("No manual replay");
  });
});
