"""US House 2022->2024 conditional residual structure: is there correlated (non-state) structure
treated as independent race noise, and how does that affect seat-total variance?"""
import json, math, sys
import numpy as np
r = json.load(open('/home/philiph/workspace/principalfish.github.io/data/models/us/calibration/house-inputs.json'))
ch = r['national_raw_changes']
print('national raw changes', {k: (v if not isinstance(v, dict) else list(v)[:3]) for k, v in ch.items()})
rows = []
for d in r['districts']:
    if not d.get('included'): continue
    b, o = d['baseline_2022'], d['observed_2024']
    tb, to = sum(b.values()), sum(o.values())
    if tb <= 0 or to <= 0: continue
    mb = 100*(b['democrat']-b['republican'])/tb
    mo = 100*(o['democrat']-o['republican'])/to
    rows.append((d['district'], d['state'], mb, mo))
mb = np.array([x[2] for x in rows]); mo = np.array([x[3] for x in rows]); st = [x[1] for x in rows]
swing = mo - mb
# Simple uniform national margin swing (turnout-unweighted mean) as the conditional reference
resid = swing - swing.mean()
print('districts', len(rows), 'mean margin swing', round(swing.mean(),2), 'SD of district margin swing', round(swing.std(),2))
# structure: regress residual on baseline margin (proportional-type pattern) and on |margin|
X = np.column_stack([np.ones_like(mb), mb, np.abs(mb)])
beta, *_ = np.linalg.lstsq(X, resid, rcond=None)
fit = X @ beta
print('residual ~ 1 + margin + |margin|: beta', np.round(beta,4), 'R^2', round(1 - ((resid-fit)**2).sum()/((resid-resid.mean())**2).sum(), 3))
# state component share
states = sorted(set(st)); idx = {s:i for i,s in enumerate(states)}
sm = np.zeros(len(states)); cnt = np.zeros(len(states))
for v, s in zip(resid, st): sm[idx[s]] += v; cnt[idx[s]] += 1
state_mean = sm/cnt
within = resid - state_mean[[idx[s] for s in st]]
print('between-state var (district-weighted)', round(float((state_mean[[idx[s] for s in st]]**2).mean()),2), ' within var', round(float((within**2).mean()),2))
# after removing state means, is there remaining structure on baseline margin?
beta2, *_ = np.linalg.lstsq(X, within, rcond=None)
fit2 = X @ beta2
print('within-state residual ~ margin terms: R^2', round(1 - ((within-fit2)**2).sum()/((within-within.mean())**2).sum(), 3), 'beta', np.round(beta2,4))
# Effect on seat-total variance: count districts within +-10 of 0 at baseline (competitive); the systematic margin-slope component
# is common across districts within a cycle; if its across-cycle SD were |beta1| ~ realised, how much seat-total variance would it add?
comp = np.abs(mb) < 15
print('competitive districts (|baseline margin|<15):', int(comp.sum()))
