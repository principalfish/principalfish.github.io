"""Convert a Wikipedia "results by state" table into the project election-JSON shape.

Source (ungated): the English Wikipedia article for a cycle, e.g.
    https://en.wikipedia.org/wiki/1968_United_States_presidential_election

538's ``election-results`` CSV only reaches back to 2000, so the pre-2000 presidential
cycles (1964–1996) are sourced from Wikipedia instead. Each article carries a
"results by state" ``wikitable`` giving, per state, each candidate's popular vote and
the state's electoral-vote split. This script parses that table and emits the same
``{unit: {seatInfo, partyInfo}}`` structure that ``convert_538_presidential.py`` produces
and ``import_presidential_elections.py`` consumes.

The header layout varies by cycle — 1964/1968/1976 name candidates in the first header
row; 1992/1996 nest them under "Candidates with / without electoral votes" over a third
row — so the parser builds a colspan/rowspan-aware header grid and locates each
candidate's vote column by the ``#`` sub-header beneath its name, rather than by a fixed
column offset.

Maine and Nebraska appear as single statewide rows for these cycles (neither split its
electoral votes by district before 2008), so their per-district map units are backfilled
from the statewide result via ``_backfill_me_ne_cd_units`` (shared with the 538 converter).
Electoral votes are then stamped from ``us_electoral_votes.ev_map_for_year`` so the JSON
carries the correct per-era, per-unit weights (ME statewide = 2, each CD unit = 1, …) and
sums to 538 — matching the 538-sourced files.

Usage:
    python old_data/scripts/usa/convert_wiki_presidential.py \
        --year 1968 \
        --out old_data/files/usa/presidential-1968.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Tag

sys.path.insert(0, str(Path(__file__).resolve().parent))

from convert_538_presidential import _backfill_me_ne_cd_units
from regions import STATE_DIVISION
from us_electoral_votes import ev_map_for_year

WIKI_URL = "https://en.wikipedia.org/wiki/{year}_United_States_presidential_election"
USER_AGENT = "principalfish-election-maps/1.0 (research; historical results import)"

# The 50 states + DC, exactly as the presidential map / regions table names them. Used both
# to locate the results table (its first column is full of these) and to reject footer rows.
STATE_NAMES: frozenset[str] = frozenset(STATE_DIVISION)

# Per-cycle candidate surname → project party key. A surname not listed here (minor and
# regional candidates, "Unpledged electors", write-ins) folds into ``others``; the two-party
# trend divides over dem+rep only, so only the major parties and state-winning third parties
# (Wallace 1968) must be named. Keys must be drawn from us_import.PARTY_KEY_TO_NAME.
YEAR_CONFIG: dict[int, dict[str, str]] = {
    1964: {"Johnson": "democrat", "Goldwater": "republican"},
    1968: {"Nixon": "republican", "Humphrey": "democrat", "Wallace": "independent"},
    1972: {"Nixon": "republican", "McGovern": "democrat"},
    1976: {"Carter": "democrat", "Ford": "republican"},
    1980: {"Reagan": "republican", "Carter": "democrat", "Anderson": "independent"},
    1984: {"Reagan": "republican", "Mondale": "democrat"},
    1988: {"Bush": "republican", "Dukakis": "democrat"},
    1992: {"Clinton": "democrat", "Bush": "republican", "Perot": "independent"},
    1996: {"Clinton": "democrat", "Dole": "republican", "Perot": "independent"},
}

# Party keys the downstream importer accepts (mirrors us_import.PARTY_KEY_TO_NAME). Kept as a
# local literal so this pure HTML→JSON converter needn't import the db-backed importer module.
# ``others`` is the default for any unconfigured candidate.
_ALLOWED_PARTY_KEYS = frozenset(
    {"democrat", "republican", "libertarian", "usgreen", "independent", "others"}
)

# A YEAR_CONFIG typo pointing at an unknown party key would otherwise only surface as a hard
# failure deep in import_us_election; catch it at load time instead.
_UNKNOWN_KEYS = {key for cfg in YEAR_CONFIG.values() for key in cfg.values()} - _ALLOWED_PARTY_KEYS
if _UNKNOWN_KEYS:
    raise ValueError(f"YEAR_CONFIG uses unknown party keys: {sorted(_UNKNOWN_KEYS)}")

# Header labels (matched case-insensitively as substrings above a vote column) that mark it as:
#   skip  — a derived margin/swing column, never a candidate or a total;
#   total — the state's popular-vote total (used only to fold un-columned votes into ``others``);
#   group — a wrapper row ("Candidates with electoral votes") sitting above the real candidate.
# Skip is tested before total so a "Overall popular vote" span covering both a margin and a
# total column (1992/1996) resolves to the single genuine total column.
_SKIP_LABELS = ("margin", "swing")
_TOTAL_LABELS = ("state total", "overall popular", "total votes")
_GROUP_LABELS = ("candidates with",)

# Bottom-row sub-headers that mark a popular-vote column. Wikipedia labels it "#" (1964–1976),
# "Votes" (1972) or "Vote" (1992/1996) across cycles; compared lower-cased.
_VOTE_LABELS = frozenset({"#", "vote", "votes"})

# First-column spellings of the District of Columbia, compared after stripping spaces,
# periods and commas ("D.C.", "D. C.", "DC", "Washington, D.C." all normalise to "dc").
_DC_NORMALISED = frozenset({"dc", "districtofcolumbia", "washingtondc"})

# Party affiliation words stripped from a candidate header to recover the display name
# ("Bill Clinton Democratic" → "Bill Clinton"). Longest-first so "American Independent"
# is removed before "Independent".
_PARTY_WORDS = (
    "American Independent", "Democratic", "Republican", "Libertarian", "Independent",
    "Reform", "Green", "Unpledged electors", "Unpledged", "Write-in", "National", "Other",
)


def _fetch(year: int) -> str:
    """Return the HTML of the Wikipedia presidential-election article for ``year``."""
    url = WIKI_URL.format(year=year)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https Wikipedia host
            return response.read().decode("utf-8", "replace")
    except (URLError, HTTPError) as err:
        raise ValueError(f"Could not fetch {url}: {err}") from err


def _clean(text: str) -> str:
    """Collapse whitespace and drop bracketed footnote refs from a cell's text."""
    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", "", text)).strip()


def _strip_footnotes(text: str) -> str:
    """Remove dagger / asterisk footnote markers a cell may carry ("Maine †" → "Maine")."""
    return _clean(re.sub(r"[†‡*§¶]", "", text))


def _state_name(cell_text: str) -> str | None:
    """Return the canonical state name in a first-column cell, or None if not a state.

    Strips footnote markers ("Nebraska †") and normalises DC's several spellings ("D. C.",
    …) to "District of Columbia". Maine/Nebraska congressional-district sub-rows ("Maine-1",
    "ME-2 …") and footer rows match no state name and return None, so each state is taken from
    its single statewide row and the ME/NE district units are backfilled afterwards.
    """
    cleaned = _strip_footnotes(cell_text)
    if cleaned in STATE_NAMES:
        return cleaned
    if re.sub(r"[.\s,]", "", cleaned).lower() in _DC_NORMALISED:
        return "District of Columbia"
    return None


def _parse_votes(cell_text: str) -> int:
    """Parse a vote count cell into an int; blank / dash cells are zero."""
    digits = re.sub(r"[^\d]", "", cell_text)
    return int(digits) if digits else 0


def _find_results_table(soup: BeautifulSoup) -> Tag:
    """Return the wikitable whose first column holds the most state names."""
    best: Tag | None = None
    best_hits = 0
    for table in soup.find_all("table", class_="wikitable"):
        hits = 0
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if cells and _state_name(cells[0].get_text(" ", strip=True)):
                hits += 1
        if hits > best_hits:
            best_hits, best = hits, table
    if best is None or best_hits < 40:
        raise ValueError(f"No results-by-state table found (best had {best_hits} states)")
    return best


def _split_header_data(table: Tag) -> tuple[list[Tag], list[Tag]]:
    """Split a results table's rows into leading header rows and per-state data rows.

    Header rows are those before the first state row; data rows are every row whose first
    cell names a state (footer "Total" rows fail that test and are dropped).
    """
    rows = table.find_all("tr")
    data_start = next(
        (
            i
            for i, row in enumerate(rows)
            if (cells := row.find_all(["td", "th"]))
            and _state_name(cells[0].get_text(" ", strip=True))
        ),
        len(rows),
    )
    header_rows = rows[:data_start]
    data_rows = [
        row
        for row in rows[data_start:]
        if (cells := row.find_all(["td", "th"]))
        and _state_name(cells[0].get_text(" ", strip=True))
    ]
    return header_rows, data_rows


def _row_width(row: Tag) -> int:
    """Return a row's column count (its cells' colspans summed)."""
    return sum(int(cell.get("colspan", 1) or 1) for cell in row.find_all(["td", "th"]))


def _expand_header_grid(header_rows: list[Tag], ncols: int) -> list[list[str]]:
    """Expand header rows into a dense ``[col][row]`` label grid, honouring col/rowspans.

    Returns one list of top-to-bottom header labels per data column, so a candidate name in
    an upper row and its ``#`` sub-header in the bottom row line up under the same column.
    """
    occupied: dict[tuple[int, int], str] = {}
    for r, row in enumerate(header_rows):
        c = 0
        for cell in row.find_all(["th", "td"]):
            while (r, c) in occupied:
                c += 1
            colspan = int(cell.get("colspan", 1) or 1)
            rowspan = int(cell.get("rowspan", 1) or 1)
            text = _clean(cell.get_text(" ", strip=True))
            for dr in range(rowspan):
                for dc in range(colspan):
                    occupied[(r + dr, c + dc)] = text
            c += colspan
    nrows = len(header_rows)
    return [[occupied.get((r, c), "") for r in range(nrows)] for c in range(ncols)]


def _vote_columns(grid: list[list[str]], year: int) -> tuple[dict[int, str], int | None]:
    """Map data-column index → candidate party key, and find the state-total column.

    A column is a candidate vote column when its bottom-row sub-header is a vote label
    (``#`` / ``Vote`` / ``Votes``) and an upper label names a candidate; the candidate's
    surname is matched (word-boundary) in ``YEAR_CONFIG`` (default ``others``). A vote column
    whose group is a "State total" / "Overall popular vote" span is returned as the total
    column instead. Margin/swing columns are skipped before either test.
    """
    config = YEAR_CONFIG[year]
    key_by_col: dict[int, str] = {}
    total_col: int | None = None
    for col, labels in enumerate(grid):
        if not labels or labels[-1].lower() not in _VOTE_LABELS:
            continue
        uppers = [label for label in labels[:-1] if label]
        joined = " | ".join(label.lower() for label in uppers)
        if any(tok in joined for tok in _SKIP_LABELS):
            continue
        if any(tok in joined for tok in _TOTAL_LABELS):
            total_col = col
            continue
        candidate = next(
            (
                label
                for label in reversed(uppers)
                if not any(tok in label.lower() for tok in _GROUP_LABELS + _TOTAL_LABELS)
            ),
            None,
        )
        if candidate is None:
            continue
        key_by_col[col] = next(
            (
                key
                for surname, key in config.items()
                if re.search(rf"\b{re.escape(surname)}\b", candidate, re.IGNORECASE)
            ),
            "others",
        )
    return key_by_col, total_col


def _display_name(candidate_label: str) -> str:
    """Recover a candidate's display name from its header label, stripping party words."""
    name = candidate_label
    for word in _PARTY_WORDS:
        name = re.sub(rf"\b{re.escape(word)}\b", "", name)
    cleaned = _clean(name)
    return cleaned or "Other"


def _row_columns(row: Tag, ncols: int) -> list[Tag | None]:
    """Expand a data row's cells into ``ncols`` positional slots, honouring colspan.

    Aligns each data cell to the same column index as the colspan-aware header grid, so a
    spanned cell shifts nothing downstream. Columns a row does not reach stay ``None``.
    """
    slots: list[Tag | None] = [None] * ncols
    col = 0
    for cell in row.find_all(["td", "th"]):
        for _ in range(int(cell.get("colspan", 1) or 1)):
            if col < ncols:
                slots[col] = cell
                col += 1
    return slots


def _cell_text(slots: list[Tag | None], col: int) -> str:
    """Return the text of the cell at ``col`` (empty string when the slot is absent)."""
    cell = slots[col] if 0 <= col < len(slots) else None
    return cell.get_text(" ", strip=True) if cell is not None else ""


def _unit_from_row(
    slots: list[Tag | None],
    key_by_col: dict[int, str],
    total_col: int | None,
    grid: list[list[str]],
) -> dict[str, Any]:
    """Build one state's ``{seatInfo, partyInfo}`` from its span-aligned data-row slots."""
    party_info: dict[str, dict[str, Any]] = {}
    winner_key = ""
    winner_votes = -1
    for col, key in key_by_col.items():
        if col >= len(slots) or slots[col] is None:
            continue
        votes = _parse_votes(_cell_text(slots, col))
        entry = party_info.setdefault(key, {"total": 0, "name": ""})
        entry["total"] += votes
        if not entry["name"] or key != "others":
            # Prefer a real candidate name over the "Other" fallback for aggregate buckets.
            candidate_label = next((lbl for lbl in reversed(grid[col][:-1]) if lbl), "")
            entry["name"] = _display_name(candidate_label) if key != "others" else "Other"
        if votes > winner_votes:
            winner_votes, winner_key = votes, key

    # Fold any residual (minor candidates without their own column) into ``others`` so the
    # per-unit totals reconcile with the state's reported turnout.
    if total_col is not None:
        counted = sum(entry["total"] for entry in party_info.values())
        residual = _parse_votes(_cell_text(slots, total_col)) - counted
        if residual > 0:
            other = party_info.setdefault("others", {"total": 0, "name": "Other"})
            other["total"] += residual

    return {
        "seatInfo": {"current": winner_key, "electoral_votes": 0},
        "partyInfo": party_info,
    }


def convert(year: int, html: str | None = None) -> dict[str, Any]:
    """Convert a cycle's Wikipedia results table into the project election-JSON structure.

    Args:
        year: Election cycle (must be a key of ``YEAR_CONFIG``).
        html: Optional pre-fetched article HTML (for tests); fetched from Wikipedia if None.

    Returns:
        Mapping of unit display name to ``{"seatInfo", "partyInfo"}``, with ME/NE district
        units backfilled and electoral votes stamped from the year's apportionment era.
    """
    if year not in YEAR_CONFIG:
        raise ValueError(f"No candidate config for {year}; add it to YEAR_CONFIG")

    soup = BeautifulSoup(html if html is not None else _fetch(year), "html.parser")
    table = _find_results_table(soup)
    header_rows, data_rows = _split_header_data(table)

    # Column count is the widest row's summed colspans, so the header grid and every data row
    # share one coordinate system even when a row carries spanned cells.
    ncols = max(_row_width(row) for row in table.find_all("tr"))
    grid = _expand_header_grid(header_rows, ncols)
    key_by_col, total_col = _vote_columns(grid, year)
    if not key_by_col:
        raise ValueError(f"No candidate vote columns found for {year}")

    result: dict[str, Any] = {}
    for row in data_rows:
        slots = _row_columns(row, ncols)
        state = _state_name(_cell_text(slots, 0))
        if state is None:
            continue
        if state in result:
            raise ValueError(f"Duplicate row for {state!r} in the {year} table")
        unit = _unit_from_row(slots, key_by_col, total_col, grid)
        if not unit["partyInfo"]:
            raise ValueError(f"No candidate votes parsed for {state} in {year}")
        result[state] = unit

    _backfill_me_ne_cd_units(result)

    # Stamp per-unit electoral votes from the year's apportionment era (the JSON's EV field
    # is otherwise inert at import, but this keeps the file self-consistent and summing to 538).
    ev_map = ev_map_for_year(year)
    for unit, data in result.items():
        data["seatInfo"]["electoral_votes"] = ev_map.get(unit, 0)

    return dict(sorted(result.items()))


def main() -> None:
    """CLI entry point: fetch a cycle's Wikipedia results and write the project JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True, help="Election cycle (e.g. 1968)")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()

    data = convert(args.year)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total_ev = sum(unit["seatInfo"]["electoral_votes"] for unit in data.values())
    print(f"Wrote {len(data)} units ({total_ev} electoral votes) to {args.out}")


if __name__ == "__main__":
    main()
