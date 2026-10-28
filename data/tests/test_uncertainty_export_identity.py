"""Public result/summary pairs remain identifiable across cached same-day exports."""

from __future__ import annotations

import copy

from scripts.export.uncertainty import link_uncertainty_payload


def test_identity_is_shared_stable_and_contains_only_public_inputs() -> None:
    result: dict[str, object] = {"schema": "pf-results-v4", "seats": [{"p": [[1, 42]]}]}
    document: dict[str, object] = {"election_name": "UNS 2026-10-12", "interval": 0.9}
    linked = link_uncertainty_payload(result, document)
    assert linked is not None
    assert linked["uncertaintyId"] == result["uncertaintyId"]
    assert "uncertaintyId" not in document
    assert link_uncertainty_payload(result, linked) == linked
    reordered = dict(reversed(list(document.items())))
    assert link_uncertainty_payload(result, reordered) == linked


def test_same_day_central_result_or_summary_change_changes_identity() -> None:
    result: dict[str, object] = {"seats": [{"p": [[1, 42]], "u": [[1, 30, 50, 0.6]]}]}
    document: dict[str, object] = {"election_name": "UNS 2026-10-12", "interval": 0.9}
    original = link_uncertainty_payload(result, document)
    assert original is not None
    changed_result: dict[str, object] = {"seats": [{"p": [[1, 43]], "u": [[1, 30, 50, 0.6]]}]}
    changed = link_uncertainty_payload(changed_result, document)
    assert changed is not None
    assert changed["uncertaintyId"] != original["uncertaintyId"]
    changed = link_uncertainty_payload(copy.deepcopy(result), {**document, "interval": 0.8})
    assert changed is not None
    assert changed["uncertaintyId"] != original["uncertaintyId"]


def test_no_uncertainty_preserves_ordinary_payload() -> None:
    result: dict[str, object] = {"schema": "pf-results-v4", "seats": []}
    original = copy.deepcopy(result)
    assert link_uncertainty_payload(result, None) is None
    assert result == original
