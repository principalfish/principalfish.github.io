"""Current run: central seats at lambda=0 (published) vs the historical mean lambda-hat 0.34."""
import sys, os
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data')
import numpy as np
from dataclasses import replace
from pathlib import Path
from model_support.monte_carlo import load_draws, ErrorDraws, project_seats_vectorised
draws, meta = load_draws(Path('/home/philiph/dbs/simulations/westminster-latest.npz'))
inp = draws.replay.inputs
names = {1:'Lab',2:'Ref',3:'PC',4:'Con',5:'LD',6:'Grn',8:'SNP',15:'Oth'}
for lam in (0.0, 0.34, 0.57, 1.0):
    z = ErrorDraws.zeros(inp); z = replace(z, swing_type=np.array([lam]))
    w = project_seats_vectorised(inp, z).winner[0]
    c = np.bincount(w, minlength=inp.party_count)
    print(f'lambda={lam:.2f}:', ', '.join(f'{names[p]} {c[k]}' for k, p in enumerate(inp.party_ids) if p in names and c[k]))
