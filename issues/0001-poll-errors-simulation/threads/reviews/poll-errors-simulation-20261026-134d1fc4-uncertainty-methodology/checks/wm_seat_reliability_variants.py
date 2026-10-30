"""Which ingredient drives 2019n->2024 seat overconfidence? Perfect regional swing; LOO params."""
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
rng = np.random.default_rng(7)
S = 4000
def evaluate(held_idx, lam_centre, lam_sd, seat_sd_raw, tail='gauss'):
    held = pairs[held_idx]
    label, e0, e1 = [x for x in cu.ELECTION_PAIRS if x[0]==held.label][0]
    earlier = cu.load_election(conn, e0); later = cu.load_election(conn, e1)
    speaker = {cu.speaker_seat_id(conn, n, earlier.map_id) for n in (e0, e1)}
    pc = build_pair_cells(earlier.votes, later.votes, excluded_seats=speaker, additive_party_ids=set(cu.PARTY_ID_ALIASES.values()), min_regional_share=cfg.lambda_min_share)
    fav_n=fav_lost=0; briers=[]; lo_n=lo_won=0
    for seat in pc.seats:
        base, add, prop = seat.baseline, seat.additive, seat.proportional
        cen = np.maximum(base + add + lam_centre*(prop-add), 0); cen = cen/cen.sum()*100
        sd = seat_sd_raw*share_scale_array(cen, cfg)
        q = centring_weights(cen, np.ones_like(cen, dtype=bool))
        lam = lam_centre + lam_sd*rng.standard_normal(S)[:,None]
        if tail == 'gauss':
            z = rng.standard_normal((S, len(base)))
        else:  # student t, 4 df, scaled to have the same MAD as N(0,1)
            nu = 4
            t = rng.standard_t(nu, (S, len(base)))
            z = t * (0.6745/0.7407)  # MAD of t4 = 0.7407
        e = z*sd; e -= q*e.sum(1, keepdims=True)
        x = np.maximum(base + add + lam*(prop-add) + e, 0)
        tot = x.sum(1, keepdims=True); x = x/np.where(tot>0,tot,1)
        pw = np.bincount(x.argmax(1), minlength=len(base))/S
        lv = later.votes.seat_votes[seat.seat_id]; ap = max(lv, key=lambda p: lv[p])
        if ap not in seat.party_ids: continue
        a = seat.party_ids.index(ap); y = np.zeros(len(base)); y[a]=1
        briers.append(((pw-y)**2).sum())
        f = pw.argmax()
        if pw[f] >= 0.95: fav_n += 1; fav_lost += (f != a)
        for k in range(len(base)):
            if 0 < pw[k] < 0.05: lo_n += 1; lo_won += y[k]
    return np.mean(briers), fav_n, fav_lost, lo_n, lo_won
for held_idx in (1, 3):
    train = [p for j,p in enumerate(pairs) if j != held_idx]
    pt = pool_pairs(train)
    import statistics
    lam_mean = statistics.fmean(p.lam for p in train)
    lam_sd_centred = statistics.pstdev([p.lam for p in train])
    print('==', pairs[held_idx].label, f'train: sigma_raw={pt.sigma_seat_raw:.2f}, rms_lam={pt.sigma_lambda:.2f}, mean_lam={lam_mean:.2f}, sd_lam={lam_sd_centred:.2f}')
    for name, args in (
        ('as built (lam~N(0,rms), gauss, robust sigma)', (0.0, pt.sigma_lambda, pt.sigma_seat_raw, 'gauss')),
        ('lam centred at train mean', (lam_mean, max(lam_sd_centred,0.1), pt.sigma_seat_raw, 'gauss')),
        ('t4 seat tails, same MAD', (0.0, pt.sigma_lambda, pt.sigma_seat_raw, 't')),
        ('lam mean + t4 tails', (lam_mean, max(lam_sd_centred,0.1), pt.sigma_seat_raw, 't')),
    ):
        b, fn, fl, ln, lw = evaluate(held_idx, *args)
        print(f'   {name:45s} Brier={b:.4f}  fav>=95%: {fl}/{fn} lost ({fl/max(fn,1):.2%}); cells 0<p<5%: {int(lw)}/{ln} won ({lw/max(ln,1):.2%})')
