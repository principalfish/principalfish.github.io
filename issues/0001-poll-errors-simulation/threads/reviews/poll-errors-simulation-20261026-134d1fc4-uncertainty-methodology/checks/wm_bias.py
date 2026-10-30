"""Westminster: MC centre vs deterministic central, using the saved 2026-10-22 replay snapshot."""
import sys, os
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data')
import numpy as np
from dataclasses import replace
from pathlib import Path
from model_support.monte_carlo import (load_draws, deterministic_projection, error_scales,
    ErrorStreams, draw_errors, project_seats_vectorised, run_simulations)
draws, meta = load_draws(Path('/home/philiph/dbs/simulations/westminster-latest.npz'))
inp, cfg = draws.replay.inputs, draws.replay.config
names = {1:'Lab',2:'Ref',3:'PC',4:'Con',5:'LD',6:'Grn',8:'SNP',9:'DUP',10:'All',11:'UUP',12:'SDLP',13:'SF',15:'Oth'}
pn = [names[p] for p in inp.party_ids]
det = deterministic_projection(inp)
w = inp.seat_turnout
det_vote = np.average(det.share[0], axis=0, weights=w)
det_seats = np.bincount(det.winner[0], minlength=inp.party_count)

def run(cfg, label, S=10000, seed=None):
    seed = draws.seed if seed is None else seed
    streams = ErrorStreams.from_seed(seed)
    scales = error_scales(inp, cfg, det.share[0])
    votes=[]; seats=[]; winsum=np.zeros((inp.seat_count, inp.party_count)); share_sum=np.zeros((inp.seat_count, inp.party_count))
    for start in range(0, S, 1000):
        e = draw_errors(streams, scales, 1000)
        pr = project_seats_vectorised(inp, e)
        votes.append(np.einsum('snk,n->sk', pr.share, w)/w.sum())
        seats.append(np.stack([np.bincount(row, minlength=inp.party_count) for row in pr.winner]))
        winsum += (pr.winner[...,None]==np.arange(inp.party_count)).sum(0)
        share_sum += pr.share.sum(0)
    votes=np.concatenate(votes); seats=np.concatenate(seats)
    print(f'--- {label}')
    print(f"{'party':5s} {'det_v':>7s} {'mc_mean':>7s} {'mc_med':>7s} {'bias':>6s} | {'det_s':>5s} {'mean_s':>6s} {'med_s':>5s} {'p5':>4s} {'p95':>4s} {'sd_v':>5s}")
    for k in range(inp.party_count):
        if det_vote[k] < 0.3 and det_seats[k]==0: continue
        lo, med, hi = np.quantile(seats[:,k],[0.05,0.5,0.95], method='inverted_cdf')
        print(f"{pn[k]:5s} {det_vote[k]:7.2f} {votes[:,k].mean():7.2f} {np.median(votes[:,k]):7.2f} {votes[:,k].mean()-det_vote[k]:6.2f} | {det_seats[k]:5d} {seats[:,k].mean():6.1f} {int(med):5d} {int(lo):4d} {int(hi):4d} {votes[:,k].std():5.2f}")
    pwin = winsum / S
    detw = det.winner[0]
    p_det = pwin[np.arange(inp.seat_count), detw]
    print('seats where central winner has P(win)<0.5:', int((p_det<0.5).sum()), ' modal winner != central winner:', int((pwin.argmax(1)!=detw).sum()))
    # mean seat share bias
    mean_share = share_sum/S
    diff = mean_share - det.share[0]
    print('max |mean seat share - central| =', np.abs(diff).max().round(3), ' mean abs over contested cells (det share>5):', np.abs(diff[det.share[0]>5]).mean().round(3))
    # sum of P(win) = expected seats
    print('expected seats (sum pwin):', dict((pn[k], round(pwin[:,k].sum(),1)) for k in range(inp.party_count) if pwin[:,k].sum()>0.5))
    return votes, seats, pwin

print('det seats', dict((pn[k], int(det_seats[k])) for k in range(inp.party_count) if det_seats[k]))
v, s, pw = run(cfg, 'production config')
run(replace(cfg, sigma_seat=0.0), 'no seat noise')
run(replace(cfg, sigma_seat=0.0, sigma_lambda=0.0), 'no seat noise, no lambda')
run(replace(cfg, sigma_nat=0.0, sigma_reg=0.0, sigma_lambda=0.0, design_effect=1e9, n_eff_cap=3000), 'seat noise only (sampling ~0)')
