import sqlite3
import json
import re
import random

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
    
    # Initialize our counters 🔢
    counts = {
        'year': 0,
        'decade': 0,
        'century': 0
    }

    for year, era, timeframe, events_str in rows:

        raw_events = events_str.split('||')
        clean_events = []
        
        for f in raw_events:
            text = f.strip()
            # 1. Remove "c. —" or "c. " prefixes at the start
            text = re.sub(r'^c\.\s*[\u2014\-]?\s*', '', text)
            
            # 2. Skip if the text is empty, just "c.", or looks like a broken sentence 
            # (e.g., "It is dated to the summer of .")
            if not text or text.lower() in ['c.', 'c'] or text.endswith('summer of .'):
                continue
            
            clean_events.append(text)

        # Skip this challenge if no valid facts remain
        if not clean_events:
            continue
            
        # RANDOMIZATION: Pick up to 5 random facts from the available pool
        num_to_pick = min(len(clean_events), 5)
        final_events = random.sample(clean_events, num_to_pick)

        if timeframe == 'year':
            final_challenges.append({"y": year, "e": era, "t": timeframe, "f": final_events})
            counts['year'] += 1
            
        elif timeframe == 'decade':
                    # Round down to the nearest 10 (e.g., 1333 becomes 1330)
                    decade_start = (year // 10) * 10
                    key = f"{decade_start}-{era}"
                    
                    if key not in seen_decades:
                        # We store the 'decade_start' as the year so the JS knows 
                        # exactly where the decade begins for its math/display.
                        final_challenges.append({"y": decade_start, "e": era, "t": timeframe, "f": final_events})
                        seen_decades.add(key)
                        counts['decade'] += 1
                
        elif timeframe == 'century':
            cent_num = (year // 100) + 1
            key = f"{cent_num}-{era}"
            if key not in seen_centuries:
                final_challenges.append({"y": year, "e": era, "t": timeframe, "f": final_events})
                seen_centuries.add(key)
                counts['century'] += 1

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(final_challenges, f, indent=2)
        
    conn.close()
    
    
    def print_gaps(seen_set, start_val, end_val, step, era_label):
        missing = []
        # Loop through the timeline based on the step (10 for decades, 100 for centuries)
        for val in range(start_val, end_val + 1, step):
            key = f"{val}-{era_label}"
            if key not in seen_set:
                missing.append(f"{val} {era_label}")
        
        if missing:
            print(f"\nMissing {era_label} units ({step}yr blocks):")
            print(", ".join(missing))
        else:
            print(f"\n✅ No gaps found for {era_label}!")

  
    # For BC Decades (0 to 4000)
    print_gaps(seen_decades, 600, 1800, 10, "BC")
    
    def analyze_year_gaps(seen_years, start_year, end_year, era_label):
            missing_count = 0
            missing_list = []
            
            for y in range(start_year, end_year + 1):
                key = f"{y}-{era_label}"
                if key not in seen_years:
                    missing_count += 1
                    missing_list.append(y)
                    print (key)
                    
            return missing_count, missing_list

    # 1. Create a set specifically for individual years seen
    seen_years_ad = {f"{c['y']}-AD" for c in final_challenges if c['t'] == 'year' and c['e'] == 'AD'}
    seen_years_bc = {f"{c['y']}-BC" for c in final_challenges if c['t'] == 'year' and c['e'] == 'BC'}

    # 2. Run the analysis
    ad_miss_count, ad_miss_list = analyze_year_gaps(seen_years_ad, 1, 2025, "AD")
    bc_miss_count, bc_miss_list = analyze_year_gaps(seen_years_bc, 1, 500, "BC")

    print(f"\n--- Year Gap Analysis ---")
    print(f"AD Years Missing: {ad_miss_count} / 2025")
    print(f"BC Years Missing: {bc_miss_count} / 500")

    print("\n--- Export Summary ---")
    print(f"Total Years:    {counts['year']}")
    print(f"Total Decades:  {counts['decade']}")
    print(f"Total Centuries: {counts['century']}")
    print(f"Grand Total:    {len(final_challenges)}")

if __name__ == "__main__":
    export_challenges('wikipedia_history.db', 'challenges.json')