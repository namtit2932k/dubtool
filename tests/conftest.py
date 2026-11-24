"""Shared test fixtures.

AGENT_UPDATE_7.md found none of the three user bugs because suite-level
state leaked between test files. The guard below makes the app language
lab-local: any test may switch it, and the next test starts fresh.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dubtool.app import i18n

# Pure-GUI test modules (Qt widgets / layout checks) and multi-stage integration
# modules; individual tests using the `qtbot` fixture are also auto-tagged `gui`.
_GUI_TEST_FILES = frozenset({
    "test_agu13.py",
    "test_app_ui.py",
    "test_fillers_dialog.py",
    "test_hotfix_regressions.py",
    "test_layout_check.py",
    "test_llm_cancel.py",
    "test_update9_ui.py",
})

_INTEGRATION_TEST_FILES = frozenset({
    "test_api_and_service.py",
    "test_cpu_limits.py",
    "test_definition_of_done.py",
    "test_jobs_retention.py",
    "test_mix_graph.py",
    "test_readable_export.py",
    "test_update16_multilingual.py",
})


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply pyproject.toml suite markers (unit/gui/integration/packaged) cleanly.

    Explicit per-file/per-test markers (e.g. ``@pytest.mark.packaged`` on
    ``test_screenshots.py`` or ``@pytest.mark.gui`` on Qt tests) are preserved;
    remaining tests are classified by fixture usage and module role so
    ``pytest -m unit``, ``-m gui``, ``-m integration``, and ``-m packaged``
    all select meaningful subsets without duplicating boilerplate in 60+ files.
    """
    for item in items:
        filename = Path(str(item.fspath)).name
        has_packaged = item.get_closest_marker("packaged") is not None
        has_gui = item.get_closest_marker("gui") is not None
        has_integration = item.get_closest_marker("integration") is not None

        if not has_gui and (filename in _GUI_TEST_FILES or "qtbot" in getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.gui)
            has_gui = True

        if not has_integration and filename in _INTEGRATION_TEST_FILES:
            item.add_marker(pytest.mark.integration)
            has_integration = True

        if not (has_packaged or has_gui or has_integration):
            item.add_marker(pytest.mark.unit)


@pytest.fixture(autouse=True)
def _default_language():
    """The app starts Vietnamese (dialog slot 669 i18n.set_language is real,
    desired behavior); this only undoes it between tests."""
    i18n.set_language("vi")
    yield
    i18n.set_language("vi")
