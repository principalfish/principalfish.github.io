"""Perfect-regional-swing backtest of NATIONAL SEAT TOTALS (seat noise + lambda only, LOO params).
If actual seat totals fall far outside the simulated range, seat-level deviations were correlated,
which independent seat noise cannot represent."""
import sys, os, sqlite3, math
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data')
sys.path.insert(0, os.environ['REPO'] + '/data/scripts')
import numpy as np
from model_support.uncertainty import load_uncertainty_config
from model_support.monte_carlo import share_scale_array, centring_weights
import calibrate_uncertainty as cu
from model_support.calibration import pool_pairs, build_pair_cells
conn = sqlite3.connect('file:/home/philiph/dbs/elections.db?mode=ro', uri=True)
cfg = load_uncertainty_config()
pairs = cu.fit_pairs(conn, cfg)
names = dict(conn.execute('select id, name from parties').fetchall())
rng = np.random.default_rng(2024)
S = 4000
for i, held in enumerate(pairs):
    pt = pool_pairs([p for j, p in enumerate(pairs) if j != i])
    s_raw, s_lam = pt.sigma_seat_raw, pt.sigma_lambda
    label, e0, e1 = [x for x in cu.ELECTION_PAIRS if x[0] == held.label][0]
    earlier = cu.load_election(conn, e0); later = cu.load_election(conn, e1)
    speaker = {cu.speaker_seat_id(conn, n, earlier.map_id) for n in (e0, e1)}
    pc = build_pair_cells(earlier.votes, later.votes, excluded_seats=speaker, additive_party_ids=set(cu.PARTY_ID_ALIASES.values()), min_regional_share=cfg.lambda_min_share)
    universe = pc.seats[0].party_ids
    K = len(universe)
    sim_tot = np.zeros((S, K), dtype=np.int64); actual = np.zeros(K, dtype=np.int64); central = np.zeros(K, dtype=np.int64)
    lam = s_lam * rng.standard_normal(S)[:, None]  # one lambda per simulated election, shared by seats
    for seat in pc.seats:
        base, add, prop = seat.baseline, seat.additive, seat.proportional
        cen = np.maximum(base + add, 0); cen = cen / cen.sum() * 100
        sd = s_raw * share_scale_array(cen, cfg); q = centring_weights(cen, np.ones_like(cen, dtype=bool))
        e = rng.standard_normal((S, K)) * sd; e -= q * e.sum(1, keepdims=True)
        x = np.maximum(base + add + lam * (prop - add) + e, 0)
        win = x.argmax(1)
        np.add.at(sim_tot, (np.arange(S), win), 1)
        central[cen.argmax()] += 1
        lv = later.votes.seat_votes[seat.seat_id]; ap = max(lv, key=lambda p: lv[p])
        if ap in universe: actual[universe.index(ap)] += 1
    print(f'== {held.label}: LOO sigma_seat_raw={s_raw:.2f} sigma_lambda={s_lam:.2f}')
    for k in np.argsort(-actual)[:5]:
        lo, med, hi = np.quantile(sim_tot[:, k], [0.05, 0.5, 0.95])
        pct = (sim_tot[:, k] < actual[k]).mean() + 0.5 * (sim_tot[:, k] == actual[k]).mean()
        print(f'   {names.get(universe[k], universe[k])[:18]:18s} actual {actual[k]:4d}  central(additive) {central[k]:4d}  sim 5/50/95 {int(lo)}/{int(med)}/{int(hi)}  actual percentile {pct:.3f}')
