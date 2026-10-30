"""Perfect-regional-swing backtest of seat win probabilities (seat noise + lambda only), leave-one-pair-out."""
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
conn.execute('PRAGMA query_only=1')
cfg = load_uncertainty_config()
pairs = cu.fit_pairs(conn, cfg)
rng = np.random.default_rng(12345)
S = 4000
bins = np.array([0,0.05,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,0.95,1.0001])
for i, held in enumerate(pairs):
    train = [p for j,p in enumerate(pairs) if j != i]
    pt = pool_pairs(train)
    for label_mode, s_raw, s_lam in (('LOO', pt.sigma_seat_raw, pt.sigma_lambda), ('prod', 4.9029, 0.3818)):
        label, e0, e1 = [x for x in cu.ELECTION_PAIRS if x[0]==held.label][0]
        earlier = cu.load_election(conn, e0); later = cu.load_election(conn, e1)
        speaker = {cu.speaker_seat_id(conn, n, earlier.map_id) for n in (e0, e1)}
        pc = build_pair_cells(earlier.votes, later.votes, excluded_seats=speaker, additive_party_ids=set(cu.PARTY_ID_ALIASES.values()), min_regional_share=cfg.lambda_min_share)
        probs=[]; outcome=[]; fav_lost=0; fav_n=0; brier=[]
        for seat in pc.seats:
            base, add, prop = seat.baseline, seat.additive, seat.proportional
            central = np.maximum(base + add, 0); central = central/central.sum()*100
            sd = s_raw*share_scale_array(central, cfg)
            q = centring_weights(central, np.ones_like(central, dtype=bool))
            lam = s_lam*rng.standard_normal(S)[:,None]
            e = rng.standard_normal((S, len(base)))*sd
            e -= q*e.sum(1, keepdims=True)
            x = base + add + lam*(prop-add) + e
            x = np.maximum(x, 0)
            tot = x.sum(1, keepdims=True)
            x = np.where(tot>0, x/np.where(tot>0,tot,1)*100, base)
            win = x.argmax(1)
            pw = np.bincount(win, minlength=len(base))/S
            # actual later winner
            later_votes = later.votes.seat_votes[seat.seat_id]
            actual_party = max(later_votes, key=lambda p: later_votes[p])
            if actual_party not in seat.party_ids: continue
            a = seat.party_ids.index(actual_party)
            y = np.zeros(len(base)); y[a]=1
            brier.append(((pw-y)**2).sum())
            for k in range(len(base)):
                if pw[k]>0 or y[k]: probs.append(pw[k]); outcome.append(y[k])
            f = pw.argmax()
            if pw[f] >= 0.95:
                fav_n += 1; fav_lost += (f != a)
        probs=np.array(probs); outcome=np.array(outcome)
        idx = np.digitize(probs, bins)-1
        rows=[]
        for b in range(len(bins)-1):
            m = idx==b
            if m.sum()>0 and bins[b] >= 0.05 or (b==0 and m.sum()>0):
                rows.append(f'[{bins[b]:.2f},{min(bins[b+1],1):.2f}): n={m.sum():4d} mean_p={probs[m].mean():.3f} obs={outcome[m].mean():.3f}')
        print(f'== {held.label} ({label_mode}: sigma_seat_raw={s_raw:.2f}, sigma_lambda={s_lam:.2f}); seats={len(brier)}, mean Brier={np.mean(brier):.4f}; favourites>=95%: {fav_n}, of which lost: {fav_lost} ({fav_lost/max(fav_n,1):.3%})')
        for r in rows: print('   ', r)
