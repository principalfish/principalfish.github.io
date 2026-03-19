/**
 * Google Analytics runtime gating IIFE.
 *
 * Injects the GA4 gtag script and configures the measurement ID `G-DF15MKHP0V`
 * only when running on a non-development hostname. No tracking occurs during
 * local/file-protocol sessions.
 *
 * Reads:
 *   - `window.location.protocol` — skips setup when equal to `'file:'`.
 *   - `window.location.hostname` — skips setup for `localhost`, `127.0.0.1`,
 *     `0.0.0.0`, `[::1]`, and any `.local` hostname.
 *   - `window.location.pathname` — used to detect the `/electionmaps` page.
 *   - `window.__gaDisableAutoPageView` — external flag that pages can set to
 *     suppress the automatic page-view event.
 *
 * Side effects (non-dev hosts only):
 *   - Appends an async `<script>` tag for `googletagmanager.com/gtag/js` to
 *     `document.head`.
 *   - Initialises `window.dataLayer` and `window.gtag`.
 *   - Calls `gtag('config', ...)` with `send_page_view: false` on the
 *     `/electionmaps` page (or when `window.__gaDisableAutoPageView` is set),
 *     so that page-view events can be fired manually at the right moment.
 */
// Google Analytics runtime gating for dev/localhost
(function () {
  const hostname = window.location.hostname;
  const isDevelopmentHost =
    window.location.protocol === 'file:' ||
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '0.0.0.0' ||
    hostname === '[::1]' ||
    hostname.endsWith('.local');

  if (isDevelopmentHost) return;

  const script = document.createElement('script');
  script.async = true;
  script.src = 'https://www.googletagmanager.com/gtag/js?id=G-DF15MKHP0V';
  document.head.appendChild(script);

  window.dataLayer = window.dataLayer || [];
  window.gtag = function () { window.dataLayer.push(arguments); };
  window.gtag('js', new Date());
  const pathname = String(window.location.pathname || '').toLowerCase();
  const isElectionMapsPage =
    pathname === '/electionmaps'
    || pathname === '/electionmaps/'
    || pathname.endsWith('/electionmaps/index.html');
  const disableAutoPageView = window.__gaDisableAutoPageView === true || isElectionMapsPage;
  const configOptions = disableAutoPageView
    ? { cookie_domain: 'auto', send_page_view: false }
    : { cookie_domain: 'auto' };
  window.gtag('config', 'G-DF15MKHP0V', configOptions);
})();
