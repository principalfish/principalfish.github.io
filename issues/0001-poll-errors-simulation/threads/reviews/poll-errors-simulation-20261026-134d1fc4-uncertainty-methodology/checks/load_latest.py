import sys, os
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data')
import numpy as np
from pathlib import Path
from model_support.monte_carlo import load_draws
draws, meta = load_draws(Path('/home/philiph/dbs/simulations/westminster-latest.npz'))
print(meta)
print('sims', len(draws.lam), 'parties', draws.party_ids)
snap = draws.replay
print(snap.config)
inp = snap.inputs
print('regions', inp.region_ids)
print('national avg', dict(zip(inp.party_ids, np.round(inp.national_average,2))))
