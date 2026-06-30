// ─── Postcode search (feature) ──────────────────────────────────────────────
//
// Postcode → constituency lookup, wired as an opt-in feature (pages list `postcode` in
// their parliament's `features`). Fully config-driven: the lookup endpoint, the result
// property to read, an optional boundary-mismatch warning, and an optional seat-rename
// fallback all come from the active mapMode's `postcode` block in the manifest — there is
// no jurisdiction-specific branching here. The markup is `hidden` by default, so on a page
// that doesn't load this feature the input never appears.

import { selectSeatBySearchQuery } from '../dom.js';
import { state } from '../state.js';
import { fetchJson } from '../files.js';
import { seatLookupKey } from '../utils.js';

const postcodeSearchInput = document.getElementById('maps-postcode-search');
const postcodeSearchGroup = postcodeSearchInput?.closest('.maps-toolbar-group-postcode') ?? null;
const postcodeWarningBtn = document.getElementById('mapsPostcodeWarningBtn');
const postcodeWarningPanel = document.getElementById('mapsPostcodeWarningPanel');
let postcodeErrorTimeout = null;

/**
 * Shows or hides the postcode search group based on whether the active mapMode configures a
 * `postcode` block. Surfaces the boundary-mismatch warning button only when the config opts
 * into it (`postcode.boundaryWarning`). Registered as a map-init hook so it runs per render.
 * @returns {void}
 */
export function initPostcodeSearch() {
  const cfg = state.mapConfig?.postcode;
  postcodeSearchGroup.hidden = !cfg;
  // Some maps' lookup API returns constituencies on different boundaries than the rendered
  // map; the warning icon toggles a panel listing the affected seats (markup is per-page HTML).
  const showWarning = Boolean(cfg?.boundaryWarning);
  postcodeWarningBtn.hidden = !showWarning;
  if (!showWarning) postcodeWarningPanel.hidden = true;
}

/**
 * Flashes an error message inside the postcode input for 2 seconds, then clears the
 * input so the placeholder is shown again. The input is made readonly during the flash
 * to prevent accidental edits. Cancels any in-flight error flash before starting a new one.
 * @param {string} msg - The error text to display in the input.
 * @returns {void}
 */
function showPostcodeError(msg) {
  // Cancel any in-flight flash before starting a new one to avoid overlapping timers.
  clearPostcodeError();
  // Display the error text in the input and lock it readonly so the user can't type over it.
  postcodeSearchInput.value = msg;
  postcodeSearchInput.readOnly = true;
  postcodeSearchInput.classList.add('is-postcode-error');
  // Store the timer ID so clearPostcodeError can cancel it if the user focuses before 2 s.
  postcodeErrorTimeout = window.setTimeout(() => {
    postcodeSearchInput.readOnly = false;
    postcodeSearchInput.value = '';
    postcodeSearchInput.classList.remove('is-postcode-error');
    postcodeErrorTimeout = null;
  }, 2000);
}

/**
 * Cancels any active postcode error flash and removes the error style.
 * Does not restore the input value — caller is responsible for that if needed.
 * @returns {void}
 */
function clearPostcodeError() {
  // Cancel the pending auto-clear timer.
  if (postcodeErrorTimeout) {
    clearTimeout(postcodeErrorTimeout);
    postcodeErrorTimeout = null;
  }
  // Restore the input to its normal editable state.
  if (postcodeSearchInput) {
    postcodeSearchInput.readOnly = false;
    postcodeSearchInput.classList.remove('is-postcode-error');
  }
}

/**
 * Looks up a postcode via the mapMode-configured API and returns the constituency name, or
 * null if the postcode is not found or the active map has no `postcode` config. The endpoint
 * URL prefix and the result property to read both come from `state.mapConfig.postcode`.
 * @param {string} postcode - The raw postcode string entered by the user.
 * @returns {Promise<string|null>} The constituency name, or null on failure.
 */
async function lookupPostcode(postcode) {
  const cfg = state.mapConfig?.postcode;
  if (!cfg?.endpoint || !cfg?.resultProperty) return null;

  // Strip all whitespace then re-insert the canonical space before the inward code
  // (always the last 3 characters). The lookup API requires this format.
  const stripped = postcode.trim().toUpperCase().replace(/\s+/g, '');
  const normalised = stripped.length >= 5 ? `${stripped.slice(0, -3)} ${stripped.slice(-3)}` : stripped;

  const url = `${cfg.endpoint}${encodeURIComponent(normalised)}`;

  try {
    // fetchJson throws on a non-OK status (e.g. 404 for an unknown postcode); the catch below
    // turns that into the same null this lookup returns for any other failure.
    const data = await fetchJson(url);
    const rawName = data?.result?.[cfg.resultProperty] ?? null;

    if (!rawName) return null;

    // Normalise accented characters to ASCII so names like "Ynys Môn" match
    // our seat data which stores the unaccented form "Ynys Mon".
    const constituencyName = rawName.normalize('NFD').replace(/[\u0300-\u036f]/g, '');

    // If the returned name has no match in the current seat index, apply the configured
    // boundary-rename fallback (e.g. when the API uses older boundaries than the map).
    const seatKey = seatLookupKey(constituencyName);
    if (!state.currentSeatNameByKey.has(seatKey) && cfg.seatRenames) {
      const mapped = cfg.seatRenames[constituencyName] ?? null;
      if (mapped) return mapped;
    }

    return constituencyName;
  } catch {
    return null;
  }
}

/**
 * Attaches event listeners to the postcode search input. On Enter or blur, looks up the
 * postcode and zooms to the matched constituency. Disables the input during the fetch,
 * shows an inline error on failure, and deduplicates blur-after-Enter submissions.
 * Guards against double-wiring via dataset flag.
 * @returns {void}
 */
export function wirePostcodeSearch() {
  if (postcodeSearchInput.dataset.wired === 'true') return;

  let lastSubmittedPostcode = '';

  /**
   * Reads the postcode input, runs the lookup, and selects the resolved seat.
   * Deduplicates against the last submitted value to avoid double-fetching on blur after Enter.
   * @returns {void}
   */
  const submitPostcode = async () => {
    const query = postcodeSearchInput.value.trim();
    if (!query || query === lastSubmittedPostcode) return;
    lastSubmittedPostcode = query;
    postcodeSearchInput.disabled = true;
    clearPostcodeError();
    const constituencyName = await lookupPostcode(query);
    postcodeSearchInput.disabled = false;
    if (constituencyName) {
      selectSeatBySearchQuery(constituencyName);
    } else {
      showPostcodeError('Postcode not found');
    }
  };

  postcodeSearchInput.addEventListener('focus', () => {
    // If the error flash is showing, dismiss it and restore the original value
    // so the user can immediately retype without clearing "Postcode not found" manually.
    if (postcodeSearchInput.readOnly) {
      clearPostcodeError();
      postcodeSearchInput.value = '';
      lastSubmittedPostcode = '';
    }
  });
  postcodeSearchInput.addEventListener('input', () => {
    // Reset the dedup guard so the same value can be re-submitted after editing.
    lastSubmittedPostcode = '';
    clearPostcodeError();
  });
  postcodeSearchInput.addEventListener('keydown', (event) => {
    // Enter submits immediately without waiting for blur.
    if (event.key === 'Enter') {
      event.preventDefault();
      submitPostcode();
    }
  });
  postcodeSearchInput.addEventListener('blur', () => {
    // Brief delay absorbs Enter-then-blur so submitPostcode isn't called twice in quick succession.
    window.setTimeout(submitPostcode, 120);
  });

  postcodeSearchInput.dataset.wired = 'true';
}
