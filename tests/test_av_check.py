"""AGENT_UPDATE_8 task 7.1: the startup PyAV version guard."""

from __future__ import annotations

from dubtool.av_check import REPAIR_COMMAND, problem


def test_new_pyav_warns_with_the_repair_command():
    warning = problem("19.2.0")
    assert warning is not None
    assert "19.2.0" in warning
    assert REPAIR_COMMAND in warning


def test_compatible_pyav_warns_nothing():
    assert problem("11.0.3") is None
    assert problem("18.4.12") is None


def test_unreadable_version_never_blocks():
    # The real lookup runs (installed venv has a pinned av); the result is
    # either a warning or None, never an exception.
    result = problem()
    assert result is None or REPAIR_COMMAND in result
    # An invalid string must not raise either.
    assert problem("garbage") is None
