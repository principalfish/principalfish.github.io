"""Current Westminster inputs: error budget by component, lead SD and correlation, sampling vs systematic."""
import sys, os, math
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data')
import numpy as np
from dataclasses import replace
from pathlib import Path
from model_support.monte_carlo import (load_draws, deterministic_projection, error_scales,
    ErrorStreams, draw_errors, project_seats_vectorised)
draws, meta = load_draws(Path('/home/philiph/dbs/simulations/westminster-latest.npz'))
inp, cfg = draws.replay.inputs, draws.replay.config
names = {1:'Lab',2:'Ref',3:'PC',4:'Con',5:'LD',6:'Grn',8:'SNP',9:'DUP',10:'All',11:'UUP',12:'SDLP',13:'SF',15:'Oth'}
pn = [names[p] for p in inp.party_ids]
det = deterministic_projection(inp)
sc = error_scales(inp, cfg, det.share[0])
w = inp.seat_turnout
rw = np.bincount(inp.seat_region, weights=w)/w.sum()
print('region weights', np.round(rw,3))
print('regional sampling SD (pts) by region for Lab/Ref/Con/LD/Grn:')
ks = [pn.index(x) for x in ('Lab','Ref','Con','LD','Grn','SNP')]
sd_samp = sc.regional_sampling_sd
for r, rid in enumerate(inp.region_ids):
    print('  region', rid, 'fallback' if inp.region_fallback[r].any() else '', np.round(sd_samp[r, ks],2), 'exact' if sc.regional_sampling_exact[r] else 'FALLBACK-total-var')
print('national V (implied by regional samp aggregated) approx SD:', np.round(np.sqrt((rw[:,None]**2 * sd_samp**2).sum(0))[ks],2))
def nat_vote(cfgx, S=6000, seed=99):
    st = ErrorStreams.from_seed(seed); s = error_scales(inp, cfgx, det.share[0]); out=[]
    for _ in range(S//1000):
        e = draw_errors(st, s, 1000); pr = project_seats_vectorised(inp, e)
        out.append(np.einsum('snk,n->sk', pr.share, w)/w.sum())
    return np.concatenate(out)
full = nat_vote(cfg)
L, R = pn.index('Lab'), pn.index('Ref')
print('full: SD Lab %.2f Ref %.2f corr %.2f lead SD %.2f' % (full[:,L].std(), full[:,R].std(), np.corrcoef(full[:,L], full[:,R])[0,1], (full[:,L]-full[:,R]).std()))
comp = {
 'national only': replace(cfg, sigma_reg=0, sigma_seat=0, sigma_lambda=0, design_effect=1e12),
 'regional systematic only': replace(cfg, sigma_nat=0, sigma_seat=0, sigma_lambda=0, design_effect=1e12),
 'sampling only': replace(cfg, sigma_nat=0, sigma_reg=0, sigma_seat=0, sigma_lambda=0),
 'seat only': replace(cfg, sigma_nat=0, sigma_reg=0, sigma_lambda=0, design_effect=1e12),
 'lambda only': replace(cfg, sigma_nat=0, sigma_reg=0, sigma_seat=0, design_effect=1e12),
}
for k, c in comp.items():
    v = nat_vote(c, S=3000)
    print(f'{k:26s} national vote SD: ' + ' '.join(f'{pn[j]}={v[:,j].std():.2f}' for j in ks) + f'  lead(Lab-Ref) SD={((v[:,L]-v[:,R]).std()):.2f}')
