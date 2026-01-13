import sqlite3
import json

def export_for_web():
    conn = sqlite3.connect('wikipedia_history.db')
    conn.row_factory = sqlite3.Row
    
    # We group by year/era to create a list of challenges
    rows = conn.execute('''
        SELECT year, era, timeframe_type, GROUP_CONCAT(event, '|||') as event_list 
        FROM events 
        GROUP BY year, era 
        HAVING COUNT(*) >= 3
    ''').fetchall()
    
    challenges = []
    for r in rows:
        challenges.append({
            "y": r['year'],
            "e": r['era'],
            "t": r['timeframe_type'],
            "f": r['event_list'].split('|||')[:5] # Top 5 facts
        })
    
    with open('challenges.json', 'w') as f:
        json.dump(challenges, f)
    conn.close()

export_for_web()