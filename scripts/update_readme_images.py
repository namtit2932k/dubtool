"""Update 9 task 7: copy the README's images from the newest screenshot folder.

Findings the task fixes: README.md pointed at screenshots/03_ready.png etc.,
which the build never creates (it writes screenshots/update_<N>_<hash>/ and
.gitignore ignores that folder), so every image link was broken.

Usage:
    python scripts/update_readme_images.py [SOURCES_DIR]

Without an argument the newest  screenshots/update_<N>_<hash>/  folder is
used. Every image is smaller than 300 KB (they are offscreen-rendered
windows); the script refuses anything larger so docs/ never bloats.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS = ROOT / "screenshots"
IMAGES = ROOT / "docs" / "images"

# state in the screenshot folder -> stable file name in docs/images/
# (chosen states: ready/video imported, running with details, finished,
# Settings -> API keys)
CHOSEN = {
    "1100x800_video.png": "ready.png",
    "1100x800_running.png": "running.png",
    "1100x800_finished.png": "finished.png",
    "dialog_settings_tab0_Khóa_API.png": "settings_api_keys.png",
}

MAX_BYTES = 300 * 1024


def newest_folder() -> Path:
    candidates = sorted(
        SCREENSHOTS.glob("update_*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"no screenshots/update_* folder under {SCREENSHOTS}")
    return candidates[0]


def main() -> int:
    src_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_folder()
    print(f"copying README images from {src_dir}")
    IMAGES.mkdir(parents=True, exist_ok=True)
    missing = []
    for src_name, dest_name in CHOSEN.items():
        src = src_dir / src_name
        dest = IMAGES / dest_name
        if not src.is_file():
            missing.append(src_name)
            continue
        size = src.stat().st_size
        if size > MAX_BYTES:
            raise SystemExit(f"{src} is {size} bytes (> {MAX_BYTES}); compress it first")
        shutil.copyfile(src, dest)
        print(f"  {src_name} -> docs/images/{dest_name} ({size} bytes)")
    if missing:
        print("MISSING (not copied):", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
