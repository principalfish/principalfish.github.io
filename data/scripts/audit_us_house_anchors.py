"""Reproduce candidate House anchor counts from locally supplied official bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import cast

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model_support.us_house_anchors import (
    AnchorYear,
    HouseAnchorError,
    inspect_house_anchor_catalogue,
)

_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA "
    "WA WV WI WY".split(),
)


def _major_party(value: str) -> str | None:
    party = value.replace("\xa0", "").strip()
    if party in {"DFL", "DNL", "D(UND)"}:
        return "D"
    if party == "GOP":
        return "R"
    tokens = set(re.split(r"[^A-Z]+", party))
    if {"D", "R"} <= tokens:
        raise HouseAnchorError("Ambiguous D/R candidate needs the pinned Welch rule")
    return next(iter(tokens & {"D", "R"}), None)


def audit_workbook(
    anchor: AnchorYear, path: Path, *, correction_pdf: Path | None = None,
) -> dict[str, object]:
    """Verify raw bytes, independently extract atomic rows and reconcile both views."""
    if anchor.year == 2024:
        raise HouseAnchorError("The qualified 2024 PDF evidence has no ready anchor")
    if anchor.year == 2020:
        corrections = cast(list[dict[str, object]], json.loads(anchor.corrections_json))
        if (correction_pdf is None
                or hashlib.sha256(correction_pdf.read_bytes()).hexdigest()
                != corrections[0]["source_sha256"]):
            raise HouseAnchorError("2020 requires the pinned NYC correction PDF bytes")
    if hashlib.sha256(path.read_bytes()).hexdigest() != anchor.source_sha256:
        raise HouseAnchorError("Official workbook bytes differ from the pinned source")
    extraction = cast(dict[str, object], json.loads(anchor.extraction_json))
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(workbook[str(extraction["sheet"])].iter_rows(values_only=True))
    finally:
        workbook.close()
    records: list[list[object]] = []
    groups: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    unmeasured: set[str] = set()
    measured: set[str] = set()
    for row_number, row in enumerate(rows, 1):
        if (len(row) < 16 or row[1] not in _STATES or row[9]
                or (not row[8] and row[10] != "W")):
            continue
        district = str(row[3]).strip().upper()
        if "UNEXPIRED" in district:
            continue
        match = re.fullmatch(r"(\d+)(?:\.0)?(?:\s*-.*)?", district)
        statewide_writein = (
            district == "H" and "Write-In Votes for All Districts" in str(row[8])
        )
        if match is None and not statewide_writein:
            continue
        unit = (f"{row[1]}-{int(match[1]) or 1:02d}" if match
                else f"{row[1]}-statewide-writeins")
        votes = row[15]
        if not isinstance(votes, (float, int)) or isinstance(votes, bool):
            if str(votes).lower() == "unopposed":
                unmeasured.add(unit)
            continue
        if votes < 0 or not float(votes).is_integer():
            raise HouseAnchorError("Source general votes must be integer counts")
        name = re.sub(r"\s+", " ", str(row[8] or "Unnamed write-in").replace(
            "#", "",
        ).replace("(Opportunity to Ballot)", "").strip()).casefold()
        party = str(row[10] or "").strip().replace("*", "")
        records.append([row_number, unit, name, party, int(votes)])
        groups[unit, name].append((party, int(votes)))
        if match:
            measured.add(unit)
    records_hash = hashlib.sha256(json.dumps(
        records, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    if (records_hash != extraction["selected_records_sha256"]
            or [row[0] for row in records] != extraction["record_numbers"]
            or len(measured) != anchor.measured_units
            or unmeasured != set(anchor.unmeasured_units)):
        raise HouseAnchorError("Source row extraction or measured population differs")
    totals = {"D": 0, "R": 0, "other": 0}
    by_unit: dict[str, dict[str, int]] = {}
    for (unit, name), ballots in groups.items():
        parties: set[str] = set()
        for party, _ in ballots:
            if anchor.year == 2016 and unit == "VT-01" and name == "welch, peter":
                if party != "D/R":
                    raise HouseAnchorError("Welch source party differs")
                parties.add("D")
            elif major := _major_party(party):
                parties.add(major)
        if len(parties) > 1:
            raise HouseAnchorError("Opposing affiliations share a candidate group")
        selected = next(iter(parties), "other")
        votes = sum(votes for _, votes in ballots)
        totals[selected] += votes
        by_unit.setdefault(unit, {"D": 0, "R": 0, "other": 0})[selected] += votes
    if anchor.year == 2020:
        # The signed NYC certification corrects the Republican ballot line.
        if rows[2378][15] != 52447:
            raise HouseAnchorError("NY-14 correction no longer matches the raw row")
        totals["R"] += 30
    general = {**totals, "denominator": sum(totals.values())}
    final = dict(general)
    replacements: dict[str, tuple[int, int]] = {}
    if anchor.year == 2018:
        match = re.search(
            r"For the general election, the final-round vote totals were:\s*"
            r"Jared Golden, ([0-9,]+)\s*\([^)]*\) and Bruce Poliquin, ([0-9,]+)",
            str(rows[1584][22]),
        )
        if match is None:
            raise HouseAnchorError("2018 Maine final-round source note differs")
        replacements["ME-02"] = (
            int(match[1].replace(",", "")), int(match[2].replace(",", "")),
        )
    elif anchor.year == 2022:
        for unit, column, d_row, r_row in (
            ("AK-01", 28, 58, 59), ("ME-02", 26, 1649, 1651),
        ):
            values = (rows[d_row][column], rows[r_row][column])
            if any(type(value) is not int or value <= 0 for value in values):
                raise HouseAnchorError("2022 final-RCV source counts differ")
            replacements[unit] = cast(tuple[int, int], values)
    for unit, (d_votes, r_votes) in replacements.items():
        for party, votes in (("D", d_votes), ("R", r_votes), ("other", 0)):
            final[party] += votes - by_unit[unit][party]
    final["denominator"] = final["D"] + final["R"] + final["other"]
    for name, result in (("published-general", general), ("final-rcv", final)):
        counts = anchor.view(name).counts
        if counts is None or result != {
            "D": counts.democratic, "R": counts.republican,
            "other": counts.other, "denominator": counts.denominator,
        }:
            raise HouseAnchorError(f"{anchor.year} {name} extracted totals differ")
    return {
        "year": anchor.year, "source_sha256": anchor.source_sha256,
        "selected_records_sha256": records_hash, "measured_units": len(measured),
        "unmeasured_units": sorted(unmeasured), "published-general": general,
        "final-rcv": final, "accepted": False, "activation_policy": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--correction-pdf", type=Path)
    args = parser.parse_args()
    catalogue = inspect_house_anchor_catalogue()
    anchor, = catalogue.for_baselines([f"{args.year}-us-senate"])
    print(json.dumps(audit_workbook(
        anchor, args.workbook, correction_pdf=args.correction_pdf,
    ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
