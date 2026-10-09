import { afterEach, describe, expect, it, vi } from "vitest";
import { createControllerBundle, createUIDoubles } from "../harness.mjs";

const words = ["AAAAA", "BBBBB", "CCCCC", "DDDDD", "EEEEE"];
const replacements = {
  "data.js": `export async function dailyGame(state, day) { return day < 1002 ? ${JSON.stringify(words)} : null; }
export function dailyClueGrid() { return Array(5).fill("22222"); } export async function loadAssets() { return {}; }`,
  "strategy.js": `export const STRATEGY = { prune_w13_doubles: false, prune_w5_plurals: false, prune_w13_combos: false };`,
  "solver.js": `export function solve(slots) { const words = ${JSON.stringify(words)};
const cands = words.map((w,b) => slots[b].guesses.length ? [w] : [w,"ZZZZZ"]);
return { solvable:true, cands, perSlotFeasible:cands, viableFinals:cands[4], unionFeasible:words, clueUsed:false }; }
export const solveRelaxed = solve;`,
  "suggest.js": `export function buildSuggestions(res) { const board = res.perSlotFeasible.findIndex(a=>a.length>1);
return { ranked: board<0 ? [] : [{ board, word:res.perSlotFeasible[board][0],setSize:2,expRemaining:1 }], perBoard:[] }; }`,
};
const state = () => ({ POOL: words, ALL_GUESSES: words, PM: new Uint8Array(), N: 5,
  poolIndex: new Map(words.map((w,i)=>[w,i])), PLURALS: new Set() });
const deferred = () => { let resolve, reject; const promise = new Promise((yes,no)=>{resolve=yes;reject=no;}); return { promise, resolve, reject }; };
async function setup(kind = "daily") {
  const module = await createControllerBundle({ replacements });
  const service = module.createComputeService(state());
  const jobs = [];
  const compute = {
    request(message) {
      if (["advance-daily", "analyse-manual", "replay-manual", "load-day"].includes(message.type)) {
        const job = { ...deferred(), message, run() {
          try { this.resolve(service.request(message)); } catch (error) { this.reject(error); }
        } };
        jobs.push(job);
        return job.promise;
      }
      return service.request(message);
    },
    cancel: vi.fn(), acceptDaily: vi.fn(), acceptManual: vi.fn(),
  };
  const ui = createUIDoubles(false);
  const controller = kind === "daily" ? module.initDailyMode(state(), ui.manual, ui.clueUI, ui.uiEls, compute)
    : module.initManualMode(state(), ui.manual, ui.clueUI, ui.uiEls, compute);
  if (kind === "daily") { const loading = controller.loadDay(1000); await jobs[0].run(); await loading; }
  return { module, controller, compute, jobs, service, ...ui };
}
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
afterEach(() => { vi.useRealTimers(); });

describe("async daily publication", () => {
  it("commits duplicate Next clicks exactly once and restores its button only after accepted completion", async () => {
    const { controller, jobs, module, uiEls } = await setup();
    const first = controller.nextTurn();
    expect(await controller.nextTurn()).toBe(false);
    expect(jobs).toHaveLength(2);
    expect(uiEls.nextTurnBtn.disabled).toBe(true);
    expect(module.capture.publication).toBeNull();
    jobs[1].run();
    await first;
    expect(module.capture.publication.moves).toHaveLength(1);
    expect(uiEls.nextTurnBtn.disabled).toBe(false);
  });

  it.each(["stop", "resetSolve", "loadDay"])("suppresses obsolete results and stale cleanup after %s", async (action) => {
    const { controller, jobs, module, uiEls, compute } = await setup();
    const stale = controller.nextTurn();
    let control;
    if (action === "loadDay") {
      control = controller.loadDay(1001);
      jobs.at(-1).run();
      await control;
    } else await controller[action]();
    const newer = controller.nextTurn();
    const latest = jobs.at(-1);
    jobs[1].run();
    await stale;
    expect(uiEls.nextTurnBtn.disabled).toBe(true);
    expect(compute.acceptDaily).toHaveBeenCalledTimes(action === "loadDay" ? 2 : 1);
    expect(module.capture.publication).toBeNull();
    latest.run();
    await newer;
    expect(module.capture.publication.moves).toHaveLength(1);
    expect(uiEls.nextTurnBtn.disabled).toBe(false);
    expect(compute.cancel).toHaveBeenCalled();
  });

  it("waits for each real move, supports Solve re-click skip and leaves cancellation reusable", async () => {
    const { controller, jobs, module, uiEls } = await setup();
    vi.useFakeTimers();
    const run = controller.solveToEnd();
    await vi.advanceTimersByTimeAsync(0);
    await controller.solveToEnd();
    expect(jobs).toHaveLength(2);
    expect(module.capture.publication).toBeNull();
    for (let i = 1; i <= 6; i++) {
      expect(jobs).toHaveLength(i+1);
      jobs[i].run();
      await flush();
    }
    await run;
    expect(module.capture.publication.moves).toHaveLength(5);
    expect(module.capture.publication.game.solved).toBe(true);
    expect(uiEls.nextTurnBtn.disabled).toBe(false);
  });

  it("Solve waits for an already pending Next move and then continues it exactly once", async () => {
    const { controller, jobs, module, uiEls } = await setup();
    const first = controller.nextTurn();
    vi.useFakeTimers();
    const run = controller.solveToEnd();
    await controller.solveToEnd();
    await vi.advanceTimersByTimeAsync(0);
    expect(jobs).toHaveLength(2);
    expect(uiEls.nextTurnBtn.disabled).toBe(true);
    jobs[1].run(); await first; await flush();
    expect(jobs).toHaveLength(3);
    for (let i = 2; i <= 6; i++) { jobs[i].run(); await flush(); }
    await run;
    expect(module.capture.publication.moves).toHaveLength(5);
    expect(module.capture.publication.game.solved).toBe(true);
    expect(uiEls.nextTurnBtn.disabled).toBe(false);
  });

  it("preserves accepted history after calculation failure and retries the next move", async () => {
    const { controller, jobs, module, manual, uiEls } = await setup();
    const first = controller.nextTurn(); jobs[1].run(); await first;
    const accepted = structuredClone(manual.getSlots());
    const failure = controller.nextTurn();
    const rejected = expect(failure).rejects.toThrow("crash");
    jobs[2].reject(new Error("crash")); await rejected;
    expect(manual.getSlots()).toEqual(accepted);
    expect(module.capture.publication.moves).toHaveLength(1);
    expect(uiEls.statusEl.textContent).toContain("retry");
    const retry = controller.nextTurn(); jobs[3].run(); await retry;
    expect(module.capture.publication.moves).toHaveLength(2);
  });

  it("scrubs accepted daily history without new computation and cancels an active move", async () => {
    const { controller, jobs, module, compute } = await setup();
    const first = controller.nextTurn(); jobs[1].run(); await first;
    const active = controller.nextTurn();
    controller.scrubTo(0);
    expect(jobs).toHaveLength(3);
    expect(compute.cancel).toHaveBeenCalled();
    jobs[2].run(); await active;
    expect(module.capture.publication.moves).toHaveLength(1);
  });

  it("restores the accepted daily display after switching back from edited manual inputs", async () => {
    const { controller, jobs, manual, clueUI } = await setup();
    const first = controller.nextTurn(); jobs[1].run(); await first;
    const accepted = structuredClone(manual.getSlots());
    controller.stop();
    manual.addGuess(2, "ZZZZZ", "00000");
    clueUI.setClueGrid(Array(5).fill("00000"));
    controller.notifyReady();
    expect(manual.getSlots()).toEqual(accepted);
    expect(clueUI.getClueGrid()).toEqual(Array(5).fill("22222"));
    expect(jobs).toHaveLength(2);
  });
});

describe("async manual analysis and lazy replay", () => {
  it("keeps input edits on demand and suppresses obsolete clue-only results", async () => {
    const { controller, jobs, uiEls, clueUI, compute } = await setup("manual");
    clueUI.setClueGrid(["10000", "00000", "00000", "00000", "00000"]);
    controller.notifyReady();
    expect(jobs).toHaveLength(0);
    const pending = controller.onEdit();
    controller.inputsChanged();
    jobs[0].run(); await pending;
    expect(uiEls.suggestEl.innerHTML).toBe("");
    expect(uiEls.statusEl.textContent).toContain("Inputs changed");
    expect(compute.acceptManual).not.toHaveBeenCalled();
    expect(jobs).toHaveLength(1);
  });

  it("makes probe refresh asynchronous, stops old ownership and leaves reset empty", async () => {
    const { controller, jobs, clueUI, uiEls } = await setup("manual");
    clueUI.setClueGrid(["10000", "00000", "00000", "00000", "00000"]);
    const old = controller.onEdit();
    uiEls.expandedToggle.checked = true;
    const refresh = controller.refreshSuggest();
    jobs[0].run(); await old;
    expect(uiEls.suggestEl.innerHTML).toBe("");
    expect(jobs[1].message.expanded).toBe(true);
    controller.reset();
    jobs[1].run(); await refresh;
    expect(uiEls.suggestEl.innerHTML).toBe("");
    expect(clueUI.getClueGrid()).toBeNull();
  });

  it("only solves the final input and visited prefixes, and latest scrub wins", async () => {
    const { controller, jobs, manual, module, uiEls } = await setup("manual");
    manual.addGuess(0, words[0], "22222"); manual.addGuess(1, words[1], "22222");
    const analyse = controller.onEdit();
    expect(jobs[0].message.compact).toBe(true);
    jobs[0].run(); await flush();
    expect(jobs).toHaveLength(2);
    jobs[1].run(); await analyse;
    const old = controller.scrubTo(0);
    const newer = controller.scrubTo(1);
    jobs[2].run(); await old;
    expect(module.capture.step.index).toBe(1);
    expect(uiEls.statusEl.textContent).toContain("Calculating move 2");
    jobs[3].run(); await newer;
    expect(module.capture.step.index).toBe(1);
    expect(manual.getSlots()[1].guesses).toHaveLength(1);
  });

  it("suppresses in-flight replay on manual edits or leaving the mode", async () => {
    const { controller, jobs, manual, uiEls } = await setup("manual");
    manual.addGuess(0, words[0], "22222");
    const analyse = controller.onEdit(); jobs[0].run(); await flush(); jobs[1].run(); await analyse;
    const scrub = controller.scrubTo(0);
    controller.stop(); controller.inputsChanged();
    manual.addGuess(2, words[2], "00000");
    jobs[2].run(); await scrub;
    expect(manual.getSlots()[2].guesses).toHaveLength(1);
    expect(uiEls.moveTableEl.innerHTML).toBe("");
  });
});
