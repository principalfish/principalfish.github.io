// ─── Shell loader ─────────────────────────────────────────────────────────────
//
// Injects the shared app markup (shell.html) plus a page's opt-in fragments
// (fragments/*.html) into the page before the engine bundle is imported. Each page's
// index.html keeps only its own head/header and a <div id="mapsShellMount"> placeholder,
// then runs:
//
//   import { loadShell } from '../electionmapslogic/shell-loader.min.js';
//   await loadShell(['postcode', 'polltracker']);   // fragment names, page-declared
//   await import('./<page-entry>.min.js');
//
// The engine (dom.js, feature views, mobile-sidebar.js) queries shell elements at module
// load and wires [data-popup-action] buttons once at startup, so the shell and every
// fragment MUST be in the DOM before those modules are evaluated — hence the await
// between loadShell and the bundle import.
//
// This module is deliberately dependency-free (it runs before everything else and is
// minified standalone); paths resolve relative to this file so it works from any page.

/**
 * Fetches a same-origin HTML file and returns its text. Throws on non-OK status so a
 * missing shell/fragment fails loudly instead of injecting an error page's markup.
 * @param {URL} url - Fully resolved URL of the HTML file.
 * @returns {Promise<string>} The response body text.
 * @throws {Error} When the response status is not OK.
 */
async function fetchHtml(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${url}: ${response.status}`);
  }
  return response.text();
}

/**
 * Loads the shared shell markup and the page's fragments into the DOM.
 *
 * 1. Fetches `shell.html` and each `fragments/<name>.html` in parallel (URLs resolved
 *    against this module's location, so pages don't need to know the engine directory).
 * 2. Replaces the page's `#mapsShellMount` placeholder with the shell markup wholesale
 *    (`outerHTML`), so no wrapper element disturbs the `.maps-wrap` layout.
 * 3. Appends each fragment's top-level elements to the shell element matching their
 *    `data-shell-target` selector (the attribute is stripped after insertion). Fragments
 *    are applied in list order; every fragment element appends after the shell's own
 *    children, mirroring where the markup sat when it lived in the page HTML.
 *
 * @param {string[]} [fragments] - Fragment names (file stems under `fragments/`) this
 *   page opts into, e.g. `['postcode', 'referendum-info', 'polltracker']`. The list is
 *   page-declared, like the feature modules each page entry imports; the manifest still
 *   decides which features switch on.
 * @returns {Promise<void>}
 * @throws {Error} When the mount point is missing, a fetch fails, or a fragment names a
 *   target selector the shell doesn't contain.
 */
export async function loadShell(fragments = []) {
  const base = new URL('.', import.meta.url);
  const [shellHtml, ...fragmentHtmls] = await Promise.all([
    fetchHtml(new URL('shell.html', base)),
    ...fragments.map((name) => fetchHtml(new URL(`fragments/${name}.html`, base))),
  ]);

  const mount = document.getElementById('mapsShellMount');
  if (!mount) {
    throw new Error('Shell mount point #mapsShellMount not found in page');
  }
  mount.outerHTML = shellHtml;

  fragmentHtmls.forEach((html, i) => {
    const template = document.createElement('template');
    template.innerHTML = html;
    // Copy the child list first — appending moves nodes out of the template content.
    for (const element of [...template.content.children]) {
      const selector = element.getAttribute('data-shell-target');
      const target = selector ? document.querySelector(selector) : null;
      if (!target) {
        throw new Error(`Shell fragment '${fragments[i]}' targets '${selector}', not found in shell`);
      }
      element.removeAttribute('data-shell-target');
      target.appendChild(element);
    }
  });
}
