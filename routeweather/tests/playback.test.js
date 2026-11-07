// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from "vitest";

import {
  createClock, createPlaybackUI, MAX_FRAME_MS, PLAYBACK_DURATION_MS, SLIDER_MAX,
  VALUETEXT_INTERVAL_MS,
} from "../js/playback.js";
import { PLAYBACK_SPEEDS } from "../js/config.js";

// --- fake frame clock ---

// requestAnimationFrame stand-in: frames run only when the test calls step().
function fakeFrames() {
  let t = 1000;
  let nextId = 1;
  const pending = new Map();
  return {
    now: () => t,
    raf: (cb) => {
      const id = nextId++;
      pending.set(id, cb);
      return id;
    },
    caf: (id) => pending.delete(id),
    pendingCount: () => pending.size,
    // Advance time by `ms` and run the frames that were waiting.
    step(ms) {
      t += ms;
      const due = [...pending.values()];
      pending.clear();
      due.forEach((cb) => cb(t));
    },
    advance(ms) { t += ms; },
  };
}

function makeClock(options = {}) {
  const frames = fakeFrames();
  const ticks = [];
  const states = [];
  const clock = createClock({
    durationMs: 1000,
    onTick: (f) => ticks.push(f),
    onStateChange: (p) => states.push(p),
    now: frames.now,
    raf: frames.raf,
    caf: frames.caf,
    ...options,
  });
  return { clock, frames, ticks, states };
}

// --- clock ---

describe("createClock", () => {
  it("defaults to the 30 s placeholder duration", () => {
    expect(PLAYBACK_DURATION_MS).toBe(30000);
  });

  it("advances the fraction with time while playing", () => {
    const { clock, frames, ticks, states } = makeClock();
    clock.play();
    expect(clock.isPlaying()).toBe(true);
    expect(states).toEqual([true]);
    frames.step(100);
    frames.step(150);
    expect(ticks).toEqual([expect.closeTo(0.1, 9), expect.closeTo(0.25, 9)]);
    expect(clock.fraction()).toBeCloseTo(0.25, 9);
  });

  it("stops advancing when paused", () => {
    const { clock, frames, ticks, states } = makeClock();
    clock.play();
    frames.step(100);
    clock.pause();
    expect(frames.pendingCount()).toBe(0);
    frames.step(500);
    expect(ticks).toHaveLength(1);
    expect(clock.isPlaying()).toBe(false);
    expect(states).toEqual([true, false]);
  });

  it("does not count paused time when it resumes", () => {
    const { clock, frames } = makeClock();
    clock.play();
    frames.step(100);
    clock.pause();
    frames.advance(5000);
    clock.play();
    frames.step(100);
    expect(clock.fraction()).toBeCloseTo(0.2, 9);
  });

  it("seeks, ticking the new fraction, and carries on from there", () => {
    const { clock, frames, ticks } = makeClock();
    clock.seek(0.5);
    expect(ticks).toEqual([0.5]);
    expect(clock.isPlaying()).toBe(false);
    clock.play();
    frames.step(100);
    expect(clock.fraction()).toBeCloseTo(0.6, 9);
    clock.seek(2);
    expect(clock.fraction()).toBe(1);
    clock.seek(-1);
    expect(clock.fraction()).toBe(0);
  });

  it("counts time from the seek, not from before it", () => {
    const { clock, frames } = makeClock();
    clock.play();
    frames.advance(200);
    clock.seek(0.5);
    frames.step(100);
    expect(clock.fraction()).toBeCloseTo(0.6, 9);
  });

  it("multiplies the pace by the speed", () => {
    const { clock, frames } = makeClock();
    clock.setSpeed(4);
    clock.play();
    frames.step(100);
    expect(clock.fraction()).toBeCloseTo(0.4, 9);
    expect(clock.speed()).toBe(4);
  });

  it("banks time at the old speed when the speed changes mid-frame", () => {
    const { clock, frames } = makeClock();
    clock.play();
    frames.advance(100);
    clock.setSpeed(2);
    frames.step(100);
    expect(clock.fraction()).toBeCloseTo(0.3, 9);
  });

  it("clamps the time banked on a speed change at the end", () => {
    const { clock, frames, ticks } = makeClock();
    clock.seek(0.95);
    clock.play();
    frames.advance(200);
    clock.setSpeed(2);
    expect(clock.fraction()).toBe(1);
    frames.step(100);
    expect(ticks.at(-1)).toBe(1);
    expect(clock.isPlaying()).toBe(false);
  });

  it("ignores a speed that isn't a positive number", () => {
    const { clock } = makeClock();
    clock.setSpeed(0);
    clock.setSpeed(-2);
    clock.setSpeed(NaN);
    expect(clock.speed()).toBe(1);
  });

  it("stops at the end, ticking exactly 1", () => {
    const { clock, frames, ticks, states } = makeClock();
    clock.play();
    for (let i = 0; i < 6; i++) frames.step(200);
    expect(ticks[ticks.length - 1]).toBe(1);
    expect(ticks.filter((f) => f === 1)).toHaveLength(1);
    expect(clock.isPlaying()).toBe(false);
    expect(states).toEqual([true, false]);
    expect(frames.pendingCount()).toBe(0);
  });

  it("restarts from the beginning when played at the end", () => {
    const { clock, frames, ticks } = makeClock();
    clock.seek(1);
    clock.toggle();
    expect(clock.isPlaying()).toBe(true);
    expect(ticks).toEqual([1, 0]);
    frames.step(100);
    expect(clock.fraction()).toBeCloseTo(0.1, 9);
  });

  it("toggles between playing and paused", () => {
    const { clock } = makeClock();
    clock.toggle();
    expect(clock.isPlaying()).toBe(true);
    clock.toggle();
    expect(clock.isPlaying()).toBe(false);
  });

  it("caps one frame's step so a background tab doesn't jump ahead", () => {
    const { clock, frames } = makeClock({ durationMs: 10000 });
    clock.play();
    frames.step(60000);
    expect(clock.fraction()).toBeCloseTo(MAX_FRAME_MS / 10000, 9);
  });

  it("play while playing doesn't schedule a second frame loop", () => {
    const { clock, frames } = makeClock();
    clock.play();
    clock.play();
    expect(frames.pendingCount()).toBe(1);
  });

  it("stops the loop when onTick pauses", () => {
    let clock;
    const frames = fakeFrames();
    clock = createClock({
      durationMs: 1000, now: frames.now, raf: frames.raf, caf: frames.caf,
      onTick: () => clock.pause(),
    });
    clock.play();
    frames.step(100);
    expect(clock.isPlaying()).toBe(false);
    expect(frames.pendingCount()).toBe(0);
  });

  it("keeps one frame loop when onTick pauses and plays again", () => {
    let clock;
    const frames = fakeFrames();
    clock = createClock({
      durationMs: 1000, now: frames.now, raf: frames.raf, caf: frames.caf,
      onTick: () => { clock.pause(); clock.play(); },
    });
    clock.play();
    frames.step(100);
    expect(clock.isPlaying()).toBe(true);
    expect(frames.pendingCount()).toBe(1);
  });

  it("pauses and rethrows when onTick throws", () => {
    const states = [];
    const frames = fakeFrames();
    const clock = createClock({
      durationMs: 1000, now: frames.now, raf: frames.raf, caf: frames.caf,
      onTick: () => { throw new Error("boom"); },
      onStateChange: (p) => states.push(p),
    });
    clock.play();
    expect(() => frames.step(100)).toThrow("boom");
    expect(clock.isPlaying()).toBe(false);
    expect(states).toEqual([true, false]);
    expect(frames.pendingCount()).toBe(0);
  });

  it("destroy cancels the pending frame", () => {
    const { clock, frames } = makeClock();
    clock.play();
    clock.destroy();
    expect(frames.pendingCount()).toBe(0);
    expect(clock.isPlaying()).toBe(false);
  });
});

// --- controls ---

describe("createPlaybackUI", () => {
  let container;
  let calls;
  let ui;

  let t;

  beforeEach(() => {
    document.body.innerHTML = "<div id=\"pb\"></div>";
    container = document.getElementById("pb");
    calls = [];
    t = 0;
    ui = createPlaybackUI(container, {
      onToggle: () => calls.push(["toggle"]),
      onSeek: (f) => calls.push(["seek", f]),
      onSpeed: (m) => calls.push(["speed", m]),
      onScrubStart: () => calls.push(["start"]),
      onScrubEnd: () => calls.push(["end"]),
      now: () => t,
    });
  });

  it("builds a play button, a labelled slider, a speed select and the time", () => {
    const { button, slider, select, time } = ui.elements;
    expect(container.contains(button)).toBe(true);
    expect(button.textContent).toContain("Play");
    expect(slider.type).toBe("range");
    expect(slider.min).toBe("0");
    expect(slider.max).toBe(String(SLIDER_MAX));
    expect(slider.getAttribute("aria-label")).toBe("Position along the route");
    expect([...select.options].map((o) => Number(o.value))).toEqual(PLAYBACK_SPEEDS);
    expect(select.closest("label").textContent).toContain("Speed");
    expect(time.textContent).toBe("");
  });

  it("reports the button, slider and select through the callbacks", () => {
    const { button, slider, select } = ui.elements;
    button.click();
    slider.value = "250";
    slider.dispatchEvent(new Event("input"));
    select.value = "4";
    select.dispatchEvent(new Event("change"));
    expect(calls).toEqual([["toggle"], ["seek", 0.25], ["speed", 4]]);
  });

  it("switches the button label between Play and Pause", () => {
    const { button } = ui.elements;
    ui.setPlaying(true);
    expect(button.textContent).toContain("Pause");
    expect(button.classList.contains("playing")).toBe(true);
    ui.setPlaying(false);
    expect(button.textContent).toContain("Play");
    expect(button.textContent).not.toContain("Pause");
  });

  it("moves the slider and sets its spoken value and the visible time", () => {
    const { slider, time } = ui.elements;
    ui.setPosition(0.123, "12.3 km · 10:42", "10:42 · 2 h 28 min · 12.3 km");
    expect(slider.value).toBe("123");
    expect(slider.getAttribute("aria-valuetext")).toBe("12.3 km · 10:42");
    expect(time.textContent).toBe("10:42 · 2 h 28 min · 12.3 km");
  });

  it("shows a text-style play icon (VS15), not an emoji", () => {
    expect(ui.elements.button.querySelector(".pb-icon").textContent).toBe("\u25B6\uFE0E");
  });

  it("brackets a pointer drag of the slider with scrub start and end", () => {
    const { slider } = ui.elements;
    slider.dispatchEvent(new MouseEvent("pointerdown", { button: 0 }));
    slider.dispatchEvent(new MouseEvent("pointerdown", { button: 0 }));
    slider.value = "400";
    slider.dispatchEvent(new Event("input"));
    slider.dispatchEvent(new MouseEvent("pointerup"));
    slider.dispatchEvent(new Event("change"));
    expect(calls).toEqual([["start"], ["seek", 0.4], ["end"]]);
  });

  it("captures the pointer and ends the scrub when the capture is lost", () => {
    const { slider } = ui.elements;
    const captured = [];
    slider.setPointerCapture = (id) => captured.push(id);
    const down = new MouseEvent("pointerdown", { button: 0 });
    Object.defineProperty(down, "pointerId", { value: 7 });
    slider.dispatchEvent(down);
    expect(captured).toEqual([7]);
    // Released off the slider with no value change: no pointerup or change.
    slider.dispatchEvent(new Event("lostpointercapture"));
    expect(calls).toEqual([["start"], ["end"]]);
  });

  it("ignores a non-primary button on the slider", () => {
    ui.elements.slider.dispatchEvent(new MouseEvent("pointerdown", { button: 2 }));
    expect(calls).toEqual([]);
  });

  it("doesn't rewrite the slider or time when they haven't changed", () => {
    const { slider, time } = ui.elements;
    ui.setPosition(0.5, "a", "b");
    slider.setAttribute("aria-valuetext", "sentinel");
    time.textContent = "sentinel";
    slider.value = "7";
    ui.setPosition(0.5, "a", "b");
    expect(slider.getAttribute("aria-valuetext")).toBe("sentinel");
    expect(time.textContent).toBe("sentinel");
    expect(slider.value).toBe("7");
  });

  it("throttles the spoken value while playing, and catches up on pause", () => {
    const { slider, time } = ui.elements;
    const spoken = () => slider.getAttribute("aria-valuetext");
    ui.setPlaying(true);
    ui.setPosition(0.1, "1 km", "1");
    expect(spoken()).toBe("1 km");
    t += 100;
    ui.setPosition(0.2, "2 km", "2");
    expect(spoken()).toBe("1 km");
    // The visible text and the slider still follow every frame.
    expect(time.textContent).toBe("2");
    expect(slider.value).toBe("200");
    t += VALUETEXT_INTERVAL_MS;
    ui.setPosition(0.3, "3 km", "3");
    expect(spoken()).toBe("3 km");
    t += 100;
    ui.setPosition(0.4, "4 km", "4");
    expect(spoken()).toBe("3 km");
    ui.setPlaying(false);
    expect(spoken()).toBe("4 km");
    // Paused, every position is spoken at once.
    ui.setPosition(0.5, "5 km", "5");
    expect(spoken()).toBe("5 km");
  });

  it("destroy empties the container", () => {
    ui.destroy();
    expect(container.children).toHaveLength(0);
  });
});
