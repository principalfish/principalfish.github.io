// Route playback: a clock that walks a fraction 0 -> 1 along the route on
// animation frames, and the controls that drive it (play/pause, a position
// slider, a speed select and the current time/distance).
//
// The clock is pure time logic: `now`, `raf` and `caf` are injected, so tests
// step it with a fake clock. It knows nothing about routes; the playback
// controller maps a fraction to a distance (fraction × route length).
//
// Pacing: at 1× the whole route plays in PLAYBACK_DURATION_MS, whatever its
// length or the planned speed; config.js PLAYBACK_SPEEDS multiply that. Both
// are placeholders to be tuned.

import { PLAYBACK_SPEEDS } from "./config.js";

export const PLAYBACK_DURATION_MS = 30000;

// The slider's integer range: 1000 steps is finer than any slider is wide.
export const SLIDER_MAX = 1000;

// Longest time one animation frame may advance the clock (see frame()).
export const MAX_FRAME_MS = 250;

const clamp01 = (f) => Math.min(Math.max(Number(f) || 0, 0), 1);

// --- clock ---

// `onTick(fraction)` fires on every frame while playing, on seek, and when a
// play restarts from the end. `onStateChange(playing)` fires whenever playing
// starts or stops (including reaching the end), so the UI can follow.
export function createClock({
  durationMs = PLAYBACK_DURATION_MS,
  onTick = () => {},
  onStateChange = () => {},
  now = () => performance.now(),
  raf = (cb) => requestAnimationFrame(cb),
  caf = (id) => cancelAnimationFrame(id),
} = {}) {
  let fraction = 0;
  let speed = 1;
  let playing = false;
  let frameId = null;
  let lastMs = 0;

  function cancelFrame() {
    if (frameId !== null) caf(frameId);
    frameId = null;
  }

  function setPlaying(next) {
    if (playing === next) return;
    playing = next;
    if (!playing) cancelFrame();
    onStateChange(playing);
  }

  // Time is measured with `now()` rather than raf's timestamp so a test's fake
  // clock drives both. A frame's step is capped at MAX_FRAME_MS: a background
  // tab gets no frames, and coming back to it should carry on from where it
  // was rather than jump ahead by the minutes it was hidden.
  const advance = (t) => clamp01(
    fraction + (Math.min(t - lastMs, MAX_FRAME_MS) * speed) / durationMs,
  );

  // onTick may pause (or pause and play again, which schedules its own
  // frame), so the loop only continues if it is still the one playing. If
  // onTick throws, playback stops (the button shows Play) and the error
  // still surfaces.
  function frame() {
    frameId = null;
    if (!playing) return;
    const t = now();
    fraction = advance(t);
    lastMs = t;
    try {
      onTick(fraction);
    } catch (err) {
      setPlaying(false);
      throw err;
    }
    if (!playing || frameId !== null) return;
    if (fraction >= 1) setPlaying(false);
    else frameId = raf(frame);
  }

  function play() {
    if (playing) return;
    // A frame left over from before (none should be) must not run twice.
    cancelFrame();
    // Playing from the end starts again from the beginning.
    if (fraction >= 1) {
      fraction = 0;
      onTick(fraction);
    }
    lastMs = now();
    setPlaying(true);
    frameId = raf(frame);
  }

  const pause = () => setPlaying(false);
  const toggle = () => (playing ? pause() : play());

  // Jumps to `f` (clamped to [0, 1]); keeps playing if it was, from there.
  function seek(f) {
    fraction = clamp01(f);
    lastMs = now();
    onTick(fraction);
  }

  // Multiplier on the 1× pace; ignores anything not a positive number.
  function setSpeed(multiplier) {
    if (!(multiplier > 0) || !Number.isFinite(multiplier)) return;
    // Bank the time played at the old speed first.
    if (playing) {
      const t = now();
      fraction = advance(t);
      lastMs = t;
    }
    speed = multiplier;
  }

  function destroy() {
    cancelFrame();
    playing = false;
  }

  return {
    play,
    pause,
    toggle,
    seek,
    setSpeed,
    destroy,
    isPlaying: () => playing,
    fraction: () => fraction,
    speed: () => speed,
  };
}

// --- controls ---

// How often the slider's spoken value may change while playing: a screen
// reader re-announcing it 60 times a second is noise. Paused, it follows every
// seek at once, and pausing catches it up.
export const VALUETEXT_INTERVAL_MS = 1000;

// "▶" with VS15 so it renders as text, not a colour emoji.
const PLAY_ICON = "\u25B6\uFE0E";
const PAUSE_ICON = "❚❚";

// Builds the controls into `container` (replacing its contents):
//
//   [▶ Play] [=====o==========] Speed [1× v]   Mon 10:42 · 1 h 05 min · 12.3 km
//
// Callbacks: onToggle(), onSeek(fraction) while the slider moves (input),
// onSpeed(multiplier), and onScrubStart() / onScrubEnd() around a pointer drag
// of the slider (pointerdown, then pointerup / pointercancel /
// lostpointercapture / change, once),
// so the controller can pause playback while you scrub. The controller pushes
// state back through setPlaying / setPosition, which only write the DOM when
// a value changed.
//
// The play button changes its visible label ("Play" / "Pause") rather than
// carrying aria-pressed: a toggle button's name must stay fixed when it uses
// aria-pressed, and "Pause, pressed" reads worse than the label alone.
export function createPlaybackUI(container, {
  onSeek = () => {},
  onToggle = () => {},
  onSpeed = () => {},
  onScrubStart = () => {},
  onScrubEnd = () => {},
  now = () => performance.now(),
} = {}) {
  const doc = container.ownerDocument;

  function el(tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const root = el("div", "pb-controls");

  const button = el("button", "pb-toggle");
  button.type = "button";
  const icon = el("span", "pb-icon");
  icon.setAttribute("aria-hidden", "true");
  const label = el("span", "pb-label");
  button.append(icon, label);

  const slider = el("input", "pb-slider");
  slider.type = "range";
  slider.min = "0";
  slider.max = String(SLIDER_MAX);
  slider.step = "1";
  slider.value = "0";
  slider.setAttribute("aria-label", "Position along the route");

  const speedLabel = el("label", "pb-speed");
  const speedText = el("span", "pb-speed-text", "Speed");
  const select = el("select");
  for (const m of PLAYBACK_SPEEDS) {
    const option = el("option", undefined, `${m}×`);
    option.value = String(m);
    select.append(option);
  }
  speedLabel.append(speedText, select);

  const time = el("span", "pb-time");
  // Visual only: the slider's (throttled) valuetext is what assistive tech
  // reads, and a live region here would announce every frame.
  time.setAttribute("aria-hidden", "true");

  root.append(button, slider, speedLabel, time);
  container.replaceChildren(root);

  let scrubbing = false;
  const endScrub = () => {
    if (!scrubbing) return;
    scrubbing = false;
    onScrubEnd();
  };
  button.addEventListener("click", () => onToggle());
  slider.addEventListener("pointerdown", (event) => {
    if (event.button > 0 || scrubbing) return;
    scrubbing = true;
    // Captured, so a release off the slider still ends the scrub (and
    // lostpointercapture covers the release being swallowed altogether).
    if (event.pointerId !== undefined) slider.setPointerCapture?.(event.pointerId);
    onScrubStart();
  });
  slider.addEventListener("input", () => onSeek(Number(slider.value) / SLIDER_MAX));
  slider.addEventListener("pointerup", endScrub);
  slider.addEventListener("pointercancel", endScrub);
  slider.addEventListener("lostpointercapture", endScrub);
  slider.addEventListener("change", endScrub);
  select.addEventListener("change", () => onSpeed(Number(select.value)));

  let shownPlaying = null;
  let shownValue = null;
  let shownValueText = null;
  let pendingValueText = null;
  let valueTextAt = -Infinity;
  let shownTime = null;

  function writeValueText(text) {
    pendingValueText = null;
    valueTextAt = now();
    if (text === shownValueText) return;
    shownValueText = text;
    slider.setAttribute("aria-valuetext", text);
  }

  function setPlaying(playing) {
    if (playing === shownPlaying) return;
    shownPlaying = playing;
    icon.textContent = playing ? PAUSE_ICON : PLAY_ICON;
    label.textContent = playing ? "Pause" : "Play";
    button.classList.toggle("playing", playing);
    if (!playing && pendingValueText !== null) writeValueText(pendingValueText);
  }

  // `valueText` is the slider's spoken value ("12.3 km · 10:42"); `timeText`
  // the visible readout beside it.
  function setPosition(fraction, valueText = "", timeText = valueText) {
    const value = String(Math.round(clamp01(fraction) * SLIDER_MAX));
    if (value !== shownValue) {
      shownValue = value;
      slider.value = value;
    }
    if (!shownPlaying || now() - valueTextAt >= VALUETEXT_INTERVAL_MS) writeValueText(valueText);
    else pendingValueText = valueText;
    if (timeText !== shownTime) {
      shownTime = timeText;
      time.textContent = timeText;
    }
  }

  const destroy = () => container.replaceChildren();

  setPlaying(false);
  return { setPlaying, setPosition, destroy, elements: { button, slider, select, time } };
}
