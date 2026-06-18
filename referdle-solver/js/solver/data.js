// Loads the static solver assets (pattern matrix + word lists + bundled dailies)
// and exposes the daily/day arithmetic ported from referdle/daily.py. No server.

import { getComparison } from "./compare.js";

export const POOL_N = 4047;
export const SET_SIZE = 1000;
export const MAX_BUNDLED_DAY = 1999;

// Referdle launch date. new Date("25 Nov 2022") parses to LOCAL midnight in the
// original JS; mirror that (month is 0-indexed in JS).
const EPOCH_MS = new Date(2022, 10, 25).getTime();
const DAY_MS = 86400000;

// Resolve the data dir relative to THIS module (static/js/solver/data.js ->
// static/data), so it works regardless of which HTML page loads it.
const BASE = new URL("../../data", import.meta.url).href;

export function dayNumber(date = new Date()) {
  return Math.floor((date.getTime() - EPOCH_MS) / DAY_MS);
}

// Which bundled file holds `day`, and the offset within it. Mirrors daily.py.
export function dailyLocation(day) {
  const setN = Math.floor(day / SET_SIZE);
  return {
    set: setN,
    index: day - setN * SET_SIZE,
    file: `daily-${(setN + 1) * SET_SIZE}.json`,
  };
}

export function pmCode(PM, N, i, j) {
  return PM[i * N + j];
}

async function fetchText(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.text();
}

async function fetchJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function parseWordList(text) {
  return text
    .split("\n")
    .map((w) => w.trim().toUpperCase())
    .filter((w) => w.length === 5);
}

// Decompress the gzipped raw int16 matrix into an Int16Array.
async function loadMatrix(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  const ds = new DecompressionStream("gzip");
  const stream = res.body.pipeThrough(ds);
  const buf = await new Response(stream).arrayBuffer();
  // Raw little-endian int16. On little-endian hosts (every browser target),
  // a direct Int16Array view is correct.
  return new Int16Array(buf);
}

export async function loadAssets(onProgress) {
  const note = (m) => onProgress && onProgress(m);

  note("Loading word lists…");
  const [poolTxt, expandedTxt, pluralsTxt, manifest] = await Promise.all([
    fetchText(`${BASE}/pool.txt`),
    fetchText(`${BASE}/expanded.txt`),
    fetchText(`${BASE}/plurals.txt`),
    fetchJSON(`${BASE}/manifest.json`),
  ]);

  const POOL = parseWordList(poolTxt);
  const EXPANDED = parseWordList(expandedTxt);
  const PLURALS = new Set(parseWordList(pluralsTxt));
  const ALL_GUESSES = POOL.concat(EXPANDED);
  const N = manifest.matrix_dim || POOL.length;

  const poolIndex = new Map();
  for (let i = 0; i < POOL.length; i++) poolIndex.set(POOL[i], i);

  note(`Decompressing pattern matrix (${(manifest.gzip_bytes / 1e6).toFixed(1)} MB)…`);
  const PM = await loadMatrix(`${BASE}/pool_matrix.int16.gz`);
  if (PM.length !== N * N) {
    throw new Error(`matrix length ${PM.length} != ${N}*${N}`);
  }

  return {
    PM,
    N,
    POOL,
    EXPANDED,
    ALL_GUESSES,
    PLURALS,
    poolIndex,
    dailyCache: new Map(),
    manifest,
  };
}

async function loadDailyFile(state, file) {
  if (!state.dailyCache.has(file)) {
    let arr = null;
    try {
      arr = await fetchJSON(`${BASE}/${file}`);
    } catch (e) {
      arr = null;
    }
    state.dailyCache.set(file, arr);
  }
  return state.dailyCache.get(file);
}

// The 5 answers for a day, uppercased (or null if unavailable).
export async function dailyGame(state, day) {
  const loc = dailyLocation(day);
  const arr = await loadDailyFile(state, loc.file);
  if (!arr) return null;
  const puzzle = loc.index < arr.length ? arr[loc.index] : arr[0];
  return puzzle.map((w) => w.toUpperCase());
}

// Clue grid for a day's words: row i = get_comparison(words[i], final), row 5 green.
export function dailyClueGrid(words) {
  const final = words[4];
  return [
    getComparison(words[0], final),
    getComparison(words[1], final),
    getComparison(words[2], final),
    getComparison(words[3], final),
    "22222",
  ];
}

// Day numbers (newest first, capped at MAX_BUNDLED_DAY) where all 5 answers are
// in the pool. Includes pre-day-1000 games; the all-in-pool filter still excludes
// the 84 broken old-era games whose answers aren't representable. Async because
// daily files are loaded lazily/cached.
export async function testableDays(state, endDay) {
  const today = dayNumber();
  const end = Math.min(endDay == null ? today : endDay, MAX_BUNDLED_DAY);
  const out = [];
  for (let d = end; d >= 0; d--) {
    const words = await dailyGame(state, d);
    if (!words) continue;
    if (words.every((w) => state.poolIndex.has(w))) out.push(d);
  }
  return out; // newest first
}

// {day, words} for a calendar date, or null if no bundled puzzle / not testable.
export async function dailyForDate(state, date) {
  const day = dayNumber(date);
  if (day < 0 || day > MAX_BUNDLED_DAY) return null;
  const words = await dailyGame(state, day);
  if (!words) return null;
  return { day, words };
}
