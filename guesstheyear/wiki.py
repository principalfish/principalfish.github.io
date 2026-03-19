import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import re

def setup_database():
    """Create the SQLite database and events table if they do not already exist.

    Opens (or creates) ``wikipedia_history.db`` in the current directory, enables
    WAL journal mode for safer concurrent access, and creates the ``events`` table
    with columns: id, year, era, timeframe_type, event, source_page.

    Returns:
        tuple[sqlite3.Connection, sqlite3.Cursor]: A (conn, cursor) pair ready for
        querying and inserting rows.
    """
    conn = sqlite3.connect('wikipedia_history.db')
    cursor = conn.cursor()
    cursor.execute('PRAGMA journal_mode=WAL;')
    
    # Updated table creation with timeframe_type
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER,
            era TEXT,
            timeframe_type TEXT,
            event TEXT,
            source_page TEXT
        )
    ''')
    conn.commit()
    return conn, cursor

def get_timeframe_info(year, era):
    """Returns (primary_title, fallback_title, timeframe_type, year_range)"""
    if era == "AD":
        # Usually AD_1 to AD_999, but some (like 151) are just numbers
        primary = f"AD_{year}" if year < 1000 else str(year)
        fallback = str(year) if year < 1000 else f"AD_{year}"
        return primary, fallback, "year", [year]
    
    # BC Logic - Returning 4 values to match the AD structure
    if year <= 500:
        title = f"{year}_BC"
        return title, None, "year", [year]
    
    elif year <= 1799:
        decade = (year // 10) * 10
        # Wikipedia quirk for century heads
        title = f"{year}s_BC_(decade)" if year % 100 == 0 else f"{decade}s_BC"
        return title, None, "decade", range(decade, decade + 10)
    
    else:
        # Century calculation (e.g., 1801-1900 is 19th Century)
        century = ((year - 1) // 100) + 1
        suffixes = {1: 'st', 2: 'nd', 3: 'rd'}
        suffix = suffixes.get(century % 10 if not 11 <= century <= 13 else None, 'th')
        title = f"{century}{suffix}_century_BC"
        return title, None, "century", range((century-1)*100 + 1, (century*100) + 1)

def get_year_events(page_title):
    """Fetch a Wikipedia page via the API and extract list items from event sections.

    Queries the Wikipedia REST API for the section list of the given page, filters to
    sections whose titles contain recognised event keywords (e.g. "Events", "By place",
    "January – March") while excluding non-content sections (References, Sources, etc.),
    then fetches the HTML for each valid section and collects all ``<li>`` text items that
    are at least 25 characters long and not already seen.  Citation superscripts and
    bracketed references are removed before collecting each item.

    Args:
        page_title (str): The Wikipedia page title to fetch (e.g. ``"AD_1066"`` or
            ``"1066"``).

    Returns:
        list[str]: A list of cleaned event strings extracted from the page, or an empty
        list if the page does not exist or an error occurs.
    """
    URL = "https://en.wikipedia.org/w/api.php"
    headers = {'User-Agent': 'HistoryGameParser/1.0 (contact@email.com)'}
    
    params = {"action": "parse", "page": page_title, "prop": "sections", "format": "json"}
    try:
        res = requests.get(URL, params=params, headers=headers, timeout=10).json()
        if 'parse' not in res: return []
     
        # EXCLUDE undesirable sections like References, Sources, External Links
        exclude_keywords = ["references", "sources", "bibliography", "external links", "further reading", "notes"]
        
        # Get only valid section indices
        valid_sections = [
            s['index'] for s in res['parse']['sections'] 
            if any(kw.lower() in s['line'].lower() for kw in ["events", "by place", "content", "Events and trends", "January – March", "April – June", "July – September", "October – December"])
            and not any(ex in s['line'].lower() for ex in exclude_keywords)
        ]

        all_events = []
        for section_index in valid_sections:
         
            params = {"action": "parse", "page": page_title, "section": section_index, "prop": "text", "format": "json"}
            page_res = requests.get(URL, params=params, headers=headers).json()
            soup = BeautifulSoup(page_res['parse']['text']['*'], 'html.parser')
  
            for li in soup.find_all('li'):
                if li.parent.name in ['ul', 'ol']:
                    # 1. Remove HTML citation sups [1], [2], etc.
                    for sup in li.find_all('sup'): 
                        sup.decompose() 

                    cleaned = li.get_text().strip()

                    # 2. Skip if it's a plain-text reference line (starts with ^)
                    if cleaned.startswith('^'):
                        continue

                    # 3. Final cleaning: Remove any remaining bracketed [edit] or [12] 
                    # that might not have been in <sup> tags
                    cleaned = re.sub(r'\[\d+\]|\[edit\]', '', cleaned)
                    
                    # Filter out short fragments or empty strings
                    if len(cleaned) > 25 and cleaned not in all_events: 
                        all_events.append(cleaned)
        
        return all_events
    except Exception as e:
        print(f"Error on {page_title}: {e}")
        return []

def scrape_range(year_range, era, conn, cursor):
    """Iterate over a range of years, fetch Wikipedia events, and insert rows into the DB.

    For each year in year_range, calls get_timeframe_info to determine the Wikipedia
    page title and timeframe type, skips the year if it already has rows in the database,
    calls get_year_events (with fallback title if the primary returns nothing), and bulk-
    inserts one row per event per year covered by the timeframe (e.g. all years in a
    decade or century).  Commits after each successful fetch.  A 0.1-second sleep is
    added between requests to respect Wikipedia's rate limits.

    Args:
        year_range (range): The sequence of years to process (e.g. ``range(1, 2027)``).
        era (str): Either ``"AD"`` or ``"BC"``, used for DB lookups and page title
            construction.
        conn (sqlite3.Connection): Open database connection used for committing.
        cursor (sqlite3.Cursor): Database cursor used for queries and inserts.

    Returns:
        None
    """
    for year in year_range:
        primary_title, fallback_title, t_type, y_range = get_timeframe_info(year, era)
        
        # IMPROVED CHECK: Does this specific year already have data?
        cursor.execute('SELECT 1 FROM events WHERE year = ? AND era = ? LIMIT 1', (year, era))
        
        if cursor.fetchone():
            # If it's a century or decade, we might want to skip the whole range 
            # but for a year-by-year loop, this is the safest way to ensure no duplicates.
            print(f"Skipping {year} {era} (Year already has data)...")
            continue

        print(f"Fetching {primary_title} ({t_type})...")
        events = get_year_events(primary_title)
        
        # Fallback logic
        if not events and fallback_title:
            print(f"  No events in {primary_title}, trying fallback {fallback_title}...")
            events = get_year_events(fallback_title)
            final_title = fallback_title if events else primary_title
        else:
            final_title = primary_title
    
        if events:
            print (f"  Found {len(events)} events for Year {year} ({t_type})")
            for r_year in y_range:
                # One last safety check for bulk inserts (decades/centuries)
                # to prevent a decade insert from overlapping existing years
                cursor.execute('SELECT 1 FROM events WHERE year = ? AND era = ? LIMIT 1', (r_year, era))
                if cursor.fetchone():
                    continue
                
                data = [(r_year, era, t_type, e, final_title) for e in events]
                cursor.executemany(
                    'INSERT INTO events (year, era, timeframe_type, event, source_page) VALUES (?, ?, ?, ?, ?)', 
                    data
                )
            conn.commit()
        else:
            # We save a dummy entry or log it to prevent re-scraping "empty" years
            print(f"  Warning: No events found for Year {year}")
            
        time.sleep(0.1)

def main():
    """Entry point: set up the database and scrape Wikipedia events for AD and BC years.

    Creates the database via setup_database, then calls scrape_range for AD years 1–2026
    and BC years 1–4000 in sequence.  Closes the database connection on completion.

    Returns:
        None
    """
    conn, cursor = setup_database()
    
    print("--- Starting AD Scraping ---")
    scrape_range(range(1, 2027), "AD", conn, cursor)
 
    print("\n--- Starting BC Scraping ---")
    scrape_range(range(1, 4001), "BC", conn, cursor)

    conn.close()
    print("\nExtraction Complete!")

if __name__ == "__main__":
    main()