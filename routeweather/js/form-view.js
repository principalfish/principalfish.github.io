// The inputs panel: GPX file input + drop zone, activity, speed, start time,
// units (the preset and the "Customise units" toggles), the load status line,
// the Retry button and the plan error. Owns that DOM only; every user action
// is reported through `onChange(event)`:
//
//   { type: "file", file }        a GPX file chosen or dropped
//   { type: "activity", value }   "walk" | "run" | "cycle" | "custom"
//   { type: "speed", value }      the speed input's raw text (active units)
//   { type: "start", value }      the start input's raw "YYYY-MM-DDTHH:MM"
//   { type: "commit" }            the speed or start edit was committed
//                                 (`change`), so it can go into the URL
//   { type: "units", value }      "metric" | "imperial" picked (also when
//                                 already checked: re-picking the preset
//                                 resets the per-quantity toggles)
//   { type: "unit", quantity, value }
//                                 a per-quantity toggle: quantity "wind" |
//                                 "rain" | "temp", value a units.js
//                                 UNIT_CHOICES token ("mph", "in", "f", ...)
//   { type: "retry" }
//
// `update(state)` refreshes everything derived from state on each render,
// writing to the DOM only where something changed (so the live regions don't
// re-announce). The speed and start VALUES are only written by
// syncSpeed/syncStart, which the controller calls when the change didn't come
// from that input — rewriting a field on every render would fight the user's
// typing ("12." -> "12").

import { kmhToSpeed, UNIT_CHOICES } from "./units.js";
import { toLocalInputValue } from "./schedule.js";
import { zoneLabel } from "./zone.js";

const SPEED_UNIT = { metric: "km/h", imperial: "mph" };

// Radio group name for each per-quantity toggle.
const UNIT_TOGGLE_NAME = { wind: "windUnit", rain: "rainUnit", temp: "tempUnit" };
const UNIT_QUANTITIES = Object.keys(UNIT_CHOICES);

// Assign `el[prop] = value` only when it differs.
function setProp(el, prop, value) {
  if (el[prop] !== value) el[prop] = value;
}

export function createFormUI(doc, onChange) {
  const $ = (id) => doc.getElementById(id);
  const emit = (event) => onChange(event);
  const radios = (name) => Array.from(doc.querySelectorAll(`input[name="${name}"]`));

  // --- listeners ---

  $("gpxFile").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) emit({ type: "file", file });
    // Clear so choosing the same file again (e.g. to retry) fires `change`.
    e.target.value = "";
  });

  const zone = $("dropZone");

  // A file dropped anywhere else on the page would make the browser navigate
  // to it, losing the page; swallow those, and show "no drop" outside the zone.
  doc.addEventListener("dragover", (e) => {
    e.preventDefault();
    const inZone = e.target instanceof Element && e.target.closest("#dropZone");
    if (e.dataTransfer && !inZone) e.dataTransfer.dropEffect = "none";
  });
  doc.addEventListener("drop", (e) => e.preventDefault());

  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("dragging");
  });
  zone.addEventListener("dragleave", (e) => {
    // Moving onto a child of the zone fires dragleave on the zone itself.
    if (e.relatedTarget && zone.contains(e.relatedTarget)) return;
    zone.classList.remove("dragging");
  });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragging");
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) emit({ type: "file", file });
  });

  radios("activity").forEach((el) => {
    el.addEventListener("change", () => emit({ type: "activity", value: el.value }));
  });
  // `click`, not `change`: re-picking the already-checked preset must still
  // reset the toggles, and `change` doesn't fire for a checked radio. A
  // keyboard selection (arrows, Space) fires `click` too.
  radios("units").forEach((el) => {
    el.addEventListener("click", () => emit({ type: "units", value: el.value }));
  });
  for (const quantity of UNIT_QUANTITIES) {
    radios(UNIT_TOGGLE_NAME[quantity]).forEach((el) => {
      el.addEventListener("change", () => emit({ type: "unit", quantity, value: el.value }));
    });
  }
  for (const [id, type] of [["speedInput", "speed"], ["startInput", "start"]]) {
    $(id).addEventListener("input", (e) => emit({ type, value: e.target.value }));
    $(id).addEventListener("change", () => emit({ type: "commit" }));
  }
  $("retryButton").addEventListener("click", () => emit({ type: "retry" }));

  // --- explicit value sync ---

  // Rounded to 1 dp so a shared 10 mph link shows 10, not 9.9979. The speed
  // follows the preset (units.system), never the wind setting.
  function syncSpeed({ speedKmh, units }) {
    const value = kmhToSpeed(speedKmh, units);
    if (Number.isFinite(value)) $("speedInput").value = String(Math.round(value * 10) / 10);
  }

  // In the route's zone once it is known (state.timeZone), else the browser's.
  function syncStart({ startMs, timeZone }) {
    $("startInput").value = Number.isFinite(startMs) ? toLocalInputValue(startMs, timeZone) : "";
  }

  // Expands the "Customise units" disclosure (e.g. when the URL overrides a
  // unit); after that it's left to the user.
  function openCustomise() {
    $("unitsCustomise").open = true;
  }

  // The status line has tabindex="-1" so focus can land on it.
  function focusStatus() {
    $("status").focus();
  }

  // --- render ---

  function update(state) {
    radios("activity").forEach((el) => setProp(el, "checked", el.value === state.activity));
    const { units } = state;
    radios("units").forEach((el) => setProp(el, "checked", el.value === units.system));
    for (const quantity of UNIT_QUANTITIES) {
      radios(UNIT_TOGGLE_NAME[quantity])
        .forEach((el) => setProp(el, "checked", el.value === units[quantity]));
    }
    setProp($("speedUnit"), "textContent", SPEED_UNIT[units.system]);

    const start = $("startInput");
    const { timeZone } = state;
    setProp(start, "min", toLocalInputValue(state.startBounds.minMs, timeZone));
    setProp(start, "max", toLocalInputValue(state.startBounds.maxMs, timeZone));
    const zone = timeZone ? `route time: ${zoneLabel(state.startMs, timeZone)}` : "your local time";
    setProp($("startZone"), "textContent", zone);

    const status = $("status");
    const isError = state.status.kind === "error";
    setProp(status, "textContent", state.status.message);
    if (status.classList.contains("error") !== isError) {
      status.classList.toggle("error", isError);
      status.classList.toggle("muted", !isError);
    }

    // Kept visible but disabled while a retry runs, so the layout holds still.
    const retry = $("retryButton");
    setProp(retry, "hidden", state.retry === null);
    setProp(retry, "disabled", state.retry === "busy");

    // Never hidden: a live region inside display:none may not be announced.
    setProp($("planError"), "textContent", state.planError ?? "");
  }

  return { update, syncSpeed, syncStart, openCustomise, focusStatus };
}
