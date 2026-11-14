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
sys.path.insert(0, str(ROOT / "src"))
from dubtool import workspace  # noqa: E402

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
    screenshots = workspace.screenshots_dir()
    candidates = sorted(
        screenshots.glob("update_*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"no screenshots/update_* folder under {screenshots}")
    return candidates[0]


def _copy_png_if_changed(src: Path, dest: Path) -> bool:
    """Copy src -> dest unless dest already exists with identical geometry and >=99% identical pixels
    (suppressing sub-second Windows QProgressBar shimmer phase noise across identical builds)."""
    if not dest.is_file():
        shutil.copyfile(src, dest)
        return True
    if src.read_bytes() == dest.read_bytes():
        return False
    try:
        from PySide6.QtGui import QImage

        img_a = QImage(str(src))
        img_b = QImage(str(dest))
        if not img_a.isNull() and not img_b.isNull():
            if img_a.width() == img_b.width() and img_a.height() == img_b.height():
                w, h = img_a.width(), img_a.height()
                step_x = max(1, w // 100)
                step_y = max(1, h // 100)
                total = 0
                same = 0
                for y in range(0, h, step_y):
                    for x in range(0, w, step_x):
                        total += 1
                        if img_a.pixel(x, y) == img_b.pixel(x, y):
                            same += 1
                if total > 0 and (same / total) >= 0.98:
                    return False
    except Exception:
        pass
    shutil.copyfile(src, dest)
    return True


def main() -> int:
    src_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_folder()
    print(f"copying README images from {src_dir}")
    IMAGES.mkdir(parents=True, exist_ok=True)
    missing = []
    current_set: set[str] = set()

    # Copy all current PNGs from src_dir first (except CHOSEN target names handled below)
    chosen_targets = set(CHOSEN.values())
    for src in sorted(src_dir.glob("*.png")):
        if src.name in chosen_targets:
            continue
        size = src.stat().st_size
        if size > MAX_BYTES:
            raise SystemExit(f"{src} is {size} bytes (> {MAX_BYTES}); compress it first")
        dest = IMAGES / src.name
        _copy_png_if_changed(src, dest)
        current_set.add(src.name)

    # Ensure CHOSEN canonical README aliases come from their exact mapped sources
    for src_name, dest_name in CHOSEN.items():
        src = src_dir / src_name
        dest = IMAGES / dest_name
        if not src.is_file():
            missing.append(src_name)
            continue
        size = src.stat().st_size
        if size > MAX_BYTES:
            raise SystemExit(f"{src} is {size} bytes (> {MAX_BYTES}); compress it first")
        _copy_png_if_changed(src, dest)
        current_set.add(dest_name)
        print(f"  {src_name} -> docs/images/{dest_name} ({size} bytes)")

    index_src = src_dir / "INDEX.txt"
    if index_src.is_file():
        shutil.copyfile(index_src, IMAGES / "INDEX.txt")
        current_set.add("INDEX.txt")
        print(f"  INDEX.txt -> docs/images/INDEX.txt ({index_src.stat().st_size} bytes)")

    # Delete any stale file in docs/images/ not part of the current set
    for existing in sorted(IMAGES.iterdir()):
        if existing.is_file() and existing.name not in current_set:
            existing.unlink()
            print(f"  removed stale docs/images/{existing.name}")

    if missing:
        print("MISSING (not copied):", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
