// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// jsdom replaces the global URL, which node:fs rejects, so the path comes from
// __dirname. RUN_LIVE_SCRIPT lets a scratch copy of the script stand in for the real one.
const SCRIPT_PATH = process.env.RUN_LIVE_SCRIPT
  || resolve(__dirname, "../static/run_live.js");
const SOURCE = readFileSync(SCRIPT_PATH, "utf8");

const STATUS_URL = "/runs/job1/status";

// The markup run_job.html renders for a running job.
function mountPage({ step = 0, stdoutOffset = 0, stderrOffset = 0 } = {}) {
  document.body.innerHTML = `
    <p id="run-live-step"><strong>Step 1:</strong> First</p>
    <div id="run-live" data-status-url="${STATUS_URL}" data-step="${step}"
         data-stdout-offset="${stdoutOffset}" data-stderr-offset="${stderrOffset}">
      <pre id="run-live-stdout"></pre>
      <pre id="run-live-stderr"></pre>
    </div>`;
}

const out = () => document.getElementById("run-live-stdout").textContent;
const err = () => document.getElementById("run-live-stderr").textContent;

// A status body in the shape /runs/<id>/status returns.
function body(over = {}) {
  return {
    status: "running", done: false, step: 0, steps: 1, step_label: "First",
    stdout: "", stderr: "", stdout_offset: 0, stderr_offset: 0, caught_up: false,
    ...over,
  };
}

let calls;
let reload;

// Each fetch call is recorded and stays pending until the test settles it.
function fakeFetch(url, options) {
  const call = { url, options };
  call.promise = new Promise((resolve, reject) => {
    call.resolve = (data) => resolve({ ok: true, status: 200, json: async () => data });
    call.status = (code) => resolve({ ok: false, status: code, json: async () => ({}) });
    call.reject = reject;
  });
  calls.push(call);
  return call.promise;
}

// The script is a browser script with no exports: run its source against the
// jsdom document, with fetch faked and window.location.reload observable
// (jsdom's own location cannot be replaced).
function runScript() {
  const fakeWindow = {
    location: { reload },
    setTimeout: (...args) => globalThis.setTimeout(...args),
  };
  new Function("window", "document", "fetch", SOURCE)(fakeWindow, document, fakeFetch);
}

// Lets the promise chain after a settled fetch run to completion.
const flush = () => vi.advanceTimersByTimeAsync(0);

async function tick(ms = 1000) {
  await vi.advanceTimersByTimeAsync(ms);
}

describe("run_live.js", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    calls = [];
    reload = vi.fn();
    mountPage();
  });

  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  it("appends each chunk once, in order, to the matching block, asking from the returned offsets", async () => {
    runScript();
    await tick();
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(`${STATUS_URL}?step=0&stdout=0&stderr=0`);

    calls[0].resolve(body({ stdout: "out-1;", stderr: "err-1;", stdout_offset: 6, stderr_offset: 6 }));
    await flush();
    expect(out()).toBe("out-1;");
    expect(err()).toBe("err-1;");

    await tick();
    expect(calls).toHaveLength(2);
    expect(calls[1].url).toBe(`${STATUS_URL}?step=0&stdout=6&stderr=6`);

    calls[1].resolve(body({ stdout: "out-2;", stderr: "", stdout_offset: 12, stderr_offset: 6 }));
    await flush();
    expect(out()).toBe("out-1;out-2;");
    expect(err()).toBe("err-1;");

    await tick();
    expect(calls[2].url).toBe(`${STATUS_URL}?step=0&stdout=12&stderr=6`);
  });

  it("starts from the offsets the server rendered into the page", async () => {
    mountPage({ step: 2, stdoutOffset: 40, stderrOffset: 7 });
    runScript();
    await tick();
    expect(calls[0].url).toBe(`${STATUS_URL}?step=2&stdout=40&stderr=7`);
  });

  it("polls once a second: nothing at 999 ms, a request at 1000 ms", async () => {
    runScript();
    await tick(999);
    expect(calls).toHaveLength(0);
    await tick(1);
    expect(calls).toHaveLength(1);

    calls[0].resolve(body());
    await flush();
    await tick(999);
    expect(calls).toHaveLength(1);
    await tick(1);
    expect(calls).toHaveLength(2);
  });

  it("waits for the response before scheduling the next poll", async () => {
    runScript();
    await tick();
    await tick(5000);
    expect(calls).toHaveLength(1);
  });

  it("stops polling and reloads the page once when the job is done", async () => {
    runScript();
    await tick();
    calls[0].resolve(body({ status: "ok", done: true, stdout: "last\n", stdout_offset: 5 }));
    await flush();
    expect(reload).toHaveBeenCalledTimes(1);
    expect(out()).toBe("last\n");

    await tick(10000);
    expect(calls).toHaveLength(1);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does not reload while the job is still running", async () => {
    runScript();
    await tick();
    calls[0].resolve(body({ stdout: "x", stdout_offset: 1 }));
    await flush();
    expect(reload).not.toHaveBeenCalled();
  });

  it("clears the output and restarts the offsets when the step changes", async () => {
    runScript();
    await tick();
    calls[0].resolve(body({ stdout: "old out", stderr: "old err", stdout_offset: 7, stderr_offset: 7, steps: 2 }));
    await flush();
    expect(out()).toBe("old out");

    await tick();
    expect(calls[1].url).toBe(`${STATUS_URL}?step=0&stdout=7&stderr=7`);
    calls[1].resolve(body({
      step: 1, steps: 2, step_label: "Second", stdout: "new", stdout_offset: 3, stderr_offset: 0,
    }));
    await flush();
    expect(out()).toBe("new");
    expect(err()).toBe("");
    expect(document.getElementById("run-live-step").textContent).toBe("Step 2: Second");

    await tick();
    expect(calls[2].url).toBe(`${STATUS_URL}?step=1&stdout=3&stderr=0`);
  });

  it("moves on to the next step when a finished step has nothing left to read", async () => {
    runScript();
    await tick();
    calls[0].resolve(body({ steps: 2, caught_up: true, stdout_offset: 9, stderr_offset: 4 }));
    await flush();

    await tick();
    expect(calls[1].url).toBe(`${STATUS_URL}?step=1&stdout=0&stderr=0`);
  });

  it("keeps polling after a rejected fetch and appends nothing", async () => {
    runScript();
    await tick();
    calls[0].reject(new Error("network down"));
    await flush();
    expect(out()).toBe("");
    expect(err()).toBe("");

    await tick();
    expect(calls).toHaveLength(2);
    expect(calls[1].url).toBe(`${STATUS_URL}?step=0&stdout=0&stderr=0`);
    expect(reload).not.toHaveBeenCalled();
  });

  it("keeps polling after a non-OK response and appends nothing", async () => {
    runScript();
    await tick();
    calls[0].status(500);
    await flush();
    expect(out()).toBe("");
    expect(err()).toBe("");

    await tick();
    expect(calls).toHaveLength(2);

    calls[1].resolve(body({ stdout: "back", stdout_offset: 4 }));
    await flush();
    expect(out()).toBe("back");
  });

  it("does nothing on a page without the live block", async () => {
    document.body.innerHTML = "<p>finished</p>";
    runScript();
    await tick(5000);
    expect(calls).toHaveLength(0);
  });
});
