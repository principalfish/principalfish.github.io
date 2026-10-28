"""President source normalization without opening a database or fitting odds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from model_support.us_comparison_inputs import (
    PARTY_KEYS,
    ComparisonData,
    ComparisonError,
    CycleFact,
    JsonObject,
    UnitFact,
    count,
    fingerprint,
    load_payload,
    major_result,
    national_record,
    number,
    obj,
    proxy_diagnostics,
    seq,
    source_hash,
    text_value,
)
from polls.importers.us.us_geography import STATE_POSTAL


def load_president_comparison(path: Path | None = None) -> ComparisonData:
    payload, input_hash = load_payload("president", path)
    inventory = tuple(obj(row) for row in seq(payload["inventory"]))
    if len(inventory) != 156:
        raise ComparisonError("President inventory requires 156 geography/cycle rows")
    keys = {(count(row["year"]), text_value(row["state"])) for row in inventory}
    expected = {(year, name.lower().replace(" ", "-"))
                for year in (2016, 2020, 2024) for name in STATE_POSTAL}
    expected |= {(year, "national") for year in (2016, 2020, 2024)}
    if keys != expected or len(keys) != len(inventory):
        raise ComparisonError("President inventory identities differ")
    national = tuple(national_record(row) for row in inventory
                     if row["state"] == "national")
    national = tuple(sorted(national, key=lambda fact: fact.cycle))
    baselines: dict[int, JsonObject] = {}
    for item in seq(payload["installed_baselines"]):
        record = obj(item)
        content = text_value(record["content"])
        if hashlib.sha256(content.encode()).hexdigest() != source_hash(record["sha256"]):
            raise ComparisonError("installed President baseline content hash differs")
        year = count(record["cycle"])
        if year in baselines:
            raise ComparisonError("duplicate installed baseline")
        baselines[year] = obj(json.loads(content))
    if set(baselines) != {2012, 2016, 2020}:
        raise ComparisonError("President requires three pinned prior-cycle baselines")
    cycles: list[CycleFact] = []
    proxies: list[JsonObject] = []
    ledger: list[JsonObject] = []
    for fact in national:
        official_by_name = {
            text_value(row["state"]): row for row in inventory
            if row["year"] == fact.cycle and row["state"] != "national"
        }
        totals = [0, 0, 0]
        for record in official_by_name.values():
            official = obj(record["official_result"])
            if (source_hash(official["sha256"])
                    != obj(fact.provenance["official_result"])["sha256"]):
                raise ComparisonError("President official outcome versions differ")
            for j, value in enumerate(major_result(official)):
                totals[j] += value
            if record.get("status") == "raw_final_average_verified":
                raw = obj(record["raw_shares"])
                poll = (number(obj(raw["D"])["percentage"]),
                        number(obj(raw["R"])["percentage"]))
                d, r, total = major_result(official)
                proxies.append(proxy_diagnostics(record, poll, (d, r), total))
            ledger.append({"cycle": fact.cycle, "geography": record["state"],
                           "poll_status": record["status"], "source_record": record})
        if tuple(totals) != fact.result:
            raise ComparisonError("51 official state outcomes do not sum to national")
        baseline = baselines[fact.cycle - 4]
        if len(baseline) != 56 or sum(
            count(obj(obj(row)["seatInfo"])["electoral_votes"])
            for row in baseline.values()
        ) != 538:
            raise ComparisonError("installed baseline requires 56 units and 538 EV")
        expected_units = set(STATE_POSTAL) | {
            "Maine CD-1", "Maine CD-2", "Nebraska CD-1", "Nebraska CD-2",
            "Nebraska CD-3",
        }
        if set(baseline) != expected_units:
            raise ComparisonError("installed baseline geographic identities differ")
        units: list[UnitFact] = []
        for name, baseline_value in sorted(baseline.items()):
            record = obj(baseline_value)
            parties = obj(record["partyInfo"])
            if not set(parties) <= set(PARTY_KEYS):
                raise ComparisonError("installed baseline has unknown engine party")
            counts = tuple(count(obj(parties[key])["total"]) if key in parties else 0
                           for key in PARTY_KEYS)
            state = name.split(" CD-", 1)[0]
            observed = official_by_name[state.lower().replace(" ", "-")]
            d, r, total = major_result(obj(observed["official_result"]))
            is_district = name != state
            units.append(UnitFact(
                name, state, counts, (d, r, 0, 0, 0, 0), total,
                ("District outcome/geography not curated; excluded from targets",)
                if is_district else (),
                {"baseline_cycle": fact.cycle - 4,
                 "baseline_policy": "Pinned installed version, not official-certified",
                 "installed_baseline": record, "official_state_target": observed,
                 "target_party_scope": "D/R only; remainder not mapped to engine party"},
                complete_target_parties=False,
            ))
        cycles.append(CycleFact(fact.cycle, tuple(units), fact, "official-all-vote"))
    if len(proxies) != 53:
        raise ComparisonError("President comparison requires 53 verified state averages")
    return ComparisonData(
        "president", input_hash, fingerprint(payload), payload, national,
        tuple(cycles), tuple(proxies), tuple(ledger),
    )
