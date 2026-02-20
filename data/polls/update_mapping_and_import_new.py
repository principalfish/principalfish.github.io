#!/usr/bin/env python3
"""Refresh Wikipedia mappings and import mapped polls with implemented parsers.

Run from data root:
  ../election_data/bin/python polls/update_mapping_and_import_new.py
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from db import Database
from models import Poll, Pollster

MAPPINGS_DIR = ROOT_DIR / "polls" / "mappings"
MAPPING_CSV = MAPPINGS_DIR / "wikipedia_national_polls_mapping.csv"
PARSER_REGISTRY_JSON = MAPPINGS_DIR / "parser_registry.json"
UNIMPORTABLE_REPORT_CSV = MAPPINGS_DIR / "last_unimportable_urls.csv"

PARSER_URL_ARGS = {
    "yougov": "--pdf-url",
    "techne": "--pdf-url",
    "ipsos": "--pdf-url",
    "find_out_now": "--xlsx-url",
    "more_in_common": "--xlsx-url",
    "opinium": "--xlsx-url",
    "bmg_research": "--xlsx-url",
    "focaldata": "--xlsx-url",
    "survation": "--xlsx-url",
    "deltapoll": "--source-url",
    "lord_ashcroft": "--source-url",
}

PARSER_EXPECTED_EXT = {
    "yougov": ".pdf",
    "techne": ".pdf",
    "ipsos": ".pdf",
    "find_out_now": ".xlsx",
    "more_in_common": ".xlsx",
    "opinium": ".xlsx",
    "bmg_research": ".xlsx",
    "focaldata": ".xlsx",
    "survation": ".xlsx",
}


def run_step(command: list[str], label: str) -> None:
    print(f"\n== {label} ==")
    printable = " ".join(command)
    print(f"$ {printable}")
    subprocess.run(command, cwd=ROOT_DIR, check=True)


def load_registry() -> dict[str, dict[str, str | None]]:
    with PARSER_REGISTRY_JSON.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return {
        str(identifier): {
            "module": meta.get("module"),
            "status": meta.get("status"),
        }
        for identifier, meta in raw.items()
    }


def iter_mapping_rows() -> list[dict[str, str]]:
    with MAPPING_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows.reverse()
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue processing remaining rows when an importer command fails",
    )
    parser.add_argument(
        "--include-unimported-parsers",
        action="store_true",
        help="Include parser identifiers that currently have no imported polls in the database",
    )
    return parser.parse_args()


def load_existing_source_urls() -> set[str]:
    db = Database()
    with db.session() as session:
        values = session.execute(select(Poll.source_url).where(Poll.source_url.is_not(None))).scalars().all()
    return {value.strip() for value in values if isinstance(value, str) and value.strip()}


def load_parsers_with_existing_polls() -> set[str]:
    db = Database()
    with db.session() as session:
        identifiers = session.execute(
            select(Pollster.identifier).join(Poll, Poll.pollster_id == Pollster.id).distinct()
        ).scalars().all()
    return {identifier.strip() for identifier in identifiers if isinstance(identifier, str) and identifier.strip()}


def write_unimportable_report(rows: list[tuple[str, str, str]]) -> None:
    MAPPINGS_DIR.mkdir(parents=True, exist_ok=True)
    with UNIMPORTABLE_REPORT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["parser_identifier", "source_url", "reason"],
        )
        writer.writeheader()
        for parser_identifier, source_url, reason in rows:
            writer.writerow(
                {
                    "parser_identifier": parser_identifier,
                    "source_url": source_url,
                    "reason": reason,
                }
            )


def classify_unimportable_url(parser_identifier: str, source_url: str) -> str | None:
    lower = source_url.lower()

    if parser_identifier == "more_in_common":
        if "jan2026_mrp_tables" in lower:
            return "more_in_common-mrp-output"
        if "x.com/" in lower or "twitter.com/" in lower:
            return "more_in_common-social-post"

    if parser_identifier == "bmg_research":
        if "october-2024-omni-tables-for-the-i-v2.xlsx" in lower:
            return "bmg_research-non-xlsx-payload"
        if "inews.co.uk/" in lower:
            return "bmg_research-article-page"

    if parser_identifier == "focaldata" and "/blog/" in lower:
        return "focaldata-blog-page"

    if parser_identifier == "survation" and "linkedin.com/" in lower:
        return "survation-linkedin-page"

    if parser_identifier == "techne" and "airtable.com/" in lower:
        return "techne-airtable-wrapper"

    if "web.archive.org/web/" in lower and "/if_/" not in lower:
        return "wayback-wrapper"

    if "view.officeapps.live.com/" in lower:
        return "office-viewer-wrapper"

    expected_ext = PARSER_EXPECTED_EXT.get(parser_identifier)
    if expected_ext and expected_ext not in lower:
        return f"{parser_identifier}-missing-{expected_ext[1:]}-url"

    return None


def main() -> int:
    args = parse_args()
    python_bin = sys.executable

    run_step([python_bin, "polls/build_wikipedia_poll_mappings.py"], "Refresh mapping from Wikipedia")
    run_step([python_bin, "polls/sync_pollsters_from_mapping.py", "--apply"], "Sync pollsters")
    existing_source_urls = load_existing_source_urls()
    parsers_with_polls = load_parsers_with_existing_polls()
    print(f"Loaded {len(existing_source_urls)} existing poll source URLs from database")
    print(f"Loaded {len(parsers_with_polls)} parser identifiers with existing polls")

    registry = load_registry()
    rows = iter_mapping_rows()

    summary = Counter()
    skipped_planned = Counter()
    seen_urls: set[str] = set()
    failures: list[tuple[str, str, int]] = []
    unimportable_rows: list[tuple[str, str, str]] = []

    print("\n== Import mapped rows ==")

    for row in rows:
        parser_identifier = (row.get("parser_identifier") or "").strip()
        source_url = (row.get("source_url") or "").strip()
        year_label = (row.get("year_label") or "").strip()

        if not parser_identifier:
            summary["missing_parser_identifier"] += 1
            continue
        if not source_url:
            summary["missing_source_url"] += 1
            continue
        if source_url in seen_urls:
            summary["duplicate_source_url"] += 1
            continue
        seen_urls.add(source_url)

        if source_url in existing_source_urls:
            summary["already_in_database"] += 1
            continue

        if not args.include_unimported_parsers and parser_identifier not in parsers_with_polls:
            skipped_planned[parser_identifier] += 1
            summary["skipped_unimported_parser"] += 1
            continue

        unimportable_reason = classify_unimportable_url(parser_identifier, source_url)
        if unimportable_reason is not None:
            summary["skipped_unimportable_url"] += 1
            summary[f"skipped_unimportable_url_{unimportable_reason}"] += 1
            unimportable_rows.append((parser_identifier, source_url, unimportable_reason))
            continue

        parser_meta = registry.get(parser_identifier)
        if parser_meta is None:
            skipped_planned[parser_identifier] += 1
            summary["skipped_unknown_parser"] += 1
            continue

        if parser_meta.get("status") != "implemented":
            skipped_planned[parser_identifier] += 1
            summary["skipped_unimplemented_parser"] += 1
            continue

        module_name = parser_meta.get("module")
        if not module_name:
            skipped_planned[parser_identifier] += 1
            summary["skipped_missing_module"] += 1
            continue

        url_arg = PARSER_URL_ARGS.get(parser_identifier)
        if not url_arg:
            raise RuntimeError(
                f"No URL argument mapping for parser '{parser_identifier}'. "
                f"Update PARSER_URL_ARGS in this script."
            )

        command = [
            python_bin,
            module_name.replace(".", "/") + ".py",
            url_arg,
            source_url,
        ]
        if parser_identifier in {"more_in_common", "find_out_now"} and year_label.isdigit():
            command.extend(["--fieldwork-year-hint", year_label])

        print(f"- importing [{parser_identifier}] {source_url}")
        try:
            subprocess.run(command, cwd=ROOT_DIR, check=True)
        except subprocess.CalledProcessError as exc:
            summary["import_failed"] += 1
            failures.append((parser_identifier, source_url, int(exc.returncode)))
            if args.continue_on_error:
                print(
                    f"  ! importer failed (exit {exc.returncode}), continuing due to --continue-on-error"
                )
                continue
            raise
        summary["import_attempted"] += 1

    print("\n== Summary ==")
    print(f"Import attempts: {summary['import_attempted']}")
    print(f"Skipped duplicate source URL: {summary['duplicate_source_url']}")
    print(f"Skipped already in database: {summary['already_in_database']}")
    print(f"Skipped missing parser identifier: {summary['missing_parser_identifier']}")
    print(f"Skipped missing source URL: {summary['missing_source_url']}")
    print(f"Skipped unimported parser: {summary['skipped_unimported_parser']}")
    print(f"Skipped unimportable URL: {summary['skipped_unimportable_url']}")
    print(f"Skipped unimplemented parser: {summary['skipped_unimplemented_parser']}")
    print(f"Skipped unknown parser: {summary['skipped_unknown_parser']}")
    print(f"Skipped missing module: {summary['skipped_missing_module']}")
    print(f"Failed imports: {summary['import_failed']}")

    if skipped_planned:
        print("\nUnimplemented/unknown parser rows skipped:")
        for parser_identifier, count in sorted(skipped_planned.items()):
            print(f"- {parser_identifier}: {count}")

    url_reason_counts = {
        key.replace("skipped_unimportable_url_", ""): value
        for key, value in summary.items()
        if key.startswith("skipped_unimportable_url_") and value
    }
    if url_reason_counts:
        print("\nUnimportable URL rows skipped:")
        for reason in sorted(url_reason_counts):
            print(f"- {reason}: {url_reason_counts[reason]}")

    write_unimportable_report(unimportable_rows)
    print(f"\nWrote unimportable URL report: {UNIMPORTABLE_REPORT_CSV} ({len(unimportable_rows)} rows)")

    if failures:
        print("\nFailed import rows:")
        for parser_identifier, source_url, code in failures:
            print(f"- [{parser_identifier}] (exit {code}) {source_url}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
