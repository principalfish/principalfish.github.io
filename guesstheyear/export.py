import sqlite3
import json
import re
import random

def get_first_two_sentences(text):
    """Splits text into sentences and returns the first two, respecting St./Mt. abbreviations."""
    # 1. Protect titles before splitting
    text = re.sub(r'\bSt\.\s', 'St_PLACEHOLDER ', text)
    text = re.sub(r'\bMt\.\s', 'Mt_PLACEHOLDER ', text)
    text = re.sub(r'\bDr\.\s', 'Dr_PLACEHOLDER ', text)
    
    # 2. Split by punctuation followed by space and capital letter
    # This is more robust than just splitting by '.'
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    
    # 3. Take the first two
    truncated = " ".join(sentences[:2])
    
    # 4. Restore titles
    truncated = truncated.replace('St_PLACEHOLDER', 'St.')
    truncated = truncated.replace('Mt_PLACEHOLDER', 'Mt.')
    truncated = truncated.replace('Dr_PLACEHOLDER', 'Dr.')
    
    return truncated

def clean_event_text(text, target_year):
    """Cleans years and restricts to two sentences."""
    # Apply sentence truncation first
    text = get_first_two_sentences(text)

    # Year/Era filtering
    text = re.sub(r'\b\d+(?:st|nd|rd|th)\s+century\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(AD|BC|BCE|CE)\b', '', text, flags=re.IGNORECASE)

    def year_replacer(match):
        val = int(match.group())
        if (target_year - 100) <= val <= (target_year + 100):
            return "" 
        return match.group() 

    text = re.sub(r'\b\d{1,4}\b', year_replacer, text)
    
    # Cleanup formatting
    text = re.sub(r'\(\s*[,.]?\s*\w*\s*\)', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\s+([,.])', r'\1', text)
    
    text = text.strip()
    if text:
        text = text[0].upper() + text[1:]
        if not text.endswith('.'):
            text += '.'
    return text

def export_challenges(db_path, output_file):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = """
        SELECT year, era, timeframe_type, GROUP_CONCAT(event, '||') as events
        FROM events
        GROUP BY year, era, timeframe_type
        HAVING COUNT(*) >= 1
    """
    
    cursor.execute(query)
    rows = cursor.fetchall()

    final_challenges = []
    seen_decades = set()
    seen_centuries = set()
    counts = {'year': 0, 'decade': 0, 'century': 0}

    for year, era, timeframe, events_str in rows:
        raw_events = events_str.split('||')
        clean_events = []
        target_year = int(year)
        
        for f in raw_events:
            t = clean_event_text(f, target_year)
            # Basic quality filter
            if t and len(t) > 20:
                clean_events.append(t)

        if not clean_events:
            continue
            
        # Pick up to 5 individual facts
        num_to_pick = min(len(clean_events), 5)
        final_events = random.sample(clean_events, num_to_pick)

        entry = {"y": year, "e": era, "t": timeframe, "f": final_events}

        if timeframe == 'year':
            final_challenges.append(entry)
            counts['year'] += 1
        elif timeframe == 'decade':
            decade_start = (year // 10) * 10
            key = f"{decade_start}-{era}"
            if key not in seen_decades:
                entry["y"] = decade_start
                final_challenges.append(entry)
                seen_decades.add(key)
                counts['decade'] += 1
        elif timeframe == 'century':
            cent_start = (year // 100) * 100
            key = f"{cent_start}-{era}"
            if key not in seen_centuries:
                final_challenges.append(entry)
                seen_centuries.add(key)
                counts['century'] += 1

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(final_challenges, f, indent=2)
    
    # --- ANALYSIS CHECKS ---
    seen_years_ad = {f"{c['y']}-AD" for c in final_challenges if c['t'] == 'year' and c['e'] == 'AD'}
    print(f"\n✅ Export Summary: {len(final_challenges)} challenges.")
    print(f"AD Years Coverage: {len(seen_years_ad)} / 2025")
    
    conn.close()

if __name__ == "__main__":
    export_challenges('wikipedia_history.db', 'challenges.json')