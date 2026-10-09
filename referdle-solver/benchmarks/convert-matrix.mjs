import { createHash } from "node:crypto";
import { lstat, link, mkdtemp, readFile, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gunzipSync, gzipSync } from "node:zlib";

const MATRIX_FILENAME = /^[A-Za-z0-9][A-Za-z0-9._-]*\.gz$/;

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

export function encodeLegacyMatrix(input, dimension) {
  const count = dimension * dimension;
  if (!Number.isSafeInteger(dimension) || dimension < 1 || !Number.isSafeInteger(count * 2)) {
    throw new Error("Matrix dimension must be a positive safe integer with a safe byte length");
  }
  const legacy = gunzipSync(input);
  if (legacy.length !== count * 2) throw new Error(`Legacy matrix length ${legacy.length} != ${count * 2}`);
  const view = new DataView(legacy.buffer, legacy.byteOffset, legacy.byteLength);
  // Validate signed little-endian values before any conversion can truncate them.
  for (let i = 0; i < count; i++) {
    const code = view.getInt16(i * 2, true);
    if (code < 0 || code > 242) throw new Error(`Invalid legacy pattern code ${code} at index ${i}`);
  }
  const bytes = Buffer.alloc(count);
  for (let i = 0; i < count; i++) bytes[i] = view.getInt16(i * 2, true);
  // Node's gzip header has zero mtime and no input filename; level is explicit.
  const compressed = gzipSync(bytes, { level: 9 });
  const decoded = gunzipSync(compressed);
  const patternsHash = sha256(bytes);
  if (decoded.length !== count || sha256(decoded) !== patternsHash) throw new Error("Converted matrix round-trip hash differs");
  for (let i = 0; i < count; i++) {
    if (decoded[i] !== view.getInt16(i * 2, true)) throw new Error(`Converted pattern differs at index ${i}`);
  }
  return {
    compressed,
    dimension,
    legacyRawBytes: legacy.length,
    decodedBytes: decoded.length,
    gzipBytes: compressed.length,
    patternsHash,
    gzipHash: sha256(compressed),
  };
}

async function existing(filename) {
  try { return await lstat(filename); } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

export async function convertMatrix({ input, output, dimension, manifestPath, replaceManifest = false }) {
  if (!input || !output || !manifestPath) throw new Error("Input, output and manifest paths are required");
  const inputPath = await realpath(input);
  const inputInfo = await stat(inputPath);
  if (!inputInfo.isFile()) throw new Error("Input must be a regular file");
  const outputDir = await realpath(path.dirname(path.resolve(output)));
  const manifestDir = await realpath(path.dirname(path.resolve(manifestPath)));
  if (outputDir !== manifestDir) throw new Error("Matrix output and manifest must share a directory");
  const outputPath = path.join(outputDir, path.basename(output));
  const targetManifest = path.join(manifestDir, path.basename(manifestPath));
  if (!MATRIX_FILENAME.test(path.basename(outputPath))) throw new Error("Matrix output must have a component-local gzip filename");
  if (outputPath === inputPath || targetManifest === inputPath || targetManifest === outputPath) {
    throw new Error("Input, output and manifest paths must be distinct");
  }
  if (await existing(outputPath)) throw new Error("Refusing to overwrite matrix output");
  const manifestInfo = await existing(targetManifest);
  let previousManifest = null;
  if (manifestInfo) {
    if (!manifestInfo.isFile()) throw new Error("Manifest must be a regular file, not a symlink");
    if (manifestInfo.dev === inputInfo.dev && manifestInfo.ino === inputInfo.ino) throw new Error("Manifest aliases matrix input");
    if (!replaceManifest) throw new Error("Replacing an existing manifest requires --replace-manifest");
    previousManifest = await readFile(targetManifest);
    const current = JSON.parse(previousManifest.toString("utf8"));
    if (current.pool_n !== dimension || current.matrix_dim !== dimension) throw new Error("Existing manifest dimensions differ");
  }

  const encoded = encodeLegacyMatrix(await readFile(inputPath), dimension);
  const manifest = {
    pool_n: dimension,
    matrix_dim: dimension,
    matrix_file: path.basename(outputPath),
    matrix_storage: "uint8",
    decoded_bytes: encoded.decodedBytes,
    gzip_bytes: encoded.gzipBytes,
    patterns_sha256: encoded.patternsHash,
    gzip_sha256: encoded.gzipHash,
  };
  const staging = await mkdtemp(path.join(outputDir, ".matrix-conversion-"));
  const stagedOutput = path.join(staging, "matrix.gz");
  const stagedManifest = path.join(staging, "manifest.json");
  let outputPublished = false;
  try {
    await writeFile(stagedOutput, encoded.compressed, { flag: "wx" });
    await writeFile(stagedManifest, `${JSON.stringify(manifest, null, 2)}\n`, { flag: "wx" });
    const stagedBytes = await readFile(stagedOutput);
    if (!stagedBytes.equals(encoded.compressed) || sha256(gunzipSync(stagedBytes)) !== encoded.patternsHash) {
      throw new Error("Staged matrix differs from validated conversion");
    }
    // Publish the validated matrix first, then switch the manifest to that filename.
    // Hard links refuse a destination that appeared after the initial path checks.
    await link(stagedOutput, outputPath);
    outputPublished = true;
    if (manifestInfo) {
      const currentInfo = await existing(targetManifest);
      if (!currentInfo?.isFile() || currentInfo.dev !== manifestInfo.dev || currentInfo.ino !== manifestInfo.ino
          || !(await readFile(targetManifest)).equals(previousManifest)) {
        throw new Error("Manifest changed during conversion");
      }
      await rename(stagedManifest, targetManifest);
    } else {
      await link(stagedManifest, targetManifest);
    }
  } catch (error) {
    if (outputPublished) await rm(outputPath);
    throw error;
  } finally {
    await rm(staging, { recursive: true, force: true });
  }
  const { compressed, ...report } = encoded;
  return { ...report, output: outputPath, manifest: targetManifest };
}

export const HELP = `Usage: node referdle-solver/benchmarks/convert-matrix.mjs
  --input FILE.gz --output FILE.gz --dimension N --manifest FILE.json
  [--replace-manifest]
Converts signed little-endian int16 gzip to uint8 gzip without regenerating patterns.
Output must not exist. Existing manifests require explicit replacement and matching dimensions.
Output and manifest must share an existing directory. Input is never modified.`;

export function parseArgs(args) {
  const options = {};
  const seen = new Set();
  const names = { "--input": "input", "--output": "output", "--dimension": "dimension", "--manifest": "manifestPath" };
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (seen.has(arg)) throw new Error(`Repeated option ${arg}`);
    seen.add(arg);
    if (arg === "--help") { options.help = true; continue; }
    if (arg === "--replace-manifest") { options.replaceManifest = true; continue; }
    if (!names[arg]) throw new Error(`Unknown option ${arg}`);
    const value = args[++i];
    if (!value || value.startsWith("--")) throw new Error(`Missing value for ${arg}`);
    if (arg === "--dimension" && !/^[1-9]\d*$/.test(value)) throw new Error("Invalid dimension");
    options[names[arg]] = arg === "--dimension" ? Number(value) : value;
  }
  if (!options.help && (!options.input || !options.output || !options.dimension || !options.manifestPath)) throw new Error("Input, output, dimension and manifest are required");
  return options;
}

export async function main(args = process.argv.slice(2)) {
  const options = parseArgs(args);
  if (options.help) { process.stdout.write(`${HELP}\n`); return; }
  const result = await convertMatrix(options);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
}
