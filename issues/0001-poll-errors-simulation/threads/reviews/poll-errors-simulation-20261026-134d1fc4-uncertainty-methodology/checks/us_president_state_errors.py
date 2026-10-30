"""President: are final state-average errors correlated across states beyond the national error?
(The kernel has only national G + independent per-state H/R for states.)"""
import json, math
import numpy as np
r = json.load(open('/home/philiph/workspace/principalfish.github.io/data/models/us/calibration/president-comparison-inputs.json'))['payload']
inv = r['inventory']
by_year = {}
for row in inv:
    if not row.get('included'): continue
    e = row.get('raw_margin_poll_minus_result_pp')
    if e is None: continue
    by_year.setdefault(row['year'], []).append((row['state'], float(e)))
# national proxy errors from the President comparison report
rep = json.load(open('/home/philiph/workspace/principalfish.github.io/data/models/us/calibration/president-comparison-report.json'))
nat = {}
def find(o, path=''):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ('national_proxy_errors', 'national_errors', 'national_observations'):
                print('found', path + '/' + k, json.dumps(v)[:400])
            find(v, path + '/' + k)
find({k: rep[k] for k in rep if k not in ('scenarios', 'references')})
for y, items in sorted(by_year.items()):
    e = np.array([v for _, v in items])
    print(y, 'states', len(e), 'mean state margin error (poll-result) %+.2f' % e.mean(), 'SD about mean %.2f' % e.std(ddof=1), 'RMS %.2f' % math.sqrt((e**2).mean()))
    print('   ', ', '.join(f'{s[:6]}:{v:+.1f}' for s, v in sorted(items, key=lambda t: t[1])))
