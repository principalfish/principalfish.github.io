import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { performance } from "node:perf_hooks";
import { build } from "esbuild";
import { normalizeTrace } from "./trace.mjs";
import { createLocalFetch as confinedFetch } from "./local-fetch.mjs";
import { loadSample, validateSample, validateSampleAnswers } from "./sample.mjs";
export { loadSample, validateSample, validateSampleAnswers } from "./sample.mjs";

export const createLocalFetch = (dataDir = DATA_DIR, fallback = globalThis.fetch) => confinedFetch(dataDir, fallback);

export const COMPONENT_DIR = fileURLToPath(new URL("../", import.meta.url));
export const DATA_DIR = path.join(COMPONENT_DIR, "data");
const SOLVER_DIR = path.join(COMPONENT_DIR, "js", "solver");
let fetchScopeActive = false;

export async function withLocalFetch(callback, dataDir = DATA_DIR) {
  if (fetchScopeActive) throw new Error("Concurrent local fetch scopes are not supported");
  fetchScopeActive = true;
  const original = globalThis.fetch;
  globalThis.fetch = createLocalFetch(dataDir, original);
  try {
    return await callback();
  } finally {
    globalThis.fetch = original;
    fetchScopeActive = false;
  }
}

function replaceOnce(source, marker, replacement, label) {
  if (source.split(marker).length !== 2) throw new Error(`Benchmark observer guard failed: ${label}`);
  return source.replace(marker, replacement);
}

export function observeDailySource(source) {
  let result = replaceOnce(source,
    '      turn.done = reply.continuation.done;',
    '      benchmarkCapture.lastResult = reply.result;\n      turn.done = reply.continuation.done;', "engine final result");
  result = replaceOnce(result,
    '        turn.moves.push(reply.move);',
    '        reply.suggest.__benchmarkBefore = reply.before;\n        turn.moves.push(reply.move);', "engine before state");
  // The real renderer is called only when a move exists. Observe empty terminals too.
  result = replaceOnce(result, "setupScrub(turn.moves.length);",
    "if (!turn.moves.length) renderAutoSolveTable(uiEls.moveTableEl, lastAutoSolve, activeIdx, jumpToMove);\n    setupScrub(turn.moves.length);",
    "empty terminal publication");
  return `import { capture as benchmarkCapture } from "benchmark:capture";\n${result}`;
}

const CAPTURE_SOURCE = "export const capture = { publication: null, lastResult: null };";
const RENDER_SOURCE = `
import { capture } from "benchmark:capture";
export function buildOverlay() { return null; }
export function renderWordLists() {}
export function renderStepSuggest(container, index, move, suggest) { capture.step = { index, move, suggest }; }
export function perWordTopHTML() { return ""; }
export function renderAutoSolveTable(container, publication) { capture.publication = publication; }
`;

// Replacements are source-text fixtures for bounded tests, never the real benchmark.
export async function createControllerBundle({ observe = true, replacements = {} } = {}) {
  const dailyPath = path.join(SOLVER_DIR, "daily-mode.js");
  const output = await build({
    stdin: {
      contents: `export { initDailyMode } from ${JSON.stringify(dailyPath)};
export { initManualMode } from ${JSON.stringify(path.join(SOLVER_DIR, "manual-mode.js"))};
export { createComputeService } from ${JSON.stringify(path.join(SOLVER_DIR, "compute-service.js"))};
export { loadAssets, dailyGame, dailyClueGrid } from ${JSON.stringify(path.join(SOLVER_DIR, "data.js"))};
export { createWorkerClient } from ${JSON.stringify(path.join(SOLVER_DIR, "worker-client.js"))};
export { STRATEGY } from ${JSON.stringify(path.join(SOLVER_DIR, "strategy.js"))};
export { capture } from "benchmark:capture";`,
      resolveDir: SOLVER_DIR,
    },
    bundle: true, write: false, format: "esm", platform: "node", logLevel: "silent",
    define: { "import.meta.url": JSON.stringify(pathToFileURL(path.join(SOLVER_DIR, "data.js")).href) },
    plugins: [{
      name: "benchmark-observation",
      setup(builder) {
        builder.onResolve({ filter: /^benchmark:/ }, (args) => ({ path: args.path, namespace: "benchmark" }));
        builder.onLoad({ filter: /.*/, namespace: "benchmark" }, (args) => {
          if (args.path === "benchmark:capture") return { contents: CAPTURE_SOURCE };
          throw new Error(`Unknown benchmark module: ${args.path}`);
        });
        builder.onLoad({ filter: /[/\\]render\.js$/ }, async () => ({ contents: RENDER_SOURCE, resolveDir: SOLVER_DIR }));
        builder.onLoad({ filter: /[/\\](daily-mode|manual-mode|engine|analysis|compute-service|data|solver|suggest|strategy)\.js$/ }, async (args) => {
          const basename = path.basename(args.path);
          let contents = replacements[basename] ?? await readFile(args.path, "utf8");
          if (basename === "daily-mode.js" && observe) contents = observeDailySource(contents);
          return { contents, resolveDir: SOLVER_DIR };
        });
      },
    }],
  });
  // A fragment gives each harness independent module state without a filesystem output.
  const url = `data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text).toString("base64")}#${crypto.randomUUID()}`;
  return import(url);
}

export function createUIDoubles(expanded) {
  let slots = Array.from({ length: 5 }, () => ({ guesses: [] }));
  let clueGrid = null;
  const manual = {
    reset() { slots = Array.from({ length: 5 }, () => ({ guesses: [] })); },
    addGuess(board, word, colors) { slots[board].guesses.push({ word, colors }); },
    getSlots() { return slots; },
  };
  const clueUI = {
    setClueGrid(value) { clueGrid = value; },
    getClueGrid() { return clueGrid; },
    setResults() {},
  };
  const uiEls = {
    slotsEl: { innerHTML: "" }, suggestEl: { innerHTML: "" }, moveTableEl: { innerHTML: "" },
    statusEl: { textContent: "" }, expandedToggle: { checked: expanded },
    sliderEl: {}, solveToEndBtn: {}, nextTurnBtn: {}, prevBtn: {}, nextBtn: {},
  };
  return { manual, clueUI, uiEls };
}

export async function driveController(controller, capture, { maxTurns = 80, context = "game", now = () => performance.now() } = {}) {
  if (!Number.isInteger(maxTurns) || maxTurns < 1 || maxTurns > 80) throw new Error("Invalid turn bound");
  const turnMs = [];
  const turnRoundtripMs = [];
  let previousMoves = 0;
  let finalResult = null;
  for (let i = 0; i < maxTurns; i++) {
    capture.publication = null;
    capture.lastResult = null;
    const start = now();
    try {
      await controller.nextTurn();
    } catch (error) {
      throw new Error(`${context}: turn ${i} failed: ${error.message}`, { cause: error });
    }
    const elapsed = now() - start;
    const timing = controller.getTiming?.();
    turnMs.push(timing?.computeMs ?? elapsed);
    turnRoundtripMs.push(timing?.roundtripMs ?? null);
    const publication = capture.publication;
    if (!publication?.game || !Array.isArray(publication.moves)) throw new Error(`${context}: missing game publication at turn ${i}`);
    if (capture.lastResult !== null) finalResult = capture.lastResult;
    const count = publication.moves.length;
    if (count < previousMoves || count > previousMoves + 1) throw new Error(`${context}: inconsistent move progression at turn ${i}`);
    if (!publication.game.inProgress) {
      if (!finalResult) throw new Error(`${context}: missing final solver result`);
      return { publication, finalResult, turnMs, turnRoundtripMs,
        computeMs: turnMs.reduce((sum, ms) => sum + ms, 0),
        roundtripMs: turnRoundtripMs.every(ms => ms !== null) ? turnRoundtripMs.reduce((sum, ms) => sum + ms, 0) : null };
    }
    if (count === previousMoves) throw new Error(`${context}: no progress at turn ${i}`);
    previousMoves = count;
  }
  throw new Error(`${context}: exhausted ${maxTurns}-turn bound`);
}

// The CLI selects Worker explicitly; direct remains useful for bounded fixtures.
export async function createHarness({ sample, onProgress, backend = "direct", workerOptions } = {}) {
  if (!["direct", "worker"].includes(backend)) throw new Error("Invalid benchmark backend");
  sample ??= await loadSample();
  validateSample(sample);
  const module = await createControllerBundle();
  let bridge = null;
  let client = null;
  let state = null;
  let contract = null;
  let ownership = null;
  try {
    if (backend === "worker") {
      const { createNativeWorkerFactory } = await import("./worker-bridge.mjs");
      bridge = await createNativeWorkerFactory(workerOptions);
      client = module.createWorkerClient({ workerFactory: bridge.factory, onProgress,
        directFactory: async () => { throw new Error("Worker benchmark cannot use direct compatibility mode"); } });
      const catalogue = await client.request({ type: "initialize" });
      ({ contract, ownership } = await bridge.contract(bridge.workers[0], sample));
      state = { POOL: catalogue.pool, ALL_GUESSES: catalogue.expanded };
    } else {
      state = await withLocalFetch(async () => {
        const loaded = await module.loadAssets(onProgress);
        await validateSampleAnswers(sample, day => module.dailyGame(loaded, day));
        for (const game of sample.games) {
          if (!game.answers.every(word => loaded.poolIndex.has(word))) throw new Error(`Daily #${game.day} has an answer outside the pool`);
        }
        return loaded;
      });
    }
  } catch (error) {
    client?.dispose();
    await bridge?.dispose();
    throw error;
  }
  let running = false;
  let disposed = false;
  return {
    sample, state, strategy: module.STRATEGY, contract, ownership, backend,
    async dispose() {
      disposed = true;
      client?.dispose();
      await bridge?.dispose();
    },
    async runGame(day, expanded, options = {}) {
      if (disposed) throw new Error("Benchmark harness is disposed");
      const game = sample.games.find(entry => entry.day === day);
      if (!game) throw new Error(`Day ${day} is outside the frozen sample`);
      if (typeof expanded !== "boolean") throw new Error("Probe mode must be explicitly true or false");
      if (running) throw new Error("Benchmark cases must run sequentially");
      running = true;
      try {
        const { manual, clueUI, uiEls } = createUIDoubles(expanded);
        const controller = module.initDailyMode(state, manual, clueUI, uiEls, client ?? undefined);
        if (client) await controller.loadDay(day);
        else await withLocalFetch(() => controller.loadDay(day));
        const answers = controller.getWords();
        if (!answers || answers.some((word, i) => word !== game.answers[i])) throw new Error(`Loaded answers changed for daily #${day}`);
        const clueGrid = clueUI.getClueGrid();
        const result = await driveController(controller, module.capture, { ...options, context: `Daily #${day} (${expanded ? "expanded" : "pool"})` });
        const trace = normalizeTrace({ day, answers, expanded, clueGrid, ...result, finalSlots: manual.getSlots() });
        return { trace, computeMs: result.computeMs, turnMs: result.turnMs,
          roundtripMs: result.roundtripMs, turnRoundtripMs: result.turnRoundtripMs };
      } finally {
        running = false;
      }
    },
  };
}
