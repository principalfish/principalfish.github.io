"""Naming, slug and value-normalisation helpers for the election export."""

from __future__ import annotations

import json
import re

from models import Election, ElectionType, Map, Party, Vote


PARTY_NAME_TO_KEY = {
    "alba": "alba",
    "albaparty": "alba",
    "alliance": "alliance",
    "conservative": "conservative",
    "democraticunionistparty": "dup",
    "green": "green",
    "labour": "labour",
    "liberaldemocrats": "libdems",
    "plaidcymru": "plaidcymru",
    "reformuk": "reform",
    "scottishgreens": "scottishgreens",
    "scottishgreensscottishgreens": "scottishgreens",
    "sdlp": "sdlp",
    "sinnfein": "sinnfein",
    "scottishnationalparty": "snp",
    "ulsterunionistparty": "uu",
    "democratic": "democrat",
    "republican": "republican",
    "independent": "independent",
    "libertarian": "libertarian",
    "usgreen": "usgreen",
    "other": "other",
    "others": "others",
}


def normalize_token(value: str) -> str:
    """Strip all non-alphanumeric characters and lowercase the result.

    Args:
        value: Arbitrary string to normalise.

    Returns:
        Lowercased string containing only ASCII letters and digits.
    """
    return re.sub(r"[^a-z0-9]", "", value.lower())


def slugify(value: str) -> str:
    """Convert a string to a URL-safe slug using hyphens as separators.

    Args:
        value: Arbitrary string to slugify.

    Returns:
        Lowercased hyphen-separated slug. Falls back to ``"election"`` if the
        input contains no alphanumeric characters.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "election"


def legacy_party_key_for_vote(vote: Vote, election_year: int | None = None) -> str:
    """Resolve the legacy party key string for a vote row.

    Checks ``vote.party.short_name`` first, then falls back to
    ``vote.party.name``.  Handles the Reform UK / UKIP split: the same party
    is keyed as ``"reform"`` for 2024+ elections and ``"ukip"`` for earlier
    ones.

    Args:
        vote: Vote ORM row, with ``party`` relationship eagerly loaded.
        election_year: Four-digit year of the parent election, used to
            distinguish Reform UK from UKIP.  Pass ``None`` when unknown.

    Returns:
        Normalised party key string (e.g. ``"labour"``, ``"reform"``).
        Falls back to the normalised party name if no mapping is found.
        Returns ``"others"`` when ``vote.party`` is ``None``.
    """
    if vote.party is None:
        return "others"

    short_name = (vote.party.short_name or "").strip()
    if short_name:
        normalized_short = normalize_token(short_name)
        if normalized_short == "reformuk":
            return "reform" if (election_year is not None and election_year >= 2024) else "ukip"
        if normalized_short in PARTY_NAME_TO_KEY:
            return PARTY_NAME_TO_KEY[normalized_short]

    normalized_name = normalize_token(vote.party.name)
    if normalized_name == "reformuk":
        return "reform" if (election_year is not None and election_year >= 2024) else "ukip"
    return PARTY_NAME_TO_KEY.get(normalized_name, normalized_name)


def normalize_region_name(value: str | None) -> str:
    """Convert a region display name to a compact lowercase key.

    Slugifies the value and removes all hyphens, producing a single lowercase
    word suitable for use as a dict key (e.g. ``"East Midlands"`` →
    ``"eastmidlands"``).

    Args:
        value: Raw region name string, or ``None``.

    Returns:
        Compact lowercase key string.  Returns ``"unknown"`` when ``value``
        is ``None`` or empty.
    """
    if not value:
        return "unknown"
    return slugify(value).replace("-", "")


def normalize_vote_total_value(value: float) -> int | float:
    """Round a vote total and collapse whole numbers to ``int``.

    Rounds to two decimal places; if the result is a whole number it is
    returned as ``int`` to keep JSON output compact.

    Args:
        value: Raw vote total (may be a float from the DB or a calculation).

    Returns:
        ``int`` when the rounded value has no fractional part, otherwise a
        ``float`` rounded to two decimal places.
    """
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def choose_map_template_filename(map_row: Map) -> str:
    """Return the legacy map template filename appropriate for a map row.

    Selects the post-2022 boundary file (``650map_new.json``) when the map
    name contains ``"post 2022"`` or ``"2024"``; falls back to
    ``650map.json`` for older boundaries.

    Args:
        map_row: Map ORM row whose ``name`` is used for detection.

    Returns:
        Filename string (without directory prefix) of the template to use.
    """
    name = map_row.name.lower()
    if "post 2022" in name or "2024" in name:
        return "650map_new.json"
    return "650map.json"


def map_filename_for_map_id(map_id: int) -> str:
    """Return the TopoJSON output filename for the given map primary key.

    Args:
        map_id: Primary key of the Map row.

    Returns:
        Filename string of the form ``"map-{map_id}.topo.json"``.
    """
    return f"map-{map_id}.topo.json"


def file_stem_for_election(election: Election) -> str:
    """Derive the output JSON filename stem for an election.

    Special cases:
    - ``model_uns`` elections → ``"prediction-simulation"``
    - UK general elections matching ``"{year} General Election"`` →
      ``"uk-general-{year}"``
    - All others → ``"{type}-{year}-{name}"`` slugified.

    Args:
        election: Election ORM row with ``type``, ``name``, and ``year``
            populated.

    Returns:
        Filename stem string (no extension) used to construct the results
        JSON path under ``electionmaps/data/results/``.
    """
    if election.type == ElectionType.model_uns:
        return "prediction-simulation"

    general_match = re.fullmatch(r"(\d{4})\s+General\s+Election", election.name)
    if election.type == ElectionType.uk_general and general_match:
        year = general_match.group(1)
        return f"uk-general-{year}"

    holyrood_match = re.fullmatch(r"(\d{4})\s+Scottish Parliament Election", election.name)
    if election.type == ElectionType.holyrood_general and holyrood_match:
        return f"holyrood-general-{holyrood_match.group(1)}"

    holyrood_boundaries_match = re.fullmatch(r"(\d{4})\s+Scottish Parliament Election \(\d{4} Boundaries\)", election.name)
    if election.type == ElectionType.holyrood_general and holyrood_boundaries_match:
        return f"holyrood-general-{holyrood_boundaries_match.group(1)}-changed-boundaries"

    return slugify(f"{election.type.value}-{election.year}-{election.name}")


def manifest_id_for_election(election: Election) -> str:
    """Derive the stable manifest ``id`` string for an election.

    Special cases:
    - ``model_uns`` elections → ``"current-prediction"``
    - UK general elections matching ``"{year} General Election"`` →
      ``"{year}-general"``
    - All others → ``"{year}-{name}"`` slugified.

    Args:
        election: Election ORM row with ``type``, ``name``, and ``year``
            populated.

    Returns:
        Stable string identifier used as the ``id`` field in
        ``map-modes.json``.
    """
    if election.type == ElectionType.model_uns:
        return "current-prediction"

    general_match = re.fullmatch(r"(\d{4})\s+General\s+Election", election.name)
    if election.type == ElectionType.uk_general and general_match:
        return f"{general_match.group(1)}-general"

    holyrood_match = re.fullmatch(r"(\d{4})\s+Scottish Parliament Election", election.name)
    if election.type == ElectionType.holyrood_general and holyrood_match:
        return f"{holyrood_match.group(1)}-holyrood"

    return slugify(f"{election.year}-{election.name}")


def manifest_name_for_election(election: Election) -> str:
    """Return the human-readable display name for an election in the manifest.

    Shortens the verbose DB names to the curated forms used in the front-end:

    - ``model_uns`` → ``"Current prediction"``
    - ``uk_general`` ``"{year} General Election"`` → ``"{year} Election"``
    - ``holyrood_general`` ``"{year} Scottish Parliament Election"`` →
      ``"{year} Election"``
    - ``holyrood_general`` ``"{year} Scottish Parliament Election ({yyyy} Boundaries)"``
      → ``"{year} Election ({yyyy} boundaries)"``
    - anything else → the ``name`` field verbatim.

    Args:
        election: Election ORM row.

    Returns:
        The display name string used in ``map-modes.json``.
    """
    if election.type == ElectionType.model_uns:
        return "Current prediction"

    general_match = re.fullmatch(r"(\d{4})\s+General\s+Election", election.name)
    if election.type == ElectionType.uk_general and general_match:
        return f"{general_match.group(1)} Election"

    if election.type == ElectionType.holyrood_general:
        boundaries_match = re.fullmatch(
            r"(\d{4})\s+Scottish Parliament Election \((\d{4}) Boundaries\)", election.name
        )
        if boundaries_match:
            return f"{boundaries_match.group(1)} Election ({boundaries_match.group(2)} boundaries)"

        holyrood_match = re.fullmatch(r"(\d{4})\s+Scottish Parliament Election", election.name)
        if holyrood_match:
            return f"{holyrood_match.group(1)} Election"

    return election.name


def party_key_for_party(party: Party) -> str:
    """Resolve the canonical party key string for a Party row.

    Checks ``party.short_name`` first (preferred), then falls back to
    ``party.name``.  Handles UKIP / Reform UK disambiguation (always maps
    to ``"ukip"`` or ``"reform"`` based on the normalised name, without
    year context).

    Args:
        party: Party ORM row with ``short_name`` and ``name`` populated.

    Returns:
        Canonical party key string (e.g. ``"labour"``, ``"reform"``).
        Falls back to the normalised party name when no explicit mapping
        exists.
    """
    short_name = (party.short_name or "").strip()
    if short_name:
        normalized_short = normalize_token(short_name)
        if normalized_short == "reformuk":
            return "reform"
        if normalized_short == "ukip":
            return "ukip"
        if normalized_short in PARTY_NAME_TO_KEY:
            return PARTY_NAME_TO_KEY[normalized_short]

    normalized_name = normalize_token(party.name)
    if normalized_name in {"ukip", "ukindependenceparty"}:
        return "ukip"
    if normalized_name == "reformuk":
        return "reform"
    return PARTY_NAME_TO_KEY.get(normalized_name, normalized_name)
