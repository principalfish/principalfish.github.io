// Playback glue: owns the charts, the playback controls, the readout and the
// clock, and on every tick moves all of them (and the map's position marker)
// to the same point on the route.
//
// `createPlaybackController({ mapView, chartsEl, playbackEl, clockOptions })`
// builds the charts into `chartsEl` (#chartsPanel) and the controls plus the
// readout into `playbackEl` (#playback), and returns:
//
//   update(state, reason)   register with the page controller's addView, AFTER
//                           the map view, so a tick here lands after the map's
//                           own update
//   tick(fraction)          move everything to `fraction` of the route (the
//                           clock calls this; exposed for tests)
//   clock, destroy()
//
// Per tick: fraction -> distKm -> weatherAtDistance -> map marker, chart
// cursor, readout and the slider/time text. Nothing is rebuilt per frame.
//
// Dragging the slider or the chart pauses playback for the drag and resumes
// it afterwards if it was playing.
//
// On a new route the position goes back to the start and playback pauses. New
// results for the same route (speed, start or units changed) keep the position
// and the playing state, and re-tick so everything shows the new numbers.
// Without results (an invalid plan) the clock pauses; the panels are hidden.
//
// There is no autoplay, so prefers-reduced-motion needs nothing here: motion
// only ever starts from the Play button.

import { createChartsUI } from "./charts.js";
import { createClock, createPlaybackUI } from "./playback.js";
import { createReadoutUI } from "./readout-view.js";
import { timeAtDistance, weatherAtDistance } from "./along-route.js";
import { formatDistance } from "./units.js";
import { formatArrival, formatDuration } from "./display.js";

export function createPlaybackController({ mapView, chartsEl, playbackEl, clockOptions = {} }) {
  const doc = playbackEl.ownerDocument;
  let state = null;
  let route = null;
  let results = null;
  let units = null;
  // While a slider or chart drag is in progress: whether it interrupted play.
  let scrub = null;

  const controlsEl = doc.createElement("div");
  controlsEl.className = "pb-bar";
  const readoutEl = doc.createElement("div");
  readoutEl.className = "pb-readout";
  playbackEl.replaceChildren(controlsEl, readoutEl);

  const charts = createChartsUI(chartsEl, {
    onSeek: (km) => seekKm(km),
    onScrubStart: () => startScrub(),
    onScrubEnd: () => endScrub(),
  });
  const readout = createReadoutUI(readoutEl);
  const ui = createPlaybackUI(controlsEl, {
    onToggle: () => clock.toggle(),
    onSeek: (f) => clock.seek(f),
    onSpeed: (m) => clock.setSpeed(m),
    onScrubStart: () => startScrub(),
    onScrubEnd: () => endScrub(),
    ...(clockOptions.now ? { now: clockOptions.now } : {}),
  });
  const clock = createClock({
    ...clockOptions,
    onTick: (f) => tick(f),
    onStateChange: (playing) => ui.setPlaying(playing),
  });

  const totalKm = () => state?.route?.totalDistKm ?? 0;

  function seekKm(km) {
    const total = totalKm();
    clock.seek(total > 0 ? km / total : 0);
  }

  // Scrubbing pauses playback for the length of the drag, then resumes it
  // if it was playing (unless the drag ended at the finish).
  function startScrub() {
    if (scrub) return;
    scrub = { wasPlaying: clock.isPlaying() };
    clock.pause();
  }

  function endScrub() {
    if (!scrub) return;
    const { wasPlaying } = scrub;
    scrub = null;
    if (wasPlaying && clock.fraction() < 1) clock.play();
  }

  // --- tick ---

  function tick(fraction) {
    if (!state?.route) return;
    const distKm = fraction * totalKm();
    const weather = weatherAtDistance(state, distKm);
    mapView?.setPosition(distKm, weather);
    charts.setCursor(distKm);
    const tMs = timeAtDistance(state, distKm);
    readout.update(state, weather, distKm, tMs);
    const where = formatDistance(distKm, state.units);
    // The weekday too when the time isn't today (multi-day routes).
    const when = Number.isFinite(tMs) ? formatArrival(tMs, state.nowMs ?? Date.now()) : "";
    const elapsed = Number.isFinite(tMs) ? formatDuration(tMs - state.startMs) : "";
    ui.setPosition(
      fraction,
      when ? `${where} · ${when}` : where,
      when ? `${when} · ${elapsed} · ${where}` : where,
    );
  }

  // --- update ---

  function update(next) {
    state = next;
    // Cheap when nothing it draws changed: it compares identities itself.
    charts.update(next);
    if (next.route !== route) {
      route = next.route;
      results = next.results;
      units = next.units;
      scrub = null;
      clock.pause();
      // seek() ticks, which also puts the map marker back at the start.
      clock.seek(0);
      return;
    }
    if (next.results === results && next.units === units) return;
    results = next.results;
    units = next.units;
    if (!next.results) {
      clock.pause();
      return;
    }
    tick(clock.fraction());
  }

  function destroy() {
    clock.destroy();
    charts.destroy();
    ui.destroy();
    playbackEl.replaceChildren();
  }

  return { update, tick, clock, destroy };
}
