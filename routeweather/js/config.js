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
  // precipitation_probability is fetched separately (PROBABILITY below).
  "precipitation",
  "weather_code",
  "wind_speed_10m",
  "wind_direction_10m",
  "wind_gusts_10m",
  // Sky: cloud cover is instantaneous (%), sunshine_duration the seconds of
  // sunshine in the PRECEDING hour. ukmo_seamless only has sunshine for about
  // the first 2¾ days (the UKV range; nulls after), so weather.js estimates it
  // beyond that from cloud cover and daylight.
  "cloud_cover",
  "sunshine_duration",
  // For the feels-like temperature (weather.js apparentTemperatureC).
  "relative_humidity_2m",
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

// The chance of rain comes from its own small request. ukmo_seamless's
// precipitation_probability is a 3-member ensemble (0/33/67/100 %) that
// often disagrees with the rain amounts beside it (0 % chance with 0.7 mm/h);
// the Met Office global model's comes from its ~18-member ensemble. That
// ensemble is ~20 km, so it is fetched at points `spacingKm` apart along the
// route (at most `maxPoints`, both ends included) rather than at every sample,
// costing a few weighted calls instead of one per sample (usage.js).
export const PROBABILITY = Object.freeze({
  model: "ukmo_global_deterministic_10km",
  variable: "precipitation_probability",
  spacingKm: 10,
  maxPoints: 20,
});

// How far ahead of now the forecast is requested, in hours (7 days). Open-Meteo
// accepts this whatever the model, but pads hours past the model's last run
// step with nulls (the Met Office global model reaches about 7 days from its
// run time, which is hours in the past), so check a route's finish against the
// last hour with data (schedule.js forecastSpan), not now + FORECAST_HOURS.
// Up to 14 days costs no more per location (usage.js).
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
