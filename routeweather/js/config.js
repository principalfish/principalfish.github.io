// Static configuration for the route weather page. Pure data — no logic beyond
// assembling the Open-Meteo variable list from its parts.

// Activity presets. Speeds are internal units (km/h) and are placeholders to be
// tuned later. `custom` has no preset: the user types their own speed.
export const ACTIVITIES = {
  walk: { label: "Walk", defaultSpeedKmh: 5 },
  run: { label: "Run", defaultSpeedKmh: 10 },
  cycle: { label: "Cycle", defaultSpeedKmh: 20 },
  custom: { label: "Custom", defaultSpeedKmh: null },
};

export const DEFAULT_ACTIVITY = "walk";

// Accepted speed range (km/h), shared by typed input and the URL. The floor
// keeps route durations bounded; the ceiling is well above any cycling average.
export const SPEED_RANGE_KMH = { min: 0.5, max: 100 };

// Playback multipliers for the route animation. 1x is intended to play the
// whole route in roughly 30 seconds; exact pacing is still to be decided.
export const PLAYBACK_SPEEDS = [1, 2, 4];

// Route-sampling constants: at most `maxSamples` weather points, no closer than
// `minSpacingKm`, plus local elevation extremes that stand out by at least
// `prominenceM` metres.
export const SAMPLING = {
  maxSamples: 150,
  minSpacingKm: 1,
  prominenceM: 30,
};

// Pressure levels (hPa) requested for upper-air temperature and wind.
export const PRESSURE_LEVELS = [1000, 925, 850, 700];

const SURFACE_VARS = [
  "temperature_2m",
  "precipitation",
  "precipitation_probability",
  "weather_code",
  "wind_speed_10m",
  "wind_direction_10m",
  "wind_gusts_10m",
];

const PRESSURE_LEVEL_VARS = [
  "temperature",
  "wind_speed",
  "wind_direction",
  "geopotential_height",
];

// Every Open-Meteo hourly variable this page requests: surface variables, then
// each pressure-level variable for each level (e.g. `temperature_850hPa`).
export const HOURLY_VARS = [
  ...SURFACE_VARS,
  ...PRESSURE_LEVELS.flatMap((level) =>
    PRESSURE_LEVEL_VARS.map((name) => `${name}_${level}hPa`),
  ),
];

// Open-Meteo forecast length in hours (7 days). The window starts at 00:00 UTC
// today, not now, so check a route's finish against the last hour the API
// actually returns rather than now + FORECAST_HOURS.
export const FORECAST_HOURS = 168;

// OpenTopoMap base layer.
export const TILE_URL = "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png";
export const TILE_SUBDOMAINS = ["a", "b", "c"];
export const TILE_MAX_ZOOM = 17;
export const TILE_ATTRIBUTION =
  `Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, SRTM`
  + ` | Map style &copy; <a href="https://opentopomap.org">OpenTopoMap</a>`
  + ` (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)`;

// Weather data credit (Open-Meteo data is CC-BY 4.0).
export const OPEN_METEO_ATTRIBUTION =
  `Weather data by <a href="https://open-meteo.com/">Open-Meteo.com</a>`
  + ` (<a href="https://creativecommons.org/licenses/by/4.0/">CC-BY 4.0</a>)`;
