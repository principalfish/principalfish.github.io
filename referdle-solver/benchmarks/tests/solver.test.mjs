import { beforeAll, describe, expect, it } from "vitest";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import { COMPONENT_DIR } from "../harness.mjs";

let solver;

beforeAll(async () => {
  const solverDir = path.join(COMPONENT_DIR, "js/solver");
  const solverPath = path.join(solverDir, "solver.js");
  const output = await build({
    stdin: {
      contents: `export { solve, poolBlock, chainArcConsistency } from ${JSON.stringify(solverPath)};
export { compareCode } from ${JSON.stringify(path.join(solverDir, "compare.js"))};`,
      resolveDir: solverDir,
    },
    bundle: true, write: false, format: "esm", platform: "node", logLevel: "silent",
    define: { "import.meta.url": JSON.stringify(pathToFileURL(path.join(solverDir, "data.js")).href) },
    plugins: [{
      name: "solver-test-access",
      setup(builder) {
        builder.onLoad({ filter: /[/\\]solver\.js$/ }, async (args) => ({
          contents: `${await readFile(args.path, "utf8")}\nexport { poolBlock, chainArcConsistency };`,
          resolveDir: solverDir,
        }));
      },
    }],
  });
  solver = await import(`data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text).toString("base64")}`);
});

function assets(pool) {
  const poolIndex = new Map(pool.map((word, i) => [word, i]));
  const PM = new Int16Array(pool.flatMap((guess) => pool.map((answer) => solver.compareCode(guess, answer))));
  return { PM, N: pool.length, poolIndex };
}

function block(aWords, bWords, pool) {
  const { PM, N, poolIndex } = assets(pool);
  return solver.poolBlock(aWords, bWords, PM, N, poolIndex);
}

function solve(pool, slots, clueGrid = null) {
  const { PM, N, poolIndex } = assets(pool);
  return solver.solve(slots, clueGrid, pool, PM, N, poolIndex, new Set(), false, false, false);
}

describe("pattern blocks", () => {
  it("preserves directional repeated-letter patterns and the all-green byte", () => {
    const rows = block(["LEVEL", "EERIE"], ["EERIE", "LEVEL"], ["EERIE", "LEVEL"]);
    expect(rows.every((row) => row instanceof Uint8Array)).toBe(true);
    expect(rows.map((row) => [...row])).toEqual([[33, 242], [242, 7]]);
  });

  it("computes patterns when either or both words are outside the matrix pool", () => {
    const rows = block(["LEVEL", "APPLE"], ["APPLE", "ALLEY", "LEVEL"], ["ALLEY", "LEVEL"]);
    expect(rows.map((row) => [...row])).toEqual([[4, 136, 242], [242, 110, 108]]);
  });

  it("handles empty row and column sets", () => {
    expect(block([], ["LEVEL"], ["LEVEL"])).toEqual([]);
    const rows = block(["LEVEL"], [], ["LEVEL"]);
    expect(rows).toHaveLength(1);
    expect([...rows[0]]).toEqual([]);
  });

  it("keeps the matching middle column before removing unsupported rows", () => {
    const pool = ["SLATE", "CRANE", "BRICK", "FROST", "CHIME"];
    const { N, poolIndex } = assets(pool);
    const PM = new Int16Array(N * N);
    PM[poolIndex.get("SLATE") * N + poolIndex.get("FROST")] = 1;
    const candidates = [["SLATE", "CRANE"], ["BRICK", "FROST", "CHIME"], [], [], []];
    solver.chainArcConsistency(candidates, ["10000", null, null, null, null], PM, N, poolIndex);
    expect(candidates).toEqual([["SLATE"], ["FROST"], [], [], []]);
  });
});

describe("small solver states", () => {
  it("pins a clued chain with an off-pool guess and repeated-letter answers", () => {
    const answers = ["ABCDE", "FGHIJ", "KLMNO", "PQQRS", "TTUVW"];
    const pool = ["LEVEL", ...answers.slice().reverse()];
    const slots = answers.map((word, i) => ({
      guesses: [{ word: i === 0 ? "ABCDZ" : word, colors: i === 0 ? "22220" : "22222" }],
    }));
    const result = solve(pool, slots, ["00000", "00000", "00000", "00000", "22222"]);
    expect(result.solvable).toBe(true);
    expect(result.clueUsed).toBe(true);
    expect(result.cands).toEqual(answers.map((word) => [word]));
    expect(result.perSlotFeasible).toEqual(answers.map((word) => [word]));
    expect(result.viableFinals).toEqual(["TTUVW"]);
    expect(result.unionFeasible).toEqual(answers);
  });

  it("reports an empty board when an off-pool all-green guess has no answer", () => {
    const slots = Array.from({ length: 5 }, () => ({ guesses: [] }));
    slots[0].guesses.push({ word: "EERIE", colors: "22222" });
    const result = solve(["LEVEL"], slots);
    expect(result.solvable).toBe(false);
    expect(result.candCounts).toEqual([0, 1, 1, 1, 1]);
    expect(result.reason).toBe("Word(s) 1 have no candidate words at all.");
    expect(result.perSlotFeasible).toEqual([[], [], [], [], []]);
  });
});
