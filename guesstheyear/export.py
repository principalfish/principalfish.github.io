import sqlite3
import json
import re
import random

def clean_event_text(text, target_year):
    """Removes numbers within +/- 100 of the target year and cleans formatting."""
    text = re.sub(r'\b\d+(?:st|nd|rd|th)\s+century\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(AD|BC|BCE|CE)\b', '', text, flags=re.IGNORECASE)

    def year_replacer(match):
        val = int(match.group())
        if (target_year - 100) <= val <= (target_year + 100):
            return "" 
        return match.group() 

    text = re.sub(r'\b\d{1,4}\b', year_replacer, text)
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
            t = re.sub(r'^c\.\s*[\u2014\-]?\s*', '', f.strip())
            t = clean_event_text(t, target_year)
            if not t or len(t) < 15:
                continue
            clean_events.append(t)

        if len(clean_events) < 2:
            continue
            
        num_to_pick = min(len(clean_events), 5)
        final_events = random.sample(clean_events, num_to_pick)

        if timeframe == 'year':
            final_challenges.append({"y": year, "e": era, "t": timeframe, "f": final_events})
            counts['year'] += 1
        elif timeframe == 'decade':
            decade_start = (year // 10) * 10
            key = f"{decade_start}-{era}"
            if key not in seen_decades:
                final_challenges.append({"y": decade_start, "e": era, "t": timeframe, "f": final_events})
                seen_decades.add(key)
                counts['decade'] += 1
        elif timeframe == 'century':
            cent_start = (year // 100) * 100
            key = f"{cent_start}-{era}"
            if key not in seen_centuries:
                final_challenges.append({"y": year, "e": era, "t": timeframe, "f": final_events})
                seen_centuries.add(key)
                counts['century'] += 1

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(final_challenges, f, indent=2)
    
    # --- START OF RESTORED GAP ANALYSIS ---

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

    # Gather years for individual analysis
    seen_years_ad = {f"{c['y']}-AD" for c in final_challenges if c['t'] == 'year' and c['e'] == 'AD'}
    seen_years_bc = {f"{c['y']}-BC" for c in final_challenges if c['t'] == 'year' and c['e'] == 'BC'}

    print("\n--- Timeline Coverage Analysis ---")
    print_gaps(seen_decades, 600, 1800, 10, "BC")
    
    ad_miss = analyze_year_gaps(seen_years_ad, 1, 2025, "AD")
    bc_miss = analyze_year_gaps(seen_years_bc, 1, 500, "BC")

    print(f"\n--- Export Summary ---")
    print(f"AD Years Missing: {ad_miss} / 2025")
    print(f"BC Years Missing: {bc_miss} / 500")
    print(f"Total Years:      {counts['year']}")
    print(f"Total Decades:    {counts['decade']}")
    print(f"Total Centuries:  {counts['century']}")
    print(f"Grand Total:      {len(final_challenges)}")
    
    conn.close()

if __name__ == "__main__":
    export_challenges('wikipedia_history.db', 'challenges.json')