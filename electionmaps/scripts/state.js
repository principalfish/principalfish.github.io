// Shared mutable state for the electionmaps application.
// All modules import this object and mutate its properties directly.
// A single shared object reference means every importer sees the same state.

import { normalizeRegionKey } from './utils.js';

export let manifest = null;

/**
 * Sets the manifest, normalises missing top-level fields, and hydrates all
 * party and region lookup maps on state. The only way to reassign the exported binding.
 * @param {object} m - Raw manifest object from map-modes.json.
 * @returns {void}
 */
export function setManifest(m) {
  manifest = m;
  hydrateManifestSettings();
}

/**
 * Normalises missing manifest fields and populates party and region lookup maps
 * from the manifest's top-level `parties` array and per-map `regions` in `mapModes`.
 * @returns {void}
 */
function hydrateManifestSettings() {
  manifest.mapModes ??= {};
  manifest.parliamentFeatures ??= {};
  manifest.parties ??= [];
  manifest.files ??= {};
  manifest.files.mapsById ??= {};
  manifest.files.electionsById ??= {};

  // manifest.partiesByKey — plain object keyed by party.key string (e.g. "labour").
  // Used for display lookups: name and colour given a key already known from seat data.
  // manifest.partiesById — Map keyed by numeric party.id from the DB.
  // Used during data normalisation to resolve raw [partyId, votes] pairs into party keys.
  manifest.partiesByKey = {};
  manifest.partiesById = new Map();
  manifest.parties.forEach((party) => {
    const id = Number(party?.id);
    if (!Number.isFinite(id)) return;
    manifest.partiesById.set(id, party);
    const key = party?.key;
    if (key && !manifest.partiesByKey[key]) manifest.partiesByKey[key] = party;
  });

  // manifest.regionsById — Map keyed by numeric region.id.
  // Used during seat normalisation to resolve a region ID to its normalised key string.
  // manifest.regionsByMapId — plain object keyed by mapId string.
  // Used to build per-election region label lookups for the filter UI.
  manifest.regionsById = new Map();
  manifest.regionsByMapId = {};
  Object.entries(manifest.mapModes).forEach(([mapId, mapMode]) => {
    const regionRows = mapMode.regions || [];
    manifest.regionsByMapId[mapId] = regionRows;
    regionRows.forEach((region) => {
      const id = Number(region?.id);
      if (!Number.isFinite(id)) return;
      manifest.regionsById.set(id, normalizeRegionKey(region?.name || ''));
    });
  });
}

export const state = {
  // Sort / UI / totals
  currentSort: { key: 'seats', direction: 'desc' },
  voteTotalsExpanded: false,
  voteTotalsMode: 'all',
  hiddenVoteTotalsParties: new Set(),
  currentSeatView: 'seats',
  selectedSeatRow: null,
  activeSeatPathNode: null,
  currentOpenSeatName: null,

  // Manifest
  currentParliament: '',

  // Election / seat data
  currentSeats: [],
  currentComparisonSeats: [],
  baseElectionSeats: [],
  defaultComparisonSeats: [],
  defaultComparisonSummary: null,
  currentSeatsByKey: new Map(),
  comparisonSeatsByKey: new Map(),
  currentSeatNameByKey: new Map(),
  seatListRowByKey: new Map(),
  currentRegionLabelsByKey: new Map(),
  currentElectionType: null,
  currentElectionId: null,
  currentByElectionSeats: null,
  currentMapData: null,

  // Map filters / choropleth
  mapViewState: {
    filterParty: 'all',
    filterRegion: 'all',
    filterSecondParty: 'all',
    majorityMin: 0,
    majorityMax: 100,
    gainsOnly: false,
    choroplethType: 'none',
    choroplethParty: 'all',
  },

  // Map interaction controller — replaced by renderTopoMap
  mapInteractionController: {
    zoomBy: () => {},
    reset: () => {},
    clearSelection: () => {},
    highlightSeat: () => {},
    zoomToSeat: () => false,
    flashRegion: () => {},
  },

  // Search
  seatSearchNames: [],
  seatSearchSuggestions: [],
  seatSearchSuggestionIndex: -1,
  seatSearchMenuEl: null,
  postcodeErrorTimeout: null,

  // Predict mode
  predictModeActive: false,
  predictModeLinkEl: null,
  predictBaseSeats: [],
  predictBaseSeatsByKey: new Map(),
  predictBaseMapData: null,
  predictBaseRegionLabelsByKey: new Map(),
  predictColumnPartyKeys: [],
  predictInputByRegionParty: new Map(),
  predictBaselineShareByRegionParty: new Map(),
  predictRegionalSwingsByParty: new Map(),
  predictEnglandExpanded: false,
  predictOtherCellByRegion: new Map(),
  predictHolyroodTab: 'constituency',
  predictHolyroodRegionsExpanded: false,
  predictConstInputByRegionParty: new Map(),
  predictListInputByRegionParty: new Map(),
  predictNationalBaselines: new Map(),
  predictNationalListBaselines: new Map(),
  predictBaselineConstShareByRegionParty: new Map(),
  predictBaselineListShareByRegionParty: new Map(),
  predictHolyroodConstSwingsByParty: new Map(),
  predictHolyroodListSwingsByParty: new Map(),
  predictCurrentSimulationLoaded: false,
  predictCurrentSimulationSeats: [],
  predictCurrentSimulationConstShares: new Map(),
  predictCurrentSimulationListShares: new Map(),

  // Poll tracker
  pollTrackerModeActive: false,
  pollTrackerModeLinkEl: null,
  pollTrackerDataLoaded: false,
  pollTrackerTimeline: [],
  pollTrackerSeriesByParty: new Map(),
  pollTrackerRangeSelection: 'all',
  pollTrackerMetaLoaded: false,
  pollTrackerLatestSnippet: '',
  holyroodPredictionSnippet: '',

  // Misc
  countdownIntervalId: null,
  lastTrackedVirtualPagePath: '',
};
