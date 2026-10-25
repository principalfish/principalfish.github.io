// The playback readout: what it is like at the current position along the
// route. A compact grid of label/value pairs, built once; update() rewrites
// only the values that changed, so it is cheap enough for every frame.
//
// `createReadoutUI(container)` returns `update(state, weather, distKm, tMs)`,
// where `weather` is along-route.js weatherAtDistance() (null shows dashes)
// and `tMs` the arrival time at `distKm` (optional: timeAtDistance() when
// omitted). All text is written with textContent.

import { describeWeather } from "./weather.js";
import {
  formatDistance, formatElevation, formatPrecip, formatTemp, formatWind, MISSING,
} from "./units.js";
import {
  compassPoint, formatCrossWind, formatDateTime, formatDuration, formatHeadWind,
  isFiniteNumber as isNum,
} from "./display.js";
import { positionAtDistance } from "./route.js";
import { timeAtDistance } from "./along-route.js";

const FIELDS = [
  ["time", "Time"],
  ["elapsed", "Elapsed"],
  ["distance", "Distance"],
  ["elevation", "Elevation"],
  ["weather", "Weather"],
  ["temp", "Temperature"],
  ["wind", "Wind"],
  ["relative", "Head / cross"],
  ["rain", "Rain"],
  ["model", "Model"],
];

// Fields that need the weather (dashes without it).
const WEATHER_KEYS = ["weather", "temp", "wind", "relative", "rain", "model"];

const OUTSIDE_NOTE = "outside the forecast: nearest hour shown";

// Above the forecast's top level (weather.js aboveProfile).
const HELD_NOTE = "(held at the ~3,000 m value)";

// --- values ---

// Elevation here: the GPX's own (interpolated along the route) when it has
// some; otherwise the samples' elevation that the weather was computed for,
// marked "(model)" when that is only the forecast cell's terrain height.
function elevationText(state, weather, distKm, units) {
  if (state.route?.hasElevation) {
    return formatElevation(positionAtDistance(state.route, distKm).ele, units);
  }
  if (!weather) return MISSING;
  const ele = formatElevation(weather.eleUsed, units);
  return weather.eleFromModel && ele !== MISSING ? `${ele} (model)` : ele;
}

// Every field's text for this position; `tMs` is the arrival time there
// (worked out from the state when not given).
export function readoutValues(state, weather, distKm, tMs = timeAtDistance(state, distKm)) {
  const { units, route } = state;
  const w = weather;
  const values = {
    time: formatDateTime(tMs, state.timeZone),
    elapsed: isNum(tMs) ? formatDuration(tMs - state.startMs) : MISSING,
    distance: route
      ? `${formatDistance(distKm, units)} of ${formatDistance(route.totalDistKm, units)}`
      : MISSING,
    elevation: route ? elevationText(state, w, distKm, units) : MISSING,
  };
  if (!w) {
    return { ...values, ...Object.fromEntries(WEATHER_KEYS.map((key) => [key, MISSING])) };
  }
  const from = isNum(w.windKmh) ? ` from ${compassPoint(w.windFromDeg)}` : "";
  const chance = isNum(w.precipProb) ? `${Math.round(w.precipProb)}% chance` : MISSING;
  const description = describeWeather(w.code);
  return {
    ...values,
    weather: w.outsideForecast ? `${description} (${OUTSIDE_NOTE})` : description,
    temp: `${formatTemp(w.tempC, units)}${w.aboveProfile ? ` ${HELD_NOTE}` : ""}`,
    wind: `${formatWind(w.windKmh, units)}${from} · gusts ${formatWind(w.gustKmh, units)}`
      + (w.aboveProfile ? ` ${HELD_NOTE}` : ""),
    relative: `${formatHeadWind(w.headKmh, units)} · ${formatCrossWind(w.crossKmh, units)}`,
    rain: `${formatPrecip(w.precipMm, units)} · ${chance}`,
    model: `10 m wind ${formatWind(w.model10mWindKmh, units)}`
      + ` · cell elevation ${formatElevation(w.cellElev, units)}`,
  };
}

// --- factory ---

export function createReadoutUI(container) {
  const doc = container.ownerDocument;
  const list = doc.createElement("dl");
  list.className = "readout";
  const nodes = {};
  const shown = {};
  for (const [key, label] of FIELDS) {
    const item = doc.createElement("div");
    item.className = `readout-item readout-${key}`;
    const dt = doc.createElement("dt");
    dt.textContent = label;
    const dd = doc.createElement("dd");
    dd.textContent = MISSING;
    item.append(dt, dd);
    list.append(item);
    nodes[key] = dd;
    shown[key] = MISSING;
  }
  container.replaceChildren(list);

  function update(state, weather, distKm, tMs) {
    const values = readoutValues(state, weather, distKm, tMs);
    for (const [key] of FIELDS) {
      if (values[key] === shown[key]) continue;
      shown[key] = values[key];
      nodes[key].textContent = values[key];
    }
    list.classList.toggle("outside", Boolean(weather?.outsideForecast));
  }

  return { update };
}
