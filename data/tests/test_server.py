"""The console entrypoint's debug switch."""

from __future__ import annotations

import pytest

from server import debug_enabled


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_a_truthy_console_debug_turns_debug_on(value: str) -> None:
    assert debug_enabled({"CONSOLE_DEBUG": value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "debug"])
def test_anything_else_leaves_debug_off(value: str) -> None:
    assert debug_enabled({"CONSOLE_DEBUG": value}) is False


def test_debug_is_off_when_unset() -> None:
    assert debug_enabled({}) is False
