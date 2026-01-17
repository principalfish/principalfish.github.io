import sqlite3
import json
import re
import random

def get_first_two_sentences(text):
    """Splits text into sentences and returns the first two, respecting St./Mt. abbreviations."""
    # 1. Protect titles and circa abbreviations before splitting
    text = re.sub(r'\bSt\.\s', 'St_PLACEHOLDER ', text)
    text = re.sub(r'\bMt\.\s', 'Mt_PLACEHOLDER ', text)
    text = re.sub(r'\bDr\.\s', 'Dr_PLACEHOLDER ', text)
    text = re.sub(r'\bMr\.\s', 'Mr_PLACEHOLDER ', text)
    text = re.sub(r'\bMrs\.\s', 'Mrs_PLACEHOLDER ', text)
    text = re.sub(r'\b[Cc]\.\s', 'C_PLACEHOLDER ', text)
    text = re.sub(r'\b[Cc]a\.\s', 'Ca_PLACEHOLDER ', text)
    
    # 2. Split by punctuation followed by space and capital letter
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    
    # 3. Take the first two
    truncated = " ".join(sentences[:2])
    
    # 4. Restore titles and abbreviations
    truncated = truncated.replace('St_PLACEHOLDER', 'St.')
    truncated = truncated.replace('Mt_PLACEHOLDER', 'Mt.')
    truncated = truncated.replace('Dr_PLACEHOLDER', 'Dr.')
    truncated = truncated.replace('Mr_PLACEHOLDER', 'Mr.')
    truncated = truncated.replace('Mrs_PLACEHOLDER', 'Mrs.')
    truncated = truncated.replace('C_PLACEHOLDER', 'c.')
    truncated = truncated.replace('Ca_PLACEHOLDER', 'ca.')
    
    return truncated

def clean_event_text(text, target_year):
    """Cleans prefixes, citations, years, and restricts to two sentences."""
    
    # 1. Strip leading "c.", "ca.", "circa" - must have period or be full word
    text = text.strip()
    # Match only: "c. ", "C. ", "ca. ", "Ca. ", "circa ", "Circa " (with required space/period)
    text = re.sub(r'^(?:[Cc]\.\s+|[Cc]a\.\s+|[Cc]irca\s+)[\u2014\-\u2013]?\s*', '', text)
    
    # 2. Remove Wikipedia citations like [1] or [12]
    text = re.sub(r'\[\d+\]', '', text)
    
    # 3. Remove newlines and replace with space (handles multi-line facts)
    text = re.sub(r'\n+', ' ', text)
    
    # 4. Apply sentence truncation
    text = get_first_two_sentences(text)

    # 5. Year/Era filtering
    text = re.sub(r'\b\d+(?:st|nd|rd|th)\s+century\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(AD|BC|BCE|CE)\b', '', text, flags=re.IGNORECASE)

    # 6. IMPROVED: Replace years within 100 years of target with a placeholder first
    # This prevents leaving behind "-year-old" artifacts
    def year_replacer(match):
        val = int(match.group())
        if (target_year - 100) <= val <= (target_year + 100):
            return "YYYY"  # Use placeholder
        return match.group()

    text = re.sub(r'\b\d{1,4}\b', year_replacer, text)
    
    # 7. Clean up YYYY artifacts: "his YYYY-year-old" -> "his young", "YYYY months" -> "several months"
    text = re.sub(r'\bYYYY-year-old\b', 'young', text)
    text = re.sub(r'\bYYYY years?\b', 'some years', text)
    text = re.sub(r'\bYYYY months?\b', 'several months', text)
    text = re.sub(r'\bYYYY\b', '', text)  # Remove remaining YYYY
    
    # 8. Cleanup formatting "wreckage"
    text = re.sub(r'\(\s*[,.]?\s*\)', '', text)       # Remove empty parens
    text = re.sub(r'\(\s*\w*\s*\)', '', text)         # Remove parens with only a word
    text = re.sub(r'\s{2,}', ' ', text)               # Collapse multiple spaces
    text = re.sub(r'\s+([,.])', r'\1', text)          # Fix spaces before punctuation
    text = re.sub(r'\s+([,])\s+', r'\1 ', text)       # Normalize comma spacing
    
    # 9. Remove facts that start with just a month (likely incomplete after cleanup)
    if re.match(r'^(January|February|March|April|May|June|July|August|September|October|November|December)\s+[A-Z]', text):
        # Check if it's ONLY a month and a name/place without substance
        if len(text) < 50:
            return ""
    
    text = text.strip()
    
    # 10. Quality filters - reject if too short or doesn't contain enough words
    if len(text) < 30 or len(text.split()) < 5:
        return ""
    
    if text:
        # Ensure it starts with a Capital and ends with proper punctuation
        text = text[0].upper() + text[1:]
        if not text.endswith(('.', '!', '?')):
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
    quality_stats = {'total_raw': 0, 'filtered_out': 0, 'kept': 0}

    for year, era, timeframe, events_str in rows:
        raw_events = events_str.split('||')
        clean_events = []
        target_year = int(year)
        
        for f in raw_events:
            quality_stats['total_raw'] += 1
            t = clean_event_text(f, target_year)
            # Enhanced quality filter
            if t and len(t) > 30 and not t.startswith('C. '):
                clean_events.append(t)
                quality_stats['kept'] += 1
            else:
                quality_stats['filtered_out'] += 1

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
    print(f"\n✅ Export Summary: {len(final_challenges)} challenges.")
    print(f"   - Years: {counts['year']}")
    print(f"   - Decades: {counts['decade']}")
    print(f"   - Centuries: {counts['century']}")
    print(f"\n📊 Quality Stats:")
    print(f"   - Raw facts: {quality_stats['total_raw']}")
    print(f"   - Kept: {quality_stats['kept']} ({100*quality_stats['kept']/quality_stats['total_raw']:.1f}%)")
    print(f"   - Filtered: {quality_stats['filtered_out']} ({100*quality_stats['filtered_out']/quality_stats['total_raw']:.1f}%)")
    
    conn.close()

if __name__ == "__main__":
    export_challenges('wikipedia_history.db', 'challenges.json')
