import { readFile } from "node:fs/promises";
const SAMPLE_URL = new URL("sample.json", import.meta.url);

export async function loadSample() {
  const sample = JSON.parse(await readFile(SAMPLE_URL, "utf8"));
  validateSample(sample);
  return sample;
}

export function validateSample(sample) {
  if (sample?.schemaVersion !== 1 || sample.range?.start !== 1000 || sample.range?.end !== 1414
      || !Array.isArray(sample.games) || sample.games.length !== 100) throw new Error("Invalid frozen 100-game sample");
  const expectedDays = Array.from({ length: 100 }, (_, i) => 1000 + Math.round(i * 414 / 99));
  for (let i = 0; i < sample.games.length; i++) {
    const game = sample.games[i];
    if (game.day !== expectedDays[i] || !Array.isArray(game.answers) || game.answers.length !== 5
        || !game.answers.every((word) => typeof word === "string" && /^[A-Z]{5}$/.test(word))) {
      throw new Error(`Invalid frozen sample case at index ${i}`);
    }
  }
}

export async function validateSampleAnswers(sample, getAnswers) {
  validateSample(sample);
  for (const game of sample.games) {
    const actual = await getAnswers(game.day);
    if (!actual || actual.length !== game.answers.length || actual.some((word, i) => word !== game.answers[i])) {
      throw new Error(`Frozen answers differ from loaded daily #${game.day}`);
    }
  }
}
