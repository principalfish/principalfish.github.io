"""Sanity checks on parsed regional voting-intention shares.

The SNP stands only in Scotland and Plaid Cymru only in Wales, so a regional
table that gives either a material share anywhere else, or next to nothing in
its own nation, has its columns misaligned (a demographic column read as a
region, say). Importers reject such a parse, and repair scripts use the same
test to recognise broken stored rows.

Separately, :func:`regional_basis_problem` notices regional shares computed
on another basis from the national ones (Lord Ashcroft's regions once divided
counts by an all-adult base), whose totals drift far from the national total.
"""

from __future__ import annotations

from collections.abc import Mapping

# A share above this anywhere outside the party's own nation is misalignment,
# not a real crossbreak (both parties poll 0-1 elsewhere, from rounding).
MISALIGNMENT_THRESHOLD = 3.0

# Party -> the only region (macro or internal: both spell these the same)
# where it may poll above the threshold.
_HOME_REGION: Mapping[str, str] = {
    "Scottish National Party": "Scotland",
    "Plaid Cymru": "Wales",
}
# Party -> the least it plausibly polls in its home region (well below any
# poll since 2010: SNP lows ~15%, Plaid ~8% in Wales crossbreaks).
MIN_HOME_SHARE: Mapping[str, float] = {
    "Scottish National Party": 10.0,
    "Plaid Cymru": 5.0,
}


def regional_alignment_problem(
    shares: Mapping[str, Mapping[str, float]],
) -> str | None:
    """Describe a misaligned regional table, or return None if it looks right.

    Args:
        shares: Region name -> canonical party name -> share. Region keys may
            be an importer's macro regions or internal region names; only
            "Scotland" and "Wales" are treated specially.

    Returns:
        A description of the worst misplaced share, or None when the SNP and
        Plaid Cymru are at most ``MISALIGNMENT_THRESHOLD`` outside Scotland
        and Wales and at least ``MIN_HOME_SHARE`` inside them (when the table
        has those regions and parties).
    """
    worst: tuple[float, str, str] | None = None
    for region, parties in shares.items():
        for party, home in _HOME_REGION.items():
            share = parties.get(party, 0.0)
            if region != home and share > MISALIGNMENT_THRESHOLD:
                if worst is None or share > worst[0]:
                    worst = (share, party, region)
    if worst is not None:
        share, party, region = worst
        return f"{party} {share:g}% in {region}: regional columns look misaligned"
    for party, home in _HOME_REGION.items():
        home_shares = shares.get(home, {})
        if party in home_shares and home_shares[party] < MIN_HOME_SHARE[party]:
            return (
                f"{party} only {home_shares[party]:g}% in {home}:"
                " regional columns look misaligned"
            )
    return None


# The most the average regional total may stray from the national total before
# the regional shares count as being on another basis. Correct Lord Ashcroft
# polls sit within about 1.5 points (regional don't-know rates vary around the
# national one); the misparsed ones were 15-53 points below.
REGIONAL_BASIS_THRESHOLD = 5.0


def regional_basis_problem(
    regional: Mapping[str, Mapping[str, float]],
    national: Mapping[str, float],
) -> str | None:
    """Describe regional shares on a different basis from the national ones.

    For each region, the parties it shares with the national figures are
    summed on both sides; the mean of (regional total - national total) over
    the regions should be near zero whatever the basis (decided shares near
    100, don't-know-inclusive shares near the national figure).

    Args:
        regional: Region name -> party -> share.
        national: Party -> national share.

    Returns:
        A description when the mean gap exceeds ``REGIONAL_BASIS_THRESHOLD``,
        or None (also when either side is empty).
    """
    gaps = []
    for parties in regional.values():
        common = set(parties) & set(national)
        if common:
            gaps.append(
                sum(parties[p] for p in common) - sum(national[p] for p in common)
            )
    if not gaps:
        return None
    mean_gap = sum(gaps) / len(gaps)
    if abs(mean_gap) <= REGIONAL_BASIS_THRESHOLD:
        return None
    return (
        f"regional shares average {mean_gap:+.1f} points against the national"
        " total: regional basis looks wrong"
    )
