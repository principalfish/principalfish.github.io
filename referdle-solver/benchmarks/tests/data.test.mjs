import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { gzipSync } from "node:zlib";
import { build } from "esbuild";
import { COMPONENT_DIR } from "../harness.mjs";

let loadAssets;

beforeAll(async () => {
  const modulePath = path.join(COMPONENT_DIR, "js/solver/data.js");
  const output = await build({
    stdin: { contents: `export { loadAssets } from ${JSON.stringify(modulePath)};`, resolveDir: path.dirname(modulePath) },
    bundle: true, write: false, format: "esm", platform: "node", logLevel: "silent",
    define: { "import.meta.url": JSON.stringify(pathToFileURL(modulePath).href) },
  });
  ({ loadAssets } = await import(`data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text).toString("base64")}`));
});

afterEach(() => { vi.unstubAllGlobals(); });

function mockAssets({ changes = {}, codes = [242, 32, 110, 242], compressed = gzipSync(Buffer.from(codes)), matrixStatus = 200 } = {}) {
  const manifest = {
    pool_n: 2, matrix_dim: 2, matrix_file: "test-matrix.uint8.gz", matrix_storage: "uint8",
    decoded_bytes: 4, gzip_bytes: compressed.length, ...changes,
  };
  const files = new Map([
    ["pool.txt", "alley\napple\n"], ["expanded.txt", "level\n"], ["plurals.txt", "alley\n"],
    ["manifest.json", JSON.stringify(manifest)], ["test-matrix.uint8.gz", compressed],
  ]);
  const fetch = vi.fn(async (url) => {
    const filename = new URL(url).pathname.split("/").at(-1);
    if (!files.has(filename)) return new Response(null, { status: 404 });
    return new Response(files.get(filename), { status: filename === "test-matrix.uint8.gz" ? matrixStatus : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("uint8 asset loading", () => {
  it("loads the manifest-selected gzip through native DecompressionStream and preserves word order", async () => {
    const fetch = mockAssets();
    const progress = [];
    const state = await loadAssets((note) => progress.push(note));
    expect(state.PM).toBeInstanceOf(Uint8Array);
    expect([...state.PM]).toEqual([242, 32, 110, 242]);
    expect(state.PM.byteLength).toBe(4);
    expect(state.N).toBe(2);
    expect(state.POOL).toEqual(["ALLEY", "APPLE"]);
    expect(state.EXPANDED).toEqual(["LEVEL"]);
    expect(state.ALL_GUESSES).toEqual(["ALLEY", "APPLE", "LEVEL"]);
    expect([...state.poolIndex]).toEqual([["ALLEY", 0], ["APPLE", 1]]);
    expect([...state.PLURALS]).toEqual(["ALLEY"]);
    expect(state.dailyCache.size).toBe(0);
    expect(fetch.mock.calls.at(-1)[0]).toBe(pathToFileURL(path.join(COMPONENT_DIR, "data/test-matrix.uint8.gz")).href);
    expect(progress).toHaveLength(2);
  });

  it.each(["int16", "uint16", undefined])("rejects unsupported storage %s before fetching a matrix", async (matrix_storage) => {
    const fetch = mockAssets({ changes: { matrix_storage } });
    await expect(loadAssets()).rejects.toThrow("storage format");
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it.each(["../matrix.gz", "/matrix.gz", "https://example.invalid/matrix.gz", "matrix.bin", "matrix.gz?x=1", undefined])
    ("rejects non-component-local filename %s", async (matrix_file) => {
      const fetch = mockAssets({ changes: { matrix_file } });
      await expect(loadAssets()).rejects.toThrow("matrix filename");
      expect(fetch).toHaveBeenCalledTimes(4);
    });

  it.each([
    { matrix_dim: 0 }, { matrix_dim: 1.5 }, { matrix_dim: 3 }, { matrix_dim: "2" },
    { pool_n: 3 }, { decoded_bytes: 8 }, { gzip_bytes: 0 }, { gzip_bytes: 1.5 },
  ])("rejects malformed dimensions/byte metadata %j", async (changes) => {
    const fetch = mockAssets({ changes });
    await expect(loadAssets()).rejects.toThrow(/dimensions|byte length/);
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it.each([{ codes: [242, 32, 110] }, { codes: [242, 32, 110, 242, 0] }])("rejects decoded length %j", async ({ codes }) => {
    mockAssets({ codes });
    await expect(loadAssets()).rejects.toThrow("matrix length");
  });

  it.each([243, 255])("rejects invalid byte pattern %s", async (code) => {
    mockAssets({ codes: [242, 32, 110, code] });
    await expect(loadAssets()).rejects.toThrow(`code ${code} at index 3`);
  });

  it("propagates unavailable matrices and corrupted gzip errors", async () => {
    mockAssets({ matrixStatus: 404 });
    await expect(loadAssets()).rejects.toThrow("404");
    mockAssets({ compressed: Buffer.from("not gzip") });
    await expect(loadAssets()).rejects.toThrow();
  });
});
