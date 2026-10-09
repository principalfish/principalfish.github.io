// The direct transport uses the same cloneable requests as an asynchronous backend.
import { dailyGame } from "./data.js";
import { createDailyEngine } from "./engine.js";
import { createManualAnalysis } from "./analysis.js";

export function createComputeService(state) {
  const daily = createDailyEngine(state);
  const manual = createManualAnalysis(state);
  let day = null;
  let answers = null;

  function dispatch(message) {
    switch (message.type) {
      case "initialize":
      case "catalogue":
        return structuredClone({ pool: state.POOL, expanded: state.ALL_GUESSES });
      case "load-day":
        return dailyGame(state, message.day).then((words) => {
          day = message.day;
          answers = words ? words.slice() : null;
          return answers ? { status: "loaded", day, ...daily.load(answers) }
            : { status: "unavailable-day", day };
        });
      case "advance-daily":
        if (!answers) throw new Error("No daily puzzle loaded");
        return daily.advance(message.expanded);
      case "reset-daily":
        return answers ? daily.load(answers) : null;
      case "restore-daily":
        day = message.day;
        answers = message.continuation.words.slice();
        return daily.restore(message.continuation);
      case "analyse-manual":
        return manual.analyse(message.slots, message.clueGrid, message.expanded, message.compact);
      case "restore-manual":
        manual.reset();
        manual.restore(message.slots, message.clueGrid);
        return null;
      case "replay-manual":
        return manual.replay(message.index, message.clueGrid, message.expanded);
      case "reset-manual":
        manual.reset();
        return null;
      default:
        throw new Error(`Unknown computation request: ${message.type}`);
    }
  }

  let lastTiming = null;
  function request(message) {
    const start = performance.now();
    const result = dispatch(message);
    lastTiming = { computeMs: performance.now() - start };
    return result;
  }
  return { request, getTiming: () => lastTiming };
}
