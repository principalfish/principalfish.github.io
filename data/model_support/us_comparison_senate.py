"""Stage-aware Senate normalization for offline comparison, not chamber replay."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from model_support.us_comparison_inputs import (
    PARTY_KEYS,
    ComparisonData,
    ComparisonError,
    CycleFact,
    JsonObject,
    NationalFact,
    UnitFact,
    canonical_state_names,
    count,
    fingerprint,
    load_payload,
    number,
    obj,
    proxy_diagnostics,
    seq,
    source_hash,
    text_value,
)

_ALIASES = {
    "democrat": {"D", "DEMOCRAT", "DEMOCRATIC", "DFL", "DNL",
                 "DEMOCRATIC-NONPARTISAN LEAGUE"},
    "republican": {"R", "REPUBLICAN"},
    "independent": {"IND", "INDEPENDENT"},
    "libertarian": {"LIB", "LIBERTARIAN", "LIBERTARIAN PARTY OF FLORIDA", "LBF"},
    "usgreen": {"GRE", "GREEN", "PG", "GRM", "MGR"},
}


def candidate_party(labels: list[str]) -> tuple[int, str]:
    """Apply major-party fusion precedence; preserve unmapped ballot labels."""
    normalized = {label.strip().rstrip("*").strip().upper() for label in labels}
    expanded = normalized | {part for label in normalized for part in label.split("/")}
    for key in ("republican", "democrat", "libertarian", "usgreen", "independent"):
        if expanded & _ALIASES[key]:
            return PARTY_KEYS.index(key), f"Explicit ballot alias/fusion priority: {key}"
    if all(label.startswith("W") or label in {"OTH", "OTHER"} for label in normalized):
        return 5, "Write-in/Other ballot category retained in canonical Others"
    return 5, "Unmapped/ambiguous ballot labels retained in Others by converter convention"


def normalize_candidates(
    candidates: list[JsonObject], *, baseline: bool = False, runoff: bool = False,
) -> tuple[tuple[int, ...], int, list[JsonObject]]:
    totals = [0] * 6
    nota = 0
    ledger: list[JsonObject] = []
    grouped: dict[str, list[JsonObject]] = defaultdict(list)
    for row in candidates:
        grouped[text_value(row["candidate" if baseline else "name"])].append(row)
    for name, rows in grouped.items():
        labels: list[str] = []
        votes = 0
        combined: set[int] = set()
        for row in rows:
            if baseline:
                value = row["general_runoff_votes" if runoff else "general_votes"]
                if value is None:
                    continue
                labels.append(str(row.get("party_label") or "UNKNOWN"))
                votes += count(value)
                if not runoff and row.get("combined_candidate_votes") is not None:
                    combined.add(count(row["combined_candidate_votes"]))
            else:
                labels.extend(str(label or "UNKNOWN") for label in seq(row["party_labels"]))
                votes += count(row["votes"])
        if len(combined) > 1 or (combined and votes != next(iter(combined))):
            raise ComparisonError("fusion ballot-line sum differs from combined candidate count")
        if not labels:
            continue
        if "none of these" in name.lower():
            nota += votes
            ledger.append({"name": name, "votes": votes, "labels": labels,
                           "engine_party": None, "rule": "NOTA kept separate, never Others"})
            continue
        party, rule = candidate_party(labels)
        totals[party] += votes
        ledger.append({"name": name, "votes": votes, "labels": labels,
                       "engine_party": PARTY_KEYS[party], "rule": rule,
                       "source_rows": rows})
    if sum(totals) <= 0:
        raise ComparisonError("candidate stage has no positive votes")
    return tuple(totals), nota, ledger


def _generic_facts(payload: JsonObject) -> tuple[NationalFact, ...]:
    facts: list[NationalFact] = []
    for item in seq(payload["generic_observations"]):
        row = obj(item)
        poll = obj(row["poll"])
        outcome = obj(row["outcome"])
        source_hash(outcome["source_sha256"])
        counts = obj(outcome["final_RCV_no_nominal_view"])
        d, r, other, total = (count(counts[key]) for key in ("D", "R", "other", "denominator"))
        if d + r + other != total:
            raise ComparisonError("generic outcome component sum differs")
        facts.append(NationalFact(
            count(row["cycle"]),
            (number(poll["raw_D_pct"]), number(poll["raw_R_pct"])),
            (d, r, total), row.get("clean_fit_evidence_ready") is True, row,
        ))
    if {fact.cycle for fact in facts} != set(range(2012, 2025, 2)):
        raise ComparisonError("generic series cycles differ")
    return tuple(sorted(facts, key=lambda fact: fact.cycle))


def candidate_proxy(record: JsonObject, official: JsonObject, total: int) -> JsonObject:
    """Canonicalize D/R direction and keep other named contrasts separate."""
    joins = [obj(item) for item in seq(record["candidate_joins"])]
    if len(joins) != 2:
        raise ComparisonError("selected-candidate proxy requires exactly two joined candidates")
    parties: dict[str, int] = {}
    for join in joins:
        header = text_value(join["poll_header"])
        candidates = [obj(item) for item in seq(join["official_candidates"])]
        labels = [text_value(label) for candidate in candidates
                  for label in seq(candidate["party_labels"])]
        if (not candidates or set(labels) != set(seq(join["official_party_labels"]))
                or any(candidate not in seq(official["candidates"])
                       for candidate in candidates)
                or sum(count(candidate["votes"]) for candidate in candidates)
                != count(join["official_candidate_votes"])):
            raise ComparisonError("proxy candidate identity/counts differ from official source")
        party = candidate_party(labels)[0]
        marker = re.search(r"\((D|R|I|IND)\)", header)
        if marker and party != {"D": 0, "R": 1, "I": 2, "IND": 2}[marker[1]]:
            raise ComparisonError("proxy poll/official candidate party identity differs")
        if header in parties:
            raise ComparisonError("proxy candidate headers are duplicated")
        parties[header] = party
    if set(parties.values()) == {0, 1}:
        joins.sort(key=lambda row: parties[text_value(row["poll_header"])])
        axis = group = "D-R"
    elif set(parties.values()) in ({1, 2}, {0, 2}):
        joins.sort(key=lambda row: parties[text_value(row["poll_header"])] != 2)
        axis = group = "I-R" if 1 in parties.values() else "I-D"
    else:
        axis = "named-candidate: publisher first minus second"
        group = f"named:{record['year']}-{record['state_code']}-{record['event']}"
    proxy = proxy_diagnostics(
        record,
        (number(joins[0]["poll_raw_share"]), number(joins[1]["poll_raw_share"])),
        (count(joins[0]["official_candidate_votes"]),
         count(joins[1]["official_candidate_votes"])),
        total, contrast_axis=axis, contrast_group=group,
    )
    proxy["contrast"] = [row["poll_header"] for row in joins]
    proxy["contrast_scope"] = (
        "Selected D/R candidates; no local-floor or sampling decomposition"
        if axis == "D-R" else
        "Separate named-candidate contrast; not D/R partisan bias, a caucus mapping or a local-floor estimate"
    )
    return proxy


def load_senate_comparison(path: Path | None = None) -> ComparisonData:
    payload, input_hash = load_payload("senate", path)
    inventory = tuple(obj(row) for row in seq(payload["inventory"]))
    baselines = tuple(obj(row) for row in seq(payload["baseline_sources"]))
    if len(inventory) != 72 or len(baselines) != 71:
        raise ComparisonError("Senate requires complete 72-event/71-baseline inventory")
    identities = [(row["year"], row["state_code"], row["event"]) for row in inventory]
    if len(set(identities)) != len(identities):
        raise ComparisonError("duplicate Senate event")
    national = _generic_facts(payload)
    names = canonical_state_names()
    proxies: list[JsonObject] = []
    ledger: list[JsonObject] = []
    cycle_units: dict[tuple[int, str], list[UnitFact]] = defaultdict(list)
    for record in inventory:
        year = count(record["year"])
        state = text_value(record["state_code"])
        event = text_value(record["event"])
        official = obj(record["official_result"])
        source_hash(official["sha256"])
        observed, nota, candidate_ledger = normalize_candidates(
            [obj(item) for item in seq(official["candidates"])],
        )
        total = sum(observed) + nota
        if record.get("candidate_joins"):
            for policy, denominator in (("reported-including-nota", total),
                                        ("candidate-votes-excluding-nota", sum(observed))):
                proxy = candidate_proxy(record, official, denominator)
                proxy["denominator_policy"] = policy
                proxies.append(proxy)
        excluded = event == "runoff" or (state == "CA" and event == "special")
        entry: JsonObject = {
            "cycle": year, "event_id": f"{year}-{state}-{event}",
            "slot_id": f"{year}-{state}-{'regular' if state == 'CA' else event}",
            "election_fold": year, "source_record": record,
            "normalization": candidate_ledger, "nota": nota,
            "reported_total": total,
            "baseline_panel_status": "linked alternate stage/ballot" if excluded else "included field",
        }
        ledger.append(entry)
        if excluded:
            continue
        baseline_year = (2020 if event == "special" else year - 6)
        matches = [row for row in baselines if row["year"] == baseline_year
                   and row["state_code"] == state and row["event"] == "regular"]
        if len(matches) != 1:
            raise ComparisonError("Senate baseline stage/event does not resolve uniquely")
        baseline = matches[0]
        source_hash(baseline["sha256"])
        baseline_counts, baseline_nota, baseline_ledger = normalize_candidates(
            [obj(item) for item in seq(baseline["candidate_ballot_lines"])],
            baseline=True, runoff=baseline_year == 2016 and state == "LA",
        )
        issues: list[str] = []
        if not all(baseline_counts[j] > 0 and observed[j] > 0 for j in (0, 1)):
            issues.append("D/R contrast lacks baseline/observed support; structural eligibility")
        winner = max(range(6), key=lambda j: observed[j])
        if baseline_counts[winner] == 0:
            issues.append("Observed winning party unreachable without historical own polls")
        provenance: JsonObject = {
            "baseline_source": baseline, "baseline_normalization": baseline_ledger,
            "baseline_nota": baseline_nota,
            "baseline_policy": "Official candidate counts, NOTA separate; not deployed history replay",
            "baseline_stage": "runoff" if baseline_year == 2016 and state == "LA" else "general",
            "observed_source": official, "observed_normalization": candidate_ledger,
            "semantic_limit": "Party first preference only" if state == "AK" and year == 2022
            else "Party aggregation; candidate/RCV/control semantics not reconstructed",
        }
        for policy, denominator in (("reported-including-nota", total),
                                    ("candidate-votes-excluding-nota", sum(observed))):
            cycle_units[(year, policy)].append(UnitFact(
                f"{year}-{state}-{event}", names[state], baseline_counts, observed, denominator,
                tuple(issues), provenance,
            ))
    cycles: list[CycleFact] = []
    for (year, policy), units in sorted(cycle_units.items()):
        if len(units) != (35 if year == 2022 else 34):
            raise ComparisonError("Senate calibration field slot count differs")
        cycles.append(CycleFact(year, tuple(sorted(units, key=lambda unit: unit.key)),
                                next(fact for fact in national if fact.cycle == year), policy))
    if len(proxies) != 48:
        raise ComparisonError("Senate requires 24 retrieved averages and two denominator alternatives")
    return ComparisonData(
        "senate", input_hash, fingerprint(payload), payload, national,
        tuple(cycles), tuple(proxies), tuple(ledger),
    )
