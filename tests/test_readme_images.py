"""Update 9 task 7: README images that work.

- update_readme_images.py copies the chosen states from the newest
  screenshots/update_*/ folder into docs/images/ with stable names, and each
  copied image stays under 300 KB.
- check_readme_links.py fails on a broken or untracked link; links to http(s)
  are skipped.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import check_readme_links as crl  # noqa: E402
import update_readme_images as uri  # noqa: E402


def test_docs_images_exist_and_small():
    images = ROOT / "docs" / "images"
    assert images.is_dir()
    for name in uri.CHOSEN.values():
        p = images / name
        assert p.is_file(), f"missing docs/images/{name}"
        assert p.stat().st_size < uri.MAX_BYTES, f"{name} too big for the repo"


def test_readme_uses_docs_images_not_screenshots():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "](screenshots/" not in text  # no image link into the ignored folder
    for name in uri.CHOSEN.values():
        assert f"docs/images/{name}" in text


def test_docs_images_not_gitignored():
    """The gate checks that link targets are tracked — which only works if
    .gitignore does not swallow docs/ (screenshots/ stays ignored)."""
    from dubtool import proc

    for name in uri.CHOSEN.values():
        done = proc.run(["git", "check-ignore", f"docs/images/{name}"], cwd=ROOT)
        assert done.returncode != 0, f"docs/images/{name} is gitignored"


def test_link_checker_fails_on_broken_and_passes_links(tmp_path):
    root = tmp_path
    (root / "README.md").write_text(
        "# t\n![x](docs/images/ready.png)\n[y](Z.md)\n", encoding="utf-8"
    )
    (root / "README_BUILD.md").write_text("", encoding="utf-8")
    (root / "docs" / "images").mkdir(parents=True)
    (root / "docs" / "images" / "ready.png").write_bytes(b"png")
    problems = crl.check(root, None)
    assert problems == ["README.md: broken link -> Z.md"]


def test_link_checker_ignores_remote_links(tmp_path):
    root = tmp_path
    (root / "README.md").write_text(
        "[g](https://example.com/x)\n", encoding="utf-8"
    )
    (root / "README_BUILD.md").write_text("", encoding="utf-8")
    assert crl.check(root, None) == []


def test_link_checker_tracks_target_when_scanning_git(tmp_path):
    root = tmp_path
    (root / "README.md").write_text("![i](docs/images/a.png)\n", encoding="utf-8")
    (root / "README_BUILD.md").write_text("", encoding="utf-8")
    (root / "docs" / "images").mkdir(parents=True)
    (root / "docs" / "images" / "a.png").write_bytes(b"p")
    problems = crl.check(root, {"docs/images/a.png"})
    assert problems == []
    problems = crl.check(root, set())          # tracked set empty -> not tracked
    assert len(problems) == 1 and "not tracked" in problems[0]
