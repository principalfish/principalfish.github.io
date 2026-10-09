import { readFile, realpath } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export function createLocalFetch(dataDir, fallback = globalThis.fetch) {
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

