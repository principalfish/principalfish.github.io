// Wordle-pattern comparison. Reuses wordleColors from manual.js (the canonical
// two-pass implementation) and adds base-3 pattern-code helpers that mirror
// referdle/fastcompare.py (weights [1,3,9,27,81]).

export { wordleColors } from "../manual.js";
import { wordleColors } from "../manual.js";

const WEIGHTS = [1, 3, 9, 27, 81];

// Base-3 code of a colour array like [0,1,2,0,1].
export function patternCode(colors) {
  let code = 0;
  for (let i = 0; i < 5; i++) code += colors[i] * WEIGHTS[i];
  return code;
}

// Base-3 code of a colour string like "01201".
export function cluePatternCode(colorStr) {
  let code = 0;
  for (let i = 0; i < 5; i++) code += (colorStr.charCodeAt(i) - 48) * WEIGHTS[i];
  return code;
}

// Pattern code of a single guess vs a single answer.
export function compareCode(guess, answer) {
  return patternCode(wordleColors(guess, answer));
}

// get_comparison(guess, answer) -> "01201" string.
export function getComparison(guess, answer) {
  return wordleColors(guess, answer).join("");
}

// On-the-fly pattern matrix (probe words vs a small answer set). Int32Array,
// row-major [guessWords.length * answerWords.length].
export function patternMatrix(guessWords, answerWords) {
  const ng = guessWords.length, na = answerWords.length;
  const out = new Int32Array(ng * na);
  for (let gi = 0; gi < ng; gi++) {
    const g = guessWords[gi];
    for (let ai = 0; ai < na; ai++) {
      out[gi * na + ai] = compareCode(g, answerWords[ai]);
    }
  }
  return out;
}
