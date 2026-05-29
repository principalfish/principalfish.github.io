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

import { state, manifest, Seat } from './state.js';
import { normalizeRegionKey, roundShare, base64urlEncode, base64urlDecode } from './utils.js';

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
    if (sum > 100) {
      let minKey = null;
      let minVal = Infinity;
      partyMap.forEach((v, k) => { if (v > 0 && v < minVal) { minVal = v; minKey = k; } });
      // Clamp at zero — overshoot is bounded by ~3pp in practice but the guard prevents
      // a negative share leaking through if a future party set raises the bound.
      if (minKey) partyMap.set(minKey, Math.max(0, minVal - (sum - 100)));
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
 *   When set, sub-regions whose own swing is unset fall back to the aggregate's swing.
 * @returns {Seat}
 */
export function projectSeatUniformSwing(baseSeat, swingsByRegionByParty, modelledPartyKeys, aggregateConfig = null) {
  const totalVotes = baseSeat.turnout;
  if (totalVotes <= 0) return new Seat(baseSeat);

  const regionKey = normalizeRegionKey(baseSeat.region);
  const baseVotes = baseSeat.votes || {};

  // Resolve the swing for a party: prefer the seat's own region, fall back to the
  // configured aggregate (e.g. 'england', 'scotland') for sub-regions when the region has
  // no direct entry.
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
    const baseShare = (Number(baseVotes[partyKey] || 0) / totalVotes) * 100;
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
 * Runs D'Hondt seat allocation for one Holyrood region, deducting constituency wins from
 * the divisor so list seats top up under-represented parties.
 * @param {Map<string, number>} votesByParty
 * @param {number} nSeats
 * @param {Map<string, number>} constWinsByParty
 * @returns {string[]} Ordered array of winning party keys (one per seat).
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
    if (bestParty != null) {
      listSeatsWon.set(bestParty, (listSeatsWon.get(bestParty) || 0) + 1);
      winners.push(bestParty);
    }
  }
  return winners;
}

/**
 * Abstract base for predict models. Owns the baseline election, the user-input share map,
 * and the projection entry point. Reads/writes go through the active input/baseline maps,
 * which subclasses expose via currentInputMap()/currentBaselineMap() — Westminster has a
 * single pair, Holyrood swaps between const and list pairs as the active tab changes.
 *
 * Subclasses implement parliament-specific row/column structure (regions, parties,
 * gridSections), the projection algorithm (project), and serialization. Everything else
 * — share reads/writes, swing computation, validation, the 'other' share helper — is
 * shared.
 */
class PredictModel {
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
   *   - `model` {'westminster'|'holyrood'} — class selector read by `predictModelClassFor`
   *     before construction; the model class itself does not consume this field.
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
   *   - `tabs` {Array<{key: string, label: string}>} — Holyrood-only ballot tabs (typically
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
   * aggregate config. Subclasses call this in their constructor (Westminster: once over
   * all seats; Holyrood: once each over the const and deduped-list slices).
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
    return out.map((r) => ({
      ...r,
      predictLabel: this.regionLabelOverrides[normalizeRegionKey(r.regionKey)] || r.regionLabel,
    }));
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
    for (const section of this.gridSectionsConfig) {
      if (section.containsAggregate && this.aggregateConfig) {
        if (rk === this.aggregateConfig.key) return section;
        if (this.aggregateConfig.isMember(rk)) return section;
      }
      const explicits = [...(section.extraRegionKeys || []), ...(section.regionKeys || [])];
      if (explicits.some((k) => normalizeRegionKey(k) === rk)) return section;
    }
    return null;
  }

  /** Override: returns the user-input map currently being read/written. Westminster always
   * returns the same map; Holyrood swaps based on activeTab. */
  currentInputMap() { return new Map(); }

  /** Override: returns the baseline map paired with the current input map (so getShare's
   * fallback uses the matching ballot's baseline). */
  currentBaselineMap() { return new Map(); }

  /** Writes a single share input into the active input map. */
  setShare(regionKey, partyKey, value) {
    const input = this.currentInputMap();
    if (!input.has(regionKey)) input.set(regionKey, new Map());
    input.get(regionKey).set(partyKey, roundShare(value));
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

  /** Returns the implied 'other' share for a region (100 − sum of party shares). */
  getOtherShare(regionKey) {
    const sum = this.parties(regionKey).reduce((s, p) => s + this.getShare(regionKey, p), 0);
    return roundShare(100 - sum);
  }

  /** Returns rows whose entered shares sum > 100. */
  validate() {
    const invalid = [];
    this.regions().forEach((row) => {
      const sum = this.parties(row.regionKey).reduce((s, p) => s + this.getShare(row.regionKey, p), 0);
      if (sum > 100) invalid.push({ ...row, total: roundShare(sum) });
    });
    return invalid;
  }

  /**
   * Builds a regionKey → partyKey → swing-pp map from explicit input/baseline pair. Zero
   * swings are dropped. Subclasses call this once (Westminster) or twice (Holyrood, for
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
   * electoral system (e.g. Holyrood's constituency + D'Hondt list two-pass) override this.
   * @returns {Seat[]}
   */
  project() {
    const swings = this.buildSwings(this.currentInputMap(), this.currentBaselineMap());
    if (swings.size === 0) return this.baseSeats.slice();
    return this.baseSeats.map((seat) => projectSeatUniformSwing(seat, swings, this.modelledPartyKeys, this.aggregateConfig));
  }

  /** Reset shared state. Subclasses override to also clear their input maps and ballot
   * tab, calling `super.reset()` to handle the aggregate flag. */
  reset() { this.aggregateExpanded = false; }

  /**
   * Builds the wire payload shared by every subclass `serialize()`. Returns '' when the
   * model has no overrides AND no aggregate-expanded flag — the empty payload is what
   * suppresses the `?predict=` query param entirely.
   * @param {Array} overrides - From `collectOverrides`. Shape varies per subclass.
   * @param {object} [extra] - Extra envelope keys (e.g. Holyrood's `{ h: 1 }` discriminator).
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

  /** Override: serialize to a URL-safe payload string. */
  serialize() { return ''; }

  /** Override: load a serialized payload. */
  deserialize(_payload) {}

  /** Override: load from a model-output simulation seat array (Apply current forecast). */
  loadSimulationShares(_simulationSeats) {}

  /**
   * Override hook: returns every input map subject to aggregate expand/collapse handling.
   *
   * Westminster has a single nested `Map<regionKey, Map<partyKey, share>>` and returns
   * `[this.inputByRegion]`. Holyrood maintains separate const and list ballots and
   * returns `[this.constInput, this.listInput]` so `setAggregateExpanded`'s propagation
   * + drop logic touches both ballots in lockstep, preventing a stale value from leaking
   * through after a tab switch.
   *
   * Default returns `[]` so a subclass without input maps (or `aggregateConfig === null`)
   * is a safe no-op when `setAggregateExpanded` runs.
   *
   * @returns {Map<string, Map<string, number>>[]}
   */
  inputMaps() { return []; }

  /**
   * Toggles the predict grid between collapsed (single aggregate row) and expanded
   * (aggregate + sub-region rows). No-ops when no aggregate is configured or the flag
   * already matches.
   *
   * On expand: for every input map returned by `inputMaps()`, copies the aggregate row's
   * party shares onto each empty sub-region (preserving the user's national-level intent
   * as a per-region starting point), then clears the aggregate row so sub-rows become the
   * source of truth. Sub-regions that already carry inputs (e.g. populated by a previous
   * Apply while collapsed) are not overwritten.
   *
   * On collapse: drops every sub-region entry from each input map. The aggregate row's
   * own inputs are kept, so a user who toggled Show → Hide retains any aggregate-level
   * value they had typed earlier.
   *
   * The propagation step is what fixes the otherwise-jarring transition where expanding
   * after typing "SNP 50%" at the national level would silently revert sub-rows to
   * baseline despite the projection still using the user's swing.
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
   *   for Holyrood — const vs list — by the caller).
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
   * - Without prefix (Westminster, single ballot): `[regionKey, partyKey, value]`.
   * - With prefix (Holyrood, two ballots): `[prefix, regionKey, partyKey, value]` where
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
 * Westminster predict: GB regions (England aggregate or sub-regions) + NI region. Inputs
 * are a nested Map<region, Map<party, share>>. Grid layout, columns, and the 'nat'
 * virtual column are all driven by the manifest's `gridSections` + `virtualColumns`.
 */
export class WestminsterPredict extends PredictModel {
  constructor(nextElectionYear, config) {
    super(nextElectionYear, config);
    this.baselineByRegion = this.baselineFor(this.baseSeats);
    this.inputByRegion = new Map();
  }

  currentInputMap() { return this.inputByRegion; }
  currentBaselineMap() { return this.baselineByRegion; }
  inputMaps() { return [this.inputByRegion]; }

  reset() {
    super.reset();
    this.inputByRegion = new Map();
  }

  // project() uses the base-class uniform-swing default (single GB+NI pass).

  /** Populates inputs from a simulation seat array (model-output forecast). */
  loadSimulationShares(simulationSeats) {
    this.inputByRegion = new Map();
    this.loadSharesFromSeats(simulationSeats, this.inputByRegion);
  }

  /** Encodes inputs as a base64url-encoded JSON object. Empty string when no overrides. */
  serialize() {
    return this.buildSerializedPayload(this.collectOverrides(this.inputByRegion, this.baselineByRegion));
  }

  deserialize(payload) {
    const decoded = decodePredictPayload(payload);
    if (!decoded) return;
    this.aggregateExpanded = decoded.e === 1;
    const validRegions = new Set(this.regions().map((r) => r.regionKey));
    (decoded.r || []).forEach(([regionKey, partyKey, value]) => {
      if (!validRegions.has(regionKey)) return;
      if (!this.parties(regionKey).includes(partyKey)) return;
      this.setShare(regionKey, partyKey, value);
    });
  }
}

/**
 * Holyrood predict: 8 regional rows with separate constituency and list ballot inputs.
 * Active tab determines which input map the grid reads/writes; project() runs both passes
 * regardless of the active tab.
 */
export class HolyroodPredict extends PredictModel {
  constructor(nextElectionYear, config) {
    super(nextElectionYear, config);
    this.tabs = this.config.tabs || [];
    this.activeTab = this.tabs[0]?.key || 'constituency';
    this.constInput = new Map();
    this.listInput = new Map();
    this.constBaselineByRegion = this.baselineFor(this.baseSeats.filter((s) => !Seat.isList(s)));
    // List baselines: each list seat in a region carries the full regional list total
    // duplicated, so dedupe by region before computing shares.
    this.listBaselineByRegion = this.baselineFor(HolyroodPredict.#dedupeListSeatsByRegion(this.baseSeats));
  }

  static #dedupeListSeatsByRegion(seats) {
    const seen = new Set();
    return seats.filter((s) => {
      if (!Seat.isList(s)) return false;
      if (seen.has(s.region)) return false;
      seen.add(s.region);
      return true;
    });
  }

  currentInputMap() { return this.activeTab === 'list' ? this.listInput : this.constInput; }
  currentBaselineMap() { return this.activeTab === 'list' ? this.listBaselineByRegion : this.constBaselineByRegion; }
  inputMaps() { return [this.constInput, this.listInput]; }

  /**
   * Switches the active ballot tab. Subsequent reads/writes via `currentInputMap` and
   * `currentBaselineMap` route to the matching ballot's pair (const vs list).
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
    this.constInput = new Map();
    this.listInput = new Map();
  }

  project() {
    const constSwings = this.buildSwings(this.constInput, this.constBaselineByRegion);
    const listSwings = this.buildSwings(this.listInput, this.listBaselineByRegion);
    // Zero-swing short-circuit: with no inputs the user expects the published baseline
    // result exactly. Re-running D'Hondt on the baseline data can produce a mathematically
    // valid but different allocation when the source data wasn't itself a clean D'Hondt
    // recomputation (e.g. the "2021 Election (2026 boundaries)" file preserves the historical
    // 2021 list winners rather than re-allocating under the new region structure).
    if (constSwings.size === 0 && listSwings.size === 0) return this.baseSeats.slice();
    const effectiveListSwings = listSwings.size > 0 ? listSwings : constSwings;

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
   * Projects the regional list seats and runs D'Hondt allocation per region (Holyrood pass 2).
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

  loadSimulationShares(simulationSeats) {
    const constSeats = simulationSeats.filter((s) => !Seat.isList(s));
    const dedupedListSeats = HolyroodPredict.#dedupeListSeatsByRegion(simulationSeats);
    this.constInput = new Map();
    this.listInput = new Map();
    this.loadSharesFromSeats(constSeats, this.constInput);
    this.loadSharesFromSeats(dedupedListSeats, this.listInput);
  }

  serialize() {
    const overrides = [
      ...this.collectOverrides(this.constInput, this.constBaselineByRegion, 'c'),
      ...this.collectOverrides(this.listInput, this.listBaselineByRegion, 'l'),
    ];
    return this.buildSerializedPayload(overrides, { h: 1 });
  }

  deserialize(payload) {
    const decoded = decodePredictPayload(payload);
    if (!decoded || decoded.h !== 1) return;
    this.aggregateExpanded = decoded.e === 1;
    const validRegions = new Set(this.regions().map((r) => r.regionKey));
    (decoded.r || []).forEach(([prefix, regionKey, partyKey, value]) => {
      if (!validRegions.has(regionKey)) return;
      if (!this.parties(regionKey).includes(partyKey)) return;
      const target = prefix === 'l' ? this.listInput : this.constInput;
      if (!target.has(regionKey)) target.set(regionKey, new Map());
      target.get(regionKey).set(partyKey, roundShare(value));
    });
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
  if (config.model === 'westminster') return WestminsterPredict;
  if (config.model === 'holyrood') return HolyroodPredict;
  return null;
}
