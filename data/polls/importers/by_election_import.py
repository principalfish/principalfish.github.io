"""Import a UK by-election result from a Wikipedia page.

Scrapes the results table and infobox from a Wikipedia by-election article,
then inserts an Election (type=by_election) with Vote rows for each candidate.

Usage from CLI:
    python polls/importers/by_election_import.py \
        --url https://en.wikipedia.org/wiki/2025_Runcorn_and_Helsby_by-election \
        --parent-election "2024 General Election"

    python polls/importers/by_election_import.py --url <URL> --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db import Database
from models import ElectionType

DEFAULT_PARENT_ELECTION_NAME = "2024 General Election"
DEFAULT_MAP_NAME = "UK Constituencies post 2022"

PARTY_NAME_MAP: dict[str, str] = {
    "conservative": "Conservative",
    "conservative and unionist": "Conservative",
    "labour": "Labour",
    "labour and co-operative": "Labour",
    "liberal democrat": "Liberal Democrats",
    "liberal democrats": "Liberal Democrats",
    "reform uk": "Reform UK",
    "reform": "Reform UK",
    "green": "Green",
    "green party": "Green",
    "scottish national party": "Scottish National Party",
    "snp": "Scottish National Party",
    "plaid cymru": "Plaid Cymru",
    "democratic unionist party": "Democratic Unionist Party",
    "dup": "Democratic Unionist Party",
    "sinn féin": "Sinn Féin",
    "sinn fein": "Sinn Féin",
    "alliance": "Alliance",
    "alliance party": "Alliance",
    "sdlp": "SDLP",
    "ulster unionist party": "Ulster Unionist Party",
    "uup": "Ulster Unionist Party",
    "independent": "Others",
    "speaker": "Others",
    "liberal": "Others",
}

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


@dataclass
class ParsedCandidate:
    party_name: str
    candidate_name: str
    votes: int
    elected: bool


@dataclass
class ByElectionImportPlan:
    url: str
    constituency_name: str
    election_date: date | None
    election_name: str
    candidates: list[ParsedCandidate]
    parent_election_id: int | None
    parent_election_name: str
    map_id: int
    seat_id: int | None
    seat_name_matched: str | None
    party_id_by_name: dict[str, int] = field(default_factory=dict)


def normalize_name(value: str) -> str:
    value = value.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", value)


def fetch_wikipedia_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": "ElectionMapsBot/1.0"})
    with urlopen(req, timeout=15) as response:
        return response.read().decode("utf-8")


def parse_election_date(soup: BeautifulSoup) -> date | None:
    """Extract election date from the infobox.

    Wikipedia by-election infoboxes embed the date in a <b> tag inside an
    infobox-subheader cell rather than a labelled th/td row, so we try both
    approaches: first the labelled row, then any bold text in the infobox.
    """
    infobox = soup.find("table", class_="infobox")
    if not infobox:
        return None

    # Approach 1: labelled row (e.g. general election pages)
    for row in infobox.find_all("tr"):
        header = row.find("th")
        if header and "date" in header.get_text(strip=True).lower():
            cell = row.find("td")
            if cell:
                result = _parse_date_text(cell.get_text(strip=True))
                if result:
                    return result

    # Approach 2: bold text in infobox-subheader cell (by-election pages)
    for bold in infobox.find_all("b"):
        result = _parse_date_text(bold.get_text(strip=True))
        if result:
            return result

    return None


def _parse_date_text(text: str) -> date | None:
    """Parse a date string like '6 March 2025' or 'March 6, 2025'."""
    # UK format: 6 March 2025
    match = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if match:
        day = int(match.group(1))
        month_name = match.group(2).lower()
        year = int(match.group(3))
        month = MONTH_MAP.get(month_name)
        if month:
            return date(year, month, day)

    # US format: March 6, 2025
    match = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if match:
        month_name = match.group(1).lower()
        day = int(match.group(2))
        year = int(match.group(3))
        month = MONTH_MAP.get(month_name)
        if month:
            return date(year, month, day)

    return None


def parse_constituency_name(soup: BeautifulSoup) -> str:
    """Extract constituency name from the page title or infobox."""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
        # Remove " - Wikipedia" suffix
        title = re.sub(r"\s*[-–]\s*Wikipedia.*$", "", title)
        # Extract constituency from "2025 Runcorn and Helsby by-election"
        match = re.match(r"\d{4}\s+(.+?)\s+by-election", title, re.IGNORECASE)
        if match:
            return match.group(1)
        return title
    return "Unknown"


def parse_election_name_from_title(soup: BeautifulSoup) -> str:
    """Extract the full election name from the page title."""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
        title = re.sub(r"\s*[-–]\s*Wikipedia.*$", "", title)
        return title
    return "Unknown by-election"


def parse_results_table(soup: BeautifulSoup) -> list[ParsedCandidate]:
    """Parse the election results table from the Wikipedia page."""
    candidates: list[ParsedCandidate] = []

    # Find all wikitables and look for the one with vote results
    tables = soup.find_all("table", class_="wikitable")

    for table in tables:
        headers = []
        for th in (table.find("tr") or table).find_all("th"):
            headers.append(th.get_text(strip=True).lower())

        # Look for a table with "votes" or "candidate" columns
        has_votes = any("votes" in h or "vote" in h for h in headers)
        has_candidate = any("candidate" in h for h in headers)
        if not (has_votes and has_candidate):
            continue

        # Determine column indices
        party_col = _find_column(headers, ["party"])
        candidate_col = _find_column(headers, ["candidate"])
        votes_col = _find_column(headers, ["votes"])

        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cells = row.find_all(["td", "th"])

            # Skip summary/footer rows that have fewer cells than the header
            # (e.g. "Majority", "Turnout", "Rejected ballots" rows)
            if len(cells) < len(headers):
                continue

            # Data rows often have an extra leading colour-swatch td that shifts
            # all columns right by 1 relative to the header indices.
            row_offset = len(cells) - len(headers)

            party_text = _extract_party_from_row(cells, party_col, headers)
            if not party_text:
                continue

            eff_candidate_col = (candidate_col + row_offset) if candidate_col is not None else None
            eff_votes_col = (votes_col + row_offset) if votes_col is not None else None

            candidate_text = cells[eff_candidate_col].get_text(strip=True) if eff_candidate_col is not None and eff_candidate_col < len(cells) else ""
            votes_text = cells[eff_votes_col].get_text(strip=True) if eff_votes_col is not None and eff_votes_col < len(cells) else "0"

            # Clean votes text (remove commas, footnote refs)
            votes_clean = re.sub(r"[^\d]", "", votes_text)
            if not votes_clean:
                continue

            # Check if this candidate won (bold text or checkmark)
            elected = bool(row.find("b") and eff_candidate_col is not None and cells[eff_candidate_col].find("b"))
            # Also check for ✓ or tick mark
            row_text = row.get_text()
            if "✓" in row_text or "✔" in row_text or "Yes" in row_text:
                elected = True

            mapped_party = _map_party_name(party_text)

            candidates.append(ParsedCandidate(
                party_name=mapped_party,
                candidate_name=candidate_text,
                votes=int(votes_clean),
                elected=elected,
            ))

        if candidates:
            break

    # If no candidate was marked elected, mark the one with most votes
    if candidates and not any(c.elected for c in candidates):
        top = max(candidates, key=lambda c: c.votes)
        top.elected = True

    return candidates


def _find_column(headers: list[str], keywords: list[str]) -> int | None:
    for i, h in enumerate(headers):
        for kw in keywords:
            if kw in h:
                return i
    return None


def _extract_party_from_row(cells: list, party_col: int | None, headers: list[str]) -> str | None:
    """Extract party name from a results table row.

    Wikipedia results tables often have a narrow colour cell before the party name cell.
    If the detected party_col cell is very short (just a colour swatch), use the next cell.
    """
    if party_col is None:
        return None
    if party_col >= len(cells):
        return None

    cell = cells[party_col]
    text = cell.get_text(strip=True)

    # If the cell is empty or very short (colour swatch), try the next cell
    if len(text) <= 1 and party_col + 1 < len(cells):
        cell = cells[party_col + 1]
        text = cell.get_text(strip=True)

    # Also check for a link inside the cell
    link = cell.find("a")
    if link:
        text = link.get_text(strip=True)

    if not text or text.lower() in ("", "n/a"):
        return None

    return text


def _map_party_name(raw_name: str) -> str:
    """Map a raw Wikipedia party name to our canonical name."""
    normalized = raw_name.strip().lower()
    # Direct match
    if normalized in PARTY_NAME_MAP:
        return PARTY_NAME_MAP[normalized]

    # Partial match
    for key, value in PARTY_NAME_MAP.items():
        if key in normalized or normalized in key:
            return value

    return "Others"


def build_import_plan(
    db: Database,
    *,
    url: str,
    parent_election_name: str = DEFAULT_PARENT_ELECTION_NAME,
    map_name: str = DEFAULT_MAP_NAME,
) -> ByElectionImportPlan:
    html = fetch_wikipedia_html(url)
    soup = BeautifulSoup(html, "lxml")

    constituency_name = parse_constituency_name(soup)
    election_date = parse_election_date(soup)
    election_name = parse_election_name_from_title(soup)
    candidates = parse_results_table(soup)

    # Look up parent election
    parent_election = db.get_election_by_name(parent_election_name)
    parent_election_id = parent_election.id if parent_election else None

    # Look up map
    map_row = db.get_map_by_name(map_name)
    map_id = map_row.id if map_row else 0

    # Match constituency to seat
    seat_id = None
    seat_name_matched = None
    if map_row:
        seats = db.get_seats_for_map(map_row.id)
        normalized_constituency = normalize_name(constituency_name)
        for seat in seats:
            if normalize_name(seat.seat_name) == normalized_constituency:
                seat_id = seat.id
                seat_name_matched = seat.seat_name
                break

    # Match party names to party IDs
    all_parties = db.get_all_parties()
    party_name_to_id: dict[str, int] = {}
    for party in all_parties:
        party_name_to_id[party.name] = party.id

    party_id_by_name: dict[str, int] = {}
    for candidate in candidates:
        if candidate.party_name in party_name_to_id:
            party_id_by_name[candidate.party_name] = party_name_to_id[candidate.party_name]

    return ByElectionImportPlan(
        url=url,
        constituency_name=constituency_name,
        election_date=election_date,
        election_name=election_name,
        candidates=candidates,
        parent_election_id=parent_election_id,
        parent_election_name=parent_election_name,
        map_id=map_id,
        seat_id=seat_id,
        seat_name_matched=seat_name_matched,
        party_id_by_name=party_id_by_name,
    )


def commit_import_plan(
    db: Database,
    plan: ByElectionImportPlan,
) -> dict:
    if not plan.seat_id:
        raise ValueError(f"No seat matched for constituency '{plan.constituency_name}'")

    if not plan.candidates:
        raise ValueError("No candidates parsed from Wikipedia page")

    # Check if this by-election already exists
    existing = db.get_election_by_name(plan.election_name)
    if existing:
        raise ValueError(f"Election '{plan.election_name}' already exists (id={existing.id})")

    election = db.add_election(
        map_id=plan.map_id,
        year=plan.election_date.year if plan.election_date else 2025,
        name=plan.election_name,
        election_type=ElectionType.by_election,
        parent_election_id=plan.parent_election_id,
        election_date=plan.election_date,
    )

    votes_inserted = 0
    for candidate in plan.candidates:
        party_id = plan.party_id_by_name.get(candidate.party_name)
        db.add_vote(
            election.id,
            plan.seat_id,
            party_id=party_id,
            candidate_name=candidate.candidate_name,
            vote_total=float(candidate.votes),
            elected=candidate.elected,
        )
        votes_inserted += 1

    return {
        "election_id": election.id,
        "election_name": election.name,
        "seat_name": plan.seat_name_matched,
        "votes_inserted": votes_inserted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a by-election from Wikipedia")
    parser.add_argument("--url", required=True, help="Wikipedia by-election URL")
    parser.add_argument(
        "--parent-election",
        default=DEFAULT_PARENT_ELECTION_NAME,
        help="Name of the parent general election",
    )
    parser.add_argument("--map-name", default=DEFAULT_MAP_NAME, help="Map name")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't write to DB")
    args = parser.parse_args()

    db = Database()
    db.create_tables()

    plan = build_import_plan(
        db,
        url=args.url,
        parent_election_name=args.parent_election,
        map_name=args.map_name,
    )

    print(f"Constituency: {plan.constituency_name}")
    print(f"Election: {plan.election_name}")
    print(f"Date: {plan.election_date}")
    print(f"Seat match: {plan.seat_name_matched or 'NOT FOUND'}")
    print(f"Parent election ID: {plan.parent_election_id}")
    print(f"Candidates: {len(plan.candidates)}")
    for c in plan.candidates:
        marker = " *" if c.elected else ""
        party_id = plan.party_id_by_name.get(c.party_name, "?")
        print(f"  {c.party_name} (id={party_id}): {c.candidate_name} - {c.votes:,}{marker}")

    if args.dry_run:
        print("\nDry run — no database writes.")
        return

    result = commit_import_plan(db, plan)
    print(f"\nImported: election #{result['election_id']}, {result['votes_inserted']} votes")


if __name__ == "__main__":
    main()
