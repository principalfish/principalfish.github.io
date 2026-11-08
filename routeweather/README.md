# Route weather

Static page (`js/`, `css/`, `tests/` = Vitest specs).

`/routeweather/` takes a GPX file, an activity speed and a start time up to 7
days ahead, and shows the weather you would meet at each point of the route
when you get there. Everything runs in the browser; there is no build step
(native ES modules) and no API key.

**Data sources**
- Forecast: [Open-Meteo](https://open-meteo.com/) `models=ukmo_seamless` (Met
  Office UKV 2 km over the UK and Ireland, Met Office Global 10 km elsewhere),
  CC-BY 4.0, non-commercial free tier. Only the sampled point coordinates leave
  the browser.
- Elevation: the GPX's own `<ele>` values; Open-Meteo's elevation API only when
  the file has none.
- Map: [OpenTopoMap](https://opentopomap.org/) tiles (CC-BY-SA, OSM data) on
  Leaflet 1.9.4, vendored with `npm run vendor:leaflet`.

**How it works**
- The forecast is requested from now to 7 days ahead. Open-Meteo returns
  nulls past the model's last step (the Met Office global model's run started
  hours earlier, so its 7 days end a little sooner), and the page uses the
  last hour with data everywhere as the forecast's end: the status line says
  when that is, and the latest valid start follows it.
- The route is sampled at up to 150 points (about every 1 km, plus every
  summit and valley with at least 30 m of prominence), and every sample is
  fetched once per GPX load. Speed, start and units changes only recompute.
- Requests use `elevation=nan`, so each location comes back with its model
  grid cell's own height. Temperature and wind are then interpolated through
  the model's vertical profile — the 2 m / 10 m values at cell height plus the
  1000/925/850/700 hPa levels at their geopotential heights — to the walker's
  GPX altitude. Below the lowest profile point temperature follows a 6.5 K/km
  lapse rate; above 700 hPa it clamps. Gusts at altitude are the altitude wind
  plus the 10 m gust's excess over the 10 m mean wind.
- The wind chart shows wind speed and gusts at your altitude; wind relative
  to the direction of travel (headwind and crosswind) is in the readout and
  table. The summary gives the coldest temperature, the coldest feels-like and
  the strongest wind on the route, with where and when.
- Sky: cloud cover and sunshine per hour, shown as a "Sky" chart strip, in
  the readout and table, and as a route total ("~X h of Y h daylight").
  `ukmo_seamless` only has `sunshine_duration` for about the first 2¾ days
  (the UKV range); after that it is estimated as the hour's daylight minutes
  × (1 − cloud cover) and labelled "est.".
- Sunrise and sunset are computed in the browser with NOAA's solar position
  algorithm (no API call), at the walker's position and arrival time. The
  summary gives the start day's times at the start point and says when the
  route starts before sunrise or runs past sunset; the charts shade the dark
  stretches and mark ☀↑ / ☀↓ where you pass them; the readout says whether
  it is light and how long until the next change. They are for a flat
  horizon: terrain can hide the sun for longer.
- Feels like: Steadman's apparent temperature without radiation, as the
  Australian Bureau of Meteorology uses it: AT = T + 0.33·e − 0.70·v − 4.00,
  with T the altitude-corrected temperature (°C), v the altitude-corrected
  wind (m/s) and e = (RH/100)·6.105·exp(17.27·T/(237.7+T)) hPa from the
  model's 2 m relative humidity. Shown in the table, the readout, a dotted line
  on the temperature chart and the summary (coldest on the route). Its fixed
  −4 °C term means calm, dry air already "feels" 4 °C colder than it is, and
  it only reads warmer than the air when it is humid and still; it ignores
  sunshine.
- Units: Metric / Imperial is the preset (distance, elevation, speed);
  "Customise units" overrides wind (km/h | mph), rain (mm | in) and
  temperature (°C | °F) one by one, and picking a preset resets them. The URL
  carries `wind`, `rain` and `temp` only when they differ from the preset.
- Times: once a route loads, one extra `timezone=auto` request for its first
  point gives the route's IANA time zone, and every time on the page — the
  start input included — is shown in it (labelled with the zone name). Until
  then, or if that lookup fails, times are in the browser's zone. The URL's
  `start` is always UTC. A wall time that happens twice when clocks go back
  reads as the earlier one; one skipped when clocks go forward moves on by the
  gap.

**Limits of the altitude adjustment.** It is linear interpolation between a
handful of heights, so it captures broad inversions and stronger winds aloft but
not local effects: valley cold pools below the model terrain, wind acceleration
over a ridge, or sheltering in a corrie. Beyond about 2 days the forecast comes
from the 10 km global model, whose terrain is much smoother. The profile tops
out at the 700 hPa level (about 3,000 m): above it, temperature and wind are
held at that level's values, and the page says so (a summary note, a "Held"
badge on the table row and a note in the readout).

**API budget.** A full 150-point route costs about 390 of Open-Meteo's weighted
calls (26 hourly variables count as 2.6 calls per location), plus 1 for the
time zone lookup. The free tier allows
600 a minute and 10,000 a day per IP. Open-Meteo sends no rate-limit headers, so
the page prints an estimate of this browser's usage to the console after each
load (`routeweather/js/usage.js`).

**Tests:** `npm test` runs the `routeweather/tests/` Vitest specs (pure modules
in Node, page/map/charts under jsdom). No browser is needed.
