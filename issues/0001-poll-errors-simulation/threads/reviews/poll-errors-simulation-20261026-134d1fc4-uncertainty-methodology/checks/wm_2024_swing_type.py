"""2019n->2024: seat totals implied by actual regional shares under additive, fitted-lambda and proportional swing."""
import sys, os, sqlite3
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data'); sys.path.insert(0, os.environ['REPO'] + '/data/scripts')
import numpy as np
from model_support.uncertainty import load_uncertainty_config
import calibrate_uncertainty as cu
from model_support.calibration import build_pair_cells, predicted_shares
conn = sqlite3.connect('file:/home/philiph/dbs/elections.db?mode=ro', uri=True)
cfg = load_uncertainty_config(); pairs = cu.fit_pairs(conn, cfg)
names = dict(conn.execute('select id, name from parties').fetchall())
for held in pairs:
    label, e0, e1 = [x for x in cu.ELECTION_PAIRS if x[0] == held.label][0]
    earlier = cu.load_election(conn, e0); later = cu.load_election(conn, e1)
    speaker = {cu.speaker_seat_id(conn, n, earlier.map_id) for n in (e0, e1)}
    pc = build_pair_cells(earlier.votes, later.votes, excluded_seats=speaker, additive_party_ids=set(cu.PARTY_ID_ALIASES.values()), min_regional_share=cfg.lambda_min_share)
    uni = pc.seats[0].party_ids
    out = {}
    for lam in (0.0, held.lam, 1.0):
        tot = np.zeros(len(uni), dtype=int)
        for seat in pc.seats: tot[predicted_shares(seat, lam).argmax()] += 1
        out[round(lam, 3)] = tot
    act = np.zeros(len(uni), dtype=int)
    for seat in pc.seats:
        lv = later.votes.seat_votes[seat.seat_id]; ap = max(lv, key=lambda p: lv[p])
        if ap in uni: act[uni.index(ap)] += 1
    top = np.argsort(-act)[:4]
    print(held.label, ' | '.join(f"{names.get(uni[k], uni[k])[:12]}: actual {act[k]} / lam0 {out[0.0][k]} / lam_hat({held.lam:.2f}) {out[round(held.lam,3)][k]} / lam1 {out[1.0][k]}" for k in top))
