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


def main() -> int:
    tracked = _tracked(ROOT)
    problems = check(ROOT, tracked)
    for p in problems:
        print(p)
    print("check_readme_links:", "FAIL" if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
