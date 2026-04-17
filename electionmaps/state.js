// Shared mutable state for the electionmaps application.
// All modules import this object and mutate its properties directly.
// A single shared object reference means every importer sees the same state.

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
  currentManifest: null,
  manifestPartiesByKey: {},
  manifestPartiesById: new Map(),
  manifestRegionsById: new Map(),
  manifestRegionsByMapId: {},
  mapModesById: {},
  parliamentFeaturesConfig: {},
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
