"""Shared test fixtures.

AGENT_UPDATE_7.md found none of the three user bugs because suite-level
state leaked between test files. The guard below makes the app language
lab-local: any test may switch it, and the next test starts fresh.
"""

from __future__ import annotations

import pytest

from dubtool.app import i18n


@pytest.fixture(autouse=True)
def _default_language():
    """The app starts Vietnamese (dialog slot 669 i18n.set_language is real,
    desired behavior); this only undoes it between tests."""
    i18n.set_language("vi")
    yield
    i18n.set_language("vi")
