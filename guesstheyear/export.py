import sqlite3
import json
import re
import random

def clean_event_text(text, target_year):
    """Protects titles, removes numbers +/- 100 years, and heals formatting."""
    # 1. Protect common abbreviations from being treated as sentence endings
    text = re.sub(r'\bSt\.\s', 'St_PLACEHOLDER ', text)
    text = re.sub(r'\bMt\.\s', 'Mt_PLACEHOLDER ', text)
    text = re.sub(r'\bDr\.\s', 'Dr_PLACEHOLDER ', text)
    text = re.sub(r'\bCapt\.\s', 'Capt_PLACEHOLDER ', text)

    # 2. Year/Era filtering
    text = re.sub(r'\b\d+(?:st|nd|rd|th)\s+century\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(AD|BC|BCE|CE)\b', '', text, flags=re.IGNORECASE)

    def year_replacer(match):
        val = int(match.group())
        if (target_year - 100) <= val <= (target_year + 100):
            return "" 
        return match.group() 

    text = re.sub(r'\b\d{1,4}\b', year_replacer, text)
    
    # 3. Cleanup "wreckage"
    text = re.sub(r'\(\s*[,.]?\s*\w*\s*\)', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\s+([,.])', r'\1', text)
    
    # 4. Restore Protected Titles
    text = text.replace('St_PLACEHOLDER', 'St.')
    text = text.replace('Mt_PLACEHOLDER', 'Mt.')
    text = text.replace('Dr_PLACEHOLDER', 'Dr.')
    text = text.replace('Capt_PLACEHOLDER', 'Capt.')

    text = text.strip()
    if text:
        text = text[0].upper() + text[1:]
        if not text.endswith('.'):
            text += '.'
    return text

def export_challenges(db_path, output_file):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # We need at least 2 events per year now to make a paired challenge
    query = """
        SELECT year, era, timeframe_type, GROUP_CONCAT(event, '||') as events
        FROM events
        GROUP BY year, era, timeframe_type
        HAVING COUNT(*) >= 2
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
            t = re.sub(r'^c\.\s*[\u2014\-]?\s*', '', f.strip())
            t = clean_event_text(t, target_year)
            if t and len(t) > 25:
                clean_events.append(t)

        if len(clean_events) < 2:
            continue
            
        # Select exactly 2 random facts and combine them
        selected = random.sample(clean_events, 2)
        combined_fact = " ".join(selected)

        # Structure the entry
        entry = {"y": year, "e": era, "t": timeframe, "f": [combined_fact]}

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
    
    # --- RESTORED COVERAGE ANALYSIS ---

    def print_gaps(seen_set, start_val, end_val, step, era_label):
        missing = []
        for val in range(start_val, end_val + 1, step):
            key = f"{val}-{era_label}"
            if key not in seen_set:
                missing.append(f"{val} {era_label}")
        if missing:
            print(f"\nMissing {era_label} units ({step}yr blocks):")
            print(", ".join(missing))
        else:
            print(f"\n✅ No gaps found for {era_label} {step}yr blocks!")

    def analyze_year_gaps(seen_years, start_year, end_year, era_label):
        missing_count = 0
        for y in range(start_year, end_year + 1):
            key = f"{y}-{era_label}"
            if key not in seen_years:
                missing_count += 1
        return missing_count

    seen_years_ad = {f"{c['y']}-AD" for c in final_challenges if c['t'] == 'year' and c['e'] == 'AD'}
    seen_years_bc = {f"{c['y']}-BC" for c in final_challenges if c['t'] == 'year' and c['e'] == 'BC'}

    print("\n--- Coverage Analysis ---")
    print_gaps(seen_decades, 600, 1800, 10, "BC")
    
    ad_miss = analyze_year_gaps(seen_years_ad, 1, 2025, "AD")
    bc_miss = analyze_year_gaps(seen_years_bc, 1, 500, "BC")

    print(f"\n--- Final Export Summary ---")
    print(f"AD Years Missing: {ad_miss} / 2025")
    print(f"BC Years Missing: {bc_miss} / 500")
    print(f"Total Challenges: {len(final_challenges)}")
    
    conn.close()

if __name__ == "__main__":
    export_challenges('wikipedia_history.db', 'challenges.json')