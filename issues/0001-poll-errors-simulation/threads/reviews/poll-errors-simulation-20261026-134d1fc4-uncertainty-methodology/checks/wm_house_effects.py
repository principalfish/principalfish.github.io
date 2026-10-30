"""Westminster 30-day window at 2026-10-22: pollster concentration and house-effect variance
that neither V (independent sampling) nor sigma_nat (equal-weight final polls) represents."""
import sqlite3, math, datetime as dt
from collections import defaultdict
import numpy as np
conn = sqlite3.connect('file:/home/philiph/dbs/elections.db?mode=ro', uri=True)
T = dt.date(2026, 10, 22)
rows = conn.execute("""
  SELECT p.id, ps.identifier, p.fieldwork_end, pr.party_id, pr.percentage
  FROM polls p JOIN pollsters ps ON ps.id = p.pollster_id JOIN maps m ON m.id = p.map_id
  JOIN poll_rows pr ON pr.poll_id = p.id
  WHERE m.parliament = 'westminster' AND p.seat_id IS NULL AND pr.region_id IS NULL
    AND p.fieldwork_end <= ? AND p.fieldwork_end > ?""", (T.isoformat(), (T - dt.timedelta(days=30)).isoformat())).fetchall()
polls = defaultdict(dict); meta = {}
for pid, ps, end, party, pct in rows:
    polls[pid][party] = pct; meta[pid] = (ps, dt.date.fromisoformat(end[:10]))
for pid in polls:
    if meta[pid][0] == 'lord_ashcroft':
        tot = sum(polls[pid].values()); polls[pid] = {k: v*100/tot for k, v in polls[pid].items()}
print('polls in window', len(polls), '(Lord Ashcroft shares renormalised to 100 as the model does)')
w = {pid: 2 ** (-(T - meta[pid][1]).days / 30) for pid in polls}
W = sum(w.values())
by_ps = defaultdict(float)
for pid in polls: by_ps[meta[pid][0]] += w[pid] / W
print('pollster weight shares:', {k: round(v, 3) for k, v in sorted(by_ps.items(), key=lambda t: -t[1])})
kish_ps = 1 / sum(v * v for v in by_ps.values())
print('effective number of pollsters (Kish on pollster weight):', round(kish_ps, 2), ' raw pollsters:', len(by_ps))
for party, name in ((1, 'Lab'), (2, 'Ref'), (4, 'Con'), (5, 'LD'), (6, 'Grn')):
    avg = sum(w[p] * polls[p].get(party, 0) for p in polls) / W
    means = {}
    for ps in by_ps:
        ids = [p for p in polls if meta[p][0] == ps]
        means[ps] = sum(w[p] * polls[p].get(party, 0) for p in ids) / sum(w[p] for p in ids)
    house = np.array([means[ps] - avg for ps in by_ps])
    sh = float(np.sqrt((house ** 2).mean()))
    var_deployed = sh ** 2 * sum(v * v for v in by_ps.values())
    var_equal = sh ** 2 / len(by_ps)
    print(f'{name}: average {avg:.1f}; pollster-mean SD (house-effect proxy) {sh:.2f} pts; implied house-effect SD of deployed average {math.sqrt(var_deployed):.2f} vs equal-weight-by-pollster {math.sqrt(var_equal):.2f}')
