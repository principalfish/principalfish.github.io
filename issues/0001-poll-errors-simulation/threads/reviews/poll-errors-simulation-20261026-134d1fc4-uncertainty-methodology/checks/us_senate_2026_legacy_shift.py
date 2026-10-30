"""Size of the legacy cohort-composition shift for the 2026 Senate field: pooled 2020 Senate field
shares (legacy subtractor) vs the accepted 2020 House anchor (new subtractor)."""
import sys, os, sqlite3, json
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['REPO'] + '/data')
from model_support.us_senate_anchors import resolve_senate_house_anchors
c = sqlite3.connect('file:/home/philiph/dbs/elections.db?mode=ro', uri=True)
eid = c.execute("select id from elections where name='2020 US Senate Election'").fetchone()[0]
cycles = json.load(open(os.environ['REPO'] + '/data/models/us/senate_cycles/cycles-v1.json'))
roster = json.load(open(os.environ['REPO'] + '/data/models/us/senate_cycles/roster-2026-v1.json'))
names = dict(c.execute('select id, name from parties'))
rows = c.execute("select s.seat_name, v.party_id, v.vote_total from votes v join seats s on s.id=v.seat_id where v.election_id=? and v.vote_total is not null", (eid,)).fetchall()
tot = {}
for seat, party, votes in rows: tot.setdefault(seat, {}).setdefault(names[party], 0); tot[seat][names[party]] += votes
# 2026 regular states = Class 2 states in the roster
cl2 = sorted({seat['n'] for seat in roster['seats'] if any(m.get('class') == 2 for m in seat['members'])})
print('class-2 states from roster:', None if cl2 is None else len(cl2))
states = [s for s in tot if cl2 is None or s in cl2]
D = sum(tot[s].get('Democratic', 0) for s in states); R = sum(tot[s].get('Republican', 0) for s in states); T = sum(sum(tot[s].values()) for s in states)
print(f'pooled 2020 Senate field ({len(states)} states): D {100*D/T:.2f} R {100*R/T:.2f}  (D-R {100*(D-R)/T:+.2f})')
a = resolve_senate_house_anchors({0: '2020-us-senate'})[0].shares
print(f"accepted 2020 House anchor: D {a['Democratic']:.2f} R {a['Republican']:.2f} (D-R {a['Democratic']-a['Republican']:+.2f})")
print(f"legacy minus new subtractor on D-R margin: {(100*(D-R)/T) - (a['Democratic']-a['Republican']):+.2f} pts -> legacy fallback shifted unpolled races by {-(100*(D-R)/T - (a['Democratic']-a['Republican'])):+.2f} D-R")
