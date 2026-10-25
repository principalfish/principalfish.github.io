// Time zones for display: wall-clock parts of an instant in a given IANA zone,
// and the reverse (a wall-clock time in a zone -> epoch ms), via Intl. Pure
// functions. Every `timeZone` argument is an IANA name ("Europe/Zurich") or
// null/undefined for the browser's own zone, so callers that never learn the
// route's zone behave exactly as before.
//
// Clock changes: a wall time that happens twice (clocks going back) resolves
// to the EARLIER instant; one that never happens (clocks going forward) is
// moved on by the size of the gap (01:30 on a spring-forward night in London
// -> 02:30 BST), which is what browsers do for their own zone.

const MINUTE_MS = 60 * 1000;
const DAY_MS = 24 * 60 * MINUTE_MS;

// One formatter per named zone: building an Intl.DateTimeFormat is slow, and
// the playback readout formats a time every frame. The browser's own zone
// uses Date's local getters instead, which always follow the current zone.
const formatters = new Map();

function partsFormatter(timeZone) {
  if (!formatters.has(timeZone)) {
    formatters.set(timeZone, new Intl.DateTimeFormat("en-GB", {
      timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
      weekday: "short",
    }));
  }
  return formatters.get(timeZone);
}

const WEEKDAY_INDEX = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

// True for a time zone name Intl accepts.
export function isValidTimeZone(timeZone) {
  if (typeof timeZone !== "string" || timeZone === "") return false;
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone });
    return true;
  } catch {
    return false;
  }
}

const NO_PARTS = Object.freeze({
  year: NaN, month: NaN, day: NaN, hour: NaN, minute: NaN, second: NaN, weekday: NaN,
});

// Epoch ms -> { year, month (1-12), day, hour, minute, second, weekday (0 =
// Sunday) } on the wall clock in `timeZone`; all NaN for a non-finite `ms`.
export function zonedParts(ms, timeZone) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return NO_PARTS;
  if (!timeZone) {
    const d = new Date(ms);
    return {
      year: d.getFullYear(),
      month: d.getMonth() + 1,
      day: d.getDate(),
      hour: d.getHours(),
      minute: d.getMinutes(),
      second: d.getSeconds(),
      weekday: d.getDay(),
    };
  }
  const out = {};
  for (const { type, value } of partsFormatter(timeZone).formatToParts(new Date(ms))) {
    if (type === "weekday") out.weekday = WEEKDAY_INDEX[value];
    else if (type !== "literal") out[type] = Number(value);
  }
  return out;
}

// The zone's offset from UTC at `ms`, in ms (BST: +3600000). Seconds are
// dropped: no zone in use today has them, and LMT-era offsets don't matter here.
export function zoneOffsetMs(ms, timeZone) {
  const p = zonedParts(ms, timeZone);
  const wall = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
  return wall - Math.floor(ms / MINUTE_MS) * MINUTE_MS;
}

// The wall time (year, month 1-12, day, hour, minute) in `timeZone` -> epoch
// ms, resolving clock changes as described at the top. NaN for non-finite
// parts.
export function zonedTimeToMs({ year, month, day, hour, minute }, timeZone) {
  const naive = Date.UTC(year, month - 1, day, hour, minute);
  if (!Number.isFinite(naive)) return NaN;
  // The offsets either side of any change near this wall time; a day either
  // way spans every real transition.
  const before = zoneOffsetMs(naive - DAY_MS, timeZone);
  const after = zoneOffsetMs(naive + DAY_MS, timeZone);
  const valid = [...new Set([before, after])]
    .map((offset) => naive - offset)
    .filter((ms) => zoneOffsetMs(ms, timeZone) === naive - ms);
  if (valid.length > 0) return Math.min(...valid);
  // In a gap: read with the offset from before the change, which lands the
  // gap's length later on the new clock.
  return naive - before;
}

// Same wall-clock day in `timeZone`.
export function sameZonedDay(a, b, timeZone) {
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
  const pa = zonedParts(a, timeZone);
  const pb = zonedParts(b, timeZone);
  return pa.year === pb.year && pa.month === pb.month && pa.day === pb.day;
}

// The zone's short name at `ms` where Intl has one ("BST", "CET"), else its
// GMT offset ("GMT+5:30"); "" when Intl gives nothing.
export function zoneAbbreviation(ms, timeZone) {
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      ...(timeZone ? { timeZone } : {}),
      timeZoneName: "short",
    }).formatToParts(new Date(ms));
    return parts.find((p) => p.type === "timeZoneName")?.value ?? "";
  } catch {
    return "";
  }
}

// "Europe/Zurich (CET)": the label shown next to route times. The IANA name's
// underscores read as spaces ("America/New York").
export function zoneLabel(ms, timeZone) {
  if (!timeZone) return "";
  const name = timeZone.replace(/_/g, " ");
  const abbr = zoneAbbreviation(ms, timeZone);
  return abbr && abbr !== name ? `${name} (${abbr})` : name;
}
