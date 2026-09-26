"""Contest layer: Wikipedia pages → seat- and matchup-scoped US poll rows.

The parser in ``us_polls_common`` reads a *table*: its grid, the headings it
sits under, its candidates and its rows. It applies no editorial rule. This
module supplies the rules, one set per **contest**:

- which pages a contest lives on (a fixed list, or links discovered from an
  index page);
- which of a page's tables count (general-election sections only, no primaries,
  no poll averages, no collapsed hypotheticals unless the user opts in);
- which **seat** a table belongs to (the page slug for a Senate race, a
  "District N" heading for a House district, a "Statewide › Nevada" heading for
  the President);
- which table is the race's **lead** — the first visible one, whose matchup
  becomes the race's automatic tracked matchup.

Scraping writes nothing: ``db`` is read only to turn a seat *name* into the
seat id on the contest's map, and every rule that fails to place a table is
reported on :class:`UsPollIndex` rather than dropped — the console's summary
page is the only place Wikipedia markup drift becomes visible.

The **Importing** section then turns one scraped row into a
:class:`UsImportPlan` and writes it as a ``Poll`` plus its ``PollRow``\\ s, and
points each race at its lead matchup. The wrapper scripts' command line lives
in :mod:`polls.importers.us.us_poll_cli`, so nothing here prints.

Fetching is injectable end to end — every test passes a dict-backed fake
fetcher, so no test touches the network.
"""

from __future__ import annotations

import logging
import re
import sys
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError

from bs4 import BeautifulSoup, Tag

# ``data/`` root — home of db.py / models.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db import Database
from models import Poll, PollRow, Pollster, Seat
from polls.importers.types import PollImportResult, ScrapedPollRow
from polls.importers.us.us_geography import (
    AT_LARGE_STATES,
    HOUSE_DISTRICT_COUNTS,
    STATE_POSTAL,
    canonical_state,
    house_seat_name,
    president_seat_for_heading,
    state_from_page_slug,
)
from polls.importers.us.us_polls_common import (
    MAX_PAGE_GRID_CELLS,
    CandidateReading,
    Heading,
    PageTables,
    ParsedTable,
    fetch_html,
    parse_poll_tables,
    pollster_identifier,
)
from sqlalchemy import select

logger = logging.getLogger(__name__)

WIKIPEDIA_BASE = "https://en.wikipedia.org"

HOUSE_INDEX_URL = (
    f"{WIKIPEDIA_BASE}/wiki/2026_United_States_House_of_Representatives_elections"
)
SENATE_INDEX_URL = f"{WIKIPEDIA_BASE}/wiki/2026_United_States_Senate_elections"

# The presidential pages, in the order they are fetched. The Statewide article
# does not exist yet (it 404s, which :func:`fetch_pages` records as a note
# rather than an error), but it is the page the statewide polls will appear on,
# so it is listed here and this tuple is overridable per run.
PRESIDENT_PAGE_URLS: tuple[str, ...] = (
    f"{WIKIPEDIA_BASE}/wiki/"
    "Nationwide_opinion_polling_for_the_2028_United_States_presidential_election",
    f"{WIKIPEDIA_BASE}/wiki/"
    "Statewide_opinion_polling_for_the_2028_United_States_presidential_election",
)

#: A page fetcher: takes a URL, returns the page source, raises on failure.
Fetcher = Callable[[str], str]

#: One fetch's outcome: ``(html, failure, note)``, exactly one of them set.
_FetchResult = tuple[str | None, str | None, str | None]

#: Where a contest's pages come from beyond its fixed ``page_urls``.
PageDiscovery = Literal["none", "senate_index", "house_index"]

#: Which of a page's tables a contest accepts.
SectionRule = Literal["general_election", "generic_ballot"]

#: How a table's seat is worked out.
SeatRule = Literal["national", "senate_state", "house_district", "president"]

#: What the contest's matchups mean — the automatic-tracking rule.
MatchupPolicy = Literal["auto_lead", "national_setting", "none"]

# "District 3", "District 40" — the h2 a multi-district House page groups a
# race under.
_DISTRICT_HEADING_RE = re.compile(r"^district\s+(\d{1,3})\b", re.IGNORECASE)

# Senate race pages: "2026 United States Senate (special) election in <State>".
_SENATE_PAGE_RE = re.compile(
    r"^(?:https?://en\.wikipedia\.org)?/wiki/"
    r"(2026_United_States_Senate_(?:special_)?election_in_[^#?]+)$"
)

# House state pages: plural for multi-district states, singular for at-large.
_HOUSE_PAGE_RE = re.compile(
    r"^(?:https?://en\.wikipedia\.org)?/wiki/"
    r"(2026_United_States_House_of_Representatives_elections?_in_[^#?]+)$"
)

_HEADING_SEPARATOR = " › "

# At most this many race pages are taken per state from an index. One is the
# norm; a second is let through so that one state holding two races in one
# cycle still reaches the duplicate-seat check, which reports it. Variant
# spellings of one state's page beyond that cannot grow the fetch list.
_MAX_PAGES_PER_STATE = 2

# Backstop on the race pages taken from one index, whatever ``keep`` admits.
_MAX_DISCOVERED_PAGES = _MAX_PAGES_PER_STATE * len(STATE_POSTAL)

# Most rejected race links one index reports by URL; the rest are only counted.
# A real index drops 0-2 (one state holding a regular and a special race in the
# same cycle), so 50 is far past it while bounding the cached queue payload and
# keeping the summary's page-failure list readable.
_MAX_DROPPED_LINKS = 50

# Most memory the page sources kept by one ``fetch_pages`` batch may hold,
# measured with ``sys.getsizeof``: CPython stores a whole string at 2 bytes per
# character once it holds one non-Latin-1 character (an en dash, which nearly
# every Wikipedia page has), so a character count would understate it by half.
# A real batch keeps ~90 pages of a few hundred KB, ~1 MB the largest seen, so
# even 90 x 1 MB x 2 bytes (~180 MB) fits. It caps the worst case well below the
# ~1.4 GB that 90 pages at the 8 MiB per-page cap could otherwise hold.
MAX_TOTAL_PAGE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class UsContest:
    """One US contest: where its polls live and how to place them.

    Attributes:
        slug: Stable identifier, used on the CLI (``--contest senate_races``)
            and stored on every row.
        label: Human name for the console.
        map_name: The forecast map the contest's polls belong to.
        pollster_suffix: Appended to a pollster slug, so the same house polling
            two chambers keeps one weight per chamber (``_us_senate``).
        pollster_label: Shown in a newly created pollster's name,
            ``"YouGov (US Senate)"``.
        page_urls: Pages fetched unconditionally.
        index_url: Page whose links are scanned for race pages, or None.
        discovery: Which link pattern ``index_url`` is scanned for.
        section_rule: Which of a page's tables count.
        seat_rule: How a table's seat is worked out.
        matchup_policy: ``"auto_lead"`` — the lead table's matchup becomes the
            race's automatic tracked matchup; ``"national_setting"`` — the user
            picks one national matchup and the seats follow it (President);
            ``"none"`` — the contest has no matchups (the generic ballot).
        allow_party_labels: Accept "Republicans"/"Democrats" column headers,
            which the generic-ballot aggregation table uses in place of
            candidates.
        summary_pollster_labels: Pollster labels that are a summary of the
            table's other rows rather than a source of their own. Matched
            case-insensitively and never imported.
    """

    slug: str
    label: str
    map_name: str
    pollster_suffix: str
    pollster_label: str
    page_urls: tuple[str, ...] = ()
    index_url: str | None = None
    discovery: PageDiscovery = "none"
    section_rule: SectionRule = "general_election"
    seat_rule: SeatRule = "national"
    matchup_policy: MatchupPolicy = "auto_lead"
    allow_party_labels: bool = False
    summary_pollster_labels: tuple[str, ...] = ()

    @property
    def is_per_state(self) -> bool:
        """True when each of the contest's pages covers one state's races."""
        return self.discovery != "none"

    @property
    def requires_matchup(self) -> bool:
        """True when every poll must name a matchup to be usable.

        The model and the matchup pages only read a poll through its matchup,
        so in such a contest a poll without one would be stored and then never
        used.
        """
        return self.matchup_policy != "none"


HOUSE_NATIONAL = UsContest(
    slug="house_national",
    label="House national generic ballot",
    map_name="US House Districts 2024",
    pollster_suffix="_us_house",
    pollster_label="US House",
    page_urls=(HOUSE_INDEX_URL,),
    section_rule="generic_ballot",
    seat_rule="national",
    matchup_policy="none",
    allow_party_labels=True,
    # The aggregation table ends with an "Average" row: the mean of the six
    # aggregators above it. Importing it would store a seventh pollster and
    # count every aggregator twice in the national average.
    summary_pollster_labels=("Average",),
)

HOUSE_DISTRICTS = UsContest(
    slug="house_districts",
    label="House district polls",
    map_name="US House Districts 2024",
    pollster_suffix="_us_house",
    pollster_label="US House",
    index_url=HOUSE_INDEX_URL,
    discovery="house_index",
    seat_rule="house_district",
)

SENATE_RACES = UsContest(
    slug="senate_races",
    label="Senate race polls",
    map_name="US Senate 2024",
    pollster_suffix="_us_senate",
    pollster_label="US Senate",
    index_url=SENATE_INDEX_URL,
    discovery="senate_index",
    seat_rule="senate_state",
)

PRESIDENT = UsContest(
    slug="president",
    label="Presidential polls",
    map_name="US Presidential 2024",
    pollster_suffix="_us_president",
    pollster_label="US President",
    page_urls=PRESIDENT_PAGE_URLS,
    seat_rule="president",
    matchup_policy="national_setting",
)

US_CONTESTS: tuple[UsContest, ...] = (
    HOUSE_NATIONAL,
    HOUSE_DISTRICTS,
    SENATE_RACES,
    PRESIDENT,
)

US_CONTESTS_BY_SLUG: Mapping[str, UsContest] = {
    contest.slug: contest for contest in US_CONTESTS
}


class UsPollRow(ScrapedPollRow):
    """One US poll, placed on a contest, a seat and a matchup.

    The queue reads the inherited fields (dates, pollster, sample label, source
    URL, matchup); everything below is what the US review step shows and what
    :func:`commit_us_import_plan` turns into a ``Poll`` plus its
    ``PollRow``\\ s.

    Attributes:
        contest: The :class:`UsContest` slug the row was read under.
        page_url: The Wikipedia page it was read from.
        heading_path: The section path as display text, e.g.
            ``"General election › Polling"``.
        seat_name: The seat on ``map_name``, or None for a national reading.
        seat_id: That seat's database id, or None for a national reading.
        map_name: The contest's forecast map.
        readings: One entry per candidate column that held a number, as
            written (not rescaled). A party whose suffix is unknown carries
            ``party_name=None`` and is excluded at commit time.
        sample_size: Respondents, or None when the table showed none.
        population: Sampled population code — "LV", "RV", "A".
        is_lead: True on the rows of the race's lead table: the first visible,
            accepted table with a matchup. :func:`apply_auto_tracked_matchups`
            promotes its matchup to the race's automatic tracked matchup.
        collapsed: True when the row came from a hidden hypothetical table,
            which only happens under the collapsed-only opt-in.
        pollster_tags: Partisan sponsor tags stripped from the pollster cell,
            e.g. ``("R",)``.
        notes: Per-table warnings worth showing next to the row.
    """

    contest: str
    page_url: str
    heading_path: str
    seat_name: str | None
    seat_id: int | None
    map_name: str
    readings: tuple[CandidateReading, ...]
    sample_size: int | None = None
    population: str | None = None
    is_lead: bool = False
    collapsed: bool = False
    pollster_tags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UnmatchedSeat:
    """A table whose seat could not be resolved, so its rows were dropped.

    Attributes:
        contest: The contest slug.
        page_url: The page the table sits on.
        heading_path: The table's section path as display text.
        seat_name: The seat name the rules produced, or None when they produced
            none at all.
        reason: Why the table could not be placed.
        dropped_rows: How many parsed poll rows were lost with it.
    """

    contest: str
    page_url: str
    heading_path: str
    seat_name: str | None
    reason: str
    dropped_rows: int


@dataclass(frozen=True, slots=True)
class EmptyTable:
    """A table that classified as a polling table but yielded no poll rows.

    Normally a sign of markup drift: the table named a pollster column and a
    date column, the contest's section rule accepted it, and then every row
    failed to parse.

    Attributes:
        contest: The contest slug.
        page_url: The page the table sits on.
        heading_path: The table's section path as display text.
        collapsed: True when the table was a hidden hypothetical.
    """

    contest: str
    page_url: str
    heading_path: str
    collapsed: bool


@dataclass(frozen=True, slots=True)
class NoMatchupTable:
    """A table whose rows were dropped because they form no matchup.

    Only reported for a contest whose polls need one
    (:attr:`UsContest.requires_matchup`): a table naming fewer than two
    candidates would store polls the model can never read.

    Attributes:
        contest: The contest slug.
        page_url: The page the table sits on.
        heading_path: The table's section path as display text.
        seat_name: The seat the table resolved to, or None for a national one.
        dropped_rows: How many parsed poll rows were lost with it.
    """

    contest: str
    page_url: str
    heading_path: str
    seat_name: str | None
    dropped_rows: int


@dataclass(frozen=True, slots=True)
class OversizedTable:
    """A table too large to read, abandoned before it was classified.

    Attributes:
        contest: The contest slug.
        page_url: The page the table sits on.
        heading_path: The table's section path as display text.
    """

    contest: str
    page_url: str
    heading_path: str


@dataclass(frozen=True, slots=True)
class CollapsedOnlyRace:
    """A race whose only general-election tables are collapsed hypotheticals.

    Attributes:
        contest: The contest slug.
        page_url: The race's page.
        seat_name: The seat, or None for a national scope.
        available_rows: Rows the collapsed tables hold.
        included: True when ``include_collapsed_for_uncovered`` was set and
            those rows are in :attr:`UsPollIndex.rows`.
    """

    contest: str
    page_url: str
    seat_name: str | None
    available_rows: int
    included: bool


@dataclass(frozen=True, slots=True)
class DiscoveredPages:
    """The race pages an index page links to.

    Attributes:
        urls: One absolute URL per race page to fetch, in index order.
        dropped: URL → reason for each race link left out for passing a cap
            (:data:`_MAX_PAGES_PER_STATE` or :data:`_MAX_DISCOVERED_PAGES`), at
            most :data:`_MAX_DROPPED_LINKS` of them.
        dropped_overflow: How many more links were left out past that, counted
            rather than listed.
    """

    urls: tuple[str, ...]
    dropped: Mapping[str, str]
    dropped_overflow: int = 0


@dataclass(frozen=True, slots=True)
class FetchedPages:
    """The outcome of fetching a batch of pages.

    Attributes:
        pages: URL → page source, for the pages that came back.
        failures: URL → reason, for the pages that did not.
        notes: Human-readable remarks that are not failures — a 404 means the
            page has not been written yet, which is expected for a future
            cycle's article.
    """

    pages: Mapping[str, str]
    failures: Mapping[str, str]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PageRows:
    """Everything one page contributed.

    Attributes:
        rows: The poll rows, in document order.
        unmatched_seats: Tables whose seat could not be resolved.
        empty_tables: Accepted tables that produced no rows.
        collapsed_only_races: Races on this page with no visible rows.
        unknown_suffixes: Party suffix → number of accepted tables carrying it.
        variants_dropped: Repeat rows (LV/RV, "with leaners") dropped.
        summary_rows_skipped: Rows dropped for naming a summary "pollster"
            (the generic-ballot table's "Average" row).
        no_matchup_tables: Accepted tables dropped for forming no matchup.
        oversized_tables: Tables too large to read.
    """

    rows: tuple[UsPollRow, ...]
    unmatched_seats: tuple[UnmatchedSeat, ...]
    empty_tables: tuple[EmptyTable, ...]
    collapsed_only_races: tuple[CollapsedOnlyRace, ...]
    unknown_suffixes: Mapping[str, int]
    variants_dropped: int
    summary_rows_skipped: int = 0
    no_matchup_tables: tuple[NoMatchupTable, ...] = ()
    oversized_tables: tuple[OversizedTable, ...] = ()


@dataclass(frozen=True, slots=True)
class UsPollIndex:
    """Every row scraped in one run, plus what the run could not place.

    The diagnostics are not incidental: the parser is deliberately strict, so a
    Wikipedia layout change shows up as rows moving into ``unmatched_seats`` or
    ``empty_tables`` rather than as silently missing polls. The console's
    summary page renders all of them.

    Attributes:
        rows: The poll rows, contest by contest in page order.
        page_failures: URL → reason for every page that could not be read or
            could not be placed (a second page claiming a seat another page
            already owns, or a race link past a discovery cap). Pages that
            could not be placed are never fetched. Past
            :data:`_MAX_DROPPED_LINKS` rejected links on one index, the rest
            are one entry keyed ``"<contest>: N more discovery link(s)
            dropped"`` — the only key that is not a URL.
        notes: Remarks that are not failures, e.g. a page that is not written
            yet.
        collapsed_only_races: Races whose only tables are hypotheticals.
        unknown_suffixes: Party suffix → number of tables carrying it, for
            suffixes that map to no database party.
        variants_dropped: Repeat rows dropped across the whole run.
        unmatched_seats: Tables whose seat could not be resolved.
        empty_tables: Accepted tables that produced no rows.
        pages_fetched: How many pages were read, for the summary line.
        summary_rows_skipped: Rows dropped for naming a summary "pollster"
            (the generic-ballot table's "Average" row).
        no_matchup_tables: Tables dropped because, in a contest whose polls
            need a matchup, they named fewer than two candidates. Their rows
            are never queued: such a poll could not be used, and a national
            one would be indistinguishable from a legacy poll.
        oversized_tables: Tables abandoned for being too large to read.
    """

    rows: tuple[UsPollRow, ...]
    page_failures: Mapping[str, str]
    notes: tuple[str, ...]
    collapsed_only_races: tuple[CollapsedOnlyRace, ...]
    unknown_suffixes: Mapping[str, int]
    variants_dropped: int
    unmatched_seats: tuple[UnmatchedSeat, ...]
    empty_tables: tuple[EmptyTable, ...]
    pages_fetched: int
    summary_rows_skipped: int = 0
    no_matchup_tables: tuple[NoMatchupTable, ...] = ()
    oversized_tables: tuple[OversizedTable, ...] = ()


# ── Page discovery ────────────────────────────────────────────────────────────


def _href(link: Tag) -> str | None:
    """Read an ``<a href>`` as a single string, or None when it has none."""
    value = link.get("href")
    if isinstance(value, list):
        value = " ".join(value)
    return value or None


def _discovered_pages(
    html: str,
    pattern: re.Pattern[str],
    *,
    keep: Callable[[str], bool],
) -> DiscoveredPages:
    """Return the absolute URLs of an index page's race links, deduped.

    A repeated link is dropped silently — the live index links each race more
    than once. Distinct links are bounded by the state they resolve to, not by
    their spelling: past :data:`_MAX_PAGES_PER_STATE` pages for one state, or
    :data:`_MAX_DISCOVERED_PAGES` in all, a link is reported in
    :attr:`DiscoveredPages.dropped` instead, so an index full of variant
    spellings cannot turn into hundreds of requests. That report is itself
    capped at :data:`_MAX_DROPPED_LINKS` links; any more are only counted, in
    :attr:`DiscoveredPages.dropped_overflow`.

    Args:
        html: The index page source.
        pattern: Matches an ``href`` and captures the page slug.
        keep: Predicate on the state a slug names — territory pages and, for
            the House, the District of Columbia are dropped here.

    Returns:
        One absolute ``https://en.wikipedia.org/wiki/…`` URL per race page, in
        the order the index first links it, plus the links left out.
    """
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    dropped: dict[str, str] = {}
    overflow = 0
    seen: set[str] = set()
    pages_per_state: Counter[str] = Counter()

    def drop(url: str, reason: str) -> None:
        nonlocal overflow
        if len(dropped) < _MAX_DROPPED_LINKS:
            dropped[url] = reason
        else:
            overflow += 1

    for link in soup.find_all("a"):
        if not isinstance(link, Tag):
            continue
        href = _href(link)
        if href is None:
            continue
        match = pattern.match(href)
        if match is None:
            continue
        slug = match.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        state = state_from_page_slug(slug)
        if state is None or not keep(state):
            continue
        url = f"{WIKIPEDIA_BASE}/wiki/{slug}"
        if pages_per_state[state] >= _MAX_PAGES_PER_STATE:
            drop(
                url,
                f"{state} already has {_MAX_PAGES_PER_STATE} race pages on the index",
            )
            continue
        if len(urls) >= _MAX_DISCOVERED_PAGES:
            drop(
                url,
                f"past the {_MAX_DISCOVERED_PAGES}-page limit on one index's "
                "race pages",
            )
            continue
        pages_per_state[state] += 1
        urls.append(url)
    return DiscoveredPages(
        urls=tuple(urls),
        dropped=dropped,
        dropped_overflow=overflow,
    )


def discover_senate_pages(html: str) -> DiscoveredPages:
    """Return the Senate race pages linked from the 2026 Senate index.

    The index links each race as an absolute URL matching
    ``/wiki/2026_United_States_Senate_(special_)?election_in_<State>``. In 2026
    that is 35 pages: the 33 regular class-2 races plus the Florida and Ohio
    specials.

    Args:
        html: The Senate index page source.

    Returns:
        One URL per race page, deduped, in index order, plus any links left
        out for passing a discovery cap.
    """
    return _discovered_pages(html, _SENATE_PAGE_RE, keep=lambda state: True)


def discover_house_pages(html: str) -> DiscoveredPages:
    """Return the per-state House pages linked from the 2026 House index.

    The index links 44 multi-district states as
    ``…_House_of_Representatives_elections_in_<State>`` and the 6 at-large
    states in the singular ``…_election_in_<State>`` form. It also links six
    territory/DC pages — and the Northern Mariana Islands twice — none of which
    has a seat on the House map, so they are dropped here rather than turning
    into unmatched seats later.

    Args:
        html: The House index page source.

    Returns:
        One URL per state page, deduped, in index order, plus any links left
        out for passing a discovery cap.
    """
    return _discovered_pages(
        html,
        _HOUSE_PAGE_RE,
        keep=lambda state: state in HOUSE_DISTRICT_COUNTS,
    )


# ── Fetching ──────────────────────────────────────────────────────────────────


def _fetch_one(url: str, fetcher: Fetcher) -> _FetchResult:
    """Fetch one page, turning every failure into text.

    Returns:
        ``(html, failure, note)`` — exactly one of the three is set.
    """
    try:
        return fetcher(url), None, None
    except HTTPError as err:
        if err.code == 404:
            return None, None, f"{url}: not present yet (HTTP 404)"
        return None, f"HTTP {err.code}: {err.reason}", None
    except Exception as err:  # noqa: BLE001 - per-URL boundary, see below
        # One unreachable page out of ~90 must not abort a whole import run,
        # and a fetcher is free to raise anything (socket, TLS, decoding), so
        # the boundary is deliberately broad. Nothing is swallowed: the
        # traceback is logged, and the reason is reported on the index and
        # rendered on the summary page.
        logger.warning("Fetching %s failed", url, exc_info=True)
        return None, f"{type(err).__name__}: {err}", None


def fetch_pages(
    urls: Sequence[str],
    *,
    fetcher: Fetcher = fetch_html,
    max_workers: int = 4,
) -> FetchedPages:
    """Fetch pages concurrently, recording failures instead of raising.

    A US import reads around 90 Wikipedia pages, so they are fetched in
    parallel (a small pool — this is someone else's server) with the 30-second
    timeout :func:`~polls.importers.us.us_polls_common.fetch_html` applies.

    A 404 is not treated as a failure: an article for a future cycle simply may
    not exist yet, which is the case for the presidential Statewide page today.

    Memory is bounded two ways. Each fetch is capped at
    :data:`~polls.importers.wikipedia_common.MAX_PAGE_BYTES` by the fetcher,
    and at most ``2 * max_workers`` fetches are outstanding (running, or done
    but not yet read) at once, so finished pages cannot pile up behind a slow
    one. The pages kept are capped at :data:`MAX_TOTAL_PAGE_BYTES` of memory in
    total: a page that would pass it is still fetched, but dropped and reported
    as a failure, and a smaller page after it may still fit. Parsing each page
    as it arrives was considered and not done — it would turn
    :func:`fetch_us_poll_index`'s index-then-pages flow inside out for a saving
    these bounds already give.

    Args:
        urls: Pages to fetch. Repeats are fetched once.
        fetcher: Injection point — tests pass a dict-backed fake.
        max_workers: Size of the thread pool.

    Returns:
        A :class:`FetchedPages` holding the sources, the failures and the
        notes, each keyed or ordered by the request order.
    """
    wanted = list(dict.fromkeys(urls))
    pages: dict[str, str] = {}
    failures: dict[str, str] = {}
    notes: list[str] = []
    if not wanted:
        return FetchedPages(pages=pages, failures=failures, notes=())

    workers = max(1, max_workers)
    budget = MAX_TOTAL_PAGE_BYTES
    kept = 0
    queued = iter(wanted)
    outstanding: deque[tuple[str, Future[_FetchResult]]] = deque()
    with ThreadPoolExecutor(max_workers=workers) as pool:

        def submit_next() -> None:
            url = next(queued, None)
            if url is not None:
                outstanding.append((url, pool.submit(_fetch_one, url, fetcher)))

        for _ in range(2 * workers):
            submit_next()
        # Read the futures in request order, so the index is deterministic
        # whatever order the pool finishes them in. Each is dropped as it is
        # read, so a page is held only by ``pages`` once kept.
        while outstanding:
            url, future = outstanding.popleft()
            html, failure, note = future.result()
            del future
            submit_next()
            if html is not None:
                size = sys.getsizeof(html)
                if kept + size > budget:
                    failure = (
                        "dropped: keeping it would pass this batch's "
                        f"{budget // (1024 * 1024)} MiB page budget"
                    )
                else:
                    pages[url] = html
                    kept += size
            if failure is not None:
                failures[url] = failure
            if note is not None:
                notes.append(note)

    return FetchedPages(pages=pages, failures=failures, notes=tuple(notes))


# ── Section, seat and lead rules ──────────────────────────────────────────────


def _headings_text(headings: Sequence[Heading]) -> str:
    """Render a heading path for display: ``"General election › Polling"``."""
    return _HEADING_SEPARATOR.join(heading.text for heading in headings)


def _innermost_anchor(headings: Sequence[Heading]) -> str | None:
    """Return the deepest heading anchor, so a row can link to its section."""
    for heading in reversed(headings):
        if heading.anchor:
            return heading.anchor
    return None


def accepts_table(contest: UsContest, table: ParsedTable) -> bool:
    """Report whether a contest's section rule accepts a parsed table.

    ``house_national`` wants exactly one table: the generic-ballot poll
    *aggregation* table under the "Generic congressional ballot aggregate
    polls" h2. There are no individual generic-ballot polls on Wikipedia, so
    the aggregators' snapshot is the series.

    Every other contest wants the opposite: an individual-poll table in a
    **general election** section, never under a "Primary" heading (CA-40 nests
    its general-election hypotheticals under one) and never an aggregation
    table, whose rows are averages of the polls being imported alongside them.

    Collapsed hypotheticals are not decided here — the caller keeps them aside
    for the uncovered-race fallback.
    """
    if contest.section_rule == "generic_ballot":
        if not table.info.columns.is_aggregation:
            return False
        return any(
            heading.level == 2 and "generic" in heading.text.lower()
            for heading in table.headings
        )

    if table.info.columns.is_aggregation:
        return False
    texts = [heading.text.lower() for heading in table.headings]
    if any(text.startswith("primar") for text in texts):
        return False
    return any("general election" in text for text in texts)


def seat_name_for_table(
    contest: UsContest,
    page_url: str,
    headings: Sequence[Heading],
) -> tuple[str | None, str | None]:
    """Work out which seat a table belongs to.

    Each contest names its races differently:

    - **Senate:** one race per page, so the state comes from the page slug and
      is the seat name on the Senate map.
    - **House districts:** the state comes from the page slug and the district
      from the ``District N`` heading the race sits under, giving ``CA-40``. An
      at-large state's page carries no such heading and is always ``XX-01``.
    - **President:** a path through "Nationwide" is the national reading and
      has no seat; otherwise the deepest heading that names a state or a split
      district wins (``Statewide › Nevada › matchup`` → Nevada).
    - **House national:** national by definition.

    Args:
        contest: The contest being scraped.
        page_url: The page the table sits on.
        headings: The table's heading path, outermost first.

    Returns:
        ``(seat_name, reason)``. ``(None, None)`` is a national reading;
        ``(None, reason)`` means the rules could not place the table.
    """
    if contest.seat_rule == "national":
        return None, None

    if contest.seat_rule == "president":
        if any("nationwide" in heading.text.lower() for heading in headings):
            return None, None
        for heading in reversed(headings):
            seat = president_seat_for_heading(heading.text)
            if seat is not None:
                return seat, None
        return None, "no heading names a state or district"

    state = state_from_page_slug(page_url)
    if state is None:
        return None, "the page slug names no state"

    if contest.seat_rule == "senate_state":
        return state, None

    if state in AT_LARGE_STATES:
        seat = house_seat_name(state, None)
        if seat is None:
            return None, f"{state} has no House seat"
        return seat, None
    for heading in headings:
        match = _DISTRICT_HEADING_RE.match(heading.text)
        if match is None:
            continue
        district = int(match.group(1))
        seat = house_seat_name(state, district)
        if seat is None:
            return None, f"{state} has no district {district}"
        return seat, None
    return None, f"no 'District N' heading above the table ({state})"


@dataclass(frozen=True, slots=True)
class _SeatRef:
    """A resolved seat, or the reason the table could not be placed."""

    seat_name: str | None
    seat_id: int | None
    unmatched: UnmatchedSeat | None


def _resolve_seat(
    contest: UsContest,
    page_url: str,
    table: ParsedTable,
    seat_ids: Mapping[str, int],
) -> _SeatRef:
    """Name a table's seat and look its database id up on the contest's map."""
    seat_name, reason = seat_name_for_table(contest, page_url, table.headings)
    seat_id: int | None = None
    if reason is None and seat_name is not None:
        seat_id = seat_ids.get(seat_name)
        if seat_id is None:
            reason = f"no seat named {seat_name!r} on {contest.map_name!r}"
    if reason is None:
        return _SeatRef(seat_name=seat_name, seat_id=seat_id, unmatched=None)
    return _SeatRef(
        seat_name=None,
        seat_id=None,
        unmatched=UnmatchedSeat(
            contest=contest.slug,
            page_url=page_url,
            heading_path=_headings_text(table.headings),
            seat_name=seat_name,
            reason=reason,
            dropped_rows=len(table.rows),
        ),
    )


def is_lead_table(table: ParsedTable) -> bool:
    """Report whether a table can be a race's lead table.

    The lead table is what :func:`apply_auto_tracked_matchups` reads a race's
    automatic tracked matchup off, so a table that forms no matchup cannot be
    one however early it appears: that rules out the generic-ballot party
    columns, a single-candidate table, and Wikipedia's occasional stray.
    """
    return bool(table.rows) and len(table.candidates) >= 2 and table.matchup is not None


def _sample_label(sample_size: int | None, population: str | None) -> str:
    """Render the sample cell for display, e.g. ``"1,000 (LV)"``.

    Rebuilt from the parsed size and population rather than kept verbatim —
    the parser normalises the cell, and this is the only display use.
    """
    if sample_size is None:
        return f"({population})" if population else ""
    size = f"{sample_size:,}"
    return f"{size} ({population})" if population else size


def _table_notes(table: ParsedTable, *, collapsed: bool) -> tuple[str, ...]:
    """Build the per-table warnings carried on each of its rows."""
    notes: list[str] = []
    if table.unknown_suffixes:
        suffixes = ", ".join(table.unknown_suffixes)
        notes.append(f"unrecognised party suffix: {suffixes}")
    if table.variants_dropped:
        notes.append(
            f"{table.variants_dropped} repeat row(s) dropped "
            "(likely-voter / with-leaners variants)"
        )
    if collapsed:
        notes.append("collapsed hypothetical table")
    return tuple(notes)


def _is_summary_row(contest: UsContest, pollster_label: str) -> bool:
    """Report whether a row summarises the table rather than reporting a poll.

    Wikipedia's generic-ballot aggregation table ends with an "Average" row —
    the mean of the aggregators above it. It parses like any other row, so it
    would otherwise be stored as a pollster of its own and count every
    aggregator twice.
    """
    label = pollster_label.strip().lower()
    return any(label == name.lower() for name in contest.summary_pollster_labels)


def _rows_for_table(
    contest: UsContest,
    page_url: str,
    table: ParsedTable,
    seat: _SeatRef,
    *,
    is_lead: bool,
    collapsed: bool,
) -> tuple[list[UsPollRow], int]:
    """Turn one accepted table's parsed rows into contest rows.

    Returns:
        The rows, and how many summary rows were skipped.
    """
    heading_path = _headings_text(table.headings)
    anchor = _innermost_anchor(table.headings)
    section_url = f"{page_url}#{anchor}" if anchor else page_url
    notes = _table_notes(table, collapsed=collapsed)
    wanted = [
        parsed
        for parsed in table.rows
        if not _is_summary_row(contest, parsed.pollster_label)
    ]
    rows = [
        UsPollRow(
            fieldwork_start=parsed.fieldwork_start,
            fieldwork_end=parsed.fieldwork_end,
            date_label=parsed.date_label,
            pollster_label=parsed.pollster_label,
            pollster_identifier=pollster_identifier(
                parsed.pollster_label, contest.pollster_suffix
            ),
            sample_size_label=_sample_label(parsed.sample_size, parsed.population),
            source_url=parsed.source_url or section_url,
            matchup=table.matchup,
            contest=contest.slug,
            page_url=page_url,
            heading_path=heading_path,
            seat_name=seat.seat_name,
            seat_id=seat.seat_id,
            map_name=contest.map_name,
            readings=parsed.readings,
            sample_size=parsed.sample_size,
            population=parsed.population,
            is_lead=is_lead,
            collapsed=collapsed,
            pollster_tags=parsed.pollster_tags,
            notes=notes,
        )
        for parsed in wanted
    ]
    return rows, len(table.rows) - len(wanted)


def rows_for_page(
    contest: UsContest,
    page_url: str,
    html: str,
    *,
    seat_ids: Mapping[str, int],
    include_collapsed_for_uncovered: bool = False,
) -> PageRows:
    """Apply a contest's rules to one page and return what it contributes.

    Visible tables are taken in document order. The first accepted one per seat
    that forms a matchup is the race's **lead** — Wikipedia puts the nominee
    table first and hides the hypotheticals — and its rows carry ``is_lead``.

    A seat that ends up with no visible rows at all is a *collapsed-only* race:
    it is always reported, and under ``include_collapsed_for_uncovered`` its
    hidden tables' rows are imported too, flagged ``collapsed`` and never lead.

    In a contest that :attr:`~UsContest.requires_matchup`, a table forming no
    matchup contributes no rows and is reported in ``no_matchup_tables``.

    Args:
        contest: The contest being scraped.
        page_url: The page's URL, used for the seat rule and row source links.
        html: The page source.
        seat_ids: Seat name → id on the contest's map.
        include_collapsed_for_uncovered: Opt in to the collapsed fallback.

    Returns:
        The page's rows and everything it could not place.
    """
    page_tables = parse_poll_tables(
        html,
        allow_party_labels=contest.allow_party_labels,
        keep_empty=True,
    )
    visible: list[ParsedTable] = []
    hidden: list[ParsedTable] = []
    for table in page_tables.tables:
        if not accepts_table(contest, table):
            continue
        (hidden if table.info.collapsed else visible).append(table)

    rows: list[UsPollRow] = []
    unmatched: list[UnmatchedSeat] = []
    empty: list[EmptyTable] = []
    no_matchup: list[NoMatchupTable] = []
    unknown: Counter[str] = Counter()
    variants_dropped = 0
    summary_rows_skipped = 0
    seats_with_rows: set[str | None] = set()
    seats_with_lead: set[str | None] = set()

    for table in visible:
        unknown.update(table.unknown_suffixes)
        variants_dropped += table.variants_dropped
        if not table.rows:
            empty.append(
                EmptyTable(
                    contest=contest.slug,
                    page_url=page_url,
                    heading_path=_headings_text(table.headings),
                    collapsed=False,
                )
            )
            continue
        seat = _resolve_seat(contest, page_url, table, seat_ids)
        if seat.unmatched is not None:
            unmatched.append(seat.unmatched)
            continue
        if _lacks_matchup(contest, table):
            no_matchup.append(_no_matchup_table(contest, page_url, table, seat))
            continue
        is_lead = seat.seat_name not in seats_with_lead and is_lead_table(table)
        if is_lead:
            seats_with_lead.add(seat.seat_name)
        table_rows, skipped = _rows_for_table(
            contest, page_url, table, seat, is_lead=is_lead, collapsed=False
        )
        rows.extend(table_rows)
        summary_rows_skipped += skipped
        seats_with_rows.add(seat.seat_name)

    collapsed_only, collapsed_variants, collapsed_summary = _collapsed_fallback(
        contest,
        page_url,
        hidden,
        seat_ids=seat_ids,
        seats_with_rows=seats_with_rows,
        include=include_collapsed_for_uncovered,
        rows=rows,
        empty=empty,
        no_matchup=no_matchup,
        unknown=unknown,
    )
    variants_dropped += collapsed_variants
    summary_rows_skipped += collapsed_summary

    return PageRows(
        rows=tuple(rows),
        unmatched_seats=tuple(unmatched),
        empty_tables=tuple(empty),
        collapsed_only_races=tuple(collapsed_only),
        unknown_suffixes=dict(unknown),
        variants_dropped=variants_dropped,
        summary_rows_skipped=summary_rows_skipped,
        no_matchup_tables=tuple(no_matchup),
        oversized_tables=_oversized_tables(contest, page_url, page_tables),
    )


def _oversized_tables(
    contest: UsContest,
    page_url: str,
    page_tables: PageTables,
) -> tuple[OversizedTable, ...]:
    """Report a page's unread tables: each oversized one, then any skipped.

    The tables skipped once the page's grid budget ran out become one line, so
    the report stays bounded however many there were.
    """
    oversized = [
        OversizedTable(
            contest=contest.slug,
            page_url=page_url,
            heading_path=_headings_text(headings),
        )
        for headings in page_tables.oversized
    ]
    if page_tables.budget_skipped:
        oversized.append(
            OversizedTable(
                contest=contest.slug,
                page_url=page_url,
                heading_path=(
                    f"{page_tables.budget_skipped} further table(s) not read: "
                    f"the page's {MAX_PAGE_GRID_CELLS:,}-cell grid budget "
                    "was reached"
                ),
            ),
        )
    return tuple(oversized)


def _lacks_matchup(contest: UsContest, table: ParsedTable) -> bool:
    """Report whether a table's rows would be unusable for want of a matchup."""
    return contest.requires_matchup and table.matchup is None


def _no_matchup_table(
    contest: UsContest,
    page_url: str,
    table: ParsedTable,
    seat: _SeatRef,
) -> NoMatchupTable:
    """Describe a table dropped by :func:`_lacks_matchup`."""
    return NoMatchupTable(
        contest=contest.slug,
        page_url=page_url,
        heading_path=_headings_text(table.headings),
        seat_name=seat.seat_name,
        dropped_rows=len(table.rows),
    )


def _collapsed_fallback(
    contest: UsContest,
    page_url: str,
    hidden: Sequence[ParsedTable],
    *,
    seat_ids: Mapping[str, int],
    seats_with_rows: set[str | None],
    include: bool,
    rows: list[UsPollRow],
    empty: list[EmptyTable],
    no_matchup: list[NoMatchupTable],
    unknown: Counter[str],
) -> tuple[list[CollapsedOnlyRace], int, int]:
    """Report, and optionally import, the hidden tables of uncovered races.

    A race with a visible table is left alone: its hypotheticals are answers to
    questions Wikipedia chose not to promote. A race with none has only its
    hypotheticals, so the user can opt in to them — never as the lead table,
    always flagged ``collapsed``, so the review step can say where they came
    from.

    A hidden table that forms no matchup where the contest needs one is left
    out of the race's available rows; it is reported in ``no_matchup`` only
    when ``include`` is set, since otherwise nothing was going to be imported
    from it anyway.

    ``rows``, ``empty``, ``no_matchup`` and ``unknown`` are appended to in
    place.

    Returns:
        The uncovered races, how many repeat rows their tables dropped, and how
        many summary rows they skipped (both counted only when the tables were
        imported).
    """
    grouped: dict[str | None, list[tuple[_SeatRef, ParsedTable]]] = {}
    for table in hidden:
        seat = _resolve_seat(contest, page_url, table, seat_ids)
        if seat.unmatched is not None:
            # Not reported: nothing was going to be imported from it anyway,
            # and a page full of unplaceable hypotheticals would drown out the
            # visible tables that really did fail to place.
            continue
        if seat.seat_name in seats_with_rows:
            continue
        if not table.rows:
            empty.append(
                EmptyTable(
                    contest=contest.slug,
                    page_url=page_url,
                    heading_path=_headings_text(table.headings),
                    collapsed=True,
                )
            )
            continue
        if _lacks_matchup(contest, table):
            if include:
                no_matchup.append(_no_matchup_table(contest, page_url, table, seat))
            continue
        grouped.setdefault(seat.seat_name, []).append((seat, table))

    races: list[CollapsedOnlyRace] = []
    variants_dropped = 0
    summary_rows_skipped = 0
    for seat_name, entries in grouped.items():
        races.append(
            CollapsedOnlyRace(
                contest=contest.slug,
                page_url=page_url,
                seat_name=seat_name,
                available_rows=sum(len(table.rows) for _, table in entries),
                included=include,
            )
        )
        if not include:
            continue
        for seat, table in entries:
            unknown.update(table.unknown_suffixes)
            variants_dropped += table.variants_dropped
            table_rows, skipped = _rows_for_table(
                contest, page_url, table, seat, is_lead=False, collapsed=True
            )
            rows.extend(table_rows)
            summary_rows_skipped += skipped
    return races, variants_dropped, summary_rows_skipped


# ── The run ───────────────────────────────────────────────────────────────────


def normalise_states(states: Iterable[str]) -> tuple[list[str], list[str]]:
    """Resolve a state filter to canonical names.

    Accepts full names in any case and postal codes, so ``["tx", "Michigan"]``
    works.

    Returns:
        ``(canonical names, unrecognised inputs)``.
    """
    names: list[str] = []
    unknown: list[str] = []
    for value in states:
        canonical = canonical_state(value, allow_postal=True)
        if canonical is None:
            unknown.append(value)
        elif canonical not in names:
            names.append(canonical)
    return names, unknown


def _seat_ids_by_map_id(db: Database, map_id: int) -> dict[str, int]:
    """Return seat name → id for a map."""
    return {seat.seat_name: seat.id for seat in db.get_seats_for_map(map_id)}


def _seat_ids_for_map(db: Database, map_name: str) -> Mapping[str, int] | None:
    """Return seat name → id for a map, or None when the map does not exist."""
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        return None
    return _seat_ids_by_map_id(db, poll_map.id)


def _contest_page_urls(
    contest: UsContest,
    *,
    index_html: str | None,
    wanted_states: Sequence[str] | None,
) -> tuple[list[str], dict[str, str]]:
    """List the pages a contest should read, honouring the state filter.

    Returns:
        ``(urls, failures)`` — the pages to fetch, and URL → reason for each
        page that is not to be fetched because it could not be placed: a race
        link past a discovery cap, or a second page for a seat another page
        already claims. Both are decided here, before any fetch, so an
        unplaceable page costs no request.
    """
    urls = list(contest.page_urls)
    failures: dict[str, str] = {}
    discovered: DiscoveredPages | None = None
    if index_html is not None:
        if contest.discovery == "senate_index":
            discovered = discover_senate_pages(index_html)
        elif contest.discovery == "house_index":
            discovered = discover_house_pages(index_html)
        if discovered is not None:
            urls.extend(discovered.urls)
            failures.update(discovered.dropped)
    # One line for the rejected links past the cap. It is kept out of
    # ``failures`` until the state filter has run, which would drop it (the key
    # names no state), and keyed by contest so two indexes' entries stay apart.
    # Under a filter the count covers every state: overflow links are not
    # kept, so they cannot be filtered.
    overflow: dict[str, str] = {}
    if discovered is not None and discovered.dropped_overflow:
        count = discovered.dropped_overflow
        overflow[f"{contest.slug}: {count} more discovery link(s) dropped"] = (
            f"past the {_MAX_DROPPED_LINKS}-link cap on one index's rejected "
            "race links"
        )
    wanted = list(dict.fromkeys(urls))
    if not contest.is_per_state:
        return wanted, {**failures, **overflow}

    if wanted_states is not None:
        allowed = set(wanted_states)
        wanted = [url for url in wanted if state_from_page_slug(url) in allowed]
        failures = {
            url: reason
            for url, reason in failures.items()
            if state_from_page_slug(url) in allowed
        }
    failures.update(overflow)

    # A seat is named after its state, so two pages for one state — a regular
    # and a special race in the same cycle — cannot be told apart. The first
    # page keeps the seat and the rest are reported.
    kept: list[str] = []
    claimed: dict[str, str] = {}
    for url in wanted:
        state = state_from_page_slug(url)
        owner = claimed.get(state) if state is not None else None
        if owner is not None:
            failures[url] = (
                f"{state} is already covered by {owner} — "
                "two races for one seat cannot be told apart"
            )
            continue
        if state is not None:
            claimed[state] = url
        kept.append(url)
    return kept, failures


def fetch_us_poll_index(
    db: Database,
    contests: Sequence[UsContest],
    *,
    states: Sequence[str] | None = None,
    include_collapsed_for_uncovered: bool = False,
    fetcher: Fetcher = fetch_html,
    max_workers: int = 4,
) -> UsPollIndex:
    """Scrape every requested contest and return its rows plus its diagnostics.

    Runs in four passes. The maps come first, because a contest whose map is
    missing can import nothing and there is no point fetching its ~35 pages to
    find that out. Then the index pages (a contest's race pages are links on
    one), then every race page, then the parsing. A page fetched for one
    contest is reused by another — the House index is both
    ``house_national``'s only page and ``house_districts``' directory. A race
    page that could not be placed (see :func:`_contest_page_urls`) is reported
    in ``page_failures`` and never fetched.

    ``db`` is read only: each contest's map supplies the seat names its rows can
    attach to. A table whose seat is not on that map is reported in
    ``unmatched_seats`` rather than imported without one.

    Args:
        db: Database to resolve seat names against. Nothing is written.
        contests: The contests to scrape, in the order to scrape them.
        states: Restrict the per-state race pages to these states, given as
            names or postal codes. None means every state.
        include_collapsed_for_uncovered: Import hidden hypothetical tables for
            races that have no visible table.
        fetcher: Injection point — tests pass a dict-backed fake.
        max_workers: Size of the fetch thread pool.

    Returns:
        A :class:`UsPollIndex`.
    """
    notes: list[str] = []
    page_failures: dict[str, str] = {}
    wanted_states: list[str] | None = None
    if states is not None:
        wanted_states, unknown_states = normalise_states(states)
        for value in unknown_states:
            notes.append(f"ignored unrecognised state filter: {value!r}")

    scoped: list[tuple[UsContest, Mapping[str, int]]] = []
    for contest in contests:
        seat_ids = _seat_ids_for_map(db, contest.map_name)
        if seat_ids is None:
            notes.append(
                f"{contest.slug}: skipped — no map named {contest.map_name!r}"
            )
            continue
        scoped.append((contest, seat_ids))

    index_urls = [
        contest.index_url for contest, _ in scoped if contest.index_url is not None
    ]
    fetched = fetch_pages(index_urls, fetcher=fetcher, max_workers=max_workers)
    pages: dict[str, str] = dict(fetched.pages)
    page_failures.update(fetched.failures)
    notes.extend(fetched.notes)

    urls_by_contest: dict[str, list[str]] = {}
    for contest, _ in scoped:
        index_html = pages.get(contest.index_url) if contest.index_url else None
        if contest.index_url is not None and index_html is None:
            notes.append(
                f"{contest.slug}: no race pages — its index could not be read"
            )
        urls, unplaced = _contest_page_urls(
            contest, index_html=index_html, wanted_states=wanted_states
        )
        urls_by_contest[contest.slug] = urls
        page_failures.update(unplaced)

    outstanding = [
        url
        for urls in urls_by_contest.values()
        for url in urls
        if url not in pages and url not in page_failures
    ]
    fetched = fetch_pages(outstanding, fetcher=fetcher, max_workers=max_workers)
    pages.update(fetched.pages)
    page_failures.update(fetched.failures)
    notes.extend(fetched.notes)

    rows: list[UsPollRow] = []
    unmatched: list[UnmatchedSeat] = []
    empty: list[EmptyTable] = []
    no_matchup: list[NoMatchupTable] = []
    oversized: list[OversizedTable] = []
    collapsed_only: list[CollapsedOnlyRace] = []
    unknown_suffixes: Counter[str] = Counter()
    variants_dropped = 0
    summary_rows_skipped = 0

    for contest, seat_ids in scoped:
        for url in urls_by_contest[contest.slug]:
            html = pages.get(url)
            if html is None:
                continue
            page_rows = rows_for_page(
                contest,
                url,
                html,
                seat_ids=seat_ids,
                include_collapsed_for_uncovered=include_collapsed_for_uncovered,
            )
            rows.extend(page_rows.rows)
            unmatched.extend(page_rows.unmatched_seats)
            empty.extend(page_rows.empty_tables)
            no_matchup.extend(page_rows.no_matchup_tables)
            oversized.extend(page_rows.oversized_tables)
            collapsed_only.extend(page_rows.collapsed_only_races)
            unknown_suffixes.update(page_rows.unknown_suffixes)
            variants_dropped += page_rows.variants_dropped
            summary_rows_skipped += page_rows.summary_rows_skipped

    return UsPollIndex(
        rows=tuple(rows),
        page_failures=page_failures,
        notes=tuple(notes),
        collapsed_only_races=tuple(collapsed_only),
        unknown_suffixes=dict(unknown_suffixes),
        variants_dropped=variants_dropped,
        unmatched_seats=tuple(unmatched),
        empty_tables=tuple(empty),
        pages_fetched=len(pages),
        summary_rows_skipped=summary_rows_skipped,
        no_matchup_tables=tuple(no_matchup),
        oversized_tables=tuple(oversized),
    )


# ── Importing ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PlannedUsPollRow:
    """One reading of a scraped poll, resolved to a database party.

    Attributes:
        party_id: Primary key of the ``Party`` the reading belongs to.
        party_name: That party's name, for the review page.
        candidate_name: The candidate polled, or None for a party column
            (the generic ballot).
        percentage: The reading as written, not rescaled.
    """

    party_id: int
    party_name: str
    candidate_name: str | None
    percentage: float


@dataclass(frozen=True, slots=True)
class UsImportPlan:
    """Everything one scraped row would write, worked out before writing it.

    Attributes:
        map_id: The contest's map.
        seat_id: The seat the poll covers, or None for a national reading.
        pollster_identifier: The pollster slug, contest suffix included.
        pollster_name: The pollster's stored name — its existing one, or the
            name it would be created under.
        pollster_exists: False when the commit would create the pollster.
        rows: The readings that resolved to a party, in table order.
        unknown_parties: Readings that did not, named by their party or, for
            an unrecognised suffix, by their candidate. Reported, not imported.
        warnings: Human-readable remarks for the review step.
    """

    map_id: int
    seat_id: int | None
    pollster_identifier: str
    pollster_name: str
    pollster_exists: bool
    rows: tuple[PlannedUsPollRow, ...]
    unknown_parties: tuple[str, ...]
    warnings: tuple[str, ...]


def build_us_import_plan(db: Database, row: UsPollRow) -> UsImportPlan:
    """Resolve a scraped row against the database, without writing anything.

    The seat is looked up by *name* on the contest's map rather than trusted
    from the row: a queued row can be hours old, and the name is the stable
    identity.

    A reading whose party is unknown — an unrecognised suffix, or a party the
    database does not hold — is reported and left out rather than failing the
    row: Wikipedia's minor-party columns should not cost the import its
    two-party readings.

    Args:
        db: Database to resolve against. Nothing is written.
        row: The scraped row to plan.

    Returns:
        A :class:`UsImportPlan`.

    Raises:
        ValueError: If the row names no known contest, its map is missing, its
            seat is not on that map, or none of its readings resolve to a
            party — all of which would otherwise store a poll with no figures —
            or if its contest needs a matchup and the row has none, which would
            store a poll nothing can read.
    """
    contest = US_CONTESTS_BY_SLUG.get(row.contest)
    if contest is None:
        raise ValueError(f"unknown contest: {row.contest!r}")
    if contest.requires_matchup and row.matchup is None:
        raise ValueError(
            f"{row.pollster_label!r} has no matchup, which every"
            f" {contest.slug} poll needs"
        )

    poll_map = db.get_map_by_name(row.map_name)
    if poll_map is None:
        raise ValueError(f"no map named {row.map_name!r}")

    seat_id: int | None = None
    if row.seat_name is not None:
        seat_id = _seat_ids_by_map_id(db, poll_map.id).get(row.seat_name)
        if seat_id is None:
            raise ValueError(f"no seat named {row.seat_name!r} on {row.map_name!r}")

    party_ids = {party.name: party.id for party in db.get_all_parties()}
    planned: list[PlannedUsPollRow] = []
    unknown: list[str] = []
    warnings: list[str] = []
    for reading in row.readings:
        if reading.party_name is None:
            label = reading.candidate_name or "(unnamed column)"
            if label not in unknown:
                unknown.append(label)
                warnings.append(f"no party for {label} — unrecognised suffix")
            continue
        party_id = party_ids.get(reading.party_name)
        if party_id is None:
            if reading.party_name not in unknown:
                unknown.append(reading.party_name)
                warnings.append(
                    f"party not in the database: {reading.party_name!r}"
                )
            continue
        planned.append(
            PlannedUsPollRow(
                party_id=party_id,
                party_name=reading.party_name,
                candidate_name=reading.candidate_name or None,
                percentage=reading.percentage,
            )
        )

    if not planned:
        raise ValueError(
            f"no reading of {row.pollster_label!r} resolved to a party"
            f" (saw: {', '.join(unknown) or 'nothing'})"
        )

    pollster = db.get_pollster_by_identifier(row.pollster_identifier)
    return UsImportPlan(
        map_id=poll_map.id,
        seat_id=seat_id,
        pollster_identifier=row.pollster_identifier,
        pollster_name=(
            pollster.name
            if pollster is not None
            else f"{row.pollster_label} ({contest.pollster_label})"
        ),
        pollster_exists=pollster is not None,
        rows=tuple(planned),
        unknown_parties=tuple(unknown),
        warnings=tuple(warnings),
    )


def commit_us_import_plan(
    db: Database,
    row: UsPollRow,
    plan: UsImportPlan,
) -> PollImportResult:
    """Write one planned poll, pollster and rows in a single transaction.

    Everything happens in one session, so a row that fails to insert takes its
    poll and its pollster back out with it rather than leaving a poll with no
    figures behind.

    The poll's identity is the five-tuple ``(pollster identifier, fieldwork
    start, fieldwork end, matchup, seat)``, compared with ``IS`` so that a null
    matchup or a national scope matches itself. It is re-checked here and not
    only when the plan was built: the queue can have been sitting on the plan
    while another import stored the same poll.

    Rows are national *within their seat* (``region_id`` NULL) — the seat is on
    the poll. Two candidates of one party each get their own row; the model
    sums them.

    Args:
        db: Database to write to.
        row: The scraped row the plan was built from.
        plan: Its :class:`UsImportPlan`.

    Returns:
        A :class:`PollImportResult`. An already-stored poll comes back with
        ``skipped_existing_rows`` set and its id, having written nothing.

    Raises:
        ValueError: If the plan's seat is no longer on its map.
    """
    with db.session() as session:
        existing_id = session.execute(
            select(Poll.id)
            .join(Pollster, Poll.pollster_id == Pollster.id)
            .where(
                Poll.map_id == plan.map_id,
                Pollster.identifier == plan.pollster_identifier,
                Poll.fieldwork_start == row.fieldwork_start,
                Poll.fieldwork_end == row.fieldwork_end,
                Poll.matchup.is_not_distinct_from(row.matchup),
                Poll.seat_id.is_not_distinct_from(plan.seat_id),
            )
        ).scalar()
        if existing_id is not None:
            return PollImportResult(
                created_pollster=False,
                created_poll=False,
                poll_id=existing_id,
                inserted_rows=0,
                replaced_rows=0,
                skipped_existing_rows=True,
            )

        if plan.seat_id is not None:
            seat = session.get(Seat, plan.seat_id)
            if seat is None or seat.map_id != plan.map_id:
                raise ValueError(f"seat {plan.seat_id} is not on map {plan.map_id}")

        pollster = session.execute(
            select(Pollster).where(Pollster.identifier == plan.pollster_identifier)
        ).scalar_one_or_none()
        created_pollster = pollster is None
        if pollster is None:
            pollster = Pollster(
                name=plan.pollster_name,
                identifier=plan.pollster_identifier,
                weight=1.0,
            )
            session.add(pollster)
            session.flush()

        poll = Poll(
            pollster_id=pollster.id,
            map_id=plan.map_id,
            fieldwork_start=row.fieldwork_start,
            fieldwork_end=row.fieldwork_end,
            sample_size=row.sample_size,
            source_url=row.source_url,
            matchup=row.matchup,
            seat_id=plan.seat_id,
        )
        session.add(poll)
        session.flush()
        poll_id = poll.id
        session.add_all(
            [
                PollRow(
                    poll_id=poll_id,
                    region_id=None,
                    party_id=planned.party_id,
                    percentage=planned.percentage,
                    candidate_name=planned.candidate_name,
                )
                for planned in plan.rows
            ]
        )

    return PollImportResult(
        created_pollster=created_pollster,
        created_poll=True,
        poll_id=poll_id,
        inserted_rows=len(plan.rows),
        replaced_rows=0,
        skipped_existing_rows=False,
    )


#: The outcomes :func:`apply_auto_tracked_matchups` counts, in report order.
AUTO_TRACKING_OUTCOMES: tuple[str, ...] = (
    "created",
    "updated",
    "unchanged",
    "kept_manual",
    "no_polls",
)


def apply_auto_tracked_matchups(
    db: Database,
    rows: Sequence[UsPollRow],
) -> dict[str, int]:
    """Point each ``auto_lead`` race at its lead table's matchup.

    Only the contests whose ``matchup_policy`` is ``"auto_lead"`` (the Senate
    races and the House districts) nominate their own matchup: the President
    follows one matchup the user picks, and the generic ballot has none.

    A race is only tracked once it has **stored** polls for that matchup, so a
    run whose every poll was a duplicate, or whose rows all failed, does not
    move a race onto a matchup the model would then find empty. A manual
    override is never overwritten — :meth:`Database.set_tracked_matchup`
    reports that as ``kept_manual`` and still records the automatic value.

    Args:
        db: Database to write to.
        rows: The scraped rows of a run, in any order. Rows that are not a
            race's lead are ignored.

    Returns:
        A count per outcome: ``created``, ``updated``, ``unchanged``,
        ``kept_manual`` (a manual override stood) and ``no_polls`` (the race's
        matchup has nothing stored).
    """
    counts = dict.fromkeys(AUTO_TRACKING_OUTCOMES, 0)
    scopes: dict[str, tuple[int, Mapping[str, int]] | None] = {}
    leads: dict[tuple[int, int], str] = {}
    for row in rows:
        contest = US_CONTESTS_BY_SLUG.get(row.contest)
        if contest is None or contest.matchup_policy != "auto_lead":
            continue
        # A national row cannot name a race, and Database.set_tracked_matchup
        # rejects an automatic write with no matchup, so both are left to the
        # user.
        if not row.is_lead or row.matchup is None or row.seat_name is None:
            continue
        if row.map_name not in scopes:
            poll_map = db.get_map_by_name(row.map_name)
            scopes[row.map_name] = (
                None
                if poll_map is None
                else (poll_map.id, _seat_ids_by_map_id(db, poll_map.id))
            )
        scope = scopes[row.map_name]
        if scope is None:
            continue
        map_id, seat_ids = scope
        seat_id = seat_ids.get(row.seat_name)
        if seat_id is None:
            continue
        # One lead table per race; a second page claiming the seat is already a
        # page failure, so the first wins here.
        leads.setdefault((map_id, seat_id), row.matchup)

    stored_scopes: dict[int, dict[tuple[int | None, str | None], date]] = {}
    for (map_id, seat_id), matchup in leads.items():
        if map_id not in stored_scopes:
            stored_scopes[map_id] = db.get_latest_poll_end_by_scope(map_id)
        if (seat_id, matchup) not in stored_scopes[map_id]:
            counts["no_polls"] += 1
            continue
        outcome = db.set_tracked_matchup(map_id, seat_id, matchup, source="auto")
        counts[outcome] += 1
    return counts
