"""National polling error: leave-one-election-out coverage, tails, and the rival-lead (contrast) variance."""
import sys, os, json, math
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data')
import numpy as np
from model_support.monte_carlo import share_scale_array, centring_weights, centring_sd_ratio
from model_support.uncertainty import UncertaintyConfig
cfg = UncertaintyConfig()
r = json.load(open(os.environ['REPO'] + '/data/models/westminster/uncertainty_calibration_report.json'))
els = r['national']['elections']
# per election: list of (key, z_raw, ratio, error, result share)
def sigma_raw(subset):
    # election-weighted RMS of z_raw
    return math.sqrt(np.mean([np.mean([p['z_raw']**2 for p in e['parties']]) for e in subset]))
print('all-election sigma_raw', round(sigma_raw(els),4))
cover = []; contrast_z = []
for i, e in enumerate(els):
    train = [x for j,x in enumerate(els) if j != i]
    s = sigma_raw(train)
    inside = [abs(p['z_raw']) < 1.645*s for p in e['parties']]
    cover += inside
    # contrast between the two largest parties at the election (by result)
    ps = sorted(e['parties'], key=lambda p: -p['result'])
    a, b = ps[0], ps[1]
    shares = np.array([p['result'] for p in e['parties']] + [max(0.0, 100 - sum(p['result'] for p in e['parties']))])
    sd_raw = s * share_scale_array(shares, cfg)
    q = centring_weights(shares, np.ones_like(shares, dtype=bool))
    # covariance of centred errors: C = (I - q 1^T) D (I - q 1^T)^T
    K = len(shares); A = np.eye(K) - np.outer(q, np.ones(K)); D = np.diag(sd_raw**2)
    C = A @ D @ A.T
    ia, ib = 0, 1  # parties sorted? map back
    keys = [p['key'] for p in e['parties']]
    ia, ib = keys.index(a['key']), keys.index(b['key'])
    var_lead = C[ia,ia] + C[ib,ib] - 2*C[ia,ib]
    rho = C[ia,ib]/math.sqrt(C[ia,ia]*C[ib,ib])
    lead_err = a['error'] - b['error']
    contrast_z.append(lead_err/math.sqrt(var_lead))
    print(f"{e['year']}: LOO sigma_raw={s:.3f}; cells inside 90%: {sum(inside)}/{len(inside)}; lead {a['key']}-{b['key']} error {lead_err:+.2f} pts, model SD {math.sqrt(var_lead):.2f}, z={lead_err/math.sqrt(var_lead):+.2f}, model rho={rho:+.2f}")
print(f'LOO cell coverage of nominal 90%: {np.mean(cover):.3f} ({sum(cover)}/{len(cover)})')
cz = np.array(contrast_z)
print(f'lead z: RMS={math.sqrt((cz**2).mean()):.3f} (1.0 = calibrated), |z|>1.645: {(np.abs(cz)>1.645).sum()}/{len(cz)}')
# historical correlation of Con and Lab errors
con = np.array([[p['error'] for p in e['parties'] if p['key']=='con'][0] for e in els])
lab = np.array([[p['error'] for p in e['parties'] if p['key']=='lab'][0] for e in els])
print('historical zero-origin corr(Con err, Lab err) =', round(float((con*lab).sum()/math.sqrt((con**2).sum()*(lab**2).sum())),3), ' (centred:', round(float(np.corrcoef(con,lab)[0,1]),3),')')
