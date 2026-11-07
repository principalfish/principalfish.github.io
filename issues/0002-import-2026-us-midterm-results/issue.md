# Import 2018–2026 US House and Senate results from NYT data

The 2026 midterms were held on 3 November, and the US maps still stop at 2024. FiveThirtyEight, the source of the current US House and Senate imports, closed in 2025. This issue builds a re-runnable toolchain on the New York Times' published results JSON (AP calls), covering House and Senate from 2018 to 2026. It replaces the 538-sourced 2020–2024 data in place and adds House 2018 and 2020 and Senate 2018.

Provisional 2026 results go in now and refresh as counts finish. Uncalled races are stored with votes so far and flagged, not given to the leader. Runoffs and ranked-choice races keep the deciding round. Senate specials are kept: they take the state's map slot when they stand alone, and are listed separately when they clash with a regular race. The local data console gets a fetch, preview and import tool, so a refresh doesn't need the command line.

Nine states redrew their House districts for 2026 (CA, TX, UT, OH, NC, FL, TN, LA, AL). A new 2026 district map is stitched from the Census 2024 file plus those states' enacted plans. A 2012-cycle map serves 2018 and 2020. The predict pages roll forward to 2028 baselines, opening on the 2026 result. The Current Senate is rebuilt from the 2026 winners, and the site credits NYT/AP under the map.

Out of scope: governors, presidential sources, 2028 forecast models, and scoring the 2026 forecast against the result (a separate issue).
