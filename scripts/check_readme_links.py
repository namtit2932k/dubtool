r"""Update 9 task 7: every image/relative link in the READMEs must resolve.

README.md and README_BUILD.md use relative links (images with ![…](…), other
files with plain [text](…)). Links to http(s) are skipped. With a repository
the target must be tracked by git (`git ls-files`); without one any existing
file passes. This is one of the pre-push gates, so a README that points at a
deleted or never-committed file fails the push.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

from dubtool import proc  # every external process goes through the helper (AGU6 task 1)

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md", "README_BUILD.md")


def _tracked(root: Path) -> set[str] | None:
    """git ls-files output, or None when there is no repository."""
    try:
        out = proc.run(["git", "ls-files"], cwd=root)
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return None
    text = out.stdout or ""
    return {line.strip().replace("\\", "/") for line in text.splitlines() if line.strip()}


def _links(text: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r"!?\[[^\]]*\]\(([^)\s]+)\)", text):
        target = match.group(1).split("#")[0].split("?")[0]
        if not target or target.lower().startswith(("http://", "https://")):
            continue
        links.append(target)
    return links


def check(root: Path, tracked: set[str] | None) -> list[str]:
    problems: list[str] = []
    for doc in DOCS:
        path = root / doc
        if not path.is_file():
            problems.append(f"{doc}: missing")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for target in _links(text):
            clean = unquote(target).replace("\\", "/")
            dest = (root / clean).resolve()
            if not dest.exists():
                problems.append(f"{doc}: broken link -> {target}")
                continue
            if tracked is not None:
                rel = dest.relative_to(root.resolve()).as_posix()
                if rel not in tracked:
                    problems.append(f"{doc}: link target not tracked by git -> {target}")
    return problems


def check_images(root: Path) -> list[str]:
    """Update 12 task 5 item 3: automatic image checks for every PNG the
    READMEs reference — the sidecar INDEX.txt must record the real
    windows platform, each image must be at least 600 px wide and not
    blank, and the windows-area luminance must match the dark theme (no
    mostly-white offscreen leftovers)."""
    problems: list[str] = []
    images_dir = root / "docs" / "images"
    index_path = images_dir / "INDEX.txt"
    references: list[Path] = []
    for doc in DOCS:
        doc_path = root / doc
        if not doc_path.is_file():
            continue
        text = doc_path.read_text(encoding="utf-8", errors="replace")
        for target in _links(text):
            dest = (root / unquote(target)).resolve()
            if dest.suffix.lower() == ".png" and dest.exists():
                references.append(dest)
    if not references:
        return problems  # no README images at all
    if not index_path.exists():
        problems.append("docs/images/INDEX.txt: missing (required next to the images)")
    else:
        body = index_path.read_text(encoding="utf-8", errors="replace").lower()
        if "platform: windows" not in body:
            problems.append(
                'docs/images/INDEX.txt: does not say "platform: windows" — '
                "the screenshots must come from the real platform"
            )
    try:
        from PySide6.QtGui import QImage
    except Exception as exc:  # pragma: no cover - env dependent
        problems.append(f"image checks skipped (QImage unavailable: {exc})")
        return problems
    for dest in references:
        image = QImage(str(dest))
        if image.isNull():
            problems.append(f"{dest.relative_to(root)}: unreadable/blank PNG")
            continue
        if image.width() < 600:
            problems.append(
                f"{dest.relative_to(root)}: only {image.width()} px wide (< 600)"
            )
        step = 7
        pixels = []
        for y in range(0, image.height(), step):
            for x in range(0, image.width(), step):
                color = image.pixelColor(x, y)
                pixels.append(0.299 * color.red() + 0.587 * color.green()
                              + 0.114 * color.blue())
        if not pixels:
            problems.append(f"{dest.relative_to(root)}: no pixels sampled")
            continue
        mean = sum(pixels) / len(pixels)
        if mean > 140:
            problems.append(
                f"{dest.relative_to(root)}: mean luminance {mean:.0f} — looks "
                "like a light-theme/leftover image (the real theme is dark)"
            )
        variations = max(pixels) - min(pixels)
        if variations < 30:
            problems.append(
                f"{dest.relative_to(root)}: nearly flat image (range {variations:.0f})"
            )
    return problems


def main() -> int:
    tracked = _tracked(ROOT)
    problems = check(ROOT, tracked)
    problems += check_images(ROOT)
    for p in problems:
        print(p)
    print("check_readme_links:", "FAIL" if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
