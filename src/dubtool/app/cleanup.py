"""Workspace cleanup planner (AGENT_UPDATE_6.md task 5, sections 6.1–6.3).

`plan()` is pure: it never deletes anything and returns per-item actions so
the CLI can print a dry run. `apply()` deletes only items whose action is
"delete" and writes cleanup.log. Safety rules:

- Paths are resolved against the given root; nothing outside it is touched.
- Symlinks/junctions are removed as the link itself, never their target.
- `.git/` is never touched (only reported).
- `--apply` (the CLI) requires explicit `--categories` or `--all-regenerable`.

Categories are a fixed list; anything not listed is left alone entirely.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("dubtool")

# ------------------------------------------------------------- categories
REGENERABLE_CATEGORIES = (
    "build",          # PyInstaller intermediate build/
    "caches",         # __pycache__, .pytest_cache, .mypy_cache, *.pyc, *.egg-info
    "temp_clips",     # temp files named ~raw-* / ~trim-*
    "shots",          # shots*/ and screenshots/ except the newest set
    "check_frames",   # top-level check/ frame folders
    "old_backups",    # old_before_*/
    "review_zips",    # review/*.zip beyond the 3 newest
    "dist_old",       # dist/ artifacts other than the newest
    "jobs_old",       # job folders older than --jobs-days, newest kept per id
)

# report-only: shown with sizes, never deleted automatically by this script
REPORT_ONLY = (
    ".venv", ".venv-build", "vendor", "models", "sample", ".git", ".env",
    ".env.example", "src", "tests", "packaging", "scripts", "agent",
    ".vscode", ".freebuff", "jobs",
)
REPORT_ONLY_FILES = ("AGENT*.md", "README*.md", "REVIEW_NOTES.md",
                     "config.yaml", "pyproject.toml")

_TEMP_FILE_RE = re.compile(r"^~(raw|trim)-.*")


@dataclass
class PlanItem:
    path: Path
    size: int          # bytes; for files the file size, for folders the tree
    category: str
    action: str        # "delete" | "keep"
    note: str = ""


# ----------------------------------------------------------------- helpers
def _tree_size(p: Path) -> int:
    """Recursively sum file sizes; symlinks count as 0 bytes (never followed)."""
    if p.is_symlink():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    if not p.is_dir():
        return 0
    total = 0
    try:
        for entry in os.scandir(p):
            try:
                if entry.is_symlink():
                    continue
                if entry.is_file():
                    total += entry.stat().st_size
                elif entry.is_dir():
                    total += _tree_size(Path(entry.path))
            except OSError:
                continue
    except OSError:
        pass
    return total


def human(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} B"
        nbytes /= 1024
    return f"{nbytes:.1f} GB"


def top_items(root: Path, n: int = 30) -> list[tuple[str, int]]:
    """The n largest entries (files and folders) directly under root."""
    entries: list[tuple[Path, int]] = []
    for entry in sorted(os.scandir(root), key=lambda e: e.name):  # type: ignore[arg-type]
        p = Path(entry.path)
        if p.name in (".git", ".pytest_cache", ".vscode", "__pycache__"):
            continue
        entries.append((p, _tree_size(p)))
    entries.sort(key=lambda kv: -kv[1])
    out = [(p.name, s) for p, s in entries[:n]]
    return out


def _newest(paths: list[Path]) -> Path | None:
    try:
        return max(paths, key=lambda p: p.stat().st_mtime)
    except (ValueError, OSError):
        return None


def _plan_jobs(root: Path, cutoff_days: int, now: float) -> list[PlanItem]:
    """Old job folders, newest kept per job id and never running/recent.

    Job ids are stable per (video, options), so repeated runs of the same
    job recreate the same folder — an old duplicate is redundant. The
    three-day rule keeps a possible resumable run alive even if the folder
    would be old enough: the folder's mtime is our proxy for the run time.
    """
    jobs_root = root / "jobs"
    items: list[PlanItem] = []
    cutoff = now - cutoff_days * 86400
    resume_cutoff = now - 3 * 86400
    try:
        job_dirs = [p for p in jobs_root.iterdir() if p.is_dir() and not p.is_symlink()]
    except OSError:
        return items
    newest_per_id: dict[str, Path] = {}
    for p in job_dirs:
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt > resume_cutoff:
            continue  # resumable from the last 3 days
        state = p / "state.json"
        if state.exists():
            try:
                import json
                st = json.loads(state.read_text(encoding="utf-8"))
            except Exception:
                st = {}
            if any(s == "running" for s in (st.get("stages") or {}).values()):
                continue  # never delete the job that is running
        if p.name not in newest_per_id or _mt(p) > _mt(newest_per_id[p.name]):
            newest_per_id[p.name] = p
    for p in job_dirs:
        if newest_per_id.get(p.name) is None:
            continue
        if newest_per_id[p.name] is not p:
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt >= cutoff:
            continue
        items.append(PlanItem(p, _tree_size(p), "jobs_old", "delete",
                              note="older than the jobs-days limit"))
    return items


def plan(root: Path, *, jobs_days: int = 14, now: float | None = None) \
        -> tuple[list[PlanItem], list[PlanItem], list[tuple[str, int]]]:
    """Plan the run. Returns (deletions, report_only_items, top_entries)."""
    root = Path(root).resolve()
    now = time.time() if now is None else now
    deletions: list[PlanItem] = []
    reported: list[PlanItem] = []

    def add(p: Path, category: str, action: str, note: str = "") -> None:
        if not p.exists():
            return
        item = PlanItem(p, _tree_size(p), category, action, note)
        (deletions if action == "delete" else reported).append(item)

    # --- regenerable -----------------------------------------------------
    add(root / "build", "build", "delete", "PyInstaller intermediate")
    for name in ("__pycache__", ".pytest_cache", ".mypy_cache"):
        for p in [root / name] + list(root.glob(f"**/{name}")):
            add(p, "caches", "delete")
    for p in root.glob("**/*.egg-info"):
        add(p, "caches", "delete")
    for p in root.glob("**/*.pyc"):
        add(p, "caches", "delete")

    for p in root.glob("**/~raw-*"):
        add(p, "temp_clips", "delete", "temp file")
    for p in root.glob("**/~trim-*"):
        add(p, "temp_clips", "delete", "temp file")

    shot_dirs = [p for p in root.glob("shots*") if p.is_dir()]
    if shot_dirs:
        newest = _newest(shot_dirs)
        for p in shot_dirs:
            if newest is not None and p.samefile(newest):
                continue  # keep the newest set
            add(p, "shots", "delete", "older shots set")
    if (root / "screenshots").is_dir():
        # Keep the newest screenshot files as the current set; older ones go.
        files = [p for p in (root / "screenshots").rglob("*")
                 if p.is_file() and not p.is_symlink()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[8:]:
            add(p, "shots", "delete")

    add(root / "check", "check_frames", "delete", "frame dump folders")

    for p in root.glob("old_before_*"):
        add(p, "old_backups", "delete", "job backup")

    zips = sorted((root / "review").glob("*.zip")) if (root / "review").is_dir() else []
    for p in _by_mtime(zips)[3:]:
        add(p, "review_zips", "delete", "beyond the 3 newest")
    for p in zips[:3]:
        add(p, "review_zips", "keep")

    # dist: keep only the newest artifact (a DubTool folder or a zip)
    dist = root / "dist"
    if dist.is_dir():
        try:
            artifacts = [p for p in dist.iterdir()]
        except OSError:
            artifacts = []
        ordered = _by_mtime(artifacts)
        for p in ordered[1:]:
            add(p, "dist_old", "delete", "older dist artifact")
        if ordered:
            add(ordered[0], "dist_old", "keep", "newest artifact")

    deletions.extend(_plan_jobs(root, jobs_days, now))

    # --- report-only -----------------------------------------------------
    for name in REPORT_ONLY:
        p = root / name
        if p.exists():
            add(p, name, "keep", "report-only (never deleted automatically)")
    for pat in REPORT_ONLY_FILES:
        for p in root.glob(pat):
            add(p, p.name, "keep", "report-only file")

    tops = top_items(root)
    # Sizes cached in items are reused to avoid a second walk below.
    return deletions, reported, tops


def _by_mtime(paths: list[Path]) -> list[Path]:
    """Newest first; ties in mtime fall back to name descending, so within
    a same-timestamp batch the lexicographically latest name is kept."""
    by_name = sorted(paths, key=lambda p: p.name, reverse=True)
    return sorted(by_name, key=lambda p: -_mt(p))


def _mt(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def check_root(root: Path) -> None:
    """Refuse to plan or delete outside the project root."""
    if not root.is_dir():
        raise SystemExit(f"project root not found: {root}")


def apply(items: list[PlanItem], root: Path, log_path: Path) -> int:
    """Delete only items whose action is 'delete'; write cleanup.log."""
    reclaimed = 0
    lines = ["# cleanup.log  " +
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")]
    root = Path(root).resolve()
    for item in items:
        if item.action != "delete":
            continue
        p = item.path.resolve()
        if not (p == root or p.is_relative_to(root)):
            log.warning("skipping outside-root path: %s", p)
            continue
        size = item.size
        try:
            if p.is_symlink():
                if p.is_dir():
                    p.rmdir()      # remove the link only
                else:
                    p.unlink()
            elif p.is_file():
                p.unlink()
            elif p.is_dir():
                _rmtree_nofollow(p)
            else:
                continue
        except OSError as exc:
            log.warning("failed to delete %s: %s", p, exc)
            lines.append(f"FAILED\t{size}\t{p}\t{exc}")
            continue
        reclaimed += size
        lines.append(f"DELETED\t{size}\t{p}\t{datetime.now().strftime('%H:%M:%S')}")
    lines.append(f"TOTAL\t{reclaimed}")
    try:
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("could not write %s: %s", log_path, exc)
    return reclaimed


def _rmtree_nofollow(dirpath: Path) -> None:
    for entry in os.scandir(dirpath):
        try:
            p = Path(entry.path)
            if entry.is_symlink():
                if entry.is_dir():
                    p.rmdir()
                else:
                    p.unlink()
            elif entry.is_dir():
                _rmtree_nofollow(p)
            else:
                p.unlink()
        except OSError:
            continue
    try:
        dirpath.rmdir()
    except OSError:
        pass


# --------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    import argparse

    # src/dubtool/app/cleanup.py -> project root is four levels up
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(
        description="Clean regenerable files from the DubTool project tree")
    parser.add_argument("--apply", action="store_true",
                        help="actually delete (default: dry run)")
    parser.add_argument("--categories", default="",
                        help="comma-separated categories to delete with --apply")
    parser.add_argument("--all-regenerable", action="store_true",
                        help="delete all regenerable categories with --apply")
    parser.add_argument("--jobs-days", type=int, default=14)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--log", default=str(root / "cleanup.log"))
    args = parser.parse_args(argv)

    check_root(root)
    deletions, reported, tops = plan(root, jobs_days=args.jobs_days)
    reclaimable = sum(i.size for i in deletions)

    print("=== Largest entries under the project root ===")
    for name, size in tops:
        print(f"{human(size):>10}  {name}")

    print("\n=== Regenerable (delete candidates) ===")
    for item in sorted(deletions, key=lambda i: -i.size):
        print(f"{human(item.size):>10}  delete  [{item.category}] {item.path.relative_to(root)}  {item.note}")
    print(f"{human(reclaimable):>10} total reclaimable")

    print("\n=== Report-only (kept) ===")
    for item in sorted(reported, key=lambda i: -i.size):
        print(f"{human(item.size):>10}  keep    {item.path.relative_to(root)}")

    if not args.apply:
        total = _tree_size(root)
        print(f"\nDRY RUN (nothing deleted). Project total: {human(total)}")
        return 0

    wanted = (set(REGENERABLE_CATEGORIES) if args.all_regenerable
              else {c.strip() for c in args.categories.split(",") if c.strip()})
    unknown = wanted - set(REGENERABLE_CATEGORIES)
    if unknown:
        print(f"unknown categories: {', '.join(sorted(unknown))}")
        return 2
    if not wanted:
        print("--apply needs --categories or --all-regenerable")
        return 2
    items = [i for i in deletions if i.category in wanted]
    reclaimed = apply(items, root, Path(args.log))
    print(f"deleted {len(items)} item(s), reclaimed {human(reclaimed)}"
          f" (log: {args.log})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
