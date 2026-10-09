// Tagged JSON keeps field presence and every JavaScript number intact.
export const TRACE_SCHEMA_VERSION = 1;

function pack(value, ancestors = new Set()) {
  if (value === null) return ["null"];
  if (value === undefined) return ["undefined"];
  if (typeof value === "number") {
    const special = Number.isNaN(value) ? "NaN"
      : value === Infinity ? "Infinity"
        : value === -Infinity ? "-Infinity"
          : Object.is(value, -0) ? "-0" : value;
    return ["number", special];
  }
  if (typeof value === "string" || typeof value === "boolean") return [typeof value, value];
  if (typeof value !== "object") throw new Error(`Unsupported trace value: ${typeof value}`);
  if (ancestors.has(value)) throw new Error("Cyclic trace value");
  if (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype
      && Object.getPrototypeOf(value) !== null) throw new Error("Trace objects must be plain objects");
  ancestors.add(value);
  let result;
  if (Array.isArray(value)) {
    result = ["array", Array.from({ length: value.length }, (_, i) =>
      Object.hasOwn(value, i) ? pack(value[i], ancestors) : ["hole"])];
  } else {
    result = ["object", Object.keys(value).sort().map((key) => [key, pack(value[key], ancestors)])];
  }
  ancestors.delete(value);
  return result;
}

function unpack(node) {
  if (!Array.isArray(node)) throw new Error("Invalid trace node");
  const [tag, value] = node;
  if (tag === "null" && node.length === 1) return null;
  if (tag === "undefined" && node.length === 1) return undefined;
  if (tag === "string" && node.length === 2 && typeof value === "string") return value;
  if (tag === "boolean" && node.length === 2 && typeof value === "boolean") return value;
  if (tag === "number" && node.length === 2) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (value === "NaN") return NaN;
    if (value === "Infinity") return Infinity;
    if (value === "-Infinity") return -Infinity;
    if (value === "-0") return -0;
  }
  if (tag === "array" && node.length === 2 && Array.isArray(value)) {
    const result = new Array(value.length);
    value.forEach((entry, i) => {
      if (!(Array.isArray(entry) && entry.length === 1 && entry[0] === "hole")) result[i] = unpack(entry);
    });
    return result;
  }
  if (tag === "object" && node.length === 2 && Array.isArray(value)) {
    const result = {};
    for (const entry of value) {
      if (!Array.isArray(entry) || entry.length !== 2 || typeof entry[0] !== "string"
          || Object.hasOwn(result, entry[0])) throw new Error("Invalid trace object field");
      Object.defineProperty(result, entry[0], {
        value: unpack(entry[1]), enumerable: true, configurable: true, writable: true,
      });
    }
    return result;
  }
  throw new Error(`Invalid trace tag: ${tag}`);
}

export function encodeTrace(value) {
  return JSON.stringify(pack(value));
}

export function decodeTrace(encoded) {
  return unpack(JSON.parse(encoded));
}

function excerpt(value, present) {
  if (!present) return "<missing>";
  const encoded = encodeTrace(value);
  return encoded.length <= 180 ? encoded : `${encoded.slice(0, 177)}...`;
}

// Object insertion order is immaterial; array order and property presence are exact.
export function compareTraces(expected, actual) {
  function visit(a, b, path, aPresent = true, bPresent = true) {
    if (aPresent !== bPresent) return difference();
    if (Object.is(a, b)) return null;
    if (a === null || b === null || typeof a !== "object" || typeof b !== "object"
        || Array.isArray(a) !== Array.isArray(b)) return difference();
    if (Array.isArray(a)) {
      if (a.length !== b.length) return visit(a.length, b.length, `${path}.length`);
      for (let i = 0; i < a.length; i++) {
        const result = visit(a[i], b[i], `${path}[${i}]`, Object.hasOwn(a, i), Object.hasOwn(b, i));
        if (result) return result;
      }
    } else {
      const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort();
      for (const key of keys) {
        const result = visit(a[key], b[key], `${path}[${JSON.stringify(key)}]`, Object.hasOwn(a, key), Object.hasOwn(b, key));
        if (result) return result;
      }
    }
    return null;

    function difference() {
      return { path, expected: excerpt(a, aPresent), actual: excerpt(b, bPresent) };
    }
  }
  const difference = visit(expected, actual, "$");
  if (!difference) return { equal: true };
  const moveMatch = difference.path.match(/\["moves"\]\[(\d+)\]/);
  return {
    equal: false,
    day: actual?.day ?? expected?.day,
    expanded: actual?.expanded ?? expected?.expanded,
    moveIndex: moveMatch ? Number(moveMatch[1]) : null,
    ...difference,
  };
}

export function normalizeTrace({ day, answers, expanded, clueGrid, publication, finalSlots, finalResult }) {
  if (!publication || publication.moves.length !== publication.steps.length) throw new Error("Missing or inconsistent game publication");
  const moves = publication.moves.map((move, i) => {
    const { __benchmarkBefore: before, ...suggest } = publication.steps[i].suggest;
    if (!Object.hasOwn(publication.steps[i].suggest, "__benchmarkBefore")) {
      throw new Error(`Missing before-state observation at move ${i}`);
    }
    return { ...move, before, suggest, after: publication.steps[i].after };
  });
  const boardsComplete = finalSlots.every((slot, b) => {
    const last = slot.guesses.at(-1);
    return last?.word === answers[b] && last.colors === "22222";
  });
  return {
    schemaVersion: TRACE_SCHEMA_VERSION,
    day, answers, expanded, clueGrid, moves,
    final: {
      game: publication.game,
      moves: publication.moves,
      slots: finalSlots,
      result: finalResult,
      status: boardsComplete && finalResult?.solvable ? "solved" : "incomplete",
    },
  };
}
