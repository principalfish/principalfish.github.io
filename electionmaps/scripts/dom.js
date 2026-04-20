import { manifest, _state } from './state.js';

const electionList = document.getElementById('mapsElectionList');
const mapsTitle = document.querySelector('.maps-title');

/**
 * Updates the left panel: highlights the active parliament tab and rebuilds the election list nav.
 * @param {string|null} activeId - ID of the currently active election, or null for no active item.
 * @param {{ onPredict?: function, onPollTracker?: function }} callbacks - Click handlers for mode buttons.
 * @returns {void}
 */
export function updateLeft(activeId, { onPredict, onPollTracker } = {}) {
  updateParliamentTabsUI();
  renderElectionLinks(activeId, onPredict, onPollTracker);
}

function updateParliamentTabsUI() {
  document.querySelectorAll('[data-parliament]').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.parliament === _state.currentParliament);
  });
  if (mapsTitle && _state.currentParliament) {
    const label = _state.currentParliament[0].toUpperCase() + _state.currentParliament.slice(1);
    mapsTitle.textContent = `UK Election Maps · ${label}`;
  }
}

/**
 * Rebuilds the election list nav, inserting Predict and Poll tracker buttons after the
 * current-prediction entry (or near the top as a fallback). Stores references to
 * _state.predictModeLinkEl and _state.pollTrackerModeLinkEl.
 * @param {string|null} activeId - ID of the currently active election, or null for no active item.
 * @param {function|undefined} onPredict - Click handler for the Predict button.
 * @param {function|undefined} onPollTracker - Click handler for the Poll tracker button.
 * @returns {void}
 */
function renderElectionLinks(activeId, onPredict, onPollTracker) {
  if (!electionList) return;

  const createPredictButton = () => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'maps-election-item';
    btn.textContent = _state.currentParliament === 'holyrood' ? 'Predict 2026' : 'Predict 2029';
    if (onPredict) btn.addEventListener('click', () => onPredict().catch(console.error));
    return btn;
  };

  const createPollTrackerButton = () => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'maps-election-item';
    btn.textContent = 'Poll tracker';
    if (onPollTracker) btn.addEventListener('click', () => onPollTracker().catch(console.error));
    return btn;
  };

  const parliamentElections = manifest.elections.filter((e) => e.parliament === _state.currentParliament);
  const parlConfig = manifest.parliamentFeatures[_state.currentParliament] ?? {};
  const hasPredictMode = parlConfig.features?.includes('predict') ?? false;
  const hasPollTracker = parlConfig.features?.includes('pollTracker') ?? false;
  const predictAnchorId = parlConfig.predictAnchorElectionId ?? null;

  electionList.innerHTML = '';
  _state.predictModeLinkEl = null;
  _state.pollTrackerModeLinkEl = null;
  let insertedPredictLink = false;
  let insertedPollTrackerLink = false;
  parliamentElections.forEach((election) => {
    const link = document.createElement('a');
    link.href = `?view=election&election=${encodeURIComponent(election.id)}&parliament=${_state.currentParliament}`;
    link.className = `maps-election-item${election.id === activeId ? ' active' : ''}`;
    link.textContent = election.name;
    electionList.appendChild(link);

    if (hasPredictMode && !insertedPredictLink && election.id === predictAnchorId) {
      const predictButton = createPredictButton();
      electionList.appendChild(predictButton);
      _state.predictModeLinkEl = predictButton;
      insertedPredictLink = true;
    }

    if (hasPollTracker && insertedPredictLink && !insertedPollTrackerLink && election.id !== predictAnchorId) {
      const trackerButton = createPollTrackerButton();
      electionList.appendChild(trackerButton);
      _state.pollTrackerModeLinkEl = trackerButton;
      insertedPollTrackerLink = true;
    }
  });

  if (hasPredictMode && !insertedPredictLink) {
    const predictButton = createPredictButton();
    if (electionList.children.length > 0) {
      electionList.insertBefore(predictButton, electionList.children[1] || null);
    } else {
      electionList.appendChild(predictButton);
    }
    _state.predictModeLinkEl = predictButton;

    if (hasPollTracker && !insertedPollTrackerLink) {
      const trackerButton = createPollTrackerButton();
      const predictIndex = Array.from(electionList.children).indexOf(predictButton);
      if (predictIndex >= 0 && electionList.children[predictIndex + 1]) {
        electionList.insertBefore(trackerButton, electionList.children[predictIndex + 1]);
      } else {
        electionList.appendChild(trackerButton);
      }
      _state.pollTrackerModeLinkEl = trackerButton;
      insertedPollTrackerLink = true;
    }
  }

  if (hasPollTracker && !insertedPollTrackerLink) {
    const trackerButton = createPollTrackerButton();
    if (_state.predictModeLinkEl && _state.predictModeLinkEl.nextSibling) {
      electionList.insertBefore(trackerButton, _state.predictModeLinkEl.nextSibling);
    } else {
      electionList.appendChild(trackerButton);
    }
    _state.pollTrackerModeLinkEl = trackerButton;
  }
}
