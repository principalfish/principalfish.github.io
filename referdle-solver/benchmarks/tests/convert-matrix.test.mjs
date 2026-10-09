import { afterEach, describe, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import { link, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { gunzipSync, gzipSync } from "node:zlib";
import { convertMatrix, encodeLegacyMatrix, parseArgs } from "../convert-matrix.mjs";

const publicationFailure = vi.hoisted(() => ({ enabled: false }));
vi.mock("node:fs/promises", async (importOriginal) => {
  const actual = await importOriginal();
  function failManifest(source) {
    if (publicationFailure.enabled && source.endsWith("manifest.json")) throw new Error("Manifest publication failed");
  }
  return {
    ...actual,
    async link(source, destination) { failManifest(source); return actual.link(source, destination); },
    async rename(source, destination) { failManifest(source); return actual.rename(source, destination); },
  };
});

function legacy(values, littleEndian = true) {
  const bytes = Buffer.alloc(values.length * 2);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  values.forEach((value, i) => view.setInt16(i * 2, value, littleEndian));
  return gzipSync(bytes);
}

describe("lossless matrix encoding", () => {
  it("preserves every little-endian pattern byte and produces deterministic gzip", () => {
    const input = legacy([0, 1, 242, 128]);
    const encoded = encodeLegacyMatrix(input, 2);
    expect(gunzipSync(encoded.compressed)).toEqual(Buffer.from([0, 1, 242, 128]));
    expect(encodeLegacyMatrix(input, 2).compressed).toEqual(encoded.compressed);
    expect(encoded.compressed.readUInt32LE(4)).toBe(0);
    expect(encoded.legacyRawBytes).toBe(8);
    expect(encoded.decodedBytes).toBe(4);
    expect(encoded.patternsHash).toBe(createHash("sha256").update(Buffer.from([0, 1, 242, 128])).digest("hex"));
    expect(encoded.gzipHash).toBe(createHash("sha256").update(encoded.compressed).digest("hex"));
  });

  it.each([-32768, -1, 243, 256, 32767])("rejects signed/out-of-range code %s without truncation", (value) => {
    expect(() => encodeLegacyMatrix(legacy([0, 1, 242, value]), 2)).toThrow(`code ${value} at index 3`);
  });

  it("does not interpret big-endian bytes as valid little-endian codes", () => {
    expect(() => encodeLegacyMatrix(legacy([0, 1, 242, 3], false), 2)).toThrow("code 256 at index 1");
  });

  it.each([0, -1, 1.5, Infinity, Number.MAX_SAFE_INTEGER])("rejects invalid dimension %s", (dimension) => {
    expect(() => encodeLegacyMatrix(legacy([0]), dimension)).toThrow("dimension");
  });

  it("rejects truncated, odd-length and non-gzip inputs", () => {
    expect(() => encodeLegacyMatrix(legacy([0, 1, 2]), 2)).toThrow("length 6 != 8");
    expect(() => encodeLegacyMatrix(gzipSync(Buffer.alloc(7)), 2)).toThrow("length 7 != 8");
    expect(() => encodeLegacyMatrix(Buffer.from("not gzip"), 2)).toThrow();
  });
});

describe("conversion publication", () => {
  let dir;
  afterEach(async () => {
    publicationFailure.enabled = false;
    if (dir) await rm(dir, { recursive: true, force: true });
  });

  async function setup(values = [0, 1, 242, 128]) {
    dir = await mkdtemp(path.join(os.tmpdir(), "referdle-convert-"));
    const options = { input: path.join(dir, "legacy.gz"), output: path.join(dir, "matrix.uint8.gz"), dimension: 2, manifestPath: path.join(dir, "manifest.json") };
    await writeFile(options.input, legacy(values));
    return options;
  }

  it("publishes a verified matrix and manifest without changing its input", async () => {
    const options = await setup();
    const input = await readFile(options.input);
    const report = await convertMatrix(options);
    const manifest = JSON.parse(await readFile(options.manifestPath, "utf8"));
    expect(manifest).toEqual({
      pool_n: 2, matrix_dim: 2, matrix_file: "matrix.uint8.gz", matrix_storage: "uint8",
      decoded_bytes: 4, gzip_bytes: report.gzipBytes, patterns_sha256: report.patternsHash, gzip_sha256: report.gzipHash,
    });
    expect(gunzipSync(await readFile(options.output))).toEqual(Buffer.from([0, 1, 242, 128]));
    expect(await readFile(options.input)).toEqual(input);
    expect((await readdir(dir)).sort()).toEqual(["legacy.gz", "manifest.json", "matrix.uint8.gz"]);
    await expect(convertMatrix(options)).rejects.toThrow("overwrite matrix output");
  });

  it("requires explicit replacement and matching dimensions for an existing manifest", async () => {
    const options = await setup();
    const original = JSON.stringify({ pool_n: 2, matrix_dim: 2, gzip_bytes: 99 });
    await writeFile(options.manifestPath, original);
    await expect(convertMatrix(options)).rejects.toThrow("--replace-manifest");
    expect(await readFile(options.manifestPath, "utf8")).toBe(original);
    expect((await readdir(dir)).sort()).toEqual(["legacy.gz", "manifest.json"]);
    await expect(convertMatrix({ ...options, dimension: 3, replaceManifest: true })).rejects.toThrow("dimensions differ");
    await convertMatrix({ ...options, replaceManifest: true });
    expect(JSON.parse(await readFile(options.manifestPath, "utf8")).matrix_storage).toBe("uint8");
  });

  it("leaves an existing manifest and all destinations untouched for invalid values", async () => {
    const options = await setup([0, 1, 242, -1]);
    const original = JSON.stringify({ pool_n: 2, matrix_dim: 2 });
    await writeFile(options.manifestPath, original);
    await expect(convertMatrix({ ...options, replaceManifest: true })).rejects.toThrow("code -1");
    expect(await readFile(options.manifestPath, "utf8")).toBe(original);
    expect((await readdir(dir)).sort()).toEqual(["legacy.gz", "manifest.json"]);
  });

  it.each([false, true])("rolls back the new matrix if manifest publication fails (replacement=%s)", async (replaceManifest) => {
    const options = await setup();
    const original = JSON.stringify({ pool_n: 2, matrix_dim: 2 });
    if (replaceManifest) await writeFile(options.manifestPath, original);
    publicationFailure.enabled = true;
    await expect(convertMatrix({ ...options, replaceManifest })).rejects.toThrow("Manifest publication failed");
    expect((await readdir(dir)).sort()).toEqual(replaceManifest ? ["legacy.gz", "manifest.json"] : ["legacy.gz"]);
    if (replaceManifest) expect(await readFile(options.manifestPath, "utf8")).toBe(original);
  });

  it("refuses identical paths, symlink destinations and hard-link input aliases", async () => {
    const options = await setup();
    await expect(convertMatrix({ ...options, output: options.input })).rejects.toThrow("distinct");
    await expect(convertMatrix({ ...options, manifestPath: options.output })).rejects.toThrow("distinct");
    await symlink(options.input, options.output);
    await expect(convertMatrix(options)).rejects.toThrow("overwrite matrix output");
    await rm(options.output);
    await link(options.input, options.manifestPath);
    await expect(convertMatrix({ ...options, replaceManifest: true })).rejects.toThrow("aliases matrix input");
    await rm(options.manifestPath);
    await symlink(options.input, options.manifestPath);
    await expect(convertMatrix({ ...options, replaceManifest: true })).rejects.toThrow("not a symlink");
  });

  it("rejects a manifest in another directory and filenames the loader cannot use", async () => {
    const options = await setup();
    await expect(convertMatrix({ ...options, manifestPath: path.join(os.tmpdir(), "manifest.json") })).rejects.toThrow("share a directory");
    await expect(convertMatrix({ ...options, output: path.join(dir, "matrix.bin") })).rejects.toThrow("component-local gzip filename");
  });
});

describe("conversion CLI options", () => {
  it("requires explicit files/dimension and rejects ambiguous or missing options", () => {
    expect(parseArgs(["--input", "old.gz", "--output", "new.gz", "--dimension", "4047", "--manifest", "manifest.json", "--replace-manifest"]))
      .toEqual({ input: "old.gz", output: "new.gz", dimension: 4047, manifestPath: "manifest.json", replaceManifest: true });
    expect(parseArgs(["--help"])).toEqual({ help: true });
    expect(() => parseArgs([])).toThrow("required");
    expect(() => parseArgs(["--input", "--output"])).toThrow("Missing value");
    expect(() => parseArgs(["--dimension", "2.5"])).toThrow("Invalid dimension");
    expect(() => parseArgs(["--help", "--help"])).toThrow("Repeated option");
    expect(() => parseArgs(["--overwrite"])).toThrow("Unknown option");
  });
});
