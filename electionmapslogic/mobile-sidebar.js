const MOBILE_BREAKPOINT = 980;

const pageRoot = document.querySelector('.maps-page');
const toggleButton = document.getElementById('mapsSidebarToggle');
const sidebarPanel = document.getElementById('mapsLeftPanel');
const overlayButton = document.getElementById('mapsSidebarOverlay');
const electionList = document.getElementById('mapsElectionList');

if (pageRoot && toggleButton && sidebarPanel && overlayButton && electionList) {
  const mediaQuery = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`);

  /**
   * Returns whether the viewport is currently at or below the mobile breakpoint.
   *
   * @returns {boolean} `true` when the media query `(max-width: 980px)` matches.
   */
  function isMobileView() {
    return mediaQuery.matches;
  }

  /**
   * Sets the sidebar open/close state.
   *
   * Toggles `maps-mobile-sidebar-open` on the page root (only applied when in
   * mobile view), updates the toggle button's `aria-expanded` attribute, and
   * shows or hides the overlay element.
   *
   * @param {boolean} expanded - `true` to open the sidebar, `false` to close it.
   */
  function setExpanded(expanded) {
    pageRoot.classList.toggle('maps-mobile-sidebar-open', expanded && isMobileView());
    toggleButton.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    overlayButton.hidden = !expanded;
  }

  /**
   * Collapses the sidebar unconditionally.
   *
   * Delegates to `setExpanded(false)`, which removes the open CSS class,
   * resets `aria-expanded`, and hides the overlay.
   */
  function closeSidebar() {
    setExpanded(false);
  }

  /**
   * Opens the sidebar, but only when the viewport is at the mobile breakpoint.
   *
   * Has no effect on wider viewports; the sidebar is always visible there via CSS
   * and does not need programmatic expansion.
   */
  function openSidebar() {
    if (!isMobileView()) return;
    setExpanded(true);
  }

  /**
   * Toggle-button click handler.
   *
   * Event: `click` on `#mapsSidebarToggle`.
   * Reads: presence of `maps-mobile-sidebar-open` on `.maps-page` to determine
   *   current state.
   * Side effects: calls `closeSidebar()` or `openSidebar()` depending on state.
   */
  toggleButton.addEventListener('click', () => {
    const isOpen = pageRoot.classList.contains('maps-mobile-sidebar-open');
    if (isOpen) {
      closeSidebar();
      return;
    }
    openSidebar();
  });

  /**
   * Overlay click handler.
   *
   * Event: `click` on `#mapsSidebarOverlay`.
   * Side effects: closes the sidebar (same as pressing the toggle button when open).
   */
  overlayButton.addEventListener('click', closeSidebar);

  /**
   * Election-list click handler.
   *
   * Event: `click` (bubbled) on `#mapsElectionList`.
   * Reads: traverses upward from `event.target` via `closest('button, a')` to
   *   confirm the click originated on an interactive election item.
   * Side effects: closes the sidebar when an election button or link is activated,
   *   so the map is visible immediately after selection on mobile.
   */
  electionList.addEventListener('click', (event) => {
    const clickedElection = event.target.closest('button, a');
    if (!clickedElection) return;
    closeSidebar();
  });

  /**
   * Global keyboard handler.
   *
   * Event: `keydown` on `document`.
   * Reads: `event.key` to detect the Escape key.
   * Side effects: closes the sidebar when Escape is pressed, providing keyboard
   *   accessibility for dismissing the mobile overlay.
   */
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeSidebar();
  });

  /**
   * Viewport breakpoint change handler.
   *
   * Event: `change` on the `(max-width: 980px)` MediaQueryList.
   * Reads: `isMobileView()` to check whether the viewport has moved to desktop width.
   * Side effects: closes the sidebar automatically when the user resizes out of the
   *   mobile breakpoint, preventing the open-sidebar state from persisting on desktop.
   */
  mediaQuery.addEventListener('change', () => {
    if (!isMobileView()) closeSidebar();
  });
}
