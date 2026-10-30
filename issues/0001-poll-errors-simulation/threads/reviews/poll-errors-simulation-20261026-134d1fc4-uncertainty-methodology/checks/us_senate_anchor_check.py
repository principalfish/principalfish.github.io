"""Light correctness check of the Senate House-anchor fallback (087afcfc + uncommitted comparison v2)."""
import sys, os
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data'); sys.path.insert(0, os.environ['REPO'] + '/data/models/us')
import numpy as np
from collections import defaultdict
from _common import senate_fallback_swings
# 1) recipe: Senate race baseline 70/30, House anchor 50/50, current House generic 50/50 -> zero fallback swing
names = {20: 'Democratic', 21: 'Republican'}
ws = defaultdict(float, {(None, 20): 50.0, (None, 21): 50.0}); tw = defaultdict(float, {(None, 20): 1.0, (None, 21): 1.0})
out = senate_fallback_swings(anchor_shares_by_seat={1: {'Democratic': 50.0, 'Republican': 50.0, 'Others': 0.0}},
    region_by_seat_id={1: 7}, region_swings={7: {20: -20.0, 21: 20.0}}, weighted_sums=ws, total_weights=tw,
    party_name_by_id=names, party_universe=(20, 21))
print('recipe fallback swing (expect 0/0):', out)
# 2) mixed-year races in one region get different deltas
out2 = senate_fallback_swings(anchor_shares_by_seat={1: {'Democratic': 50.0, 'Republican': 48.0, 'Others': 2.0}, 2: {'Democratic': 47.0, 'Republican': 51.0, 'Others': 2.0}},
    region_by_seat_id={1: 7, 2: 7}, region_swings={7: {20: 0.0, 21: 0.0}}, weighted_sums=ws, total_weights=tw,
    party_name_by_id=names, party_universe=(20, 21))
print('mixed-year deltas:', out2)
# 3) the v2 comparison reference: reproduce 2022 corrected and legacy signed means without Monte Carlo
from model_support.us_calibration_comparison import load_comparison, build_reference
data = load_comparison('senate')
for cyc in data.cycles:
    if cyc.cycle != 2022 or cyc.denominator_policy != 'reported-including-nota': continue
    new = build_reference(data, cyc); old = build_reference(data, cyc, legacy_senate=True)
    rn, ro = np.array(new.residuals), np.array(old.residuals)
    print(f'2022 reported-incl-NOTA raw: corrected mean {rn.mean():+.4f} (n={len(rn)}), legacy mean {ro.mean():+.4f}; corrected centred MS {((rn-rn.mean())**2).mean():.2f}, legacy centred MS {((ro-ro.mean())**2).mean():.2f}')
    raw = np.array(new.observed) - np.array(new.raw_center)
    print(f'   corrected raw-poll mean {raw.mean():+.4f}')
