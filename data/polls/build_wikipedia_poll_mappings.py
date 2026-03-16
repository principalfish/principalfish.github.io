#!/usr/bin/env python3
"""Build poll source mappings from the Wikipedia UK polling page.

This script:
1) Scrapes the "National poll results" table rows (since 2024 election onward)
2) Resolves citation references to source URLs
3) Classifies source format (pdf/xlsx/html/etc.)
4) Suggests canonical parser identifiers per pollster

Outputs:
- polls/mappings/wikipedia_national_polls_mapping.csv
- polls/mappings/pollster_parser_profiles.json
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Tag

WIKI_URL = "https://en.wikipedia.org/wiki/Opinion_polling_for_the_next_United_Kingdom_general_election"
OUT_DIR = Path(__file__).resolve().parent / "mappings"
CSV_OUT = OUT_DIR / "wikipedia_national_polls_mapping.csv"
JSON_OUT = OUT_DIR / "pollster_parser_profiles.json"
REGISTRY_OUT = OUT_DIR / "parser_registry.json"


@dataclass
class PollSourceRow:
    date_label: str
    year_label: str
    pollster_label: str
    citation_id: str
    source_url: str
    format_family: str
    parser_identifier: str
    notes: str


def fetch_html(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; poll-mapper/1.0)",
        },
    )
    with urlopen(req) as response:
        data: bytes = response.read()
        return data.decode("utf-8", errors="replace")


def normalize_pollster_name(value: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]", "", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\(.*?\)", "", cleaned).strip()
    cleaned = cleaned.replace("/", "_")
    cleaned = re.sub(r"[^a-zA-Z0-9_ ]", "", cleaned)
    cleaned = cleaned.lower().replace(" ", "_")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")

    if "find_out_now" in cleaned and "electoral_calculus" in cleaned:
        return "find_out_now_electoral_calculus"
    if "yougov" in cleaned:
        return "yougov"
    if "more_in_common" in cleaned:
        return "more_in_common"
    if "opinium" in cleaned:
        return "opinium"
    if "survation" in cleaned:
        return "survation"
    if "techne" in cleaned:
        return "techne"
    if "bmg" in cleaned:
        return "bmg_research"
    if "focaldata" in cleaned:
        return "focaldata"
    if "freshwater" in cleaned:
        return "freshwater_strategy"
    if "j_l_partners" in cleaned or "jl_partners" in cleaned:
        return "jl_partners"
    if "ipsos" in cleaned:
        return "ipsos"
    if "deltapoll" in cleaned:
        return "deltapoll"
    if "lord_ashcroft" in cleaned:
        return "lord_ashcroft"
    return cleaned


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_ref_numeric_id(ref_href: str) -> str:
    # '#cite_note-123' -> '123'
    raw = ref_href.split("#")[-1]
    match = re.search(r"cite_note-(\d+)", raw)
    return match.group(1) if match else ""


def extract_reference_url_map(soup: BeautifulSoup) -> dict[str, str]:
    ref_map: dict[str, str] = {}
    for li in soup.select("li[id^=cite_note-]"):
        raw_li_id = li.get("id", "")
        li_id = raw_li_id if isinstance(raw_li_id, str) else ""
        match = re.search(r"cite_note-(\d+)", li_id)
        if not match:
            continue
        ref_id = match.group(1)

        external_link = (
            li.select_one("a.external.text")
            or li.select_one("span.reference-text a.external")
            or li.select_one("a.external")
        )
        if external_link and external_link.get("href"):
            raw_href = external_link.get("href", "")
            ref_map[ref_id] = raw_href if isinstance(raw_href, str) else ""
    return ref_map


def infer_format_family(url: str) -> str:
    lower = url.lower()

    path = urlparse(lower).path
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith(".xlsx"):
        return "xlsx"
    if path.endswith(".xlsb"):
        return "xlsb"
    if path.endswith(".xls"):
        return "xls"
    if "docs.google.com/spreadsheets" in lower:
        return "google_sheet"
    if "x.com/" in lower or "twitter.com/" in lower:
        return "social_post"
    if "download" in path or lower.endswith("/download"):
        return "download"
    return "html"


def extract_national_poll_rows(soup: BeautifulSoup, ref_map: dict[str, str]) -> list[dict[str, str]]:
    headline = soup.find(id="National_poll_results")
    if headline is None:
        raise ValueError("Could not find National poll results section")

    rows: list[dict[str, str]] = []

    node: Any = headline.parent
    current_year = ""
    while node is not None:
        node = node.find_next_sibling()
        if node is None:
            break
        if isinstance(node, Tag) and node.name == "h2":
            break

        if not isinstance(node, Tag):
            continue

        if node.name == "h3":
            year_text = clean_text(node.get_text(" ", strip=True))
            year_match = re.search(r"(20\d{2})", year_text)
            if year_match:
                current_year = year_match.group(1)
            continue

        if node.name != "table":
            continue

        classes = node.get("class", [])
        if "wikitable" not in classes:
            continue

        for tr in node.select("tbody > tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if len(cells) < 5:
                continue

            date_label = clean_text(cells[0].get_text(" ", strip=True))
            pollster_label = clean_text(cells[1].get_text(" ", strip=True))
            pollster_label = re.sub(r"\[[^\]]+\]", "", pollster_label).strip()
            area_label = clean_text(cells[3].get_text(" ", strip=True))

            if not date_label or not pollster_label:
                continue

            if area_label not in {"GB", "UK"}:
                continue

            # Skip event rows and election baseline rows
            if pollster_label.lower() in {"2024 general election", "2025 by-election"}:
                continue
            if "is elected" in pollster_label.lower() or "announces" in pollster_label.lower():
                continue

            ref_links = cells[1].select("a[href*='cite_note-']")
            citation_id = ""
            source_url = ""
            for ref_link in ref_links:
                raw_href = ref_link.get("href", "")
                href = raw_href if isinstance(raw_href, str) else ""
                ref_id = parse_ref_numeric_id(href)
                if ref_id and ref_id in ref_map:
                    citation_id = ref_id
                    source_url = ref_map[ref_id]
                    break

            if not source_url:
                row_ref_links = tr.select("a[href*='cite_note-']")
                for ref_link in row_ref_links:
                    raw_href = ref_link.get("href", "")
                    href = raw_href if isinstance(raw_href, str) else ""
                    ref_id = parse_ref_numeric_id(href)
                    if ref_id and ref_id in ref_map:
                        citation_id = ref_id
                        source_url = ref_map[ref_id]
                        break

            row_year = current_year
            if not row_year:
                prev_h3 = tr.find_previous("h3")
                if prev_h3 is not None:
                    year_match = re.search(r"(20\d{2})", clean_text(prev_h3.get_text(" ", strip=True)))
                    if year_match:
                        row_year = year_match.group(1)

            rows.append(
                {
                    "date_label": date_label,
                    "year_label": row_year,
                    "pollster_label": pollster_label,
                    "citation_id": citation_id,
                    "source_url": source_url,
                }
            )

    return rows


def assign_parser_identifiers(rows: list[dict[str, str]]) -> list[PollSourceRow]:
    out: list[PollSourceRow] = []
    for row in rows:
        pollster_key = normalize_pollster_name(row["pollster_label"])
        format_family = infer_format_family(row.get("source_url", "")) if row.get("source_url") else "unknown"
        parser_identifier = pollster_key
        notes = "" if row.get("source_url") else "missing citation/source url"

        out.append(
            PollSourceRow(
                date_label=row["date_label"],
                year_label=row.get("year_label", ""),
                pollster_label=row["pollster_label"],
                citation_id=row["citation_id"],
                source_url=row["source_url"],
                format_family=format_family,
                parser_identifier=parser_identifier,
                notes=notes,
            )
        )

    return out


def write_outputs(mapped_rows: list[PollSourceRow]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with CSV_OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "date_label",
                "year_label",
                "pollster_label",
                "citation_id",
                "source_url",
                "format_family",
                "parser_identifier",
                "notes",
            ],
        )
        writer.writeheader()
        for row in mapped_rows:
            writer.writerow(asdict(row))

    profile: dict[str, dict[str, Any]] = {}
    for row in mapped_rows:
        key = normalize_pollster_name(row.pollster_label)
        entry = profile.setdefault(
            key,
            {
                "pollster_examples": set(),
                "format_counts": Counter(),
                "parser_identifiers": set(),
            },
        )
        entry["pollster_examples"].add(row.pollster_label)
        entry["format_counts"][row.format_family] += 1
        entry["parser_identifiers"].add(row.parser_identifier)

    serializable_profile: dict[str, dict[str, Any]] = {}
    for key, value in profile.items():
        serializable_profile[key] = {
            "pollster_examples": sorted(value["pollster_examples"]),
            "format_counts": dict(value["format_counts"]),
            "parser_identifiers": sorted(value["parser_identifiers"]),
        }

    with JSON_OUT.open("w", encoding="utf-8") as handle:
        json.dump(serializable_profile, handle, indent=2, ensure_ascii=False)

    parser_registry: dict[str, dict[str, str | None]] = {}
    for parser_id in sorted({row.parser_identifier for row in mapped_rows}):
        if parser_id == "yougov":
            parser_registry[parser_id] = {
                "module": "polls.importers.yougov_import",
                "status": "implemented",
            }
        elif parser_id == "find_out_now":
            parser_registry[parser_id] = {
                "module": "polls.importers.find_out_now_import",
                "status": "implemented",
            }
        elif parser_id == "more_in_common":
            parser_registry[parser_id] = {
                "module": "polls.importers.more_in_common_import",
                "status": "implemented",
            }
        elif parser_id == "techne":
            parser_registry[parser_id] = {
                "module": "polls.importers.techne_import",
                "status": "implemented",
            }
        elif parser_id == "opinium":
            parser_registry[parser_id] = {
                "module": "polls.importers.opinium_import",
                "status": "implemented",
            }
        elif parser_id == "bmg_research":
            parser_registry[parser_id] = {
                "module": "polls.importers.bmg_research_import",
                "status": "implemented",
            }
        elif parser_id == "focaldata":
            parser_registry[parser_id] = {
                "module": "polls.importers.focaldata_import",
                "status": "implemented",
            }
        elif parser_id == "survation":
            parser_registry[parser_id] = {
                "module": "polls.importers.survation_import",
                "status": "implemented",
            }
        elif parser_id == "deltapoll":
            parser_registry[parser_id] = {
                "module": "polls.importers.deltapoll_import",
                "status": "implemented",
            }
        elif parser_id == "ipsos":
            parser_registry[parser_id] = {
                "module": "polls.importers.ipsos_import",
                "status": "implemented",
            }
        elif parser_id == "lord_ashcroft":
            parser_registry[parser_id] = {
                "module": "polls.importers.lord_ashcroft_import",
                "status": "implemented",
            }
        else:
            parser_registry[parser_id] = {
                "module": None,
                "status": "planned",
            }

    with REGISTRY_OUT.open("w", encoding="utf-8") as handle:
        json.dump(parser_registry, handle, indent=2, ensure_ascii=False)


def main() -> None:
    html = fetch_html(WIKI_URL)
    soup = BeautifulSoup(html, "lxml")

    ref_map = extract_reference_url_map(soup)
    raw_rows = extract_national_poll_rows(soup, ref_map)
    mapped_rows = assign_parser_identifiers(raw_rows)
    write_outputs(mapped_rows)

    with_urls = sum(1 for row in mapped_rows if row.source_url)
    variants = len({row.parser_identifier for row in mapped_rows})

    print(f"Wrote {len(mapped_rows)} mapped rows to {CSV_OUT}")
    print(f"Rows with source URL: {with_urls}")
    print(f"Unique parser identifiers: {variants}")
    print(f"Wrote pollster profile summary: {JSON_OUT}")
    print(f"Wrote parser registry: {REGISTRY_OUT}")


if __name__ == "__main__":
    main()
