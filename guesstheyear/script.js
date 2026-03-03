let allChallenges = [];
let guesses = [];
let currentChallenge = null;
let attempts = 8;
let isGameOver = false;
let mode = 'daily';
let currentSeed = null;
let guessHistory = [];
let won = false;
let currentStreak = 0;

function getDailyChallengeIndex(seedSource, challengeCount) {
    const hash = Array.from(seedSource).reduce((sum, char) => Math.imul(31, sum) + char.charCodeAt(0) | 0, 0);
    const seed = Math.abs((hash * 12) + 912);
    return seed % challengeCount;
}

function getDailySaveKey(seedSource) {
    return 'chronos_save_' + seedSource;
}

function loadDailyGameState(seedSource) {
    const raw = localStorage.getItem(getDailySaveKey(seedSource));
    if (!raw) return null;
    return JSON.parse(raw);
}

function saveDailyGameState(seedSource, gameState) {
    localStorage.setItem(getDailySaveKey(seedSource), JSON.stringify(gameState));
}

function setGuessControlsDisabled(disabled) {
    document.getElementById('guessYear').disabled = disabled;
    document.getElementById('guessEra').disabled = disabled;
    const guessBtn = document.querySelector("button[onclick='handleGuess()']");
    if (guessBtn) guessBtn.disabled = disabled;
}

async function init() {
try {
    const response = await fetch('challenges.json');
    if (!response.ok) throw new Error("File not found");
    allChallenges = await response.json();
    
    // Clean up old daily save games
    cleanupOldDailySaves();
    
    const params = new URLSearchParams(window.location.search);
    mode = params.get('mode') === 'infinite' ? 'infinite' : 'daily';
    loadEasyModePref();
    if (mode === 'daily') {
        currentSeed = new Date().toDateString();
        const dailyIndex = getDailyChallengeIndex(currentSeed, allChallenges.length);
        currentChallenge = allChallenges[dailyIndex];
        // 2. NOW CHECK FOR SAVED STATE
        const gameState = loadDailyGameState(currentSeed);
        if (gameState) {
            guesses = gameState.guesses || [];
            attempts = gameState.attempts;
            won = gameState.won;
            guessHistory = gameState.guessHistory || [];
            visibleFacts = gameState.visibleFacts || 1;

            renderSavedGuesses();
            if (won || attempts <= 0) {
                isGameOver = true;
                setupGame(); // Render facts before showing results
                showAlreadyPlayed();
                // Disable game controls
                setGuessControlsDisabled(true);
                return;
            }
        }
    }
    setupGame();
} catch (e) {
    console.log(e)
    document.getElementById('facts-area').innerHTML = `<div class="alert alert-danger">Error loading data.</div>`;
}
}

// Clean up old daily save games (keep only today's)
function cleanupOldDailySaves() {
    const today = new Date().toDateString();
    const todayKey = 'chronos_save_' + today;
    
    // Get all localStorage keys
    const keysToRemove = [];
    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        // Find all chronos_save_ keys that aren't today's
        if (key && key.startsWith('chronos_save_') && key !== todayKey) {
            keysToRemove.push(key);
        }
    }
    
    // Remove old save games
    keysToRemove.forEach(key => localStorage.removeItem(key));
    
    if (keysToRemove.length > 0) {
        console.log(`Cleaned up ${keysToRemove.length} old daily save game(s)`);
    }
}

// Save the preference
function saveEasyModePref() {
    const isEasy = document.getElementById('easyModeToggle').checked;
    localStorage.setItem('chronos_easy_mode', isEasy);
}

// Load the preference on startup
function loadEasyModePref() {
    const savedPref = localStorage.getItem('chronos_easy_mode');
    if (savedPref !== null) {
        // localStorage stores everything as strings, so we compare to "true"
        document.getElementById('easyModeToggle').checked = (savedPref === 'true');
    }
}

function showAlreadyPlayed() {
    document.getElementById('game-view').classList.add('hidden');
    document.getElementById('result-view').classList.remove('hidden');
    document.getElementById('btn-view-results').classList.remove('hidden');
    document.getElementById('result-title').innerText = "Daily Complete!";
    // Add the timer display here
    document.getElementById('result-text').innerHTML = `
        <div class="mb-3">You've already played today's challenge.</div>
        <div class="small text-muted">Next Daily Challenge in:</div>
        <div id="timer" class="fw-bold fs-3 text-primary">00:00:00</div>
    `;
    startTimer();
}

// --- Countdown Timer Logic ---
function startTimer() {
    updateTimer();
    setInterval(updateTimer, 1000);
}

function updateTimer() {
    const timerEl = document.getElementById('timer');
    if (!timerEl) return;

    const now = new Date();
    const tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(0, 0, 0, 0);

    const diff = tomorrow - now;
    
    const hours = Math.floor(diff / (1000 * 60 * 60)).toString().padStart(2, '0');
    const mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60)).toString().padStart(2, '0');
    const secs = Math.floor((diff % (1000 * 60)) / 1000).toString().padStart(2, '0');

    timerEl.innerText = `${hours}:${mins}:${secs}`;
}

function setupGame() {
    if (mode === 'daily') {
        // Daily mode already set currentChallenge above
    } else {
        currentChallenge = allChallenges[Math.floor(Math.random() * allChallenges.length)];
        document.getElementById('mode-badge').innerText = "Infinite Mode";
    }
    renderFacts();
}

let visibleFacts = 1; // Start with one fact

function renderFacts() {
    const factsArea = document.getElementById('facts-area');
    if (!factsArea || !currentChallenge) return;
    
    const typeLabel = currentChallenge.t.charAt(0).toUpperCase() + currentChallenge.t.slice(1); 
    factsArea.innerHTML = `<p class="text-muted text-uppercase small fw-bold">Target Type: ${typeLabel}</p>`;
    currentChallenge.f.forEach((fact, index) => {
        // Trim to first sentence in the browser
        const firstSentence = fact.split(/(?<=[.!?])\s+/)[0];
        
        const isHidden = index >= visibleFacts ? 'hidden' : '';
        factsArea.innerHTML += `
            <div class="event-card fact-item ${isHidden}" id="fact-${index}">
                ${firstSentence}
            </div>`;
    });

    // Add Hint Button if there are more facts to show
    if (visibleFacts < currentChallenge.f.length) {
        factsArea.innerHTML += `
            <button id="hint-btn" class="btn btn-sm btn-outline-info mt-2" onclick="showHint()">
                💡 Reveal another clue
            </button>`;
    }
}

function renderSavedGuesses() {
    const feedbackList = document.getElementById('feedback-list');
    if (!feedbackList) return;
    feedbackList.innerHTML = ''; 

    [...guesses].forEach(g => {
        const item = document.createElement('div');
        const isLightColor = ['#ffc107', '#0dcaf0', '#f8f9fa'].includes(g.config.color);
        const textColor = isLightColor ? 'text-dark' : 'text-white';
        
        // Using the slimmer py-2 padding and full width
        item.className = `feedback-item py-2 px-3 mb-2 rounded shadow-sm d-flex justify-content-between align-items-center ${textColor}`;
        item.style.backgroundColor = g.config.color;

        item.innerHTML = `
            <div class="d-flex align-items-center">
                <span class="fw-bold fs-5 me-3">${g.year} ${g.era}</span>
                <span class="fw-medium" style="font-size: 0.9rem;">${g.config.label}</span>
            </div>
            <div class="fs-4">${g.config.emoji}</div>
        `;
        feedbackList.prepend(item);
    });
}

function getDisplayName(challenge) {
    const { y, e, t } = challenge;
    
    if (t === 'year') return `Year: ${y} ${e}`;
    
    if (t === 'decade') {
        const decadeStart = Math.floor(y / 10) * 10;
        return `The ${decadeStart}s ${e}`;
    }
    
    if (t === 'century') {
        const century = Math.floor(y / 100) + 1;
        const s = ["th", "st", "nd", "rd"], v = century % 100;
        const suffix = s[(v - 20) % 10] || s[v] || s[0];
        return `The ${century}${suffix} Century ${e}`;
    }
}

function getOrdinalSuffix(n) {
    let s = ["th", "st", "nd", "rd"],
        v = n % 100;
    return s[(v - 20) % 10] || s[v] || s[0];
}

function showHint() {
    const nextFact = document.getElementById(`fact-${visibleFacts}`);
    if (nextFact) {
        nextFact.classList.remove('hidden');
        visibleFacts++;
    }
    // Remove button if no more facts
    if (visibleFacts >= currentChallenge.f.length) {
        document.getElementById('hint-btn')?.remove();
    }
}

function handleGuess() {  
if (isGameOver) return;

const gYearInput = document.getElementById('guessYear');
const gYear = parseInt(gYearInput.value);
const gEra = document.getElementById('guessEra').value;
const isEasyMode = document.getElementById('easyModeToggle').checked;

if (isNaN(gYear)) {
    alert("Please enter a valid year.");
    return;
}

const targetY = currentChallenge.y;
const targetE = currentChallenge.e;
const type = currentChallenge.t;

let diff = 0;
let won = false;

// Convert to absolute years (BC is negative) for math
const tVal = targetE === 'BC' ? -targetY : targetY;
const gVal = gEra === 'BC' ? -gYear : gYear;

// Calculate ranges
const span = (type === 'decade') ? 9 : (type === 'century' ? 99 : 0);
const rangeStart = tVal;
const rangeEnd = tVal + (targetE === 'BC' ? -span : span);

const minRange = Math.min(rangeStart, rangeEnd);
const maxRange = Math.max(rangeStart, rangeEnd);

// Winning Logic
if (gVal >= minRange && gVal <= maxRange) {
    diff = 0;
    won = true;
} else {
    diff = gVal < minRange ? minRange - gVal : gVal - maxRange;
}

// Adjust for "Year 0" gap
if ((tVal < 0 && gVal > 0) || (tVal > 0 && gVal < 0)) diff -= 1;

const config = getFeedbackConfig(diff, won);

// Store the guess for restoration
guesses.push({ year: gYear, era: gEra, config: config });

// --- EASY MODE HINT LOGIC ---
let hintArrow = "";
if (isEasyMode && !won) {
    // If guess is -500 (500 BC) and target is -400 (400 BC), -500 < -400, so "Later" (More modern)
    hintArrow = gVal < minRange ? " ⬆️ Later" : " ⬇️ Earlier";
}

// Add the emoji to guess history for sharing
guessHistory.push(config.emoji);

// --- Add to UI ---
const feedbackList = document.getElementById('feedback-list');
const item = document.createElement('div');

const isLightColor = ['#ffc107', '#0dcaf0', '#f8f9fa'].includes(config.color);
const textColor = isLightColor ? 'text-dark' : 'text-white';

item.className = `feedback-item p-3 mb-2 rounded shadow-sm d-flex justify-content-between align-items-center ${textColor}`;
item.style.backgroundColor = config.color;

item.innerHTML = `
    <div>
        <span class="fw-bold fs-5">${gYear} ${gEra}</span>
        <span class="ms-3 fw-medium">${config.label}</span>
    </div>
    <div class="d-flex align-items-center">
        ${hintArrow ? `<span class="badge bg-dark me-2">${hintArrow}</span>` : ''}
        <div class="fs-3">${config.emoji}</div>
    </div>
`;

feedbackList.prepend(item);
 if (won) {
        // 1. Add the pulse class to the specific item we just created
        item.classList.add('pulse-win');
        // Disable to prevent spamming during the animation
    setGuessControlsDisabled(true);

        // 3. Wait 3 seconds before showing the Game Over/Victory screen
        setTimeout(() => {
            endGame(true); // This triggers the fireworks
        }, 1200);
    } else {
        attempts--;
        document.getElementById('attempts-left').innerText = attempts;
        if (attempts <= 0) endGame(false);
    }
    
    gYearInput.value = ''; // Clear input for next guess
    
    // Save game state after each guess in daily mode
    if (mode === 'daily') {
        const gameState = {
            guesses: guesses,
            attempts: attempts,
            won: won,
            guessHistory: guessHistory,
            visibleFacts: visibleFacts
        };
        saveDailyGameState(currentSeed, gameState);
    }
}

function getFeedbackConfig(diff, won) {
    if (won || diff === 0) {
        return { label: "Correct!", color: "#198754", emoji: "✅" };
    }
    
    // 1-2 Years: Burning
    if (diff <= 2) {
        return { label: "1-2 yrs (Burning!)", color: "#dc3545", emoji: "🔥" };
    } 
    // 3-10 Years: Hot
    if (diff <= 10) {
        return { label: "3-10 yrs (Hot)", color: "#fd7e14", emoji: "♨️" };
    } 
    // 11-40 Years: Warm
    if (diff <= 40) {
        return { label: "11-40 yrs (Warm)", color: "#ffc107", emoji: "☀️" };
    } 
    // 41-200 Years: Chilly
    if (diff <= 200) {
        return { label: "41-200 yrs (Chilly)", color: "#0dcaf0", emoji: "🧊" };
    } 
    // 201-1000 Years: Cold
    if (diff <= 1000) {
        return { label: "201-1000 yrs (Cold)", color: "#0d6efd", emoji: "❄️" };
    } 
    // 1000+ Years: Freezing
    return { label: "1000+ yrs (Freezing)", color: "#6c757d", emoji: "🌌" };
}

function addFeedbackUI(year, era, cfg) {
    const row = document.createElement('div');
    row.className = 'guess-row';
    row.style.backgroundColor = cfg.color;
    row.innerHTML = `<span>${year} ${era}</span> <span>${cfg.label}</span>`;
    document.getElementById('feedback-list').prepend(row);
}

function endGame(winStatus, isRestoring = false) {
    if (!currentChallenge) return;

    const resultYear = document.getElementById('result-year');
    if (resultYear) {
        resultYear.innerText = `${currentChallenge.y} ${currentChallenge.e}`;
    }
    isGameOver = true;
    won = winStatus; // Ensure global won is set
    document.getElementById('game-view').classList.add('hidden');
    document.getElementById('result-view').classList.remove('hidden');
    document.getElementById('btn-view-results').classList.remove('hidden');
    
    if (winStatus) {
        document.getElementById('result-title').innerText = "🏆 Victory!";
        const resultCard = document.getElementById('result-view');
        resultCard.classList.add('victory-card-active');
        // ONLY launch fireworks if they actually just won (not on refresh)
        if (!isRestoring) {
                launchFireworks();
            }
        } else {
            document.getElementById('result-title').innerText = "⌛ Time's Up!";
    }
    // Use formatHistoryDate here to ensure "1333" becomes "1330s" for the reveal
    const displayDate = formatHistoryDate(currentChallenge.y, currentChallenge.e, currentChallenge.t);
    document.getElementById('result-text').innerHTML = `The target was <b>${displayDate}</b>.`;

    if (!isRestoring) {
        console.log("Saving game result to history.");
        if (mode === 'daily') {
            const dailyResult = { 
                won: won, 
                score: guessHistory.length, 
                emojis: guessHistory 
            };
            localStorage.setItem('chronos_daily_result', JSON.stringify(dailyResult));
        }
        saveHistory(won);
    }
  
    const history = JSON.parse(localStorage.getItem('chronos_history_v3') || '[]');    
    calculateStreak(history);
    document.getElementById('result-text').innerHTML += `
        <div class="mt-2 small">Current Streak: <b>${currentStreak}</b> 🔥</div>
    `;
    if (mode === 'daily') startTimer();
}

function viewGame() {
    document.getElementById('result-view').classList.add('hidden');
    document.getElementById('game-view').classList.remove('hidden');
    document.getElementById('btn-view-results').classList.remove('hidden');
    
    // Disable game controls when viewing completed game
    if (isGameOver) {
        setGuessControlsDisabled(true);
    }
}

function viewResults() {
    document.getElementById('game-view').classList.add('hidden');
    document.getElementById('result-view').classList.remove('hidden');
}

function saveHistory(wonStatus) {
    const history = JSON.parse(localStorage.getItem('chronos_history_v3') || '[]');
    const today = new Date();
    const dateStr = String(today.getDate()).padStart(2, '0') + '/' + 
                    String(today.getMonth() + 1).padStart(2, '0') + '/' + 
                    today.getFullYear();
    history.push({ 
        d: dateStr, 
        y: currentChallenge.y,  // Store as number
        e: currentChallenge.e,  // Store as "AD" or "BC"
        t: currentChallenge.t,  // Store as "year", "decade", or "century"
        w: wonStatus, 
        s: guessHistory.length,
        m: mode 
    });
    localStorage.setItem('chronos_history_v3', JSON.stringify(history));
}

function shareResult() {
    let finalWon = won;
    let emojis = guessHistory.join('');
    let finalScore = guessHistory.length; // Change this line

    if (mode === 'daily' && guessHistory.length === 0) {
        const saved = JSON.parse(localStorage.getItem('chronos_daily_result'));
        if (saved) {
            finalWon = saved.won;
            finalScore = saved.score;
            emojis = saved.emojis.join('');
        }
    }
    const scoreDisplay = finalWon ? finalScore : "X";

    // Get the formatted date for the share message
    const displayDate = formatHistoryDate(currentChallenge.y, currentChallenge.e, currentChallenge.t);

    const text = `⌛ Chronos ${mode === 'daily' ? 'Daily' : 'Infinite'}\n` +
                `${emojis}\n` +
                `Result: ${scoreDisplay}/8\n` +
                window.location.href;

    navigator.clipboard.writeText(text).then(() => {
        alert("Result copied to clipboard!");
    });
}

// Load fireworks preference
function loadFireworksPref() {
    return localStorage.getItem('chronos_fireworks_mode') || 'low';
}

// Save fireworks preference
function saveFireworksPref(mode) {
    localStorage.setItem('chronos_fireworks_mode', mode);
}

// Update your toggleHistory function's modal footer or top
function toggleHistory() {
    const history = JSON.parse(localStorage.getItem('chronos_history_v3') || '[]');
    calculateStreak(history); 

    const dailyGames = history.filter(g => g.m === 'daily');
    const infiniteGames = history.filter(g => g.m === 'infinite');

    const statsHeader = `
        <div class="row text-center mb-4">
            <div class="col">
                <div class="small text-muted text-uppercase">Daily</div>
                <div class="h4 mb-0">${dailyGames.length} Played</div>
                <div class="fw-bold text-primary">${currentStreak} Day Streak 🔥</div>
            </div>
            <div class="col border-start">
                <div class="small text-muted text-uppercase">Infinite</div>
                <div class="h4 mb-0">${infiniteGames.length} Played</div>
                <div class="text-success">${infiniteGames.filter(g => g.w).length} Wins ✅</div>
            </div>
        </div>
    `;

    const fireworksMode = loadFireworksPref();
    const tableHtml = `
        <ul class="nav nav-tabs" id="historyTabs">
            <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#h-daily">Daily</button></li>
            <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#h-inf">Infinite</button></li>
            <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#h-settings">🎆 Fireworks</button></li>
        </ul>
        <div class="tab-content pt-3" style="max-height: 400px; overflow-y: auto;">
            <div class="tab-pane fade show active" id="h-daily">${generateTable(dailyGames.reverse())}</div>
            <div class="tab-pane fade" id="h-inf">${generateTable(infiniteGames.reverse())}</div>
            <div class="tab-pane fade" id="h-settings">
                <h5 class="mb-3">Victory Fireworks Intensity</h5>
                <p class="text-muted small">Choose how extravagant your victory celebration should be!</p>
                <div class="list-group">
                    <label class="list-group-item d-flex gap-3 align-items-center" style="cursor: pointer;">
                        <input class="form-check-input flex-shrink-0" type="radio" name="fireworks" value="low" ${fireworksMode === 'low' ? 'checked' : ''} onchange="saveFireworksPref('low')">
                        <div>
                            <strong>🎇 Low (Default)</strong>
                            <div class="small text-muted">Gentle celebration - first few seconds only</div>
                        </div>
                    </label>
                    <label class="list-group-item d-flex gap-3 align-items-center" style="cursor: pointer;">
                        <input class="form-check-input flex-shrink-0" type="radio" name="fireworks" value="high" ${fireworksMode === 'high' ? 'checked' : ''} onchange="saveFireworksPref('high')">
                        <div>
                            <strong>🎆 High</strong>
                            <div class="small text-muted">Full display - the complete show</div>
                        </div>
                    </label>
                    <label class="list-group-item d-flex gap-3 align-items-center" style="cursor: pointer;">
                        <input class="form-check-input flex-shrink-0" type="radio" name="fireworks" value="extreme" ${fireworksMode === 'extreme' ? 'checked' : ''} onchange="saveFireworksPref('extreme')">
                        <div>
                            <strong>💥 Extreme</strong>
                            <div class="small text-muted">Over the top - absolute chaos and glory!</div>
                        </div>
                    </label>
                </div>
            </div>
        </div>
    `;

    const clearBtn = `<div class="mt-4 pt-3 border-top text-center">
        <button class="btn btn-sm btn-outline-danger" onclick="clearAllData()">Clear All Game Data</button>
    </div>`;

    document.getElementById('history-content').innerHTML = statsHeader + tableHtml + clearBtn;
    new bootstrap.Modal(document.getElementById('historyModal')).show();
}

function generateTable(data) {
    if (!data || data.length === 0) return "<p class='text-center p-3 text-muted'>No games played yet.</p>";
    
    let t = '<table class="table table-hover align-middle"><thead><tr><th>Date</th><th>Target</th><th>Result</th></tr></thead><tbody>';
    
    data.forEach(g => {
        // Handle potential undefined issues from old vs new data
        let displayDate = "Unknown";
        
        if (g.y && g.e && g.t) {
            // New data format: format it nicely
            displayDate = formatHistoryDate(g.y, g.e, g.t);
        } else if (g.y) {
            // Old data format (just year/era string)
            displayDate = g.y; 
        }

        const resultText = g.w ? `${g.s} ${g.s === 1 ? 'guess' : 'guesses'}` : 'Failed';
        const badgeClass = g.w ? 'bg-success' : 'bg-danger';

        t += `<tr>
                <td class="small">${g.d}</td>
                <td><span class="badge bg-secondary">${displayDate}</span></td>
                <td><span class="badge ${badgeClass}">${resultText}</span></td>
            </tr>`;
    });
    return t + '</tbody></table>';
}

function formatHistoryDate(year, era, timeframe) {
    if (timeframe === 'year') return `${year} ${era}`;
    
    if (timeframe === 'decade') {
        const decadeStart = Math.floor(year / 10) * 10;
        return `${decadeStart}s ${era}`;
    }
    
    if (timeframe === 'century') {
        const cent = Math.floor(year / 100) + 1;
        const s = ["th", "st", "nd", "rd"], v = cent % 100;
        const suffix = s[(v - 20) % 10] || s[v] || s[0];
        return `${cent}${suffix} Century ${era}`;
    }
    return `${year} ${era}`;
}

function calculateStreak(history) {
    // 1. Get only successful Daily wins
    const dailyWins = history.filter(g => g.m === 'daily' && g.w === true);
    if (dailyWins.length === 0) {
        currentStreak = 0;
        return;
    }
    // 2. Extract unique dates and sort them NEWEST to OLDEST
    // Using a Set handles cases where a user might have two entries for one day
    const uniqueDates = [...new Set(dailyWins.map(g => g.d))];
    
    // Sort by converting DD/MM/YYYY to comparable format
    uniqueDates.sort((a, b) => {
        const [dayA, monthA, yearA] = a.split('/').map(Number);
        const [dayB, monthB, yearB] = b.split('/').map(Number);
        const dateA = new Date(yearA, monthA - 1, dayA);
        const dateB = new Date(yearB, monthB - 1, dayB);
        return dateB - dateA; // Newest first
    });

    let streak = 0;
    const today = new Date();
    const todayStr = String(today.getDate()).padStart(2, '0') + '/' + 
                     String(today.getMonth() + 1).padStart(2, '0') + '/' + 
                     today.getFullYear();
    
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayStr = String(yesterday.getDate()).padStart(2, '0') + '/' + 
                         String(yesterday.getMonth() + 1).padStart(2, '0') + '/' + 
                         yesterday.getFullYear();

    // 3. Check if the most recent win is today OR yesterday
    // If the most recent win is older than yesterday, the streak is dead.
    if (uniqueDates[0] !== todayStr && uniqueDates[0] !== yesterdayStr) {
        currentStreak = 0;
        return;
    }

    // 4. Walk through the sorted dates and count consecutive days
    // Parse DD/MM/YYYY format
    const parts = uniqueDates[0].split('/');
    let expectedDate = new Date(parts[2], parts[1] - 1, parts[0]);
    expectedDate.setHours(0, 0, 0, 0); // Normalize to midnight

    for (let dateStr of uniqueDates) {
        const dateParts = dateStr.split('/');
        const actualDate = new Date(dateParts[2], dateParts[1] - 1, dateParts[0]);
        actualDate.setHours(0, 0, 0, 0); // Normalize to midnight
        
        if (actualDate.getTime() === expectedDate.getTime()) {
            streak++;
            // Move expected date back by one day
            expectedDate.setDate(expectedDate.getDate() - 1);
        } else {
            // There is a gap in the dates
            break;
        }
    }
    currentStreak = streak;
}

function clearAllData() {
    if (confirm("Are you sure? This will delete ALL history, streaks, and saved games!")) {
        // Clear all known keys
        const keys = [
            'chronos_history_v2', 
            'chronos_history_v3', 
            'chronos_daily_result',
            'chronos_easy_mode',
            'chronos_fireworks_mode',
            'chronos_save_' + currentSeed
        ];
        keys.forEach(k => localStorage.removeItem(k));
        
        // Remove ALL daily save games (chronos_save_*)
        const keysToRemove = [];
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key && key.startsWith('chronos_save_')) {
                keysToRemove.push(key);
            }
        }
        keysToRemove.forEach(k => localStorage.removeItem(k));
        
        alert("All data cleared.");
        // Redirect to Daily mode to start fresh
        window.location.href = window.location.pathname; 
    }
}

function playAgain() {
    // If we are in Daily mode and finished, 'Play Again' should take us to Infinite
    if (mode === 'daily') {
        window.location.href = "?mode=infinite";
    } else {
        // If in Infinite mode, check if a new daily is available
        const currentSeed = new Date().toDateString();
        const saved = localStorage.getItem('chronos_save_' + currentSeed);
        
        // If no saved game for today, or if the saved game is not finished, go to daily
        if (!saved) {
            window.location.href = window.location.pathname; // Go to daily mode
        } else {
            const gameState = JSON.parse(saved);
            const isFinished = gameState.won || gameState.attempts <= 0;
            
            if (!isFinished) {
                // Daily game in progress, go to it
                window.location.href = window.location.pathname;
            } else {
                // Daily already completed, stay in infinite and reset
                resetGameState();
            }
        }
    }
}

function resetGameState() {
    // Reset variables
    attempts = 8;
    isGameOver = false;
    guessHistory = [];
    won = false;
    visibleFacts = 1;

    // Reset UI
    document.getElementById('attempts-left').innerText = attempts;
    document.getElementById('feedback-list').innerHTML = '';
    document.getElementById('game-view').classList.remove('hidden');
    document.getElementById('result-view').classList.add('hidden');
    document.getElementById('btn-view-results').classList.add('hidden');
    document.getElementById('guessYear').value = '';
    setGuessControlsDisabled(false);
    // Pick a new challenge
    setupGame();
}
function launchFireworks() {
    const fireworksMode = loadFireworksPref();
    
    if (fireworksMode === 'low') {
        launchFireworksLow();
    } else if (fireworksMode === 'extreme') {
        launchFireworksExtreme();
    } else {
        launchFireworksHigh();
    }
}

function launchFireworksLow() {
    // Enhanced gentle celebration for 5 seconds
    const duration = 5 * 1000;
    const animationEnd = Date.now() + duration;
    
    // Gentle shimmer throughout
    const shimmer = setInterval(() => {
        if (Date.now() > animationEnd) {
            clearInterval(shimmer);
            return;
        }
        confetti({
            particleCount: 3,
            angle: 90,
            spread: 60,
            origin: { y: 0.8 },
            colors: ['#ffffff', '#ffc107', '#28a745'],
            gravity: 0.5,
            scalar: 1
        });
    }, 250);
    
    // A couple of gentle bursts
    [1500, 3000].forEach((delay) => {
        setTimeout(() => {
            confetti({
                particleCount: 40,
                spread: 70,
                origin: { y: 0.6 },
                colors: ['#ffc107', '#ffffff'],
                gravity: 0.8,
                scalar: 1.2
            });
        }, delay);
    });
}

function launchFireworksHigh() {
    const duration = 12 * 1000; // Increased to 12 seconds for the full symphony
    const animationEnd = Date.now() + duration;

    // --- ACT 1: THE TENSE OPENING (Gentle Shimmer) ---
    const act1 = setInterval(() => {
        confetti({
            particleCount: 2,
            angle: 90,
            spread: 50,
            origin: { y: 0.8 },
            colors: ['#ffffff', '#ffc107'],
            gravity: 0.5,
            scalar: 0.8
        });
    }, 300);

    // --- ACT 2: THE CANNON BLASTS (Heavy Hits) ---
    // These trigger at 3s, 4s, 5s, 6s
    [3000, 4000, 5000, 6000].forEach((delay) => {
        setTimeout(() => {
            // Massive central blast (The Cannon)
            confetti({
                particleCount: 150,
                spread: 100,
                origin: { y: 0.5 },
                scalar: 2,
                colors: ['#ff0000', '#ffffff', '#ffc107'],
                shapes: ['star']
            });
            // Haptic-style screen flash (if you want to add a white overlay briefly)
        }, delay);
    });

    // --- ACT 3: THE FLANKING MANEUVER (Rapid Side Fire) ---
    setTimeout(() => {
        const sideFire = setInterval(() => {
            if (Date.now() > animationEnd - 4000) return clearInterval(sideFire);
            
            confetti({
                particleCount: 30,
                angle: 60,
                spread: 55,
                origin: { x: 0, y: 0.6 },
                colors: ['#28a745', '#ffc107']
            });
            confetti({
                particleCount: 30,
                angle: 120,
                spread: 55,
                origin: { x: 1, y: 0.6 },
                colors: ['#28a745', '#ffc107']
            });
        }, 400);
    }, 6000);

    // --- ACT 4: THE GRAND FINALE (Total Chaos) ---
    setTimeout(() => {
        clearInterval(act1); // Stop the shimmer
        const finale = setInterval(() => {
            const timeLeft = animationEnd - Date.now();
            if (timeLeft <= 0) return clearInterval(finale);

            confetti({
                particleCount: 80,
                spread: 360,
                startVelocity: 45,
                origin: { x: Math.random(), y: Math.random() - 0.2 },
                colors: ['#ff0000', '#00ff00', '#0000ff', '#ffff00', '#ffffff'],
                gravity: 1,
                scalar: 1.2
            });
        }, 150);
    }, 8500);
    // --- THE GRAND SLAM (The Final Note) ---
setTimeout(() => {
    // 1. The "Big One" - 500 particles at once
    confetti({
        particleCount: 500,
        spread: 200,
        origin: { y: 0.4 },
        scalar: 2.5, // Extra large chunks
        colors: ['#ffc107', '#ffffff', '#ff0000', '#ffd700'], // Gold and Fire
        ticks: 400, // Makes them last longer on screen
        gravity: 0.6, // They float down slowly
        drift: 0,
        shapes: ['star']
    });

    // 2. The "Echo" - A wide, thin shimmer across the whole horizon
    confetti({
        particleCount: 100,
        spread: 360,
        origin: { y: 0.3 },
        scalar: 0.5,
        colors: ['#ffffff'],
        ticks: 500,
        gravity: 0.2, // Very floaty
    });

    // 3. Final Screen Flash
    document.body.style.transition = "background-color 0.1s";
    document.body.style.backgroundColor = "white";
    setTimeout(() => {
        document.body.style.backgroundColor = "";
    }, 150);

}, 12000); // Triggers at the 12-second mark
}

function launchFireworksExtreme() {
    const duration = 20 * 1000; // Extended to 20 seconds of pure chaos
    const animationEnd = Date.now() + duration;

    // --- PHASE 1: OVERWHELMING OPENING (Triple Shimmer) ---
    const phase1 = setInterval(() => {
        confetti({
            particleCount: 8,
            angle: 90,
            spread: 80,
            origin: { y: 0.8 },
            colors: ['#ffffff', '#ffc107', '#ff0000', '#00ff00'],
            gravity: 0.3,
            scalar: 1.2
        });
    }, 150);

    // --- PHASE 2: MEGA CANNON BLASTS (More frequent, bigger) ---
    [2000, 4000, 6000, 8000].forEach((delay) => {
        setTimeout(() => {
            // Alternating pattern: single center, then side splits
            const pattern = (delay / 2000) % 2;
            if (pattern === 0) {
                // Center mega blast
                confetti({
                    particleCount: 250,
                    spread: 140,
                    origin: { x: 0.5, y: 0.4 },
                    scalar: 2.8,
                    colors: ['#ff0000', '#ffd700', '#ffffff'],
                    shapes: ['star'],
                    gravity: 0.9,
                    startVelocity: 60
                });
            } else {
                // Left and right dual blasts
                [0.2, 0.8].forEach((xPos) => {
                    confetti({
                        particleCount: 150,
                        spread: 100,
                        origin: { x: xPos, y: 0.5 },
                        scalar: 2.2,
                        colors: ['#00ff00', '#0000ff', '#ffc107', '#ff69b4'],
                        shapes: ['circle', 'star'],
                        gravity: 0.7,
                        startVelocity: 50
                    });
                });
            }
            // Screen pulse
            document.body.style.transition = "background-color 0.05s";
            document.body.style.backgroundColor = `rgba(${Math.random()*255}, ${Math.random()*255}, ${Math.random()*255}, 0.1)`;
            setTimeout(() => {
                document.body.style.backgroundColor = "";
            }, 100);
        }, delay);
    });

    // --- PHASE 3: QUADRUPLE FLANKING (All four corners) ---
    setTimeout(() => {
        const sideFire = setInterval(() => {
            if (Date.now() > animationEnd - 8000) return clearInterval(sideFire);
            
            // All four corners firing
            [[0, 60], [1, 120], [0, 300], [1, 240]].forEach(([x, angle]) => {
                confetti({
                    particleCount: 40,
                    angle: angle,
                    spread: 70,
                    origin: { x: x, y: 0.6 },
                    colors: ['#28a745', '#ffc107', '#ff69b4', '#00ffff'],
                    scalar: 1.5
                });
            });
        }, 200);
    }, 8000);

    // --- PHASE 4: ABSOLUTE CHAOS (Total madness) ---
    setTimeout(() => {
        clearInterval(phase1);
        const chaos = setInterval(() => {
            const timeLeft = animationEnd - Date.now();
            if (timeLeft <= 0) return clearInterval(chaos);

            // Random explosions everywhere
            confetti({
                particleCount: 120,
                spread: 360,
                startVelocity: 60,
                origin: { x: Math.random(), y: Math.random() },
                colors: ['#ff0000', '#00ff00', '#0000ff', '#ffff00', '#ffffff', '#ff69b4', '#ffa500'],
                gravity: 1.2,
                scalar: 1.8,
                drift: Math.random() * 2 - 1
            });
        }, 80);
    }, 12000);

    // --- PHASE 5: TRIPLE GRAND FINALE ---
    [16000, 17000, 18000].forEach((delay) => {
        setTimeout(() => {
            // Massive explosion
            confetti({
                particleCount: 300,
                spread: 180,
                origin: { y: 0.3 },
                scalar: 3,
                colors: ['#ffc107', '#ffffff', '#ff0000', '#ffd700', '#00ff00'],
                ticks: 500,
                gravity: 0.5,
                shapes: ['star']
            });
            
            // Shockwave effect
            confetti({
                particleCount: 150,
                spread: 360,
                origin: { y: 0.3 },
                scalar: 0.3,
                colors: ['#ffffff'],
                ticks: 600,
                gravity: 0.1,
                startVelocity: 80
            });
            
            // Screen flash
            document.body.style.transition = "background-color 0.08s";
            document.body.style.backgroundColor = "white";
            setTimeout(() => {
                document.body.style.backgroundColor = "";
            }, 120);
        }, delay);
    });

    // --- THE ULTIMATE FINALE ---
    setTimeout(() => {
        confetti({
            particleCount: 1000,
            spread: 1000,
            origin: { x: 0.3, y: 0.3 },
            scalar: 3,
            colors: ['#ffc107', '#ffffff', '#ff0000', '#ffd700', '#ff69b4', '#00ff00', '#0000ff'],
            ticks: 600,
            gravity: 0.4,
            shapes: ['star', 'circle']
        });
    }, 19500);

    setTimeout(() => {
        confetti({
            particleCount: 1000,
            spread: 1000,
            origin: { x: 0.7, y: 0.7 },
            scalar: 3,
            colors: ['#ffc107', '#ffffff', '#ff0000', '#ffd700', '#ff69b4', '#00ff00', '#0000ff'],
            ticks: 600,
            gravity: 0.4,
            shapes: ['star', 'circle']
        });
    }, 20000);
    

    setTimeout(() => {
        confetti({
            particleCount: 1000,
            spread: 1000,
            origin: { x: 0.3, y: 0.7 },
            scalar: 3,
            colors: ['#ffc107', '#ffffff', '#ff0000', '#ffd700', '#ff69b4', '#00ff00', '#0000ff'],
            ticks: 600,
            gravity: 0.4,
            shapes: ['star', 'circle']
        });
    }, 20500);

    setTimeout(() => {
        confetti({
            particleCount: 1000,
            spread: 1000,
            origin: { x: 0.7, y: 0.3 },
            scalar: 3,
            colors: ['#ffc107', '#ffffff', '#ff0000', '#ffd700', '#ff69b4', '#00ff00', '#0000ff'],
            ticks: 600,
            gravity: 0.4,
            shapes: ['star', 'circle']
        });
    }, 21000);

     setTimeout(() => {
        confetti({
            particleCount: 1000,
            spread: 1000,
            origin: { y: 0.4 },
            scalar: 3,
            colors: ['#ffc107', '#ffffff', '#ff0000', '#ffd700', '#ff69b4', '#00ff00', '#0000ff'],
            ticks: 600,
            gravity: 0.4,
            shapes: ['star', 'circle']
        });
    }, 21500);
}

init();
