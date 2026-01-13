from flask import Flask, jsonify, render_template_string
import sqlite3

app = Flask(__name__)

def get_db():
    conn = sqlite3.connect('wikipedia_history.db')
    conn.row_factory = sqlite3.Row
    return conn

# HTML Template (Bootstrap for quick styling)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Chronos Guess</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #f4f7f6; font-family: 'Segoe UI', serif; }
        .event-card { background: white; border-left: 5px solid #2c3e50; margin-bottom: 10px; padding: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        .guess-row { display: flex; align-items: center; padding: 10px; margin-top: 5px; border-radius: 4px; color: white; font-weight: bold; }
        #feedback-list { margin-top: 20px; }
    </style>
</head>
<body class="container py-5">
    <div class="row justify-content-center">
        <div class="col-md-8">
            <h1 class="text-center mb-4">📜 Guess The Year</h1>
            
            <div id="facts-container"></div>

            <div class="card p-4 mt-4 shadow-sm">
                <div class="input-group mb-3">
                    <input type="number" id="guessYear" class="form-control" placeholder="Enter Year">
                    <select id="guessEra" class="form-select" style="max-width: 80px;">
                        <option value="AD">AD</option>
                        <option value="BC">BC</option>
                    </select>
                    <button class="btn btn-primary" onclick="makeGuess()">Submit Guess</button>
                </div>
                <p class="text-muted">Guesses remaining: <span id="attempts">8</span></p>
            </div>

            <div id="feedback-list"></div>
        </div>
    </div>

    <script>
        let target = {};
        let attempts = 8;

        async function startNewGame() {
            const res = await fetch('/api/challenge');
            target = await res.json();
            attempts = 8;
            document.getElementById('attempts').innerText = attempts;
            document.getElementById('feedback-list').innerHTML = '';
            
            const container = document.getElementById('facts-container');
            container.innerHTML = `<h4>Evidence from a ${target.timeframe_type}:</h4>`;
            target.events.forEach(e => {
                container.innerHTML += `<div class="event-card">${e}</div>`;
            });
        }

        function makeGuess() {
            if (attempts <= 0) return;

            const gYear = parseInt(document.getElementById('guessYear').value);
            const gEra = document.getElementById('guessEra').value;
            
            if (isNaN(gYear)) return;

            // Timeline Math: AD is positive, BC is negative
            // No Year 0: ... -2, -1, 1, 2 ...
            const tVal = target.era === 'BC' ? -target.year : target.year;
            const gVal = gEra === 'BC' ? -gYear : gYear;

            let diff = Math.abs(tVal - gVal);
            // Adjust if boundary crossed (BC to AD)
            if ((tVal < 0 && gVal > 0) || (tVal > 0 && gVal < 0)) diff -= 1;

            // Handle Decade/Century "Win"
            let isWin = false;
            if (target.timeframe_type === 'year' && diff === 0) isWin = true;
            if (target.timeframe_type === 'decade' && diff < 10 && Math.floor(Math.abs(tVal)/10) === Math.floor(Math.abs(gVal)/10)) isWin = true;
            if (target.timeframe_type === 'century' && diff < 100) isWin = true;

            displayFeedback(gYear, gEra, diff, isWin);

            if (isWin) {
                alert("Victory! The answer was " + target.year + " " + target.era);
                location.reload();
            } else {
                attempts--;
                document.getElementById('attempts').innerText = attempts;
                if (attempts === 0) {
                    alert("Game Over! The year was " + target.year + " " + target.era);
                    location.reload();
                }
            }
        }

        function displayFeedback(year, era, diff, isWin) {
            const list = document.getElementById('feedback-list');
            const row = document.createElement('div');
            row.className = 'guess-row';
            
            let cfg = { label: "Over 1000 yrs away", color: "#4b0082" };
            if (isWin) cfg = { label: "CORRECT", color: "#198754" };
            else if (diff <= 2) cfg = { label: "1-2 years away (Burning!)", color: "#dc3545" };
            else if (diff <= 10) cfg = { label: "3-10 years away (Hot)", color: "#fd7e14" };
            else if (diff <= 40) cfg = { label: "11-40 years away (Warm)", color: "#ffc107" };
            else if (diff <= 200) cfg = { label: "41-200 years away (Cool)", color: "#0dcaf0" };
            else if (diff <= 1000) cfg = { label: "200-1000 years away (Cold)", color: "#0d6efd" };

            row.style.backgroundColor = cfg.color;
            row.innerText = `${year} ${era} — ${cfg.label}`;
            list.prepend(row);
        }

        startNewGame();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/challenge')
def challenge():
    conn = get_db()
    # Find a random year that has at least 3 events for a better game
    row = conn.execute('''
        SELECT year, era, timeframe_type FROM events 
        GROUP BY year, era 
        HAVING COUNT(*) >= 3 
        ORDER BY RANDOM() LIMIT 1
    ''').fetchone()
    
    events = conn.execute('SELECT event FROM events WHERE year = ? AND era = ? ORDER BY RANDOM() LIMIT 5', 
                         (row['year'], row['era'])).fetchall()
    conn.close()
    
    return jsonify({
        "year": row['year'],
        "era": row['era'],
        "timeframe_type": row['timeframe_type'],
        "events": [e['event'] for e in events]
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)