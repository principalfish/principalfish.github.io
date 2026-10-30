"""Seat-level residual tails and leave-one-pair-out coverage for the Westminster seat+lambda terms."""
import sys, os, sqlite3, statistics, math
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data')
sys.path.insert(0, os.environ['REPO'] + '/data/scripts')
import numpy as np
from model_support.uncertainty import load_uncertainty_config
import calibrate_uncertainty as cu
from model_support.calibration import pool_pairs, seat_predictions, seat_residuals, share_scale
conn = sqlite3.connect('file:/home/philiph/dbs/elections.db?mode=ro', uri=True)
conn.execute('PRAGMA query_only=1')
cfg = load_uncertainty_config(cu.UNCERTAINTY_CONFIG_PATH if hasattr(cu,'UNCERTAINTY_CONFIG_PATH') else None)
pairs = cu.fit_pairs(conn, cfg)
pooled = pool_pairs(pairs)
print('pooled sigma_seat_raw', round(pooled.sigma_seat_raw,4), 'sigma_lambda', round(pooled.sigma_lambda,4), 'mean_lambda', round(pooled.mean_lambda,4))
raw_all = np.array([u for p in pairs for u in p.residuals.raw])
real_all = np.array([u for p in pairs for u in p.residuals.realised])
print('raw u: plain SD %.3f robust SD %.3f  kurtosis(excess) %.2f' % (raw_all.std(), pooled.raw_spread.sd, ((raw_all-raw_all.mean())**4).mean()/raw_all.var()**2-3))
thr = 1.645*pooled.sigma_seat_raw
print('in-sample: frac |u_raw - 0| < 1.645*sigma_raw (engine 90%% seat-noise interval): %.3f' % (np.abs(raw_all) < thr).mean())
for z,lab in ((1.645,'90%'),(2.576,'99%'),(3.29,'99.9%')):
    print(f'  outside {lab} gaussian: observed {(np.abs(raw_all) > z*pooled.sigma_seat_raw).mean():.4f} vs nominal {2*(1-statistics.NormalDist().cdf(z)):.4f}')
# Leave-one-pair-out: total engine seat-level deviation = lambda*(b-a) + seat noise
# history: y - a = lam_hat*(b-a) + u*s(p_hat)
print()
print('Leave-one-pair-out coverage of (y - a) by engine seat+lambda distribution (centred seat noise, lambda~N(0,sigma_l^2)):')
from model_support.calibration import build_pair_cells
for i, held in enumerate(pairs):
    train = [p for j,p in enumerate(pairs) if j != i]
    pool_t = pool_pairs(train)
    s_raw, s_lam = pool_t.sigma_seat_raw, pool_t.sigma_lambda
    # recompute the held pair's cells at lambda=0 prediction (engine central uses lambda=0)
    label, e0, e1 = [x for x in cu.ELECTION_PAIRS if x[0]==held.label][0]
    earlier = cu.load_election(conn, e0); later = cu.load_election(conn, e1)
    speaker = {cu.speaker_seat_id(conn, n, earlier.map_id) for n in (e0, e1)}
    pc = build_pair_cells(earlier.votes, later.votes, excluded_seats=speaker, additive_party_ids=set(cu.PARTY_ID_ALIASES.values()), min_regional_share=cfg.lambda_min_share)
    preds = seat_predictions(pc.seats, 0.0, cfg)
    inside = []; inside_cont = []
    for c in pc.cells:
        pr = preds[(c.seat_id, c.party_id)]
        if pr.share <= 0 or not math.isfinite(pr.ratio): continue
        sd_seat = s_raw * share_scale(min(pr.share,100.0), cfg) * pr.ratio
        lever = c.proportional - c.additive
        sd = math.sqrt(sd_seat**2 + (s_lam*lever)**2)
        ok = abs(c.y - c.additive) < 1.645*sd
        inside.append(ok)
        if pr.contender: inside_cont.append(ok)
    print(f'  hold out {held.label}: train sigma_seat_raw={s_raw:.3f} sigma_lambda={s_lam:.3f} (held lam_hat={held.lam:.3f}); coverage all cells {np.mean(inside):.3f} (n={len(inside)}), contenders {np.mean(inside_cont):.3f}')
