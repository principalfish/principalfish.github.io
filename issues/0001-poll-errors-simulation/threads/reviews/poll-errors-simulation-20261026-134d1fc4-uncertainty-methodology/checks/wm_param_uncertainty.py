"""Sensitivity of Westminster headline odds to (a) propagating sigma_nat estimation uncertainty
(posterior-predictive t_9 national errors, 9 calibration elections) and (b) heavier seat tails,
plus Monte Carlo standard errors of the published odds."""
import sys, os, math
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data')
import numpy as np
from pathlib import Path
from model_support.monte_carlo import (load_draws, deterministic_projection, error_scales,
    ErrorStreams, ErrorDraws, project_seats_vectorised, _centre, _sampling_errors)
draws, meta = load_draws(Path('/home/philiph/dbs/simulations/westminster-latest.npz'))
inp, cfg = draws.replay.inputs, draws.replay.config
det = deterministic_projection(inp)
sc = error_scales(inp, cfg, det.share[0])
L = inp.party_ids.index(1); R = inp.party_ids.index(2)
def run(nat_dist='gauss', seat_dist='gauss', S=10000, seed=draws.seed):
    st = ErrorStreams.from_seed(seed); rng = np.random.default_rng(seed + 17)
    seats = []
    for start in range(0, S, 1000):
        n = min(1000, S-start); k = sc.national_sd.shape[0]; r = sc.regional_systematic_sd.shape[0]; ns = sc.seat_sd.shape[0]
        z = st.national.standard_normal((n, k))
        if nat_dist == 't9':  # posterior predictive: shared chi2 mixing per simulation (one sigma per election)
            z = z / np.sqrt(rng.chisquare(9, (n, 1)) / 9)
        national = sc.national_sd * z
        nbr = national[:, None, :] * sc.national_reach
        rn = st.regional.standard_normal((n, 2, r, k))
        systematic = sc.regional_systematic_sd * rn[:, 0]
        sampling = _sampling_errors(rn[:, 1].astype(np.float32), sc.regional_sampling_shape, sc.regional_sampling_scale)
        zs = st.seat.standard_normal((n, ns, k))
        if seat_dist == 't4':
            zs = rng.standard_t(4, (n, ns, k)) * (0.6745/0.7407)
        seat = (zs * sc.seat_sd).astype(np.float32)
        lam = sc.sigma_lambda * st.swing_type.standard_normal(n)
        e = ErrorDraws(national=_centre(national.copy(), sc.national_weight), national_by_region=_centre(nbr, sc.national_region_weight),
                       regional=_centre(systematic, sc.regional_weight) + sampling, regional_sampling=sampling,
                       seat=_centre(seat, sc.seat_weight.astype(np.float32)), swing_type=lam)
        pr = project_seats_vectorised(inp, e)
        seats.append(np.stack([np.bincount(row, minlength=inp.party_count) for row in pr.winner]))
    seats = np.concatenate(seats)
    pm = (seats[:, L] >= 326).mean(); pmR = (seats[:, R] >= 326).mean()
    largest = seats == seats.max(1, keepdims=True); pl = (largest / largest.sum(1, keepdims=True)).mean(0)
    hung = (seats.max(1) < 326).mean()
    lo, med, hi = np.quantile(seats[:, L], [0.05, 0.5, 0.95], method='inverted_cdf')
    return pm, pl[L], hung, (int(lo), int(med), int(hi)), pmR, pl[R]
for label, nd, sd in (('as built (Gaussian national, Gaussian seat)', 'gauss', 'gauss'),
                      ('t9 posterior-predictive national', 't9', 'gauss'),
                      ('t4 seat tails (same MAD)', 'gauss', 't4'),
                      ('t9 national + t4 seat', 't9', 't4')):
    pm, pl, hung, rng_, pmR, plR = run(nd, sd)
    se = lambda p: math.sqrt(p*(1-p)/10000)
    print(f'{label:46s} Lab P(maj)={pm:.4f} (MC SE {se(pm):.4f})  Lab P(largest)={pl:.4f} (SE {se(pl):.4f})  hung={hung:.4f}  Lab seats 5/50/95={rng_}  Ref P(maj)={pmR:.4f} P(largest)={plR:.4f}')
# seed-to-seed spread of the published odds at fixed inputs
vals = [run('gauss','gauss', S=10000, seed=s)[0] for s in (1, 2, 3, 4, 5)]
print('Lab P(maj) across 5 seeds:', [round(v,4) for v in vals], 'SD', round(float(np.std(vals, ddof=1)),4))
