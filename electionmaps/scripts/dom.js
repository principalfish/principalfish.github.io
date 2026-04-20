import { manifest, _state, state, getPredictAnchorElectionId, getParlFeatures } from './state.js';

const electionList = document.getElementById('mapsElectionList');
const mapsTitle = document.querySelector('.maps-title');

/**
 * Updates the left panel: highlights the active parliament tab and rebuilds the election list nav.
 * @param {{ onPredict?: function, onPollTracker?: function }} callbacks - Click handlers for mode buttons.
 * @returns {void}
 */
export function updateLeftBar({ onPredict, onPollTracker } = {}) {
  updateParliamentTabsUI();
  renderElectionLinks(onPredict, onPollTracker);
}

function updateParliamentTabsUI() {
  document.querySelectorAll('[data-parliament]').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.parliament === state.currentParliament);
  });
  if (mapsTitle && state.currentParliament) {
    const label = state.currentParliament[0].toUpperCase() + state.currentParliament.slice(1);
    mapsTitle.textContent = `UK Election Maps · ${label}`;
  }
}

/**
 * Rebuilds the election list nav, inserting Predict and Poll tracker buttons after the
 * current-prediction entry (or near the top as a fallback). Stores references to
 * _state.predictModeLinkEl and _state.pollTrackerModeLinkEl.
 * @param {function|undefined} onPredict - Click handler for the Predict button.
 * @param {function|undefined} onPollTracker - Click handler for the Poll tracker button.
 * @returns {void}
 */
function renderElectionLinks(onPredict, onPollTracker) {
  const activeId = _state.pollTrackerModeActive ? null : state.currentElection?.id;
  if (!electionList) return;

  const createPredictButton = () => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'maps-election-item';
    btn.textContent = state.currentParliament === 'holyrood' ? 'Predict 2026' : 'Predict 2029';
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

  const parliamentElections = manifest.elections.filter((e) => e.parliament === state.currentParliament);
  const features = getParlFeatures();
  const hasPredictMode = features?.includes('predict') ?? false;
  const hasPollTracker = features?.includes('pollTracker') ?? false;
  const predictAnchorId = getPredictAnchorElectionId() ?? null;

  electionList.innerHTML = '';
  _state.predictModeLinkEl = null;
  _state.pollTrackerModeLinkEl = null;
  let insertedPredictLink = false;
  let insertedPollTrackerLink = false;
  parliamentElections.forEach((election) => {
    const link = document.createElement('a');
    link.href = `?view=election&election=${encodeURIComponent(election.id)}&parliament=${state.currentParliament}`;
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
