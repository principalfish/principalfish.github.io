import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const serverScript = process.env.SERVER_WATCH_SCRIPT
  ?? fileURLToPath(new URL('../../server.sh', import.meta.url));
const addedInputs = [
  'electionmapslogic/shell.html',
  'electionmapslogic/features/senate-baseline.js',
  'electionmapslogic/features/senate-chamber.js',
  'electionmapslogic/features/senate-forecast-controller.js',
  'electionmapslogic/features/senate-forecast-view.js',
];

const fakeNpm = `#!/usr/bin/env node
const fs = require('node:fs');
if (process.argv.slice(2).join(' ') === 'run vendor:d3') process.exit(0);
if (process.argv.slice(2).join(' ') !== 'run minify:electionmaps') process.exit(2);
const build = fs.existsSync('build-count') ? Number(fs.readFileSync('build-count', 'utf8')) + 1 : 1;
fs.writeFileSync('build-count', String(build));
const snapshot = fs.readFileSync(process.env.WATCH_TEST_INPUT, 'utf8');
fs.writeFileSync('started-' + build, snapshot);
const timer = setInterval(() => {
  if (!fs.existsSync('release-' + build)) return;
  clearInterval(timer);
  const failed = fs.existsSync('fail-' + build);
  if (!failed) fs.writeFileSync('artifact', snapshot);
  fs.writeFileSync('finished-' + build, failed ? 'failed' : 'passed');
  process.exit(failed ? 1 : 0);
}, 10);
`;
const fakePython = `#!/usr/bin/env node
require('node:fs').writeFileSync('server-started', 'ready');
setInterval(() => {}, 1000);
`;
const markedSleep = `#!/usr/bin/env bash
if [[ -e server-sleep ]]; then
  touch watch-ready
else
  touch server-sleep
fi
exec /bin/sleep "$@"
`;

async function createHarness(input = 'electionmapslogic/app.js') {
  const directory = await mkdtemp(join(tmpdir(), 'server-watch-'));
  let child;
  let exited;
  let spawnError;
  let output = '';
  const path = (file) => join(directory, file);
  const read = (file) => readFile(path(file), 'utf8');
  const write = (file, contents = '') => writeFile(path(file), contents);

  async function waitFor(label, check) {
    const deadline = Date.now() + 6000;
    while (Date.now() < deadline) {
      if (spawnError) throw new Error(`Cannot launch server.sh: ${spawnError.message}`);
      if (child.exitCode !== null || child.signalCode !== null) {
        throw new Error(`server.sh exited while waiting for ${label}:\n${output}`);
      }
      if (await check()) return;
      await delay(20);
    }
    throw new Error(`Timed out waiting for ${label}:\n${output}`);
  }

  async function waitForFile(file) {
    await waitFor(file, async () => {
      try {
        await read(file);
        return true;
      } catch (error) {
        if (error.code === 'ENOENT') return false;
        throw error;
      }
    });
  }

  async function dispose() {
    if (child) {
      // The detached child owns this process group, including gated build children.
      if (child.pid) {
        try {
          process.kill(-child.pid, 'SIGKILL');
        } catch (error) {
          if (error.code !== 'ESRCH') throw error;
        }
      }
      await exited;
    }
    await rm(directory, { recursive: true, force: true });
  }

  try {
    const script = await readFile(serverScript, 'utf8');
    const watchList = script.match(/WATCH_FILES=\(([\s\S]*?)\)/)[1].trim().split(/\s+/);
    for (const file of new Set([...watchList, ...addedInputs, input])) {
      await mkdir(dirname(path(file)), { recursive: true });
      await write(file, 'A');
    }
    await mkdir(path('node_modules'));
    await mkdir(path('bin'));
    await write('server.sh', script);
    await writeFile(path('bin/npm'), fakeNpm, { mode: 0o755 });
    await writeFile(path('bin/python3'), fakePython, { mode: 0o755 });
    await writeFile(path('bin/sleep'), markedSleep, { mode: 0o755 });
    child = spawn('bash', ['server.sh'], {
      cwd: directory,
      detached: true,
      env: {
        ...process.env,
        PATH: `${path('bin')}:${process.env.PATH}`,
        WATCH_TEST_INPUT: input,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    exited = new Promise((resolve) => {
      child.once('error', (error) => { spawnError = error; });
      child.once('close', resolve);
    });
    child.stdout.on('data', (data) => { output += data; });
    child.stderr.on('data', (data) => { output += data; });
    await waitForFile('started-1');
    return {
      read,
      write,
      dispose,
      waitForBuild: (build) => waitForFile(`started-${build}`),
      async release(build, failed = false) {
        if (failed) await write(`fail-${build}`);
        await write(`release-${build}`);
        await waitForFile(`finished-${build}`);
      },
      async finishStartup() {
        await this.release(1);
        // The old script prints "Watching" before capturing its startup baseline.
        await waitForFile('watch-ready');
      },
      async expectIdle(build) {
        // Allow more than two real polling intervals to detect accidental retries.
        await delay(2200);
        expect(await read('build-count'), output).toBe(String(build));
      },
    };
  } catch (error) {
    await dispose();
    throw error;
  }
}

async function withHarness(input, test) {
  const harness = await createHarness(input);
  try {
    await test(harness);
  } finally {
    await harness.dispose();
  }
}

describe('server.sh source watcher', () => {
  it('rebuilds an edit made during startup minification', async () => {
    await withHarness(undefined, async (harness) => {
      expect(await harness.read('started-1')).toBe('A');
      await harness.write('electionmapslogic/app.js', 'B');
      await harness.release(1);
      await harness.waitForBuild(2);
      await harness.release(2);
      expect(await harness.read('artifact')).toBe('B');
      await harness.expectIdle(2);
    });
  }, 15000);

  it('rebuilds an edit made during a watch-loop minification', async () => {
    await withHarness(undefined, async (harness) => {
      await harness.finishStartup();
      await harness.write('electionmapslogic/app.js', 'B');
      await harness.waitForBuild(2);
      expect(await harness.read('started-2')).toBe('B');
      await harness.write('electionmapslogic/app.js', 'C');
      await harness.release(2);
      await harness.waitForBuild(3);
      await harness.release(3);
      expect(await harness.read('artifact')).toBe('C');
      await harness.expectIdle(3);
    });
  }, 15000);

  it.each(addedInputs)('rebuilds when %s changes', async (input) => {
    await withHarness(input, async (harness) => {
      await harness.finishStartup();
      await harness.write(input, 'B');
      await harness.waitForBuild(2);
      await harness.release(2);
      expect(await harness.read('artifact')).toBe('B');
    });
  }, 15000);

  it('retries after edits during failed builds, then waits for another edit', async () => {
    await withHarness(undefined, async (harness) => {
      await harness.finishStartup();
      await harness.write('electionmapslogic/app.js', 'B');
      await harness.waitForBuild(2);
      await harness.write('electionmapslogic/app.js', 'C');
      await harness.release(2, true);
      await harness.waitForBuild(3);
      expect(await harness.read('started-3')).toBe('C');
      await harness.release(3, true);
      expect(await harness.read('artifact')).toBe('A');
      await harness.expectIdle(3);
      await harness.write('electionmapslogic/app.js', 'D');
      await harness.waitForBuild(4);
      await harness.release(4);
      expect(await harness.read('artifact')).toBe('D');
      await harness.expectIdle(4);
    });
  }, 20000);
});
