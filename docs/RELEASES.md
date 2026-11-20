# DubTool Release History

| Version | Commit | Status | Reason | Date |
| --- | --- | --- | --- | --- |
| `v1.0.6` | `1d8c6428` | Historical | Tag-only release before mechanical release pipeline | 2026-10-27 |
| `v1.0.7` | `e271c903` | Historical | Tag-only release before mechanical release pipeline | 2026-10-28 |
| `v1.0.8` | `262f9ebd` | Historical | Tag added retroactively for exact Update 8 commit match | 2026-10-30 |
| `v1.0.9` | `ad373d84` | Historical | Tag-only release before mechanical release pipeline | 2026-10-31 |
| `v1.0.10` | `1f25a1a9` | Historical | GitHub release published before Update 15 mechanical evidence pipeline | 2026-11-01 |
| `v1.0.11` | `0bcc7230` | Historical | GitHub release published before Update 15 mechanical evidence pipeline | 2026-11-04 |
| `v1.0.12` | `04531dd3` | Historical | GitHub release published before Update 15 mechanical evidence pipeline | 2026-11-05 |
| `v1.0.13` | `63c85a18` | Historical | Tag-only release before mechanical release pipeline | 2026-11-11 |
| `v1.0.14` | `045660d0` | Pre-release, superseded | Published from mismatched working tree (`BUILD_INFO` commit `63c85a18` != tag `045660d0`); history preserved, superseded by `v1.0.15` | 2026-11-12 |
| `v1.0.15` | `102528cd` | Official | Mechanical release via `scripts/release.py 15`; `BUILD_INFO`, tag, `origin/main`, and asset SHA-256 verified | 2026-11-14 |
| `v1.0.16` | `76ba8313` | Official | Automatic source-language detection → Vietnamese, MIT license, screenshot/audit fixes; mechanical release via `scripts/release.py 16` | 2026-11-20 |
| `v1.0.17` | *(pending release)* | In progress | Replace `edge-tts` with offline CPU ONNX VieNeu-TTS v3 (`v3turbo_int8` default + `v3nano` low-RAM) | 2026-11-20 |

## Release Audit & Hook Limits

The local Git `pre-push` hook (`scripts/git_push_guard.py`) only intercepts client-side `git push` operations. Commands such as `gh release create` (or creating a release in the GitHub web UI) can create a tag directly on the GitHub server without invoking the local `pre-push` hook.

To close that gap, all releases must run through `scripts/release.py <N>`, and `scripts/verify_release.py --audit` (also enforced by `scripts/definition_of_done.py`) lists every Git tag and GitHub release and checks each release created after Update 15 against local evidence (`RELEASE_EVIDENCE.txt`, tag commit, `BUILD_INFO.txt`, and asset SHA-256).
