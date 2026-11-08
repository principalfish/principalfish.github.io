"""Tests for deriving the Current Senate from the 2026 Senate results."""

from __future__ import annotations

import copy
import json
import os
import stat
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from scripts.us_results import cli
from scripts.us_results.current_senate import (
    DEFAULT_SNAPSHOT,
    CurrentSenateError,
    build_current_senate,
    rebuild_current_senate,
    render,
)
from tests.senate_current_fixtures import senate_election, senate_race


@pytest.fixture()
def snapshot() -> dict[str, Any]:
    """The committed chamber: 100 senators; class 2 and FL/OH class 3 were up."""
    data: dict[str, Any] = json.loads(DEFAULT_SNAPSHOT.read_text(encoding="utf-8"))
    return data


def _member(payload: dict[str, Any], state: str, seat_class: int) -> dict[str, Any]:
    [seat] = [seat for seat in payload["seats"] if seat["n"] == state]
    [member] = [member for member in seat["members"] if member["class"] == seat_class]
    assert isinstance(member, dict)
    return member


def _seat(payload: dict[str, Any], state: str) -> dict[str, Any]:
    [seat] = [seat for seat in payload["seats"] if seat["n"] == state]
    assert isinstance(seat, dict)
    return seat


class TestBuild:
    def test_fixture_splits_65_holdovers_and_35_winners(self, snapshot: dict[str, Any]) -> None:
        built = build_current_senate(snapshot, senate_election(snapshot))

        members = [m for seat in built.payload["seats"] for m in seat["members"]]
        assert (built.holdovers, built.winners, built.uncalled) == (65, 35, 0)
        assert len(members) == 100
        assert all(len(seat["members"]) == 2 for seat in built.payload["seats"])
        assert Counter(m["class"] for m in members) == {1: 33, 2: 33, 3: 34}
        assert built.warnings == []

    def test_class_two_and_the_specials_take_the_election_winners(
        self, snapshot: dict[str, Any]
    ) -> None:
        built = build_current_senate(snapshot, senate_election(snapshot))

        assert _member(built.payload, "Georgia", 2) == {
            "party": "democrat", "name": "Winner Georgia", "class": 2,
        }
        assert _member(built.payload, "Florida", 3) == {
            "party": "republican", "name": "Special Florida", "class": 3,
        }
        assert _member(built.payload, "Ohio", 3)["name"] == "Special Ohio"
        # Their other seats were not up and carry over unchanged.
        assert _member(built.payload, "Florida", 1) == _member(snapshot, "Florida", 1)
        assert _member(built.payload, "Ohio", 1) == _member(snapshot, "Ohio", 1)
        # Class 3 seats elsewhere stay with their sitting senators.
        assert _member(built.payload, "Arizona", 3) == _member(snapshot, "Arizona", 3)

    def test_state_colour_is_the_shared_party_or_split(self, snapshot: dict[str, Any]) -> None:
        built = build_current_senate(snapshot, senate_election(snapshot))

        # Maine: Collins' class 2 seat won by a Democrat, King (independent) stays.
        assert _seat(built.payload, "Maine")["w"] == "split"
        # Delaware: Blunt Rochester (D) holds on, so the pair is Democratic.
        assert _seat(built.payload, "Delaware")["w"] == "democrat"
        # Ohio: Moreno (R) and the special's Republican winner.
        assert _seat(built.payload, "Ohio")["w"] == "republican"
        # Alabama: Britt (R) stays, the class 2 seat goes to a Democrat.
        assert _seat(built.payload, "Alabama")["w"] == "split"

    def test_output_has_the_converter_shape_and_order(self, snapshot: dict[str, Any]) -> None:
        built = build_current_senate(snapshot, senate_election(snapshot))

        payload = built.payload
        assert list(payload) == ["schema", "seats"]
        assert payload["schema"] == "pf-senate-current-v1"
        assert [seat["n"] for seat in payload["seats"]] == [seat["n"] for seat in snapshot["seats"]]
        for seat, old in zip(payload["seats"], snapshot["seats"], strict=True):
            assert list(seat) == ["n", "r", "w", "members"]
            assert seat["r"] == old["r"]
            assert [m["class"] for m in seat["members"]] == sorted(m["class"] for m in seat["members"])
            assert all(list(m) == ["party", "name", "class"] for m in seat["members"])

    def test_the_inputs_are_not_modified(self, snapshot: dict[str, Any]) -> None:
        election = senate_election(snapshot)
        before = copy.deepcopy((snapshot, election))

        build_current_senate(snapshot, election)

        assert (snapshot, election) == before

    def test_a_winner_without_a_name_warns_and_gets_a_placeholder(
        self, snapshot: dict[str, Any]
    ) -> None:
        election = senate_election(
            snapshot, Texas=senate_race("republican", None, seat_class=2),
        )

        built = build_current_senate(snapshot, election)

        assert _member(built.payload, "Texas", 2)["name"] == "Republican winner"
        assert built.warnings == [
            "Texas class 2: winner has no name; using 'Republican winner'"
        ]

    def test_a_special_that_clashes_with_a_regular_race_comes_from_the_specials_file(
        self, snapshot: dict[str, Any]
    ) -> None:
        # Georgia: its class 3 seat is also filled early, in the specials file.
        election = senate_election(snapshot)
        special = {
            "meta": {"chamber": "senate", "year": 2026, "special": True},
            "Georgia": senate_race("republican", "Special Georgia", seat_class=3, special=True),
        }

        built = build_current_senate(snapshot, election, special_election=special)

        assert (built.holdovers, built.winners) == (64, 36)
        assert _member(built.payload, "Georgia", 3)["name"] == "Special Georgia"
        assert _member(built.payload, "Georgia", 2)["name"] == "Winner Georgia"


class TestUncalled:
    def _build(self, snapshot: dict[str, Any], **overrides: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = build_current_senate(
            snapshot, senate_election(snapshot, **overrides),
        ).payload
        return payload

    def test_an_uncalled_race_is_an_unknown_member_not_its_leader(
        self, snapshot: dict[str, Any]
    ) -> None:
        # The Republican leads on votes, but no winner is called.
        uncalled = senate_race(None, None, seat_class=2, called=False, pct=62.54)
        built = build_current_senate(snapshot, senate_election(snapshot, Texas=uncalled))

        assert (built.holdovers, built.winners, built.uncalled) == (65, 34, 1)
        assert _member(built.payload, "Texas", 2) == {
            "party": None, "name": None, "class": 2, "c": False, "pc": 62.5,
        }

    def test_pc_is_left_out_when_the_count_is_unknown(self, snapshot: dict[str, Any]) -> None:
        payload = self._build(snapshot, Texas=senate_race(None, None, seat_class=2, called=False, pct=None))

        assert _member(payload, "Texas", 2) == {"party": None, "name": None, "class": 2, "c": False}

    def test_one_called_and_one_uncalled_member_colours_the_state_by_the_called_one(
        self, snapshot: dict[str, Any]
    ) -> None:
        payload = self._build(
            snapshot, Texas=senate_race(None, None, seat_class=2, called=False, pct=40)
        )

        # Cruz (R, class 1) is called, so Texas reads Republican until class 2 is called.
        texas = _seat(payload, "Texas")
        assert list(texas) == ["n", "r", "w", "members"]
        assert texas["w"] == "republican"
        assert "c" not in texas
        # Only the member carries the uncalled flag.
        assert [m.get("c") for m in texas["members"]] == [None, False]
        assert texas["members"][1] == {
            "party": None, "name": None, "class": 2, "c": False, "pc": 40.0,
        }

    def test_a_mixed_state_follows_the_called_member_whatever_its_party(
        self, snapshot: dict[str, Any]
    ) -> None:
        payload = self._build(
            snapshot, Maine=senate_race(None, None, seat_class=2, called=False)
        )

        # King (independent, class 1) is the called member: no "split" with an unknown.
        assert _seat(payload, "Maine")["w"] == "independent"
        assert "c" not in _seat(payload, "Maine")

    def test_two_uncalled_members_leave_the_state_uncalled_with_no_colour(
        self, snapshot: dict[str, Any]
    ) -> None:
        election = senate_election(
            snapshot, Georgia=senate_race(None, None, seat_class=2, called=False)
        )
        special = {
            "Georgia": senate_race(
                None, None, seat_class=3, special=True, called=False
            ),
        }

        built = build_current_senate(snapshot, election, special_election=special)

        georgia = _seat(built.payload, "Georgia")
        assert list(georgia) == ["n", "r", "c", "members"]
        assert georgia["c"] is False
        assert [m["c"] for m in georgia["members"]] == [False, False]
        assert (built.holdovers, built.winners, built.uncalled) == (64, 34, 2)
        # States with every member called keep w and have no c.
        assert "c" not in _seat(built.payload, "Ohio")
        assert "w" in _seat(built.payload, "Ohio")

    def test_an_uncalled_special_is_unknown_too(self, snapshot: dict[str, Any]) -> None:
        payload = self._build(
            snapshot, Ohio=senate_race(None, None, seat_class=3, special=True, called=False, pct=10),
        )

        assert _member(payload, "Ohio", 3)["c"] is False
        assert _member(payload, "Ohio", 1)["name"] == _member(snapshot, "Ohio", 1)["name"]

    def test_a_called_race_with_no_winning_party_is_refused(
        self, snapshot: dict[str, Any]
    ) -> None:
        broken = senate_race("democrat", "X", seat_class=2)
        broken["seatInfo"]["current"] = None

        with pytest.raises(CurrentSenateError, match="Texas class 2 is called but names no"):
            build_current_senate(snapshot, senate_election(snapshot, Texas=broken))


class TestValidation:
    def test_a_missing_class_two_state_is_refused(self, snapshot: dict[str, Any]) -> None:
        election = senate_election(snapshot)
        del election["Georgia"]

        with pytest.raises(CurrentSenateError, match="no class 2 race for: Georgia"):
            build_current_senate(snapshot, election)

    def test_a_missing_special_is_refused(self, snapshot: dict[str, Any]) -> None:
        election = senate_election(snapshot)
        # Ohio has no class 2 seat, so removing its entry drops only the special.
        del election["Ohio"]

        with pytest.raises(CurrentSenateError, match="no special race for: Ohio"):
            build_current_senate(snapshot, election)

    def test_a_race_for_a_seat_no_one_holds_is_refused(self, snapshot: dict[str, Any]) -> None:
        # Arizona holds classes 1 and 3: it has no class 2 senator to replace.
        election = senate_election(snapshot, Arizona=senate_race("democrat", "A", seat_class=2))

        with pytest.raises(CurrentSenateError, match="Arizona has no sitting class 2"):
            build_current_senate(snapshot, election)

    def test_a_state_with_the_wrong_member_count_is_refused(self, snapshot: dict[str, Any]) -> None:
        broken = copy.deepcopy(snapshot)
        _seat(broken, "Texas")["members"].append(
            {"party": "republican", "name": "Extra", "class": 1},
        )

        with pytest.raises(CurrentSenateError, match="Texas has 3 members, not 2"):
            build_current_senate(broken, senate_election(snapshot))

    def test_a_state_left_with_one_member_is_refused(self, snapshot: dict[str, Any]) -> None:
        broken = copy.deepcopy(snapshot)
        _seat(broken, "Texas")["members"].pop(0)  # Cruz, class 1
        broken_election = senate_election(snapshot)

        with pytest.raises(CurrentSenateError, match="Texas has 1 members, not 2"):
            build_current_senate(broken, broken_election)

    def test_two_members_of_one_class_are_refused(self, snapshot: dict[str, Any]) -> None:
        broken = copy.deepcopy(snapshot)
        for member in _seat(broken, "Texas")["members"]:
            member["class"] = 3

        with pytest.raises(CurrentSenateError, match="Texas has two members of class 3"):
            build_current_senate(broken, senate_election(broken))

    def test_a_chamber_that_is_not_100_is_refused(self, snapshot: dict[str, Any]) -> None:
        short = copy.deepcopy(snapshot)
        short["seats"].pop()  # Wyoming
        election = senate_election(snapshot)
        del election["Wyoming"]

        with pytest.raises(CurrentSenateError, match="98 members, not 100"):
            build_current_senate(short, election)

    def test_a_non_state_seat_is_refused(self, snapshot: dict[str, Any]) -> None:
        election = senate_election(snapshot, **{"TX-01": senate_race("democrat", "A", seat_class=2)})

        with pytest.raises(CurrentSenateError, match="'TX-01' is not a state"):
            build_current_senate(snapshot, election)

    def test_a_house_election_is_refused(self, snapshot: dict[str, Any]) -> None:
        election = senate_election(snapshot)
        election["meta"]["chamber"] = "house"

        with pytest.raises(CurrentSenateError, match="house election, not senate"):
            build_current_senate(snapshot, election)

    def test_the_wrong_snapshot_schema_is_refused(self, snapshot: dict[str, Any]) -> None:
        snapshot["schema"] = "other"

        with pytest.raises(CurrentSenateError, match="schema"):
            build_current_senate(snapshot, senate_election(snapshot))

    def test_a_special_without_a_class_is_refused(self, snapshot: dict[str, Any]) -> None:
        race = senate_race("republican", "S", seat_class=3, special=True)
        del race["seatInfo"]["senateClass"]

        with pytest.raises(CurrentSenateError, match="special in Ohio has no senateClass"):
            build_current_senate(snapshot, senate_election(snapshot, Ohio=race))

    def test_the_same_state_in_both_election_files_is_refused(
        self, snapshot: dict[str, Any]
    ) -> None:
        election = senate_election(snapshot)

        with pytest.raises(CurrentSenateError, match="Florida class 3 is contested in both"):
            build_current_senate(snapshot, election, special_election={"Florida": election["Florida"]})

    def test_custom_expected_specials_are_respected(self, snapshot: dict[str, Any]) -> None:
        election = senate_election(snapshot)
        del election["Ohio"]

        built = build_current_senate(snapshot, election, expected_specials={"Florida": 3})

        assert _member(built.payload, "Ohio", 3) == _member(snapshot, "Ohio", 3)

    @pytest.mark.parametrize(
        ("edit", "message"),
        [
            (lambda seat: seat["members"][0].update({"class": "two"}), "class 'two'"),
            (lambda seat: seat["members"][0].update({"class": 4}), "class 4"),
            (lambda seat: seat["members"][0].update({"class": True}), "class True"),
            (lambda seat: seat["members"][0].update({"class": None}), "class None"),
            (lambda seat: seat["members"][0].pop("party"), "no 'party'"),
            (lambda seat: seat["members"][0].pop("name"), "no 'name'"),
            (lambda seat: seat["members"][0].update({"name": 7}), "bad 'name'"),
            (lambda seat: seat["members"].append("Cruz"), "is not an object"),
            (lambda seat: seat.pop("members"), "no members list"),
            (lambda seat: seat.pop("r"), "no region 'r'"),
            (lambda seat: seat.pop("n"), "without a state name"),
            (lambda seat: seat.update({"n": 5}), "without a state name"),
        ],
    )
    def test_a_malformed_snapshot_is_refused_not_a_crash(
        self,
        snapshot: dict[str, Any],
        edit: Any,
        message: str,
    ) -> None:
        broken = copy.deepcopy(snapshot)
        edit(_seat(broken, "Texas"))

        with pytest.raises(CurrentSenateError, match=message):
            build_current_senate(broken, senate_election(snapshot))

    def test_a_snapshot_naming_a_state_twice_is_refused(
        self, snapshot: dict[str, Any]
    ) -> None:
        broken = copy.deepcopy(snapshot)
        broken["seats"].append(copy.deepcopy(_seat(broken, "Texas")))

        with pytest.raises(CurrentSenateError, match="names Texas twice"):
            build_current_senate(broken, senate_election(snapshot))

    @pytest.mark.parametrize(
        "pct", [float("nan"), float("inf"), float("-inf"), 100.5, -1, "62", True]
    )
    def test_an_invalid_percent_counted_is_left_out_with_a_warning(
        self, snapshot: dict[str, Any], pct: Any
    ) -> None:
        race = senate_race(None, None, seat_class=2, called=False, pct=pct)

        built = build_current_senate(snapshot, senate_election(snapshot, Texas=race))

        assert _member(built.payload, "Texas", 2) == {
            "party": None, "name": None, "class": 2, "c": False,
        }
        [warning] = built.warnings
        assert warning.startswith("Texas class 2: percent counted")
        render(built.payload)  # strict JSON: no NaN or infinity got through

    def test_the_bounds_of_the_percent_counted_are_valid(
        self, snapshot: dict[str, Any]
    ) -> None:
        for pct in (0, 100):
            race = senate_race(None, None, seat_class=2, called=False, pct=pct)
            built = build_current_senate(snapshot, senate_election(snapshot, Texas=race))
            assert _member(built.payload, "Texas", 2)["pc"] == pct
            assert built.warnings == []

    def test_render_refuses_nan_and_infinity(self) -> None:
        with pytest.raises(CurrentSenateError, match="not valid JSON"):
            render({"schema": "x", "seats": [float("nan")]})


class TestIdempotence:
    def test_rerunning_over_its_own_output_gives_identical_bytes(
        self, snapshot: dict[str, Any]
    ) -> None:
        election = senate_election(snapshot, Texas=senate_race(None, None, seat_class=2, called=False, pct=40))
        first = build_current_senate(snapshot, election)

        second = build_current_senate(first.payload, election)

        assert render(second.payload) == render(first.payload)
        assert (second.holdovers, second.winners, second.uncalled) == (65, 34, 1)

    def test_a_later_election_state_replaces_the_earlier_derived_seats(
        self, snapshot: dict[str, Any]
    ) -> None:
        early = senate_election(snapshot, Texas=senate_race(None, None, seat_class=2, called=False))
        late = senate_election(snapshot, Texas=senate_race("republican", "Texan Winner", seat_class=2))
        derived_early = build_current_senate(snapshot, early).payload

        from_derived = build_current_senate(derived_early, late)
        from_original = build_current_senate(snapshot, late)

        assert render(from_derived.payload) == render(from_original.payload)
        assert "w" in _seat(from_derived.payload, "Texas")
        assert _member(from_derived.payload, "Texas", 2)["name"] == "Texan Winner"

    def test_the_same_inputs_give_the_same_bytes_whatever_the_dict_order(
        self, snapshot: dict[str, Any]
    ) -> None:
        election = senate_election(snapshot)
        reordered = {key: election[key] for key in reversed(list(election))}

        first = render(build_current_senate(snapshot, election).payload)
        second = render(build_current_senate(snapshot, reordered).payload)

        assert first == second


class TestModelLoader:
    def test_the_senate_model_still_reads_the_derived_file(
        self, snapshot: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from models.us.run_us_senate_model import class_state_allowlist

        election = senate_election(snapshot, Texas=senate_race(None, None, seat_class=2, called=False))
        path = tmp_path / "senate-current.json"
        path.write_text(render(build_current_senate(snapshot, election).payload), encoding="utf-8")

        class_two = class_state_allowlist(2, path)
        class_three = class_state_allowlist(3, path)

        # An uncalled member keeps its class: Texas is still a 2026 class 2 state, and no
        # party is read at all, so it is neither a party's seat nor missing.
        assert class_two is not None and "Texas" in class_two
        assert len(class_two) == 33
        # Florida and Ohio hold their special winners, class 3, up again in 2028.
        assert class_three is not None and {"Florida", "Ohio"} <= class_three
        assert len(class_three) == 34


class TestRebuild:
    def _files(self, snapshot: dict[str, Any], tmp_path: Path) -> tuple[Path, Path]:
        snapshot_path = tmp_path / "senate-current.json"
        snapshot_path.write_text(render(snapshot), encoding="utf-8")
        election_path = tmp_path / "senate-2026.json"
        election_path.write_text(json.dumps(senate_election(snapshot)), encoding="utf-8")
        return snapshot_path, election_path

    def test_writes_the_converter_text_to_out(self, snapshot: dict[str, Any], tmp_path: Path) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        out = tmp_path / "nested" / "derived.json"

        report = rebuild_current_senate(
            snapshot_path=snapshot_path, election_path=election_path, out_path=out,
        )

        assert "65 holdovers, 35 winners, 0 uncalled (100 members)" in report
        assert out.read_text(encoding="utf-8").endswith("}\n")
        assert json.loads(out.read_text(encoding="utf-8"))["seats"][0]["n"] == "Alabama"
        assert snapshot_path.read_text(encoding="utf-8") == render(snapshot)

    def test_out_defaults_to_the_snapshot_and_a_rerun_is_unchanged(
        self, snapshot: dict[str, Any], tmp_path: Path
    ) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)

        rebuild_current_senate(snapshot_path=snapshot_path, election_path=election_path)
        first = snapshot_path.read_text(encoding="utf-8")
        report = rebuild_current_senate(snapshot_path=snapshot_path, election_path=election_path)

        assert first != render(snapshot)
        assert snapshot_path.read_text(encoding="utf-8") == first
        assert f"unchanged {snapshot_path}" in report
        assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []

    def test_dry_run_writes_nothing(self, snapshot: dict[str, Any], tmp_path: Path) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        out = tmp_path / "out.json"

        report = rebuild_current_senate(
            snapshot_path=snapshot_path, election_path=election_path, out_path=out, dry_run=True,
        )

        assert "65 holdovers, 35 winners, 0 uncalled" in report
        assert "dry run: would rewrite" in report
        assert not out.exists()
        assert snapshot_path.read_text(encoding="utf-8") == render(snapshot)

    def test_missing_election_file_is_an_error(self, snapshot: dict[str, Any], tmp_path: Path) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        election_path.unlink()

        with pytest.raises(CurrentSenateError, match="election file not found"):
            rebuild_current_senate(snapshot_path=snapshot_path, election_path=election_path)

    def test_an_election_file_of_another_year_is_refused(
        self, snapshot: dict[str, Any], tmp_path: Path
    ) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        election = json.loads(election_path.read_text(encoding="utf-8"))
        election["meta"]["year"] = 2024
        election_path.write_text(json.dumps(election), encoding="utf-8")

        with pytest.raises(CurrentSenateError, match="2024 senate election, not 2026"):
            rebuild_current_senate(snapshot_path=snapshot_path, election_path=election_path)

    def test_a_named_specials_file_is_read_and_must_exist(
        self, snapshot: dict[str, Any], tmp_path: Path
    ) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        election = json.loads(election_path.read_text(encoding="utf-8"))
        election["meta"]["special_file"] = "senate-2026-special.json"
        election_path.write_text(json.dumps(election), encoding="utf-8")

        with pytest.raises(CurrentSenateError, match="specials file not found"):
            rebuild_current_senate(snapshot_path=snapshot_path, election_path=election_path)

        special = {"Georgia": senate_race("republican", "Special Georgia", seat_class=3, special=True)}
        (tmp_path / "senate-2026-special.json").write_text(json.dumps(special), encoding="utf-8")
        report = rebuild_current_senate(
            snapshot_path=snapshot_path, election_path=election_path, dry_run=True,
        )
        assert "64 holdovers, 36 winners" in report

    def test_invalid_json_is_an_error(self, snapshot: dict[str, Any], tmp_path: Path) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        election_path.write_text("{nope", encoding="utf-8")

        with pytest.raises(CurrentSenateError, match="not valid JSON"):
            rebuild_current_senate(snapshot_path=snapshot_path, election_path=election_path)

    @pytest.mark.parametrize(
        "special_file",
        ["../senate-2026-special.json", "sub/special.json", "..", ".", "", "a\\b.json", 7],
    )
    def test_a_special_file_that_is_not_a_plain_name_is_refused(
        self, snapshot: dict[str, Any], tmp_path: Path, special_file: Any
    ) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        election = json.loads(election_path.read_text(encoding="utf-8"))
        election["meta"]["special_file"] = special_file
        election_path.write_text(json.dumps(election), encoding="utf-8")
        # A file the traversal would reach, so a read would succeed if it were allowed.
        (tmp_path.parent / "senate-2026-special.json").write_text("{}", encoding="utf-8")

        with pytest.raises(CurrentSenateError, match="plain file name"):
            rebuild_current_senate(
                snapshot_path=snapshot_path, election_path=election_path
            )

    def test_nan_in_the_election_file_never_reaches_the_output(
        self, snapshot: dict[str, Any], tmp_path: Path
    ) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        election = senate_election(
            snapshot,
            Texas=senate_race(None, None, seat_class=2, called=False, pct=float("nan")),
        )
        election_path.write_text(json.dumps(election), encoding="utf-8")  # writes NaN

        report = rebuild_current_senate(
            snapshot_path=snapshot_path, election_path=election_path
        )

        assert "warning: Texas class 2: percent counted" in report
        text = snapshot_path.read_text(encoding="utf-8")
        assert "NaN" not in text
        json.loads(text, parse_constant=lambda name: pytest.fail(name))

    def test_the_written_file_keeps_the_targets_mode(
        self, snapshot: dict[str, Any], tmp_path: Path
    ) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        os.chmod(snapshot_path, 0o640)

        rebuild_current_senate(snapshot_path=snapshot_path, election_path=election_path)

        assert stat.S_IMODE(snapshot_path.stat().st_mode) == 0o640

    def test_a_new_output_file_is_world_readable(
        self, snapshot: dict[str, Any], tmp_path: Path
    ) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        out = tmp_path / "new" / "out.json"

        rebuild_current_senate(
            snapshot_path=snapshot_path, election_path=election_path, out_path=out
        )

        assert stat.S_IMODE(out.stat().st_mode) == 0o644

    def test_the_data_is_synced_before_it_replaces_the_target(
        self,
        snapshot: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        events: list[str] = []
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(fd: int) -> None:
            events.append("fsync")
            real_fsync(fd)

        def replace(source: Any, target: Any) -> None:
            events.append("replace")
            real_replace(source, target)

        monkeypatch.setattr(os, "fsync", fsync)
        monkeypatch.setattr(os, "replace", replace)

        rebuild_current_senate(snapshot_path=snapshot_path, election_path=election_path)

        assert events == ["fsync", "replace"]

    def test_an_undecodable_existing_output_is_just_changed(
        self, snapshot: dict[str, Any], tmp_path: Path
    ) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        out = tmp_path / "out.json"
        out.write_bytes(b"\xff\xfe not utf-8")

        dry = rebuild_current_senate(
            snapshot_path=snapshot_path,
            election_path=election_path,
            out_path=out,
            dry_run=True,
        )
        rebuild_current_senate(
            snapshot_path=snapshot_path, election_path=election_path, out_path=out
        )

        assert "dry run: would rewrite" in dry
        assert json.loads(out.read_text(encoding="utf-8"))["schema"].startswith("pf-")

    def test_an_undecodable_snapshot_is_an_error(
        self, snapshot: dict[str, Any], tmp_path: Path
    ) -> None:
        snapshot_path, election_path = self._files(snapshot, tmp_path)
        snapshot_path.write_bytes(b"\xff\xfe")

        with pytest.raises(CurrentSenateError, match="snapshot .* not valid JSON"):
            rebuild_current_senate(
                snapshot_path=snapshot_path, election_path=election_path
            )


class TestCli:
    def _files(self, snapshot: dict[str, Any], tmp_path: Path) -> list[str]:
        snapshot_path = tmp_path / "senate-current.json"
        snapshot_path.write_text(render(snapshot), encoding="utf-8")
        election_path = tmp_path / "senate-2026.json"
        election_path.write_text(json.dumps(senate_election(snapshot)), encoding="utf-8")
        return ["current-senate", "--snapshot", str(snapshot_path), "--election-file", str(election_path)]

    def test_writes_the_snapshot_in_place(
        self, snapshot: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = self._files(snapshot, tmp_path)

        assert cli.main(argv) == 0

        out = capsys.readouterr().out
        assert "65 holdovers, 35 winners, 0 uncalled (100 members)" in out
        derived = json.loads((tmp_path / "senate-current.json").read_text(encoding="utf-8"))
        assert _member(derived, "Georgia", 2)["name"] == "Winner Georgia"

    def test_dry_run_prints_counts_and_writes_nothing(
        self, snapshot: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = self._files(snapshot, tmp_path)
        out_path = tmp_path / "elsewhere.json"

        assert cli.main([*argv, "--out", str(out_path), "--dry-run"]) == 0

        assert "65 holdovers, 35 winners, 0 uncalled" in capsys.readouterr().out
        assert not out_path.exists()
        assert (tmp_path / "senate-current.json").read_text(encoding="utf-8") == render(snapshot)

    def test_missing_election_file_is_a_clean_error(
        self, snapshot: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = self._files(snapshot, tmp_path)
        (tmp_path / "senate-2026.json").unlink()

        assert cli.main(argv) == 1

        captured = capsys.readouterr()
        assert "error: election file not found" in captured.err
        assert (tmp_path / "senate-current.json").read_text(encoding="utf-8") == render(snapshot)

    def test_never_opens_the_database(
        self, snapshot: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_database(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("current-senate must not touch the database")

        monkeypatch.setattr(cli, "Database", no_database)

        assert cli.main([*self._files(snapshot, tmp_path), "--dry-run"]) == 0

    def test_an_unwritable_output_is_a_clean_error(
        self, snapshot: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = self._files(snapshot, tmp_path)
        out = tmp_path / "a-directory"
        out.mkdir()

        assert cli.main([*argv, "--out", str(out)]) == 1

        assert capsys.readouterr().err.startswith("error: ")
        assert list(out.iterdir()) == []
        assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []

    def test_an_unreadable_snapshot_is_a_clean_error(
        self, snapshot: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = self._files(snapshot, tmp_path)
        (tmp_path / "senate-current.json").write_bytes(b"\xff\xfe")

        assert cli.main(argv) == 1

        assert "error: snapshot" in capsys.readouterr().err

    def test_a_malformed_snapshot_is_a_clean_error(
        self, snapshot: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = self._files(snapshot, tmp_path)
        snapshot["seats"][0]["members"][0]["class"] = "two"
        (tmp_path / "senate-current.json").write_text(json.dumps(snapshot), encoding="utf-8")

        assert cli.main(argv) == 1

        assert "error: snapshot member of Alabama has class 'two'" in capsys.readouterr().err

    def test_the_default_paths_are_the_committed_files(self) -> None:
        parser = cli._current_senate_parser()

        args = parser.parse_args([])

        assert args.snapshot == DEFAULT_SNAPSHOT
        assert args.election_file.name == "senate-2026.json"
        assert args.election_file.parent.name == "usa"
        assert args.out is None and args.dry_run is False
