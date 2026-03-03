const MOBILE_BREAKPOINT = 980;

const pageRoot = document.querySelector('.maps-page');
const toggleButton = document.getElementById('mapsSidebarToggle');
const sidebarPanel = document.getElementById('mapsLeftPanel');
const overlayButton = document.getElementById('mapsSidebarOverlay');
const electionList = document.getElementById('mapsElectionList');

if (pageRoot && toggleButton && sidebarPanel && overlayButton && electionList) {
  const mediaQuery = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`);

  function isMobileView() {
    return mediaQuery.matches;
  }

  function setExpanded(expanded) {
    pageRoot.classList.toggle('maps-mobile-sidebar-open', expanded && isMobileView());
    toggleButton.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    overlayButton.hidden = !expanded;
  }

  function closeSidebar() {
    setExpanded(false);
  }

  function openSidebar() {
    if (!isMobileView()) return;
    setExpanded(true);
  }

  toggleButton.addEventListener('click', () => {
    const isOpen = pageRoot.classList.contains('maps-mobile-sidebar-open');
    if (isOpen) {
      closeSidebar();
      return;
    }
    openSidebar();
  });

  overlayButton.addEventListener('click', closeSidebar);

  electionList.addEventListener('click', (event) => {
    const clickedElection = event.target.closest('button, a');
    if (!clickedElection) return;
    closeSidebar();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeSidebar();
  });

  mediaQuery.addEventListener('change', () => {
    if (!isMobileView()) closeSidebar();
  });
}
