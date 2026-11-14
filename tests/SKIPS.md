# Documented Environmental Test Skips (`tests/SKIPS.md`)

Every skipped test reported by `pytest tests -q --timeout=120 -rs` must be listed here with its test ID, its skip reason, and the environmental condition under which it runs. Non-environmental skips are forbidden.

| Test ID | Skip Reason Pattern | Condition Under Which It Runs |
|---|---|---|
| `tests/test_cleanup.py::test_symlink_removed_without_touching_target` | `symlink unavailable: [WinError 1314] A required privilege is not held by the client` | Runs on POSIX (Linux/macOS) or on Windows when Developer Mode is enabled or the process holds `SeCreateSymbolicLinkPrivilege` (elevated Administrator session). |
| `tests/test_housekeeping.py::test_symlink_removed_not_target` | `symlink unavailable: [WinError 1314] A required privilege is not held by the client` | Runs on POSIX (Linux/macOS) or on Windows when Developer Mode is enabled or the process holds `SeCreateSymbolicLinkPrivilege` (elevated Administrator session). |

## Non-Environmental Skips Fixed in Update 15 (Now Running & Passing)

1. `tests/test_agu11_tts.py::test_budget_dialog_texts_exist_in_both_languages`: previously checked `i18n._dicts` instead of `i18n.STRINGS`; fixed to assert both `vi` and `en` tables directly.
2. `tests/test_screenshots.py::test_only_one_screenshot_folder`, `tests/test_screenshots.py::test_required_pngs_exist_and_are_newer_than_the_stamp`, `tests/test_screenshots.py::test_index_lists_the_current_build_id`: previously looked only at `<repo>/screenshots` and `<repo>/dist` instead of `dubtool.workspace.screenshots_dir()` and `dubtool.workspace.dist_dir()`; fixed to resolve the active build root.
3. `tests/test_subtitle_sizing.py::test_render_text_block_geometry[1920x1080]`, `[1280x720]`, `[1080x1920]`: previously required `PIL` (`pillow not available`); fixed `_measure_block` to fall back to `PySide6.QtGui.QImage` and `audio_utils.ffmpeg_bin()`.
