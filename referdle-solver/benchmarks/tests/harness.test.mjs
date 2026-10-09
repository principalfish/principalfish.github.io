import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, writeFile, mkdir, symlink, rm, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  COMPONENT_DIR, createControllerBundle, createLocalFetch, createUIDoubles, driveController,
  loadSample, observeDailySource, validateSample, validateSampleAnswers, withLocalFetch,
} from "../harness.mjs";
import { normalizeTrace } from "../trace.mjs";

const ANSWERS = ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEE"];
const REPLACEMENTS = {
  "data.js": `export async function dailyGame() { return ${JSON.stringify(ANSWERS)}; }
export function dailyClueGrid() { return Array(5).fill("22222"); }
export async function loadAssets() { return {}; }`,
  "solver.js": `
function result(slots) {
  const moved = slots.some((slot) => slot.guesses.length);
  const answers = ${JSON.stringify(ANSWERS)};
  const candidates = answers.map((answer) => moved ? [answer] : [answer, "FFFFF"]);
  return { solvable: true, cands: candidates, perSlotFeasible: candidates, viableFinals: candidates[4], unionFeasible: answers, candCounts: candidates.map((c) => c.length), clueUsed: true, note: undefined };
}
export function solve(slots) { return result(slots); }
export function solveRelaxed(slots) { return result(slots); }`,
  "suggest.js": `
export function bestGuessAcrossBoards(res, PM, N, poolIndex, guesses) {
  return res.perSlotFeasible[0].length === 1 ? [] : [{ board: 0, word: "AAAAA", probe: false, setSize: 2, expRemaining: guesses.length, score: -0 }];
}
export function topGuessesForBoard(answers, PM, N, poolIndex, guesses) { return [{ word: answers[0], score: 1.0000000000000002, guessCount: guesses.length }]; }
export function buildSuggestions(res, PM, N, poolIndex, guesses) {
  const ranked = bestGuessAcrossBoards(res, PM, N, poolIndex, guesses);
  const perBoard = res.perSlotFeasible.flatMap((answers, board) => answers.length > 1
    ? [{ board, top: topGuessesForBoard(answers, PM, N, poolIndex, guesses) }] : []);
  return { solvable: true, ranked, perBoard };
}`,
};
function state() {
  return { POOL: ANSWERS, ALL_GUESSES: [...ANSWERS, "FFFFF"], PM: new Int16Array(), N: 5, poolIndex: new Map(ANSWERS.map((w, i) => [w, i])), PLURALS: new Set() };
}

describe("frozen sample", () => {
  it("has exactly 100 ascending released games and all five actual daily answers", async () => {
    const sample = await loadSample();
    expect(sample.games[0].day).toBe(1000);
    expect(sample.games.at(-1).day).toBe(1414);
    expect(new Set(sample.games.map((game) => game.day)).size).toBe(100);
    expect(sample.games.every((game, i) => i === 0 || game.day > sample.games[i - 1].day)).toBe(true);
    const daily = JSON.parse(await readFile(path.join(COMPONENT_DIR, "data/daily-2000.json"), "utf8"));
    await validateSampleAnswers(sample, (day) => daily[day - 1000]);
    const changed = structuredClone(sample); changed.games[1].answers[0] = "ZZZZZ";
    await expect(validateSampleAnswers(changed, (day) => daily[day - 1000])).rejects.toThrow(`daily #${changed.games[1].day}`);
    changed.games[1].day = 1000;
    expect(() => validateSample(changed)).toThrow("index 1");
  });
});

describe("local asset fetch", () => {
  let dir;
  afterEach(async () => { if (dir) await rm(dir, { recursive: true, force: true }); });
  it("returns real streamed responses, restricts paths and forwards remote fetch", async () => {
    dir = await mkdtemp(path.join(os.tmpdir(), "referdle-fetch-"));
    const data = path.join(dir, "data"); await mkdir(data);
    await writeFile(path.join(data, "asset.txt"), "hello");
    await writeFile(path.join(dir, "outside.txt"), "outside");
    await symlink(path.join(dir, "outside.txt"), path.join(data, "link.txt"));
    const fallback = vi.fn(async () => new Response("remote"));
    const fetch = createLocalFetch(data, fallback);
    const response = await fetch(pathToFileURL(path.join(data, "asset.txt")));
    expect(response.body).toBeInstanceOf(ReadableStream);
    expect(await response.text()).toBe("hello");
    expect((await fetch(pathToFileURL(path.join(data, "missing.txt")))).status).toBe(404);
    await expect(fetch(pathToFileURL(path.join(dir, "outside.txt")))).rejects.toThrow("outside");
    await expect(fetch(pathToFileURL(path.join(data, "link.txt")))).rejects.toThrow("symlink");
    expect(await (await fetch("https://example.invalid/test")).text()).toBe("remote");
    expect(fallback).toHaveBeenCalledOnce();
  });
  it("restores fetch on success and failure and refuses overlapping adapters", async () => {
    const original = globalThis.fetch;
    await withLocalFetch(async () => {
      expect(globalThis.fetch).not.toBe(original);
      await expect(withLocalFetch(async () => {})).rejects.toThrow("Concurrent");
    });
    expect(globalThis.fetch).toBe(original);
    await expect(withLocalFetch(async () => { throw new Error("failure"); })).rejects.toThrow("failure");
    expect(globalThis.fetch).toBe(original);
  });
});

describe("live daily controller observation", () => {
  it.each([false, true])("drives the real controller with expanded=%s without changing decisions", async (expanded) => {
    const plain = await createControllerBundle({ observe: false, replacements: REPLACEMENTS });
    const observed = await createControllerBundle({ replacements: REPLACEMENTS });
    async function run(module) {
      const ui = createUIDoubles(expanded);
      const controller = module.initDailyMode(state(), ui.manual, ui.clueUI, ui.uiEls);
      await controller.loadDay(1000);
      for (let i = 0; i < 10; i++) {
        controller.nextTurn();
        if (module.capture.publication?.game.inProgress === false) break;
      }
      return { ...ui, publication: module.capture.publication, finalResult: module.capture.lastResult };
    }
    const a = await run(plain), b = await run(observed);
    const stripped = { ...b.publication, steps: b.publication.steps.map(({ suggest, ...step }) => {
      const { __benchmarkBefore, ...rest } = suggest;
      return { ...step, suggest: rest };
    }) };
    expect(stripped).toEqual(a.publication);
    expect(b.publication.moves).toHaveLength(5);
    expect(b.publication.moves[0].expRemaining).toBe(expanded ? 6 : 5);
    const trace = normalizeTrace({ day: 1000, answers: ANSWERS, expanded, clueGrid: b.clueUI.getClueGrid(), ...b, finalSlots: b.manual.getSlots() });
    expect(trace.moves[0].before.cands).toHaveLength(5);
    expect(trace.moves[0].suggest.perBoard[0].top[0].score).toBe(1.0000000000000002);
    expect(trace.moves[0].after.perSlotFeasible[0]).toEqual(["AAAAA"]);
    expect(trace.final.status).toBe("solved");
    expect(Object.hasOwn(trace.moves[0].suggest, "__benchmarkBefore")).toBe(false);
  });

  it("publishes and observes a no-move unsolvable terminal", async () => {
    const module = await createControllerBundle({ replacements: { ...REPLACEMENTS, "solver.js": `export function solve() { return { solvable: false, reason: "none" }; } export const solveRelaxed = solve;` } });
    const ui = createUIDoubles(false);
    const controller = module.initDailyMode(state(), ui.manual, ui.clueUI, ui.uiEls);
    await controller.loadDay(1000);
    const result = driveController(controller, module.capture);
    expect(result.publication.moves).toEqual([]);
    expect(result.publication.game.solved).toBe(true);
    expect(result.finalResult).toEqual({ solvable: false, reason: "none" });
  });

  it("fails guards for removed or ambiguous observations and changed render exports", async () => {
    const source = await readFile(path.join(COMPONENT_DIR, "js/solver/daily-mode.js"), "utf8");
    expect(() => observeDailySource(source.replace("      turn.moves.push(reply.move);", ""))).toThrow("engine before state");
    expect(() => observeDailySource(`${source}\n      turn.moves.push(reply.move);`)).toThrow("engine before state");
    expect(() => observeDailySource(source.replace("setupScrub(turn.moves.length);", ""))).toThrow("terminal publication");
    expect(() => observeDailySource(source.replace("    turn.done = reply.continuation.done;", ""))).toThrow("engine final result");
    const changed = source.replace("buildOverlay, renderWordLists", "missingRenderExport, renderWordLists").replace("buildOverlay(slotsAfter", "missingRenderExport(slotsAfter");
    await expect(createControllerBundle({ observe: false, replacements: { ...REPLACEMENTS, "daily-mode.js": changed } })).rejects.toThrow("missingRenderExport");
  });
});

describe("bounded controller execution", () => {
  it("sums only turn computation timings", () => {
    const capture = {};
    let turns = 0;
    const nextTurn = () => { turns++; capture.lastResult = { solvable: true }; capture.publication = { moves: Array(turns).fill({}), game: { inProgress: turns < 2 } }; };
    const clock = [10, 14, 20, 27];
    const result = driveController({ nextTurn }, capture, { now: () => clock.shift() });
    expect(result.turnMs).toEqual([4, 7]);
    expect(result.computeMs).toBe(11);
  });
  it("rejects missing publications, nonprogress, stale final results and excessive turns", () => {
    expect(() => driveController({ nextTurn() {} }, {}, { context: "Daily #1000" })).toThrow("Daily #1000: missing");
    const capture = {};
    expect(() => driveController({ nextTurn() { capture.publication = { moves: [], game: { inProgress: true } }; } }, capture)).toThrow("no progress");
    expect(() => driveController({ nextTurn() { capture.publication = { moves: [], game: { inProgress: false } }; } }, capture)).toThrow("final solver result");
    expect(() => driveController({ nextTurn() { capture.lastResult = {}; capture.publication = { moves: [{}], game: { inProgress: true } }; } }, capture, { maxTurns: 1 })).toThrow("exhausted");
    expect(() => driveController({ nextTurn() { throw new Error("broken"); } }, {}, { context: "Daily #1000 (pool)" })).toThrow("Daily #1000 (pool): turn 0 failed: broken");
  });
});
