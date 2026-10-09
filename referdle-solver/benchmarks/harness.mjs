import { readFile, realpath } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { performance } from "node:perf_hooks";
import { build } from "esbuild";
import { normalizeTrace } from "./trace.mjs";

export const COMPONENT_DIR = fileURLToPath(new URL("../", import.meta.url));
export const DATA_DIR = path.join(COMPONENT_DIR, "data");
const SOLVER_DIR = path.join(COMPONENT_DIR, "js", "solver");
const SAMPLE_URL = new URL("sample.json", import.meta.url);
let fetchScopeActive = false;

export async function loadSample() {
  const sample = JSON.parse(await readFile(SAMPLE_URL, "utf8"));
  validateSample(sample);
  return sample;
}

export function validateSample(sample) {
  if (sample?.schemaVersion !== 1 || sample.range?.start !== 1000 || sample.range?.end !== 1414
      || !Array.isArray(sample.games) || sample.games.length !== 100) throw new Error("Invalid frozen 100-game sample");
  const expectedDays = Array.from({ length: 100 }, (_, i) => 1000 + Math.round(i * 414 / 99));
  for (let i = 0; i < sample.games.length; i++) {
    const game = sample.games[i];
    if (game.day !== expectedDays[i] || !Array.isArray(game.answers) || game.answers.length !== 5
        || !game.answers.every((word) => typeof word === "string" && /^[A-Z]{5}$/.test(word))) {
      throw new Error(`Invalid frozen sample case at index ${i}`);
    }
  }
}

export async function validateSampleAnswers(sample, getAnswers) {
  validateSample(sample);
  for (const game of sample.games) {
    const actual = await getAnswers(game.day);
    if (!actual || actual.length !== game.answers.length || actual.some((word, i) => word !== game.answers[i])) {
      throw new Error(`Frozen answers differ from loaded daily #${game.day}`);
    }
  }
}

export function createLocalFetch(dataDir = DATA_DIR, fallback = globalThis.fetch) {
  const allowedDir = path.resolve(dataDir);
  return async function localFetch(input, options) {
    const url = new URL(input instanceof Request ? input.url : input);
    if (url.protocol !== "file:") {
      if (!fallback) throw new Error(`No fetch adapter for ${url.protocol}`);
      return fallback(input, options);
    }
    const filename = fileURLToPath(url);
    if (path.dirname(filename) !== allowedDir || url.search || url.hash) throw new Error("Local fetch outside solver data directory");
    try {
      const resolved = await realpath(filename);
      if (path.dirname(resolved) !== await realpath(allowedDir)) throw new Error("Local fetch symlink outside solver data directory");
      return new Response(await readFile(resolved), { status: 200 });
    } catch (error) {
      if (error.code === "ENOENT") return new Response("Not found", { status: 404 });
      throw error;
    }
  };
}

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
    '    turn.done = reply.continuation.done;',
    '    benchmarkCapture.lastResult = reply.result;\n    turn.done = reply.continuation.done;', "engine final result");
  result = replaceOnce(result,
    '      turn.moves.push(reply.move);',
    '      reply.suggest.__benchmarkBefore = reply.before;\n      turn.moves.push(reply.move);', "engine before state");
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

export function driveController(controller, capture, { maxTurns = 80, context = "game", now = () => performance.now() } = {}) {
  if (!Number.isInteger(maxTurns) || maxTurns < 1 || maxTurns > 80) throw new Error("Invalid turn bound");
  const turnMs = [];
  let previousMoves = 0;
  let finalResult = null;
  for (let i = 0; i < maxTurns; i++) {
    capture.publication = null;
    capture.lastResult = null;
    const start = now();
    try {
      controller.nextTurn();
    } catch (error) {
      throw new Error(`${context}: turn ${i} failed: ${error.message}`, { cause: error });
    }
    turnMs.push(now() - start);
    const publication = capture.publication;
    if (!publication?.game || !Array.isArray(publication.moves)) throw new Error(`${context}: missing game publication at turn ${i}`);
    if (capture.lastResult !== null) finalResult = capture.lastResult;
    const count = publication.moves.length;
    if (count < previousMoves || count > previousMoves + 1) throw new Error(`${context}: inconsistent move progression at turn ${i}`);
    if (!publication.game.inProgress) {
      if (!finalResult) throw new Error(`${context}: missing final solver result`);
      return { publication, finalResult, turnMs, computeMs: turnMs.reduce((sum, ms) => sum + ms, 0) };
    }
    if (count === previousMoves) throw new Error(`${context}: no progress at turn ${i}`);
    previousMoves = count;
  }
  throw new Error(`${context}: exhausted ${maxTurns}-turn bound`);
}

export async function createHarness({ sample, onProgress } = {}) {
  sample ??= await loadSample();
  validateSample(sample);
  const module = await createControllerBundle();
  const state = await withLocalFetch(async () => {
    const loaded = await module.loadAssets(onProgress);
    await validateSampleAnswers(sample, (day) => module.dailyGame(loaded, day));
    for (const game of sample.games) {
      if (!game.answers.every((word) => loaded.poolIndex.has(word))) throw new Error(`Daily #${game.day} has an answer outside the pool`);
    }
    return loaded;
  });
  let running = false;
  return {
    sample, state, strategy: module.STRATEGY,
    async runGame(day, expanded, options = {}) {
      const game = sample.games.find((entry) => entry.day === day);
      if (!game) throw new Error(`Day ${day} is outside the frozen sample`);
      if (typeof expanded !== "boolean") throw new Error("Probe mode must be explicitly true or false");
      if (running) throw new Error("Benchmark cases must run sequentially");
      running = true;
      try {
        const { manual, clueUI, uiEls } = createUIDoubles(expanded);
        const controller = module.initDailyMode(state, manual, clueUI, uiEls);
        await withLocalFetch(() => controller.loadDay(day));
        const answers = controller.getWords();
        if (!answers || answers.some((word, i) => word !== game.answers[i])) throw new Error(`Loaded answers changed for daily #${day}`);
        const clueGrid = clueUI.getClueGrid();
        const result = driveController(controller, module.capture, { ...options, context: `Daily #${day} (${expanded ? "expanded" : "pool"})` });
        const trace = normalizeTrace({ day, answers, expanded, clueGrid, ...result, finalSlots: manual.getSlots() });
        return { trace, computeMs: result.computeMs, turnMs: result.turnMs };
      } finally {
        running = false;
      }
    },
  };
}
