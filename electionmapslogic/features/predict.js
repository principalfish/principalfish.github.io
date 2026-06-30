// ─── Predict engine ──────────────────────────────────────────────────────────
//
// The prediction engine: the projection math (baseline shares, uniform swing, D'Hondt)
// and the per-parliament PredictModel classes. Deliberately DOM-free — it reads the
// baseline from the `state` singleton and is otherwise pure, so it can be unit-tested in
// a plain Node environment. The predict-view glue (action handlers, view activation) lives
// in predict-controller.js, which imports from here.
//
// Predict mode lets the user enter per-region per-party vote shares, computes regional
// uniform-swing deltas vs. a baseline election, and projects a synthetic future result.
// The projected seats are wrapped in an ElectionData and assigned to state.electionData
// so the regular render pipeline (map / seat list / vote totals / subtitle) consumes them
// unchanged. The original baseline ElectionData becomes state.comparisonElectionData so
// gains-from-baseline and the comparison column work without further wiring.

import { state, manifest, Seat } from '../state.js';
import { normalizeRegionKey, roundShare, base64urlEncode, base64urlDecode } from '../utils.js';

/**
 * Computes baseline regional vote share percentages from a seat array. For each party in
 * `modelledPartyKeys` and each distinct seat region, returns share = (regional party votes
 * / regional turnout) × 100. When `aggregateConfig` is supplied, regions matching its
 * `isMember` predicate also contribute to a synthetic aggregate row keyed by `aggregateConfig.key`
 * (e.g. 'england' for Westminster, 'scotland' for Holyrood). Rounded to integers; if rounding
 * pushes a region's total above 100 the smallest non-zero party is decremented to compensate.
 * @param {Seat[]} seats
 * @param {string[]} modelledPartyKeys - Parties to compute shares for (others are ignored).
 * @param {{key: string, isMember: (regionKey: string) => boolean} | null} [aggregateConfig]
 * @returns {Map<string, Map<string, number>>} regionKey → partyKey → share (0–100).
 */
export function buildBaselineShares(seats, modelledPartyKeys, aggregateConfig = null) {
  // ── Phase 1: accumulate raw votes + turnout per region ──
  // byRegion maps regionKey → { totalVotes (the share denominator), votesByParty (numerators) }.
  // ensureRegion lazily creates a zeroed accumulator the first time a region is seen.
  const byRegion = new Map();
  const ensureRegion = (regionKey) => {
    if (!byRegion.has(regionKey)) byRegion.set(regionKey, { totalVotes: 0, votesByParty: new Map() });
    return byRegion.get(regionKey);
  };

  (seats || []).forEach((seat) => {
    const regionKey = normalizeRegionKey(seat.region);
    if (!regionKey) return;
    const turnout = Number(seat.turnout || 0);
    // Skip empty seats: they contribute nothing and would risk a divide-by-zero in phase 2.
    if (turnout <= 0) return;

    // Credit this seat's turnout + per-party votes to its own region. Only modelledPartyKeys are
    // tracked here; every other party is implicitly folded into the "other" remainder downstream.
    const region = ensureRegion(regionKey);
    region.totalVotes += turnout;
    modelledPartyKeys.forEach((partyKey) => {
      const partyVotes = Number(seat.votes?.[partyKey] || 0);
      region.votesByParty.set(partyKey, Number(region.votesByParty.get(partyKey) || 0) + partyVotes);
    });

    // If this region feeds a synthetic aggregate (e.g. an English region → 'england'), credit the
    // same turnout + votes a second time under the aggregate key, so the aggregate row ends up as
    // the sum of its members. The seat legitimately appears in both its own row and the aggregate.
    if (aggregateConfig && aggregateConfig.isMember(regionKey)) {
      const agg = ensureRegion(aggregateConfig.key);
      agg.totalVotes += turnout;
      modelledPartyKeys.forEach((partyKey) => {
        const partyVotes = Number(seat.votes?.[partyKey] || 0);
        agg.votesByParty.set(partyKey, Number(agg.votesByParty.get(partyKey) || 0) + partyVotes);
      });
    }
  });

  // ── Phase 2: convert each region's accumulated votes into rounded integer percentage shares ──
  const result = new Map();
  byRegion.forEach((stats, regionKey) => {
    const partyMap = new Map();
    modelledPartyKeys.forEach((partyKey) => {
      const votes = Number(stats.votesByParty.get(partyKey) || 0);
      // share = party votes / regional turnout × 100. The totalVotes>0 guard is defensive — phase 1
      // only accumulates when turnout>0 — but keeps the division safe regardless.
      const share = stats.totalVotes > 0 ? (votes / stats.totalVotes) * 100 : 0;
      partyMap.set(partyKey, roundShare(share)); // roundShare clamps to [0,100] and rounds to integer
    });

    // Independently rounding each party can push the integer total above 100 (e.g. 33.6×3 → 102).
    // Absorb the overshoot by trimming the smallest non-zero party — least relative distortion, and
    // never touches a zero party (which would otherwise go negative). Under-100 totals are left
    // alone: the shortfall is the implicit "other" share.
    let sum = 0;
    partyMap.forEach((v) => { sum += v; });
    // Trim the overshoot off the smallest non-zero party first; if that party can't absorb it
    // all (the overshoot exceeds its share), zero it and continue onto the next-smallest, so
    // the corrected total always lands at 100 even when many small parties each rounded up.
    let overshoot = sum - 100;
    while (overshoot > 0) {
      let minKey = null;
      let minVal = Infinity;
      partyMap.forEach((v, k) => { if (v > 0 && v < minVal) { minVal = v; minKey = k; } });
      if (!minKey) break;
      const reduced = Math.max(0, minVal - overshoot);
      partyMap.set(minKey, reduced);
      overshoot -= minVal - reduced;
    }
    result.set(regionKey, partyMap);
  });

  return result;
}

/**
 * Projects a single seat by applying regional swings to baseline vote shares. Modelled
 * party shares are adjusted by their swing; any remaining share is redistributed pro rata
 * to non-modelled parties (or assigned to 'others' if none exist). Returns a new Seat with
 * updated votes, preserved turnout, and recomputed winner.
 * @param {Seat} baseSeat
 * @param {Map<string, Map<string, number>>} swingsByRegionByParty - regionKey → partyKey → swing pp.
 * @param {string[]} modelledPartyKeys - Parties whose share is adjusted by swing (rest absorb residue).
 * @param {{key: string, isMember: (regionKey: string) => boolean} | null} [aggregateConfig]
 *   When set, sub-regions whose own swing is unset *or zero* fall back to the aggregate's swing.
 * @returns {Seat}
 */
export function projectSeatUniformSwing(baseSeat, swingsByRegionByParty, modelledPartyKeys, aggregateConfig = null) {
  const totalVotes = baseSeat.turnout;
  if (totalVotes <= 0) return new Seat(baseSeat);

  const regionKey = normalizeRegionKey(baseSeat.region);
  if (!regionKey) return new Seat(baseSeat);
  const baseVotes = baseSeat.votes || {};

  // Resolve the swing for a party: prefer the seat's own region, fall back to the
  // configured aggregate (e.g. 'england', 'scotland') for sub-regions when the region's
  // own swing is zero or absent.
  const resolveSwing = (partyKey) => {
    const direct = Number(swingsByRegionByParty.get(regionKey)?.get(partyKey) || 0);
    if (Math.abs(direct) > 1e-9) return direct;
    if (aggregateConfig && aggregateConfig.isMember(regionKey)) {
      return Number(swingsByRegionByParty.get(aggregateConfig.key)?.get(partyKey) || 0);
    }
    return 0;
  };

  let adjustedTrackedSum = 0;
  const adjustedTrackedShare = new Map();
  modelledPartyKeys.forEach((partyKey) => {
    const baseShare = Seat.voteSharePct(baseSeat, partyKey);
    const adjusted = Math.max(0, baseShare + resolveSwing(partyKey));
    adjustedTrackedShare.set(partyKey, adjusted);
    adjustedTrackedSum += adjusted;
  });

  const otherShare = Math.max(0, 100 - adjustedTrackedSum);
  const projected = {};
  adjustedTrackedShare.forEach((share, partyKey) => {
    if (share <= 0) return;
    projected[partyKey] = (share / 100) * totalVotes;
  });

  const nonTracked = Object.entries(baseVotes).filter(([k]) => !modelledPartyKeys.includes(k));
  const nonTrackedTotal = nonTracked.reduce((sum, [, v]) => sum + Number(v || 0), 0);
  if (otherShare > 0) {
    if (nonTrackedTotal > 0) {
      nonTracked.forEach(([partyKey, votes]) => {
        const weight = Number(votes || 0) / nonTrackedTotal;
        projected[partyKey] = ((otherShare * weight) / 100) * totalVotes;
      });
    } else {
      projected.others = (otherShare / 100) * totalVotes;
    }
  }

  // Re-scale so projected votes sum to baseSeat.turnout (rounding inside the share math
  // can drift by a fraction of a vote).
  const projectedSum = Object.values(projected).reduce((s, v) => s + Number(v || 0), 0);
  if (projectedSum > 0 && Math.abs(projectedSum - totalVotes) > 1e-6) {
    const scale = totalVotes / projectedSum;
    Object.keys(projected).forEach((k) => { projected[k] = projected[k] * scale; });
  }

  const winner = Object.entries(projected)
    .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))[0]?.[0] || baseSeat.winner || 'others';

  return new Seat({ ...baseSeat, votes: projected, turnout: totalVotes, winner });
}

/**
 * Runs D'Hondt seat allocation for one AMS region, deducting constituency wins from
 * the divisor so list seats top up under-represented parties.
 * @param {Map<string, number>} votesByParty
 * @param {number} nSeats
 * @param {Map<string, number>} constWinsByParty
 * @returns {string[]} Ordered array of winning party keys — normally one per seat, but
 *   shorter than nSeats if fewer parties have positive votes than there are seats to fill.
 */
export function dhondtAllocate(votesByParty, nSeats, constWinsByParty = new Map()) {
  const listSeatsWon = new Map();
  for (const key of votesByParty.keys()) listSeatsWon.set(key, 0);
  const winners = [];
  for (let i = 0; i < nSeats; i++) {
    let bestParty = null;
    let bestQuotient = -Infinity;
    for (const [party, votes] of votesByParty) {
      if (votes <= 0) continue;
      const total = (listSeatsWon.get(party) || 0) + (constWinsByParty.get(party) || 0);
      const quotient = votes / (total + 1);
      if (quotient > bestQuotient) { bestQuotient = quotient; bestParty = party; }
    }
    if (bestParty !== null) {
      listSeatsWon.set(bestParty, (listSeatsWon.get(bestParty) || 0) + 1);
      winners.push(bestParty);
    }
  }
  return winners;
}

/**
 * Filters a seat array to one list seat per region. Each list seat in the source data
 * duplicates the full regional list total, so deduping by region is the right input for
 * `buildBaselineShares` and "use current forecast" share loading.
 * @param {Seat[]} seats
 * @returns {Seat[]}
 */
function dedupeListSeatsByRegion(seats) {
  const seen = new Set();
  return seats.filter((s) => {
    if (!Seat.isList(s)) return false;
    // Dedupe on the normalised region key — the same key buildBaselineShares and
    // #allocateListSeats bucket by — so a region's list seats collapse to one row even when
    // their raw region strings differ in case/punctuation/whitespace.
    const rk = normalizeRegionKey(s.region);
    if (seen.has(rk)) return false;
    seen.add(rk);
    return true;
  });
}

/**
 * One ballot's worth of user input + matching baseline. FPTP models have one ballot;
 * AMS models have two (constituency + list). Encapsulating them lets the base class
 * iterate `this.ballots` for serialize / deserialize / reset / loadSimulationShares
 * without branching on the model's electoral system.
 *
 * Fields:
 * - `key` — ballot identifier, matched against `AMSPredict.activeTab` to pick the
 *   active ballot. Single-ballot models can leave this null.
 * - `input` — user-input map `regionKey → partyKey → share`. Mutated in place; the
 *   reference is stable across `reset()` / `loadSimulationShares()`.
 * - `baseline` — paired baseline map; same shape as input but read-only.
 * - `serializePrefix` — `null` for single-ballot models (entry shape `[r, p, v]`);
 *   `'c'` / `'l'` for AMS (entry shape `[prefix, r, p, v]` so deserialize can route).
 * - `filterSeats` — picks the simulation-seat subset relevant to this ballot when
 *   loading "current forecast". FPTP passes seats through; AMS filters to const or
 *   deduped-list seats.
 */
class Ballot {
  constructor({ key = null, input, baseline, serializePrefix = null, filterSeats = (seats) => seats } = {}) {
    this.key = key;
    this.input = input;
    this.baseline = baseline;
    this.serializePrefix = serializePrefix;
    this.filterSeats = filterSeats;
  }
}

/**
 * Abstract base for predict models. Owns the baseline election, the user-input share maps
 * (one per ballot — see `Ballot`), and the projection entry point. Reads/writes go through
 * the currently active ballot, which `activeBallot()` exposes — FPTP has a single ballot,
 * AMS swaps between constituency and list ballots as the active tab changes.
 *
 * Subclasses implement parliament-specific row/column structure (regions, parties,
 * gridSections), the projection algorithm (`project`), and the ballot list. Everything
 * else — share reads/writes, swing computation, validation, serialize/deserialize, the
 * "load current forecast" flow — is shared.
 */
class PredictModel {
  /** Memoised region layout. `#sectionCache` maps a normalised regionKey to its grid section
   * — a pure function of the immutable grid config, so it never needs invalidating.
   * `#regionsCache` holds the last `regions()` result, keyed by `#regionsCacheKey` (the
   * current `aggregateExpanded`, the only input that changes during a model's life) so the
   * repeated buildSwings / validate / deserialize / render passes don't rebuild it. */
  #sectionCache = new Map();
  #regionsCache = null;
  #regionsCacheKey = null;

  /**
   * Reads the baseline election, map topology, and region-label lookup from module-level
   * `state` rather than holding its own references. Callers MUST populate
   * `state.comparisonElectionData`, `state.mapData`, and `state.currentRegionLabelsByKey`
   * before constructing the model — the constructor's call to `buildBaselineShares` (in
   * subclasses) reads `this.baseSeats`, which resolves through `this.baselineElectionData`
   * → `state.comparisonElectionData.currentSeats`.
   *
   * @param {number|undefined} nextElectionYear - Display year (e.g. 2029) for "Predict YYYY" labels.
   * @param {object} config - The `predict` block from manifest.parliamentFeatures. Recognised keys:
   *   - `model` {'fptp'|'ams'} — class selector read by `predictModelClassFor` before
   *     construction; the model class itself does not consume this field.
   *   - `title` {string} — heading shown above the predict input grid (e.g. 'User Input
   *     (uniform swing)'). Read by dom.js via `model.title`; defaults to 'User Input'.
   *   - `modelledPartyKeys` {string[]} — parties carried through `buildBaselineShares` and
   *     `projectSeatUniformSwing`. Any party not in this list is folded into 'other'.
   *   - `aggregate` {{key: string, label?: string, excludeRegions?: string[]} | null} — synthetic
   *     aggregate-row config (see `#buildAggregateConfig`). Omit / null = no aggregate row.
   *   - `gridSections` {Array<SectionConfig>} — declarative grid layout. Each section has:
   *     `id`, `columnKeys`, optional `containsAggregate` (the section that renders the
   *     aggregate row + its member sub-rows when expanded), optional `extraRegionKeys`
   *     (non-aggregate regions that still belong here, e.g. Westminster's Scotland and
   *     Wales sit in GB), optional `regionKeys` (explicit non-aggregate regions, e.g.
   *     Westminster's NI), optional `blankRegionHeader` (rendering hint for dom.js).
   *   - `virtualColumns` {Object<string, Object<string, string>>} — maps a virtual column
   *     key (e.g. `'nat'`) to a per-region party lookup (e.g. `{ scotland: 'snp' }`).
   *     Resolved by `resolveColumnPartyKey`; regions without an entry produce a blank
   *     column (the virtual key is dropped from `parties(rk)`).
   *   - `virtualColumnMeta` {Object<string, {title?: string, swatchClass?: string}>} —
   *     presentation metadata for virtual columns, read by dom.js via `columnMeta` to render
   *     the header swatch + tooltip (e.g. `{ nat: { title: 'NAT (...)', swatchClass: '...' } }`).
   *   - `regionLabelOverrides` {Object<string, string>} — normalised regionKey → short
   *     display label for the predict grid (e.g. `{ northernireland: 'N Ireland' }`).
   *     Region rows fall back to their map label when no override is present.
   *   - `tabs` {Array<{key: string, label: string}>} — AMS-only ballot tabs (typically
   *     constituency + list). First entry's `key` is the initial `activeTab`.
   */
  constructor(nextElectionYear, config) {
    this.nextElectionYear = nextElectionYear;
    this.config = config || {};
    this.modelledPartyKeys = this.config.modelledPartyKeys || [];
    this.title = this.config.title || 'User Input';
    this.aggregateConfig = PredictModel.#buildAggregateConfig(this.config.aggregate);
    this.virtualColumns = this.config.virtualColumns || {};
    this.virtualColumnMeta = this.config.virtualColumnMeta || {};
    this.regionLabelOverrides = this.config.regionLabelOverrides || {};
    this.gridSectionsConfig = this.config.gridSections || [];
    this.aggregateExpanded = false;
    /** @type {Ballot[]} populated by subclass constructors after super(). */
    this.ballots = [];
  }

  /** @returns {ElectionData} Baseline election the projection runs against. */
  get baselineElectionData() { return state.comparisonElectionData; }

  /** @returns {object} Topology JSON for the baseline map. */
  get baselineMapData() { return state.mapData; }

  /** @returns {Map<string, string>} regionKey → display label for the baseline map. */
  get regionLabelsByKey() { return state.currentRegionLabelsByKey; }

  /**
   * Builds the aggregate-row configuration from the manifest's `predict.aggregate` block.
   *
   * The aggregate is a synthetic region that lives only inside the predict model — it
   * doesn't exist in the data files. Its baseline shares are computed by summing every
   * `isMember` region's votes/turnout in `buildBaselineShares`; its row appears in the
   * grid courtesy of `buildRowsWithAggregate`. When the user enters values into the
   * aggregate row, `projectSeatUniformSwing` falls back to that swing for any sub-region
   * whose own swing is zero, applying the user's national-level intent uniformly.
   *
   * Westminster supplies `{ key: 'england', label: 'England', excludeRegions: ['scotland',
   * 'wales', 'northernireland'] }` so only English regions feed the aggregate. Holyrood
   * supplies `{ key: 'scotland', label: 'Scotland', excludeRegions: [] }` — every Holyrood
   * electoral region is Scottish, and `'scotland'` doesn't collide with any real region
   * key in the Holyrood map, so the synthetic aggregate is safe to key as `'scotland'`.
   *
   * @param {{key: string, label?: string, excludeRegions?: string[]} | null | undefined} aggCfg
   *   The manifest block, or absent.
   * @returns {{key: string, label: string, isMember: (rk: string) => boolean} | null}
   *   The frozen config, or null when the parliament has no aggregate (the rest of the
   *   predict pipeline treats null as "skip aggregate handling entirely").
   */
  static #buildAggregateConfig(aggCfg) {
    if (!aggCfg || !aggCfg.key) return null;
    const aggKey = normalizeRegionKey(aggCfg.key);
    const exclude = new Set((aggCfg.excludeRegions || []).map((rk) => normalizeRegionKey(rk)));
    return {
      key: aggKey,
      label: aggCfg.label || aggCfg.key,
      isMember: (rk) => {
        const k = normalizeRegionKey(rk);
        if (!k) return false;
        if (k === aggKey) return false;
        return !exclude.has(k);
      },
    };
  }

  /** @returns {Seat[]} Baseline seats (read-only reference). */
  get baseSeats() { return this.baselineElectionData.currentSeats; }

  /**
   * Computes baseline shares for a seat array using this model's modelled parties +
   * aggregate config. Subclasses call this in their constructor (FPTP: once over
   * all seats; AMS: once each over the const and deduped-list slices).
   * @param {Seat[]} seats
   * @returns {Map<string, Map<string, number>>}
   */
  baselineFor(seats) {
    return buildBaselineShares(seats, this.modelledPartyKeys, this.aggregateConfig);
  }

  /**
   * Returns the manifest's grid sections walked to produce ordered region rows. For each
   * section in declared order:
   * - If the section has `containsAggregate` and the model has an aggregate config,
   *   prepends the aggregate row + (when expanded) every aggregate-member sub-row,
   *   alpha-sorted by label.
   * - Then appends each `extraRegionKeys` entry in declared order.
   * - Then appends each `regionKeys` entry in declared order.
   * Every emitted row is tagged with `section: section.id` so `gridSections()` can split
   * them into separate tables.
   * @returns {Array<object>}
   */
  regions() {
    if (this.#regionsCache && this.#regionsCacheKey === this.aggregateExpanded) return this.#regionsCache;
    const labels = this.regionLabelsByKey;
    const allByNorm = new Map();
    Array.from(labels.entries()).forEach(([rk, label]) => {
      allByNorm.set(normalizeRegionKey(rk), { regionKey: rk, regionLabel: label });
    });

    const out = [];
    this.gridSectionsConfig.forEach((section) => {
      if (section.containsAggregate && this.aggregateConfig) {
        const subs = Array.from(allByNorm.values())
          .filter((r) => this.aggregateConfig.isMember(r.regionKey))
          .sort((a, b) => a.regionLabel.localeCompare(b.regionLabel));
        out.push(...this.buildRowsWithAggregate(subs, section.id));
      }
      const explicit = [...(section.extraRegionKeys || []), ...(section.regionKeys || [])];
      explicit.forEach((rk) => {
        const found = allByNorm.get(normalizeRegionKey(rk));
        if (found) out.push({ ...found, section: section.id });
      });
    });
    const rows = out.map((r) => ({
      ...r,
      predictLabel: this.regionLabelOverrides[normalizeRegionKey(r.regionKey)] || r.regionLabel,
    }));
    this.#regionsCache = rows;
    this.#regionsCacheKey = this.aggregateExpanded;
    return rows;
  }

  /**
   * Returns the columns to render for a region: the section's `columnKeys` with virtual
   * columns resolved to per-region parties (or dropped when blank).
   * @param {string} regionKey
   * @returns {string[]}
   */
  parties(regionKey) {
    const section = this.#sectionForRegion(regionKey);
    if (!section) return [];
    return section.columnKeys
      .map((k) => this.resolveColumnPartyKey(regionKey, k))
      .filter((k) => k !== null && k !== undefined);
  }

  /**
   * Resolves a section's column key to the actual party key for the given region. Plain
   * column keys pass through unchanged; keys present in `virtualColumns` are looked up by
   * normalised regionKey and may resolve to null (column blank for that region).
   * @param {string} regionKey
   * @param {string} columnKey
   * @returns {string|null}
   */
  resolveColumnPartyKey(regionKey, columnKey) {
    const virtual = this.virtualColumns[columnKey];
    if (!virtual) return columnKey;
    return virtual[normalizeRegionKey(regionKey)] || null;
  }

  /**
   * Presentation metadata for a virtual column (e.g. the 'nat' column that maps to SNP in
   * Scotland / Plaid Cymru in Wales). Returns `{ title, swatchClass }` so dom.js can render
   * a header swatch + tooltip without hardcoding a per-column branch; returns null for plain
   * party columns, which dom.js renders from `manifest.labelParty` / `colourParty` instead.
   * @param {string} columnKey
   * @returns {{title?: string, swatchClass?: string} | null}
   */
  columnMeta(columnKey) {
    return this.virtualColumnMeta[columnKey] || null;
  }

  /**
   * Builds the per-table grid sections rendered by dom.js. Driven entirely by
   * `gridSectionsConfig`; rows are partitioned from `regions()` by section id.
   * @returns {Array<object>}
   */
  gridSections() {
    const allRegions = this.regions();
    return this.gridSectionsConfig.map((section) => ({
      id: section.id,
      title: null,
      columnKeys: [...section.columnKeys],
      regions: allRegions.filter((r) => r.section === section.id),
      blankRegionHeader: !!section.blankRegionHeader,
    }));
  }

  /**
   * Returns the manifest section a given region belongs to. Resolution walks
   * `gridSectionsConfig` in declared order, matching on aggregate membership (incl. the
   * aggregate key itself), `extraRegionKeys`, then `regionKeys`. First match wins.
   * @param {string} regionKey
   * @returns {object|null}
   */
  #sectionForRegion(regionKey) {
    const rk = normalizeRegionKey(regionKey);
    if (this.#sectionCache.has(rk)) return this.#sectionCache.get(rk);
    let found = null;
    for (const section of this.gridSectionsConfig) {
      if (section.containsAggregate && this.aggregateConfig) {
        if (rk === this.aggregateConfig.key) { found = section; break; }
        if (this.aggregateConfig.isMember(rk)) { found = section; break; }
      }
      const explicits = [...(section.extraRegionKeys || []), ...(section.regionKeys || [])];
      if (explicits.some((k) => normalizeRegionKey(k) === rk)) { found = section; break; }
    }
    this.#sectionCache.set(rk, found);
    return found;
  }

  /**
   * Returns the active ballot. FPTP models have a single ballot, so the default
   * `this.ballots[0]` is correct; AMS overrides to pick by `activeTab`.
   * @returns {Ballot}
   */
  activeBallot() { return this.ballots[0]; }

  /** Returns the active ballot's user-input map. */
  currentInputMap() { return this.activeBallot().input; }

  /** Returns the active ballot's baseline map. */
  currentBaselineMap() { return this.activeBallot().baseline; }

  /** Writes a single share input into the active ballot's input map. */
  setShare(regionKey, partyKey, value) {
    const input = this.currentInputMap();
    if (!input.has(regionKey)) input.set(regionKey, new Map());
    input.get(regionKey).set(partyKey, roundShare(value));
  }

  /**
   * Clears a single share input from the active ballot, reverting that cell to baseline
   * (getShare falls back to baseline when no entered value is present). Drops the region's
   * inner map once empty so collectOverrides / serialize stay clean.
   */
  clearShare(regionKey, partyKey) {
    const input = this.currentInputMap();
    const regionMap = input.get(regionKey);
    if (!regionMap) return;
    regionMap.delete(partyKey);
    if (regionMap.size === 0) input.delete(regionKey);
  }

  /** Reads the current share (entered if set, else baseline). */
  getShare(regionKey, partyKey) {
    const cached = this.currentInputMap().get(regionKey)?.get(partyKey);
    if (Number.isFinite(cached)) return Number(cached);
    return this.getBaseline(regionKey, partyKey);
  }

  /** Reads the rounded baseline share for a region/party from the active baseline map. */
  getBaseline(regionKey, partyKey) {
    return roundShare(this.currentBaselineMap().get(regionKey)?.get(partyKey) ?? 0);
  }

  /**
   * Reads a region/party share from a specific ballot — the entered value if present, else
   * that ballot's baseline. Mirrors getShare but isn't tied to the active tab, so validate()
   * can check every ballot rather than only the visible one.
   */
  shareForBallot(ballot, regionKey, partyKey) {
    const cached = ballot.input.get(regionKey)?.get(partyKey);
    if (Number.isFinite(cached)) return Number(cached);
    return roundShare(ballot.baseline.get(regionKey)?.get(partyKey) ?? 0);
  }

  /** Returns the implied 'other' share for a region (100 − sum of party shares). */
  getOtherShare(regionKey) {
    const sum = this.parties(regionKey).reduce((s, p) => s + this.getShare(regionKey, p), 0);
    return roundShare(100 - sum);
  }

  /** Human-readable label for a ballot, used in validation messages. Default is the ballot key. */
  ballotLabel(ballot) { return ballot.key || ''; }

  /**
   * Returns rows whose entered shares sum > 100, checked across EVERY ballot — not just the
   * active tab — so a multi-ballot model can't slip an over-100% row past Submit on an
   * inactive ballot. For multi-ballot models the offending ballot's label is appended to the
   * region label so the alert names the tab.
   */
  validate() {
    const invalid = [];
    const multiBallot = this.ballots.length > 1;
    const rows = this.regions();
    this.ballots.forEach((ballot) => {
      rows.forEach((row) => {
        const sum = this.parties(row.regionKey)
          .reduce((s, p) => s + this.shareForBallot(ballot, row.regionKey, p), 0);
        if (sum <= 100) return;
        const regionLabel = multiBallot ? `${row.regionLabel} (${this.ballotLabel(ballot)})` : row.regionLabel;
        invalid.push({ ...row, regionLabel, total: roundShare(sum) });
      });
    });
    return invalid;
  }

  /**
   * Builds a regionKey → partyKey → swing-pp map from explicit input/baseline pair. Zero
   * swings are dropped. Subclasses call this once (FPTP) or twice (AMS, for
   * const and list passes) inside project().
   * @param {Map<string, Map<string, number>>} inputMap
   * @param {Map<string, Map<string, number>>} baselineMap
   * @returns {Map<string, Map<string, number>>}
   */
  buildSwings(inputMap, baselineMap) {
    const swings = new Map();
    this.regions().forEach((row) => {
      const partyMap = new Map();
      this.parties(row.regionKey).forEach((partyKey) => {
        const baseline = roundShare(baselineMap.get(row.regionKey)?.get(partyKey) ?? 0);
        const cached = inputMap.get(row.regionKey)?.get(partyKey);
        const input = Number.isFinite(cached) ? Number(cached) : baseline;
        const swing = input - baseline;
        if (Math.abs(swing) >= 1e-9) partyMap.set(partyKey, swing);
      });
      if (partyMap.size > 0) swings.set(row.regionKey, partyMap);
    });
    return swings;
  }

  /**
   * Default projection: a single uniform-swing pass over every baseline seat, driven by the
   * active input/baseline pair (`currentInputMap` / `currentBaselineMap`). Returns the
   * untouched baseline seats when no input differs from baseline (zero swings), so a model
   * with no user edits reproduces the published result exactly. Subclasses with a different
   * electoral system (e.g. AMS's constituency + D'Hondt list two-pass) override this.
   * @returns {Seat[]}
   */
  project() {
    const swings = this.buildSwings(this.currentInputMap(), this.currentBaselineMap());
    // Deep-copy on the zero-swing path: baseSeats are state.comparisonElectionData's Seats, so
    // returning a shallow slice would let state.electionData and state.comparisonElectionData
    // share Seat instances. Cloning keeps the projected and comparison elections independent.
    if (swings.size === 0) return this.baseSeats.map((seat) => new Seat(seat));
    return this.baseSeats.map((seat) => projectSeatUniformSwing(seat, swings, this.modelledPartyKeys, this.aggregateConfig));
  }

  /**
   * Resets the model to its constructed-empty state: clears every ballot's input map
   * (in place, so references stay stable) and resets the aggregate-expanded flag.
   * Subclasses override to also reset their own non-ballot state (e.g. AMS's
   * `activeTab`), calling `super.reset()` to handle ballots + the aggregate flag.
   */
  reset() {
    this.aggregateExpanded = false;
    this.ballots.forEach((b) => b.input.clear());
  }

  /**
   * Builds the wire payload that gets base64url-encoded into the `?predict=` query param.
   * Returns '' when the model has no overrides AND no aggregate-expanded flag — the empty
   * payload is what suppresses the query param entirely.
   * @param {Array} overrides - Flat array from `collectOverrides`, shape depends on whether
   *   ballots have a `serializePrefix`.
   * @param {object} [extra] - Extra envelope keys (e.g. AMS's `{ h: 1 }` discriminator).
   * @returns {string}
   */
  buildSerializedPayload(overrides, extra = {}) {
    if (overrides.length === 0 && !this.aggregateExpanded) return '';
    return base64urlEncode(JSON.stringify({
      e: this.aggregateExpanded ? 1 : 0,
      r: overrides,
      ...extra,
    }));
  }

  /**
   * Serializes every ballot's overrides into a URL-safe payload string. Each ballot
   * contributes entries tagged with its `serializePrefix` (null for single-ballot models,
   * `'c'`/`'l'` for AMS); `serializeExtra` lets subclasses add discriminator fields like
   * `{ h: 1 }`.
   * @returns {string}
   */
  serialize() {
    const overrides = this.ballots.flatMap((b) => this.collectOverrides(b.input, b.baseline, b.serializePrefix));
    return this.buildSerializedPayload(overrides, this.serializeExtra());
  }

  /** Override: extra envelope keys for `serialize()`. Default is empty. */
  serializeExtra() { return {}; }

  /**
   * Loads a serialized payload into the ballot inputs. Subclasses override
   * `acceptsPayload` to reject payloads that don't match this model (e.g. AMS rejects
   * payloads without the `h:1` flag). Entry shape depends on whether the model uses
   * ballot prefixes — single-ballot models read `[r, p, v]`, multi-ballot models read
   * `[prefix, r, p, v]` and route via `ballotForPrefix`.
   * @param {string} payload
   */
  deserialize(payload) {
    const decoded = decodePredictPayload(payload);
    if (!decoded) return;
    if (!this.acceptsPayload(decoded)) return;
    this.aggregateExpanded = decoded.e === 1;
    const validRegions = new Set(this.regions().map((r) => r.regionKey));
    const usesPrefixes = this.ballots.some((b) => b.serializePrefix !== null);
    // decoded.r comes from arbitrary (possibly hand-crafted) URL payloads; guard the shape so
    // a non-array `r` can't throw a TypeError out of deserialize and break predict-view load.
    const entries = Array.isArray(decoded.r) ? decoded.r : [];
    entries.forEach((entry) => {
      let prefix = null;
      let regionKey; let partyKey; let value;
      if (usesPrefixes) {
        [prefix, regionKey, partyKey, value] = entry;
      } else {
        [regionKey, partyKey, value] = entry;
      }
      if (!validRegions.has(regionKey)) return;
      if (!this.parties(regionKey).includes(partyKey)) return;
      const ballot = this.ballotForPrefix(prefix);
      if (!ballot) return;
      if (!ballot.input.has(regionKey)) ballot.input.set(regionKey, new Map());
      ballot.input.get(regionKey).set(partyKey, roundShare(value));
    });
  }

  /** Override: returns true if the decoded payload is meant for this model. */
  acceptsPayload(_decoded) { return true; }

  /**
   * Picks the ballot a serialized entry routes to. Single-ballot models always pick
   * `ballots[0]`; multi-ballot models match by `serializePrefix`.
   * @param {string|null} prefix
   * @returns {Ballot|null}
   */
  ballotForPrefix(prefix) {
    if (prefix === null) return this.ballots[0] || null;
    return this.ballots.find((b) => b.serializePrefix === prefix) || null;
  }

  /**
   * Loads regional shares from a simulation seat array into every ballot's input map.
   * Each ballot's `filterSeats` picks the simulation-seat subset it cares about (FPTP
   * passes through; AMS filters to constituency seats / deduped list seats). Inputs
   * are cleared in place before loading.
   * @param {Seat[]} simulationSeats
   */
  loadSimulationShares(simulationSeats) {
    this.ballots.forEach((b) => {
      b.input.clear();
      this.loadSharesFromSeats(b.filterSeats(simulationSeats), b.input);
    });
  }

  /** Returns every ballot's input map — used by `setAggregateExpanded` to propagate /
   * drop sub-region entries across all ballots in lockstep. */
  inputMaps() { return this.ballots.map((b) => b.input); }

  /**
   * Toggles the predict grid between collapsed (single aggregate row) and expanded
   * (aggregate + sub-region rows). No-ops when no aggregate is configured or the flag
   * already matches.
   *
   * On expand: for every input map returned by `inputMaps()`, copies the aggregate row's
   * party shares verbatim onto each empty sub-region as that region's own absolute share,
   * then clears the aggregate row so the sub-rows become the source of truth. Sub-regions
   * that already carry inputs (e.g. populated by a previous Apply while collapsed) are not
   * overwritten. This makes the expand transition explicit — the user sees the national
   * figure already filled into each region rather than the rows silently snapping back to
   * baseline. Note this is a deliberate level→level copy, NOT a swing copy: the projected
   * map can therefore shift slightly on expand, because a single aggregate swing applied
   * uniformly is not identical to each region adopting the same absolute share.
   *
   * On collapse: drops EVERY sub-region entry from each input map — including the ones that
   * were seeded from the aggregate on a prior expand — and reverts the grid to a single
   * aggregate row at baseline. Entered per-region values are intentionally not folded back
   * up into an aggregate figure: there is no unambiguous way to collapse a divergent
   * per-region breakdown into one number, and attempting it makes it confusing which
   * percentage maps to which region. A user who wants to keep working at the aggregate
   * level should stay collapsed; expanding then collapsing is a destructive reset of the
   * region inputs by design.
   *
   * @param {boolean} expanded - True to show sub-regions, false to collapse to aggregate.
   * @returns {void}
   */
  setAggregateExpanded(expanded) {
    if (!this.aggregateConfig) return;
    if (expanded === this.aggregateExpanded) return;
    const { key, isMember } = this.aggregateConfig;
    const subKeys = Array.from(this.regionLabelsByKey.keys()).filter((rk) => isMember(rk));

    const propagateAndClear = (input) => {
      const aggInputs = input.get(key);
      if (aggInputs && aggInputs.size > 0) {
        subKeys.forEach((rk) => {
          if (input.has(rk)) return;
          input.set(rk, new Map(aggInputs));
        });
      }
      input.delete(key);
    };
    const dropSubs = (input) => {
      Array.from(input.keys()).forEach((rk) => {
        if (rk !== key && isMember(rk)) input.delete(rk);
      });
    };

    this.inputMaps().forEach((input) => {
      if (expanded) propagateAndClear(input);
      else dropSubs(input);
    });
    this.aggregateExpanded = expanded;
  }

  /**
   * Loads regional shares from a simulation seat array into a target input map. Used by
   * subclass `loadSimulationShares` (the "Use current forecast" button).
   *
   * Computes per-region shares via `buildBaselineShares` (passing the aggregate config so
   * the synthetic aggregate is included in the result), then writes each share map into
   * `target` while honouring the current aggregate-expanded state:
   * - Expanded: skips the synthetic aggregate key. Sub-regions are the source of truth;
   *   writing the aggregate would shadow the sub-region values via the swing fallback.
   * - Collapsed: skips every `isMember` sub-region. Only the aggregate row is visible to
   *   the user, so pre-populating sub-rows would orphan that data. If the user later
   *   clicks "Show regions", `setAggregateExpanded(true)` then propagates the aggregate
   *   onto each sub-row cleanly, instead of revealing a stale sim breakdown that may
   *   conflict with what the user was just looking at.
   *
   * @param {Seat[]} seats - Source seats (already filtered to the relevant ballot type
   *   for AMS — const vs list — by the caller).
   * @param {Map<string, Map<string, number>>} target - Input map to populate (mutated).
   * @returns {void}
   */
  loadSharesFromSeats(seats, target) {
    const aggKey = this.aggregateConfig?.key;
    const isMember = this.aggregateConfig?.isMember;
    buildBaselineShares(seats, this.modelledPartyKeys, this.aggregateConfig).forEach((partyMap, regionKey) => {
      if (this.aggregateExpanded && regionKey === aggKey) return;
      if (!this.aggregateExpanded && isMember?.(regionKey)) return;
      target.set(regionKey, new Map(partyMap));
    });
  }

  /**
   * Walks an input map and emits override entries for any value that differs from the
   * matching baseline share. Used by subclass `serialize()` to build a compact URL payload.
   *
   * Baseline values come from the supplied `baselineMap` (rounded via `roundShare` so the
   * "differs from baseline" comparison matches the integer-share representation that
   * lives in the input map). Every party share is emitted independently — a region with
   * three changed parties produces three entries.
   *
   * Output shape:
   * - Without prefix (single-ballot, e.g. FPTP): `[regionKey, partyKey, value]`.
   * - With prefix (multi-ballot, e.g. AMS): `[prefix, regionKey, partyKey, value]` where
   *   prefix is `'c'` (constituency) or `'l'` (list) so deserialize can route the entry
   *   back to the correct input map.
   *
   * @param {Map<string, Map<string, number>>} inputMap - The user-input map to walk.
   * @param {Map<string, Map<string, number>>} baselineMap - The matching baseline map.
   * @param {string|null} [prefix=null] - Ballot tag prefix, or null to omit it.
   * @returns {Array<[string, string, number] | [string, string, string, number]>}
   */
  collectOverrides(inputMap, baselineMap, prefix = null) {
    const overrides = [];
    inputMap.forEach((partyMap, regionKey) => {
      partyMap.forEach((value, partyKey) => {
        const baseline = roundShare(baselineMap.get(regionKey)?.get(partyKey) ?? 0);
        if (value === baseline) return;
        overrides.push(prefix !== null ? [prefix, regionKey, partyKey, value] : [regionKey, partyKey, value]);
      });
    });
    return overrides;
  }

  /**
   * Helper for `regions()`: assembles the aggregate row + (optionally) its sub-region
   * children into a single ordered list. Subclasses call this after gathering the
   * sub-rows that should sit under the aggregate; rows that don't feed the aggregate
   * (e.g. Westminster's Scotland / Wales / NI) are appended by the caller.
   *
   * Output ordering:
   * - Always starts with one row tagged `isAggregate: true`, carrying the configured
   *   `regionKey` / `regionLabel` / `section`.
   * - When `aggregateExpanded` is true, every input sub-row is appended (in the order
   *   provided) and tagged `isAggregateChild: true`. dom.js uses both flags to render
   *   the Show/Hide toggle button on the aggregate and the indented child styling.
   *
   * Returns `subRows` unchanged when `aggregateConfig` is null — a parliament without
   * an aggregate just renders its sub-rows directly.
   *
   * @param {Array<{regionKey: string, regionLabel: string}>} subRows - Sub-region rows
   *   that feed the aggregate (the caller has already filtered via `aggregateConfig.isMember`).
   * @param {string} sectionKey - Section tag applied to all rows so `gridSections` can
   *   split them into separate tables.
   * @returns {Array<object>} Ordered rows ready for dom.js to render.
   */
  buildRowsWithAggregate(subRows, sectionKey) {
    if (!this.aggregateConfig) return subRows;
    const rows = [{
      regionKey: this.aggregateConfig.key,
      regionLabel: this.aggregateConfig.label,
      isAggregate: true,
      section: sectionKey,
    }];
    if (this.aggregateExpanded) {
      subRows.forEach((r) => rows.push({ ...r, isAggregateChild: true, section: sectionKey }));
    }
    return rows;
  }
}

/**
 * FPTP predict: single-ballot uniform-swing model. Used by Westminster (GB regions with
 * an England aggregate + NI), but the class is system-driven, not parliament-driven — any
 * pure first-past-the-post layout can use it. Grid layout, columns, virtual columns, and
 * aggregate handling all come from the manifest's `predict` config.
 *
 * Inherits `project()` (base-class uniform-swing default), `serialize` / `deserialize`,
 * and `loadSimulationShares` from `PredictModel`; only needs to supply the ballot.
 */
export class FPTPPredict extends PredictModel {
  constructor(nextElectionYear, config) {
    super(nextElectionYear, config);
    this.ballots = [new Ballot({
      input: new Map(),
      baseline: this.baselineFor(this.baseSeats),
    })];
  }
}

/**
 * AMS (Additional Member System) predict: two-ballot model. Pass 1 projects constituency
 * seats with FPTP uniform swing; Pass 2 runs per-region D'Hondt list allocation, seeded
 * with the pass-1 constituency wins so the list tops up under-represented parties. Used
 * by Holyrood today; the class is system-driven, so any AMS / MMP layout (Welsh Senedd,
 * London Assembly) can reuse it.
 *
 * Holds two ballots — constituency and list — and an `activeTab` that selects which one
 * the grid reads/writes. `project()` runs both passes regardless of the active tab.
 */
export class AMSPredict extends PredictModel {
  constructor(nextElectionYear, config) {
    super(nextElectionYear, config);
    this.tabs = this.config.tabs || [];
    this.activeTab = this.tabs[0]?.key || 'constituency';
    this.ballots = [
      new Ballot({
        key: 'constituency',
        serializePrefix: 'c',
        input: new Map(),
        baseline: this.baselineFor(this.baseSeats.filter((s) => !Seat.isList(s))),
        filterSeats: (seats) => seats.filter((s) => !Seat.isList(s)),
      }),
      new Ballot({
        key: 'list',
        serializePrefix: 'l',
        // List baselines: each list seat in a region carries the full regional list total
        // duplicated, so dedupe by region before computing shares.
        input: new Map(),
        baseline: this.baselineFor(dedupeListSeatsByRegion(this.baseSeats)),
        filterSeats: dedupeListSeatsByRegion,
      }),
    ];
  }

  activeBallot() {
    return this.ballots.find((b) => b.key === this.activeTab) || this.ballots[0];
  }

  /**
   * Switches the active ballot tab. Subsequent reads/writes via `currentInputMap` and
   * `currentBaselineMap` route to the matching ballot.
   *
   * Validates `tab` against the manifest's `predict.tabs` list and silently no-ops on
   * unknown keys — guards against a bad URL fragment or stray caller. `project()` runs
   * both ballot passes regardless of the active tab, so switching tabs is purely a UI
   * concern: it doesn't trigger a re-projection on its own.
   *
   * @param {string} tab - Ballot key from `this.tabs[].key` (e.g. `'constituency'`, `'list'`).
   * @returns {void}
   */
  setActiveTab(tab) {
    if (!this.tabs.some((t) => t.key === tab)) return;
    this.activeTab = tab;
  }

  reset() {
    super.reset();
    this.activeTab = this.tabs[0]?.key || 'constituency';
  }

  acceptsPayload(decoded) { return decoded.h === 1; }
  serializeExtra() { return { h: 1 }; }

  ballotLabel(ballot) {
    return this.tabs.find((t) => t.key === ballot.key)?.label || ballot.key || '';
  }

  project() {
    const [constBallot, listBallot] = this.ballots;
    const constSwings = this.buildSwings(constBallot.input, constBallot.baseline);
    const listSwings = this.buildSwings(listBallot.input, listBallot.baseline);
    // Zero-swing short-circuit: with no inputs the user expects the published baseline
    // result exactly. Re-running D'Hondt on the baseline data can produce a mathematically
    // valid but different allocation when the source data wasn't itself a clean D'Hondt
    // recomputation (e.g. the "2021 Election (2026 boundaries)" file preserves the historical
    // 2021 list winners rather than re-allocating under the new region structure).
    if (constSwings.size === 0 && listSwings.size === 0) return this.baseSeats.map((seat) => new Seat(seat));
    // List votes only move when the user edits the list ballot. Editing only the constituency
    // ballot still changes the list allocation — but indirectly, via the updated constituency
    // wins that seed each region's D'Hondt divisors (constWinsByRegion below) — not by applying
    // constituency-ballot swings (computed against the constituency baseline) to list votes,
    // which would conflate two different ballots' baselines.
    const effectiveListSwings = listSwings;

    const constBase = this.baseSeats.filter((s) => !Seat.isList(s));
    const listBase = this.baseSeats.filter((s) => Seat.isList(s));

    // Pass 1: project constituency seats with FPTP swing.
    const projectedConst = constBase.map((s) => projectSeatUniformSwing(s, constSwings, this.modelledPartyKeys, this.aggregateConfig));

    // Group constituency wins by region to seed the D'Hondt divisors.
    const constWinsByRegion = new Map();
    projectedConst.forEach((seat) => {
      const rk = normalizeRegionKey(seat.region);
      if (!constWinsByRegion.has(rk)) constWinsByRegion.set(rk, new Map());
      const wins = constWinsByRegion.get(rk);
      if (seat.winner) wins.set(seat.winner, (wins.get(seat.winner) || 0) + 1);
    });

    // Pass 2: D'Hondt list allocation per region, topping up the constituency wins.
    const projectedList = this.#allocateListSeats(listBase, effectiveListSwings, constWinsByRegion);

    return [...projectedConst, ...projectedList];
  }

  /**
   * Projects the regional list seats and runs D'Hondt allocation per region (AMS pass 2).
   * Each region's list seats share an identical regional vote total (the source data duplicates
   * it across every list seat), so the divisor pool is read from the first projected seat in the
   * region; constituency wins from pass 1 seed the divisor so the list tops up under-represented
   * parties. Returns one `Seat` per input list seat, in region order, each carrying its allocated
   * winner (or null when the region had fewer parties with votes than seats).
   * @param {Seat[]} listBase - The baseline list seats (all regions).
   * @param {Map<string, Map<string, number>>} listSwings - Swing map for the list ballot.
   * @param {Map<string, Map<string, number>>} constWinsByRegion - normalised regionKey →
   *   (partyKey → constituency wins), used to seed each region's D'Hondt divisors.
   * @returns {Seat[]}
   */
  #allocateListSeats(listBase, listSwings, constWinsByRegion) {
    const listByRegion = new Map();
    listBase.forEach((s) => {
      const rk = normalizeRegionKey(s.region);
      if (!rk) return;
      if (!listByRegion.has(rk)) listByRegion.set(rk, []);
      listByRegion.get(rk).push(s);
    });

    const projectedList = [];
    listByRegion.forEach((regionListSeats, rk) => {
      const projectedRegionList = regionListSeats.map((s) => projectSeatUniformSwing(s, listSwings, this.modelledPartyKeys, this.aggregateConfig));
      // List seats in a region share identical totals (each seat duplicates the regional vote);
      // accumulate from the first seat only.
      const voteSumByParty = new Map();
      const firstProj = projectedRegionList[0];
      Object.entries(firstProj?.votes || {}).forEach(([partyKey, voteCount]) => {
        voteSumByParty.set(partyKey, Number(voteCount || 0));
      });
      const winners = dhondtAllocate(voteSumByParty, regionListSeats.length, constWinsByRegion.get(rk) || new Map());
      projectedRegionList.forEach((proj, idx) => {
        projectedList.push(new Seat({ ...proj, winner: winners[idx] || null }));
      });
    });
    return projectedList;
  }
}

function decodePredictPayload(payload) {
  const raw = String(payload || '').trim();
  if (!raw) return null;
  const json = base64urlDecode(raw);
  if (!json) return null;
  try { return JSON.parse(json); } catch { return null; }
}

/**
 * Resolves the predict model class for a parliament from its `predict.model` config field.
 * Returns null when the parliament has no predict block (predict feature disabled).
 * @param {string} parliament - Parliament key.
 * @returns {typeof PredictModel | null}
 */
export function predictModelClassFor(parliament) {
  const config = manifest.parliamentConfig(parliament).predict;
  if (!config?.model) return null;
  if (config.model === 'fptp') return FPTPPredict;
  if (config.model === 'ams') return AMSPredict;
  return null;
}
