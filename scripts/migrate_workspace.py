"""Move generated data OUT of the project folder (AGENT_UPDATE_10.md 2.3).

Dry run by default: prints every generated item found in the project with
its size and its destination in the build root, and states `move` (same
drive: an instant rename) or `recreate` (.venv-build / .venv are NOT
relocatable — the build script makes a fresh venv in the new place and the
old folder is left for the user to delete).

    py scripts/migrate_workspace.py            # dry run
    py scripts/migrate_workspace.py --apply    # perform the moves

--apply never deletes, refuses to overwrite an existing destination,
refuses any path outside the project and the build root, does not follow
links, and writes logs/migrate.log in the build root.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dubtool import workspace  # noqa: E402


def tree_size(p: Path) -> int:
    if p.is_symlink():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
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
                    total += tree_size(Path(entry.path))
            except OSError:
                continue
    except OSError:
        pass
    return total


def _items() -> list[tuple[Path, str]]:
    """(source path, destination-in-build-root relative path) pairs.

    folders: dist, build, jobs, models, vendor, screenshots, sample/output
    root clutter: *.log and build_log*.txt, cleanup_dryrun.txt -> logs/
    not relocatable: .venv-build, .venv -> recreate (reported, never moved)
    """
    outs: list[tuple[Path, str]] = []
    for name, dest in (
        ("dist", "dist"),
        ("build", "build"),
        ("jobs", "jobs"),
        ("models", "models"),
        ("vendor", "vendor"),
        ("screenshots", "screenshots"),
        (Path("sample") / "output", "sample_output"),
        # both venvs are NOT relocatable (AGU10 2.3): the build script
        # makes a fresh environment in the build root; these are left
        # for the user to delete.
        (".venv-build", "RECREATE:venv-build"),
        (".venv", None),  # None = not relocatable, rebuild instead
    ):
        p = ROOT / name
        if p.exists():
            outs.append((p, dest if dest is not None else f"RECREATE:{name}"))
    root_logs = workspace.logs_dir()
    for pat in ("*.log", "build_log*.txt", "cleanup_dryrun.txt", "exe_build*.txt"):
        for p in sorted(ROOT.glob(pat)):
            if p.is_file() and not p.is_symlink():
                outs.append((p, f"log:{p.name}"))
    return outs


def plan(verbose: bool = True) -> list[tuple[Path, str, str, int]]:
    """[(source, kind, destination, size)] — kind is 'move'|'recreate'|'log move'."""
    out: list[tuple[Path, str, str, int]] = []
    for p, dest in _items():
        size = tree_size(p)
        if dest.startswith("RECREATE:"):
            kind = "recreate"
            destination = f"<build root>/{ 'venv-build' if 'venv-build' in dest else '.venv (dev only)'}"
        elif dest.startswith("log:"):
            kind = "log move"
            destination = f"<build root>/logs/{dest[4:]}"
        else:
            kind = "move"
            destination = f"<build root>/{dest}"
        out.append((p, kind, destination, size))
    if verbose:
        _print(out)
    return out


def _print(out: list[tuple[Path, str, str, int]]) -> None:
    print(f"{'size':>10}  {'action':<9}  source -> destination")
    for p, kind, destination, size in out:
        rel = p.relative_to(ROOT)
        disp = f"{human(size):>10}  {kind:<9}  {rel} -> {destination}"
        print(disp)


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _check_inside(p: Path) -> None:
    """Never touch anything outside the project and the build root."""
    pr = workspace.project_root()
    br = workspace.build_root(False)
    resolved = Path(os.path.realpath(str(p)))
    for base in (pr, br):
        resolved = str(resolved).replace("/", "\\") if os.name == "nt" else str(resolved)
        base_str = str(base)
        if resolved == str(base) or resolved.startswith(str(base) + os.sep):
            return
    raise RuntimeError(f"refusing path outside the project and the build root: {p}")


def _log_line(path: Path, kind: str) -> None:
    log_path = workspace.logs_dir() / "migrate.log"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp}\t{kind}\t{path}\n")


def apply_moves(items: list[tuple[Path, str, str, int]]) -> int:
    """Move without following links; refuse an existing destination."""
    moved = 0
    for p, kind, destination, size in items:
        _check_inside(p)
        if kind == "recreate":
            print(f"SKIP (recreate instead): {p.name} — the build script makes "
                  f"a fresh environment in the build root; delete the old folder yourself")
            continue
        rel = (
            Path("logs") / destination.split("logs/", 1)[1]
            if destination.startswith("<build root>/logs/")
            else Path(destination.removeprefix("<build root>/"))
        )
        target = workspace.build_root() / rel
        if target.exists() and any(target.iterdir()):
            # AGU10: skip, never overwrite — the build root may already own
            # a newer version (e.g. the fresh exe's screenshots); the
            # remaining items must still be processed.
            print(f"SKIP (destination not empty, nothing overwritten): "
                  f"{p} -> {target} — move it manually if you are sure")
            _log_line(Path(os.path.realpath(str(p))), "SKIPPED")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if p.is_dir() and (p / "__pycache__").exists():
            pass  # fine, moves with the tree
        try:
            shutil.move(str(p), str(target))
        except (OSError, shutil.Error) as exc:
            print(f"FAILED: {p} -> {target}: {exc}")
            continue
        moved += 1
        _log_line(Path(os.path.realpath(str(p))), "MOVED")
        print(f"moved  {p.name} -> {target}  ({human(size)})")
    log_path = workspace.logs_dir() / "migrate.log"
    print(f"\n{moved} item(s) moved; log: {log_path}")
    return moved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="perform the moves (default: dry run)")
    args = ap.parse_args(argv)

    # A build root inside the project must be refused even for the dry run.
    pr = workspace.project_root()
    br = workspace.build_root(False)
    if br == pr or br in pr.parents or pr in br.parents:
        print("ERROR: the build root is inside the project folder:", br, file=sys.stderr)
        return 2

    print("project   :", pr)
    print("build root:", br, "(env DUBTOOL_BUILD_ROOT overrides)")
    print()
    items = plan()
    total = sum(size for _p, _k, _d, size in items)
    print(f"\nthese items account for {human(total)} inside the project folder.")
    if not args.apply:
        print("\nDRY RUN — nothing moved. Run with --apply to perform the moves.")
        return 0
    n = apply_moves(items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
